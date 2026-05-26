<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-05-26

### Added

- **Canonical event envelope (CloudEvents 1.0–aligned).** `make_record()` now emits `specversion`, `id` (`evt_<32 hex>`), `source` (`urn:agent-rally-point:tool:<tool>`), `subject`, `time` (RFC3339), `type` (e.g. `agent-rally.handoff.created.v1`), `datacontenttype`, `dataschema`, plus ARP causal fields `thread_id` / `causation_id` / `correlation_id`. Packaged JSON Schemas under `agent_rally_point/schemas/` describe the envelope and each known kind. See `docs/COORDINATION_TRACE.md` for the full positioning.
- **Coordination-trace CLI surface.** `agent-rally-point` (alias `agent-rally`) gained `report`, `replay`, `thread`, `inbox`, `handoff`, `ack`, `reject`, `needs-info`, `score`, `herdr status`, and `herdr inject` subcommands. Every command supports `--json` for structured stdout; errors emit `{ok:false, error, exit_code, ...}` to stderr. Bare `agent-rally-point --json` prints a self-describing capability map. Stable exit codes: `0` OK, `1` RUNTIME, `2` NOT_FOUND, `4` EXTERNAL. `--version` flag added.
- **`ack` record kind.** New event kind `agent-rally.handoff.acknowledged.v1` with verdicts `done` / `rejected` / `needs-info`. The CLI refuses to post an ack for a handoff not present in the channel unless `--force` is supplied (prevents zombie acks from typos).
- **Herdr bridge with workspace targeting.** `herdr status --report` and `herdr inject` default to panes whose Herdr `cwd` matches the current `--workdir` (compared via `os.path.realpath` so symlinks and trailing slashes don't mask matches). Cross-workspace targeting requires the explicit `--allow-other-workspace` flag. Multiple matching panes resolve deterministically by `pane_id` sort order.
- **Scorer.** `agent-rally score` evaluates trace invariants (open required handoffs, dangling causation/ack references, unresolved `needs-info`) directly over `changes.jsonl`.

### Changed (breaking)

- **`ts` field removed from canonical change records.** `make_record()` no longer emits the legacy floating-point `ts` (epoch seconds); records carry only `time` (RFC3339). Consumers reading change records must switch to `time`, or use `coordination_trace.record_epoch()` which still tolerates legacy `ts` for pre-0.4 records. The envelope JSON Schema and `docs/SCHEMA.md` were updated accordingly. Records on disk written before 0.4 remain valid and readable (warns-not-drops contract).

### Security

- **Documented Herdr injection trust model.** `agent-rally herdr inject` invokes `herdr pane run` with text derived from a handoff payload — any tool with ARP write access can plant prompt-injection content into a target agent's prompt context once an operator runs injection. The trust-model section in `docs/COORDINATION_TRACE.md` now names this explicitly: treat ARP write access as equivalent to terminal write on the receiving agent.
- Cross-workspace Herdr injection is now blocked by default (see Herdr bridge above).

## [0.3.1] — 2026-05-24

### Added

- **`agent-rally-preflight` console script** (`agent_rally_point.preflight:main`). Host-neutral session-start coordination check-in for every AI coding agent (Claude Code, Codex, Cursor, Gemini, CI verifiers). The CLI:
  - Resolves the active channel via `agent_rally_point.discover` (in-process) with subprocess fallback to `agent-rally-discover`, and a stdlib-only `find_channel_dir` fallback when both are unavailable.
  - Reads canonical + legacy inboxes during `policy: migration` and dedupes by record `id` (latest write wins).
  - Self-filters `requires_ack` handoffs whose `from` equals the current `--tool` so a session never lists its own posts as pending-for-self.
  - Surfaces active peers from `<channel>/rally/presence-*.json` within the 15-minute heartbeat TTL.
  - Loads north-star (`intent.md` + `goal.md`), memory locations, guardrails, and a recent-changes glance.
  - Returns a routing decision — `join_active` when pending ACKs or active peers exist; `proceed_solo` otherwise.
  - `--start-ping` writes a presence record under `<channel>/rally/` so peers see the new session immediately.
  - `--human` renders a readable summary; default output is the structured JSON envelope.
- **README session-start integration section**. Copy-paste snippets for Claude Code (`session-start.sh`), Codex (`AGENTS.md`), Cursor, and Gemini; sample human-mode output; behavior matrix (idle / coordinated / degraded).
- **`agent_rally_point/test_preflight.py`** — 20 tests covering AC-G1 through AC-G7 (pending ACK detection, broadcasts, self-filter, dual-inbox dedupe, routing, `--start-ping` presence, graceful fallback).

### Notes

- The standalone v0.1.0 preflight at `~/.local/bin/agent-rally-preflight` is the design source; this release promotes it to a proper packaged console script that pipx installs alongside `agent-rally-discover` and `agent-rally-migrate`.
- No breaking changes to `discover`, `migrate`, or any other v0.3.0 surface.
- 143/143 tests pass (was 104 in v0.3.0).

## [0.3.0] — 2026-05-24

### Added

- Canonical channel layout at `~/.agent-rally-point/apps/<repo_id>/`.
- Three-mode policy (`canonical` / `migration` / `legacy-only`); default `migration` is dual-aware — `discover()` returns both `canonical_channel_dir` and `legacy_channel_dir` with `merged_view: true`.
- `agent-rally-migrate` console script: scan, apply, verify-cutover. Append-only audit log with sha256 integrity. The 4-condition cutover verifier returns `can_promote=true` only when legacy is fully copied, integrity-verified, no fresh writes within the TTL, and downstream-ready.
- `repo_id(cwd)` normalization (worktree-stable + clone-stable). Frozen as part of `protocol_version 1.0`.
- Versioned discover envelope with `protocol_version`, `last_resolved_at`, `policy`, and `repo_id`.
- Long-running presence watcher (`presence.run_refresh_loop`) with parent-PID liveness check.
- Compatibility table at `~/.agent-rally-point/compatibility.json` for the build-loop handshake.

## [0.2.x] and earlier

See git history for the discovery-layer + manifest scaffold (`agent-rally-discover`) and the v0.1.0 extraction from build-loop's `scripts/app_pulse/`.
