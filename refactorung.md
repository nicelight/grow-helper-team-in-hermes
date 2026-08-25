# План рефакторинга GrowHelper

## Вердикт

Проекту нужен локальный рефакторинг без изменения архитектуры.

Сохраняем:

- Hermes Profiles + Kanban + один GrowHelper plugin;
- один Plant workspace и одна Kanban board на Plant;
- Telegram как пользовательский UI;
- Hermes Dashboard как admin/debug UI;
- filesystem-first Plant storage;
- текущий постоянный roster Profiles.

Не добавляем новые сервисы, базы данных, очереди, orchestration layers,
frameworks или абстракции без подтверждённой необходимости.

## 1. Разделить `plugin.py`

`plugin/grow-helper-monitor/growhelper_monitor/plugin.py` содержит около 1250
строк и объединяет несколько независимо изменяемых обязанностей:

- состояние Telegram turn и session context;
- gateway hooks;
- permission guard;
- команды `/addplant` и `/plant`;
- Plant tools;
- создание, обновление и recovery Cycle;
- финальную Telegram publication;
- tool schemas и регистрацию plugin.

Особенно крупные функции:

- `_handle_start_cycle` — около 200 строк;
- `_handle_publish_reply` — около 160 строк;
- `_pre_tool_call` — около 100 строк.

Целевая KISS-структура:

```text
growhelper_monitor/
├── plugin.py          # только register()
├── runtime_context.py # TurnState, ContextVar, session context
├── gateway.py         # gateway hooks и compact Plant context
├── permissions.py     # pre_tool_call
├── commands.py        # /addplant и /plant
├── tools.py           # Plant/Cycle/change/publication handlers и schemas
├── core.py
├── hermes_adapter.py
├── telegram_client.py
└── validation.py
```

`plugin.py` должен остаться единственным Hermes entry point и только собирать
готовые handlers, hooks, commands и schemas.

Не вводить DI framework, service classes или дополнительную orchestration
архитектуру.

## 2. Исправить обновление установленного plugin

`scripts/install-team.py::copy_plugin()` использует `copytree()` с
`dirs_exist_ok=True`. Удалённые или переименованные файлы остаются в
установленном plugin после deploy.

Перед разбиением `plugin.py` installer должен синхронизировать managed plugin
directory без остаточных файлов. Допустимые варианты:

- собрать plugin во временном каталоге и атомарно заменить destination;
- очистить только полностью управляемый каталог `grow-helper-monitor` перед
  копированием.

Нельзя затрагивать Plant data, Profile credentials, sessions, Kanban boards и
другие каталоги `.hermes`.

Добавить один installer test: файл, отсутствующий в новой source-копии plugin,
не должен оставаться в destination после повторного install.

## 3. Реально использовать `team.yaml` как source of truth

Сейчас roster и toolsets независимо продублированы в:

- `team.yaml`;
- `scripts/install-team.py`;
- `scripts/doctor.py`;
- частично `scripts/backup.py` и тестах.

Installer и doctor должны получать имена Profiles, descriptions и toolsets из
`team.yaml`. Backup должен получать оттуда хотя бы список Profiles.

Не создавать второй config-файл с теми же данными.

Проверить тестом:

- все Profiles из `team.yaml` устанавливаются;
- doctor проверяет тот же roster и toolsets;
- plugin tools/hooks/commands соответствуют контракту `team.yaml`.

## 4. Синхронизировать Activity contract и runtime

`spec/schemas/activity-entry-core.schema.json` требует обязательные поля,
включая:

- `timestamp`;
- `plant_id`;
- `kind`;
- `cycle_id`;
- `session_id`;
- `message_id`;
- `text`;
- `media`;
- `delivery`;
- `phase`.

`core.append_activity()` сейчас принимает неполные записи. Даже существующий
unit test добавляет запись без `session_id` и `phase`.

Предпочтительное решение:

- проверять обязательные поля перед append;
- добавлять `timestamp` и `plant_id` внутри runtime, как сейчас;
- отклонять новые некорректные записи понятной ошибкой;
- сохранить tolerant reading старых строк для backward compatibility;
- привести тестовые fixtures к канонической схеме.

Не добавлять отдельную Activity database или synthetic record ID.

## 5. Удалить устаревший корневой `MANIFEST.sha256`

Committed `MANIFEST.sha256` не соответствует текущему checkout:

- часть checksum устарела;
- в нём перечислены отсутствующие файлы;
- новые файлы проекта не представлены.

`scripts/build-release.sh` уже исключает корневой manifest и создаёт свежий
`MANIFEST.sha256` внутри release bundle. Поэтому корневой committed manifest
следует удалить, а release manifest оставить generated artifact.

Не обновлять корневой manifest после каждого commit.

## 6. Унифицировать Python для тестов

`tests/run-tests.sh` жёстко использует `python3`, хотя production GrowHelper
работает через Hermes Python 3.11, а `build-release.sh` уже поддерживает
`GROWHELPER_PYTHON`.

Использовать тот же контракт:

```bash
PYTHON_BIN="${GROWHELPER_PYTHON:-python3}"
```

Все Python-вызовы в `tests/run-tests.sh` должны идти через `PYTHON_BIN`.

Production-команда:

```bash
GROWHELPER_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python \
  bash tests/run-tests.sh
```

## 7. Сделать tool schemas строгими

Добавить `additionalProperties: false` в schemas:

- `growhelper_plants`;
- `growhelper_start_cycle`;
- `growhelper_publish_reply`.

`growhelper_request_change` уже использует это правило.

Цель — отклонять опечатки и неизвестные аргументы на validation boundary, а не
молча игнорировать их внутри handler.

Не менять публичные tool names и их текущую семантику.

## 8. Упорядочить agent-legible документы

`todo.md` содержит старый план с незакрытыми checkbox, хотя большая часть
работ уже реализована и развёрнута. Такой файл вводит нового агента в
заблуждение.

Нужно:

- отметить фактический статус пунктов;
- перенести завершённый план в `docs/plans/completed/` или явно назвать его
  архивным;
- оставить в корне только актуальный backlog, если он действительно нужен.

`AGENTS.md` должен постепенно становиться картой source of truth и набором
обязательных правил. Подробности, уже закреплённые в BRIEF, spec или RUNBOOK,
не следует бесконечно дублировать в нём.

## 9. Минимально усилить тестовую опору

Текущий regression suite проходит 30 тестов. Расширять его большой матрицей не
нужно.

Перед рефакторингом достаточно добавить focused tests для:

- удаления устаревших managed plugin-файлов installer-ом;
- соответствия `team.yaml` installer/doctor контракту;
- обязательных полей Activity entry;
- строгих tool schemas;
- Dashboard recommendation: idempotent повтор и `delivery_uncertain` fence.

Существующие Telegram tests должны продолжать использовать mocked Bot API.
Real Telegram E2E остаётся ручной проверкой оператора.

## 10. Что пока не рефакторить

### `hermes_adapter.py`

Файл крупный, но имеет правильную узкую ответственность: вся совместимость с
Hermes Kanban собрана в одном месте. Не разделять без подтверждённой проблемы.

### `core.py`

Файл близок к 1000 строк, но пока остаётся одной filesystem-backed domain
границей. После разделения `plugin.py` оценить частоту его изменений.

Если дальнейшая разработка продолжит расширять `core.py`, допустимое следующее
разделение:

```text
registry.py  # registry, bindings, ownership, Plant lifecycle
workspace.py # templates, activity, media, journal и dataset reads
```

Не создавать repository/service layers поверх этих двух модулей.

### Dashboard frontend

Не добавлять bundler, framework или Node dependency. Текущий небольшой
code-native Dashboard соответствует KISS.

### Архитектура продукта

Не менять:

- Hermes core;
- Kanban source of truth;
- Plant-first storage;
- permanent Profile roster;
- один GrowHelper plugin;
- существующую production topology.

## Порядок выполнения

1. Исправить plugin synchronization в installer.
2. Удалить stale manifest и привести test runner к Hermes Python.
3. Связать installer/doctor/backup с `team.yaml`.
4. Синхронизировать Activity schema и runtime.
5. Сделать tool schemas строгими.
6. Добавить только перечисленные characterization tests.
7. Разделить `plugin.py`, не меняя observable behavior.
8. Запустить полный regression suite.
9. Выполнить deploy через `install-team.py`.
10. Проверить doctor, сервисы, Dashboard smoke test и ручной Telegram E2E.

Каждый этап должен быть отдельным проверяемым изменением. Не объединять
структурный рефакторинг с новым пользовательским функционалом.

## Критерий завершения

- `plugin.py` является коротким composition root;
- основные runtime responsibilities находятся в очевидных модулях;
- удалённые plugin-файлы не остаются после deploy;
- `team.yaml`, SDD и runtime не расходятся;
- test runner одинаково работает локально и через Hermes Python;
- committed repository не содержит устаревший release manifest;
- текущие 30 regression tests и новые focused tests проходят;
- observable Telegram, Kanban, Dashboard и Plant storage behavior не изменён.
