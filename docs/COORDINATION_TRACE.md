<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Agent Rally Point Coordination Trace Format

`changes.jsonl` is Agent Rally Point's canonical multi-agent coordination trace.
It is not just a message bus. It is the durable, append-only record of how
independent coding agents coordinated inside a repo: presence-adjacent events,
handoffs, feedback, phase changes, dependency changes, commits, and future
conflict/signature/sync events.

The trace format is intentionally local-first and substrate-level:

- no daemon required
- no network required
- no orchestration runtime required
- no hard dependency on a specific agent framework, editor, or terminal UI
- append-only JSONL that humans can inspect and tools can replay

Herdr, ACP, A2A, CI, build-loop, Claude Code, Codex, Pi, Cursor, and future
adapters are consumers/producers of the trace. They are not the trace format.

## Design goals

1. **Repo-scoped coordination truth** — one trace per canonical repo channel.
2. **Append-only auditability** — no mutation API; history remains replayable.
3. **Stable identity** — every new event has an `id` independent of local
   revision number.
4. **Causal reconstruction** — `thread_id`, `causation_id`, and
   `correlation_id` let tools rebuild handoff chains and multi-agent timelines.
5. **Warn-not-drop compatibility** — old consumers tolerate new fields and
   unknown event kinds.
6. **Schema-guided interoperability** — known event payloads have JSON Schemas,
   but validation remains advisory at the substrate layer.
7. **Future signing and sync readiness** — the envelope names the fields a
   signer/sync layer will need without making distributed operation mandatory.
8. **Trace-first product surface** — replay, reports, and scorers should operate
   over this format rather than scraping terminal transcripts.

## Relationship to other protocols

### ACP

ACP is an agent/editor transport protocol. It defines how clients and agents
exchange sessions, prompts, updates, tool calls, and permissions.

ARP's trace format defines coordination semantics across independent agents in a
repo. An ACP bridge can emit ARP events or consume ARP handoffs, but ACP is not
the source of truth for ARP coordination.

### A2A

A2A is a network protocol for agent-to-agent task exchange. Its task/context IDs
and lifecycle events influenced ARP's thread and causation model. ARP remains a
filesystem-native trace for local-first coding-agent coordination.

### OpenTelemetry

OpenTelemetry traces use spans, events, links, and attributes to reconstruct
causal relationships across distributed systems. ARP borrows the same separation
of identity/correlation metadata from event payloads, but ARP events are stored
as JSONL records in a repo channel rather than exported to an observability
backend.

### CloudEvents

CloudEvents directly influenced the envelope split: top-level metadata for
routing, identity, time, type, source, and schema; payload data in a separate
body. Native ARP records keep the legacy `payload` field for existing consumers,
so they should be described as CloudEvents-aligned rather than strict
CloudEvents JSON-format records. A strict CloudEvents projection can be produced
by mapping `payload` to `data` and omitting ARP-only compatibility fields.

## Envelope model

Each line in `changes.jsonl` is one complete JSON object.

Compatibility-core fields:

| Field | Purpose |
|---|---|
| `kind` | Coarse record discriminator. Unknown kinds warn, never drop. |
| `tool` | Tool id such as `claude_code`, `codex`, `pi`, `cursor`, `ci`. |
| `model` | Model id supplied by the producer. |
| `run_id` | Producer/session run identifier. |
| `app_slug` | Channel slug. |
| `payload` | Domain-specific event body. |
| `revision` | Local channel revision snapshot. |

CloudEvents-aligned identity/routing fields for new records:

| Field | Purpose |
|---|---|
| `specversion` | CloudEvents spec version marker, currently `1.0`. |
| `id` | Stable event identity (`evt_<32 hex>`). |
| `source` | Producer URI, e.g. `urn:agent-rally-point:tool:pi`. |
| `subject` | Producer-scoped subject, currently the app/channel slug by default. |
| `time` | RFC3339 UTC event timestamp. |
| `type` | Versioned semantic event type, e.g. `agent-rally.handoff.created.v1`. |
| `datacontenttype` | Payload media type, currently `application/json`. |
| `dataschema` | URI of the JSON Schema describing `payload`. |

ARP causal/correlation fields for new records:

| Field | Purpose |
|---|---|
| `thread_id` | Related-event group (`thr_<32 hex>`), such as handoff → feedback. |
| `causation_id` | Direct parent event `id`, if any. |
| `correlation_id` | Broader workflow/session correlation id. Defaults to `thread_id`. |

`thread_id`, `causation_id`, and `correlation_id` are ARP extensions, not part
of the CloudEvents 1.0 spec. They borrow the identity/causation split from
OpenTelemetry and A2A. A strict CloudEvents projection (see below) omits them.

`subject` currently defaults to `app_slug`. This is a placeholder: CloudEvents
defines `subject` as identifying *what within the source* the event refers to
(e.g. a file path, work item, or thread context). Producers of `handoff`,
`feedback`, etc. should override `subject` with a meaningful value (e.g. the
work item id or the first ref'd file) once the handoff lifecycle layer lands.

Top-level metadata is for routing, ordering, identity, versioning, and
correlation. Domain-specific content belongs in `payload`.

Example native ARP record:

```json
{
  "specversion": "1.0",
  "id": "evt_345ea9b74be3461b9473e0cf80a79d40",
  "source": "urn:agent-rally-point:tool:pi",
  "subject": "agent-rally-point",
  "time": "2026-05-26T18:06:19.660Z",
  "kind": "handoff",
  "type": "agent-rally.handoff.created.v1",
  "tool": "pi",
  "model": "gpt-5.1-codex-max",
  "run_id": "pi-canonical-events",
  "app_slug": "agent-rally-point",
  "thread_id": "thr_6d9d66a7e94844faacaa41f2fc1bafa5",
  "causation_id": null,
  "correlation_id": "thr_6d9d66a7e94844faacaa41f2fc1bafa5",
  "datacontenttype": "application/json",
  "dataschema": "urn:agent-rally-point:schema:handoff.created.v1",
  "payload": {
    "from_tool": "pi",
    "to_tool": "claude_code",
    "subject": "review canonical event model",
    "requires_ack": true
  },
  "revision": 18
}
```

## CloudEvents projection

Native ARP records retain `payload`, `kind`, `tool`, `model`, `run_id`,
`app_slug`, `revision`, and ARP causal fields for compatibility and local
coordination. A strict CloudEvents JSON-format projection is mechanical:

| Native ARP field | CloudEvents JSON field |
|---|---|
| `specversion` | `specversion` |
| `id` | `id` |
| `source` | `source` |
| `subject` | `subject` |
| `time` | `time` |
| `type` | `type` |
| `datacontenttype` | `datacontenttype` |
| `dataschema` | `dataschema` |
| `payload` | `data` |

ARP-only compatibility and causal fields can be carried as CloudEvents
extension attributes when a bridge needs them, or omitted when a downstream
consumer only needs the event payload.

## Revision vs event identity

`revision` is a local monotonic channel counter. It is excellent for cheap
checkpoint reads and local ordering. It is not a portable event identity.

`id` is stable identity. It survives export, replay, signing, and future
sync/merge. Two records with the same `id` represent the same logical
event; two records with different `id`s are different events even if they
have similar payloads.

Ordering rules:

1. Within one channel, sort primarily by `revision`.
2. For human display, use `time` (RFC3339) as supporting context.
3. Across future synchronized channels, use event identity plus causation/sync
   metadata rather than assuming revisions are globally comparable.

## Causal and thread model

- `thread_id` groups a chain of related events.
- `causation_id` points to the direct event that caused this one.
- `correlation_id` groups a wider workflow when multiple threads are part of one
  larger run.

Example:

```text
evt_A handoff.created       thread thr_1 causation null
evt_B feedback.posted       thread thr_1 causation evt_A
evt_C phase.recorded        thread thr_1 causation evt_B
```

This is enough for replay tools to draw interleaved multi-agent timelines and
for future scorers to ask whether handoffs were acknowledged, feedback was
acted on, or blockers remained unresolved.

## Event type mapping

Known `kind` values map to canonical event `type`s:

| `kind` | `type` |
|---|---|
| `commit` | `agent-rally.commit.created.v1` |
| `dep-change` | `agent-rally.dependency.changed.v1` |
| `phase` | `agent-rally.phase.recorded.v1` |
| `arch-scan-complete` | `agent-rally.arch-scan.completed.v1` |
| `feedback` | `agent-rally.feedback.posted.v1` |
| `handoff` | `agent-rally.handoff.created.v1` |
| `ack` | `agent-rally.handoff.acknowledged.v1` |

Future event types should prefer the form:

```text
agent-rally.<noun>.<verb-past-tense>.vN
```

Examples:

```text
agent-rally.handoff.acknowledged.v1
agent-rally.claim.created.v1
agent-rally.claim.released.v1
agent-rally.blocker.raised.v1
agent-rally.conflict.detected.v1
agent-rally.signature.attached.v1
```

## Schema registry

Packaged JSON Schemas live under `agent_rally_point/schemas/`.

Current schemas:

- `envelope.v1.schema.json`
- `handoff.created.v1.schema.json`
- `handoff.acknowledged.v1.schema.json`
- `feedback.posted.v1.schema.json`
- `phase.recorded.v1.schema.json`
- `commit.created.v1.schema.json`
- `dependency.changed.v1.schema.json`
- `arch-scan.completed.v1.schema.json`

Schema `$id` values use `urn:agent-rally-point:schema:...` identifiers. These
are stable identifiers, not network locations.

Schemas are tooling contracts. They support replay/report/scorer/bridge tooling,
but the substrate keeps the existing warns-not-drops behavior: malformed or
unknown events are kept, and consumers decide whether to act.

## CLI surfaces

The package includes initial plain-text trace commands:

```bash
agent-rally report --since 2h
agent-rally replay --since 2h
agent-rally thread <id-or-thread-id>
agent-rally inbox --tool codex
agent-rally handoff --to codex --subject "review schema" --files docs/SCHEMA.md
agent-rally ack <handoff-id> --summary "reviewed"
agent-rally reject <handoff-id> --reason "out of scope"
agent-rally needs-info <handoff-id> --reason "which branch?"
agent-rally score --since 2h
agent-rally herdr status --report
agent-rally herdr inject <handoff-id>
```

These commands are deliberately text-first and operate on the trace itself; a
future TUI can build on the same query helpers. `score` is deterministic: it
checks coordination invariants such as open required handoffs, dangling causal
references, dangling ack/feedback references, and unresolved `needs-info` states.
The Herdr bridge is a dogfood adapter: it can report pending handoffs as Herdr
custom status or inject a handoff prompt into the target pane, while ARP remains
the trace source of truth. Injection and status reporting default to panes whose
Herdr `cwd` matches the command `--workdir`/current directory; pass
`--allow-other-workspace` only when intentionally targeting a matching agent in
another workspace.

## Replay and scoring

The trace format should support two product surfaces:

1. **Replay/report** — reconstruct what happened across agents, tools, commits,
   handoffs, feedback, and phase changes.
2. **Scoring** — evaluate coordination quality from the trace itself.

Example scorer questions:

- Did every `requires_ack` handoff receive a response?
- Did feedback point to a known handoff/thread?
- Did commits happen after verification or before it?
- Did dependency changes happen before tests that assumed the old environment?
- Did soft-claim conflicts remain unresolved?
- Did a session close with open blockers?

This is distinct from LLM observability products that track prompts, tokens,
latency, or tool calls. ARP's trace is about coordination correctness across
independent coding agents.

## Trust model and known caveats

The substrate is trust-on-write. `append_change()` accepts whatever a producer
emits and only warns on unknown `kind`s; it does not enforce envelope shape.
Consequences worth naming up-front:

- **Producers can claim any `correlation_id` or `thread_id`**, including ones
  belonging to threads they did not start. This is fine for cooperative local
  use. It is **not** fine once signing lands — a signed event with a forged
  `correlation_id` is still a valid signature over forged metadata. The signing
  extension MUST constrain or attest the correlation fields a producer can set
  (e.g. signing must cover the full envelope, and verifiers must check that
  `correlation_id`/`thread_id` matches the producer's prior events or an
  out-of-band capability).
- **`causation_id` may dangle.** Nothing guarantees the referenced parent
  event exists in this channel (it may be in a different channel, a synced
  replica, or simply never emitted). Replay and report tooling MUST handle
  unresolved `causation_id` gracefully — typically by rendering "[unknown
  parent]" rather than failing.
- **`id` / `thread_id` shape is not enforced by the substrate.**
  `make_record()` accepts any string; only the packaged JSON Schema rejects
  malformed ids. This is consistent with warns-not-drops. Strict producers
  should pass ids generated by `new_event_id()` / `new_thread_id()`.
- **Synthetic `dataschema` URIs for unknown kinds.** `event_type_for_kind()`
  falls back to `agent-rally.<kind>.v1` and `dataschema_for_type()` mints a
  URN that points at no packaged schema. The URN is well-formed but
  unresolvable; consumers should treat unknown `dataschema` URNs as "best-
  effort `payload`."
- **Legacy `ts` field on read.** Records emitted before 0.4 carry a
  floating-point `ts` (epoch seconds) instead of a string `time` (RFC3339).
  `record_epoch()` reads either and prefers `ts` when present for backward
  compatibility. `make_record()` no longer emits `ts`; new records use
  `time` exclusively.
- **Herdr injection escalates a recorded event into agent input.** The
  `agent-rally herdr inject` command invokes `herdr pane run` with text
  derived from a handoff payload (subject, notes, `ref_files`). Any tool
  with ARP write access can therefore plant prompt-injection text inside
  another agent's prompt context once an operator runs injection.
  Operationally, treat ARP write access as equivalent to *terminal write*
  access on the receiving agent — and require an explicit operator action
  (the `herdr inject` invocation itself) before that escalation happens.
  Future signing should cover the full envelope so an injected prompt can
  be traced back to the producer who signed it.
  Cross-workspace injection requires the explicit `--allow-other-workspace`
  flag; by default a `cwd` mismatch blocks the send (see Herdr bridge).

## Implementation notes

- **Schema `$id` namespace.** Current schemas use `urn:agent-rally-point:schema:`
  identifiers — stable but not resolvable over the network. A future version
  MAY switch to `https://` URIs once the project hosts schemas at a stable URL,
  allowing tools to fetch schemas at runtime. The URN values will remain valid
  identifiers either way.
- **`make_record()` keyword surface.** As of envelope v1 the function accepts
  seven optional canonical-metadata kwargs in addition to the seven legacy
  core kwargs. Further additive growth should consider a `CanonicalMeta`
  dataclass to keep the call sites readable.

## Future extensions

### Signed events

The canonical envelope enables signing later because it separates identity,
correlation, and payload. A signing extension should define:

- canonical JSON bytes to sign (the full envelope, not just `payload`, so
  forged `correlation_id` / `thread_id` cannot ride on a legitimate signature
  — see "Trust model and known caveats" above)
- signer identity and key discovery
- signature field location
- unsigned-event compatibility behavior
- key rotation story

### Cross-machine sync

Future sync should treat `id` as logical identity and avoid assuming local
`revision` values are comparable across machines. Local revisions remain useful
for each replica's checkpoint reads. Merge/sync metadata can be added without
changing the core trace model.

### Bridges

ACP, A2A, Herdr, and CI adapters should consume/produce this trace format. They
should not redefine the coordination semantics independently.
