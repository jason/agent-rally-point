<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Agent Rally Point — Architecture

> **Role: protocol-of-record for agent-to-agent coordination via append-only JSONL stream.**
> Substrate only. Daemons live in [`agent-rally-watcher`](https://github.com/tyroneross/agent-rally-watcher). Consumers (build-loop, codex, claude_code, custom tools) post events into and read events out of channels owned by this package.

## Three-layer model

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3 — CONSUMERS (build-loop, codex, claude_code, custom tools) │
│    post() events · checkpoint_read() deltas · inbox/<tool>.jsonl    │
└────────────────────────────▲─────────────────────▲──────────────────┘
                             │                     │
              read filtered  │     write events    │
                             │                     │
┌────────────────────────────┴──────────┐ ┌────────┴──────────────────┐
│  Layer 2 — DAEMON (agent-rally-watcher)│ │  Layer 1.5 — DISCOVERY    │
│    kqueue/inotify tail of channel ·    │ │    manifest.toml resolver │
│    consumers.toml filter rules ·       │ │    `agent-rally discover` │
│    per-consumer cursor · sinks         │ │    `discover()` API       │
└────────────────────────────▲───────────┘ └────────▲──────────────────┘
                             │                      │
            tails             │       publishes      │
                             │                      │
┌────────────────────────────┴──────────────────────┴──────────────────┐
│  Layer 1 — SUBSTRATE (this repo, agent-rally-point)                  │
│    channel layout · changes.jsonl append-only · revision counter ·   │
│    presence/heartbeat · checkpoint_read · post · lifecycle reapers   │
└──────────────────────────────────────────────────────────────────────┘
```

**Layer 1 (this repo)** is the only writer of the canonical channel files. It defines the record schema, owns atomicity (O_APPEND single-write + tmp+rename), and exposes both a Python API and a CLI.

**Layer 2 ([agent-rally-watcher](https://github.com/tyroneross/agent-rally-watcher))** is a long-running daemon that reads the channel via kqueue/inotify and pushes filtered events to per-consumer sinks. It never writes the channel itself; it only consumes and dispatches.

**Layer 3 (consumers)** is any tool that participates — build-loop's orchestrator, the codex CLI, claude_code sessions, custom integrations. Consumers call `post(...)` to publish and either (a) call `checkpoint_read(...)` directly for cheap inline polling or (b) subscribe via the watcher daemon for push.

## Data flow

```
producer ─[1]─▶ append_change(changes.jsonl)
producer ─[2]─▶ bump_revision(revision)        ← atomic increment under fcntl lock

       ─[3]─▶  watcher reads kqueue/inotify edge on changes.jsonl mtime
              filter by consumers.toml rules per consumer
       ─[4]─▶  sink: file append (per-tool inbox), notify, HTTP

       ─[3']─▶ consumer polls checkpoint_read(cwd, session_id)
              compares current revision to session cursor
              returns delta + reactions ({reinstall, re-baseline, soft-claim})
              advances ONLY this session's cursor
```

The `post()` helper (`agent_rally_point.post`) is the canonical writer: it bumps the revision *before* appending the record so that any reader who sees the new revision can always find its corresponding line. Callers that use `append_change` directly without bumping leave the channel in a silent-no-op state (peers never notice). See [`docs/SCHEMA.md`](docs/SCHEMA.md) for the record format.

## Channel layout

One channel per canonical repo, rooted at `~/.agent-rally-point/apps/<repo_id>/` under canonical or migration policy. Under legacy-only policy, the channel root stays at the v0.1-era `~/.build-loop/apps/<slug>/`.

`repo_id` (v0.3.0+) is `<slug>-<8hex>` where `slug` is the repo name from the normalized git remote URL and `8hex` is sha256 of that normalized URL truncated. Two clones of the same upstream converge to the same `repo_id` even when checked out at different local paths; two repos with the same basename but different remotes get different `repo_id`s. When no remote is configured, the fallback is a path-derived id (`<basename>-<8hex>` where 8hex hashes the resolved repo root path). See `agent_rally_point/repo_id.py`.

The basename slug (`channel_paths.app_slug`) is still derived from `git rev-parse --git-common-dir` and stays worktree-aware. It's the right identifier for the legacy `~/.build-loop/apps/` tree (which only ever knew about slugs) and for the v0.3 `discover().app_slug` field.

### Policy axis

| Policy        | `channel_dir`                                  | Reads                              | Writes                                  |
|---------------|------------------------------------------------|------------------------------------|-----------------------------------------|
| `canonical`   | `~/.agent-rally-point/apps/<repo_id>/`         | canonical only                     | canonical only                          |
| `migration`   | canonical (primary write target)               | merged union of canonical + legacy | canonical + mirror-write to legacy      |
| `legacy-only` | `~/.build-loop/apps/<slug>/`                   | legacy only                        | legacy only                             |

Default policy is `migration`. Promote to `canonical` only after `agent-rally-migrate verify-cutover` confirms the four conditions (legacy_fully_copied + integrity_verified + no_fresh_writes_within_ttl + downstream_ready). See `docs/DISCOVERY.md`.

Under `canonical` policy, if the canonical channel becomes unreadable, `discover()` returns `coordination_unavailable: true` LOUDLY — it does **not** silently fall back to legacy. Rally Point is awareness, not enforcement; the build proceeds in degraded mode without coordination. This is the hard rule that exists to prevent the v0.12.16 silent-second-universe defect class.

| Path                            | Purpose                                      | Writer            | Reader               |
|---------------------------------|----------------------------------------------|-------------------|----------------------|
| `changes.jsonl`                 | Append-only event stream                     | any consumer      | any consumer, watcher|
| `revision`                      | Monotonic counter (cheap "did anything change") | any consumer   | any consumer         |
| `revision.lock`                 | fcntl lock for revision bumps                | revision module   | n/a                  |
| `sessions/<session-id>.json`    | Per-session presence + read cursor           | the owning session| reaper, checkpoint   |
| `inbox/<tool>.jsonl`            | Per-tool addressed messages (push target)    | watcher           | the target tool      |
| `inbox/all.jsonl`               | Broadcast inbox (all tools)                  | watcher           | every tool           |
| `rally/current.json`            | Optional rally pointer (active topic)        | rally subcommand  | any consumer         |
| `watchers/<pid>.json`           | Daemon registration (one per watcher)        | watcher           | watcher              |
| `arch/digest.json`              | Optional architecture digest (opt-in)        | architecture-scout| checkpoint reactions |

Legacy path: under `migration` policy, channels are *also* read from `~/.build-loop/apps/<slug>/` and writes mirror there. The migration tool (`agent-rally-migrate apply`) copies legacy state into the canonical location keyed by `repo_id`. Once `verify-cutover` returns `can_promote: true` and the operator promotes `[policy] mode = "canonical"` in the manifest, the legacy path is no longer read or written. See [`docs/DISCOVERY.md`](docs/DISCOVERY.md) and the migration section in the README.

## Record schema

Every line in `changes.jsonl` is one self-contained JSON record. Schema and known kinds (`commit`, `dep-change`, `phase`, `arch-scan-complete`, `feedback`, `handoff`) are documented in [`docs/SCHEMA.md`](docs/SCHEMA.md). Unknown kinds are kept-and-warned (D7), never dropped.

## Discovery

Sibling tools (build-loop, agent-rally-watcher, codex) discover the channel layout and active state without hardcoding paths via the discovery layer documented in [`docs/DISCOVERY.md`](docs/DISCOVERY.md):

- Manifest at `~/.agent-rally-point/manifest.toml` (global, auto-generated on first install).
- Optional `.agent-rally.toml` at repo root (per-repo overlay, opt-in).
- `agent-rally-discover` CLI (or `python3 -m agent_rally_point.discover`) returns the resolved layout as JSON.
- `from agent_rally_point.discover import discover` Python API returns the same dict.
- Resolution order: repo-level overlay → global manifest → legacy `~/.build-loop/apps/<slug>/` fallback.

## Cross-references

- **Watcher daemon**: [`agent-rally-watcher/ARCHITECTURE.md`](https://github.com/tyroneross/agent-rally-watcher/blob/main/ARCHITECTURE.md)
- **Build-loop's usage**: [`build-loop/skills/build-loop/references/coordination.md`](https://github.com/tyroneross/build-loop/blob/main/skills/build-loop/references/coordination.md)
- **Record schema**: [`docs/SCHEMA.md`](docs/SCHEMA.md)
- **Discovery protocol**: [`docs/DISCOVERY.md`](docs/DISCOVERY.md)

## Design invariants

| Invariant | Why |
|-----------|-----|
| Readers never lock | A slow/dead reader must never block a producer. |
| O_APPEND single-write up to PIPE_BUF is atomic | No torn lines across concurrent writers; matches POSIX guarantee. |
| The log is immutable | No rewrite/delete/truncate API — only `append_change` and `read_changes_since`. Audit replay is always possible. |
| Soft-claim is awareness, not a lock | Peer-file-overlap warns; never blocks. Consumers decide whether to mutate. |
| Worktree-independent slug | Same channel from main checkout, clone, and every git worktree of the same canonical repo. |
| Fire-and-forget writes | A coordination failure must never crash a host action. |

## Architectural decisions

These short ADRs document the load-bearing decisions that aren't visible
from the code alone. They are stable contracts; changing one is a
deliberate substrate-version bump.

### ADR-1: Two storage shapes — append-only log + ephemeral TTL state

The substrate maintains **two** kinds of state, deliberately distinguished:

| Shape | Storage | Lifetime | Examples |
|---|---|---|---|
| Append-only event log | `changes.jsonl` | Forever (immutable) | `handoff`, `ack`, `claim`, `phase`, `commit`, … |
| Ephemeral TTL state | `sessions/*.json`, `rally/presence-*.json`, `watchers/*.json` | Reaper-pruned by heartbeat TTL | presence, session cursors, daemon registration |

Why this asymmetry: presence-style signals are high-frequency
(heartbeats every few minutes), short-lived (TTL minutes), and have no
audit value once stale. Logging them as events would bloat the
immutable log without adding coordination signal. The cost is a small
amount of cognitive overhead — operators have to know presence isn't
in the trace. The split is enforced: the log has no
rewrite/delete/truncate API; the TTL state has a reaper.

### ADR-2: Worktrees share one channel

`repo_id` resolves to one channel per canonical *repo*, not per worktree
or branch. Multiple worktrees of the same upstream see each other's
events. This is the default because the common case is "agents on
different worktrees are still working on the same codebase and should
coordinate."

When isolation is desired (e.g., an experimental worktree should not
emit `commit` events into the shared channel), consumers can override
the channel via `--channel-dir`. There is no per-worktree `repo_id`
scoping in v1.

Branch scoping is also not part of `repo_id`. A branch context that
needs its own channel can write to a different `app_slug` via
`--channel-dir`, but the default is repo-level coordination.

### ADR-3: No log compaction in v1

`changes.jsonl` is append-only forever. The substrate provides no
compaction, rollover, or pruning API. This is the simplest contract
that preserves immutability and audit-replay.

Consumers that need a bounded view filter at read time
(`coordination_trace.filter_since`, `rally replay --since 2h`). The log
on disk keeps growing.

Future options if log size becomes a real problem:
- **Time-windowed rollover.** `changes-YYYY-MM.jsonl` files indexed by
  a `MANIFEST.json`; readers default to current window with `--all` to
  load everything. Additive, doesn't break immutability.
- **Snapshot + cold archive.** Periodic snapshot file representing
  "active state at T"; original log preserved cold. Consumers prefer
  the snapshot for warm reads.

Neither is in v1. The decision to defer is intentional: pre-optimizing
for a problem that hasn't appeared in any deployed channel adds
maintenance debt now for hypothetical relief later.

### ADR-4: Schema versioning — writers emit latest, readers accept all

Packaged JSON Schemas in `agent_rally_point/schemas/` are **diagnostic
contracts**, not enforced gates. Validation is warns-not-drops at the
substrate (D7). This shapes the versioning policy:

- **Writers** emit the current `type` version (`agent-rally.X.created.v1`).
- **Readers** accept any version they recognize and warn (never drop)
  on unknowns. A v1 reader seeing a v2 record keeps it; consumers
  decide whether to act on the new fields.
- **Breaking changes** bump the version suffix (`v1` → `v2`) and ship
  a new schema file alongside the old one. The old schema is kept; old
  records remain valid forever.
- **Field additions** are additive within a version: schemas have
  `additionalProperties: true` and consumers tolerate unknown fields.
- **Field removals or semantic changes** require a new version.

Result: there is no flag day. A long-running channel can contain v1
records authored years before v2 readers came online; both keep
working. The substrate never rewrites historical records.

This policy applies to envelope and payload schemas equally.
