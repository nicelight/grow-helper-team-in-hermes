# GrowHelper — deploy_history.md

## 1. Цель проекта

GrowHelper — multi-user Telegram-ассистент по выращиванию растений поверх **NousResearch Hermes Agent**, без форка ядра Hermes.

Основные принципы:
- конечный пользователь работает только через Telegram;
- администратор наблюдает процессы через Hermes Web Dashboard;
- один долгоживущий Plant/Campaign = один постоянный workspace + Kanban board;
- основной профиль `grow-helper` оркестрирует specialist profiles;
- KISS: без отдельного PostgreSQL/backend, без лишней автоматизации и без дополнительного LLM-шумогенератора;
- Hermes core не модифицируем: только Profiles + plugin + штатный Kanban/Dashboard;
- GrowHelper полностью изолирован от уже существующего root-Hermes отдельным Linux-пользователем `growhelper`.

---

## 2. Сервер

- OS: AlmaLinux 9
- Server IP: `108.181.252.78`
- Hermes Agent: `v0.20.4 (2026.8.18)`
- Hermes install: `/usr/local/lib/hermes-agent`
- Python Hermes venv: `/usr/local/lib/hermes-agent/venv`
- Python: `3.11.16`
- Node: `v22.22.2`
- npm: `10.9.7`
- GrowHelper Linux user: `growhelper`
- UID: `1003`
- GrowHelper home: `/home/growhelper`
- Hermes home для GrowHelper: `/home/growhelper/.hermes`
- GrowHelper data root: `/home/growhelper/grow-helper`
- Bundle: `/home/growhelper/apps/grow-helper-team-0.1.0`

Важно: на сервере уже есть отдельный production Hermes от `root`. Его конфигурация находится в `/root/.hermes`. GrowHelper не должен его трогать.

---

## 3. Схема запуска Hermes

Старый root-Hermes ранее был переведён с system-wide systemd на штатный **user-systemd**, чтобы избежать проблем SELinux.

Для GrowHelper используется тот же подход:
- никакого system-wide `hermes-gateway.service`;
- gateway и dashboard запускаются как user services пользователя `growhelper`;
- `loginctl enable-linger growhelper` уже сделан;
- SELinux не трогать без необходимости.

При управлении user-systemd из root используются:

```bash
GH_UID=$(id -u growhelper)

sudo -u growhelper env \
  XDG_RUNTIME_DIR=/run/user/$GH_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$GH_UID/bus \
  systemctl --user ...
```

---

## 4. Hermes Profiles

Созданы и настроены 7 Profiles:

1. `grow-helper`
2. `vision-observation`
3. `plant-state`
4. `cultivation-advisor`
5. `task-followup`
6. `data-curator`
7. `reviewer`

Все работают на:

```text
provider: openai-codex
model: gpt-5.5
terminal backend: local
```

Модель и OAuth были сначала настроены интерактивно для `grow-helper`, после чего остальные 6 профилей были пересозданы через:

```bash
hermes profile create <profile> --clone-from grow-helper --clone-all
```

После этого повторно был применён GrowHelper bundle, чтобы вернуть каждому профилю его собственный `SOUL.md` и ограниченный toolset.

### Toolsets

```text
grow-helper
  file, web, clarify, kanban, growhelper

vision-observation
  file, vision, delegation

plant-state
  file, web, delegation

cultivation-advisor
  file, web, delegation

task-followup
  file

data-curator
  file

reviewer
  file, web, delegation
```

Memory/User profile у specialist profiles выключены.

---

## 5. Роли specialist profiles

### `grow-helper`
Главный пользовательский агент и оркестратор. Единственный публичный GrowHelper identity.

### `vision-observation`
Только наблюдаемые визуальные факты по фото, без диагностики.

### `plant-state`
Нормализует текущее состояние растения, изменения и тренды, но не делает необоснованную причинность.

### `cultivation-advisor`
Формирует агрономические гипотезы и обратимые рекомендации.

### `task-followup`
Превращает выводы в конкретные проверки, измерения и сроки.

### `data-curator`
Поддерживает reusable evidence/dataset: `candidate` и `validated`.

### `reviewer`
Независимо проверяет противоречия, слабые выводы и рискованные рекомендации.

---

## 6. Workflow

Основной photo-flow:

```text
Telegram user
  ↓
grow-helper
  ↓
vision-observation
  ↓
plant-state
  ↓
cultivation-advisor
  ↓
optional reviewer
  ↓
grow-helper
  ↓
Telegram reply
```

Measurement-only flow допускает параллель:

```text
plant-state ─┐
             ├─→ synthesis by grow-helper
advisor ─────┘
```

Принцип: **observation → inference → recommendation** не смешиваются.

Specialists должны завершать работу через `kanban_complete(summary=..., metadata=...)`.

Kanban:
- `dispatch_in_gateway: true`
- `auto_decompose: false`
- `auto_subscribe_on_create: false`
- `orchestrator_profile: grow-helper`

---

## 7. GrowHelper plugin

Plugin:

```text
grow-helper-monitor
version: 0.1.0
```

Machine-level plugin:

```text
/home/growhelper/.hermes/plugins/grow-helper-monitor/
```

Profile-local copies также присутствуют в Profiles.

Основные tools:
- `growhelper_plants`
- `growhelper_start_cycle`
- `growhelper_publish_reply`

Hooks:
- `pre_tool_call`
- `pre_llm_call`
- `post_llm_call`

Plugin discovery и enablement проверены.

`doctor.py` видит регистрацию:

```text
tools=[growhelper_plants, growhelper_start_cycle, growhelper_publish_reply]
```

---

## 8. GrowHelper workspace / Plant storage

Базовая структура:

```text
/home/growhelper/grow-helper/
  plants/
    index.json
```

Для каждого Plant/Campaign предполагается workspace:

```text
campaign.md
baseline.md
current-state.md
history-summary.md
activity.jsonl
journal/
photos/
dataset/
  index.jsonl
```

`activity.jsonl` должен хранить:
- точный текст сообщения пользователя;
- точный опубликованный GrowHelper reply;
- admin recommendation;
- Cycle correlation/id.

`history-summary.md` — только существенные turning points, не полный лог.

Dataset:
- `candidate`
- `validated`

Гипотеза становится validated только после follow-up outcome:
- `supported`
- `not_supported`
- `mixed`

---

## 9. Telegram

Используется **отдельный Telegram bot** для GrowHelper.

Токен хранится только у профиля:

```text
/home/growhelper/.hermes/profiles/grow-helper/.env
```

Specialist profiles не содержат messaging credentials — это проверено doctor-скриптом.

### Access policy

Сейчас разрешён доступ всем Telegram users:

```text
TELEGRAM_ALLOW_ALL_USERS=true
```

Идея trial-mode (первые ~30 сообщений, потом admin approval) обсуждалась, но **пока НЕ реализована**.

Admin Telegram user уже настроен в bundle/config.

Telegram live smoke test успешен: пользователь написал новому боту, бот ответил, что он GrowHelper и будет помогать с растениями.

---

## 10. GrowHelper Gateway

User-systemd service:

```text
hermes-gateway-grow-helper.service
```

Unit:

```text
/home/growhelper/.config/systemd/user/hermes-gateway-grow-helper.service
```

Последний подтверждённый status:

```text
Active: active (running)
Status: "Hermes Gateway running"
```

Процесс:

```text
/usr/local/lib/hermes-agent/venv/bin/python \
  -m hermes_cli.main \
  --profile grow-helper \
  gateway run
```

Gateway успешно подключается к Telegram.

### Gateway log notes

В foreground smoke test встречались warnings:

```text
check_bfl_requirements returned False
check_browser_requirements returned False
check_browser_vision_requirements returned False
check_web_api_key returned False
```

Browser сейчас не нужен.

Но `check_web_api_key returned False` означает, что web-search capability для профилей с `web` надо отдельно проверить/настроить позже.

---

## 11. Hermes Web Dashboard

User-systemd service:

```text
growhelper-dashboard.service
```

Unit:

```text
/home/growhelper/.config/systemd/user/growhelper-dashboard.service
```

Drop-in:

```text
/home/growhelper/.config/systemd/user/growhelper-dashboard.service.d/override.conf
```

### Важный deployment fix

Изначально dashboard падал на первом запуске, потому что пытался выполнить:

```bash
npm install --workspace web
```

от пользователя `growhelper` внутри root-owned:

```text
/usr/local/lib/hermes-agent
```

и получал `EACCES: permission denied`.

Никакие ownership/permissions системного Hermes не менялись.

Использован штатный готовый frontend:

```text
/usr/local/lib/hermes-agent/hermes_cli/web_dist
```

Он существует, `index.html` найден, размер dist около `3.1M`.

В systemd override добавлено:

```ini
Environment=HERMES_WEB_DIST=/usr/local/lib/hermes-agent/hermes_cli/web_dist
```

Это предотвращает любые `npm install`/rebuild при старте GrowHelper Dashboard.

---

## 12. Dashboard public access

Dashboard сейчас слушает:

```text
0.0.0.0:9119
```

Подтверждено через `ss`.

firewalld:

```text
zone: public
port: 9119/tcp
```

Публичный URL:

```text
http://108.181.252.78:9119
```

Dashboard login page успешно открывается извне.

### Dashboard auth

Используется bundled Hermes Basic Auth provider.

Environment file:

```text
/home/growhelper/.config/growhelper-dashboard.env
```

Username:

```text
admin
```

Пароль намеренно не дублируется в этом handoff-файле; он уже известен оператору и хранится в указанном env-файле.

Dashboard auth secret также хранится там.

Permissions:

```text
600
owner: growhelper
```

---

## 13. Dashboard systemd override — ожидаемая конфигурация

```ini
[Service]
Environment=HERMES_WEB_DIST=/usr/local/lib/hermes-agent/hermes_cli/web_dist
EnvironmentFile=/home/growhelper/.config/growhelper-dashboard.env
ExecStart=
ExecStart=/usr/local/lib/hermes-agent/venv/bin/hermes dashboard --host 0.0.0.0 --port 9119 --no-open
```

---

## 14. Doctor status

Использовать Hermes Python, не системный Python 3.9:

```bash
sudo -iu growhelper /usr/local/lib/hermes-agent/venv/bin/python \
  /home/growhelper/apps/grow-helper-team-0.1.0/scripts/doctor.py
```

Последний результат:

```text
Result: 0 error(s)
```

Основные OK:
- Python 3.11.16
- Hermes 0.20.4
- все 7 Profiles
- все SOUL.md
- правильные toolsets
- plugin enabled
- memory disabled где надо
- messaging isolation specialist profiles
- Kanban config
- admin split
- Telegram token configured
- dashboard manifest
- plugin tools/hooks registration

Оставшиеся WARN:
- `TELEGRAM_ALLOWED_USERS` не настроен — ожидаемо, потому что используется `TELEGRAM_ALLOW_ALL_USERS=true`;
- service status warnings в doctor возникали из-за отсутствующего user bus при запуске через `sudo -iu`, а реальные user-systemd services работают;
- внутренний plugin-doctor выдавал discovery warning при проверке bundle path, но установленный plugin реально обнаруживается и работает.

---

## 15. Release bundle

Исходный архив:

```text
grow-helper-team-0.1.0.tar.gz
```

SHA-256:

```text
b4ee391cd01a15dd6b459292baf0f117728894a384eee950cb1ebde8608818cd
```

На сервере:

```text
/home/growhelper/grow-helper-team-0.1.0.tar.gz
```

Распаковано:

```text
/home/growhelper/apps/grow-helper-team-0.1.0
```

В bundle есть `team.yaml`, profiles, plugin, templates, schemas, install/new-plant/doctor/backup scripts, dashboard assets/API, tests и deploy assets.

---

## 16. Что уже реально протестировано

```text
Hermes v0.20.4                     OK
GrowHelper Linux isolation        OK
7 Profiles                        OK
gpt-5.5 / openai-codex            OK
OAuth clone to specialists        OK
specialist toolsets               OK
plugin discovery                  OK
Telegram bot token                OK
Telegram inbound                  OK
GrowHelper response               OK
gateway user-systemd              OK
dashboard user-systemd            OK
dashboard web-dist workaround     OK
dashboard public :9119            OK
dashboard Basic Auth login page   OK
firewalld :9119                   OK
doctor                            0 errors
```

---

## 17. Что ещё НЕ проверено end-to-end

Главные следующие шаги:

1. Войти в Dashboard и убедиться, что вкладка/раздел GrowHelper plugin реально отображается.
2. Создать первый Plant/Campaign.
3. Прогнать реальный Cycle: Telegram user message → `growhelper_start_cycle` → Kanban tasks → specialist workers → `kanban_complete` → synthesis → `growhelper_publish_reply` → Telegram reply.
4. Проверить, что Dashboard показывает exact user message, exact GrowHelper reply, workflow, specialist outputs, worker session IDs/transcripts.
5. Проверить photo-flow.
6. Проверить сохранение `activity.jsonl`, `current-state.md`, `history-summary.md`, `dataset/index.jsonl`.
7. Проверить idempotency `growhelper_publish_reply`.
8. Проверить web search provider/API key для ролей с toolset `web`.
9. Проверить reboot survival обоих user services.

---

## 18. Что НЕ делать

```text
- не менять owner/group у /usr/local/lib/hermes-agent;
- не запускать npm install от root внутри Hermes;
- не возвращать system-wide hermes-gateway.service;
- не трогать /root/.hermes;
- не копировать GrowHelper Telegram token specialist profiles;
- не включать Hermes multiplex gateway без отдельного решения;
- не модифицировать Hermes core ради GrowHelper;
- не снимать существующую Linux-user isolation;
- не менять SELinux ради удобства.
```

---

## 19. Быстрые operational команды

### GrowHelper gateway status

```bash
GH_UID=$(id -u growhelper)
sudo -u growhelper env \
  XDG_RUNTIME_DIR=/run/user/$GH_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$GH_UID/bus \
  systemctl --user status hermes-gateway-grow-helper.service --no-pager -l
```

### Dashboard status

```bash
sudo -u growhelper env \
  XDG_RUNTIME_DIR=/run/user/$GH_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$GH_UID/bus \
  systemctl --user status growhelper-dashboard.service --no-pager -l
```

### Dashboard listen

```bash
ss -ltnp | grep ':9119'
```

### Firewall

```bash
firewall-cmd --zone=public --list-ports
```

### Profiles

```bash
sudo -iu growhelper hermes profile list
```

### Doctor

```bash
sudo -iu growhelper /usr/local/lib/hermes-agent/venv/bin/python \
  /home/growhelper/apps/grow-helper-team-0.1.0/scripts/doctor.py
```

### Gateway logs

```bash
sudo -u growhelper env \
  XDG_RUNTIME_DIR=/run/user/$GH_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$GH_UID/bus \
  journalctl --user -u hermes-gateway-grow-helper.service -n 100 --no-pager
```

### Dashboard logs

```bash
sudo -u growhelper env \
  XDG_RUNTIME_DIR=/run/user/$GH_UID \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$GH_UID/bus \
  journalctl --user -u growhelper-dashboard.service -n 100 --no-pager
```

---

## 20. Текущий checkpoint

```text
Telegram bot                LIVE
GrowHelper gateway          LIVE
7 agent profiles            READY
LLM                         gpt-5.5 / openai-codex
Kanban config               READY
GrowHelper plugin           INSTALLED + ENABLED
Server Git checkout         /home/growhelper/src/grow-helper-team-in-hermes @ c99af00
Plant onboarding/menu       DEPLOYED, Telegram E2E NOT YET TESTED
Dashboard                   LIVE
Dashboard public port       9119/tcp
Dashboard auth              BASIC AUTH
Plant registry              0 Plants
Full Plant Cycle E2E        NOT YET TESTED
```

Следующему агенту следует продолжать **с первого реального Plant/Campaign и полного Kanban E2E**, не переделывая уже работающий deployment layer.

---

## 21. Deploy Plant onboarding и Telegram menu — 2026-08-24

Production обновлён из чистого server checkout:

```text
/home/growhelper/src/grow-helper-team-in-hermes
commit c99af00
```

Перед deploy полный suite прошёл через Hermes Python 3.11: `30 tests OK` и
JSON/YAML validation OK. Запущен штатный idempotent installer с
`--skip-systemd-unit`; Plant data, credentials, sessions, Kanban boards и
существующий Dashboard override сохранены. Перезапущены только:

```text
hermes-gateway-grow-helper.service
growhelper-dashboard.service
```

После deploy:

- `doctor.py`: `0 error(s)`;
- оба сервиса: `active/running`, `ExecMainStatus=0`;
- Dashboard smoke `http://127.0.0.1:9119/api/status`: OK;
- свежие warning/error logs обоих сервисов: пусто;
- installed GrowHelper SOUL, plugin и Telegram client совпадают с checkout;
- Telegram Bot API `getMyCommands` возвращает ровно `addplant`, `plant`,
  `compress`, `new`, `status`, `context`.

Реальный Telegram `/addplant` → avatar → onboarding → `/plant` и полный Kanban
Cycle остаются ручной E2E-проверкой оператора.
