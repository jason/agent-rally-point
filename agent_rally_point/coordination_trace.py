#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Coordination trace query helpers for ``changes.jsonl``.

This module is read-only. It formats and groups append-only trace records but
never mutates a channel. CLI commands such as ``report``, ``replay``,
``thread``, and ``inbox`` build on these helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import time
from typing import Iterable

from .changes import read_changes_since

_DURATION_RE = re.compile(r"^(?P<num>\d+)(?P<unit>[smhd])$")


@dataclass(frozen=True)
class PendingHandoff:
    """An unacknowledged handoff addressed to a tool or broadcast."""

    event_id: str
    thread_id: str | None
    from_tool: str | None
    to_tool: str | None
    subject: str
    age_seconds: int | None
    files: tuple[str, ...]


@dataclass(frozen=True)
class ActiveClaim:
    """An unreleased ownership claim over a coordination resource."""

    event_id: str
    thread_id: str | None
    owner_tool: str | None
    resource: str
    subject: str
    age_seconds: int | None


@dataclass(frozen=True)
class ActiveBlocker:
    """An unresolved blocker recorded in the coordination trace."""

    event_id: str
    thread_id: str | None
    tool: str | None
    subject: str
    resource: str | None
    severity: str
    age_seconds: int | None


@dataclass(frozen=True)
class ClaimConflict:
    """Two active claims that appear to cover the same resource."""

    resource: str
    claim_ids: tuple[str, ...]
    owners: tuple[str, ...]


def parse_since(value: str | None, *, now: float | None = None) -> float | None:
    """Return an epoch cutoff for a duration like ``2h`` or an ISO timestamp.

    ``None`` returns ``None`` (no cutoff). Supported durations: seconds,
    minutes, hours, days. ISO/RFC3339 timestamps are accepted for scripts.
    """
    if not value:
        return None
    text = value.strip()
    m = _DURATION_RE.match(text)
    if m:
        n = int(m.group("num"))
        unit = m.group("unit")
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
        return (time.time() if now is None else now) - seconds
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid --since value {value!r}; use e.g. 30m, 2h, 1d") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def load_records(channel_dir: Path) -> list[dict]:
    """Read all complete change records from ``channel_dir``."""
    records, _ = read_changes_since(Path(channel_dir), 0)
    return records


def record_epoch(record: dict) -> float:
    """Return the best epoch timestamp for ``record``."""
    ts = record.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    text = record.get("time")
    if isinstance(text, str):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


def filter_since(records: Iterable[dict], cutoff: float | None) -> list[dict]:
    """Return records at or after ``cutoff`` preserving trace order."""
    if cutoff is None:
        return list(records)
    return [r for r in records if record_epoch(r) >= cutoff]


def record_id(record: dict) -> str:
    """Return a stable display id for canonical and pre-canonical records."""
    rid = record.get("id")
    if isinstance(rid, str) and rid:
        return rid
    payload = record.get("payload") or {}
    pid = payload.get("id")
    if isinstance(pid, str) and pid:
        return pid
    rev = record.get("revision")
    if rev is not None:
        return f"rev:{rev}"
    return "(no-id)"


def record_aliases(record: dict) -> set[str]:
    """Return ids that may be used to refer to ``record``."""
    aliases = {record_id(record)}
    rid = record.get("id")
    if isinstance(rid, str) and rid:
        aliases.add(rid)
    payload = record.get("payload") or {}
    pid = payload.get("id")
    if isinstance(pid, str) and pid:
        aliases.add(pid)
    rev = record.get("revision")
    if rev is not None:
        aliases.add(f"rev:{rev}")
    return aliases


def event_label(record: dict) -> str:
    """Return a one-line human summary for ``record``."""
    payload = record.get("payload") or {}
    kind = record.get("kind", "unknown")
    tool = record.get("tool", "unknown")
    # Prefer payload-level subject (richer, set by producer convention); fall
    # back to the top-level CloudEvents `subject` field, ignoring the
    # placeholder where it merely echoes the channel app_slug.
    top_subject = record.get("subject")
    if top_subject == record.get("app_slug"):
        top_subject = None
    subject = (
        payload.get("subject")
        or payload.get("work_item")
        or payload.get("summary")
        or top_subject
    )
    if kind == "handoff":
        to_tool = payload.get("to_tool") or payload.get("to")
        return f"{tool} handoff -> {to_tool or '?'}: {subject or '(no subject)'}"
    if kind == "ack":
        verdict = payload.get("verdict") or payload.get("status") or "ack"
        ref = payload.get("ref_handoff_id") or payload.get("ref_event_id")
        return f"{tool} ack {ref or ''}: {verdict}".strip()
    if kind == "feedback":
        verdict = payload.get("verdict") or "feedback"
        return f"{tool} feedback: {verdict} {subject or payload.get('rationale', '')}".strip()
    if kind == "phase":
        phase = payload.get("phase", "(unknown phase)")
        return f"{tool} phase: {phase} {subject or ''}".strip()
    if kind == "commit":
        sha = payload.get("commit_sha") or payload.get("sha") or ""
        return f"{tool} commit {sha}: {payload.get('subject', '')}".strip()
    if kind == "dep-change":
        return f"{tool} dependency changed: {payload.get('manifest', '(unknown manifest)')}"
    if kind == "claim":
        resource = payload.get("resource") or payload.get("path") or "(unknown resource)"
        return f"{tool} claim {resource}: {subject or '(no subject)'}"
    if kind == "claim-release":
        ref = payload.get("ref_claim_id") or payload.get("ref_event_id") or ""
        return f"{tool} release claim {ref}: {payload.get('reason', '')}".strip()
    if kind == "blocker":
        resource = payload.get("resource") or payload.get("path")
        suffix = f" on {resource}" if resource else ""
        return f"{tool} blocker{suffix}: {subject or payload.get('reason', '')}".strip()
    if kind == "blocker-resolved":
        ref = payload.get("ref_blocker_id") or payload.get("ref_event_id") or ""
        return f"{tool} resolved blocker {ref}: {payload.get('resolution', '')}".strip()
    return f"{tool} {kind}: {subject or ''}".strip()


def format_time(record: dict) -> str:
    """Return a compact UTC timestamp for display."""
    epoch = record_epoch(record)
    if epoch <= 0:
        return "????-??-?? ??:??:??Z"
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def related_records(records: Iterable[dict], identifier: str) -> list[dict]:
    """Return records related to ``identifier`` by id, thread, or causation.

    The expansion is transitive: once a matching record reveals a thread id,
    correlation id, or record id, records referencing that value are included.
    Legacy payload ids and ack reference fields are included for compatibility.

    Complexity: O(n²) worst case in the number of records, because the
    transitive-closure loop rescans the full record list per id discovered.
    Acceptable for the channel sizes seen in practice (~10³ records); revisit
    with an index if channels grow into the 10⁵+ range. Dangling causation
    ids (parent event not present in this channel) are tolerated — the
    lookup returns whatever IS present without raising.
    """
    all_records = list(records)
    ids = {identifier}
    changed = True
    while changed:
        changed = False
        for rec in all_records:
            payload = rec.get("payload") or {}
            values = {
                rec.get("id"), rec.get("thread_id"), rec.get("correlation_id"),
                rec.get("causation_id"), payload.get("id"),
                payload.get("ref_handoff_id"), payload.get("ref_event_id"),
                payload.get("ref_claim_id"), payload.get("ref_blocker_id"),
                payload.get("checkpoint_id"),
            }
            if ids.intersection(v for v in values if isinstance(v, str)):
                for v in values:
                    if isinstance(v, str) and v and v not in ids:
                        ids.add(v)
                        changed = True
    return [
        rec for rec in all_records
        if ids.intersection(
            v for v in {
                rec.get("id"), rec.get("thread_id"), rec.get("correlation_id"),
                rec.get("causation_id"), (rec.get("payload") or {}).get("id"),
                (rec.get("payload") or {}).get("ref_handoff_id"),
                (rec.get("payload") or {}).get("ref_event_id"),
                (rec.get("payload") or {}).get("ref_claim_id"),
                (rec.get("payload") or {}).get("ref_blocker_id"),
            }
            if isinstance(v, str)
        )
    ]


def _handoff_target(payload: dict) -> str | None:
    to_tool = payload.get("to_tool") or payload.get("to")
    return to_tool if isinstance(to_tool, str) else None


def _handoff_source(record: dict, payload: dict) -> str | None:
    from_tool = payload.get("from_tool") or payload.get("from") or record.get("tool")
    return from_tool if isinstance(from_tool, str) else None


def acked_handoff_ids(records: Iterable[dict]) -> set[str]:
    """Return ids referenced by ack records."""
    acked: set[str] = set()
    for rec in records:
        if rec.get("kind") != "ack":
            continue
        payload = rec.get("payload") or {}
        for key in ("ref_handoff_id", "ref_event_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                acked.add(value)
    return acked


def pending_handoffs(records: Iterable[dict], *, tool: str | None = None) -> list[PendingHandoff]:
    """Return open handoffs, optionally filtered to ``tool``.

    Filtering semantics when ``tool`` is set:
      * include handoffs addressed ``to`` ``tool`` or to ``"all"``/unaddressed
        (broadcast), AND
      * EXCLUDE handoffs where ``from_tool == tool`` — i.e. your own outbox
        is intentionally not in your inbox. To see what you've sent, use
        ``report --tool <you>`` or omit ``--tool``.

    Handoffs are considered closed once any ``ack``-kind record references
    them via ``ref_handoff_id`` or ``ref_event_id``; ``requires_ack=False``
    on the original handoff also marks it as not needing acknowledgement.
    """
    recs = list(records)
    acked = acked_handoff_ids(recs)
    pending: list[PendingHandoff] = []
    now = time.time()
    for rec in recs:
        if rec.get("kind") != "handoff":
            continue
        payload = rec.get("payload") or {}
        requires_ack = payload.get("requires_ack", True)
        if requires_ack is False:
            continue
        hid = record_id(rec)
        if record_aliases(rec).intersection(acked):
            continue
        to_tool = _handoff_target(payload)
        from_tool = _handoff_source(rec, payload)
        if tool:
            if to_tool not in (tool, "all", None):
                continue
            if from_tool == tool:
                continue
        refs = payload.get("ref_files") or payload.get("files") or []
        if not isinstance(refs, list):
            refs = []
        subject = payload.get("subject") or payload.get("work_item") or payload.get("notes") or "(no subject)"
        pending.append(PendingHandoff(
            event_id=hid,
            thread_id=rec.get("thread_id") if isinstance(rec.get("thread_id"), str) else None,
            from_tool=from_tool,
            to_tool=to_tool,
            subject=str(subject)[:120],
            age_seconds=int(now - record_epoch(rec)) if record_epoch(rec) > 0 else None,
            files=tuple(str(x) for x in refs if isinstance(x, str)),
        ))
    return pending


def _released_claim_ids(records: Iterable[dict]) -> set[str]:
    released: set[str] = set()
    for rec in records:
        if rec.get("kind") != "claim-release":
            continue
        payload = rec.get("payload") or {}
        for key in ("ref_claim_id", "ref_event_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                released.add(value)
    return released


def active_claims(records: Iterable[dict], *, tool: str | None = None) -> list[ActiveClaim]:
    """Return active ownership claims, optionally filtered to ``tool``."""
    recs = list(records)
    released = _released_claim_ids(recs)
    out: list[ActiveClaim] = []
    now = time.time()
    for rec in recs:
        if rec.get("kind") != "claim":
            continue
        cid = record_id(rec)
        payload = rec.get("payload") or {}
        if record_aliases(rec).intersection(released):
            continue
        owner = payload.get("owner_tool") or payload.get("tool") or rec.get("tool")
        if tool and owner != tool:
            continue
        resource = payload.get("resource") or payload.get("path")
        if not isinstance(resource, str) or not resource:
            resource = "unknown"
        subject = payload.get("subject") or payload.get("notes") or "(no subject)"
        out.append(ActiveClaim(
            event_id=cid,
            thread_id=rec.get("thread_id") if isinstance(rec.get("thread_id"), str) else None,
            owner_tool=owner if isinstance(owner, str) else None,
            resource=resource,
            subject=str(subject)[:120],
            age_seconds=int(now - record_epoch(rec)) if record_epoch(rec) > 0 else None,
        ))
    return out


def _file_resource_path(resource: str) -> str | None:
    if not resource.startswith("file:"):
        return None
    return resource.removeprefix("file:").strip("/")


def _resources_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    lp = _file_resource_path(left)
    rp = _file_resource_path(right)
    if lp is None or rp is None:
        return False
    return lp.startswith(rp + "/") or rp.startswith(lp + "/")


def claim_conflicts(records: Iterable[dict]) -> list[ClaimConflict]:
    """Return conflicts among active claims from different owners.

    Non-file resources conflict on exact match. ``file:`` resources also
    conflict on path containment, so a claim on ``file:docs`` conflicts with a
    claim on ``file:docs/SCHEMA.md``.
    """
    claims = active_claims(records)
    by_resource: dict[str, list[ActiveClaim]] = {}
    for idx, left in enumerate(claims):
        for right in claims[idx + 1:]:
            if left.owner_tool == right.owner_tool:
                continue
            if not _resources_overlap(left.resource, right.resource):
                continue
            group_key = min((left.resource, right.resource), key=len)
            bucket = by_resource.setdefault(group_key, [])
            for claim in (left, right):
                if claim not in bucket:
                    bucket.append(claim)
    conflicts: list[ClaimConflict] = []
    for resource, claims in sorted(by_resource.items()):
        owners = sorted({c.owner_tool or "unknown" for c in claims})
        if len(claims) > 1 and len(owners) > 1:
            conflicts.append(ClaimConflict(
                resource=resource,
                claim_ids=tuple(c.event_id for c in claims),
                owners=tuple(owners),
            ))
    return conflicts


def active_blockers(records: Iterable[dict], *, tool: str | None = None) -> list[ActiveBlocker]:
    """Return blockers without a later ``blocker-resolved`` event."""
    recs = list(records)
    resolved: set[str] = set()
    for rec in recs:
        if rec.get("kind") != "blocker-resolved":
            continue
        payload = rec.get("payload") or {}
        for key in ("ref_blocker_id", "ref_event_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                resolved.add(value)
    out: list[ActiveBlocker] = []
    now = time.time()
    for rec in recs:
        if rec.get("kind") != "blocker":
            continue
        bid = record_id(rec)
        payload = rec.get("payload") or {}
        if record_aliases(rec).intersection(resolved):
            continue
        producer = rec.get("tool") if isinstance(rec.get("tool"), str) else None
        if tool and producer != tool:
            continue
        subject = payload.get("subject") or payload.get("reason") or "(no subject)"
        resource = payload.get("resource") or payload.get("path")
        severity = payload.get("severity") or "blocked"
        out.append(ActiveBlocker(
            event_id=bid,
            thread_id=rec.get("thread_id") if isinstance(rec.get("thread_id"), str) else None,
            tool=producer,
            subject=str(subject)[:120],
            resource=resource if isinstance(resource, str) else None,
            severity=str(severity),
            age_seconds=int(now - record_epoch(rec)) if record_epoch(rec) > 0 else None,
        ))
    return out


def find_record(records: Iterable[dict], identifier: str) -> dict | None:
    """Return the first record matching a canonical or legacy id."""
    for rec in records:
        payload = rec.get("payload") or {}
        if identifier in {rec.get("id"), payload.get("id"), f"rev:{rec.get('revision')}"}:
            return rec
    return None
