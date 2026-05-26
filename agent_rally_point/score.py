#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic coordination trace scoring.

The scorer checks trace invariants. It does not call models and does not judge
code quality; it judges whether coordination events form a coherent workflow.
"""
from __future__ import annotations

from dataclasses import dataclass

from .coordination_trace import pending_handoffs, record_id


@dataclass(frozen=True)
class ScoreFinding:
    """One deterministic coordination finding."""

    severity: str
    code: str
    message: str
    event_id: str | None = None


def _known_ids(records: list[dict]) -> set[str]:
    ids: set[str] = set()
    for rec in records:
        rid = rec.get("id")
        if isinstance(rid, str):
            ids.add(rid)
        payload = rec.get("payload") or {}
        pid = payload.get("id")
        if isinstance(pid, str):
            ids.add(pid)
        rev = rec.get("revision")
        if rev is not None:
            ids.add(f"rev:{rev}")
    return ids


def _final_ack_by_handoff(records: list[dict]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for rec in records:
        if rec.get("kind") != "ack":
            continue
        payload = rec.get("payload") or {}
        ref = payload.get("ref_handoff_id") or payload.get("ref_event_id")
        verdict = payload.get("verdict")
        if isinstance(ref, str) and isinstance(verdict, str):
            latest[ref] = verdict
    return latest


def score_records(records: list[dict], *, tool: str | None = None) -> tuple[int, list[ScoreFinding]]:
    """Return ``(score, findings)`` for a coordination trace window.

    Score starts at 100. P1 findings subtract 25, P2 subtract 10, P3 subtract 3.
    The value is capped at zero. Findings are intentionally deterministic and
    trace-local so they can run in commit hooks or CI.
    """
    findings: list[ScoreFinding] = []
    ids = _known_ids(records)

    for item in pending_handoffs(records, tool=tool):
        findings.append(ScoreFinding(
            severity="P1",
            code="open-required-handoff",
            event_id=item.event_id,
            message=f"required handoff to {item.to_tool or 'unknown'} is still open: {item.subject}",
        ))

    for rec in records:
        causation = rec.get("causation_id")
        if isinstance(causation, str) and causation and causation not in ids:
            findings.append(ScoreFinding(
                severity="P2",
                code="dangling-causation",
                event_id=record_id(rec),
                message=f"causation_id {causation} does not resolve in this trace window",
            ))
        if rec.get("kind") in ("ack", "feedback"):
            payload = rec.get("payload") or {}
            ref = payload.get("ref_handoff_id") or payload.get("ref_event_id")
            if isinstance(ref, str) and ref and ref not in ids:
                findings.append(ScoreFinding(
                    severity="P2",
                    code="dangling-reference",
                    event_id=record_id(rec),
                    message=f"{rec.get('kind')} references missing handoff/event {ref}",
                ))

    for ref, verdict in _final_ack_by_handoff(records).items():
        if verdict == "needs-info":
            findings.append(ScoreFinding(
                severity="P2",
                code="unresolved-needs-info",
                event_id=ref,
                message="handoff is still waiting on more information",
            ))

    penalty = {"P1": 25, "P2": 10, "P3": 3}
    score = max(0, 100 - sum(penalty.get(f.severity, 0) for f in findings))
    return score, findings
