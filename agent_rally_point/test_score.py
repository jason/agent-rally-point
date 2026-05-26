# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for deterministic coordination scoring."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from agent_rally_point.changes import make_record  # noqa: E402
from agent_rally_point.score import score_records  # noqa: E402


def _record(kind: str, rev: int, payload: dict, **kw) -> dict:
    return make_record(
        kind=kind, tool=kw.pop("tool", "pi"), model="m", run_id="r",
        app_slug="app", payload=payload, revision=rev, **kw,
    )


def test_score_flags_open_required_handoff():
    # intent: scorer catches the highest-value coordination failure first.
    handoff = _record(
        "handoff", 1,
        {"from_tool": "pi", "to_tool": "codex", "subject": "review", "requires_ack": True},
        event_id="evt_" + "1" * 32,
    )
    score, findings = score_records([handoff], tool="codex")
    assert score == 75
    assert [(f.severity, f.code) for f in findings] == [("P1", "open-required-handoff")]


def test_score_flags_dangling_references_and_unresolved_needs_info():
    # intent: scorer detects broken causal links and unresolved request-for-info states.
    ack = _record("ack", 1, {"ref_handoff_id": "evt_missing", "verdict": "needs-info"})
    child = _record("phase", 2, {"phase": "followup"}, causation_id="evt_missing_parent")
    score, findings = score_records([ack, child])
    codes = [f.code for f in findings]
    assert "dangling-reference" in codes
    assert "dangling-causation" in codes
    assert "unresolved-needs-info" in codes
    assert score == 70


def test_score_clean_acknowledged_handoff_is_100():
    # intent: a complete handoff lifecycle has no coordination findings.
    handoff = _record(
        "handoff", 1,
        {"from_tool": "pi", "to_tool": "codex", "subject": "review", "requires_ack": True},
        event_id="evt_" + "1" * 32,
    )
    ack = _record(
        "ack", 2, {"ref_handoff_id": handoff["id"], "verdict": "done"},
        causation_id=handoff["id"], thread_id=handoff["thread_id"], tool="codex",
    )
    score, findings = score_records([handoff, ack], tool="codex")
    assert score == 100
    assert findings == []
