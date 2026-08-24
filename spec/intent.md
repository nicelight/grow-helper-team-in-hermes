# L0 — GrowHelper intent

## Summary

GrowHelper is a Russian-speaking, multi-user Telegram assistant for long-lived
plant cultivation. Each Plant combines one cultivation contour, a persistent
workspace and one Hermes Kanban board. GrowHelper keeps observations and
history, coordinates specialist Profiles and publishes one useful final reply.

## Goals

- Keep each user's Plants, dialogue, media, state and work isolated by Plant.
- Preserve compact long-term cultivation memory without injecting full history
  into every model turn.
- Maintain the evidence chain `observation -> inference -> recommendation ->
  follow-up` across specialist handoffs.
- Make Telegram creation, selection and work with the active Plant predictable.
- Recover safely from retries and uncertain Telegram delivery without silently
  duplicating final replies.
- Remain maintainable as a small Hermes-native extension.

## Non-goals

- Acting as a general-purpose assistant outside plant cultivation and
  explanation of GrowHelper itself.
- Forking Hermes or reproducing its Profiles, sessions, Kanban or Dashboard.
- Creating a separate domain backend, event bus, scheduler or Plant database.
- Automating physical actions on the user's plants.
- Formalizing every internal function, Telegram payload or specialist prompt as
  an independent specification.

## Constraints

- Telegram is the end-user UI; Hermes Web Dashboard is the trusted admin/debug
  UI.
- One Plant owns one persistent workspace and one Kanban board.
- Plant files own cultivation history; Kanban owns task and worker state.
- GrowHelper is the only public Telegram identity.
- The plugin is glue and deterministic state handling, not an agronomic
  decision engine.
- Product code remains Hermes-native and KISS-oriented.
- Persistent project changes are complete only after they are stored in GitHub.

## Assumptions

- Hermes continues to provide Profiles, Kanban workers, sessions, tools and the
  Dashboard runtime.
- A Telegram chat binding identifies the active Plant for that conversation.
- Plant workspaces are private filesystem data under one GrowHelper Linux user.
- Operators inspect ambiguous delivery or broken Cycle state through the
  Dashboard and existing recovery scripts.

## Risks

- A registry/workspace mismatch can orphan a Plant or route a turn incorrectly.
- A Telegram timeout may occur after delivery and cause a duplicate if retried
  automatically.
- Prompt changes can blur Profile boundaries or leak internal Hermes identity.
- Unbounded history injection can exhaust context and reduce answer quality.
- Duplicating Kanban state outside Hermes can create conflicting sources of
  truth.

## Open Questions (P0)

None. The current L0 boundary is resolved by `docs/BRIEF_v2.md`, `team.yaml` and
the accepted Telegram workflow.
