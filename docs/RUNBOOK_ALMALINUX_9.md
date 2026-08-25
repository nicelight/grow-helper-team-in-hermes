# GrowHelper: runbook развёртывания на AlmaLinux 9

## 1. Назначение и итоговая топология

Runbook разворачивает первую серверную версию GrowHelper без форка Hermes и без отдельного Agro backend.

```text
/usr/local/lib/hermes-agent
      │ общий код Hermes
      ├───────────────────────────────┐
      │                               │
/root/.hermes                  /home/growhelper/.hermes
другой Hermes root             GrowHelper Hermes root
вне GrowHelper                        │
                                      ├─ grow-helper Gateway
Telegram users ───────────────────────┤
                                      ├─ GrowHelper Kanban dispatcher
                                      │    ├─ vision-observation
                                      │    ├─ plant-state
                                      │    ├─ cultivation-advisor
                                      │    ├─ task-followup
                                      │    ├─ data-curator
                                      │    └─ reviewer
                                      │
                                      └─ Plant data: /home/growhelper/grow-helper/

Trusted administrators
      │ SSH tunnel or VPN
      ▼
Hermes Web Dashboard :9119, process owner = growhelper
      ├─ GrowHelper tab
      └─ stock Kanban / Sessions / Logs / Analytics
```

Первая версия рассчитана на **один Linux host, отдельного непривилегированного OS user `growhelper`, один GrowHelper Gateway и один Kanban dispatcher**. Общий бинарник Hermes может обслуживать несколько Hermes roots, но GrowHelper runtime и Dashboard всегда запускаются от `growhelper` и используют `/home/growhelper/.hermes`.

## 2. До начала

Нужны:

- AlmaLinux 9 с выходом в интернет;
- sudo/root для первоначальной подготовки;
- установленный Hermes v0.20.4 или совместимее в `/usr/local/lib/hermes-agent` с командой `/usr/local/bin/hermes`;
- Telegram bot token от BotFather;
- numeric Telegram user IDs пилотных пользователей;
- API credentials хотя бы одного LLM provider;
- vision-capable model/provider для `vision-observation`;
- архив `grow-helper-team-0.1.0.tar.gz`.

Не публикуйте Dashboard наружу до завершения локального acceptance test.

## 3. Подготовить AlmaLinux

Под root/sudo:

```bash
sudo dnf update -y
sudo dnf install -y \
  git curl ca-certificates tar gzip unzip jq rsync openssl

id growhelper >/dev/null 2>&1 || \
  sudo useradd --create-home --shell /bin/bash growhelper

sudo loginctl enable-linger growhelper
GH_UID="$(id -u growhelper)"
sudo systemctl start "user@${GH_UID}.service"

sudo install -d -m 700 -o growhelper -g growhelper \
  /home/growhelper/.hermes \
  /home/growhelper/apps \
  /home/growhelper/grow-helper
```

Пароль или SSH key для `growhelper` настраиваются только если нужен прямой login. Для работы user-systemd services пароль не требуется. Не добавляйте `growhelper` в `wheel` и не выдавайте ему sudo.

Проверьте OS-level изоляцию:

```bash
sudo -iu growhelper /bin/bash -lc '
  printf "USER=%s\nHOME=%s\n" "$USER" "$HOME"
  id
  id -Z
  test -r /root/.hermes/config.yaml && { echo "BAD: /root/.hermes readable"; exit 1; } || true
  echo "OK: /root/.hermes isolated"
'
```

Переключитесь на dedicated user:

```bash
sudo -iu growhelper
umask 077
mkdir -p "$HOME/apps" "$HOME/incoming"
```

Все следующие команды, кроме явно помеченных `sudo`, выполняются от `growhelper`.

## 4. Проверить общую установку Hermes

GrowHelper использует общую установку Hermes и отдельный Hermes root. Повторно устанавливать Hermes под `growhelper` не нужно.

Проверьте бинарник и runtime:

```bash
test -x /usr/local/bin/hermes
test -d /usr/local/lib/hermes-agent
/usr/local/bin/hermes --version
```

Ожидается Hermes v0.20.4 или совместимее. Команда должна выполняться от `growhelper`, а `HOME` оставаться `/home/growhelper`.

Проверьте, что новый Hermes root независим:

```bash
echo "$HOME"
ls -ld "$HOME/.hermes"
test -r /root/.hermes/config.yaml && { echo "BAD: root Hermes readable"; exit 1; } || echo "OK: root Hermes isolated"
```

### Python 3.11 для скриптов релиза

Используйте Python из общей Hermes venv:

```bash
GH_PY=/usr/local/lib/hermes-agent/venv/bin/python
"$GH_PY" --version
"$GH_PY" -c 'import yaml; print("PyYAML OK")'
```

Системный Python AlmaLinux 9 для runtime GrowHelper не используется.

## 5. Проверить и распаковать релиз

Поместите архив в `/home/growhelper/` либо `~/incoming/` и убедитесь, что владельцем является `growhelper`. Для релиза 0.1.0:

```bash
ARCHIVE="$HOME/grow-helper-team-0.1.0.tar.gz"
EXPECTED_SHA256="b4ee391cd01a15dd6b459292baf0f117728894a384eee950cb1ebde8608818cd"
echo "$EXPECTED_SHA256  $ARCHIVE" | sha256sum -c -

mkdir -p "$HOME/apps"
tar -xzf "$ARCHIVE" -C "$HOME/apps"
cd "$HOME/apps/grow-helper-team-0.1.0"
sha256sum -c MANIFEST.sha256
```

Проверьте содержимое:

```bash
cat VERSION
cat HERMES_INSPECTED_COMMIT
find . -maxdepth 2 -type f | sort
```

## 6. Выполнить автономные тесты

```bash
cd "$HOME/apps/grow-helper-team-0.1.0"
"$GH_PY" -m compileall -q plugin scripts tests
"$GH_PY" -m unittest discover -s tests -p 'test_*.py' -v

# Полный wrapper, если системный python3 уже >=3.11 и имеет PyYAML:
bash tests/run-tests.sh
```

Ожидаемый результат: все tests `OK`, JSON/YAML validation `OK`, а `node --check` выполняется автоматически при наличии Node.js.

Тесты не используют реальные LLM/Telegram credentials. Они проверяют программную механику приложения, а live acceptance выполняется позже.

## 7. Установить team bundle

```bash
cd "$HOME/apps/grow-helper-team-0.1.0"

"$GH_PY" scripts/install-team.py \
  --data-root "$HOME/grow-helper" \
  --timezone Asia/Dushanbe \
  --telegram-admin-users 111111111 \
  --dashboard-host 127.0.0.1 \
  --dashboard-port 9119
```

Установщик:

- создаёт семь Profiles;
- ставит нужные `SOUL.md`;
- задаёт узкие toolsets;
- отключает shared Profile memory;
- устанавливает runtime/filesystem-guard plugin в каждый Profile;
- устанавливает Dashboard plugin на machine-level;
- создаёт `~/grow-helper/plants/index.json` и templates;
- создаёт `growhelper-dashboard.service`;
- не удаляет существующие Plants, boards, sessions и credentials.

Проверьте:

```bash
ls -la "$HOME/.hermes/profiles"
ls -la "$HOME/.hermes/plugins/grow-helper-monitor/dashboard"
ls -la "$HOME/grow-helper/plants"
```

## 8. Настроить модели

При создании specialist Profiles installer использует `grow-helper` как источник конфигурации. Model/provider можно выбирать отдельно для каждой роли. Telegram credentials в specialist Profiles удаляются.

Минимальные требования:

| Profile | Требование |
|---|---|
| `grow-helper` | надёжный tool use и хорошее synthesis reasoning |
| `vision-observation` | реальный image/vision input |
| `plant-state` | структурированный недорогой reasoning |
| `cultivation-advisor` | наиболее сильная агрономическая reasoning model |
| `task-followup` | недорогая structured planning model |
| `data-curator` | недорогая extraction/classification model |
| `reviewer` | независимая сильная reasoning model |

Настроить можно через GrowHelper Dashboard profile switcher либо CLI. Этот Dashboard работает внутри `/home/growhelper/.hermes` и не должен видеть Profiles из других Hermes roots:

```bash
hermes -p grow-helper setup
hermes -p vision-observation setup
# Повторить только там, где нужны отдельные credentials/provider.
```

Не запускайте Gateway для specialist Profiles. Они вызываются dispatcher как worker processes.

## 9. Настроить Telegram

Самый простой путь:

```bash
grow-helper gateway setup
```

Или отредактируйте:

```bash
chmod 700 "$HOME/.hermes/profiles/grow-helper"
nano "$HOME/.hermes/profiles/grow-helper/.env"
```

Минимум:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:replace-me
TELEGRAM_ALLOWED_USERS=111111111,222222222
GROWHELPER_TELEGRAM_ADMIN_USERS=111111111
GROWHELPER_DATA_ROOT=/home/growhelper/grow-helper
GROWHELPER_TIMEZONE=Asia/Dushanbe
GROWHELPER_TELEGRAM_TIMEOUT_SECONDS=20
```

Права:

```bash
chmod 600 "$HOME/.hermes/profiles/grow-helper/.env"
```

Для первого пилота **не используйте** `TELEGRAM_ALLOWED_USERS=*`. Сначала проверьте multi-user isolation на небольшой allow-list.

`GROWHELPER_TELEGRAM_ADMIN_USERS` используется установщиком для штатного Hermes
admin/user split. Пользователи из этого списка получают slash-команды
администратора; остальные разрешённые или paired пользователи остаются обычными
пользователями. После изменения списка повторно выполните:

```bash
"$GH_PY" scripts/install-team.py \
  --data-root "$HOME/grow-helper" \
  --telegram-admin-users 111111111
```

Обычный пользователь продолжает свободно общаться с GrowHelper, но не получает
`/model`, `/update`, `/kanban` и другие административные slash-команды. Базовые
`/help` и `/whoami` Hermes оставляет доступными при ручном вводе. Видимое меню
глобально ограничивается восемью командами: `/addplant`, `/plant`, `/delplant`,
`/feedback`, `/compress`, `/new`, `/status`, `/context`.

У specialist `.env` не должно быть Telegram credentials. Installer удаляет messaging keys из них.

## 10. Диагностика перед запуском

```bash
cd "$HOME/apps/grow-helper-team-0.1.0"
"$GH_PY" scripts/doctor.py
```

До запуска services допустимы только warnings о неактивных services и пустом Plant registry. Ошибок Profiles/plugin/config быть не должно.

Дополнительно:

```bash
grow-helper plugins doctor
hermes profile
```

## 11. Запустить Gateway и Dashboard

### Gateway

```bash
grow-helper gateway install --force
grow-helper gateway start
grow-helper gateway status
```

GrowHelper root должен иметь только **одного владельца Kanban dispatcher**. Не запускайте параллельно `hermes kanban daemon`. Не используйте для GrowHelper system-wide gateway unit: Gateway устанавливается как user-systemd service пользователя `growhelper`, а `loginctl enable-linger growhelper` обеспечивает запуск после logout/boot.

### Dashboard

```bash
systemctl --user daemon-reload
systemctl --user enable --now growhelper-dashboard
systemctl --user status growhelper-dashboard --no-pager
```

Проверить локально на сервере:

```bash
curl -fsS http://127.0.0.1:9119/api/status | jq .
curl -fsS http://127.0.0.1:9119/api/plugins/grow-helper-monitor/health | jq .
```

Если Dashboard сообщает о недостающих web dependencies, следуйте его точной подсказке установки внутри Hermes environment и повторите запуск.

## 12. Открыть Dashboard безопасно

### Рекомендуется: SSH tunnel

На административном компьютере:

```bash
ssh -N -L 9119:127.0.0.1:9119 ADMIN_SSH_USER@SERVER_IP
```

Откройте:

```text
http://127.0.0.1:9119
```

В sidebar должна появиться вкладка **GrowHelper** рядом со штатной **Kanban**.

Порт 9119 в firewalld открывать не нужно.

SSH tunnel можно создавать через обычный административный SSH account; forwarding на `127.0.0.1:9119` не требует входа именно от имени `growhelper`. Полный Hermes Dashboard предназначен только для доверенных технических администраторов: это не multi-tenant UI для конечных пользователей.

### Опционально: NGINX + HTTPS

Это делайте только после локального acceptance test.

1. Создайте Dashboard credentials:

```bash
mkdir -p "$HOME/.config/growhelper"
cp config/dashboard.env.example "$HOME/.config/growhelper/dashboard.env"
chmod 600 "$HOME/.config/growhelper/dashboard.env"

openssl rand -base64 36
openssl rand -base64 48
nano "$HOME/.config/growhelper/dashboard.env"
```

2. Переустановите unit с non-loopback bind, чтобы Hermes auth gate был активен:

```bash
"$GH_PY" scripts/install-team.py \
  --data-root "$HOME/grow-helper" \
  --dashboard-host 0.0.0.0 \
  --dashboard-port 9119

systemctl --user restart growhelper-dashboard
```

3. Под root установите NGINX/certificate и используйте `deploy/nginx/growhelper.conf.example`.

4. В firewalld откройте 443, но **не 9119**:

```bash
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

5. При enforcing SELinux разрешите NGINX подключаться к local upstream:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

Проверьте, что unauthenticated browser получает login, а не Dashboard.

### Опционально: существующий Docker Traefik

Если на AlmaLinux уже работает Traefik в Docker, отдельный NGINX не нужен.

1. Создайте Dashboard credentials и переустановите unit с
   `--dashboard-host 0.0.0.0`, как в предыдущем разделе. Non-loopback bind
   включает штатный Hermes auth gate.
2. Добавьте контейнеру Traefik доступ к host gateway:

```yaml
services:
  traefik:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

3. Скопируйте и адаптируйте:

```text
deploy/traefik/growhelper-dynamic.yml.example
```

в каталог file provider Traefik. В примере замените hostname и имя
`certResolver` на существующие значения.

4. Не публикуйте `9119/tcp` через Docker и не открывайте его в firewalld.
   Traefik обращается к `http://host.docker.internal:9119`, а внешние клиенты —
   только к HTTPS router Traefik.

5. Проверьте одновременно два слоя: TLS/reverse proxy и login штатного Hermes
   Dashboard. Не отключайте встроенную Dashboard-аутентификацию только потому,
   что доступ уже проходит через Traefik.

## 13. Первый live acceptance test

Используйте один тестовый Telegram user.

### 13.1 Inception и Plant creation

1. Убедитесь, что меню содержит ровно восемь согласованных команд и `/feedback`
   возвращает контакт `@dyingseed`.
2. Выполните `/addplant`; до фотографии бот должен только просить аватар.
3. Загрузите реальную фотографию и убедитесь, что Plant сразу создан в `onboarding`.
4. Задайте имя либо оставьте предложенное и пройдите Inception: identity, starting state, environment, desired result, success criteria, constraints.
5. Подтвердите Campaign draft и убедитесь, что Plant перешёл в `active`.
6. Убедитесь, что появился каталог и `photos/avatar.jpg` не превышает 500000 байт:

```bash
jq . "$HOME/grow-helper/plants/index.json"
find "$HOME/grow-helper/plants" -maxdepth 2 -type f | sort
find "$HOME/grow-helper/plants" -path '*/photos/avatar.jpg' -printf '%s %p\n'
```

7. Создайте второй Plant, выполните `/plant`, выберите первый кнопкой и проверьте возврат его аватара.
8. В Dashboard → GrowHelper должны появиться оба Plants.
9. Выполните `/delplant`, отмените удаление и убедитесь, что Plant сохранён; повторите команду, подтвердите и проверьте удаление registry, workspace и Kanban board.

Это ручной Telegram E2E. Автоматические тесты проверяют только registry и mocked Bot API payload; без наблюдаемого сообщения в Telegram нельзя заявлять успешный E2E.

### 13.2 Measurement-only Cycle

Отправьте pH, EC и температуру без фото.

Ожидается:

```text
plant-state ─────────────┐
                         ├→ GrowHelper final
cultivation-advisor ─────┘
```

Проверьте:

- пользователь получил короткий acknowledgement, затем итог;
- в GrowHelper → Workflow видны task statuses и handoffs;
- observation/inference/recommendation не смешаны;
- штатная Kanban board соответствует именно этому Plant.

### 13.3 Photo Cycle

Отправьте реальную фотографию с коротким комментарием.

Ожидается evidence-first dependency:

```text
vision-observation → plant-state → final gate
```

Advisor/Reviewer появляется только при реальной необходимости.

Критическая проверка: во вкладке Evidence фотография должна находиться в Plant workspace. Если media не скопировано, остановите пилот и проверьте формат локального media path, который Telegram adapter Hermes добавляет в LLM-visible message. Не заменяйте это догадкой в prompt.

### 13.4 Перезапуск

```bash
grow-helper gateway restart
systemctl --user restart growhelper-dashboard
```

После рестарта Plant, conversation, board, Cycle history и files должны сохраниться.

### 13.5 Multi-user isolation

Добавьте второго Telegram user в allow-list и создайте другой Plant.

Проверьте:

- пользователь A не видит и не выбирает Plant пользователя B;
- каждый Cycle попадает в отдельную board;
- каждый workspace отдельный;
- specialist handoff одной board не появляется в другой;
- Dashboard администратор видит оба Plants.

## 14. Ежедневная эксплуатация

Статус:

```bash
grow-helper gateway status
systemctl --user status growhelper-dashboard --no-pager
```

Логи:

```bash
hermes logs --follow
grow-helper logs --follow
journalctl --user -u growhelper-dashboard -f
systemctl --user list-units '*grow*' '*hermes*'
```

Plant/board lookup:

```bash
jq -r '.plants[] | [.plant_id,.nickname,.board_slug,.active_cycle_id] | @tsv' \
  "$HOME/grow-helper/plants/index.json"

hermes kanban boards list
hermes kanban --board BOARD_SLUG list
hermes kanban --board BOARD_SLUG stats
```

Не используйте `hermes kanban boards switch` в автоматизации. Всегда указывайте `--board` явно.

## 15. Что администратор может делать

Через GrowHelper Dashboard:

- наблюдать Campaign и Plant state;
- читать public conversation;
- раскрывать exact specialist `summary + metadata`;
- смотреть status/error/retry/session id;
- перейти в штатный Kanban;
- отправить пользователю одну рекомендацию от имени GrowHelper.

Administrative recommendation является сообщением, а не прямым изменением действий пользователя или Plant state. Фактический outcome должен прийти от пользователя в следующем событии.

## 16. Recovery: blocked или failed Kanban task

1. Откройте Plant → Workflow и найдите blocked node.
2. Раскройте error, latest run и parent handoffs.
3. Откройте штатный Kanban по ссылке.
4. Исправьте первопричину: provider credentials, model capability, path/media или malformed handoff.
5. Добавьте краткий operator comment и выполните retry/unblock через штатный Kanban.

CLI:

```bash
hermes kanban --board BOARD_SLUG show TASK_ID
hermes kanban --board BOARD_SLUG comment TASK_ID "Причина исправлена" --author growhelper-admin
hermes kanban --board BOARD_SLUG unblock TASK_ID
```

Не закрывайте specialist task как `done` с выдуманным handoff. Если её вклад реально не нужен, используйте осознанный `no comments`/skip path и зафиксируйте причину.

## 17. Recovery: Telegram delivery uncertain

При network timeout Telegram мог принять сообщение, но ответ потерялся. GrowHelper ставит `delivery=uncertain` и запрещает автоматический retry.

### Сообщение видно в Telegram

```bash
"$GH_PY" scripts/reconcile-delivery.py mark-sent \
  --plant-id PLANT_ID \
  --cycle-id CYCLE_ID \
  --confirm-visible \
  --telegram-message-id OPTIONAL_MESSAGE_ID \
  --unblock
```

### Сообщение точно отсутствует

```bash
"$GH_PY" scripts/reconcile-delivery.py retry \
  --plant-id PLANT_ID \
  --cycle-id CYCLE_ID \
  --confirm-not-delivered \
  --unblock
```

Если повтор тоже `uncertain`, снова проверьте Telegram. Не повторяйте команду вслепую.

## 18. Резервное копирование

Перед обновлением и ежедневно/еженедельно:

```bash
cd "$HOME/apps/grow-helper-team-0.1.0"
"$GH_PY" scripts/backup.py \
  --data-root "$HOME/grow-helper" \
  --include-config
```

Архив создаётся в `~/grow-helper-backups/` и использует SQLite backup API вместо копирования живых WAL files.

Secrets по умолчанию не включаются. Для отдельного зашифрованного backup credentials:

```bash
"$GH_PY" scripts/backup.py --include-secrets
chmod 600 "$HOME/grow-helper-backups"/*.tar.gz
```

`--include-secrets` создаёт высокочувствительный архив; храните его только зашифрованным.

## 19. Восстановление backup

1. Остановите services:

```bash
grow-helper gateway stop
systemctl --user stop growhelper-dashboard
```

2. Распакуйте backup во временный каталог:

```bash
mkdir -p "$HOME/restore-growhelper"
tar -xzf BACKUP.tar.gz -C "$HOME/restore-growhelper"
```

3. Проверьте `manifest.json`.

4. Восстановите данные:

```bash
rsync -a --delete \
  "$HOME/restore-growhelper/growhelper-backup/data/" \
  "$HOME/grow-helper/"

if [ -d "$HOME/restore-growhelper/growhelper-backup/hermes/kanban" ]; then
  mkdir -p "$HOME/.hermes/kanban"
  rsync -a --delete \
    "$HOME/restore-growhelper/growhelper-backup/hermes/kanban/" \
    "$HOME/.hermes/kanban/"
fi

if [ -f "$HOME/restore-growhelper/growhelper-backup/hermes/kanban.db" ]; then
  cp -a "$HOME/restore-growhelper/growhelper-backup/hermes/kanban.db" \
    "$HOME/.hermes/kanban.db"
fi
```

5. Повторно установите текущий GrowHelper bundle, чтобы code/plugin/config contracts соответствовали данным:

```bash
"$GH_PY" scripts/install-team.py --data-root "$HOME/grow-helper"
"$GH_PY" scripts/doctor.py
```

6. Запустите services и выполните один test Cycle.

## 20. Обновление Hermes

Сначала backup:

```bash
"$GH_PY" scripts/backup.py --include-config
```

Общая установка Hermes обновляется из привилегированного административного shell, потому что код находится в `/usr/local/lib/hermes-agent`. Один update меняет код для всех Hermes roots, использующих эту установку. На время обновления остановите GrowHelper Gateway. Из shell пользователя `growhelper`:

```bash
grow-helper gateway stop
exit
```

В root/admin shell:

```bash
/usr/local/bin/hermes update
/usr/local/bin/hermes --version
sudo -iu growhelper
export GH_PY=/usr/local/lib/hermes-agent/venv/bin/python
```

Если эту же установку используют другие Hermes roots, их Gateway processes также нужно перезапустить после обновления их владельцем. Не переносите их config/credentials в GrowHelper root.

После обновления обязательно переустановите bundle и проверьте adapter boundary:

```bash
cd "$HOME/apps/grow-helper-team-0.1.0"
"$GH_PY" scripts/install-team.py --data-root "$HOME/grow-helper"
"$GH_PY" scripts/doctor.py
bash tests/run-tests.sh

grow-helper gateway restart
systemctl --user restart growhelper-dashboard
```

Проведите один measurement Cycle и один photo Cycle. Основная зона возможной несовместимости изолирована в:

```text
plugin/grow-helper-monitor/growhelper_monitor/hermes_adapter.py
```

## 21. Обновление GrowHelper bundle

Распакуйте новую версию рядом, а не поверх старой:

```bash
cd "$HOME/incoming"
sha256sum -c grow-helper-team-NEW.tar.gz.sha256
tar -xzf grow-helper-team-NEW.tar.gz -C "$HOME/apps"
cd "$HOME/apps/grow-helper-team-NEW"

bash tests/run-tests.sh
"$GH_PY" scripts/backup.py --include-config
"$GH_PY" scripts/install-team.py --data-root "$HOME/grow-helper"
"$GH_PY" scripts/doctor.py

grow-helper gateway restart
systemctl --user restart growhelper-dashboard
```

Каталог `~/grow-helper/` не перемещается и не удаляется.

## 22. Rollback приложения

1. Остановите services.
2. Вернитесь к предыдущему каталогу release.
3. Запустите его `install-team.py`.
4. При необходимости восстановите data/Kanban из backup.
5. Запустите `doctor.py` и acceptance Cycle.

Installer создаёт `.bak-<timestamp>` рядом с изменяемыми SOUL/config files, но полноценный rollback должен опираться на backup, а не только на эти локальные копии.

## 23. Security checklist

- [ ] GrowHelper работает от отдельного непривилегированного OS user `growhelper`, без sudo/wheel.
- [ ] GrowHelper Hermes root — `/home/growhelper/.hermes`; `/root/.hermes` не читается пользователем `growhelper`.
- [ ] Telegram allow-list содержит только numeric IDs пилота.
- [ ] `allow_admin_from` содержит только доверенных Telegram admins; обычным users не выданы административные slash-команды.
- [ ] Specialist Profiles не имеют Telegram tokens.
- [ ] Shared Profile memory отключена.
- [ ] Generic terminal и code execution toolsets не включены.
- [ ] Filesystem guard plugin установлен во всех семи Profiles.
- [ ] Dashboard process принадлежит `growhelper`, не `root`, и не запускается с `HERMES_HOME=/root/.hermes`.
- [ ] Dashboard слушает `127.0.0.1`, либо non-loopback bind защищён встроенным Hermes auth + HTTPS.
- [ ] Порт 9119 не открыт напрямую в firewalld.
- [ ] Backup permissions `0600`, secrets хранятся отдельно/зашифрованы.
- [ ] Реальный photo ingestion проверен.
- [ ] Multi-user isolation проверена двумя Telegram users.
- [ ] uncertain delivery recovery отработан без duplicate send.

## 24. Acceptance criteria перед пилотом

- [ ] `/addplant` создаёт onboarding Plant после валидного аватара не более 500 КБ.
- [ ] Telegram-меню содержит восемь согласованных команд; `/feedback` возвращает контакт разработчика, `/plant` переключает контекст, а `/delplant` удаляет Plant только после подтверждения.
- [ ] Подтверждение Campaign переводит Plant в `active`.
- [ ] На Plant создаются отдельные workspace и Kanban board.
- [ ] Measurement Cycle запускает параллельный state/advisor flow.
- [ ] Photo Cycle запускает `vision → state`, а не параллельную диагностику.
- [ ] Vision handoff не содержит диагнозов и recommendations.
- [ ] Dashboard показывает user input, final reply и точные specialist handoffs.
- [ ] Candidate dataset не показывается как validated evidence.
- [ ] `history-summary.md` обновляется только на важных событиях.
- [ ] Restart сохраняет данные и board history.
- [ ] Второй user изолирован от первого.
- [ ] Admin recommendation доставляется, но не меняет state автоматически.
- [ ] Backup создаётся и читается.
- [ ] `doctor.py` завершён без errors.

## 25. Релевантные upstream references

- Hermes install: `https://hermes-agent.nousresearch.com/install.sh`
- Profiles: `https://hermes-agent.nousresearch.com/docs/user-guide/profiles`
- Telegram: `https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram`
- Kanban: `https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban`
- Web Dashboard: `https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard`
- Extending Dashboard: `https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard`
