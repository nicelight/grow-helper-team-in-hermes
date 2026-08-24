# L1 scope and domain invariants

## Domain core

The current core consists of two GrowHelper-owned persistent entities and one
Hermes-owned workflow entity:

| Key entity | Owner | Where described |
| --- | --- | --- |
| Plant registry | `grow-helper-monitor` | `spec/schemas/plant-registry-core.schema.json` |
| Activity entry | `grow-helper-monitor` | `spec/schemas/activity-entry-core.schema.json` |
| Kanban Cycle | Hermes Kanban | `docs/BRIEF_v2.md` and Hermes runtime contract; not duplicated in `spec/` |

Plant registry and Activity entry are the two local L1 core contracts. A Cycle
is essential to the product but GrowHelper stores only its references; Hermes
remains the source of truth for tasks, dependencies, runs and retries.

No OpenAPI contract is defined. GrowHelper has no separate public domain API:
Telegram, Hermes tools and Dashboard routes are delivery/runtime surfaces over
the same Plant-first contract.

## Plant registry invariants

- `plant_id` is the stable identity; `nickname` is the human business key and
  must be unique after normalization.
- A Plant belongs to one Telegram platform/chat/user tuple. A caller may select
  only a Plant matching that ownership tuple.
- Registry mutations are atomic under a file lock. A corrupt or unsupported
  registry fails closed and must not be replaced with an empty registry.
- A binding key is the normalized `platform:chat_id` tuple. It points to at most
  one active Plant, while the same owner may have multiple Plants.
- `pending_addplant` belongs to the binding, not a Plant: the Plant does not
  exist until a valid avatar has been received and compressed.
- A persisted avatar is always `photos/avatar.jpg`, JPEG and at most `500_000`
  bytes. The original heavy upload is not copied into the Plant workspace.
- `workspace_path` and `board_slug` are allocated at Plant creation and remain
  stable when the nickname changes.
- Missing legacy fields are read as `campaign_status=active`,
  `onboarding_stage=complete`, `avatar_path=null` without rewriting the entry.

### Campaign state

```text
onboarding -> active -> closed
```

- New Telegram `/addplant` Plants start in `onboarding`.
- A confirmed onboarding summary moves the Plant to `active`.
- `closed` Plants remain persistent and may be listed only where explicitly
  requested.

### Onboarding state

```text
awaiting_name -> collecting_campaign -> complete
```

- A Plant is created after avatar processing in `awaiting_name`.
- A valid or automatically selected nickname advances it to
  `collecting_campaign`.
- Explicit confirmation of the collected description advances it to
  `complete` and activates the Campaign.

## Activity entry invariants

- `activity.jsonl` is append-only and records exact public Telegram input,
  output and delivery outcome for one Plant.
- Each entry receives `timestamp` and `plant_id` at append time.
- `operator_message`, `growhelper_reply` and `admin_recommendation` are the only
  current public activity kinds.
- `phase` distinguishes direct turns, deterministic commands, Cycle input or
  update, final publication, recovery queueing and admin messages.
- An uncertain final Telegram delivery is a fence: automatic retry is refused
  until an operator reconciles it.
- Final publication is idempotent per Plant/Cycle at the workflow boundary, but
  the low-level append itself is deliberately non-idempotent.
- Entries have no synthetic mutable record ID. Their stable correlation fields
  are Plant, timestamp, kind, Telegram message/session identifiers and optional
  Cycle/idempotency keys. Do not invent a second activity database solely to
  add an ID.
- Extra fields are allowed for recovery metadata so old readers can retain new
  evidence without losing the canonical fields.

## Cross-entity invariants

- Every activity entry resolves to an existing `plant_id`.
- A Plant stores only `active_cycle_id`; full Cycle state is queried from its
  `board_slug` in Hermes.
- A final reply may be published only by the dispatcher-owned `grow-helper`
  final worker pinned to the Plant board and matching Cycle.
- Clearing `active_cycle_id` is allowed only after a confirmed final delivery or
  explicit delivery reconciliation.
- Plant workspace files and `activity.jsonl` must never become a mirror of
Hermes Kanban state.
