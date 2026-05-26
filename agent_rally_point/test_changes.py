# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/app_pulse/changes.py — append-only immutable log.

  - record schema build + validate (warns-not-drops unknown kind, D7)
  - NON-GOAL guard: no frequency/invocation/count keys anywhere
  - O_APPEND atomic under concurrency: N writers -> N intact JSON lines
  - read_changes_since(offset) returns only new records + new offset
  - immutable: no rewrite/delete/truncate API exists
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as ch  # noqa: E402

_FREQ_KEYS = {
    "count", "counts", "frequency", "freq", "invocations", "invocation_count",
    "calls", "num_calls", "call_count", "hits", "usage", "usage_count",
}


def _assert_no_freq(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FREQ_KEYS, f"non-goal key leaked: {k}"
            _assert_no_freq(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_freq(v)


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_make_record_schema():
    # intent: new records emit canonical identity fields; legacy epoch `ts` is no longer emitted.
    r = ch.make_record(
        kind="commit", tool="claude", model="opus", run_id="r1",
        app_slug="app", payload={"sha": "abc"}, revision=3,
    )
    assert set(r) >= {
        "kind", "tool", "model", "run_id", "app_slug", "payload", "revision",
    }
    assert set(r) >= {
        "specversion", "id", "source", "subject", "time", "type",
        "thread_id", "causation_id", "correlation_id",
        "datacontenttype", "dataschema",
    }
    assert "ts" not in r  # legacy epoch-seconds field dropped in 0.4
    assert r["kind"] == "commit" and r["revision"] == 3
    assert r["specversion"] == "1.0"
    assert r["id"].startswith("evt_")
    assert r["source"] == "urn:agent-rally-point:tool:claude"
    assert r["subject"] == "app"
    assert r["thread_id"].startswith("thr_")
    assert r["correlation_id"] == r["thread_id"]
    assert r["type"] == "agent-rally.commit.created.v1"
    assert r["datacontenttype"] == "application/json"
    _assert_no_freq(r)


def test_make_record_accepts_correlation_overrides():
    # intent: consumers can link later records to an existing thread and parent event.
    r = ch.make_record(
        kind="feedback", tool="codex", model="gpt", run_id="r2",
        app_slug="app", payload={}, revision=4,
        event_id="evt_" + "1" * 32,
        thread_id="thr_" + "2" * 32,
        causation_id="evt_" + "3" * 32,
    )
    assert r["id"] == "evt_" + "1" * 32
    assert r["thread_id"] == "thr_" + "2" * 32
    assert r["correlation_id"] == "thr_" + "2" * 32
    assert r["causation_id"] == "evt_" + "3" * 32
    assert r["type"] == "agent-rally.feedback.posted.v1"
    assert r["dataschema"] == "urn:agent-rally-point:schema:feedback.posted.v1"


def test_canonical_source_and_unknown_type_are_sanitized():
    # intent: producer-controlled strings still produce conservative URI-ish metadata.
    r = ch.make_record(
        kind="custom kind/β", tool="Claude Code/β", model="m", run_id="r",
        app_slug="app", payload={}, revision=1,
    )
    assert r["source"] == "urn:agent-rally-point:tool:Claude-Code"
    assert r["type"] == "agent-rally.custom-kind.v1"


def test_validate_warns_not_drops_unknown_kind(capsys):
    r = ch.make_record(
        kind="totally-new-kind", tool="t", model="m", run_id="r",
        app_slug="a", payload={}, revision=1,
    )
    out = ch.validate_record(r)  # must RETURN the record, not drop it
    assert out == r
    assert "totally-new-kind" in capsys.readouterr().err


def test_packaged_schemas_are_valid_json():
    # intent: canonical schema files stay machine-readable for downstream tooling.
    schema_dir = Path(__file__).resolve().parent / "schemas"
    names = {p.name for p in schema_dir.glob("*.json")}
    assert "envelope.v1.schema.json" in names
    assert "handoff.created.v1.schema.json" in names
    assert "handoff.acknowledged.v1.schema.json" in names
    for path in schema_dir.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["$id"].startswith("urn:agent-rally-point:schema:")


def test_make_record_matches_envelope_schema():
    # intent: envelope.v1.schema.json and make_record() must not drift apart;
    # adding a field to one without the other silently breaks downstream tooling.
    import re

    schema_path = (
        Path(__file__).resolve().parent / "schemas" / "envelope.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    r = ch.make_record(
        kind="handoff", tool="claude_code", model="opus", run_id="r1",
        app_slug="app", payload={"from_tool": "claude_code", "to_tool": "codex"},
        revision=7, causation_id="evt_" + "a" * 32,
    )
    # All required fields present.
    missing = set(schema["required"]) - set(r)
    assert not missing, f"record missing required schema fields: {missing}"
    # No unexpected top-level keys (additive evolution must update the schema).
    extra = set(r) - set(schema["properties"])
    assert not extra, f"record has fields not in envelope schema: {extra}"
    # Pattern fields validate.
    for field, prop in schema["properties"].items():
        if "pattern" in prop and r.get(field) is not None:
            assert re.match(prop["pattern"], r[field]), (
                f"{field}={r[field]!r} fails schema pattern {prop['pattern']!r}"
            )
    # const fields match.
    for field, prop in schema["properties"].items():
        if "const" in prop:
            assert r[field] == prop["const"], (
                f"{field}={r[field]!r} != const {prop['const']!r}"
            )


def test_append_and_read_since(chan: Path):
    r1 = ch.make_record(kind="commit", tool="t", model="m", run_id="r",
                         app_slug="a", payload={}, revision=1)
    ch.append_change(chan, r1)
    recs, off = ch.read_changes_since(chan, 0)
    assert [x["kind"] for x in recs] == ["commit"]
    r2 = ch.make_record(kind="phase", tool="t", model="m", run_id="r",
                        app_slug="a", payload={"phase": "execute"}, revision=2)
    ch.append_change(chan, r2)
    recs2, off2 = ch.read_changes_since(chan, off)
    assert [x["kind"] for x in recs2] == ["phase"]
    assert off2 > off
    # absent log -> empty, offset 0
    assert ch.read_changes_since(chan / "nope", 0) == ([], 0)


def _writer(d: str, tag: int):
    for i in range(20):
        ch.append_change(
            Path(d),
            ch.make_record(kind="commit", tool="t", model="m",
                           run_id=f"w{tag}", app_slug="a",
                           payload={"i": i}, revision=1),
        )


def test_concurrent_append_no_torn_lines(chan: Path):
    procs = [mp.Process(target=_writer, args=(str(chan), t)) for t in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    recs, _ = ch.read_changes_since(chan, 0)
    assert len(recs) == 6 * 20
    for r in recs:  # every line parsed cleanly == no torn writes
        assert set(r) >= {"kind", "run_id", "payload"}
        _assert_no_freq(r)


def test_no_mutation_api():
    for forbidden in ("rewrite", "delete_change", "truncate", "overwrite",
                       "update_change", "remove_change"):
        assert not hasattr(ch, forbidden), f"immutability violated: {forbidden}"
