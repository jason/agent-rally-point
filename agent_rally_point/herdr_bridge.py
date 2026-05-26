#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Small Herdr bridge for dogfooding Agent Rally Point in panes."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable

from .coordination_trace import PendingHandoff, event_label, find_record

Runner = Callable[[list[str]], subprocess.CompletedProcess]


@dataclass(frozen=True)
class HerdrAgent:
    """A detected Herdr agent pane."""

    agent: str
    pane_id: str
    status: str
    cwd: str | None = None


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def list_agents(*, runner: Runner = _run) -> list[HerdrAgent]:
    """Return Herdr agents, or an empty list when Herdr is unavailable."""
    try:
        result = runner(["herdr", "agent", "list"])
    except OSError:
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    out: list[HerdrAgent] = []
    for rec in (data.get("result") or {}).get("agents", []):
        agent = rec.get("agent")
        pane_id = rec.get("pane_id")
        if isinstance(agent, str) and isinstance(pane_id, str):
            out.append(HerdrAgent(
                agent=agent,
                pane_id=pane_id,
                status=str(rec.get("agent_status") or "unknown"),
                cwd=rec.get("cwd") if isinstance(rec.get("cwd"), str) else None,
            ))
    return out


def _normalize_cwd(value: str | None) -> str | None:
    """Resolve a cwd string for stable comparison across symlinks/trailing slashes."""
    if not value:
        return None
    try:
        return os.path.realpath(value)
    except OSError:
        return value


def _cwd_matches(agent: HerdrAgent, cwd: str | None) -> bool:
    if cwd is None:
        return True
    return _normalize_cwd(agent.cwd) == _normalize_cwd(cwd)


def match_agent(
    agents: list[HerdrAgent],
    target_tool: str | None,
    *,
    cwd: str | None = None,
    allow_other_workspace: bool = False,
) -> HerdrAgent | None:
    """Find the Herdr pane most likely to own ``target_tool``.

    Selection order when more than one pane matches:
      1. Prefer panes whose ``cwd`` matches the supplied ``cwd`` (after
         ``realpath`` normalization on both sides so symlinks and trailing
         slashes don't mask a real match).
      2. Within either tier, panes are sorted by ``pane_id`` so the choice is
         deterministic when Herdr returns multiple matching agents.
      3. If no cwd-matching pane is found and ``allow_other_workspace`` is
         False, return None rather than falling back to a different workspace.
    """
    if not target_tool:
        return None
    aliases = {
        "claude_code": {"claude", "claude_code"},
        "pi": {"pi"},
        "codex": {"codex"},
        "cursor": {"cursor"},
        "gemini": {"gemini"},
    }
    wanted = aliases.get(target_tool, {target_tool})
    matching_tool = sorted(
        (agent for agent in agents if agent.agent in wanted),
        key=lambda a: a.pane_id,
    )
    for agent in matching_tool:
        if _cwd_matches(agent, cwd):
            return agent
    if cwd is not None and not allow_other_workspace:
        return None
    return matching_tool[0] if matching_tool else None


def report_pending_status(
    handoffs: list[PendingHandoff],
    *,
    cwd: str | None = None,
    allow_other_workspace: bool = False,
    runner: Runner = _run,
) -> list[str]:
    """Report pending handoffs into matching Herdr panes as custom status.

    Returns human-readable action lines for CLI output.
    """
    agents = list_agents(runner=runner)
    lines: list[str] = []
    for item in handoffs:
        agent = match_agent(
            agents, item.to_tool,
            cwd=cwd, allow_other_workspace=allow_other_workspace,
        )
        if agent is None:
            where = f" in cwd {cwd}" if cwd and not allow_other_workspace else ""
            lines.append(f"no Herdr pane for {item.to_tool}{where}: {item.event_id}")
            continue
        result = runner([
            "herdr", "pane", "report-agent", agent.pane_id,
            "--source", "custom:agent-rally",
            "--agent", agent.agent,
            "--state", "blocked",
            "--custom-status", "handoff",
        ])
        if result.returncode == 0:
            lines.append(f"reported {item.event_id} to {agent.agent} pane {agent.pane_id}")
        else:
            lines.append(f"failed reporting {item.event_id} to {agent.pane_id}: {result.stderr.strip()}")
    return lines


def handoff_prompt(record: dict) -> str:
    """Return a compact prompt to inject into a target agent pane."""
    payload = record.get("payload") or {}
    files = payload.get("ref_files") or payload.get("files") or []
    file_text = f"\nFiles: {', '.join(files)}" if isinstance(files, list) and files else ""
    notes = payload.get("notes")
    notes_text = f"\nNotes: {notes}" if isinstance(notes, str) and notes else ""
    return (
        "Agent Rally Point handoff:\n"
        f"ID: {record.get('id') or payload.get('id') or '(no id)'}\n"
        f"{event_label(record)}"
        f"{file_text}{notes_text}\n"
        "Please acknowledge with agent-rally ack/reject/needs-info when done."
    )


def inject_handoff(
    records: list[dict],
    identifier: str,
    *,
    cwd: str | None = None,
    allow_other_workspace: bool = False,
    runner: Runner = _run,
) -> str:
    """Inject a handoff prompt into the target Herdr agent pane."""
    record = find_record(records, identifier)
    if record is None or record.get("kind") != "handoff":
        raise ValueError(f"handoff {identifier!r} not found")
    payload = record.get("payload") or {}
    target = payload.get("to_tool") or payload.get("to")
    agent = match_agent(
        list_agents(runner=runner), target if isinstance(target, str) else None,
        cwd=cwd, allow_other_workspace=allow_other_workspace,
    )
    if agent is None:
        where = f" in cwd {cwd}" if cwd and not allow_other_workspace else ""
        raise RuntimeError(f"no Herdr pane found for target {target!r}{where}")
    result = runner(["herdr", "pane", "run", agent.pane_id, handoff_prompt(record)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "herdr pane run failed")
    return f"injected {identifier} into {agent.agent} pane {agent.pane_id}"
