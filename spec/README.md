# GrowHelper SDD specifications

`spec/` contains the compact L0-L1 contract for GrowHelper. It helps an agent
answer three questions before changing code:

1. What product boundary must remain unchanged?
2. Which data and state transitions are owned by GrowHelper?
3. Which stable failures must callers handle explicitly?

Read in this order:

1. [`intent.md`](intent.md) — L0 goals, constraints and non-goals.
2. [`l1-scope.md`](l1-scope.md) — domain boundary, owners and invariants.
3. [`l1-trace.md`](l1-trace.md) — why each L1 artifact exists.
4. [`schemas/`](schemas/) — machine-readable persistent data contracts.
5. [`errors/errors.yaml`](errors/errors.yaml) — stable runtime error codes.
6. [`spec-graph.yaml`](spec-graph.yaml) — dependencies between artifacts.

`docs/BRIEF_v2.md` remains the normative product and architecture contract.
These specifications make its key domain boundaries more precise; they do not
replace the BRIEF, `team.yaml`, Hermes Kanban contracts or implementation tests.

## Maintenance rule

Update the relevant specification in the same change when modifying:

- Plant registry fields, ownership, binding or onboarding states;
- the confirmed specimen roster contract in `baseline.md`;
- the canonical shape or semantics of `activity.jsonl` entries;
- stable machine-readable errors exposed by GrowHelper tools;
- an L0 product boundary or a dependency recorded in the graph.

Do not add a schema merely because an implementation object exists. Add one
only for a persistent or shared contract whose ambiguity creates a current
maintenance risk. In particular, do not mirror Hermes Kanban, Telegram Bot API
or internal Python DTOs here.
