#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""agent-rally-point CLI.

Agentic surface: every command supports ``--json`` for structured output on
stdout. Errors in ``--json`` mode are serialized as ``{ok: false, error,
exit_code, ...}`` to stderr. Stable exit codes are documented in
``EXIT_CODES`` below and surfaced via ``agent-rally-point --json`` (no
subcommand) as a capability map.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from . import __version__
from .changes import new_event_id
from .coordination_trace import (
    active_blockers,
    active_claims,
    claim_conflicts,
    event_label,
    filter_since,
    find_record,
    format_time,
    load_records,
    parse_since,
    pending_handoffs,
    record_id,
    related_records,
)
from .discover import discover
from .diagnose import diagnose_records
from .herdr_bridge import inject_handoff, list_agents, report_pending_status
from .post import post
from .resources import resource_from_values
from .score import score_records

# Stable exit codes. Documented here once and surfaced in the capability map.
# 2 overlaps with argparse's usage error by design — both mean "input was
# rejected without doing work." Distinguish by the structured `error` field
# when running in --json mode.
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_NOT_FOUND = 2
EXIT_EXTERNAL = 4

EXIT_CODES = {
    "OK": EXIT_OK,
    "RUNTIME": EXIT_RUNTIME,
    "NOT_FOUND": EXIT_NOT_FOUND,
    "EXTERNAL": EXIT_EXTERNAL,
}


def _channel_dir(args: argparse.Namespace) -> tuple[Path, str]:
    if getattr(args, "channel_dir", None):
        return Path(args.channel_dir).expanduser(), Path.cwd().name
    info = discover(Path(getattr(args, "workdir", None) or Path.cwd()))
    channel = info.get("channel_dir")
    if not channel:
        raise RuntimeError("no channel_dir resolved for this repo")
    return Path(channel), info.get("app_slug") or Path.cwd().name


def _workdir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "workdir", None) or Path.cwd()).resolve()


def _tool(args: argparse.Namespace) -> str | None:
    value = getattr(args, "tool", None) or os.environ.get("AGENT_RALLY_TOOL") or os.environ.get("APP_PULSE_TOOL")
    return value or None


def _resource_arg(args: argparse.Namespace, *, required: bool = True) -> str | None:
    """Return canonical resource string from ``--resource`` or ``--path``."""
    return resource_from_values(
        resource=getattr(args, "resource", None),
        path=getattr(args, "path", None),
        workdir=_workdir(args),
        required=required,
    )


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _to_jsonable(obj):
    if is_dataclass(obj):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(_to_jsonable(x) for x in obj)
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _emit(args: argparse.Namespace, payload: dict, *, text_fn=None) -> int:
    """Emit a successful command result.

    In ``--json`` mode, ``payload`` is written as one JSON object to stdout.
    In text mode, ``text_fn`` is invoked (responsible for human output) and
    its return value (or 0) becomes the exit code.
    """
    # `ok` is reserved for the structured envelope; payload keys must not collide.
    assert "ok" not in payload, "command payload must not set the reserved 'ok' field"
    if _json_mode(args):
        body = {"ok": True, **payload}
        sys.stdout.write(json.dumps(_to_jsonable(body)) + "\n")
        return EXIT_OK
    if text_fn is not None:
        rc = text_fn()
        return int(rc) if rc is not None else EXIT_OK
    return EXIT_OK


def _die(args: argparse.Namespace, error: str, exit_code: int, **ctx) -> int:
    """Emit a structured error and return ``exit_code``."""
    if _json_mode(args):
        body = {"ok": False, "error": error, "exit_code": exit_code, **ctx}
        sys.stderr.write(json.dumps(_to_jsonable(body)) + "\n")
    else:
        sys.stderr.write(f"agent-rally-point: {error}\n")
    return exit_code


def _record_summary(rec: dict, *, include_id: bool = True) -> dict:
    """Stable JSON shape for a trace record line."""
    out = {
        "revision": rec.get("revision"),
        "time": format_time(rec),
        "kind": rec.get("kind"),
        "tool": rec.get("tool"),
        "label": event_label(rec),
    }
    if include_id:
        out["id"] = record_id(rec)
    return out


def _canonical_reference(target: dict | None, fallback: str) -> str:
    """Return the event id that lifecycle close events should reference."""
    return record_id(target) if target is not None else fallback


def _print_timeline(records: list[dict], *, show_ids: bool = False) -> None:
    if not records:
        print("No trace events found.")
        return
    for rec in records:
        prefix = f"{format_time(rec)}  r{rec.get('revision', '?'):>4}"
        if show_ids:
            prefix += f"  {record_id(rec)}"
        print(f"{prefix}  {event_label(rec)}")


def cmd_report(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    cutoff = parse_since(args.since)
    records = filter_since(load_records(channel), cutoff)
    windowed = records[-args.limit:]
    pending = pending_handoffs(records, tool=_tool(args))

    def text():
        print(f"Trace report: {channel}")
        if args.since:
            print(f"Window: since {args.since}")
        print()
        _print_timeline(windowed, show_ids=args.ids)
        print()
        print(f"Pending handoffs: {len(pending)}")
        for item in pending:
            age = f", age={item.age_seconds}s" if item.age_seconds is not None else ""
            files = f", files={','.join(item.files)}" if item.files else ""
            print(f"- {item.event_id} from={item.from_tool} to={item.to_tool}: {item.subject}{age}{files}")

    return _emit(args, {
        "command": "report",
        "channel": str(channel),
        "since": args.since,
        "events": [_record_summary(r, include_id=args.ids) for r in windowed],
        "pending": [_to_jsonable(p) for p in pending],
    }, text_fn=text)


def cmd_score(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = load_records(channel)
    if args.thread:
        records = related_records(records, args.thread)
    else:
        records = filter_since(records, parse_since(args.since))
    score, findings = score_records(records, tool=_tool(args))

    def text():
        print(f"Coordination score: {score}/100")
        if args.thread:
            print(f"Thread: {args.thread}")
        elif args.since:
            print(f"Window: since {args.since}")
        if not findings:
            print("No coordination issues found.")
            return 0
        for finding in findings:
            event = f" [{finding.event_id}]" if finding.event_id else ""
            print(f"- [{finding.severity}] {finding.code}{event}: {finding.message}")
        return 0

    return _emit(args, {
        "command": "score",
        "channel": str(channel),
        "thread": args.thread,
        "since": args.since,
        "score": score,
        "findings": [_to_jsonable(f) for f in findings],
    }, text_fn=text)


def cmd_replay(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = load_records(channel)
    if args.thread:
        records = related_records(records, args.thread)
    else:
        records = filter_since(records, parse_since(args.since))
    windowed = records[-args.limit:]

    def text():
        print(f"Trace replay: {channel}")
        if args.thread:
            print(f"Thread: {args.thread}")
        elif args.since:
            print(f"Window: since {args.since}")
        print()
        _print_timeline(windowed, show_ids=True)

    return _emit(args, {
        "command": "replay",
        "channel": str(channel),
        "thread": args.thread,
        "since": args.since,
        "events": [_record_summary(r, include_id=True) for r in windowed],
    }, text_fn=text)


def cmd_thread(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = related_records(load_records(channel), args.identifier)

    def text():
        print(f"Thread: {args.identifier}")
        print(f"Channel: {channel}")
        print()
        _print_timeline(records, show_ids=True)

    return _emit(args, {
        "command": "thread",
        "channel": str(channel),
        "thread": args.identifier,
        "events": [_record_summary(r, include_id=True) for r in records],
    }, text_fn=text)


def cmd_inbox(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = filter_since(load_records(channel), parse_since(args.since))
    pending = pending_handoffs(records, tool=_tool(args))

    def text():
        if not pending:
            print("Inbox empty.")
            return
        for item in pending:
            age = f" age={item.age_seconds}s" if item.age_seconds is not None else ""
            print(f"{item.event_id} from={item.from_tool} to={item.to_tool}{age}")
            print(f"  {item.subject}")
            if item.files:
                print(f"  files: {', '.join(item.files)}")

    return _emit(args, {
        "command": "inbox",
        "channel": str(channel),
        "tool": _tool(args),
        "pending": [_to_jsonable(p) for p in pending],
    }, text_fn=text)


def cmd_handoff(args: argparse.Namespace) -> int:
    channel, app_slug = _channel_dir(args)
    from_tool = args.from_tool or _tool(args) or "unknown"
    event_id = new_event_id()
    payload = {
        "from_tool": from_tool,
        "to_tool": args.to,
        "subject": args.subject,
        "requires_ack": not args.no_ack,
    }
    if args.files:
        payload["ref_files"] = args.files
    if args.notes:
        payload["notes"] = args.notes
    rev = post(
        channel_dir=channel,
        kind="handoff",
        tool=from_tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        event_id=event_id,
        subject=args.subject,
    )
    if rev is None:
        return _die(args, "failed to post handoff", EXIT_RUNTIME,
                    command="handoff", channel=str(channel))
    return _emit(args, {
        "command": "handoff",
        "channel": str(channel),
        "event_id": event_id,
        "revision": rev,
        "to_tool": args.to,
        "from_tool": from_tool,
        "subject": args.subject,
    }, text_fn=lambda: print(f"posted handoff {event_id} revision={rev} to={args.to}"))


def _ack(args: argparse.Namespace, verdict: str) -> int:
    channel, app_slug = _channel_dir(args)
    records = load_records(channel)
    target = find_record(records, args.identifier)
    if target is None and not getattr(args, "force", False):
        return _die(
            args,
            f"no handoff found for {args.identifier!r} in {channel}; "
            f"pass --force to ack anyway",
            EXIT_NOT_FOUND,
            command=f"ack:{verdict}",
            identifier=args.identifier,
            channel=str(channel),
        )
    tool = _tool(args) or args.tool or "unknown"
    ref = _canonical_reference(target, args.identifier)
    payload = {"ref_handoff_id": ref, "verdict": verdict}
    if getattr(args, "summary", None):
        payload["summary"] = args.summary
    if getattr(args, "reason", None):
        payload["reason"] = args.reason
    causation = ref
    thread = target.get("thread_id") if target else None
    rev = post(
        channel_dir=channel,
        kind="ack",
        tool=tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        causation_id=causation,
        thread_id=thread,
        subject=args.identifier,
    )
    if rev is None:
        return _die(args, "failed to post ack", EXIT_RUNTIME,
                    command=f"ack:{verdict}", channel=str(channel))
    return _emit(args, {
        "command": f"ack:{verdict}",
        "channel": str(channel),
        "verdict": verdict,
        "ref_handoff_id": ref,
        "revision": rev,
        "resolved": target is not None,
    }, text_fn=lambda: print(f"posted {verdict} ack for {args.identifier} revision={rev}"))


def cmd_ack(args: argparse.Namespace) -> int:
    return _ack(args, "done")


def cmd_reject(args: argparse.Namespace) -> int:
    return _ack(args, "rejected")


def cmd_needs_info(args: argparse.Namespace) -> int:
    return _ack(args, "needs-info")


def cmd_herdr_status(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = filter_since(load_records(channel), parse_since(args.since))
    pending = pending_handoffs(records, tool=_tool(args))
    agents = list_agents()
    reports = list(report_pending_status(
        pending,
        cwd=str(_workdir(args)),
        allow_other_workspace=args.allow_other_workspace,
    )) if args.report else []

    def text():
        print("Herdr agents:")
        if not agents:
            print("- none detected (is this running inside Herdr?)")
        for agent in agents:
            cwd = f" cwd={agent.cwd}" if agent.cwd else ""
            print(f"- {agent.agent} pane={agent.pane_id} status={agent.status}{cwd}")
        print()
        print(f"Pending ARP handoffs: {len(pending)}")
        for item in pending:
            print(f"- {item.event_id} to={item.to_tool}: {item.subject}")
        if reports:
            print()
            for line in reports:
                print(line)

    return _emit(args, {
        "command": "herdr:status",
        "channel": str(channel),
        "agents": [_to_jsonable(a) for a in agents],
        "pending": [_to_jsonable(p) for p in pending],
        "reports": reports,
    }, text_fn=text)


def cmd_herdr_inject(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    try:
        result = inject_handoff(
            load_records(channel), args.identifier,
            cwd=str(_workdir(args)),
            allow_other_workspace=args.allow_other_workspace,
        )
    except (RuntimeError, ValueError) as exc:
        return _die(args, str(exc), EXIT_EXTERNAL,
                    command="herdr:inject", identifier=args.identifier)
    return _emit(args, {
        "command": "herdr:inject",
        "channel": str(channel),
        "identifier": args.identifier,
        "result": result,
    }, text_fn=lambda: print(result))


def cmd_claim(args: argparse.Namespace) -> int:
    channel, app_slug = _channel_dir(args)
    tool = _tool(args) or "unknown"
    resource = _resource_arg(args)
    event_id = new_event_id()
    payload = {"owner_tool": tool, "resource": resource, "subject": args.subject}
    if getattr(args, "notes", None):
        payload["notes"] = args.notes
    rev = post(
        channel_dir=channel,
        kind="claim",
        tool=tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        event_id=event_id,
        subject=args.subject,
    )
    if rev is None:
        return _die(args, "failed to post claim", EXIT_RUNTIME,
                    command="claim", channel=str(channel))
    return _emit(args, {
        "command": "claim",
        "channel": str(channel),
        "event_id": event_id,
        "revision": rev,
        "tool": tool,
        "resource": resource,
        "subject": args.subject,
    }, text_fn=lambda: print(f"posted claim {event_id} revision={rev} resource={resource}"))


def cmd_release(args: argparse.Namespace) -> int:
    channel, app_slug = _channel_dir(args)
    records = load_records(channel)
    target = find_record(records, args.identifier)
    if (target is None or target.get("kind") != "claim") and not getattr(args, "force", False):
        return _die(
            args,
            f"no claim found for {args.identifier!r} in {channel}; pass --force to release anyway",
            EXIT_NOT_FOUND,
            command="release",
            identifier=args.identifier,
            channel=str(channel),
        )
    tool = _tool(args) or "unknown"
    ref = _canonical_reference(target, args.identifier)
    payload = {"ref_claim_id": ref}
    if getattr(args, "reason", None):
        payload["reason"] = args.reason
    rev = post(
        channel_dir=channel,
        kind="claim-release",
        tool=tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        causation_id=ref,
        thread_id=target.get("thread_id") if target else None,
        subject=args.identifier,
    )
    if rev is None:
        return _die(args, "failed to post claim release", EXIT_RUNTIME,
                    command="release", channel=str(channel))
    return _emit(args, {
        "command": "release",
        "channel": str(channel),
        "ref_claim_id": ref,
        "revision": rev,
        "resolved": target is not None and target.get("kind") == "claim",
    }, text_fn=lambda: print(f"released claim {args.identifier} revision={rev}"))


def cmd_claims(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = filter_since(load_records(channel), parse_since(args.since))
    claims = active_claims(records, tool=_tool(args))

    def text():
        if not claims:
            print("No active claims.")
            return
        for item in claims:
            age = f" age={item.age_seconds}s" if item.age_seconds is not None else ""
            print(f"{item.event_id} owner={item.owner_tool or 'unknown'} resource={item.resource}{age}")
            print(f"  {item.subject}")

    return _emit(args, {
        "command": "claims",
        "channel": str(channel),
        "claims": [_to_jsonable(c) for c in claims],
    }, text_fn=text)


def cmd_blocker(args: argparse.Namespace) -> int:
    channel, app_slug = _channel_dir(args)
    tool = _tool(args) or "unknown"
    resource = _resource_arg(args, required=False)
    event_id = new_event_id()
    payload = {"subject": args.subject, "reason": args.reason or args.subject, "severity": args.severity}
    if resource:
        payload["resource"] = resource
    rev = post(
        channel_dir=channel,
        kind="blocker",
        tool=tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        event_id=event_id,
        subject=args.subject,
    )
    if rev is None:
        return _die(args, "failed to post blocker", EXIT_RUNTIME,
                    command="blocker", channel=str(channel))
    return _emit(args, {
        "command": "blocker",
        "channel": str(channel),
        "event_id": event_id,
        "revision": rev,
        "tool": tool,
        "resource": resource,
        "subject": args.subject,
        "severity": args.severity,
    }, text_fn=lambda: print(f"posted blocker {event_id} revision={rev}"))


def cmd_blockers(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = filter_since(load_records(channel), parse_since(args.since))
    blockers = active_blockers(records, tool=_tool(args))

    def text():
        if not blockers:
            print("No blockers.")
            return
        for item in blockers:
            age = f" age={item.age_seconds}s" if item.age_seconds is not None else ""
            resource = f" resource={item.resource}" if item.resource else ""
            print(f"{item.event_id} tool={item.tool or 'unknown'} severity={item.severity}{resource}{age}")
            print(f"  {item.subject}")

    return _emit(args, {
        "command": "blockers",
        "channel": str(channel),
        "blockers": [_to_jsonable(b) for b in blockers],
    }, text_fn=text)


def cmd_unblock(args: argparse.Namespace) -> int:
    channel, app_slug = _channel_dir(args)
    records = load_records(channel)
    target = find_record(records, args.identifier)
    if (target is None or target.get("kind") != "blocker") and not getattr(args, "force", False):
        return _die(
            args,
            f"no blocker found for {args.identifier!r} in {channel}; pass --force to resolve anyway",
            EXIT_NOT_FOUND,
            command="unblock",
            identifier=args.identifier,
            channel=str(channel),
        )
    tool = _tool(args) or "unknown"
    ref = _canonical_reference(target, args.identifier)
    payload = {"ref_blocker_id": ref, "resolution": args.resolution}
    rev = post(
        channel_dir=channel,
        kind="blocker-resolved",
        tool=tool,
        model=args.model or "unknown",
        run_id=args.run_id or "agent-rally-cli",
        app_slug=app_slug,
        payload=payload,
        causation_id=ref,
        thread_id=target.get("thread_id") if target else None,
        subject=args.identifier,
    )
    if rev is None:
        return _die(args, "failed to post blocker resolution", EXIT_RUNTIME,
                    command="unblock", channel=str(channel))
    return _emit(args, {
        "command": "unblock",
        "channel": str(channel),
        "ref_blocker_id": ref,
        "resolution": args.resolution,
        "revision": rev,
        "resolved": target is not None and target.get("kind") == "blocker",
    }, text_fn=lambda: print(f"resolved blocker {args.identifier} revision={rev}"))


def cmd_conflicts(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    records = filter_since(load_records(channel), parse_since(args.since))
    conflicts = claim_conflicts(records)

    def text():
        if not conflicts:
            print("No claim conflicts.")
            return
        for item in conflicts:
            print(f"{item.resource}: owners={','.join(item.owners)} claims={','.join(item.claim_ids)}")

    return _emit(args, {
        "command": "conflicts",
        "channel": str(channel),
        "conflicts": [_to_jsonable(c) for c in conflicts],
    }, text_fn=text)


def cmd_diagnose(args: argparse.Namespace) -> int:
    channel, _slug = _channel_dir(args)
    all_records = load_records(channel)
    tool = _tool(args)
    if args.thread:
        records = related_records(all_records, args.thread)
        state_records = records
    else:
        records = filter_since(all_records, parse_since(args.since))
        # Open-state checks must not age out merely because the opening event is
        # older than the score/replay window. An unresolved blocker from last
        # week is still a blocker today.
        state_records = all_records
    diagnosis = diagnose_records(
        records,
        state_records=state_records,
        tool=tool,
        stale_after_seconds=args.stale_after_seconds,
        since=args.since,
    )

    def text():
        print(f"Coordination diagnosis: {diagnosis.status} (score {diagnosis.score}/100)")
        if args.thread:
            print(f"Thread: {args.thread}")
        elif args.since:
            print(f"Window: since {args.since}")
        if not diagnosis.findings:
            print("No coordination blockers found.")
            return
        print("Coordination is stuck because:")
        for idx, item in enumerate(diagnosis.findings, 1):
            event = f" [{item.event_id}]" if item.event_id else ""
            print(f"{idx}. [{item.severity}] {item.code}{event}: {item.message}")
            if item.recommendation:
                print(f"   next: {item.recommendation}")

    return _emit(args, {
        "command": "diagnose",
        "channel": str(channel),
        "status": diagnosis.status,
        "score": diagnosis.score,
        "thread": args.thread,
        "since": args.since,
        "findings": [_to_jsonable(f) for f in diagnosis.findings],
    }, text_fn=text)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--channel-dir", help="read/write a specific channel directory")
    p.add_argument("--workdir", help="repo workdir for discovery; defaults to cwd")
    p.add_argument(
        "--json", action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON on stdout (errors as {ok:false,...} on stderr)",
    )


def _capability_map() -> dict:
    """Self-describing JSON for ``agent-rally-point --json`` (no subcommand)."""
    return {
        "ok": True,
        "tool": "agent-rally-point",
        "version": __version__,
        "commands": [
            "report", "score", "replay", "thread", "inbox",
            "handoff", "ack", "reject", "needs-info",
            "claim", "release", "claims", "blocker", "unblock", "blockers", "conflicts", "diagnose",
            "herdr status", "herdr inject",
        ],
        "exit_codes": EXIT_CODES,
        "notes": (
            "All commands accept --json for structured stdout. Writes (handoff, "
            "ack, reject, needs-info) require --force only when the referenced "
            "handoff is not present in the channel."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-rally-point")
    parser.add_argument(
        "--version", action="version", version=f"agent-rally-point {__version__}",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="when used without a subcommand, print the capability map as JSON",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    report = sub.add_parser("report", help="summarize recent coordination trace events")
    _add_common(report)
    report.add_argument("--since", default="24h")
    report.add_argument("--limit", type=int, default=80)
    report.add_argument("--tool")
    report.add_argument("--ids", action="store_true")
    report.set_defaults(func=cmd_report)

    score = sub.add_parser("score", help="score coordination trace invariants")
    _add_common(score)
    score.add_argument("--since", default="24h")
    score.add_argument("--thread")
    score.add_argument("--tool")
    score.set_defaults(func=cmd_score)

    replay = sub.add_parser("replay", help="print an interleaved coordination timeline")
    _add_common(replay)
    replay.add_argument("--since", default="24h")
    replay.add_argument("--thread")
    replay.add_argument("--limit", type=int, default=200)
    replay.set_defaults(func=cmd_replay)

    thread = sub.add_parser("thread", help="show events related to an id/thread")
    _add_common(thread)
    thread.add_argument("identifier")
    thread.set_defaults(func=cmd_thread)

    inbox = sub.add_parser("inbox", help="list open handoffs")
    _add_common(inbox)
    inbox.add_argument("--tool")
    inbox.add_argument("--since", default="7d")
    inbox.set_defaults(func=cmd_inbox)

    handoff = sub.add_parser("handoff", help="post a handoff")
    _add_common(handoff)
    handoff.add_argument("--to", required=True)
    handoff.add_argument("--subject", required=True)
    handoff.add_argument("--from-tool")
    handoff.add_argument("--files", nargs="*")
    handoff.add_argument("--notes")
    handoff.add_argument("--no-ack", action="store_true")
    handoff.add_argument("--model")
    handoff.add_argument("--run-id")
    handoff.set_defaults(func=cmd_handoff)

    ack = sub.add_parser("ack", help="acknowledge a handoff as done")
    _add_common(ack)
    ack.add_argument("identifier")
    ack.add_argument("--summary")
    ack.add_argument("--tool")
    ack.add_argument("--model")
    ack.add_argument("--run-id")
    ack.add_argument(
        "--force", action="store_true",
        help="post the ack even when the referenced handoff is not found in this channel",
    )
    ack.set_defaults(func=cmd_ack)

    reject = sub.add_parser("reject", help="reject a handoff")
    _add_common(reject)
    reject.add_argument("identifier")
    reject.add_argument("--reason", required=True)
    reject.add_argument("--tool")
    reject.add_argument("--model")
    reject.add_argument("--run-id")
    reject.add_argument("--force", action="store_true")
    reject.set_defaults(func=cmd_reject)

    needs = sub.add_parser("needs-info", help="mark a handoff as needing more information")
    _add_common(needs)
    needs.add_argument("identifier")
    needs.add_argument("--reason", required=True)
    needs.add_argument("--tool")
    needs.add_argument("--model")
    needs.add_argument("--run-id")
    needs.add_argument("--force", action="store_true")
    needs.set_defaults(func=cmd_needs_info)

    claim = sub.add_parser("claim", help="claim ownership of a resource")
    _add_common(claim)
    claim.add_argument("--resource", help="resource id, e.g. file:docs/SCHEMA.md or task:ABC-123")
    claim.add_argument("--path", help="file path sugar for --resource file:<path>")
    claim.add_argument("--subject", required=True)
    claim.add_argument("--tool")
    claim.add_argument("--notes")
    claim.add_argument("--model")
    claim.add_argument("--run-id")
    claim.set_defaults(func=cmd_claim)

    release = sub.add_parser("release", help="release an ownership claim")
    _add_common(release)
    release.add_argument("identifier")
    release.add_argument("--reason")
    release.add_argument("--tool")
    release.add_argument("--model")
    release.add_argument("--run-id")
    release.add_argument("--force", action="store_true")
    release.set_defaults(func=cmd_release)

    claims = sub.add_parser("claims", help="list active ownership claims")
    _add_common(claims)
    claims.add_argument("--tool")
    claims.add_argument("--since", default="7d")
    claims.set_defaults(func=cmd_claims)

    blocker = sub.add_parser("blocker", help="record a blocker")
    _add_common(blocker)
    blocker.add_argument("--subject", required=True)
    blocker.add_argument("--reason")
    blocker.add_argument("--resource")
    blocker.add_argument("--path")
    blocker.add_argument("--severity", default="blocked")
    blocker.add_argument("--tool")
    blocker.add_argument("--model")
    blocker.add_argument("--run-id")
    blocker.set_defaults(func=cmd_blocker)

    blockers = sub.add_parser("blockers", help="list active blockers")
    _add_common(blockers)
    blockers.add_argument("--tool")
    blockers.add_argument("--since", default="7d")
    blockers.set_defaults(func=cmd_blockers)

    unblock = sub.add_parser("unblock", help="resolve a blocker")
    _add_common(unblock)
    unblock.add_argument("identifier")
    unblock.add_argument("--resolution", required=True)
    unblock.add_argument("--tool")
    unblock.add_argument("--model")
    unblock.add_argument("--run-id")
    unblock.add_argument("--force", action="store_true")
    unblock.set_defaults(func=cmd_unblock)

    conflicts = sub.add_parser("conflicts", help="detect active claim conflicts")
    _add_common(conflicts)
    conflicts.add_argument("--since", default="7d")
    conflicts.set_defaults(func=cmd_conflicts)

    diagnose = sub.add_parser("diagnose", help="explain why coordination is stuck")
    _add_common(diagnose)
    diagnose.add_argument("--since", default="7d")
    diagnose.add_argument("--thread")
    diagnose.add_argument("--tool")
    diagnose.add_argument("--stale-after-seconds", type=int, default=24 * 3600)
    diagnose.set_defaults(func=cmd_diagnose)

    herdr = sub.add_parser("herdr", help="Herdr dogfood bridge")
    herdr_sub = herdr.add_subparsers(dest="herdr_cmd", required=True)

    herdr_status = herdr_sub.add_parser("status", help="show Herdr agents and ARP handoffs")
    _add_common(herdr_status)
    herdr_status.add_argument("--since", default="7d")
    herdr_status.add_argument("--tool")
    herdr_status.add_argument("--report", action="store_true", help="report pending handoffs as Herdr custom status")
    herdr_status.add_argument(
        "--allow-other-workspace", action="store_true",
        help="allow reporting to a matching agent pane outside --workdir/cwd",
    )
    herdr_status.set_defaults(func=cmd_herdr_status)

    herdr_inject = herdr_sub.add_parser("inject", help="inject a handoff into its target Herdr pane")
    _add_common(herdr_inject)
    herdr_inject.add_argument("identifier")
    herdr_inject.add_argument(
        "--allow-other-workspace", action="store_true",
        help="allow injecting into a matching agent pane outside --workdir/cwd",
    )
    herdr_inject.set_defaults(func=cmd_herdr_inject)

    args = parser.parse_args(argv)
    # Bare `agent-rally-point --json` returns the self-describing capability map.
    if not getattr(args, "cmd", None):
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps(_capability_map()) + "\n")
            return EXIT_OK
        parser.print_help(sys.stderr)
        return EXIT_OK
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        return _die(args, str(exc), EXIT_RUNTIME)


if __name__ == "__main__":
    sys.exit(main())
