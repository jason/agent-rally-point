#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Coordination stuck-state diagnosis over Rally trace records."""
from __future__ import annotations

from dataclasses import dataclass

from .coordination_trace import active_blockers, active_claims, claim_conflicts
from .score import score_records


@dataclass(frozen=True)
class DiagnoseFinding:
    """One reason coordination appears stuck."""

    severity: str
    code: str
    message: str
    event_id: str | None = None
    recommendation: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    """Deterministic diagnosis result for a trace window."""

    status: str
    score: int
    findings: tuple[DiagnoseFinding, ...]


def diagnose_records(
    records: list[dict],
    *,
    state_records: list[dict] | None = None,
    tool: str | None = None,
    stale_after_seconds: int = 24 * 3600,
    since: str | None = None,
) -> Diagnosis:
    """Return a stuck-work diagnosis.

    ``records`` is the score/replay window. ``state_records`` is the state
    window used for open-state checks; callers should pass the full channel for
    non-thread diagnosis so old unresolved blockers/claims do not age out just
    because the score window is small.
    """
    state = records if state_records is None else state_records
    score, score_findings = score_records(records, tool=tool)
    findings: list[DiagnoseFinding] = [
        DiagnoseFinding(
            severity=f.severity,
            code=f.code,
            event_id=f.event_id,
            message=f.message,
            recommendation=(
                f"rally thread {f.event_id}" if f.event_id else "rally replay --since 2h"
            ),
        )
        for f in score_findings
    ]

    for blocker in active_blockers(state, tool=tool):
        findings.append(DiagnoseFinding(
            severity="P1",
            code="active-blocker",
            event_id=blocker.event_id,
            message=f"blocker from {blocker.tool or 'unknown'}: {blocker.subject}",
            recommendation=f"rally thread {blocker.event_id}",
        ))

    for conflict in claim_conflicts(state):
        findings.append(DiagnoseFinding(
            severity="P1",
            code="claim-conflict",
            event_id=conflict.claim_ids[0] if conflict.claim_ids else None,
            message=f"resource {conflict.resource} is claimed by {', '.join(conflict.owners)}",
            recommendation=f"rally conflicts --since {since}" if since else "rally conflicts",
        ))

    for claim in active_claims(state, tool=tool):
        if claim.age_seconds is not None and claim.age_seconds >= stale_after_seconds:
            findings.append(DiagnoseFinding(
                severity="P2",
                code="stale-claim",
                event_id=claim.event_id,
                message=f"claim on {claim.resource} by {claim.owner_tool or 'unknown'} is stale",
                recommendation=f"rally release {claim.event_id} --reason 'done or abandoned'",
            ))

    return Diagnosis(
        status="healthy" if not findings else "stuck",
        score=score,
        findings=tuple(findings),
    )
