#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""App Pulse change log — append-only, immutable, cross-tool event stream.

One ``changes.jsonl`` per channel. Each line is one self-contained JSON
record. Writes use ``O_APPEND`` with a single ``os.write`` of one
``json.dumps(...) + "\\n"``; POSIX guarantees an ``O_APPEND`` write up to
``PIPE_BUF`` is atomic, so concurrent writers from any tool never tear a
line and need no lock. The log is **immutable**: this module exposes
only ``append_change`` and ``read_changes_since`` — no rewrite, delete,
or truncate entry point exists (by design, see ``test_no_mutation_api``).

Record schema (defined here once; D7 — unknown ``kind`` warns, never
drops). Records carry canonical event identity/correlation metadata
aligned with CloudEvents 1.0 plus ARP-specific causal fields. Pre-0.4
records that still carry the legacy epoch-seconds ``ts`` field are
tolerated on read; new records emit only the RFC3339 ``time`` field:

    {specversion, id, source, subject, time, kind, type, tool, model,
     run_id, app_slug, thread_id, causation_id, correlation_id,
     datacontenttype, dataschema, payload{...}, revision}

NON-GOAL guard: records carry structure/data-flow only — never any
call-frequency / invocation-count field.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

_LOG_NAME = "changes.jsonl"

# Known kinds (D7: this is advisory — unknown kinds warn but are kept).
# Added 2026-05-20 for coordination dogfood: "feedback" (verifier verdicts:
# PASS/VARIANCE/BLOCKED) and "handoff" (plan owner → verifier work-item
# transfer). Both surface in checkpoint_read like any other kind.
KNOWN_KINDS = (
    "commit",
    "dep-change",
    "phase",
    "arch-scan-complete",
    "feedback",
    "handoff",
    "ack",
)

DEFAULT_DATA_CONTENT_TYPE = "application/json"
CLOUDEVENTS_SPECVERSION = "1.0"

_KIND_TYPES = {
    "commit": "agent-rally.commit.created.v1",
    "dep-change": "agent-rally.dependency.changed.v1",
    "phase": "agent-rally.phase.recorded.v1",
    "arch-scan-complete": "agent-rally.arch-scan.completed.v1",
    "feedback": "agent-rally.feedback.posted.v1",
    "handoff": "agent-rally.handoff.created.v1",
    "ack": "agent-rally.handoff.acknowledged.v1",
}

_RECORD_KEYS = (
    "kind", "tool", "model", "run_id", "app_slug", "payload",
    "revision",
)

def _log_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _LOG_NAME


def _new_id(prefix: str) -> str:
    """Return a stable opaque id without adding runtime dependencies."""
    return f"{prefix}_{uuid.uuid4().hex}"


def new_event_id() -> str:
    """Return a new canonical event id."""
    return _new_id("evt")


def new_thread_id() -> str:
    """Return a new canonical thread/correlation id."""
    return _new_id("thr")


def _format_time(ts: float) -> str:
    """Return RFC3339 UTC time with millisecond precision."""
    return (
        datetime.fromtimestamp(ts, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_urn_component(value: str) -> str:
    """Return a conservative URN component for producer-controlled strings."""
    safe = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "-"
        for ch in value
    )
    return safe.strip("-") or "unknown"


def source_for_tool(tool: str) -> str:
    """Return the canonical event source URI for a producer tool."""
    return f"urn:agent-rally-point:tool:{_safe_urn_component(tool or 'unknown')}"


def event_type_for_kind(kind: str) -> str:
    """Return the canonical event type for a record kind."""
    return _KIND_TYPES.get(kind, f"agent-rally.{_safe_urn_component(kind)}.v1")


def dataschema_for_type(event_type: str) -> str:
    """Return the canonical payload schema URI for ``event_type``."""
    suffix = event_type.removeprefix("agent-rally.")
    return f"urn:agent-rally-point:schema:{suffix}"


def make_record(
    *,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict,
    revision: int,
    event_id: str | None = None,
    source: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    thread_id: str | None = None,
    causation_id: str | None = None,
    correlation_id: str | None = None,
    dataschema: str | None = None,
    time_rfc3339: str | None = None,
) -> dict:
    """Build a well-formed change record (schema single source of truth)."""
    ts = time.time()
    canonical_type = event_type or event_type_for_kind(kind)
    canonical_thread = thread_id or new_thread_id()
    producer_tool = tool or "unknown"
    return {
        "specversion": CLOUDEVENTS_SPECVERSION,
        "id": event_id or new_event_id(),
        "source": source or source_for_tool(producer_tool),
        "subject": subject or app_slug,
        "time": time_rfc3339 or _format_time(ts),
        "kind": kind,
        "type": canonical_type,
        "tool": producer_tool,
        "model": model or "unknown",
        "run_id": run_id or "unknown",
        "app_slug": app_slug,
        "thread_id": canonical_thread,
        "causation_id": causation_id,
        "correlation_id": correlation_id or canonical_thread,
        "datacontenttype": DEFAULT_DATA_CONTENT_TYPE,
        "dataschema": dataschema or dataschema_for_type(canonical_type),
        "payload": payload or {},
        "revision": int(revision),
    }


def validate_record(record: dict) -> dict:
    """Return ``record`` always (immutable, warns-not-drops — D7).

    Emits a stderr warning for an unknown ``kind`` or a missing required
    key but never raises and never mutates/drops — the consumer decides.
    """
    missing = [k for k in _RECORD_KEYS if k not in record]
    if missing:
        print(
            f"app-pulse: change record missing keys {missing} (kept anyway)",
            file=sys.stderr,
        )
    if record.get("kind") not in KNOWN_KINDS:
        print(
            f"app-pulse: unknown change kind {record.get('kind')!r} "
            f"(warns-not-drops per D7)",
            file=sys.stderr,
        )
    return record


def append_change(channel_dir: Path, record: dict) -> None:
    """Atomically append one record line. Fire-and-forget, never raises.

    O_APPEND single-write — atomic up to PIPE_BUF, no lock, safe across
    tools/processes. Failure is swallowed (never blocks a host action).
    """
    try:
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        fd = os.open(
            str(_log_path(d)),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 — fire-and-forget contract
        return


def read_changes_since(channel_dir: Path, offset: int) -> tuple[list, int]:
    """Return ``(new_records, new_byte_offset)`` from byte ``offset``.

    Absent log → ``([], 0)``. Reader takes no lock and never writes the
    log. A trailing partial line (writer mid-append) is left for the
    next poll: the returned offset only advances past complete lines.
    """
    p = _log_path(channel_dir)
    try:
        size = p.stat().st_size
    except (FileNotFoundError, OSError):
        return [], 0
    if offset < 0 or offset > size:
        offset = 0
    records: list = []
    new_offset = offset
    try:
        with open(p, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partial trailing line — re-read next poll
                new_offset += len(raw)
                try:
                    records.append(json.loads(raw.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    continue  # skip a corrupt line, keep offset advancing
    except OSError:
        return [], offset
    return records, new_offset
