<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Rally Point Record Schema

The canonical wire format for `changes.jsonl`. Single source of truth: `agent_rally_point/changes.py::make_record`. This document mirrors that implementation; if they disagree, the implementation wins and this doc is the bug.

For product-level framing, design goals, and future replay/scoring/signing/sync
implications, see [`COORDINATION_TRACE.md`](COORDINATION_TRACE.md). This file is
the field-level schema reference.

## Format

Each line in `changes.jsonl` is one self-contained JSON object terminated by `\n`. No multi-line records, no leading/trailing whitespace, no comments. Lines are written via `O_APPEND` single-write so concurrent writers from different processes never interleave.

## Top-level fields

The original v0 core fields remain the compatibility baseline. New records
emitted by `make_record()` also carry canonical event identity and correlation
fields so future consumers can build inboxes, replay views, signatures, sync,
and protocol bridges without parsing per-kind payloads.

Top-level fields are for routing, ordering, identity, versioning, and
correlation. Domain-specific content stays inside `payload`.

### Compatibility core

| Field       | Type       | Description |
|-------------|------------|-------------|
| `kind`      | `string`   | Record discriminator. See [Record kinds](#record-kinds). |
| `tool`      | `string`   | Stable tool id (`claude_code`, `codex`, `cursor`, `build-loop`, etc.). Defaults to `"unknown"`. |
| `model`     | `string`   | Stable model id (`claude-opus-4-7`, `gpt-5`, etc.). Defaults to `"unknown"`. |
| `run_id`    | `string`   | Caller-chosen run identifier (the consuming tool's run/session id). Defaults to `"unknown"`. |
| `app_slug`  | `string`   | Channel slug (the directory name under `apps/`). Worktree-independent — see `channel_paths.app_slug`. |
| `payload`   | `object`   | Per-kind structured body. Schema varies by `kind`; see below. |
| `revision`  | `integer`  | Channel revision counter snapshot AT THE TIME this record was posted. Set by `post()`; raw `append_change` callers MUST set this themselves. |

### Canonical event metadata

| Field              | Type              | Description |
|--------------------|-------------------|-------------|
| `specversion`      | `string`          | CloudEvents spec version marker. Currently `1.0`. |
| `id`               | `string`          | Opaque stable event id (`evt_<32 hex>`). Unlike `revision`, this survives export/sync/merge. |
| `source`           | `string`          | Producer URI, e.g. `urn:agent-rally-point:tool:pi`. |
| `subject`          | `string`          | Producer-scoped subject. Defaults to `app_slug`. |
| `time`             | `string`          | RFC3339 UTC timestamp. Sole event-time field as of 0.4 (legacy epoch-seconds `ts` is tolerated on read but no longer emitted). |
| `type`             | `string`          | Versioned semantic event type, e.g. `agent-rally.handoff.created.v1`. `kind` remains the coarse discriminator. |
| `thread_id`        | `string`          | Opaque thread id (`thr_<32 hex>`) grouping related events such as handoff → ack → feedback. |
| `causation_id`     | `string | null`   | Direct parent event `id`, when this event was caused by a prior event. |
| `correlation_id`   | `string`          | **Optional.** Broader workflow/session correlation id, used when a workflow correlates across multiple `thread_id`s. Absent when not set; readers should fall back to `thread_id`. |
| `datacontenttype`  | `string`          | Payload media type. Currently always `application/json`. |
| `dataschema`       | `string`          | URI for the schema that describes `payload`, e.g. `urn:agent-rally-point:schema:handoff.created.v1`. |

Canonical metadata is CloudEvents-aligned. Native ARP records retain the legacy
`payload` field for existing consumers, so strict CloudEvents JSON-format output
should be produced as a projection that maps `payload` to `data` and omits
ARP-only compatibility fields. The field split mirrors the same design rule:
metadata that routers, replay tools, and bridges need goes top-level; event data
stays in `payload` for the native trace.

For the projection table and native record example, see
[`COORDINATION_TRACE.md#cloudevents-projection`](COORDINATION_TRACE.md#cloudevents-projection).

Pre-canonical records without these fields remain valid historical records.
Readers must tolerate their absence. New records built with `make_record()` emit
the canonical fields.

### Canonical known event types

| `kind` | `type` | `dataschema` |
|--------|--------|--------------|
| `commit` | `agent-rally.commit.created.v1` | `urn:agent-rally-point:schema:commit.created.v1` |
| `dep-change` | `agent-rally.dependency.changed.v1` | `urn:agent-rally-point:schema:dependency.changed.v1` |
| `phase` | `agent-rally.phase.recorded.v1` | `urn:agent-rally-point:schema:phase.recorded.v1` |
| `arch-scan-complete` | `agent-rally.arch-scan.completed.v1` | `urn:agent-rally-point:schema:arch-scan.completed.v1` |
| `feedback` | `agent-rally.feedback.posted.v1` | `urn:agent-rally-point:schema:feedback.posted.v1` |
| `handoff` | `agent-rally.handoff.created.v1` | `urn:agent-rally-point:schema:handoff.created.v1` |
| `ack` | `agent-rally.handoff.acknowledged.v1` | `urn:agent-rally-point:schema:handoff.acknowledged.v1` |
| `claim` | `agent-rally.claim.created.v1` | `urn:agent-rally-point:schema:claim.created.v1` |
| `claim-release` | `agent-rally.claim.released.v1` | `urn:agent-rally-point:schema:claim.released.v1` |
| `blocker` | `agent-rally.blocker.raised.v1` | `urn:agent-rally-point:schema:blocker.raised.v1` |
| `blocker-resolved` | `agent-rally.blocker.resolved.v1` | `urn:agent-rally-point:schema:blocker.resolved.v1` |

Packaged JSON Schemas live under `agent_rally_point/schemas/`. They are
diagnostic contracts for tooling and future bridges; validation remains
warns-not-drops at the substrate layer.

### Canonical fields and derived aliases

Several fields express overlapping concepts because the envelope evolved
through three eras (v0 legacy, canonical-identity, CloudEvents alignment).
The hierarchy below is the single source of truth — dispatch and signing
code should use the **canonical** field; other forms are derived aliases
kept for compatibility or for spec consumers.

| Concept | Canonical | Derived alias(es) | Notes |
|---|---|---|---|
| Event semantic type | `type` (`agent-rally.X.Y.vN`) | `kind` (coarse, e.g. `handoff`) | Use `kind_for_type()` to derive `kind`. Future readers may rely solely on `type`. |
| Workflow grouping | `thread_id` | `correlation_id` (optional, absent unless workflow spans threads) | Default is "one thread = one workflow"; `correlation_id` is only present for the multi-thread case. |
| Producer identity | `tool` / `model` / `run_id` | `source` (CloudEvents URI form of `tool`) | `source` is required by CloudEvents 1.0; ARP-native consumers prefer the structured `tool`/`model`/`run_id` triple. |

Signed-event canonicalization (see [`SIGNED_EVENTS.md`](SIGNED_EVENTS.md))
covers all envelope fields including derived aliases. A signer that emits
`kind` and `type` is committing to both being consistent for that record.

### NON-GOAL guard

Records carry **structure and data-flow only** — never call-frequency, invocation-count, or hit-count fields. Forbidden keys (case-insensitive, asserted by `test_changes.py::_assert_no_freq`):

```
count, counts, frequency, freq, invocations, invocation_count,
calls, num_calls, call_count, hits, usage, usage_count
```

Rally Point is a coordination protocol, not a telemetry pipeline. Telemetry data belongs in `~/.bookmark/cost-ledger.jsonl` or an equivalent sink, not the rally channel.

## Record kinds

`KNOWN_KINDS` in `changes.py` is the canonical list. Unknown kinds are **kept and warned**, never dropped (D7 — "warns not drops"). Consumers must tolerate unfamiliar kinds.

### `commit`

A commit landed on a branch the channel cares about.

```json
{
  "time": "2026-05-16T18:22:23.920Z", "kind": "commit", "tool": "claude_code",
  "model": "claude-opus-4-7", "run_id": "build-2026-05-23-...",
  "app_slug": "build-loop", "revision": 18,
  "payload": {
    "branch": "feat/foo",
    "commit_sha": "abc1234",
    "subject": "feat(x): bar",
    "files_changed": ["src/x.py", "tests/test_x.py"]
  }
}
```

Consumed by: peer sessions deciding whether to rebase, watcher dispatching `inbox/<tool>.jsonl` notifications.

### `dep-change`

A dependency manifest changed (`package.json`, `pyproject.toml`, `Cargo.toml`, etc.). Triggers a `reinstall` reaction in `checkpoint_read`.

```json
{
  "kind": "dep-change",
  "payload": {
    "manifest": "pyproject.toml",
    "added": ["watchfiles"],
    "removed": [],
    "session_id": "sess-..."
  }
}
```

### `phase`

A coordination phase event from a build-loop-style orchestrator. The most heavily used kind; the `phase` value inside `payload` is itself an open enumeration.

```json
{
  "kind": "phase",
  "payload": {
    "phase": "verification-handoff" | "joined-existing-coord" | "run-closeout" | ...,
    "target_peer": "codex",
    "verification_pending_steps": [1, 2, 3],
    "summary": "Audit work complete; awaiting peer verification."
  }
}
```

Common `phase` values in build-loop:

| `phase`                  | Meaning |
|--------------------------|---------|
| `verification-handoff`   | Plan owner is handing a work item to a verifier peer. |
| `verification-response`  | Verifier responding with PASS/VARIANCE/BLOCKED. |
| `joined-existing-coord`  | A session joined a pre-existing coordination file. |
| `run-closeout`           | Run ended; closeout reaping happened. |
| `chunk-close`            | A planning chunk closed and committed. |

### `arch-scan-complete`

An architecture scan finished. Triggers a `re-baseline` reaction in `checkpoint_read`.

```json
{
  "kind": "arch-scan-complete",
  "payload": {
    "digest_path": "arch/digest.json",
    "files_scanned": 142
  }
}
```

### `feedback`

Verifier verdict on a peer's handed-off work item (added 2026-05-20 for the audit-execution dogfood).

```json
{
  "kind": "feedback",
  "payload": {
    "step": 4,
    "verdict": "PASS" | "VARIANCE" | "BLOCKED",
    "rationale": "...",
    "evidence_paths": ["..."]
  }
}
```

### `handoff`

Plan owner → verifier work-item transfer (added 2026-05-20).

```json
{
  "kind": "handoff",
  "payload": {
    "from_tool": "claude_code",
    "to_tool": "codex",
    "work_item": "verify Step 4 archive",
    "deadline_ts": 1779400000.0
  }
}
```

### `ack`

Receiver response to a handoff. New CLI lifecycle commands use `verdict` values
`done`, `rejected`, and `needs-info`.

```json
{
  "kind": "ack",
  "payload": {
    "ref_handoff_id": "evt_...",
    "verdict": "done",
    "summary": "reviewed; no blockers"
  }
}
```

### `claim`

Ownership claim over a coordination resource. Claims are awareness and routing
signals, not filesystem locks. The CLI uses `rally claim --path <file>` as sugar
for a normalized `file:<repo-relative-posix-path>` resource.

This is distinct from historical `soft-claim` reactions in `checkpoint_read`,
which are inferred from overlapping changed files in a checkpoint delta. A
`claim` event is explicit intent: "this tool is currently working on this
resource." Preflight/checkpoint soft-claims remain compatibility awareness
signals; `rally conflicts` derives conflicts from active explicit claims.
Multiple active claims by the same `owner_tool` on the same resource are treated
as duplicate/self-overlapping intent, not a conflict; conflicts require two or
more distinct owners.

```json
{
  "kind": "claim",
  "payload": {
    "owner_tool": "pi",
    "resource": "file:docs/SCHEMA.md",
    "subject": "edit schema",
    "notes": "updating canonical event docs"
  }
}
```

### `claim-release`

Release a prior ownership claim.

```json
{
  "kind": "claim-release",
  "payload": {
    "ref_claim_id": "evt_...",
    "reason": "done"
  }
}
```

### `blocker`

Stop-sign event for stuck coordination.

```json
{
  "kind": "blocker",
  "payload": {
    "subject": "need branch",
    "reason": "which branch should the reviewer inspect?",
    "severity": "blocked",
    "resource": "task:review"
  }
}
```

### `blocker-resolved`

Resolve a prior blocker.

```json
{
  "kind": "blocker-resolved",
  "payload": {
    "ref_blocker_id": "evt_...",
    "resolution": "branch supplied"
  }
}
```

## Reactions

`checkpoint_read` derives reactions from the new-since-last-checkpoint records. Reactions are **awareness signals**, never locks (D4):

| Reaction        | Trigger                                              | Severity         |
|-----------------|------------------------------------------------------|------------------|
| `reinstall`     | At least one `dep-change` record in the delta        | informational    |
| `re-baseline`   | At least one `arch-scan-complete` record             | informational    |
| `soft-claim`    | Peer touched files that overlap this session's files | warning OR informational |

`soft-claim` carries a `severity` and `reason`:

- `severity: "informational"`, `reason: "merged_residue"` or `"squash_landed"` — peer's edits already landed on `main`; not a conflict. Proceed.
- `severity: "warning"`, `reason: "active_conflict"` — peer branch unmerged AND files differ from `main`. Treat as a real conflict source.

The reaction never includes a "block" verdict. The consumer decides what to do.

## Revision ordering

`revision` is monotonic per-channel. The `post()` helper bumps the revision *before* writing the record, so:

```
record.revision == revision_at_time_of_post
```

A reader observing `revision == N` is guaranteed to find a record line with `revision: N` in the log (modulo the brief window between the bump and the append; readers re-read on the next checkpoint and converge). A reader that calls `append_change` without `bump_revision` leaves the log ahead of the counter — peers never notice the record.

## Read API

```python
from agent_rally_point.changes import read_changes_since
records, new_offset = read_changes_since(channel_dir, offset)
```

Returns only complete lines (trailing partial lines from a mid-append writer are left for the next poll). Corrupt lines are skipped without halting iteration. The offset advances past complete lines only; reading is idempotent and re-readable.

## Validation

`validate_record(record)` is **warns-not-drops** by design (D7):

- Missing required key → stderr warning, record kept.
- Unknown `kind` → stderr warning, record kept.
- The reader, not the substrate, decides whether to act on a malformed record.

This invariant means a future schema extension can ship records with new fields and old consumers still tolerate them.
