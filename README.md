<!-- build-loop@tyroneross:canary:agent-rally-point -->
<!-- canary-end -->
# Agent Rally Point

> **Local-first coordination point for coding agents working in the same repo: presence, handoffs, verifier gates, conflicts, and closeout without a server.**

The user-facing CLI is **`rally`**. The package keeps the longer
`agent-rally-point` / `agent-rally` names as compatibility aliases.

Two or more AI agent CLIs (Claude Code, Codex, peer Claude sessions, CI verifiers) coordinating on a shared git repo with a human in the loop. No HTTP service. No broker. Just files + git for repo identity.

## Status

**v0.3.1 — preflight CLI** (2026-05-24). Adds `agent-rally-preflight`: a host-neutral session-start coordination check-in. Every AI coding agent (Claude Code, Codex, Cursor, Gemini, CI verifiers) runs the same single-line invocation at session start to resolve the canonical channel, surface pending ACKs and active peers, load shared north-star context, and decide `join_active` vs `proceed_solo`. Stdlib-only operational paths so the CLI works in degraded environments. 143/143 tests pass.

**v0.3.0 — canonical substrate** (2026-05-24). Adds: canonical channel layout at `~/.agent-rally-point/apps/<repo_id>/`, three-mode policy (canonical/migration/legacy-only), versioned discover envelope (`protocol_version: "1.0"`), repo_id normalization (worktree-stable + clone-stable), legacy → canonical migration tool with 4-condition cutover verifier, long-running presence-watcher with parent-liveness check.

Earlier: v0.2.x added the discovery layer + manifest. v0.1.0 (2026-05-20) extracted channel primitives from [build-loop](https://github.com/tyroneross/build-loop) v0.12.8.

## What it does

- **Presence**: agents heartbeat into `~/.agent-rally-point/apps/<repo_id>/sessions/`; peers see each other via `read_active_presence()`. Long-running sessions use `run_refresh_loop()` (60s default cadence, exits when parent process dies).
- **Channel**: append-only event log (`changes.jsonl`) with monotonic revision counter; readers compute deltas via `checkpoint_read()`.
- **Canonical post**: single `post()` helper that bumps revision + appends record atomically (prevents the silent-no-op bug).
- **Discovery**: `agent-rally-discover` resolves channel layout + active state via manifest with three-mode policy. Build-loop's discovery bridge reads `~/.agent-rally-point/compatibility.json` for the protocol handshake.
- **Migration**: `agent-rally-migrate` walks legacy `~/.build-loop/apps/*` → `~/.agent-rally-point/apps/<repo_id>/` with append-only audit log + sha256 integrity. `verify-cutover` returns the 4-condition can-promote verdict (legacy_fully_copied + integrity_verified + no_fresh_writes_within_ttl + downstream_ready).
- **Lifecycle hygiene**: explicit session reap on closeout; optional `changes.jsonl` rotation when log grows.
- **Repo identity**: `repo_id(cwd)` derives `<slug>-<8hex>` from normalized git remote URL — same id across clones, worktrees, HTTPS vs SSH forms. Frozen as part of `protocol_version 1.0`.

## Install

The standalone CLIs (`agent-rally-discover`, `agent-rally-migrate`) are best installed via [pipx](https://pipx.pypa.io) so they live on your `$PATH` regardless of which virtualenv you happen to be in:

```bash
# Recommended — pipx puts the CLIs on PATH globally:
pipx install agent-rally-point

# Verify (this MUST resolve from a fresh shell, not just inside .venv):
which agent-rally-discover
which agent-rally-migrate
```

PyPI publication is pending; until then, install from the local checkout:

```bash
git clone https://github.com/tyroneross/agent-rally-point.git
pipx install ./agent-rally-point      # global CLIs
# OR, for library use only (no global CLI):
uv pip install -e ./agent-rally-point
```

If `which agent-rally-discover` does NOT resolve from a fresh shell, the consuming build-loop session will fail its protocol-version handshake and proceed in degraded coordination_unavailable mode. The shell-level resolution is part of the integration contract, not optional.

## Session-start integration

Every AI coding agent should run **one line** at session start. The preflight reads the canonical channel, surfaces pending ACKs + live peers, loads north-star intent/goal, and returns a routing decision (`join_active` vs `proceed_solo`).

```bash
agent-rally-preflight --tool <host> --start-ping --human
```

Replace `<host>` with one of `claude_code`, `codex`, `cursor`, `gemini`, `ci`. Output is human-readable; drop `--human` for JSON. `--start-ping` writes a presence record so peers see this session immediately.

### Claude Code (plugin / `.claude-plugin/hooks/session-start.sh`)

```bash
#!/usr/bin/env bash
# .claude-plugin/hooks/session-start.sh
agent-rally-preflight --tool claude_code --start-ping --human
```

The hook runs once per session; the JSON or human-readable summary becomes context the model reads immediately.

### Codex (project `AGENTS.md`)

Add a single line near the top of `AGENTS.md`:

```markdown
## Session start
Run `agent-rally-preflight --tool codex --start-ping --human` and read the output before any tool call.
```

### Cursor / Gemini

Same shape — run the executable at session start with the appropriate `--tool` value. Any host that loads a session-bootstrap file (custom-instructions / system-prompt / pre-session script) can call the CLI directly.

### Sample output (human mode)

```
======================================================================
 AGENT-RALLY-PREFLIGHT v0.1.0  2026-05-24T22:14:52.448683Z
======================================================================
 tool:       codex
 session_id: codex-7d289263137a4a38-1779660892
 workdir:    /Users/me/dev/git-folder/myproj
 repo_id:    myproj-2b14b480
 channel:    /Users/me/.agent-rally-point/apps/myproj-2b14b480  [via agent-rally-point.discover]

 coordination_status: IDLE
 routing:    proceed_solo - No active peers and no pending ACKs - proceed with assigned task, log ping check-ins to substrate

 GUARDRAILS:
   - Global rules: /Users/me/.claude/CLAUDE.md
```

The JSON envelope (default; drop `--human`) carries the same data structured for programmatic consumption — `pending_acks_for_me`, `active_peers`, `routing.action`, `north_star`, `memory_locations`, `recent_changes`, `all_repos_active`.

### Behavior

- **Idle**: no peers, no pending ACKs → `routing.action: proceed_solo`. The session can run normally; the preflight writes its own presence so peers will see it on their next check.
- **Coordinated**: pending ACK or live peer detected → `routing.action: join_active`. The session should handle the inbox or coordinate with peers before parallel work.
- **Degraded**: substrate unreachable → exit code 1 with JSON envelope still emitted (just with `channel_dir: null`). Host LLM proceeds without coordination but isn't misled.

### Migrating from v0.2.x

```bash
# 1. Dry-run scan of legacy channels:
agent-rally-migrate scan

# 2. Apply the migration (writes audit log + advisory marker):
agent-rally-migrate apply

# 3. Verify the 4 cutover conditions:
agent-rally-migrate verify-cutover

# 4. Once verify-cutover returns can_promote: true, edit
#    ~/.agent-rally-point/manifest.toml: change [policy] mode = "canonical".
```

The default policy stays at `migration` (dual-aware: reads merge both paths, writes mirror to both) until the manual cutover edit lands.

## Quickstart (Python API)

```python
from pathlib import Path
from agent_rally_point import (
    app_slug, app_channel_dir, write_presence, post, checkpoint_read,
)

cwd = Path.cwd()
slug = app_slug(cwd)                            # worktree-independent repo identity
channel = app_channel_dir(slug)                 # ~/.agent-rally-point/apps/<slug>/

# Write own presence (heartbeat)
write_presence(
    channel_dir=channel,
    session_id="claude-feature-x-001",
    tool="claude_code",
    model="claude-opus-4-7",
    run_id="feature-x-001",
    app_slug=slug,
    phase="phase-1-assess",
    files_in_flight=["src/feature_x.py"],
    cwd=cwd,
)

# Post a verdict (uses the canonical helper — bumps revision + appends atomically)
new_rev = post(
    channel_dir=channel,
    kind="feedback",
    tool="claude_code",
    model="claude-opus-4-7",
    run_id="feature-x-001",
    app_slug=slug,
    payload={"step": "1", "verdict": "PASS", "evidence": {"commit": "abc1234"}},
)

# Peer reads what's new
envelope = checkpoint_read(
    channel_dir=channel,
    session_id="codex-verifier-001",
    my_files=["src/feature_x.py"],
)
print(envelope)
```

CLI surface (`agent-rally-point status`, `watch`, `post`, etc.) ships in v0.2.

## How this differs from A2A / MCP / LangGraph / CrewAI / Temporal

Most agent coordination today is **service-mediated** (A2A's HTTP+JSON-RPC), **in-process** (LangGraph state machines, CrewAI crews), or **broker-backed** (Temporal). Agent Rally Point is **filesystem-mediated** — for the case where two-or-more agent CLIs share a dev box (or shared FS) and need durable handoff signals without infrastructure.

Use Agent Rally Point when:
- You have 2+ agent CLIs (Claude Code, Codex, peer Claude) on the same repo
- You want zero-setup coordination (no service, no broker)
- You want the discipline patterns (gating verdicts, MECE handoffs, release-surface verification) as shipped tooling

Use A2A / MCP / LangGraph / CrewAI / Temporal when:
- Cross-vendor over the network → A2A
- Agent-to-tool wiring → MCP
- Single-runtime state machines → LangGraph
- Role-based agent teams in one Python process → CrewAI
- Long-running production workflows with retries → Temporal

These are complements, not competitors.

## Constitution

The binding rules of coordination (operating rule, MECE packets, post() canonical, release-surface verification, closeout hygiene) ship as in-package docs in v0.3. For now, see the extraction-source research packet at [`tyroneross/dev/research/topics/multi-peer-coordination-standalone-extraction/`](https://github.com/tyroneross) (private) or the build-loop predecessor at [`build-loop/references/coordination-rules.md`](https://github.com/tyroneross/build-loop/blob/main/references/coordination-rules.md).

## Trademark disclaimer

**Agent Rally Point is not affiliated with Rally Point IT, LLC** or any other entity holding marks containing "Rally Point." Agent Rally Point is an open-source developer tool for AI agent coordination; Rally Point IT, LLC is an IT services / infrastructure consultancy. The composite name uses the military-origin "rally point" metaphor (a known place where independent units regroup with status).

## License

Apache-2.0. See [LICENSE](./LICENSE).

## Provenance

Channel primitives copied verbatim from [build-loop](https://github.com/tyroneross/build-loop) `scripts/app_pulse/` at v0.12.8 (commit `10224c2`). Build-loop will become a downstream consumer of `agent-rally-point` once v1.0 ships; channel root migrates from `~/.build-loop/apps/` to `~/.agent-rally-point/apps/` at that cutover.

## License & Attribution

This project is licensed under the [Apache License 2.0](LICENSE).

- [`LICENSE`](LICENSE) — full license text.
- [`NOTICE`](NOTICE) — attribution notices that, per Apache 2.0 §4(d), must travel with any redistribution of this work.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution conventions: per-file SPDX headers (REUSE 3.3), AI co-author trailer, signed commits, conventional commits.

Per-file `SPDX-FileCopyrightText` and `SPDX-License-Identifier` headers are required on shipped source files. Files that cannot carry inline comments (JSON, generated assets) are annotated in [`REUSE.toml`](REUSE.toml). Validate compliance locally with `uvx reuse lint`.
