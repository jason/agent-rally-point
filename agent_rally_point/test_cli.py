# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for agent-rally-point CLI trace commands."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from agent_rally_point.changes import append_change, make_record  # noqa: E402
from agent_rally_point.cli import main  # noqa: E402
from agent_rally_point.coordination_trace import load_records  # noqa: E402


def test_handoff_inbox_ack_thread_report_smoke(tmp_path: Path, capsys):
    # intent: CLI handoff lifecycle writes trace events and clears inbox after ack.
    assert main([
        "handoff", "--channel-dir", str(tmp_path), "--to", "codex",
        "--from-tool", "pi", "--subject", "review schema", "--files", "docs/SCHEMA.md",
    ]) == 0
    out = capsys.readouterr().out
    handoff_id = out.split()[2]

    assert main(["inbox", "--channel-dir", str(tmp_path), "--tool", "codex"]) == 0
    assert handoff_id in capsys.readouterr().out

    assert main(["ack", "--channel-dir", str(tmp_path), "--tool", "codex", handoff_id, "--summary", "done"]) == 0
    assert "posted done ack" in capsys.readouterr().out

    assert main(["inbox", "--channel-dir", str(tmp_path), "--tool", "codex"]) == 0
    assert "Inbox empty" in capsys.readouterr().out

    assert main(["thread", "--channel-dir", str(tmp_path), handoff_id]) == 0
    thread_out = capsys.readouterr().out
    assert "handoff" in thread_out and "ack" in thread_out

    assert main(["report", "--channel-dir", str(tmp_path), "--since", "1d", "--ids"]) == 0
    assert "Pending handoffs: 0" in capsys.readouterr().out

    records = load_records(tmp_path)
    assert [r["kind"] for r in records] == ["handoff", "ack"]


def test_score_command_reports_open_handoff(tmp_path: Path, capsys):
    # intent: CLI scorer exposes trace coordination failures without model calls.
    assert main([
        "handoff", "--channel-dir", str(tmp_path), "--to", "codex",
        "--from-tool", "pi", "--subject", "review schema",
    ]) == 0
    capsys.readouterr()
    assert main(["score", "--channel-dir", str(tmp_path), "--tool", "codex"]) == 0
    out = capsys.readouterr().out
    assert "Coordination score: 75/100" in out
    assert "open-required-handoff" in out


def test_herdr_status_command_uses_bridge(tmp_path: Path, capsys, monkeypatch):
    # intent: Herdr status command is a thin adapter over trace state + Herdr bridge helpers.
    assert main([
        "handoff", "--channel-dir", str(tmp_path), "--to", "codex",
        "--from-tool", "pi", "--subject", "review schema",
    ]) == 0
    capsys.readouterr()

    from agent_rally_point.herdr_bridge import HerdrAgent
    monkeypatch.setattr("agent_rally_point.cli.list_agents", lambda: [HerdrAgent("codex", "1-2", "idle")])
    monkeypatch.setattr("agent_rally_point.cli.report_pending_status", lambda pending, **_kw: [f"reported {pending[0].event_id}"])

    assert main(["herdr", "status", "--channel-dir", str(tmp_path), "--report"]) == 0
    out = capsys.readouterr().out
    assert "codex pane=1-2" in out
    assert "Pending ARP handoffs: 1" in out
    assert "reported evt_" in out


def test_ack_unknown_identifier_refuses_without_force(tmp_path: Path, capsys):
    # intent: typos must not silently create zombie ack records referencing missing handoffs.
    rc = main([
        "ack", "--channel-dir", str(tmp_path), "--tool", "codex",
        "evt_deadbeef" + "0" * 24,
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no handoff found" in err
    # With --force, the ack is posted anyway.
    rc2 = main([
        "ack", "--channel-dir", str(tmp_path), "--tool", "codex", "--force",
        "evt_deadbeef" + "0" * 24,
    ])
    assert rc2 == 0
    records = load_records(tmp_path)
    assert [r["kind"] for r in records] == ["ack"]


def test_capability_map_json(capsys):
    # intent: bare `--json` returns a self-describing capability map for agentic discovery.
    import json
    assert main(["--json"]) == 0
    cap = json.loads(capsys.readouterr().out)
    assert cap["ok"] is True
    assert cap["tool"] == "agent-rally-point"
    assert "version" in cap
    assert "handoff" in cap["commands"] and "ack" in cap["commands"]
    assert cap["exit_codes"]["NOT_FOUND"] == 2


def test_global_json_flag_applies_to_subcommands(tmp_path: Path, capsys):
    # intent: agents commonly put global flags before the command; keep that JSON contract stable.
    assert main(["--json", "report", "--channel-dir", str(tmp_path)]) == 0
    import json
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["command"] == "report"


def test_handoff_and_ack_json_writes_structured_stdout(tmp_path: Path, capsys):
    # intent: write commands emit one parseable JSON object so agents can branch on result.
    import json
    assert main([
        "handoff", "--json", "--channel-dir", str(tmp_path),
        "--to", "codex", "--from-tool", "pi", "--subject", "review",
    ]) == 0
    body = json.loads(capsys.readouterr().out.strip())
    assert body["ok"] is True and body["command"] == "handoff"
    event_id = body["event_id"]
    assert event_id.startswith("evt_")

    assert main([
        "ack", "--json", "--channel-dir", str(tmp_path), "--tool", "codex", event_id,
    ]) == 0
    ack_body = json.loads(capsys.readouterr().out.strip())
    assert ack_body["ok"] is True
    assert ack_body["resolved"] is True
    assert ack_body["verdict"] == "done"


def test_ack_unknown_identifier_json_error(tmp_path: Path, capsys):
    # intent: structured stderr errors let agents branch on exit_code + error context.
    import json
    rc = main([
        "ack", "--json", "--channel-dir", str(tmp_path), "--tool", "codex",
        "evt_deadbeef" + "0" * 24,
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # JSON-mode errors must not pollute stdout
    body = json.loads(captured.err.strip())
    assert body["ok"] is False
    assert body["exit_code"] == 2
    assert "no handoff found" in body["error"]


def test_json_runtime_errors_do_not_traceback(tmp_path: Path, capsys):
    # intent: runtime validation errors honor the structured JSON error contract.
    import json
    rc = main(["report", "--json", "--channel-dir", str(tmp_path), "--since", "bogus"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    body = json.loads(captured.err.strip())
    assert body["ok"] is False
    assert "invalid --since" in body["error"]


def test_inbox_json_lists_pending(tmp_path: Path, capsys):
    # intent: read commands expose structured pending list for agent inbox scrapers.
    import json
    main([
        "handoff", "--channel-dir", str(tmp_path),
        "--to", "codex", "--from-tool", "pi", "--subject", "review schema",
    ])
    capsys.readouterr()
    assert main(["inbox", "--json", "--channel-dir", str(tmp_path), "--tool", "codex"]) == 0
    body = json.loads(capsys.readouterr().out.strip())
    assert body["ok"] is True and body["command"] == "inbox"
    assert len(body["pending"]) == 1
    assert body["pending"][0]["to_tool"] == "codex"


def test_claim_release_conflict_and_diagnose(tmp_path: Path, capsys):
    # intent: Rally can coordinate ownership claims and explain conflicts/stuck work.
    import json

    assert main([
        "claim", "--json", "--channel-dir", str(tmp_path), "--tool", "pi",
        "--path", "./docs/SCHEMA.md", "--subject", "edit schema",
    ]) == 0
    claim_a = json.loads(capsys.readouterr().out)["event_id"]
    assert main([
        "claim", "--json", "--channel-dir", str(tmp_path), "--tool", "codex",
        "--path", "docs/SCHEMA.md", "--subject", "review schema",
    ]) == 0
    claim_b = json.loads(capsys.readouterr().out)["event_id"]

    assert main(["conflicts", "--json", "--channel-dir", str(tmp_path)]) == 0
    conflicts = json.loads(capsys.readouterr().out)["conflicts"]
    assert conflicts[0]["resource"] == "file:docs/SCHEMA.md"
    assert set(conflicts[0]["claim_ids"]) == {claim_a, claim_b}

    assert main(["diagnose", "--json", "--channel-dir", str(tmp_path)]) == 0
    diag = json.loads(capsys.readouterr().out)
    assert diag["status"] == "stuck"
    assert any(f["code"] == "claim-conflict" for f in diag["findings"])

    assert main(["release", "--channel-dir", str(tmp_path), "--tool", "codex", "rev:2"]) == 0
    capsys.readouterr()
    records = load_records(tmp_path)
    release = records[-1]
    assert release["kind"] == "claim-release"
    assert release["causation_id"] == claim_b  # canonical records store the event id in top-level `id`
    assert release["payload"]["ref_claim_id"] == claim_b
    assert main(["conflicts", "--json", "--channel-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["conflicts"] == []


def test_blocker_surfaces_in_diagnose(tmp_path: Path, capsys):
    # intent: a blocker event gives agents a clear stuck reason and next action.
    import json

    assert main([
        "blocker", "--json", "--channel-dir", str(tmp_path), "--tool", "codex",
        "--subject", "need branch", "--reason", "which branch?", "--resource", "task:review",
    ]) == 0
    blocker_id = json.loads(capsys.readouterr().out)["event_id"]
    assert main(["blockers", "--json", "--channel-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["blockers"][0]["event_id"] == blocker_id

    assert main(["diagnose", "--json", "--channel-dir", str(tmp_path)]) == 0
    diag = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "active-blocker" and f["event_id"] == blocker_id for f in diag["findings"])

    assert main([
        "unblock", "--json", "--channel-dir", str(tmp_path), "--tool", "pi",
        "rev:1", "--resolution", "branch supplied",
    ]) == 0
    unblock_body = json.loads(capsys.readouterr().out)
    assert unblock_body["resolved"] is True
    assert unblock_body["ref_blocker_id"] == blocker_id
    assert main(["diagnose", "--json", "--channel-dir", str(tmp_path)]) == 0
    diag2 = json.loads(capsys.readouterr().out)
    assert not any(f["code"] == "active-blocker" for f in diag2["findings"])


def test_diagnose_does_not_age_out_open_state(tmp_path: Path, capsys):
    # intent: old unresolved blockers/claims are still current stuck state.
    import json

    old_blocker = make_record(
        kind="blocker", tool="codex", model="m", run_id="r", app_slug="app", revision=1,
        event_id="evt_" + "8" * 32,
        time_rfc3339="2020-01-01T00:00:00.000Z",
        payload={"subject": "old blocker", "reason": "still blocked"},
    )
    append_change(tmp_path, old_blocker)
    assert main(["diagnose", "--json", "--channel-dir", str(tmp_path), "--since", "1s"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "active-blocker" for f in body["findings"])
