<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Signed Rally Events

This is a design spike for signing Agent Rally Point / Rally coordination events.
It is intentionally not an implementation commitment yet.

## Goal

Rally should remain local-first and append-only, while giving consumers a way to
answer:

- who produced this event?
- was the event modified after publication?
- is this producer trusted for this event kind?
- can this event safely participate in sync, Herdr injection, or automated
  decision-making?

Signing is not for secrecy. Rally events are still readable JSONL records.
Signing is for integrity, provenance, and trust policy.

## Non-goals

- No daemon or server requirement.
- No global identity provider requirement.
- No mandatory signing for legacy records.
- No encryption in the first signing layer.
- No attempt to prove that the human approved the event unless a separate
  human-approval event is signed by a trusted human identity.

## Threat model

Signing should detect or constrain:

1. **Tampering** — a record in `changes.jsonl` was edited after append.
2. **Producer spoofing** — an event claims `tool=codex` but was not signed by a
   key trusted for Codex.
3. **Causal forgery visibility** — an event claims a `thread_id`,
   `correlation_id`, or `causation_id` that gives it false authority or
   lineage. Signing makes that claim attributable; verifier policy still has to
   decide whether the producer is allowed to attach to that thread or parent.
4. **Sync injection** — a remote or copied channel introduces events from
   unknown producers.
5. **Herdr prompt injection escalation** — a handoff payload becomes live agent
   input through `rally herdr inject`; operators should know whether the handoff
   is trusted before injection.

Signing cannot prove that an LLM's statement is true. It also cannot prove that
a claimed causal relationship is semantically valid. It only proves which key
signed the event bytes and whether local policy trusts that key for the action.

## Event canonicalization

A signature covers the canonical event envelope, including `payload` and causal
metadata.

The signed material MUST include:

- `specversion`
- `id`
- `source`
- `subject`
- `time`
- `kind`
- `type`
- `tool`
- `model`
- `run_id`
- `app_slug`
- `thread_id`
- `causation_id`
- `correlation_id`
- `datacontenttype`
- `dataschema`
- `payload`
- `revision`

The signed material MUST NOT include the signature object itself.

Canonical JSON rules for v1:

- UTF-8 JSON object
- sorted object keys
- no insignificant whitespace
- shortest JSON separators: `,` and `:`
- preserve JSON value types exactly
- reject non-finite floats if they appear in payloads

This is **Rally's Python-canonical JSON**, not RFC 8785/JCS. It is sufficient
for a Python reference implementation and deliberately versioned so a future
Rust core can introduce `rally-json-v2` if cross-language byte equivalence needs
a stricter canonicalization profile.

Python reference shape:

```python
import copy
import json

def canonical_bytes(record: dict) -> bytes:
    unsigned = copy.deepcopy(record)
    unsigned.pop("signature", None)
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
```

This canonicalization is intentionally simple enough for Python now and Rust
later. If cross-language edge cases appear, Rally can introduce
`signature.canonicalization = "rally-json-v2"` without changing older records.

## Signature envelope

A signed event adds one top-level `signature` object:

```json
{
  "signature": {
    "version": "rally-signature-v1",
    "algorithm": "ed25519",
    "key_id": "key_7f3...",
    "signed_at": "2026-05-26T18:00:00.000Z",
    "signature": "base64..."
  }
}
```

`key_id` is a stable local identifier for the public key, not a global account
name. Recommended v1 shape: `key_` plus the first 16 lowercase hex characters
of `sha256(public_key_bytes)`. Trust policy maps key ids to producers and
capabilities.

## Identity and key storage

Initial implementation should use local key material only:

```text
~/.agent-rally-point/identity/
  keys/<key_id>.pub
  private/<key_id>.key        # mode 0600, never synced by Rally
  trust.toml
```

A later implementation may use platform keychains. The file layout keeps the
first version inspectable and portable.

Recommended algorithm: Ed25519.

Python stdlib does not include Ed25519. The implementation decision should be
explicit:

- use a small audited dependency such as `cryptography`; or
- implement signing in a Rust core binary; or
- defer signing until Rally has a native core.

Do not invent custom crypto.

## Trust policy

Trust is local policy, not a fact inside the event.

Example:

```toml
[[keys]]
key_id = "key_pi_local"
public_key = "base64..."
trusted_tools = ["pi"]
allowed_kinds = ["handoff", "claim", "blocker", "ack"]

[[keys]]
key_id = "key_codex_local"
public_key = "base64..."
trusted_tools = ["codex"]
allowed_kinds = ["ack", "feedback", "claim", "claim-release"]
```

A verifier combines signature validity plus policy:

| State | Meaning |
|---|---|
| `trusted` | signature valid; key known; policy permits `tool` + `kind` |
| `valid-untrusted` | signature valid, but key/policy is not trusted for this event |
| `unsigned` | no signature object; accepted for legacy compatibility |
| `invalid` | signature object exists but verification fails |
| `unknown-key` | key id is not present in local trust policy |
| `unsupported` | algorithm or canonicalization is unsupported |

Default behavior for local-only commands should be warn-not-drop. Commands that
escalate event content into agent input, sync, or automation may require
`trusted` or an explicit `--allow-untrusted` flag.

## CLI surface

Future commands:

```bash
rally identity init --tool pi
rally sign <event-id>
rally verify
rally verify --json
rally trust add <key-file> --tool codex --kinds ack,feedback,claim
```

Write commands may later support:

```bash
rally handoff --sign ...
rally claim --sign ...
```

But signing should not become mandatory until downstream tools can tolerate it.

## Herdr policy

`rally herdr inject <handoff-id>` should eventually print trust state before
injection. In strict mode it should refuse to inject unsigned/invalid/unknown-key
handoffs unless explicitly overridden.

Prompt injection is a semantic risk even for trusted events. Signing tells the
operator who produced the payload; it does not make the payload safe.

## Sync policy

Signed events make future sync viable because a receiving channel can merge
unknown events without blindly trusting them.

Minimum sync behavior:

- preserve signatures byte-for-byte;
- never re-sign someone else's event as if it were local;
- verify before using remote events for automation;
- display untrusted remote events separately or with warnings;
- include signature status in `rally diagnose` when trust affects next actions.

## Open questions

- Should trust policy live only in `~/.agent-rally-point`, or can repos commit a
  suggested `.rally/trust.toml` that users import?
- Should unsigned local events be auto-trusted during a transition window?
- Should a human identity be distinct from tool identities?
- Should Rally sign individual records only, or also periodic checkpoints / log
  segments for faster tamper detection?
- Should the first implementation wait for a Rust core to avoid adding Python
  crypto dependencies?
