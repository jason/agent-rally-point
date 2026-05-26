# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for coordination trace query helpers."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from agent_rally_point.changes import append_change, make_record  # noqa: E402
from agent_rally_point.coordination_trace import (  # noqa: E402
    active_blockers,
    active_claims,
    claim_conflicts,
    filter_since,
    pending_handoffs,
    related_records,
)


def _record(kind: str, rev: int, payload: dict, **kw) -> dict:
    return make_record(
        kind=kind, tool=kw.pop("tool", "pi"), model="m", run_id="r",
        app_slug="app", payload=payload, revision=rev, **kw,
    )


def test_related_records_expands_by_thread_and_causation():
    # intent: thread lookup reconstructs a handoff -> ack chain from canonical ids.
    handoff = _record(
        "handoff", 1, {"from_tool": "pi", "to_tool": "codex", "subject": "review"},
        event_id="evt_" + "1" * 32, thread_id="thr_" + "2" * 32,
    )
    ack = _record(
        "ack", 2, {"ref_handoff_id": handoff["id"], "verdict": "done"},
        event_id="evt_" + "3" * 32, thread_id=handoff["thread_id"],
        causation_id=handoff["id"], tool="codex",
    )
    unrelated = _record("phase", 3, {"phase": "other"})
    found = related_records([handoff, ack, unrelated], handoff["id"])
    assert [r["id"] for r in found] == [handoff["id"], ack["id"]]


def test_pending_handoffs_filters_acked_and_self():
    # intent: inbox only shows open handoffs addressed to the requested tool.
    open_handoff = _record(
        "handoff", 1,
        {"from_tool": "pi", "to_tool": "codex", "subject": "open", "requires_ack": True},
        event_id="evt_" + "1" * 32,
    )
    closed_handoff = _record(
        "handoff", 2,
        {"from_tool": "pi", "to_tool": "codex", "subject": "closed", "requires_ack": True},
        event_id="evt_" + "2" * 32,
    )
    ack = _record(
        "ack", 3,
        {"ref_handoff_id": closed_handoff["id"], "verdict": "done"},
        tool="codex",
    )
    self_handoff = _record(
        "handoff", 4,
        {"from_tool": "codex", "to_tool": "codex", "subject": "self", "requires_ack": True},
        tool="codex",
    )
    pending = pending_handoffs([open_handoff, closed_handoff, ack, self_handoff], tool="codex")
    assert [p.subject for p in pending] == ["open"]


def test_filter_since_uses_ts_cutoff():
    # intent: report/replay windows filter records by trace timestamp.
    old = {"ts": 10, "kind": "phase", "payload": {}, "revision": 1}
    new = {"ts": 20, "kind": "phase", "payload": {}, "revision": 2}
    assert filter_since([old, new], 15) == [new]


def test_related_records_tolerates_dangling_causation():
    # intent: replay/thread lookup must not break when a causation_id references
    # an event that is not present in the channel (cross-channel or never-emitted parent).
    orphan_ack = _record(
        "ack", 5, {"ref_handoff_id": "evt_" + "9" * 32, "verdict": "done"},
        event_id="evt_" + "5" * 32,
        causation_id="evt_" + "9" * 32,  # parent not in this channel
        tool="codex",
    )
    found = related_records([orphan_ack], orphan_ack["id"])
    assert [r["id"] for r in found] == [orphan_ack["id"]]
    # Looking up the missing parent also returns the orphan ack (not a crash).
    found_parent = related_records([orphan_ack], "evt_" + "9" * 32)
    assert [r["id"] for r in found_parent] == [orphan_ack["id"]]


def test_event_label_falls_back_to_top_level_subject():
    # intent: CloudEvents-shaped records that put subject top-level (not in payload)
    # still render a meaningful summary.
    from agent_rally_point.coordination_trace import event_label

    rec = {
        "kind": "phase", "tool": "claude", "app_slug": "app",
        "subject": "verify build-loop integration",
        "payload": {"phase": "verify"},
    }
    label = event_label(rec)
    assert "verify build-loop integration" in label
    # When top-level subject is the placeholder (== app_slug), it is ignored.
    rec_placeholder = {**rec, "subject": "app", "payload": {"phase": "verify"}}
    assert "app" not in event_label(rec_placeholder).split(":", 1)[1]


def test_load_roundtrip_with_precanonical_record(tmp_path: Path):
    # intent: helpers tolerate historical records without canonical id/thread fields.
    from agent_rally_point.coordination_trace import load_records, record_id

    legacy = {"ts": 1, "kind": "phase", "tool": "t", "model": "m", "run_id": "r", "app_slug": "a", "payload": {}, "revision": 9}
    append_change(tmp_path, legacy)
    records = load_records(tmp_path)
    assert record_id(records[0]) == "rev:9"


def test_active_claims_release_and_conflicts():
    # intent: ownership is append-only; releases close claims and exact-resource conflicts are derived.
    claim_a = _record(
        "claim", 1,
        {"owner_tool": "pi", "resource": "file:docs/SCHEMA.md", "subject": "edit schema"},
        event_id="evt_" + "1" * 32,
    )
    claim_b = _record(
        "claim", 2,
        {"owner_tool": "codex", "resource": "file:docs/SCHEMA.md", "subject": "review schema"},
        event_id="evt_" + "2" * 32,
        tool="codex",
    )
    claim_c = _record(
        "claim", 3,
        {"owner_tool": "pi", "resource": "file:README.md", "subject": "docs"},
        event_id="evt_" + "3" * 32,
    )
    release_c = _record(
        "claim-release", 4,
        {"ref_claim_id": claim_c["id"], "reason": "done"},
        tool="pi",
    )
    records = [claim_a, claim_b, claim_c, release_c]
    assert [c.event_id for c in active_claims(records)] == [claim_a["id"], claim_b["id"]]
    conflicts = claim_conflicts(records)
    assert len(conflicts) == 1
    assert conflicts[0].resource == "file:docs/SCHEMA.md"
    assert conflicts[0].owners == ("codex", "pi")


def test_claim_conflicts_detect_file_path_overlap_and_rev_release():
    # intent: file claims conflict on containment, and lifecycle references may use rev aliases.
    parent = _record(
        "claim", 1,
        {"owner_tool": "pi", "resource": "file:docs", "subject": "docs sweep"},
        event_id="evt_" + "6" * 32,
    )
    child = _record(
        "claim", 2,
        {"owner_tool": "codex", "resource": "file:docs/SCHEMA.md", "subject": "schema"},
        event_id="evt_" + "7" * 32,
        tool="codex",
    )
    conflicts = claim_conflicts([parent, child])
    assert len(conflicts) == 1
    assert conflicts[0].resource == "file:docs"

    release_parent_by_rev = _record(
        "claim-release", 3,
        {"ref_claim_id": "rev:1", "reason": "done"},
        tool="pi",
    )
    assert [c.event_id for c in active_claims([parent, child, release_parent_by_rev])] == [child["id"]]


def test_active_blockers():
    # intent: blockers are queryable coordination stop-signs for diagnose/reporting.
    blocker = _record(
        "blocker", 1,
        {"subject": "need branch", "reason": "which branch?", "resource": "task:review", "severity": "blocked"},
        event_id="evt_" + "4" * 32,
        tool="codex",
    )
    assert active_blockers([blocker])[0].resource == "task:review"
    assert active_blockers([blocker], tool="pi") == []
    resolved = _record(
        "blocker-resolved", 2,
        {"ref_blocker_id": blocker["id"], "resolution": "branch supplied"},
        tool="pi",
    )
    assert active_blockers([blocker, resolved]) == []
    resolved_by_rev = _record(
        "blocker-resolved", 3,
        {"ref_blocker_id": "rev:1", "resolution": "branch supplied"},
        tool="pi",
    )
    assert active_blockers([blocker, resolved_by_rev]) == []
