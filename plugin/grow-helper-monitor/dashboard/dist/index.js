(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) {
    console.error("GrowHelper: Hermes Plugin SDK is unavailable");
    return;
  }

  const React = SDK.React;
  const h = React.createElement;
  const { useState, useEffect, useMemo } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button } = SDK.components;
  const API = "/api/plugins/grow-helper-monitor";

  function timeText(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value))) {
      const number = Number(value);
      const millis = number < 100000000000 ? number * 1000 : number;
      return new Date(millis).toLocaleString();
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function durationText(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const value = Math.round(Number(seconds));
    if (value < 60) return value + " с";
    if (value < 3600) return Math.floor(value / 60) + " мин " + (value % 60) + " с";
    return Math.floor(value / 3600) + " ч " + Math.floor((value % 3600) / 60) + " мин";
  }

  const roleNames = {
    "grow-helper": "GrowHelper / маршрутизация",
    "grow-helper-synthesis": "GrowHelper / итог",
    "vision-observation": "Визуальные факты",
    "plant-state": "Состояние растения",
    "cultivation-advisor": "Гипотеза и рекомендация",
    "task-followup": "План проверки",
    "data-curator": "Сохранение evidence",
    "reviewer": "Независимая проверка",
    "unassigned": "Без исполнителя"
  };

  const statusNames = {
    triage: "нужен разбор",
    todo: "ожидает зависимостей",
    scheduled: "запланировано",
    ready: "готово к запуску",
    running: "выполняется",
    blocked: "заблокировано",
    review: "проверка",
    done: "завершено",
    archived: "архив",
    active: "активен",
    missing: "не найден",
    unavailable: "недоступно",
    unknown: "неизвестно"
  };

  function statusBadge(status) {
    const text = statusNames[status] || status || "—";
    return h("span", { className: "gh-status gh-status-" + (status || "unknown") }, text);
  }

  function SectionTitle(props) {
    return h("h3", { className: "gh-section-title" }, props.children);
  }

  function MarkdownPanel(props) {
    return h("pre", { className: "gh-markdown" }, props.text || "Нет данных.");
  }

  function ErrorBox(props) {
    return props.text ? h("div", { className: "gh-error" }, props.text) : null;
  }

  function PlantsPane(props) {
    const plants = props.plants || [];
    return h("div", { className: "gh-plants-pane" },
      h("div", { className: "gh-pane-header" },
        h("div", null,
          h("h2", null, "Plants"),
          h("div", { className: "gh-muted" }, plants.length + " кампаний")
        ),
        h(Button, { onClick: props.onRefresh, disabled: props.loading }, props.loading ? "Обновление…" : "Обновить")
      ),
      plants.length === 0
        ? h("div", { className: "gh-empty" }, "Пока нет созданных Plant campaigns.")
        : plants.map(function (plant) {
            const active = plant.active_cycle;
            const last = plant.last_activity;
            return h("button", {
              key: plant.plant_id,
              className: "gh-plant-card " + (props.selected === plant.plant_id ? "is-selected" : ""),
              onClick: function () { props.onSelect(plant.plant_id); }
            },
              h("div", { className: "gh-plant-card-top" },
                h("strong", null, plant.nickname || plant.plant_id),
                active ? statusBadge(active.status) : h("span", { className: "gh-status gh-status-idle" }, "ожидание")
              ),
              h("div", { className: "gh-muted" }, [plant.species, plant.cultivar].filter(Boolean).join(" · ") || "Вид не указан"),
              plant.company ? h("div", { className: "gh-small" }, plant.company) : null,
              h("div", { className: "gh-plant-facts" },
                h("span", null, "Стадия: ", h("b", null, plant.stage || "unknown")),
                h("span", null, "Состояние: ", h("b", null, plant.overall_status || "unknown"))
              ),
              active && active.current_step && active.current_step.length
                ? h("div", { className: "gh-current-step" }, "Сейчас: " + (roleNames[active.current_step[0].role] || active.current_step[0].title || active.current_step[0].role))
                : null,
              last ? h("div", { className: "gh-last-line" },
                h("span", null, timeText(last.timestamp)),
                h("span", null, String(last.text || "").slice(0, 90))
              ) : null
            );
          })
    );
  }

  function Overview(props) {
    const detail = props.detail;
    return h("div", { className: "gh-stack" },
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Текущее состояние")),
        h(CardContent, null, h(MarkdownPanel, { text: detail.overview.current_state }))
      ),
      h("div", { className: "gh-two-col" },
        h(Card, null,
          h(CardHeader, null, h(CardTitle, null, "Campaign")),
          h(CardContent, null, h(MarkdownPanel, { text: detail.overview.campaign }))
        ),
        h(Card, null,
          h(CardHeader, null, h(CardTitle, null, "Baseline")),
          h(CardContent, null, h(MarkdownPanel, { text: detail.overview.baseline }))
        )
      )
    );
  }

  function MetaItems(props) {
    const items = props.items;
    if (!Array.isArray(items) || items.length === 0) return null;
    return h("div", { className: "gh-meta-section" },
      h("div", { className: "gh-meta-title" }, props.title),
      items.map(function (item, index) {
        if (typeof item !== "object" || item === null) {
          return h("div", { key: index, className: "gh-meta-item" }, String(item));
        }
        return h("div", { key: item.id || index, className: "gh-meta-item" },
          h("div", { className: "gh-meta-text" }, item.text || JSON.stringify(item)),
          h("div", { className: "gh-meta-tags" },
            item.confidence ? h("span", null, "confidence: " + item.confidence) : null,
            item.source ? h("span", null, "source: " + item.source) : null,
            item.value !== undefined ? h("span", null, "value: " + item.value + (item.unit ? " " + item.unit : "")) : null,
            item.urgency ? h("span", null, "urgency: " + item.urgency) : null,
            item.reversibility ? h("span", null, "reversibility: " + item.reversibility) : null
          ),
          item.evidence_for && item.evidence_for.length ? h("div", { className: "gh-small" }, "За: " + item.evidence_for.join(", ")) : null,
          item.evidence_against && item.evidence_against.length ? h("div", { className: "gh-small" }, "Против: " + item.evidence_against.join(", ")) : null,
          item.missing_data && item.missing_data.length ? h("div", { className: "gh-warning-text" }, "Не хватает: " + item.missing_data.join(", ")) : null
        );
      })
    );
  }

  function TaskNode(props) {
    const node = props.node;
    const metadata = node.handoff_metadata || {};
    const warnings = node.schema_warnings || [];
    return h("div", { className: "gh-task-node gh-node-" + (node.status || "unknown") },
      h("div", { className: "gh-node-head" },
        h("div", null,
          h("div", { className: "gh-role" }, roleNames[node.role] || node.role || node.assignee || "Task"),
          h("div", { className: "gh-node-title" }, node.title || node.id)
        ),
        statusBadge(node.status)
      ),
      h("div", { className: "gh-node-meta" },
        h("span", null, "Длительность: " + durationText(node.duration_seconds)),
        node.latest_run && node.latest_run.profile ? h("span", null, "Profile: " + node.latest_run.profile) : null,
        node.latest_run && node.latest_run.outcome ? h("span", null, "Outcome: " + node.latest_run.outcome) : null
      ),
      node.summary ? h("div", { className: "gh-summary" }, node.summary) : h("div", { className: "gh-muted" }, "Handoff ещё не получен."),
      h(MetaItems, { title: "Наблюдения", items: metadata.observation }),
      h(MetaItems, { title: "Гипотезы / выводы", items: metadata.inference }),
      h(MetaItems, { title: "Рекомендации", items: metadata.recommendation }),
      metadata.missing_data && metadata.missing_data.length
        ? h("div", { className: "gh-warning-text" }, "Общие missing data: " + metadata.missing_data.join(", "))
        : null,
      warnings.length ? h("div", { className: "gh-schema-warnings" },
        h("strong", null, "Schema warnings"),
        warnings.map(function (warning, index) {
          return h("div", { key: warning.code + index }, (warning.path ? warning.path + ": " : "") + warning.message);
        })
      ) : null,
      h("details", { className: "gh-debug" },
        h("summary", null, "Debug details"),
        h("div", { className: "gh-debug-links" },
          h("a", { href: "/sessions", target: "_blank", rel: "noreferrer" }, "Открыть Hermes Sessions"),
          node.worker_session_id ? h("code", null, node.worker_session_id) : h("span", null, "worker_session_id отсутствует")
        ),
        h("pre", null, JSON.stringify({
          task_id: node.id,
          parents: node.parent_ids,
          children: node.child_ids,
          body: node.body,
          latest_run: node.latest_run,
          handoff_metadata: metadata,
          events: node.events
        }, null, 2))
      )
    );
  }

  function CycleWorkflow(props) {
    const cycle = props.cycle;
    const byDepth = {};
    (cycle.nodes || []).forEach(function (node) {
      const depth = String(node.depth || 0);
      if (!byDepth[depth]) byDepth[depth] = [];
      byDepth[depth].push(node);
    });
    const depths = Object.keys(byDepth).map(Number).sort(function (a, b) { return a - b; });
    return h(Card, { className: "gh-cycle" },
      h(CardHeader, null,
        h("div", { className: "gh-cycle-head" },
          h("div", null,
            h(CardTitle, null, "Cycle " + cycle.cycle_id),
            h("div", { className: "gh-muted" }, cycle.operator_input ? timeText(cycle.operator_input.timestamp) : "")
          ),
          statusBadge(cycle.status)
        )
      ),
      h(CardContent, null,
        cycle.operator_input ? h("div", { className: "gh-user-event" },
          h("strong", null, "Сообщение пользователя"),
          h("div", null, cycle.operator_input.text || ""),
          cycle.operator_input.media && cycle.operator_input.media.length ? h("div", { className: "gh-small" }, "Media: " + cycle.operator_input.media.join(", ")) : null
        ) : null,
        depths.length === 0 ? h("div", { className: "gh-empty" }, "Kanban graph не найден.") : null,
        depths.map(function (depth, index) {
          return h(React.Fragment, { key: depth },
            index > 0 ? h("div", { className: "gh-arrow" }, "↓") : null,
            h("div", { className: "gh-depth-row" },
              byDepth[String(depth)].map(function (node) {
                return h(TaskNode, { key: node.id, node: node });
              })
            )
          );
        }),
        cycle.publication ? h("div", { className: "gh-publication gh-delivery-" + cycle.publication.delivery },
          h("strong", null, "Ответ GrowHelper пользователю"),
          h("div", null, cycle.publication.text || ""),
          h("div", { className: "gh-small" }, "Delivery: " + cycle.publication.delivery + " · " + timeText(cycle.publication.timestamp))
        ) : null
      )
    );
  }

  function Workflow(props) {
    const cycles = props.detail.cycles || [];
    return h("div", { className: "gh-stack" },
      h("div", { className: "gh-toolbar" },
        h("a", { href: props.detail.kanban_url, target: "_blank", rel: "noreferrer", className: "gh-link-button" }, "Штатный Kanban"),
        h("a", { href: props.detail.sessions_url, target: "_blank", rel: "noreferrer", className: "gh-link-button" }, "Hermes Sessions")
      ),
      cycles.length ? cycles.map(function (cycle) {
        return h(CycleWorkflow, { key: cycle.cycle_id, cycle: cycle });
      }) : h("div", { className: "gh-empty" }, "У Plant ещё нет Cycles.")
    );
  }

  function Conversation(props) {
    const rows = props.detail.activity || [];
    return h("div", { className: "gh-conversation" },
      rows.length === 0 ? h("div", { className: "gh-empty" }, "Диалог ещё не сохранён.") : null,
      rows.map(function (row, index) {
        const kind = row.kind || "event";
        const title = row.background_review ? "Hermes · фоновая задача"
          : kind === "operator_message" ? "Пользователь"
          : kind === "admin_recommendation" ? "Администратор от имени GrowHelper"
          : "GrowHelper";
        return h("div", { key: index, className: "gh-message gh-message-" + kind },
          h("div", { className: "gh-message-head" },
            h("strong", null, title),
            h("span", null, timeText(row.timestamp)),
            row.delivery ? h("span", { className: "gh-delivery-label" }, row.delivery) : null
          ),
          row.background_review === "result"
            ? h("details", { className: "gh-review-result" },
                h("summary", null, "Результат фонового обновления skills"),
                h("div", { className: "gh-message-text" }, row.text || "")
              )
            : h("div", { className: "gh-message-text" }, row.text || ""),
          row.media && row.media.length ? h("div", { className: "gh-small" }, "Media: " + row.media.join(", ")) : null,
          row.cycle_id ? h("code", { className: "gh-cycle-ref" }, row.cycle_id) : null,
          row.error ? h("div", { className: "gh-error" }, row.error) : null
        );
      })
    );
  }

  function Evidence(props) {
    const detail = props.detail;
    return h("div", { className: "gh-stack" },
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Фото и файлы")),
        h(CardContent, null,
          detail.media && detail.media.length ? h("div", { className: "gh-gallery" },
            detail.media.map(function (media) {
              const url = API + "/plants/" + encodeURIComponent(detail.plant.plant_id) + "/media?path=" + encodeURIComponent(media.path);
              return h("a", { key: media.path, href: url, target: "_blank", rel: "noreferrer", className: "gh-media-card" },
                media.is_image ? h("img", { src: url, loading: "lazy", alt: media.name }) : h("div", { className: "gh-file-icon" }, "FILE"),
                h("div", null, media.name),
                h("span", null, timeText(media.modified_at))
              );
            })
          ) : h("div", { className: "gh-empty" }, "Фото ещё не сохранены.")
        )
      ),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Candidate / validated dataset")),
        h(CardContent, null,
          detail.dataset && detail.dataset.length ? detail.dataset.map(function (item, index) {
            return h("div", { key: item.record_id || index, className: "gh-dataset-item" },
              h("div", { className: "gh-dataset-head" },
                h("strong", null, item.type || "record"),
                h("span", { className: "gh-status gh-status-" + (item.status || "candidate") }, item.status || "candidate"),
                item.validation_result ? h("span", null, item.validation_result) : null
              ),
              h("pre", null, JSON.stringify(item, null, 2))
            );
          }) : h("div", { className: "gh-empty" }, "Reusable dataset пуст.")
        )
      )
    );
  }

  function History(props) {
    const journal = props.detail.overview.journal || [];
    return h("div", { className: "gh-stack" },
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Компактная траектория Campaign")),
        h(CardContent, null, h(MarkdownPanel, { text: props.detail.overview.history_summary }))
      ),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Journal")),
        h(CardContent, null, journal.length
          ? journal.map(function (entry) {
              return h("details", { key: entry.path, className: "gh-journal-entry" },
                h("summary", null, entry.path),
                h(MarkdownPanel, { text: entry.text })
              );
            })
          : h("div", { className: "gh-empty" }, "Journal пока пуст."))
      ),
      h("div", { className: "gh-muted" }, "Kanban остаётся источником истины о задачах и runs; journal хранит только предметный ход выращивания.")
    );
  }

  function AdminRecommendation(props) {
    const [text, setText] = useState("");
    const [sending, setSending] = useState(false);
    const [result, setResult] = useState("");
    const [requestKey, setRequestKey] = useState("");

    async function send() {
      const value = text.trim();
      if (!value) return;
      const key = requestKey || ((window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : String(Date.now()));
      if (!requestKey) setRequestKey(key);
      setSending(true);
      setResult("");
      try {
        await SDK.fetchJSON(API + "/plants/" + encodeURIComponent(props.plantId) + "/recommendation", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: value, idempotency_key: key })
        });
        setText("");
        setRequestKey("");
        setResult("Отправлено через Telegram от имени GrowHelper.");
        props.onSent();
      } catch (error) {
        setResult("Ошибка: " + (error && error.message ? error.message : String(error)));
      } finally {
        setSending(false);
      }
    }

    return h(Card, { className: "gh-admin-send" },
      h(CardHeader, null, h(CardTitle, null, "Рекомендация администратора")),
      h(CardContent, null,
        h("p", { className: "gh-muted" }, "Сообщение уйдёт через того же Telegram-бота и появится в Conversation. Оно не изменяет Kanban graph или action status."),
        h("textarea", {
          value: text,
          maxLength: 4000,
          placeholder: "Текст рекомендации от имени GrowHelper…",
          onChange: function (event) {
            setText(event.target.value);
            setRequestKey("");
            setResult("");
          }
        }),
        h("div", { className: "gh-send-row" },
          h("span", { className: "gh-small" }, text.length + "/4000"),
          h(Button, { onClick: send, disabled: sending || !text.trim() }, sending ? "Отправка…" : "Отправить")
        ),
        result ? h("div", { className: result.startsWith("Ошибка") ? "gh-error" : "gh-success" }, result) : null
      )
    );
  }

  function PlantDetail(props) {
    const detail = props.detail;
    const [tab, setTab] = useState("workflow");
    const tabs = [
      ["overview", "Overview"], ["workflow", "Workflow"], ["conversation", "Conversation"],
      ["evidence", "Evidence"], ["history", "History"]
    ];
    let content = null;
    if (tab === "overview") content = h(Overview, { detail: detail });
    if (tab === "workflow") content = h(Workflow, { detail: detail });
    if (tab === "conversation") content = h(Conversation, { detail: detail });
    if (tab === "evidence") content = h(Evidence, { detail: detail });
    if (tab === "history") content = h(History, { detail: detail });

    return h("div", { className: "gh-detail" },
      h("div", { className: "gh-detail-header" },
        h("div", null,
          h("button", { className: "gh-back", onClick: props.onBack }, "← Plants"),
          h("h2", null, detail.plant.nickname || detail.plant.plant_id),
          h("div", { className: "gh-muted" },
            [detail.plant.species, detail.plant.cultivar, detail.plant.company].filter(Boolean).join(" · ")
          )
        ),
        h("div", { className: "gh-detail-state" },
          h("span", null, "Стадия: ", h("b", null, detail.plant.stage || "unknown")),
          h("span", null, "Состояние: ", h("b", null, detail.plant.overall_status || "unknown")),
          h(Button, { onClick: props.onRefresh, disabled: props.loading }, props.loading ? "Обновление…" : "Обновить")
        )
      ),
      h("div", { className: "gh-tabs" }, tabs.map(function (item) {
        return h("button", {
          key: item[0], className: tab === item[0] ? "is-active" : "",
          onClick: function () { setTab(item[0]); }
        }, item[1]);
      })),
      content,
      h(AdminRecommendation, { plantId: detail.plant.plant_id, onSent: props.onRefresh })
    );
  }

  function App() {
    const [plants, setPlants] = useState([]);
    const [selected, setSelected] = useState("");
    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    async function loadPlants() {
      setLoading(true);
      setError("");
      try {
        const data = await SDK.fetchJSON(API + "/plants");
        setPlants(data.plants || []);
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }

    async function loadDetail(id, silent) {
      if (!id) return;
      if (!silent) setLoading(true);
      setError("");
      try {
        const data = await SDK.fetchJSON(API + "/plants/" + encodeURIComponent(id));
        setDetail(data);
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      } finally {
        if (!silent) setLoading(false);
      }
    }

    useEffect(function () { loadPlants(); }, []);
    useEffect(function () {
      if (!selected) return undefined;
      loadDetail(selected, false);
      const timer = window.setInterval(function () { loadDetail(selected, true); }, 10000);
      return function () { window.clearInterval(timer); };
    }, [selected]);

    return h("div", { className: "gh-app" },
      h("div", { className: "gh-app-title" },
        h("div", null,
          h("h1", null, "GrowHelper"),
          h("p", null, "Процесс выращивания: пользовательский диалог → evidence → гипотеза → решение → follow-up")
        )
      ),
      h(ErrorBox, { text: error }),
      selected && detail
        ? h(PlantDetail, {
            detail: detail, loading: loading,
            onRefresh: function () { loadDetail(selected, false); loadPlants(); },
            onBack: function () { setSelected(""); setDetail(null); }
          })
        : h(PlantsPane, {
            plants: plants, selected: selected, loading: loading,
            onRefresh: loadPlants,
            onSelect: function (id) { setSelected(id); }
          })
    );
  }

  window.__HERMES_PLUGINS__.register("grow-helper-monitor", App);
})();
