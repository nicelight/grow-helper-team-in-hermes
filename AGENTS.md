# AGENTS.md


## Reasoning Policy: Selection Before Expansion (KISS Gate)

**Core rule:** A sufficient solution is a reason to stop expanding, not an
invitation to add optional improvements.

Choose the simplest solution that fully satisfies the current accepted
requirements, applicable constraints, and required workflow contracts. Stop
when they are satisfied.

- Before adding anything, check whether removing, simplifying, or reusing
  existing work is sufficient.
- Do not add abstractions, layers, configuration, extension points,
  dependencies, infrastructure, safeguards, or processes for hypothetical
  needs. Each addition must justify its implementation, verification,
  maintenance, and ownership cost through a current requirement, constraint,
  or evidenced material risk.
- When several approaches are sufficient, choose the one with fewer concepts,
  moving parts, and maintenance obligations. Do not merge independently
  verifiable outcomes merely to reduce their number.
- Patterns and principles, including SOLID, design patterns, and architectural
  boundaries, are tools—not goals or requirements.
- A possible edge case is not a requirement. Ask the operator before expanding
  scope for an uncovered problem.
- In review, report only evidenced defects or material risks that affect the
  requested verdict. Do not report hypothetical failures, optional
  improvements, or alternative architectures unless explicitly asked.


## Mission
This repository contains GrowHelper, a multi-user Telegram assistant for long-running plant cultivation workflows built on NousResearch Hermes Agent.
The product is primarily intended for a Russian-speaking audience.
The development agent must communicate with the operator in Russian. Use established English technical terms naturally where standard or clearer, e.g. `Profile`, `Kanban`, `worker`, `toolset`, `plugin`, `gateway`, `Dashboard`, `deploy`, `runtime`, `workflow`, `source of truth`, `smoke test`.
Keep operator communication concise and practical.

## Agent instruction writing

When adding or editing `SOUL.md`, skills, prompts, or other agent instructions, make the smallest edit that fully expresses the requested behavior.

Write compact, agent-oriented text while preserving all semantics needed to:
- perform the workflow correctly;
- explain required actions or choices to the user;
- enforce constraints and validate the result.

State each idea once within the same instruction. Remove repetition, meta-commentary, decorative prose, and rationale that does not affect a decision. Prefer concise condition → action → result wording.

Treat every `SOUL.md` as an independent runtime contract. Repeat a critical rule across different Profiles when each Profile must receive it directly.

If removing changed or added text does not affect agent behavior, necessary user guidance, constraints, or validation, remove it.

## Architecture
GrowHelper must remain Hermes-native and KISS-oriented:
- no Hermes core fork or patch unless explicitly approved;
- Hermes Profiles + Kanban + one GrowHelper plugin;
- one persistent Plant workspace and Kanban board per Plant/Campaign;
- Telegram is the end-user UI;
- Hermes Web Dashboard is the trusted admin/debug UI;
- no separate PostgreSQL domain backend;
- no separate event bus;
- no unnecessary orchestration layers or LLM calls;
- prefer the smallest solution that solves a real problem.
Do not add services, abstractions, databases, queues, or agent roles only because they look architecturally cleaner.

## Sources of truth
The canonical persistent source of truth for project code is GitHub.
Development may happen on the operator's local machine, directly on the server in a Git checkout, or temporarily in installed runtime files while debugging.
Persistent work is complete only when represented in Git and pushed to GitHub.
Use:
1. `docs/BRIEF_v2.md` — normative product/architecture contract and acceptance criteria.
2. `team.yaml` — canonical Profile roster, toolsets, Kanban and storage contract.
3. `deploy_history.md` — current production topology, paths, services and runtime quirks.
4. Relevant code and tests.
When they disagree:
- BRIEF describes intended architecture;
- code/tests describe current implementation;
- live server describes current runtime state;
- GitHub is the canonical persistent project state.

## Repository map
- `plugin/grow-helper-monitor/` — GrowHelper plugin.
- `plugin/grow-helper-monitor/growhelper_monitor/` — Python runtime.
- `plugin/grow-helper-monitor/dashboard/` — Dashboard extension.
- `profiles/` — SOUL/config overlays for seven Profiles.
- `templates/` — Plant workspace templates.
- `schemas/` — structured handoff/data schemas.
- `scripts/install-team.py` — idempotent installer/updater.
- `scripts/new-plant.py` — deterministic Plant creation.
- `scripts/doctor.py` — deployment diagnostics.
- `scripts/reconcile-delivery.py` — delivery reconciliation.
- `tests/` — automated tests.
- `docs/BRIEF_v2.md` — architecture contract.
- `deploy_history.md` — production handoff.

## Profiles
Permanent roster:
- `grow-helper` — user-facing orchestrator, routing, synthesis, canonical Plant files, final reply.
- `vision-observation` — observable visual facts only, no diagnosis.
- `plant-state` — normalized state, changes and trends without unjustified causal certainty.
- `cultivation-advisor` — hypotheses, evidence and reversible recommendations.
- `task-followup` — checks, measurements and deadlines.
- `data-curator` — reusable evidence, `candidate` / `validated`, owner of `dataset/`.
- `reviewer` — contradictions, unsupported claims and risky recommendations.
Critical invariant: `observation -> inference -> recommendation -> follow-up`
Do not blur role boundaries. `vision-observation` must not diagnose or prescribe actions.

## Plant-first storage
Canonical Plant workspace:
- `campaign.md`
- `baseline.md`
- `current-state.md`
- `history-summary.md`
- `activity.jsonl`
- `journal/`
- `photos/`
- `dataset/index.jsonl`
- `dataset/selected/`
Sources of truth:
- Kanban DB -> tasks, dependencies, runs, retries and worker handoffs.
- `activity.jsonl` -> exact public Telegram dialogue and delivery.
- `current-state.md` -> current Plant state.
- `history-summary.md` -> compact long-term trajectory.
- `journal/` -> detailed domain worklog.
- `dataset/` -> candidate/validated reusable evidence.
Do not build a second mirror of Kanban state.

## Kanban workflow
Create only the minimum relevant Cycle for a meaningful event.
Photo flow: `vision-observation -> plant-state -> cultivation-advisor -> optional reviewer -> grow-helper`
If a photo only needs state recording, the workflow may end after `plant-state`.
Measurement-only flow may run `plant-state` and `cultivation-advisor` in parallel.
Greetings, confirmations and simple conversational turns must not create a Kanban Cycle.
Specialists return handoffs through `kanban_complete(summary=..., metadata=...)`.
Do not invoke `reviewer`, `task-followup` or `data-curator` in every Cycle. Do not use swarm as the default route.

## Plugin responsibilities
`grow-helper-monitor` is a glue layer, not a new backend.
It may connect Telegram turns to Plants/Cycles, maintain `activity.jsonl`, create root Cycles through `growhelper_start_cycle`, publish final replies through `growhelper_publish_reply`, enforce idempotency, validate specialist metadata, expose Dashboard read APIs, and send admin recommendations.
It must not make agronomic decisions, duplicate Kanban, implement a parallel scheduler, become a domain backend, or control the user's real-world actions.

## Development policy
Prefer the smallest useful change.
Before editing:
1. find the actual implementation point;
2. inspect relevant tests;
3. determine whether the task affects repository code, live runtime config, or both;
4. avoid unrelated architecture changes.
Development may be performed locally or directly on the server.
Preferred persistent workflow: `Git checkout -> edit -> tests -> commit -> push -> deploy`
Direct editing under `/home/growhelper/.hermes/` is allowed for live debugging or emergency hotfixes.
If a runtime edit is meant to persist, reproduce it in the Git repository, test it, commit it, push it to GitHub, and ensure deployed runtime matches the committed implementation.
Do not treat uncommitted server-only edits as finished work.

## Tests
Before deploy:
```bash
bash tests/run-tests.sh
```
Do not consider a task complete with new known test failures.
For release artifacts:
```bash
bash scripts/build-release.sh
```
Do not bump `VERSION` or rebuild release artifacts without a reason.

## Production server
GrowHelper production:
- host: `108.181.252.78`
- Telegram bot: `@growhelperrubot`
- Linux user: `growhelper`
- Hermes root: `/home/growhelper/.hermes`
- Plant data: `/home/growhelper/grow-helper`
- shared Hermes runtime: `/usr/local/lib/hermes-agent`
Preferred SSH alias:
```text
growhelper-prod
```
Expected connection:
```bash
ssh growhelper-prod
```
For autonomous Codex CLI work, SSH should use key-based authentication without a password prompt.
If the alias or SSH key is missing, do not invent credentials. Tell the operator what setup is missing.

## Server-side development
A server Git checkout is a normal development workspace.
Recommended path:
```text
/home/growhelper/src/grow-helper-team-in-hermes
```
It is valid to fetch/pull, create task branches, edit, test, commit, push and deploy directly from that checkout.
The local checkout and server checkout are equal development environments. GitHub remains the canonical persistent source of truth.
Before starting in an existing checkout:
```bash
git status
git remote -v
git branch --show-current
git fetch
```
Do not overwrite uncommitted work.

## Production boundary
The server hosts unrelated production services.
Allowed without separate approval:
- SSH as `growhelper`;
- inspect GrowHelper files, logs, processes and service status;
- inspect Plant/Kanban state for debugging;
- edit GrowHelper code in the server Git checkout;
- edit GrowHelper runtime files under `/home/growhelper` when required;
- run tests;
- commit and push a development branch;
- deploy tested GrowHelper code;
- restart only `hermes-gateway-grow-helper.service` and `growhelper-dashboard.service`;
- run GrowHelper doctor and smoke tests.
Require explicit operator approval before:
- modifying `/root/.hermes`;
- modifying unrelated services/apps;
- changing owner/group/permissions under `/usr/local/lib/hermes-agent`;
- upgrading or patching Hermes core;
- SELinux or firewall changes;
- rebooting;
- arbitrary root-level system administration;
- destructive data migration;
- force-push;
- merging into `main`.

## Git workflow
Use a task branch for non-trivial work.
Do not merge into `main` or force-push without explicit approval.
Before finishing:
- run `git status`;
- remove accidental generated files;
- report intentional uncommitted changes;
- identify the deployed commit if production changed.
GitHub is the final source of truth regardless of where development happened.

## Deploy
`install-team.py` is designed as an idempotent updater and must not delete Plant workspaces, API credentials, sessions or Kanban boards.
Before deploy, read relevant production notes in `deploy_history.md`.
Do not blindly overwrite production systemd configuration.
Normal code deploy should prefer:
```bash
GROWHELPER_HERMES_BIN=/usr/local/bin/hermes /usr/local/lib/hermes-agent/venv/bin/python scripts/install-team.py   --data-root /home/growhelper/grow-helper   --timezone Asia/Dushanbe   --skip-systemd-unit
```
Restart only affected GrowHelper services.
If development started as a runtime hotfix, make Git and live runtime converge before declaring the task done.

## Dashboard production quirk
Do not run `npm install` inside `/usr/local/lib/hermes-agent` as part of GrowHelper development or deploy.
The shared Hermes installation is root-owned.
GrowHelper Dashboard uses:
```text
HERMES_WEB_DIST=/usr/local/lib/hermes-agent/hermes_cli/web_dist
```
Production Dashboard currently listens on `0.0.0.0:9119` and uses Hermes Basic Auth.
Preserve the existing systemd drop-in unless the task explicitly changes deployment design.

## Service operations
When logged in as `growhelper`:
```bash
systemctl --user status hermes-gateway-grow-helper.service
systemctl --user status growhelper-dashboard.service
systemctl --user restart hermes-gateway-grow-helper.service
systemctl --user restart growhelper-dashboard.service
```
If the user bus is unavailable, use the runtime-dir/DBus pattern from `deploy_history.md`.
Do not move GrowHelper back to a system-wide gateway service.

## Production verification
After a relevant deploy:
```bash
/usr/local/lib/hermes-agent/venv/bin/python scripts/doctor.py
```
Also check affected service status and recent logs.
Dashboard smoke test:
```bash
curl -fsS http://127.0.0.1:9119/api/status >/dev/null
```
Do not claim Telegram E2E success unless a real Telegram interaction was observed.
Do not create a real Plant/Campaign only for a smoke test without explicit permission.

## Secrets
Never commit `.env`, Telegram tokens, OAuth credentials, Dashboard credentials, SSH private keys or API keys.
Prefer checking whether a credential exists rather than printing its value.

## Documentation policy
Update `deploy_history.md` when production meaningfully changes: topology, paths, services, model/provider setup, persistent runtime config, deployment procedure, or an important operational workaround.
Do not update it for every ordinary code edit.
Update `docs/BRIEF_v2.md` only when the intended architecture/product contract changes.
