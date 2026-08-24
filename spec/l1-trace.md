# L0 to L1 traceability

L0 goal: isolate each user's Plants and make Telegram routing predictable.

-> L1 schema: `spec/schemas/plant-registry-core.schema.json`

-> Why critical: the registry is shared by onboarding, Plant selection,
gateway routing, Cycle creation, Dashboard reads and delivery publication.

---

L0 goal: preserve exact Plant-specific dialogue and delivery history.

-> L1 schema: `spec/schemas/activity-entry-core.schema.json`

-> Why critical: gateway hooks, Cycle input, final publication, Dashboard admin
messages and delivery reconciliation all append the same log.

---

L0 goal: recover safely from retries and uncertain Telegram delivery.

-> L1 artifact: `spec/errors/errors.yaml`

-> Why critical: workers and operators must distinguish safe correction/retry
from states that require manual inspection.

---

L0 constraint: Hermes Kanban is the only source of truth for workflow state.

-> L1 decision: no local Cycle schema or API mirror.

-> Why critical: a second representation would drift from Hermes tasks, runs,
dependencies and retries.
