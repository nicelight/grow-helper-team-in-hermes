# GrowHelper Plant — Hermes Kanban handoff

## 1. Цель и принципы

Создать разворачиваемый **GrowHelper team bundle**, который помогает пользователю вести Plant от исходного состояния до согласованного результата выращивания.

Для каждого Plant создаются отдельная долговременная Hermes Kanban board, постоянный workspace и история наблюдений, измерений, решений, фото и результатов действий. Пользователь работает только через Telegram и общается с GrowHelper (`grow-helper` — внутренний Profile id). Администратор использует отдельную вкладку GrowHelper внутри `hermes dashboard`.

Ограничения проекта:

- Hermes не форкается; используются Profiles, Kanban, hooks и Web Dashboard plugins;
- допустимы небольшие scripts и один собственный plugin;
- отдельный Agro Intellect backend не создаётся;
- решение остаётся KISS: без PostgreSQL domain model, event bus, enterprise-RBAC и лишних LLM-вызовов;
- администратор наблюдает за процессом, но не управляет действиями пользователя. Он может только отправить рекомендацию через того же Telegram-бота от имени GrowHelper;
- GrowHelper runtime работает от отдельного непривилегированного Linux-пользователя `growhelper`; его Hermes root — `/home/growhelper/.hermes`;
- общая установка Hermes может находиться в `/usr/local/lib/hermes-agent`, но другой Hermes root (например `/root/.hermes`) не является частью GrowHelper и не должен быть доступен его Dashboard или агентам.

Итоговая формула:

```text
GrowHelper Profiles + Hermes Kanban + Plant files + один monitor plugin
```

---

## 2. Архитектура

```text
Telegram user
    ↓
GrowHelper gateway session
    ├─ определяет Plant и тип события
    ├─ сохраняет входное сообщение
    └─ запускает Cycle на явной Plant board
            ↓
GrowHelper Kanban Orchestrator
    ├─ evidence tasks
    ├─ hypothesis/advice tasks
    ├─ optional review
    └─ final synthesis
            ↓
 growhelper_publish_reply
    ├─ отправляет ответ через Telegram
    └─ сохраняет точный отправленный текст

Trusted admin → hermes dashboard (user `growhelper`) → GrowHelper tab
```

Deployment boundary:

```text
/usr/local/lib/hermes-agent       → общий код Hermes
/root/.hermes                     → отдельный Hermes root, вне GrowHelper
/home/growhelper/.hermes          → GrowHelper Profiles, sessions, Kanban, plugins
/home/growhelper/grow-helper/     → Plant registry и workspaces
```

GrowHelper Dashboard запускается от `growhelper` и управляет только его Hermes root. Его нельзя запускать от `root` или с `HERMES_HOME=/root/.hermes`.

Один общий roster обслуживает все Plants:

```text
Profile       = постоянная профессиональная роль
Plant board   = task graph конкретного Plant
Workspace     = долговременные данные Plant
Telegram user = владелец одного или нескольких Plants
```

Profiles не создаются заново для каждого пользователя.

---

## 3. Handoff по механизмам Hermes

### Profiles

Profile имеет собственные config, SOUL, sessions, memory, skills и runtime state. В GrowHelper Profiles обозначают роли и находятся внутри одного GrowHelper Hermes root: `/home/growhelper/.hermes`. Данные конкретного Plant не сохраняются в общей memory specialist Profile: их каноническое место — Plant workspace.

Hermes root и Linux user — важная внешняя граница: GrowHelper процессы работают как `growhelper`, поэтому не должны иметь доступа к `/root/.hermes` и другим root-only данным. Profile сам по себе не является filesystem sandbox. Для первой версии достаточно этой OS-level границы, узкого toolset и SOUL-контракта; контейнерную изоляцию добавлять только при подтверждённой необходимости.

### Kanban

Hermes Kanban — долговременная SQLite-backed очередь задач:

- каждая board имеет отдельную DB;
- tasks содержат assignee, status, body, links и runs;
- parent → child links образуют зависимости;
- child становится `ready` после завершения обязательных parents;
- gateway dispatcher запускает assignee Profile отдельным worker process;
- worker получает task-scoped `kanban_*` tools;
- Orchestrator с включённым `kanban` toolset создаёт и связывает tasks;
- `kanban_complete(summary, metadata)` сохраняет handoff, который получает следующая task;
- idempotency key защищает от дублей после retry.

Kanban DB — источник истины о том, кто работал, что вернул и где произошли retries, errors или blocks. Полный mirror Kanban в собственных файлах не создаётся.

`delegate_task` остаётся коротким вложенным вызовом без долговременного графа. Specialist может использовать его максимум один раз и только при реальной пользе.

### Gateway, hooks и sessions

Plugin hooks используются, чтобы связать Telegram и Plant Cycle:

- `pre_llm_call` даёт сообщение, реально переданное GrowHelper;
- `post_llm_call` даёт ответ GrowHelper;
- Kanban hooks сообщают о completion/block;
- session/task identifiers позволяют связать диалог, board и worker runs.

Логируется текущий обмен, а не вся conversation history на каждом turn.

### Web Dashboard plugin

GrowHelper добавляется отдельной вкладкой, не заменяя штатные страницы. Plugin использует Dashboard SDK, собственные FastAPI routes, Plant files, Kanban data и штатную Sessions page. Dashboard является machine-level surface только внутри текущего Hermes root: запущенный как `growhelper`, он видит GrowHelper Profiles, но не `/root/.hermes`. Доступ к полному Hermes Dashboard получают только доверенные технические администраторы. Hermes-specific доступ изолируется в маленьком `hermes_adapter.py`, чтобы обновления Hermes затрагивали один слой.

---

## 4. Roster и team bundle

Создать семь Profiles:

| Profile | Ответственность |
|---|---|
| `grow-helper` | routing, минимальный task graph, synthesis, Plant files, публичный ответ |
| `vision-observation` | только наблюдаемые визуальные факты |
| `plant-state` | нормализованное состояние, изменения и тренды |
| `cultivation-advisor` | гипотезы, evidence за/против, стратегия и действия |
| `task-followup` | сложные проверки, измерения и сроки |
| `data-curator` | reusable evidence, `candidate`/`validated`, только `dataset/` |
| `reviewer` | независимая проверка противоречивых или рискованных выводов |

Общий specialist contract:

```text
Work only inside your assigned competence.
Be useful but concise.
Do not create persistent Kanban tasks.
Treat Plant files as read-only unless your role owns a subtree.
Return the handoff through kanban_complete(summary, metadata).
If there is no material contribution, answer exactly: no comments
```

Team bundle:

```text
grow-helper-team/
├── profiles/<seven profiles>/
├── plugin/grow-helper-monitor/
│   ├── growhelper_monitor/
│   │   ├── plugin.py
│   │   ├── {runtime_context,gateway,permissions,commands,tools}.py
│   │   └── {core,hermes_adapter,telegram_client,validation}.py
│   └── dashboard/{manifest.json, plugin_api.py, dist/}
├── templates/
│   ├── campaign.md
│   ├── baseline.md
│   ├── current-state.md
│   ├── history-summary.md
│   └── journal-entry.md
└── scripts/{install-team.py,new-plant.py}
```

`install-team.py` устанавливает и проверяет Profiles, plugin и config. `new-plant.py` выполняет только детерминированную механику: безопасный `plant_id`, workspace, registry entry и board. Агрономические решения остаются за агентами.

---

## 5. Multi-user routing, Inception и workspace

В Telegram агент публично является только GrowHelper: дневником наблюдений и экспертным помощником по выращиванию. Он не представляется Hermes или универсальным агентом. Рабочая область ограничена активным Plant, созданием/выбором Plants и объяснением GrowHelper; явно посторонний запрос получает короткое перенаправление к ChatGPT или Claude. Запрос изменить функциональность пересылается через узкий tool первому numeric ID из `GROWHELPER_TELEGRAM_ADMIN_USERS`, без возможности выбрать другого получателя.

Минимальный registry:

```text
/home/growhelper/grow-helper/plants/index.json
```

В командах от пользователя `growhelper` этот путь можно сокращать до `~/grow-helper/plants/index.json`.

Для Plant хранить `plant_id`, nickname, Telegram user/chat identifiers, необязательное company name, board slug, workspace path, вид/сорт, Campaign status, onboarding stage, avatar path и active Cycle. Binding также может хранить короткие `pending_addplant` до получения аватара и `pending_delplant` до подтверждения удаления. Старые записи без новых полей считаются `active/complete` без аватара. Изменения registry выполняются атомарно под file lock. Отдельная company model пока не нужна.

Routing:

```text
явно существующий Plant → использовать его board
явно другой Plant       → предложить `/plant` или `/addplant`
неоднозначно            → один короткий вопрос
```

Новый Plant и его board создаются детерминированно после первой валидной фотографии в `/addplant`, со статусом `onboarding`. Server-side операции всегда указывают board явно; глобальный `boards switch` не используется. Dispatcher worker уже получает board через `HERMES_KANBAN_BOARD`.

При создании Plant сразу получает первое глобально свободное имя из `plantNamesDefault.md`. Следующее обычное сообщение может переименовать Plant; иначе предложенное имя остаётся и повторно не запрашивается.

### Inception

`/addplant` сначала требует аватар. До валидного изображения обсуждение нового Plant не начинается. Изображение приводится к JPEG, сжимается до `500 000` байт и сохраняется только как `photos/avatar.jpg`; тяжёлый оригинал аватара в Plant workspace не копируется.

После создания Plant и предложения имени GrowHelper выясняет:

- что выращивается и каково исходное состояние;
- среду и доступные инструменты;
- желаемый результат и наблюдаемые критерии успеха;
- ограничения пользователя.

Сначала формируется Campaign draft, а не стратегия выращивания. После явного подтверждения Plant переводится из `onboarding` в `active`; `campaign.md` и baseline уточняются в его уже существующем workspace. Baseline может быть `complete` или `partial`; неизвестные данные не блокируют Campaign.

Если внутри Plant от 1 до 6 отдельно отслеживаемых растишек, GrowHelper предлагает им короткие описательные имена со стабильными номерами слева направо. Источник порядка по умолчанию — описание пользователя; обзорное фото используется только по его явной просьбе. После явного подтверждения список хранится в `baseline.md` в секции `Растишки слева направо`; неизвестные признаки не додумываются, номера автоматически не меняются. При 7 и больше используются зоны или группы без отдельных workspace или domain-сущностей.

### Workspace

```text
<plant_id>/
├── campaign.md
├── baseline.md
├── current-state.md
├── history-summary.md
├── activity.jsonl
├── journal/
├── photos/avatar.jpg
└── dataset/{index.jsonl,selected/}
```

Источники истины:

```text
Kanban DB          → tasks, dependencies, runs, specialist handoffs
activity.jsonl     → точный публичный диалог и delivery
current-state.md   → состояние Plant сейчас
history-summary.md → ключевая долговременная траектория
journal/           → подробный domain worklog
```

Отдельный каталог `rounds/` не нужен: он дублировал бы Kanban. Канонические campaign/baseline/current-state/history/journal изменяет GrowHelper, `dataset/` — только Curator; остальные specialists работают read-only. Диагностические фото хранятся в `photos/`; для аватара сохраняется только сжатая копия. Kanban attachments используются лишь для передачи файлов workers.

---

## 6. Telegram event → Cycle → reply

Публичное Telegram-меню содержит ровно `/addplant`, `/plant`, `/delplant`, `/feedback`, `/compress`, `/new`, `/status`, `/context`. Оно настраивается штатным `command_menu` Hermes с `priority_mode: replace` и лимитом 8; Hermes core не патчится. `/plant` показывает одноразовую reply-клавиатуру только с Plants владельца и после выбора присылает аватар. `/delplant` показывает только Plants владельца и после явного подтверждения удаляет запись registry, workspace и Kanban board. `/feedback` отвечает точно `Не стесняйтесь написать разработчику — @dyingseed`, ничего не пересылает и не меняет состояние. `/help` и `/whoami` могут оставаться доступны при ручном вводе как обязательный floor Hermes, но в меню не показываются.

Приветствия, подтверждения, onboarding и простые вопросы GrowHelper обрабатывает непосредственно, без Kanban. `pre_gateway_dispatch` только захватывает Telegram event и переписывает ответы одноразовой клавиатуры или pending-avatar в plugin-команды; до штатной авторизации он ничего не записывает и не отправляет.

Для значимого события:

1. `pre_llm_call` hook удерживает LLM-visible сообщение и session/message ids в request-scoped context и добавляет только четыре компактных файла активного Plant.
2. GrowHelper определяет Plant и тип события.
3. Plugin tool `growhelper_start_cycle` сохраняет captured event в `activity.jsonl` нужного Plant и создаёт root task на явной board с idempotency key на основе Telegram message id.
4. После успешного создания нового Cycle gateway сразу и без перефразирования отправляет текст из поля `acknowledgement` результата tool с nickname Plant; duplicate или ошибка создания не порождают новое подтверждение.
5. Orchestrator строит только релевантный workflow.
6. Final synthesis вызывает `growhelper_publish_reply(cycle_id, text)`.
7. Tool отправляет точный текст через того же Telegram-бота и добавляет `growhelper_reply` с delivery status.
8. После публикации synthesis task завершается.

`growhelper_publish_reply` идемпотентен по `cycle_id`: retry не отправляет ответ повторно.

Внутренние Kanban notifications пользователю отключаются:

```yaml
kanban:
  dispatch_in_gateway: true
  auto_decompose: false
  auto_subscribe_on_create: false
```

Минимальная запись `activity.jsonl`:

```json
{
  "timestamp": "2026-08-19T16:10:00+05:00",
  "kind": "operator_message|growhelper_reply|admin_recommendation",
  "plant_id": "plt_7f3k9m",
  "cycle_id": "t_abcd1234",
  "session_id": "...",
  "message_id": "...",
  "text": "...",
  "media": ["photos/2026-08-19/leaf-01.jpg"],
  "delivery": "sent|failed|unknown"
}
```

Это append-only связка Telegram ↔ Kanban, а не новая message bus.

На одной Plant board выполняется один активный Cycle. Новое событие присоединяется к тому же вопросу либо ждёт завершения текущего Cycle.

---

## 7. KISS workflow по типу события

### Фото

Использовать evidence-first pipeline:

```text
vision-observation
        ↓
plant-state
        ↓
cultivation-advisor — только если нужна гипотеза или действие
        ↓
reviewer — только при существенной неопределённости
        ↓
GrowHelper final synthesis
```

`plant-state` зависит от `vision-observation` и получает его визуальные факты вместе с текстом, измерениями и историей. Отдельная observation-synthesis task не добавляется: её функцию выполняет `plant-state`. Это обеспечивает зависимость от vision и экономит один LLM-вызов.

Если фото требует только фиксации состояния, Cycle может закончиться после `plant-state`.

### Только pH, EC, температура и другие понятные измерения

```text
plant-state ─────────┐
                     ├─→ GrowHelper synthesis
cultivation-advisor ─┘
```

Они могут работать параллельно, потому что исходные факты уже структурированы.

### Текстовый симптом без фото

```text
plant-state → cultivation-advisor при необходимости → GrowHelper
```

### Результат предыдущего действия

GrowHelper сопоставляет ожидаемый и фактический outcome. Если evidence достаточно, вызывается `data-curator` для validation candidate record.

### Дополнительные роли

`reviewer` вызывается при contradictions, низком confidence перед существенным действием, трудно обратимом вмешательстве или смене основной стратегии.

`task-followup` нужен только для нескольких связанных действий/измерений/сроков. Простой следующий шаг формулирует GrowHelper.

`kanban swarm` не используется как стандартный route: он автоматически добавляет verifier и увеличивает inference.

---

## 8. Observation, inference и recommendation

В specialist completion обязательны три top-level массива, даже если часть пуста:

```json
{
  "schema_version": "growhelper.v1",
  "round_id": "R1",
  "verdict": "comment|no_comments|needs_data",
  "observation": [
    {
      "id": "obs-1",
      "text": "Межжилковое осветление сильнее на старых листьях по краям.",
      "source": "photo:photos/2026-08-19/leaf-01.jpg",
      "timestamp": "2026-08-19T15:55:00+05:00",
      "confidence": "medium",
      "missing_data": ["нижняя сторона листа", "корни"]
    }
  ],
  "inference": [
    {
      "id": "inf-1",
      "text": "Симптом совместим с нарушением доступности магния, но причина не подтверждена.",
      "confidence": "low",
      "evidence_for": ["obs-1"],
      "evidence_against": [],
      "missing_data": ["pH", "EC"]
    }
  ],
  "recommendation": [
    {
      "id": "rec-1",
      "text": "Сначала измерить pH и EC, не меняя состав раствора.",
      "based_on": ["inf-1"],
      "urgency": "soon",
      "reversibility": "easy",
      "confidence": "high"
    }
  ],
  "confidence": "medium",
  "missing_data": []
}
```

Для измерения в observation добавляются `value`, `unit` и, если известно, `instrument`.

Role boundaries:

- `vision-observation` заполняет только observation;
- `plant-state` — observation и некаузальные state/trend inferences;
- `cultivation-advisor` — causal hypotheses и recommendations с evidence links;
- `reviewer` отмечает contradictions и unsupported claims;
- GrowHelper не выдаёт гипотезу как установленный факт.

Корректный vision output:

> Наблюдается межжилковое осветление на старых листьях. Нижняя сторона листьев и корни не видны.

Некорректный vision output:

> Это дефицит магния.

Plugin выполняет лёгкую schema validation и показывает warning в Dashboard. В MVP нарушение схемы не блокирует весь Cycle.

---

## 9. Wait-all, recovery и idempotency

Зависимости реализуются обычными Kanban parent links. Предметная неопределённость не является block: specialist завершает task с `verdict=needs_data`.

Workers получают ограниченные runtime/retries. Если task после retries остаётся blocked, администратор видит техническую причину в Dashboard и использует штатный Kanban recovery. Отдельный partial-consensus controller не создаётся.

Каждая task получает key:

```text
<cycle_id>:<phase_id>:<role>
```

После restart повторное построение графа не создаёт дубликаты.

---

## 10. Dataset: candidate → validated

Полезное observation, hypothesis или action сначала сохраняется как `candidate`. Это материал для проверки, а не подтверждённое знание.

После follow-up Curator переводит запись в `validated`, только если появился наблюдаемый outcome. Сохраняются исходные evidence/action, ожидаемый и фактический outcome, `supported | not_supported | mixed`, source references и Cycle ids.

Отрицательный результат не удаляется: он становится validated с `not_supported`. При недостаточном follow-up запись остаётся candidate. Candidate records не используются автоматически как надёжный precedent для других Plants.

`data-curator` вызывается после значимого evidence/action/outcome, а не после каждого сообщения.

---

## 11. Компактная долговременная история

Добавить `history-summary.md` с ключевой траекторией Campaign:

- turning points и смены стадии/стратегии;
- подтверждённые и опровергнутые гипотезы;
- устойчивые реакции Plant;
- повторяющиеся отклонения в действиях пользователя;
- нерешённые долгосрочные риски.

GrowHelper обновляет его только после значимого Cycle. Отдельный summarizer Profile или периодический LLM job не создаётся. Summary регулярно переписывается компактно; детали остаются в journal, dataset и Kanban.

Для нового Cycle обычно читаются `campaign.md`, `baseline.md`, `current-state.md`, `history-summary.md` и релевантные недавние journal entries. Полная старая история поднимается только при необходимости.

---

## 12. GrowHelper Web Dashboard

Штатный Kanban остаётся техническим debug-инструментом. GrowHelper tab показывает процесс в терминах выращивания.

### Plants list

Показывать nickname, вид/сорт, company, стадию, краткое состояние, последнее сообщение, активный Cycle, текущий шаг, ожидание пользователя и blocked/failed-delivery indicators.

### Plant page

1. **Overview** — Campaign Goal, baseline, current state, next expected observation.
2. **Workflow** — понятный граф текущего и прошлых Cycles.
3. **Conversation** — точные сообщения пользователя, GrowHelper и администратора.
4. **Evidence** — photos, measurements, hypotheses, candidate/validated records.
5. **History** — journal и `history-summary.md`.

Workflow:

```text
Сообщение / фото пользователя
          ↓
Visual facts                [vision-observation]
          ↓
Normalized Plant state      [plant-state]
          ↓
Hypothesis / recommendation [cultivation-advisor]
          ↓
Optional check              [reviewer]
          ↓
Ответ GrowHelper
          ↓
Follow-up / observed outcome
```

Каждый узел показывает status, duration, точный `summary`, structured metadata, confidence, evidence, missing data, retries/errors и schema warnings.

Именно `summary + metadata` считаются ответом specialist: это тот handoff, который получает зависимая synthesis task.

**Debug details** раскрывает task/run ids, parent/child links, raw body/metadata/events, model/Profile и `worker_session_id`. По нему открывается штатная Sessions page с видимым worker transcript и tool calls. Скрытый chain-of-thought не сохраняется и не показывается.

Conversation строится из `activity.jsonl` и показывает input, media, промежуточный GrowHelper reply, реально опубликованный final reply, delivery status, admin recommendation и ссылку на Cycle.

Единственная domain write-функция администратора:

```text
Отправить рекомендацию от имени GrowHelper
```

Сообщение отправляется через тот же Telegram bot и фиксируется как `admin_recommendation`. Оно не меняет action status, current state или Kanban graph.

Dashboard plugin не копирует Kanban в свою DB, использует простой polling и оставляет ссылку на штатный Kanban для низкоуровневых операций.

---

## 13. Ответственность monitor plugin

Plugin выполняет только glue-функции:

1. выполнить `/addplant`, `/plant`, подтверждаемое `/delplant` и статическое `/feedback`, включая сжатый аватар и active binding;
2. связать Telegram message/session с Plant и Cycle;
3. вести `activity.jsonl`;
4. создать root Cycle через `growhelper_start_cycle`;
5. идемпотентно опубликовать ответ через `growhelper_publish_reply`;
6. предоставить read API для Dashboard;
7. отправить `admin_recommendation` или фиксированному владельцу запрос на изменение GrowHelper;
8. проверить metadata schema и показать warning.

Plugin не принимает агрономические решения, не строит собственный scheduler, не дублирует Kanban и не управляет действиями пользователя.

---

## 14. KISS-ограничения

- Не запускать specialists для бытового диалога.
- Не вызывать reviewer, task-followup и data-curator в каждом Cycle.
- Не создавать отдельную observation-synthesis task после vision.
- Не передавать полный journal в каждый prompt.
- Не сохранять hidden reasoning; для debug достаточно messages, tool calls, graph и structured handoffs.
- Не использовать Swarm как default.
- Не добавлять telemetry или управление оборудованием.
- Не использовать GrowHelper Dashboard как multi-tenant пользовательский интерфейс: конечные пользователи работают только через Telegram, а полный Dashboard предназначен для доверенных администраторов.

Диагностический путь:

```text
GrowHelper workflow → raw Kanban task/run → Hermes worker session
```

---

## 15. Минимальный acceptance test

1. Меню показывает восемь согласованных команд; `/feedback` возвращает контакт разработчика, а `/addplant` создаёт onboarding Plant только после валидного аватара не более 500 КБ.
2. `/plant` показывает только Plants владельца, переключает binding и возвращает аватар; `/delplant` удаляет только выбранный Plant владельца и только после подтверждения.
3. Два Telegram-пользователя одновременно создают Cycles только на своих boards; routing не зависит от current board.
4. Photo Cycle идёт `vision → plant-state → advisor`; vision не пишет diagnosis/recommendation.
5. Measurement-only Cycle допускает параллельные state/advisor.
6. Dashboard показывает точный user message, GrowHelper reply, связь с Cycle и specialist `summary + metadata`.
7. Retry не создаёт duplicate tasks и не отправляет reply повторно.
8. Candidate не становится validated без outcome; `history-summary.md` хранит turning points, не копируя journal.
9. Admin recommendation отправляется через Telegram и отображается отдельно.
10. Пользователь не получает внутренних Kanban completion messages.
11. После restart доступны board, activity, Plant files и незавершённый Cycle.
12. GrowHelper Dashboard и агенты, запущенные от `growhelper`, не могут читать `/root/.hermes`.
13. Для 1–6 растишек GrowHelper только после подтверждения сохраняет в `baseline.md` стабильные имена и источник порядка; фото допустимо только по явной просьбе пользователя.

---

## 16. Что намеренно не реализуем

- fork или patch Hermes core;
- PostgreSQL domain backend;
- Agent Chat Bus, MessageEnvelope и отдельный event bus;
- enterprise permissions и сложный consensus controller;
- verifier на каждый Cycle и отдельный LLM summarizer;
- real-time telemetry и автоматическое управление оборудованием;
- action-management пользователя из admin Dashboard.

---

## 17. References

- Profiles: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/profiles.md
- Kanban: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md
- Event hooks: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md
- Web Dashboard: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md
- Extending Web Dashboard: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/extending-the-dashboard.md
