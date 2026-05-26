# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Herdr bridge helpers."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from agent_rally_point.changes import make_record  # noqa: E402
from agent_rally_point.coordination_trace import PendingHandoff  # noqa: E402
from agent_rally_point.herdr_bridge import (  # noqa: E402
    handoff_prompt,
    inject_handoff,
    list_agents,
    report_pending_status,
)


def _completed(args, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_list_agents_parses_herdr_json():
    # intent: Herdr bridge depends only on documented `herdr agent list` JSON.
    payload = {"result": {"agents": [
        {"agent": "codex", "pane_id": "1-2", "agent_status": "idle", "cwd": "/repo"}
    ]}}
    agents = list_agents(runner=lambda args: _completed(args, json.dumps(payload)))
    assert agents[0].agent == "codex"
    assert agents[0].pane_id == "1-2"


def test_report_pending_status_calls_report_agent():
    # intent: pending handoffs can surface in Herdr without requiring transcript scraping.
    calls = []
    agent_json = json.dumps({"result": {"agents": [{"agent": "codex", "pane_id": "1-2", "agent_status": "idle", "cwd": "/repo"}]}})

    def runner(args):
        calls.append(args)
        if args == ["herdr", "agent", "list"]:
            return _completed(args, agent_json)
        return _completed(args)

    lines = report_pending_status([
        PendingHandoff("evt_1", None, "pi", "codex", "review", 1, ())
    ], cwd="/repo", runner=runner)
    assert "reported evt_1" in lines[0]
    assert any(call[:4] == ["herdr", "pane", "report-agent", "1-2"] for call in calls)


def test_inject_handoff_sends_prompt_to_matching_pane():
    # intent: handoff injection sends a concise prompt to the target Herdr pane.
    record = make_record(
        kind="handoff", tool="pi", model="m", run_id="r", app_slug="app", revision=1,
        event_id="evt_" + "1" * 32,
        payload={"from_tool": "pi", "to_tool": "codex", "subject": "review", "ref_files": ["a.py"]},
    )
    calls = []
    agent_json = json.dumps({"result": {"agents": [{"agent": "codex", "pane_id": "1-2", "agent_status": "idle", "cwd": "/repo"}]}})

    def runner(args):
        calls.append(args)
        if args == ["herdr", "agent", "list"]:
            return _completed(args, agent_json)
        return _completed(args)

    result = inject_handoff([record], record["id"], cwd="/repo", runner=runner)
    assert "injected" in result
    run_calls = [call for call in calls if call[:3] == ["herdr", "pane", "run"]]
    assert run_calls and "Agent Rally Point handoff" in run_calls[0][4]
    assert "a.py" in handoff_prompt(record)


def test_inject_requires_matching_workspace_by_default():
    # intent: Herdr injection must not send repo handoffs to same-agent panes in other workspaces.
    record = make_record(
        kind="handoff", tool="pi", model="m", run_id="r", app_slug="app", revision=1,
        event_id="evt_" + "1" * 32,
        payload={"from_tool": "pi", "to_tool": "codex", "subject": "review"},
    )
    agent_json = json.dumps({"result": {"agents": [{"agent": "codex", "pane_id": "1-2", "agent_status": "idle", "cwd": "/other"}]}})

    def runner(args):
        if args == ["herdr", "agent", "list"]:
            return _completed(args, agent_json)
        return _completed(args)

    with pytest.raises(RuntimeError, match="in cwd /repo"):
        inject_handoff([record], record["id"], cwd="/repo", runner=runner)


def test_match_agent_normalizes_symlinked_cwd(tmp_path: Path):
    # intent: a symlinked workspace path must still match its real-path pane cwd.
    from agent_rally_point.herdr_bridge import HerdrAgent, match_agent

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    agents = [HerdrAgent("codex", "1-2", "idle", cwd=str(real))]
    # Passing the symlinked path must still find the pane on the real path.
    assert match_agent(agents, "codex", cwd=str(link)) is not None


def test_match_agent_deterministic_when_multiple_panes_match(tmp_path: Path):
    # intent: ambiguous matches resolve by pane_id sort order, not Herdr list order.
    from agent_rally_point.herdr_bridge import HerdrAgent, match_agent

    agents = [
        HerdrAgent("codex", "9-9", "idle", cwd="/repo"),
        HerdrAgent("codex", "1-1", "idle", cwd="/repo"),
        HerdrAgent("codex", "5-5", "idle", cwd="/repo"),
    ]
    picked = match_agent(agents, "codex", cwd="/repo")
    assert picked is not None and picked.pane_id == "1-1"
