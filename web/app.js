(() => {
  const TOKEN_KEY = "localwiki_api_token";
  let tokenPlain = "";
  let tokenVisible = false;

  const CORE_LABELS = {
    chat: "对话",
    agent: "智能体",
    knowledge_compile: "知识编译",
    memory: "记忆",
    experience_persona: "经验·风格",
    embedding: "嵌入",
  };

  let modelState = { routes: {}, providers: {}, core_routes: [] };
  let ollamaModels = [];
  /** @type {Record<string, string[]>} */
  let providerModelsCache = {};
  let showAllProposals = false;
  let wikiCy = null;
  let wikiEgoCy = null;
  let wbSessionId = null;
  let wbPagePath = null;
  let wbPollTimer = null;
  let wikiPagesCache = [];
  let wikiPageFilter = "";
  const DIR_CAT_LABELS = {
    topics: "主题",
    concepts: "概念",
    entities: "实体",
    decisions: "决策",
    questions: "问题",
    references: "来源",
    memories: "记忆",
    experiences: "经验",
    profiles: "风格/档案",
    other: "其他",
  };

  const PROBE_CAPS = [
    "availability",
    "tool_calling",
    "structured_output",
    "vision",
    "embedding_dim",
    "actual_context",
  ];

  function token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function setToken(v) {
    localStorage.setItem(TOKEN_KEY, v || "");
    tokenPlain = v || "";
    renderTokenDisplay();
  }

  function maskToken(t) {
    if (!t) return "（未设置 — 点击「从服务填入」）";
    if (tokenVisible) return t;
    if (t.length <= 8) return "••••••••";
    return t.slice(0, 4) + "…" + "•".repeat(Math.min(12, t.length - 4));
  }

  function renderTokenDisplay() {
    const el = document.getElementById("status-token-display");
    if (el) el.textContent = maskToken(tokenPlain || token());
    const input = document.getElementById("token-input");
    if (input && document.activeElement !== input) input.value = token();
  }

  async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    const res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({
      ok: false,
      error: { code: "E_FAILED", message: "non-json response " + res.status },
    }));
    if (!res.ok && !data.error) {
      data.ok = false;
      data.error = { code: "E_FAILED", message: "HTTP " + res.status };
    }
    return data;
  }

  async function fetchLocalToken() {
    const res = await fetch("/api/local-token");
    const data = await res.json().catch(() => ({ ok: false }));
    if (!res.ok || !data.ok) return data;
    const t = (data.data && data.data.token) || "";
    if (t) setToken(t);
    return data;
  }

  function showImportSubTab(which) {
    const wantProposals = which === "proposals";
    const tabId = wantProposals ? "tab-import-proposals" : "tab-import-sources";
    const tabEl = document.getElementById(tabId);
    if (tabEl && typeof bootstrap !== "undefined" && bootstrap.Tab) {
      bootstrap.Tab.getOrCreateInstance(tabEl).show();
    } else {
      const srcTab = document.getElementById("tab-import-sources");
      const propTab = document.getElementById("tab-import-proposals");
      const srcPane = document.getElementById("pane-import-sources");
      const propPane = document.getElementById("pane-import-proposals");
      if (srcTab) srcTab.classList.toggle("active", !wantProposals);
      if (propTab) propTab.classList.toggle("active", wantProposals);
      if (srcPane) {
        srcPane.classList.toggle("show", !wantProposals);
        srcPane.classList.toggle("active", !wantProposals);
      }
      if (propPane) {
        propPane.classList.toggle("show", wantProposals);
        propPane.classList.toggle("active", wantProposals);
      }
    }
    if (wantProposals) refreshProposals();
  }

  function showPanel(name) {
    let panelName = name;
    let openProposals = false;
    if (name === "proposals") {
      // R3.29: side-nav removed; map to import + 待确认 tab
      panelName = "import";
      openProposals = true;
    }
    document.querySelectorAll(".panel").forEach((el) => {
      el.classList.toggle("active", el.id === "panel-" + panelName);
    });
    document.querySelectorAll("#tabs [data-panel]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.panel === panelName);
    });
    if (openProposals) showImportSubTab("proposals");
  }

  function afterCompileNavigate(out) {
    // api() does not throw on ok:false — only navigate after successful compile
    if (!out || out.ok === false) return;
    const d = out.data != null ? out.data : out;
    refreshProposals();
    if (!d.auto_accepted) {
      showPanel("import");
      showImportSubTab("proposals");
    }
  }

  function dbg(obj) {
    const el = document.getElementById("settings-out");
    if (el) el.textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  async function refreshStatus() {
    if (!token()) await fetchLocalToken();
    const st = await api("GET", "/api/status");
    document.getElementById("status-out").textContent = JSON.stringify(st, null, 2);
    if (st.ok && st.data && st.data.token) {
      tokenPlain = st.data.token;
      if (!token()) setToken(st.data.token);
      else renderTokenDisplay();
    } else {
      renderTokenDisplay();
    }
  }

  async function loadSettings() {
    if (!token()) await fetchLocalToken();
    const s = await api("GET", "/api/settings");
    dbg(s);
    if (!s.ok) {
      dbg(s);
      return;
    }
    if (s.data) {
      const box = document.getElementById("loop-enabled");
      if (box) box.checked = !!s.data.local_agent_loop_enabled;
      const aut = s.data.autonomy || {};
      const hour = document.getElementById("nightly-hour");
      if (hour && aut.nightly_local_hour != null) hour.value = aut.nightly_local_hour;
      const pe = document.getElementById("pause-evolution");
      if (pe) pe.checked = !!aut.pause_evolution;
      renderPresets(s.data.provider_presets || {});
    }
    await loadModelsForm();
    try {
      await loadWorkbenchPresetsForm();
    } catch (e) {
      dbg({ workbench_presets_form_error: String(e && e.message ? e.message : e) });
    }
    try {
      await refreshSkillsPanel();
    } catch (e) {
      dbg({ skills_panel_error: String(e && e.message ? e.message : e) });
    }
    try {
      await refreshOcStatus();
    } catch (e) {
      dbg({ oc_status_error: String(e && e.message ? e.message : e) });
    }
    activateSettingsTab("tab-models");
  }

  function activateSettingsTab(tabId) {
    const tab = document.getElementById(tabId);
    if (tab && window.bootstrap) {
      window.bootstrap.Tab.getOrCreateInstance(tab).show();
    }
  }

  function probeLightClass(entry) {
    if (!entry || entry.ok == null) return "na";
    if (entry.error === "not_probed_native_use_compat_for_full_suite") return "na";
    if (entry.ok === true) return "ok";
    if (entry.ok === false) return "fail";
    return "na";
  }

  function renderProbeResults(payload) {
    const mount = document.getElementById("probe-results");
    if (!mount) return;
    const wrap = (payload && payload.data) || payload || {};
    const results = wrap.results || {};
    const keys = Object.keys(results);
    if (!keys.length) {
      mount.innerHTML =
        '<div class="alert alert-warning mb-0">无探活结果。请先配置并保存六路路由。</div>';
      return;
    }
    let html =
      '<div class="table-responsive"><table class="table table-sm table-bordered align-middle mb-2">' +
      "<thead><tr><th>路由</th><th>厂商 / 模型</th><th>总体</th>";
    for (const cap of PROBE_CAPS) {
      html += "<th class=\"small\">" + esc(cap) + "</th>";
    }
    html += "<th>错误摘要</th></tr></thead><tbody>";
    for (const core of keys) {
      const r = results[core] || {};
      const overall = r.ok
        ? '<span class="probe-light ok" title="ok"></span> 通'
        : r.error === "model_not_configured" || r.error === "provider_missing"
          ? '<span class="probe-light na" title="未配置"></span> 未配置'
          : '<span class="probe-light fail" title="fail"></span> 失败';
      const probes = r.probes || {};
      html +=
        "<tr><td>" +
        esc(CORE_LABELS[core] || core) +
        "</td><td class=\"small\">" +
        esc((r.provider || "—") + " / " + (r.model || "—")) +
        "</td><td>" +
        overall +
        "</td>";
      for (const cap of PROBE_CAPS) {
        const pr = probes[cap];
        const cls = probeLightClass(pr);
        const tip = pr ? esc(pr.error || (pr.ok ? "ok" : "fail")) : "未探测";
        html +=
          '<td class="text-center"><span class="probe-light ' +
          cls +
          '" title="' +
          tip +
          '"></span></td>';
      }
      const err =
        r.error ||
        (PROBE_CAPS.map((c) => (probes[c] && probes[c].error) || "")
          .filter(Boolean)
          .slice(0, 2)
          .join("; ") ||
          "—");
      html += '<td class="small text-secondary">' + esc(err) + "</td></tr>";
    }
    html += "</tbody></table></div>";
    html +=
      '<details class="small"><summary class="text-secondary">探活原始 JSON</summary>' +
      '<pre class="lw-pre card card-body mt-2">' +
      esc(JSON.stringify(payload, null, 2)) +
      "</pre></details>";
    mount.innerHTML = html;
  }

  function fillProviderEditor(id, p) {
    p = p || {};
    const idEl = document.getElementById("edit-prov-id");
    const kindEl = document.getElementById("edit-prov-kind");
    const baseEl = document.getElementById("edit-prov-base");
    const keyEl = document.getElementById("edit-prov-key");
    const hint = document.getElementById("edit-prov-key-hint");
    const badge = document.getElementById("edit-prov-key-badge");
    if (idEl) idEl.value = id || "";
    if (kindEl) kindEl.value = p.kind || "openai_compatible";
    if (baseEl) {
      baseEl.value =
        p.base_url ||
        (p.kind === "ollama" ? "http://127.0.0.1:11434" : "");
    }
    if (keyEl) keyEl.value = "";
    const hasKey = p.key_configured === true;
    const label = p.key_label || (hasKey ? "已配置 Key" : "未配置 Key");
    if (hint) {
      hint.textContent = hasKey
        ? "当前引用：" + (p.api_key || "") + "（密钥已存本机密钥库，不会回显明文）"
        : p.kind === "ollama"
          ? "本地 Ollama 无需 API Key"
          : "当前引用：（无 — 在线模型请填写 API Key 后点保存）";
    }
    if (badge) {
      if (p.kind === "ollama" || p.key_required === false) {
        badge.className = "badge text-bg-secondary ms-2";
        badge.textContent = "本地无需 Key";
      } else if (hasKey) {
        badge.className = "badge text-bg-success ms-2";
        badge.textContent = label;
      } else {
        badge.className = "badge text-bg-warning ms-2";
        badge.textContent = label;
      }
      badge.classList.remove("d-none");
    }
  }

  function renderPresets(presets) {
    const mount = document.getElementById("preset-buttons");
    if (!mount) return;
    mount.innerHTML = "";
    const labels = {
      ollama_local: "Ollama 本地",
      openai: "OpenAI",
      anthropic: "Claude",
      deepseek: "DeepSeek",
      qwen: "通义千问",
    };
    Object.keys(presets).forEach((id) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm btn-outline-primary rounded-pill px-3";
      btn.textContent = labels[id] || id;
      btn.onclick = () => {
        applyPreset(id, presets[id]);
        // jump to models tab
        const tab = document.getElementById("tab-models");
        if (tab && window.bootstrap) {
          window.bootstrap.Tab.getOrCreateInstance(tab).show();
        }
      };
      mount.appendChild(btn);
    });
  }

  function applyPreset(id, preset) {
    fillProviderEditor(id, {
      kind: preset.kind,
      location: preset.location,
      base_url: preset.base_url || "",
      api_key: preset.api_key || "",
    });
    if (preset.kind === "ollama" && preset.base_url) {
      const ou = document.getElementById("ollama-base-url");
      if (ou) ou.value = preset.base_url;
    }
  }

  function renderProvidersForm() {
    const mount = document.getElementById("providers-form");
    if (!mount) return;
    const providers = modelState.providers || {};
    const ids = Object.keys(providers);
    if (!ids.length) {
      mount.innerHTML =
        '<div class="alert alert-info mb-0">尚无已保存厂商。请在上方填写「厂商 ID / API 类型 / 访问地址 / API Key」后点保存，或点快速预设。</div>';
      return;
    }
    mount.innerHTML = ids
      .map((id) => {
        const p = providers[id] || {};
        const configured = p.key_configured === true;
        const ollama = p.kind === "ollama" || p.key_required === false;
        const badgeClass = ollama
          ? "text-bg-secondary"
          : configured
            ? "text-bg-success"
            : "text-bg-warning";
        const badgeText = ollama
          ? "本地无需 Key"
          : p.key_label || (configured ? "已配置 Key" : "未配置 Key");
        return (
          '<button type="button" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center provider-chip" data-load-prov="' +
          esc(id) +
          '">' +
          "<span><strong>" +
          esc(id) +
          '</strong> <span class="text-secondary small ms-2">' +
          esc(p.kind || "") +
          " · " +
          esc(p.base_url || "（无地址）") +
          "</span></span>" +
          '<span class="badge ' +
          badgeClass +
          '">' +
          esc(badgeText) +
          "</span></button>"
        );
      })
      .join("");
    mount.querySelectorAll("[data-load-prov]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.getAttribute("data-load-prov");
        fillProviderEditor(id, (modelState.providers || {})[id]);
        const st = document.getElementById("edit-prov-save-status");
        if (st) st.innerHTML = "";
      };
    });
  }

  async function saveOneProvider() {
    const id = (document.getElementById("edit-prov-id").value || "").trim();
    const kind = document.getElementById("edit-prov-kind").value;
    const base_url = (document.getElementById("edit-prov-base").value || "").trim();
    const api_key_value = document.getElementById("edit-prov-key").value;
    const statusEl = document.getElementById("edit-prov-save-status");
    if (!id) {
      alert("请填写厂商 ID");
      return;
    }
    if (!base_url && kind !== "anthropic") {
      alert("请填写模型访问地址（Base URL）");
      return;
    }
    if (kind !== "ollama" && !api_key_value) {
      const prev = (modelState.providers || {})[id];
      if (!(prev && prev.key_configured)) {
        if (!confirm("未填写 API Key，且该厂商密钥库中尚无有效密钥。仍要保存吗？")) return;
      }
    }
    const row = {
      kind,
      location: kind === "ollama" ? "local" : "online",
      base_url,
      api_key: ((modelState.providers || {})[id] || {}).api_key || "",
    };
    if (api_key_value) {
      row.api_key_value = api_key_value;
      row.secret_name = id;
    }
    const out = await api("POST", "/api/settings/models", {
      providers: { [id]: row },
      routes: {},
    });
    dbg(out);
    if (!out.ok) {
      alert("保存失败: " + ((out.error && out.error.message) || "unknown"));
      return;
    }
    document.getElementById("edit-prov-key").value = "";
    delete providerModelsCache[id];
    await loadModelsForm();
    fillProviderEditor(id, (modelState.providers || {})[id]);
    const saved = (modelState.providers || {})[id] || {};
    const keyOk = saved.key_configured === true;
    if (statusEl) {
      statusEl.innerHTML = keyOk
        ? '<div class="alert alert-success py-2 mb-0 mt-2">已保存。标识：<strong>已配置 Key</strong>（' +
          esc(saved.api_key || "") +
          "）</div>"
        : saved.kind === "ollama"
          ? '<div class="alert alert-success py-2 mb-0 mt-2">已保存本地厂商（无需 API Key）。</div>'
          : '<div class="alert alert-warning py-2 mb-0 mt-2">已保存厂商，但密钥库中尚无有效 API Key — 请填写后再次保存。</div>';
    }
  }

  /** Keep for route save — merge editor state into known providers */
  function collectProvidersFromForm() {
    return Object.assign({}, modelState.providers || {});
  }

  async function refreshOllama() {
    const st = document.getElementById("ollama-status");
    const pick = document.getElementById("ollama-model-pick");
    const baseInput = document.getElementById("ollama-base-url");
    const base = (baseInput && baseInput.value.trim()) || "http://127.0.0.1:11434";
    if (!token()) await fetchLocalToken();
    const url =
      "/api/settings/models/ollama-tags?base_url=" + encodeURIComponent(base);
    const tags = await api("GET", url);
    if (!tags.ok) {
      const msg =
        (tags.error && tags.error.message) ||
        "HTTP/鉴权失败 — 请先在「连接」页从服务填入 Token";
      let tip = "Ollama 列表失败: " + msg;
      if (String(msg).indexOf("not in Personal Core") >= 0) {
        tip +=
          " —— 当前 serve 进程过旧，请关闭后重新运行 start-localwiki.cmd / python -m localwiki serve，再 Ctrl+F5 刷新页面。";
      }
      if (st) st.innerHTML = '<span class="text-danger">' + esc(tip) + "</span>";
      if (pick) pick.innerHTML = "";
      ollamaModels = [];
      renderRoutesForm();
      return;
    }
    const d = tags.data || {};
    ollamaModels = (d.models || []).map((m) => m.name).filter(Boolean);
    Object.keys(modelState.providers || {}).forEach((pid) => {
      const p = modelState.providers[pid];
      if (p && p.kind === "ollama") delete providerModelsCache[pid];
    });
    if (st) {
      if (d.error) {
        st.textContent =
          "无法连接 " + (d.base_url || base) + " — " + d.error + "（请确认 Ollama 已启动）";
      } else {
        st.textContent =
          "已连接 " + (d.base_url || base) + " · 共 " + ollamaModels.length + " 个模型";
      }
    }
    if (pick) {
      pick.innerHTML = ollamaModels
        .map((n) => '<option value="' + esc(n) + '">' + esc(n) + "</option>")
        .join("");
      if (!ollamaModels.length) {
        pick.innerHTML = '<option disabled>（无模型）</option>';
      }
    }
    // ensure ollama provider exists with this base
    if (!modelState.providers) modelState.providers = {};
    if (!modelState.providers.ollama_local) {
      modelState.providers.ollama_local = {
        kind: "ollama",
        location: "local",
        base_url: base,
        api_key: "",
      };
      renderProvidersForm();
    } else {
      modelState.providers.ollama_local.base_url = base;
      const card = document.querySelector('#providers-form [data-prov-id="ollama_local"]');
      if (card) {
        const bu = card.querySelector('[data-field="base_url"]');
        if (bu) bu.value = base;
      }
    }
    renderRoutesForm();
  }

  async function loadModelsForm() {
    const m = await api("GET", "/api/settings/models");
    dbg(m);
    if (!m.ok || !m.data) return;
    modelState = {
      routes: m.data.routes || {},
      providers: m.data.providers || {},
      core_routes: m.data.core_routes || Object.keys(CORE_LABELS),
    };
    const oll = modelState.providers.ollama_local;
    const ou = document.getElementById("ollama-base-url");
    if (ou && oll && oll.base_url) ou.value = oll.base_url;
    renderProvidersForm();
    const ids = Object.keys(modelState.providers || {});
    if (ids.length) {
      fillProviderEditor(ids[0], modelState.providers[ids[0]]);
    } else {
      fillProviderEditor("ollama_local", {
        kind: "ollama",
        base_url: "http://127.0.0.1:11434",
        api_key: "",
      });
    }
    await refreshOllama();
    await prefetchRouteProviderModels();
  }

  async function fetchProviderModels(providerId) {
    const pid = (providerId || "").trim();
    if (!pid) return [];
    if (Object.prototype.hasOwnProperty.call(providerModelsCache, pid)) {
      return providerModelsCache[pid];
    }
    let names = [];
    try {
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), 6000) : null;
      const headers = { "Content-Type": "application/json" };
      const t = token();
      if (t) headers["Authorization"] = "Bearer " + t;
      const res = await fetch(
        "/api/settings/models/provider-models?provider=" + encodeURIComponent(pid),
        { headers, signal: ctrl ? ctrl.signal : undefined }
      );
      if (timer) clearTimeout(timer);
      const out = await res.json().catch(() => ({ ok: false }));
      if (out.ok && out.data) {
        names = (out.data.models || []).map((m) => m.name || m).filter(Boolean);
      }
    } catch (_e) {
      names = [];
    }
    const prov = (modelState.providers || {})[pid] || {};
    if (prov.kind === "ollama" && ollamaModels.length && !names.length) {
      names = ollamaModels.slice();
    }
    providerModelsCache[pid] = names;
    return names;
  }

  async function prefetchRouteProviderModels() {
    // Only providers actually used by CORE routes — not every configured vendor
    const ids = new Set();
    Object.values(modelState.routes || {}).forEach((r) => {
      const p = r && r.primary && r.primary.provider;
      if (p) ids.add(p);
    });
    if (!ids.size) {
      Object.keys(modelState.providers || {}).slice(0, 3).forEach((p) => ids.add(p));
    }
    await Promise.all(
      Array.from(ids).map((id) =>
        fetchProviderModels(id).catch(() => providerModelsCache[id] || [])
      )
    );
    renderRoutesForm();
  }

  function modelOptionsHtml(names, cur) {
    const opts = names.slice();
    if (cur && opts.indexOf(cur) < 0) opts.unshift(cur);
    if (!opts.length) {
      return (
        '<input class="form-control form-control-sm" data-field="model" value="' +
        esc(cur || "") +
        '" placeholder="模型名，如 gpt-4o / deepseek-chat" />'
      );
    }
    return (
      '<select class="form-select form-select-sm" data-field="model">' +
      opts
        .map(
          (n) =>
            '<option value="' +
            esc(n) +
            '"' +
            (n === cur ? " selected" : "") +
            ">" +
            esc(n) +
            "</option>"
        )
        .join("") +
      "</select>"
    );
  }

  function renderRoutesForm() {
    const mount = document.getElementById("routes-form");
    if (!mount) return;
    let providerIds = Object.keys(modelState.providers || {});
    if (!providerIds.length) providerIds = ["ollama_local"];
    const routes = modelState.core_routes || Object.keys(CORE_LABELS);
    mount.innerHTML = "";
    routes.forEach((core) => {
      const r = modelState.routes[core] || {};
      const primary = r.primary || {};
      const provName = primary.provider || providerIds[0];
      const wrap = document.createElement("div");
      wrap.className = "row g-2 align-items-center border rounded p-2 bg-white";
      wrap.dataset.route = core;

      const provSel =
        '<select class="form-select form-select-sm" data-field="provider">' +
        providerIds
          .map(
            (p) =>
              '<option value="' +
              esc(p) +
              '"' +
              (p === provName ? " selected" : "") +
              ">" +
              esc(p) +
              "</option>"
          )
          .join("") +
        "</select>";

      const prov = (modelState.providers || {})[provName] || {};
      let names = providerModelsCache[provName];
      if (!names) {
        names =
          prov.kind === "ollama" && ollamaModels.length ? ollamaModels.slice() : [];
      }
      let cur = primary.model || "";
      if (cur && names.length && names.indexOf(cur) < 0) {
        // Stale model from another vendor — drop so user picks from this vendor
        cur = names[0] || "";
        if (!modelState.routes[core]) modelState.routes[core] = {};
        modelState.routes[core].primary = Object.assign({}, primary, {
          provider: provName,
          model: cur,
        });
      } else if (!cur && names.length) {
        cur = names[0];
      }
      const modelField = modelOptionsHtml(names, cur);

      wrap.innerHTML =
        '<div class="col-md-2 fw-semibold text-primary">' +
        esc(CORE_LABELS[core] || core) +
        "</div>" +
        '<div class="col-md-4"><label class="form-label small mb-0">厂商</label>' +
        provSel +
        "</div>" +
        '<div class="col-md-6"><label class="form-label small mb-0">模型</label>' +
        modelField +
        "</div>";

      const sel = wrap.querySelector('[data-field="provider"]');
      sel.addEventListener("change", async () => {
        const routesSnap = {};
        document.querySelectorAll("#routes-form [data-route]").forEach((row) => {
          const c = row.dataset.route;
          const newProv = row.querySelector('[data-field="provider"]').value;
          let model = (row.querySelector('[data-field="model"]') || {}).value || "";
          // Clear model when this row's provider changed — will re-pick from list
          if (c === core) model = "";
          routesSnap[c] = {
            primary: {
              provider: newProv,
              model: model,
            },
          };
        });
        modelState.routes = Object.assign({}, modelState.routes, routesSnap);
        await fetchProviderModels(routesSnap[core].primary.provider);
        renderRoutesForm();
      });
      mount.appendChild(wrap);
    });
  }

  async function saveModelsForm() {
    const providers = collectProvidersFromForm();
    const routes = {};
    document.querySelectorAll("#routes-form [data-route]").forEach((row) => {
      const core = row.dataset.route;
      const provider = row.querySelector('[data-field="provider"]').value;
      const model = row.querySelector('[data-field="model"]').value;
      routes[core] = {
        primary: { provider, model },
        fallback: (modelState.routes[core] || {}).fallback || [],
        privacy: (modelState.routes[core] || {}).privacy || "content",
      };
    });
    const out = await api("POST", "/api/settings/models", { providers, routes });
    dbg(out);
    if (!out.ok) {
      alert("保存失败: " + ((out.error && out.error.message) || "unknown"));
      return;
    }
    await loadModelsForm();
  }

  async function probeModels() {
    const out = await api("POST", "/api/settings/models/probe", {});
    dbg(out);
    renderProbeResults(out);
    activateSettingsTab("tab-routes");
  }

  async function syncModels() {
    const out = await api("POST", "/api/settings/models/sync", {
      source_route: "agent",
    });
    dbg(out);
    await loadModelsForm();
  }

  async function saveAutonomy() {
    const out = await api("POST", "/api/settings", {
      local_agent_loop_enabled: document.getElementById("loop-enabled").checked,
      nightly_local_hour: Number(document.getElementById("nightly-hour").value || 3),
      pause_evolution: document.getElementById("pause-evolution").checked,
    });
    dbg(out);
  }

  async function refreshSkillsPanel() {
    const data = await api("GET", "/api/skills");
    dbg(data);
    const align = document.getElementById("skills-align");
    const d = data.data || data;
    if (align) {
      align.textContent = d.opencode_skills_aligned
        ? "OpenCode skills 路径对齐：" + (d.opencode_skills_path || "")
        : "对齐警告：" + (d.alignment_warning || "unknown");
    }
    const mount = document.getElementById("skills-installed");
    if (window.LocalWikiSkills && mount) {
      const items = [].concat(d.skills || [], d.harness || d.harnesses || []);
      window.LocalWikiSkills.renderInstalledSkills(items, mount);
    }
  }

  async function refreshGithubTokenStatus() {
    const el = document.getElementById("skills-github-token-status");
    if (!el) return;
    const st = await api("GET", "/api/settings");
    const ok = !!(st.ok && st.data && st.data.github_token_configured);
    el.textContent = ok ? "已配置" : "未配置";
    el.className = "small align-self-center " + (ok ? "text-success" : "text-secondary");
  }

  async function saveGithubToken() {
    const inp = document.getElementById("skills-github-token");
    const tok = (inp && inp.value.trim()) || "";
    if (!tok) {
      alert("请填写 GitHub Token");
      return;
    }
    const out = await api("POST", "/api/settings/github-token", { github_token: tok });
    dbg(out);
    if (inp) inp.value = "";
    if (out.ok) {
      await refreshGithubTokenStatus();
      alert("GitHub Token 已保存到本机密钥库");
    } else {
      alert((out.error && out.error.message) || "保存失败");
    }
  }

  async function clearGithubToken() {
    const out = await api("POST", "/api/settings/github-token", { clear: true });
    dbg(out);
    await refreshGithubTokenStatus();
    if (out.ok) alert("已清除 GitHub Token");
  }

  async function searchSkills() {
    const q = document.getElementById("skills-query").value.trim();
    const out = await api("POST", "/api/skills/search", { query: q, offline: false });
    dbg(out);
    const mount = document.getElementById("skills-search-out");
    if (window.LocalWikiSkills && mount && out.ok) {
      window.LocalWikiSkills.renderSearchResults(out.data || out, mount);
    }
  }

  async function installFromSource(kind) {
    const raw = document.getElementById("skills-source").value.trim();
    const path = document.getElementById("skills-path").value.trim();
    const body = { kind };
    if (raw.indexOf("github.com") >= 0) body.source_url = raw;
    else if (raw.indexOf("/") >= 0) {
      body.repo = raw;
      if (path) body.path = path;
      body.ref = "main";
    } else {
      dbg("请填写 GitHub URL 或 org/repo");
      return;
    }
    if (path && !body.path) body.path = path;
    const out = await api("POST", "/api/skills/install", body);
    dbg(out);
    await refreshSkillsPanel();
  }

  async function installFromSearchHit(btn) {
    const name = btn.getAttribute("data-name") || "";
    const repo = (btn.getAttribute("data-repo") || "").trim();
    const path = (btn.getAttribute("data-path") || "").trim();
    const url = (btn.getAttribute("data-url") || "").trim();
    const kind = btn.getAttribute("data-kind") || "skill";
    const fakeLocal = repo.indexOf("localwiki/") === 0;
    const hasGhUrl = !!(url && url.indexOf("github.com") >= 0);
    if (fakeLocal || (!repo && !hasGhUrl)) {
      alert(
        "该项无可安装源（仅策展推荐或缺少真实 GitHub repo）。请换可安装结果，或用下方 GitHub URL 安装。"
      );
      return;
    }
    const body = { kind, ref: "main", acknowledge_high_risk: false };
    if (repo) {
      body.repo = repo;
      if (path) body.path = path;
    } else {
      body.source_url = url;
      if (path) body.path = path;
    }
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = "安装中…";
    try {
      let out = await api("POST", "/api/skills/install", body);
      dbg(out);
      const payload = out.data || out;
      const blocked = !!(payload && (payload.blocked || payload.block_install));
      const needHigh =
        payload &&
        (payload.max_level === "high" ||
          (payload.scan && payload.scan.max_level === "high"));
      if ((!out.ok || blocked) && needHigh) {
        const ok = confirm(
          "「" + name + "」标记为高风险。确认仍要安装？\n" +
            ((payload && payload.message) || "")
        );
        if (!ok) {
          alert("已取消安装");
          return;
        }
        body.acknowledge_high_risk = true;
        out = await api("POST", "/api/skills/install", body);
        dbg(out);
      }
      if (out.ok) {
        alert("已安装：" + (name || repo || url));
        await refreshSkillsPanel();
      } else {
        alert(
          (out.error && out.error.message) ||
            (payload && payload.message) ||
            "安装失败"
        );
      }
    } finally {
      btn.disabled = false;
      btn.textContent = prev || "申请安装";
    }
  }

  function wireSkillsSearchInstall() {
    const mount = document.getElementById("skills-search-out");
    if (!mount || mount._lwInstallWired) return;
    mount._lwInstallWired = true;
    mount.addEventListener("click", (ev) => {
      const t = ev.target;
      if (!t || !t.getAttribute) return;
      if (t.getAttribute("data-action") !== "install-request") return;
      if (t.disabled) {
        alert("该项无可安装源");
        return;
      }
      installFromSearchHit(t);
    });
  }

  async function bootstrapOfficeSkills() {
    const out = await api("POST", "/api/skills/bootstrap-office", { force: false });
    dbg(out);
    await refreshSkillsPanel();
    if (out.ok) {
      alert(
        "办公技能包：已装 " +
          ((out.data && out.data.installed) || []).join(", ") +
          "；跳过 " +
          ((out.data && out.data.skipped) || []).join(", ")
      );
    } else {
      alert((out.error && out.error.message) || "安装失败");
    }
  }

  /** Safe href for workbench links (R3.26 generic linkify). */
  function wbSafeHref(href) {
    const h = String(href || "").trim();
    if (!h) return "";
    if (h.startsWith("/")) {
      // same-origin relative path
      if (h.startsWith("//")) return "";
      return h;
    }
    if (/^https?:\/\//i.test(h)) return h;
    return "";
  }

  /** Export filename from /api/exports/<name> (auth download; bare <a> has no Bearer). */
  function wbExportName(href) {
    const h = String(href || "").trim();
    const m = h.match(/^\/api\/exports\/([A-Za-z0-9._-]+)$/);
    return m ? m[1] : "";
  }

  function wbAnchor(href, label) {
    const safe = wbSafeHref(href);
    if (!safe) return esc(label || href || "");
    const lab = esc(label || href);
    const exportName = wbExportName(safe);
    if (exportName) {
      return (
        '<a class="wb-link wb-export-link" href="' +
        esc(safe) +
        '" data-export="' +
        esc(exportName) +
        '" title="下载 ' +
        esc(exportName) +
        '">' +
        lab +
        "</a>"
      );
    }
    return (
      '<a class="wb-link" href="' +
      esc(safe) +
      '" target="_blank" rel="noopener noreferrer">' +
      lab +
      "</a>"
    );
  }

  /** Turn assistant/user plain or Markdown-ish text into safe HTML for the workbench. */
  function wbToHtml(raw) {
    const text = String(raw == null ? "" : raw);
    const lines = text.replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let listItems = [];
    const flushList = () => {
      if (!listItems.length) return;
      blocks.push(
        "<ul class=\"wb-ul\">" +
          listItems.map((t) => "<li>" + inlineMd(t) + "</li>").join("") +
          "</ul>"
      );
      listItems = [];
    };
    const flushPara = (buf) => {
      const t = buf.join(" ").trim();
      if (t) blocks.push("<p class=\"wb-p\">" + inlineMd(t) + "</p>");
    };
    let para = [];
    for (const line of lines) {
      const s = line.trim();
      if (!s) {
        flushList();
        flushPara(para);
        para = [];
        continue;
      }
      // R3.16: export download lines stay for attachExportDownloadButtons (skip text dup)
      if (/^\/api\/exports\//.test(s)) {
        flushList();
        flushPara(para);
        para = [];
        continue;
      }
      if (/^\[下载[^\]]*\]\(\/api\/exports\//.test(s)) {
        flushList();
        flushPara(para);
        para = [];
        continue;
      }
      // R3.26: whole-line URL or same-origin path → clickable paragraph
      if (/^https?:\/\//i.test(s) || /^`https?:\/\/[^`]+`$/i.test(s)) {
        flushList();
        flushPara(para);
        para = [];
        const bare = s.replace(/^`|`$/g, "");
        blocks.push("<p class=\"wb-p\">" + wbAnchor(bare, bare) + "</p>");
        continue;
      }
      if (/^\/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$/.test(s) || /^`\/[^`]+`$/.test(s)) {
        flushList();
        flushPara(para);
        para = [];
        const bare = s.replace(/^`|`$/g, "");
        blocks.push("<p class=\"wb-p\">" + wbAnchor(bare, bare) + "</p>");
        continue;
      }
      const heading = s.match(/^#{1,3}\s+(.+)$/);
      if (heading) {
        flushList();
        flushPara(para);
        para = [];
        blocks.push("<h4 class=\"wb-h\">" + inlineMd(heading[1]) + "</h4>");
        continue;
      }
      const bullet = s.match(/^[-*•]\s+(.+)$/);
      if (bullet) {
        flushPara(para);
        para = [];
        listItems.push(bullet[1]);
        continue;
      }
      flushList();
      para.push(s);
    }
    flushList();
    flushPara(para);
    return blocks.join("") || "<p class=\"wb-p\">" + inlineMd(text) + "</p>";
  }

  function inlineMd(s) {
    let t = esc(s);
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // fenced-ish inline code that is a URL or path → link (keep code look via class)
    t = t.replace(/`([^`]+)`/g, function (_m, inner) {
      const raw = String(inner);
      if (/^https?:\/\//i.test(raw) || (raw.startsWith("/") && !raw.startsWith("//"))) {
        return wbAnchor(raw, raw);
      }
      return "<code>" + esc(raw) + "</code>";
    });
    // Markdown export links: same auth path as download buttons (not bare <a>)
    t = t.replace(
      /\[([^\]]+)\]\((\/api\/exports\/[A-Za-z0-9._-]+)\)/g,
      function (_m, lab, href) {
        return wbAnchor(href, lab);
      }
    );
    t = t.replace(/\[([^\]]+)\]\((\/[^)\s]+)\)/g, function (_m, lab, href) {
      return wbAnchor(href, lab);
    });
    t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, function (_m, lab, href) {
      return wbAnchor(href, lab);
    });
    // bare URLs (avoid matching already-linked via crude pass on remaining text)
    t = t.replace(
      /(^|[^"'>])(https?:\/\/[^\s<]+)/gi,
      function (_m, pre, url) {
        return pre + wbAnchor(url, url);
      }
    );
    t = t.replace(
      /(^|[^"'>])(\/(?:demo|api|wiki)\/[^\s<]+)/g,
      function (_m, pre, path) {
        return pre + wbAnchor(path, path);
      }
    );
    return t;
  }

  function fillWbMsg(el, role, content, opts) {
    if (!el) return;
    const fail = opts && opts.fail;
    const streaming = opts && opts.streaming;
    const statusLine = (opts && opts.statusLine) || "";
    el.className =
      "wb-msg " +
      (role === "user" ? "user" : "assistant") +
      (fail ? " fail" : "");
    const label =
      role === "user" ? "你" : fail ? "助手（失败）" : streaming ? "助手（生成中）" : "助手";
    const statusHtml =
      role === "assistant" && statusLine
        ? '<p class="wb-status">' + esc(statusLine) + "</p>"
        : "";
    const body =
      role === "assistant" || role === "user"
        ? wbToHtml(content)
        : "<p class=\"wb-p\">" + esc(content) + "</p>";
    el.innerHTML =
      '<div class="wb-role">' +
      esc(label) +
      '</div><div class="wb-body">' +
      statusHtml +
      body +
      "</div>";
    if (role === "assistant" && content && !streaming) {
      attachExportDownloadButtons(el, content);
    }
  }

  async function downloadExport(name) {
    const tok = token();
    const tryName = name;
    const res = await fetch("/api/exports/" + encodeURIComponent(tryName), {
      headers: { Authorization: "Bearer " + tok },
    });
    if (!res.ok) {
      alert("下载失败 HTTP " + res.status + "（文件尚未生成，请勿使用旧文件冒充）");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = tryName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function exportButtonLabel(n) {
    if (/\.mp3$/i.test(n)) return "下载 " + n;
    if (/\.wav$/i.test(n)) return "下载 " + n;
    if (/\.docx$/i.test(n)) return "下载 " + n;
    if (/\.xlsx$/i.test(n)) return "下载 " + n;
    return "下载 " + n;
  }

  function wireExportLinks(container) {
    if (!container) return;
    container.querySelectorAll("a.wb-export-link").forEach((a) => {
      if (a.dataset.wired === "1") return;
      a.dataset.wired = "1";
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const n = a.getAttribute("data-export") || wbExportName(a.getAttribute("href") || "");
        if (n) downloadExport(n);
      });
    });
  }

  function attachExportDownloadButtons(container, content) {
    const re = /\/api\/exports\/([A-Za-z0-9._-]+)/g;
    const seen = {};
    let m;
    const src = String(content || "");
    const actions = document.createElement("div");
    actions.className = "wb-exports mt-2";
    let any = false;
    while ((m = re.exec(src))) {
      const n = m[1];
      if (seen[n]) continue;
      seen[n] = true;
      any = true;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm btn-outline-primary me-1 mt-1";
      btn.textContent = exportButtonLabel(n);
      btn.onclick = () => downloadExport(n);
      actions.appendChild(btn);
    }
    // R3.15: never invent export.docx / materialize when no real link
    if (any) container.appendChild(actions);
    // Inline /api/exports/ anchors need Bearer; wire to downloadExport (not navigate)
    wireExportLinks(container);
  }

  async function refreshEvolution() {
    document.getElementById("evolution-out").textContent = JSON.stringify(
      await api("GET", "/api/evolution/status"),
      null,
      2
    );
  }

  async function refreshProposals() {
    wireProposalsSelectAll();
    const selAll = document.getElementById("proposals-select-all");
    if (selAll) selAll.checked = false;
    const ul = document.getElementById("proposal-list");
    ul.innerHTML = "";
    const data = await api("GET", "/api/proposals");
    if (!data.ok) {
      ul.innerHTML =
        '<li class="list-group-item text-danger">' +
        esc((data.error && data.error.message) || "error") +
        "</li>";
      return;
    }
    const all = data.data || [];
    const pendingItems = all.filter((p) => p.state === "ready" || p.state === "draft");
    const items = showAllProposals ? all : pendingItems;
    const hint = document.getElementById("proposals-filter-hint");
    if (hint) {
      hint.textContent =
        "待确认 " +
        pendingItems.length +
        " 条 · 全部 " +
        all.length +
        " 条" +
        (showAllProposals ? "（含已处理）" : "（仅待确认可勾选）");
    }
    if (!items.length) {
      ul.innerHTML =
        '<li class="list-group-item text-secondary">' +
        (pendingItems.length === 0 && !showAllProposals
          ? "暂无待确认提案。已处理的提案默认隐藏；可勾选「显示已处理」。"
          : "暂无提案") +
        "</li>";
      return;
    }
    for (const p of items) {
      const li = document.createElement("li");
      li.className = "list-group-item";
      const row = document.createElement("div");
      row.className = "d-flex gap-2 align-items-start";
      const pending = p.state === "ready" || p.state === "draft";
      if (pending) {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "form-check-input mt-1 proposal-check";
        cb.value = p.change_uid;
        row.appendChild(cb);
      }
      const meta = document.createElement("div");
      meta.className = "flex-grow-1";
      let gateNote = "";
      if (p.gate) {
        const bits = [];
        if (p.gate.needs_review) bits.push("需复核");
        const acts = Array.isArray(p.gate.actions) ? p.gate.actions : [];
        if (acts.some((a) => a && a.action === "drop_create")) bits.push("已去重挂链");
        else if (acts.some((a) => a && a.action === "inject_link")) bits.push("已挂链");
        else if (acts.some((a) => a && a.action === "propose")) bits.push("疑似重复");
        if (bits.length) gateNote = " · " + bits.join(" / ");
      }
      meta.innerHTML =
        "<strong>" +
        esc(p.purpose || p.change_uid) +
        '</strong><br/><span class="text-secondary small">' +
        esc(p.change_uid) +
        " · " +
        esc(p.state) +
        esc(gateNote) +
        "</span>";
      row.appendChild(meta);
      if (pending) {
        const actions = document.createElement("div");
        actions.className = "d-flex gap-2";
        const accept = document.createElement("button");
        accept.className = "btn btn-sm btn-success";
        accept.textContent = "接受";
        accept.onclick = async () => {
          await api("POST", "/api/review", { uid: p.change_uid, action: "accept" });
          refreshProposals();
          refreshStatus();
        };
        const reject = document.createElement("button");
        reject.className = "btn btn-sm btn-outline-danger";
        reject.textContent = "拒绝";
        reject.onclick = async () => {
          await api("POST", "/api/review", { uid: p.change_uid, action: "reject" });
          refreshProposals();
        };
        actions.appendChild(accept);
        actions.appendChild(reject);
        row.appendChild(actions);
      }
      li.appendChild(row);
      ul.appendChild(li);
    }
  }

  async function batchReview(action) {
    const uids = Array.from(document.querySelectorAll(".proposal-check:checked")).map(
      (el) => el.value
    );
    if (!uids.length) {
      alert("请先勾选提案");
      return;
    }
    await api("POST", "/api/review/batch", { uids, action });
    const selAll = document.getElementById("proposals-select-all");
    if (selAll) selAll.checked = false;
    refreshProposals();
    refreshStatus();
  }

  function wireProposalsSelectAll() {
    const selAll = document.getElementById("proposals-select-all");
    if (!selAll || selAll.dataset.bound) return;
    selAll.dataset.bound = "1";
    selAll.addEventListener("change", () => {
      document.querySelectorAll(".proposal-check").forEach((el) => {
        el.checked = selAll.checked;
      });
    });
  }

  async function showPagePreview(path, previewId) {
    const el = document.getElementById(previewId || "page-preview");
    if (!el) return;
    const c = await api(
      "GET",
      "/api/pages/content?path=" + encodeURIComponent(path)
    );
    const text = c.ok ? c.data.content : JSON.stringify(c, null, 2);
    if (el.tagName === "TEXTAREA") {
      el.value = text;
      if (previewId === "page-preview" || !previewId) {
        wbPagePath = c.ok ? path : null;
        const btn = document.getElementById("btn-save-page");
        if (btn) btn.disabled = !c.ok;
        const err = document.getElementById("page-save-err");
        if (err) err.textContent = "";
        document.querySelectorAll("#page-list .wiki-page-item").forEach((li) => {
          li.classList.toggle("active", li.dataset.path === path);
        });
        if (c.ok) refreshEgoGraph(path);
        else clearEgoGraph();
      }
    } else {
      el.textContent = text;
    }
  }

  async function savePage() {
    const err = document.getElementById("page-save-err");
    const ta = document.getElementById("page-preview");
    if (!wbPagePath || !ta) return;
    if (err) err.textContent = "";
    const out = await api("POST", "/api/pages/content", {
      path: wbPagePath,
      content: ta.value,
    });
    if (!out.ok) {
      if (err) {
        err.textContent =
          (out.error && out.error.message) || "保存失败（编辑内容已保留）";
      }
      return;
    }
    if (err) err.textContent = "已保存";
    refreshPages();
  }

  function clearEgoGraph() {
    const empty = document.getElementById("wiki-ego-empty");
    if (empty) {
      empty.classList.remove("d-none");
      empty.textContent = "选择左侧页面后显示关联节点";
    }
    if (wikiEgoCy) {
      try {
        wikiEgoCy.destroy();
      } catch (_e) {
        /* ignore */
      }
      wikiEgoCy = null;
    }
  }

  async function refreshEgoGraph(focusPath) {
    const host = document.getElementById("wiki-ego-cy");
    const empty = document.getElementById("wiki-ego-empty");
    if (!host || !focusPath) {
      clearEgoGraph();
      return;
    }
    if (typeof cytoscape !== "function") {
      if (empty) {
        empty.classList.remove("d-none");
        empty.textContent = "cytoscape 未加载";
      }
      return;
    }
    const res = await api(
      "GET",
      "/api/wiki/graph?view=ego&focus=" +
        encodeURIComponent(focusPath) +
        "&weak=0"
    );
    if (!res.ok) {
      if (empty) {
        empty.classList.remove("d-none");
        empty.textContent =
          (res.error && res.error.message) || "关联图加载失败";
      }
      return;
    }
    const data = res.data || {};
    const nodes = data.nodes || [];
    const edges = data.edges || [];
    if (empty) {
      empty.classList.toggle("d-none", nodes.length > 0);
      if (!nodes.length) empty.textContent = "无关联节点（仅当前页）";
    }
    if (wikiEgoCy) {
      try {
        wikiEgoCy.destroy();
      } catch (_e) {
        /* ignore */
      }
      wikiEgoCy = null;
    }
    const elements = [];
    for (const n of nodes) {
      elements.push({
        data: {
          id: n.id || n.path,
          label: n.label || n.title || n.path,
          path: n.path,
          kind: n.kind || "page",
          focus: n.focus ? 1 : 0,
        },
      });
    }
    for (const e of edges) {
      elements.push({
        data: {
          id: (e.source || "") + "->" + (e.target || ""),
          source: e.source,
          target: e.target,
        },
      });
    }
    wikiEgoCy = cytoscape({
      container: host,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 11,
            "background-color": "#1877f2",
            color: "#1c1e21",
            "text-wrap": "wrap",
            "text-max-width": 80,
            width: 28,
            height: 28,
          },
        },
        {
          selector: "node[focus = 1]",
          style: { "background-color": "#e7a33c", width: 34, height: 34 },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#bcc0c4",
            "curve-style": "bezier",
          },
        },
      ],
      layout: {
        name: nodes.length <= 8 ? "concentric" : "breadthfirst",
        animate: false,
        padding: 16,
        concentric: (node) => (node.data("focus") ? 2 : 1),
        levelWidth: () => 1,
        directed: false,
        spacingFactor: 1.1,
      },
    });
    wikiEgoCy.on("tap", "node", (ev) => {
      const p = ev.target.data("path");
      if (p) showPagePreview(p, "page-preview");
    });
  }

  function fillPageTypeFilter(pages) {
    const sel = document.getElementById("page-type-filter");
    if (!sel) return;
    const prev = sel.value;
    const ids = new Set();
    for (const p of pages || []) {
      ids.add(p.dir_category || "other");
    }
    sel.innerHTML = '<option value="">全部类型</option>';
    Array.from(ids)
      .sort()
      .forEach((id) => {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = DIR_CAT_LABELS[id] || id;
        sel.appendChild(opt);
      });
    if (prev && (prev === "" || ids.has(prev))) sel.value = prev;
    else sel.value = "";
    wikiPageFilter = sel.value || "";
  }

  function syncWikiPagesSelectAllState() {
    const selAll = document.getElementById("wiki-pages-select-all");
    const delBtn = document.getElementById("btn-wiki-pages-delete");
    const cbs = Array.from(document.querySelectorAll("#page-list .wiki-page-cb"));
    const checked = cbs.filter((cb) => cb.checked);
    if (selAll) {
      selAll.checked = cbs.length > 0 && checked.length === cbs.length;
      selAll.indeterminate = checked.length > 0 && checked.length < cbs.length;
    }
    if (delBtn) delBtn.disabled = checked.length === 0;
  }

  function clearWikiPagePreviewIf(path) {
    if (wbPagePath !== path) return;
    wbPagePath = null;
    const ta = document.getElementById("page-preview");
    if (ta) ta.value = "";
    const btn = document.getElementById("btn-save-page");
    if (btn) btn.disabled = true;
    clearEgoGraph();
  }

  async function deleteSelectedWikiPages() {
    const paths = Array.from(
      document.querySelectorAll("#page-list .wiki-page-cb:checked")
    ).map((cb) => cb.value);
    if (!paths.length) return;
    if (!confirm("删除所选 " + paths.length + " 个页面？")) return;
    let okN = 0;
    const fails = [];
    for (const path of paths) {
      const out = await api("POST", "/api/pages/delete", { path: path });
      if (out.ok) {
        okN += 1;
        clearWikiPagePreviewIf(path);
      } else {
        fails.push(path + ": " + ((out.error && out.error.message) || "失败"));
      }
    }
    const selAll = document.getElementById("wiki-pages-select-all");
    if (selAll) {
      selAll.checked = false;
      selAll.indeterminate = false;
    }
    await refreshPages();
    if (fails.length) {
      alert("成功 " + okN + " · 失败 " + fails.length + "\n" + fails.slice(0, 8).join("\n"));
    }
  }

  function renderPageList() {
    const ul = document.getElementById("page-list");
    if (!ul) return;
    ul.innerHTML = "";
    const selAll = document.getElementById("wiki-pages-select-all");
    if (selAll) {
      selAll.checked = false;
      selAll.indeterminate = false;
    }
    const items = (wikiPagesCache || []).filter((p) => {
      if (!wikiPageFilter) return true;
      return (p.dir_category || "other") === wikiPageFilter;
    });
    if (!items.length) {
      ul.innerHTML =
        '<li class="list-group-item text-secondary">暂无页面</li>';
      syncWikiPagesSelectAllState();
      return;
    }
    for (const p of items) {
      const li = document.createElement("li");
      li.className =
        "list-group-item list-group-item-action wiki-page-item" +
        (wbPagePath === p.path ? " active" : "");
      li.dataset.path = p.path;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "form-check-input wiki-page-cb";
      cb.value = p.path;
      cb.title = "选择";
      cb.onclick = (ev) => ev.stopPropagation();
      cb.onchange = () => syncWikiPagesSelectAllState();
      const title = document.createElement("span");
      title.className = "wiki-page-title";
      title.textContent = p.title || p.path;
      title.title = p.path;
      title.onclick = () => showPagePreview(p.path, "page-preview");
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-sm btn-link wiki-page-del";
      delBtn.title = "删除页面";
      delBtn.setAttribute("aria-label", "删除页面");
      delBtn.innerHTML = '<i class="bi bi-trash"></i>';
      delBtn.onclick = async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (!confirm("删除页面「" + (p.title || p.path) + "」？")) return;
        const out = await api("POST", "/api/pages/delete", { path: p.path });
        if (!out.ok) {
          alert((out.error && out.error.message) || "删除失败");
          return;
        }
        clearWikiPagePreviewIf(p.path);
        await refreshPages();
      };
      li.appendChild(cb);
      li.appendChild(title);
      li.appendChild(delBtn);
      ul.appendChild(li);
    }
    syncWikiPagesSelectAllState();
  }

  async function refreshPages() {
    const ul = document.getElementById("page-list");
    if (!ul) return;
    const data = await api("GET", "/api/pages");
    if (!data.ok) {
      ul.innerHTML =
        '<li class="list-group-item text-danger">' +
        esc((data.error && data.error.message) || "error") +
        "</li>";
      return;
    }
    wikiPagesCache = data.data || [];
    fillPageTypeFilter(wikiPagesCache);
    renderPageList();
  }

  function wbProviderOptionsHtml(selected) {
    let ids = Object.keys(modelState.providers || {});
    if (!ids.length) ids = ["ollama_local", "deepseek", "qwen"];
    if (selected && ids.indexOf(selected) < 0) ids = [selected].concat(ids);
    return ids
      .map(
        (p) =>
          '<option value="' +
          esc(p) +
          '"' +
          (p === selected ? " selected" : "") +
          ">" +
          esc(p) +
          "</option>"
      )
      .join("");
  }

  function wbModelOptionsHtml(names, selected) {
    const opts = (names || []).slice();
    if (selected && opts.indexOf(selected) < 0) opts.unshift(selected);
    if (!opts.length) {
      return '<option value="" selected>（无可用模型 — 先配置厂商）</option>';
    }
    return opts
      .map(
        (n) =>
          '<option value="' +
          esc(n) +
          '"' +
          (n === selected ? " selected" : "") +
          ">" +
          esc(n) +
          "</option>"
      )
      .join("");
  }

  async function loadWorkbenchPresetsForm() {
    const mount = document.getElementById("wb-preset-editors");
    if (!mount) return;
    const out = await api("GET", "/api/settings/workbench-presets");
    const presets = (out.ok && out.data) || {};
    const defaults = {
      local: { label: "本地", provider: "ollama_local" },
      deepseek: { label: "DeepSeek", provider: "deepseek" },
      qwen: { label: "千问", provider: "qwen" },
    };
    // Prefetch only providers used by the three presets (avoid hanging on unused online vendors)
    const need = new Set();
    ["local", "deepseek", "qwen"].forEach((id) => {
      const p = Object.assign({}, defaults[id] || {}, presets[id] || {});
      if (p.provider) need.add(p.provider);
    });
    await Promise.all(
      Array.from(need).map((pid) =>
        fetchProviderModels(pid).catch(() => {
          providerModelsCache[pid] = providerModelsCache[pid] || [];
          return providerModelsCache[pid];
        })
      )
    );

    mount.innerHTML = "";
    for (const id of ["local", "deepseek", "qwen"]) {
      const p = Object.assign({}, defaults[id] || {}, presets[id] || {});
      const prov = p.provider || (defaults[id] && defaults[id].provider) || "";
      let model = p.model || "";
      const names = providerModelsCache[prov] || [];
      if (!model && names.length) model = names[0];
      const row = document.createElement("div");
      row.className = "row g-2 align-items-end";
      row.dataset.preset = id;
      row.innerHTML =
        '<div class="col-md-2"><label class="form-label">显示名</label>' +
        '<input class="form-control form-control-sm" data-f="label" value="' +
        esc(p.label || id) +
        '" /></div>' +
        '<div class="col-md-4"><label class="form-label">厂商</label>' +
        '<select class="form-select form-select-sm" data-f="provider">' +
        wbProviderOptionsHtml(prov) +
        "</select></div>" +
        '<div class="col-md-4"><label class="form-label">模型</label>' +
        '<select class="form-select form-select-sm" data-f="model">' +
        wbModelOptionsHtml(names, model) +
        "</select></div>";
      const provSel = row.querySelector('[data-f="provider"]');
      provSel.addEventListener("change", async () => {
        const np = provSel.value;
        await fetchProviderModels(np);
        const mSel = row.querySelector('[data-f="model"]');
        const n2 = providerModelsCache[np] || [];
        mSel.innerHTML = wbModelOptionsHtml(n2, n2[0] || "");
      });
      mount.appendChild(row);
    }
  }

  async function saveWorkbenchPresetsForm() {
    const body = {};
    document.querySelectorAll("#wb-preset-editors [data-preset]").forEach((row) => {
      const id = row.dataset.preset;
      body[id] = {
        label: row.querySelector('[data-f="label"]').value,
        provider: row.querySelector('[data-f="provider"]').value,
        model: row.querySelector('[data-f="model"]').value,
      };
    });
    const out = await api("POST", "/api/settings/workbench-presets", body);
    dbg(out);
    if (!out.ok) {
      alert("保存预设失败: " + ((out.error && out.error.message) || "unknown"));
      return;
    }
    await loadWorkbenchPresetsForm();
  }

  async function refreshOcStatus() {
    const el = document.getElementById("oc-status-out");
    if (!el) return;
    el.textContent = JSON.stringify(await api("GET", "/api/opencode/status"), null, 2);
  }

  async function checkOcUpdate() {
    const el = document.getElementById("oc-status-out");
    if (!el) return;
    el.textContent = JSON.stringify(
      await api("POST", "/api/opencode/check-update", {}),
      null,
      2
    );
  }

  async function refreshWorkbench() {
    const list = document.getElementById("wb-session-list");
    const warn = document.getElementById("wb-storage-warn");
    const qel = document.getElementById("wb-queue");
    if (!list) return;
    const [sessions, storage, queue] = await Promise.all([
      api("GET", "/api/sessions"),
      api("GET", "/api/sessions/storage"),
      api("GET", "/api/sessions/queue"),
    ]);
    if (warn) {
      warn.classList.toggle("d-none", !(storage.ok && storage.data && storage.data.warn));
    }
    if (qel && queue.ok) {
      const q = queue.data || {};
      qel.textContent =
        "运行 " +
        (q.running || 0) +
        "/" +
        (q.max_running || 2) +
        " · 排队 " +
        (q.queued || 0);
    }
    list.innerHTML = "";
    const selAll = document.getElementById("wb-sessions-select-all");
    if (selAll) {
      selAll.checked = false;
      selAll.indeterminate = false;
    }
    const items = (sessions.ok && sessions.data) || [];
    if (!items.length) {
      list.innerHTML = '<li class="list-group-item text-secondary">暂无会话</li>';
      syncWbSessionsSelectAllState();
    }
    for (const s of items) {
      const li = document.createElement("li");
      li.className =
        "list-group-item list-group-item-action wb-session-item" +
        (s.id === wbSessionId ? " active" : "");
      li.dataset.sessionId = s.id;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "form-check-input wb-session-cb";
      cb.value = s.id;
      cb.title = "选择";
      cb.onclick = (ev) => ev.stopPropagation();
      cb.onchange = () => syncWbSessionsSelectAllState();
      const title = document.createElement("span");
      title.className = "wb-session-title";
      title.textContent = s.title || s.id;
      title.onclick = () => openWorkbenchSession(s.id);
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-sm btn-link wb-session-del";
      delBtn.title = "删除会话";
      delBtn.setAttribute("aria-label", "删除会话");
      delBtn.innerHTML = '<i class="bi bi-trash"></i>';
      delBtn.onclick = async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (!confirm("删除会话「" + (s.title || s.id) + "」？")) return;
        await api("DELETE", "/api/sessions/" + encodeURIComponent(s.id));
        clearWorkbenchSessionIf(s.id);
        await refreshWorkbench();
      };
      li.appendChild(cb);
      li.appendChild(title);
      li.appendChild(delBtn);
      list.appendChild(li);
    }
    syncWbSessionsSelectAllState();
    if (wbSessionId) await openWorkbenchSession(wbSessionId);
  }

  function syncWbSessionsSelectAllState() {
    const selAll = document.getElementById("wb-sessions-select-all");
    const delBtn = document.getElementById("btn-wb-sessions-delete");
    const cbs = Array.from(
      document.querySelectorAll("#wb-session-list .wb-session-cb")
    );
    const checked = cbs.filter((cb) => cb.checked);
    if (selAll) {
      selAll.checked = cbs.length > 0 && checked.length === cbs.length;
      selAll.indeterminate = checked.length > 0 && checked.length < cbs.length;
    }
    if (delBtn) delBtn.disabled = checked.length === 0;
  }

  function clearWorkbenchSessionIf(sid) {
    if (wbSessionId !== sid) return;
    wbSessionId = null;
    const box = document.getElementById("wb-messages");
    if (box) box.innerHTML = "";
    setWikiHint(null);
    const send = document.getElementById("btn-wb-send");
    const del = document.getElementById("btn-wb-delete");
    if (send) send.disabled = true;
    if (del) del.disabled = true;
    updateContinueBtn(false);
  }

  async function deleteSelectedSessions() {
    const ids = Array.from(
      document.querySelectorAll("#wb-session-list .wb-session-cb:checked")
    ).map((cb) => cb.value);
    if (!ids.length) return;
    if (!confirm("删除所选 " + ids.length + " 个会话？")) return;
    let okN = 0;
    const fails = [];
    for (const sid of ids) {
      const out = await api("DELETE", "/api/sessions/" + encodeURIComponent(sid));
      if (out.ok) {
        okN += 1;
        clearWorkbenchSessionIf(sid);
      } else {
        fails.push(sid + ": " + ((out.error && out.error.message) || "失败"));
      }
    }
    const selAll = document.getElementById("wb-sessions-select-all");
    if (selAll) {
      selAll.checked = false;
      selAll.indeterminate = false;
    }
    await refreshWorkbench();
    if (fails.length) {
      alert("成功 " + okN + " · 失败 " + fails.length + "\n" + fails.slice(0, 8).join("\n"));
    }
  }

  function setWikiHint(hit, paths) {
    const el = document.getElementById("wb-wiki-hint");
    if (!el) return;
    if (hit) {
      el.classList.remove("d-none");
      el.textContent =
        "已引用知识库：" + ((paths && paths.length) ? paths.join(" · ") : "有命中");
    } else if (hit === false) {
      el.classList.remove("d-none");
      el.textContent = "未命中本地知识库（通识回答）";
    } else {
      el.classList.add("d-none");
      el.textContent = "";
    }
  }

  function updateContinueBtn(hasOcRef) {
    const btn = document.getElementById("btn-wb-continue");
    const hint = document.getElementById("wb-continue-hint");
    const ok = !!wbSessionId && !!hasOcRef;
    if (btn) {
      btn.disabled = !ok;
      btn.title = ok
        ? "继续当前 OpenCode 会话"
        : "需已有 OpenCode 会话引用（发过消息并跑完一轮后可用）";
    }
    if (hint) {
      hint.textContent = ok ? "" : wbSessionId ? "尚无 OC 会话可续跑" : "";
    }
  }

  async function openWorkbenchSession(id) {
    wbSessionId = id;
    const send = document.getElementById("btn-wb-send");
    const del = document.getElementById("btn-wb-delete");
    if (send) send.disabled = !id;
    if (del) del.disabled = !id;
    const detail = await api("GET", "/api/sessions/" + encodeURIComponent(id));
    const box = document.getElementById("wb-messages");
    if (!box) return;
    box.innerHTML = "";
    if (!detail.ok) {
      box.textContent = (detail.error && detail.error.message) || "加载失败";
      updateContinueBtn(false);
      return;
    }
    const data = detail.data || {};
    updateContinueBtn(!!data.oc_session_ref);
    const msgs = data.messages || [];
    let lastWiki = null;
    for (const m of msgs) {
      const div = document.createElement("div");
      const body = m.content || "";
      fillWbMsg(div, m.role === "user" ? "user" : "assistant", body, {
        fail: m.ok === false,
      });
      box.appendChild(div);
      if (m.role === "assistant" && typeof m.wiki_hit === "boolean") {
        lastWiki = m;
      }
    }
    if (lastWiki) setWikiHint(lastWiki.wiki_hit, lastWiki.wiki_paths);
    box.scrollTop = box.scrollHeight;
    document.querySelectorAll("#wb-session-list .list-group-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.sessionId === id);
    });
  }

  /** Split a large delta into visible chunks (OC often emits one full blob). */
  function splitStreamChunks(text, chunkSize) {
    const s = String(text || "");
    const size = Math.max(8, chunkSize || 24);
    if (s.length <= size) return s ? [s] : [];
    const out = [];
    let i = 0;
    while (i < s.length) {
      let end = Math.min(i + size, s.length);
      if (end < s.length) {
        const slice = s.slice(i, end + 12);
        const m = slice.match(/[。！？\n.!?]|\s/);
        if (m && m.index != null && m.index > 0) {
          end = i + m.index + 1;
        }
      }
      out.push(s.slice(i, end));
      i = end;
    }
    return out;
  }

  function sleepMs(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function appendAssistantVisible(assistantEl, fullRef, piece, opts) {
    const fail = !!(opts && opts.fail);
    const statusLine = (opts && opts.statusLine) || "";
    const chunks =
      piece.length > 24 ? splitStreamChunks(piece, 16) : piece ? [piece] : [];
    for (const ch of chunks) {
      fullRef.value += ch;
      fillWbMsg(assistantEl, "assistant", fullRef.value, {
        fail,
        streaming: true,
        statusLine,
      });
      const box = document.getElementById("wb-messages");
      if (box) box.scrollTop = box.scrollHeight;
      if (chunks.length > 1) await sleepMs(32);
    }
  }

  let wbActiveJobId = null;

  function showRunPanel(jobId) {
    wbActiveJobId = jobId || null;
    const panel = document.getElementById("wb-run-panel");
    const title = document.getElementById("wb-run-title");
    const status = document.getElementById("wb-run-status");
    const log = document.getElementById("wb-run-log");
    const stop = document.getElementById("btn-wb-stop");
    if (panel) panel.classList.remove("d-none");
    if (title) title.textContent = "运行中";
    if (status) status.textContent = "启动中…";
    if (log) log.innerHTML = "";
    if (stop) {
      stop.disabled = !jobId;
      stop.dataset.jobId = jobId || "";
    }
  }

  function appendRunLog(line) {
    const log = document.getElementById("wb-run-log");
    const status = document.getElementById("wb-run-status");
    const text = String(line || "").trim();
    if (!text) return;
    if (status) status.textContent = text;
    if (!log) return;
    const el = document.createElement("div");
    el.className = "wb-run-line";
    el.textContent = text;
    log.appendChild(el);
    while (log.children.length > 80) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }

  function finishRunPanel(ev, forJobId) {
    // Ignore stale done from a previous SSE consumer (race with a newer job).
    if (forJobId && wbActiveJobId && forJobId !== wbActiveJobId) return;
    const title = document.getElementById("wb-run-title");
    const stop = document.getElementById("btn-wb-stop");
    const cancelled = !!(ev && (ev.cancelled || (ev.error && ev.error.code === "E_CANCELLED")));
    if (title) {
      title.textContent = cancelled ? "已停止" : ev && ev.ok === false ? "已结束（失败）" : "已完成";
    }
    if (stop) {
      stop.disabled = true;
      if (!forJobId || stop.dataset.jobId === forJobId || !stop.dataset.jobId) {
        stop.dataset.jobId = "";
      }
    }
    if (!forJobId || wbActiveJobId === forJobId) {
      wbActiveJobId = null;
    }
  }

  async function resolveStopJobId() {
    const stop = document.getElementById("btn-wb-stop");
    let jobId = (stop && stop.dataset.jobId) || wbActiveJobId;
    if (jobId) return jobId;
    try {
      const q = await api("GET", "/api/sessions/queue");
      const jobs = (q.ok && q.data && q.data.jobs) || [];
      const running = jobs.filter((j) => j.status === "running");
      const mine =
        (wbSessionId && running.find((j) => j.session_id === wbSessionId)) ||
        running[0];
      if (mine && mine.job_id) return String(mine.job_id);
    } catch {
      /* ignore */
    }
    return "";
  }

  async function stopActiveJob() {
    const stop = document.getElementById("btn-wb-stop");
    const jobId = await resolveStopJobId();
    if (!jobId) {
      appendRunLog("没有可停止的运行任务（可刷新后重试）");
      return;
    }
    if (stop) {
      stop.disabled = true;
      stop.dataset.jobId = jobId;
    }
    wbActiveJobId = jobId;
    const title = document.getElementById("wb-run-title");
    if (title) title.textContent = "正在停止";
    appendRunLog("正在停止 " + jobId + "…");
    const out = await api(
      "POST",
      "/api/sessions/jobs/" + encodeURIComponent(jobId) + "/stop",
      {}
    );
    if (!out.ok) {
      appendRunLog((out.error && out.error.message) || "停止失败");
      if (stop) stop.disabled = false;
      return;
    }
    // Poll until cancelled / gone — keep Stop re-clickable if still stuck
    for (let i = 0; i < 40; i++) {
      await sleepMs(500);
      try {
        const q = await api("GET", "/api/sessions/queue");
        const jobs = (q.ok && q.data && q.data.jobs) || [];
        const j = jobs.find((x) => x.job_id === jobId);
        if (!j || j.status !== "running") {
          appendRunLog(j && j.status === "cancelled" ? "已停止" : "任务已结束");
          finishRunPanel(
            { ok: false, cancelled: !!(j && j.status === "cancelled") },
            jobId
          );
          return;
        }
      } catch {
        /* keep waiting */
      }
    }
    appendRunLog("停止超时：进程可能仍在跑，请再点停止或刷新页面");
    if (stop) stop.disabled = false;
  }

  async function consumeJobSse(jobId, assistantEl) {
    showRunPanel(jobId);
    appendRunLog("已连接事件流");
    const tok = token();
    const res = await fetch(
      "/api/sessions/jobs/" + encodeURIComponent(jobId) + "/events",
      { headers: { Authorization: "Bearer " + tok } }
    );
    if (!res.ok || !res.body) {
      finishRunPanel({ ok: false }, jobId);
      throw new Error("SSE " + res.status);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buf = "";
    const fullRef = { value: "" };
    let statusLine = "";
    let sawStatusEvent = false;
    let lastEv = null;
    let sawDelta = false;
    let streamDone = false;
    function paintStatus() {
      if (!assistantEl) return;
      fillWbMsg(assistantEl, "assistant", fullRef.value || "（生成中…）", {
        streaming: true,
        statusLine,
      });
    }
    function applyOcStatus(ev) {
      const parts = [];
      if (ev.busy || ev.session_status === "busy") parts.push("OpenCode busy");
      else if (ev.session_status) parts.push("OpenCode " + ev.session_status);
      else parts.push("OpenCode");
      const tool = ev.tool;
      if (tool && tool.name) {
        const phase = String(tool.phase || "start");
        parts.push(phase === "end" ? "已调用 " + tool.name : "正在 " + tool.name);
      }
      if (ev.step) parts.push(String(ev.step));
      statusLine = parts.join(" · ");
      sawStatusEvent = true;
      appendRunLog(statusLine);
      paintStatus();
    }
    if (assistantEl && (assistantEl.textContent || "").indexOf("…") >= 0) {
      paintStatus();
    }
    // Only pull from disk if still empty / generating — never clobber live status.
    const reconcile = setInterval(async () => {
      if (!wbSessionId || !assistantEl || streamDone) return;
      if (sawDelta && fullRef.value) return;
      if (sawStatusEvent && !sawDelta) return; // keep live oc_status / tool line
      try {
        const detail = await api(
          "GET",
          "/api/sessions/" + encodeURIComponent(wbSessionId)
        );
        if (detail.ok && detail.data) {
          updateContinueBtn(!!detail.data.oc_session_ref);
        }
        const msgs = (detail.ok && detail.data && detail.data.messages) || [];
        const mine = [...msgs]
          .reverse()
          .find((m) => m.role === "assistant" && m.run_id === jobId);
        if (mine && mine.content && !sawDelta) {
          await appendAssistantVisible(
            assistantEl,
            fullRef,
            String(mine.content),
            { fail: mine.ok === false, statusLine }
          );
          if (mine.ok === false && mine.error && mine.error.message) {
            fullRef.value += "\n(" + mine.error.message + ")";
            fillWbMsg(assistantEl, "assistant", fullRef.value, {
              fail: true,
              statusLine,
            });
          }
          if (typeof mine.wiki_hit === "boolean") {
            setWikiHint(mine.wiki_hit, mine.wiki_paths || []);
          }
        }
      } catch {
        /* ignore transient */
      }
    }, 2500);
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const block of parts) {
          const line = block.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try {
            ev = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }
          if (ev.type === "meta") {
            setWikiHint(!!ev.wiki_hit, ev.wiki_paths || []);
            if (assistantEl && !sawDelta && !fullRef.value) {
              paintStatus();
            }
          } else if (ev.type === "oc_status") {
            applyOcStatus(ev);
          } else if (ev.type === "tool" || ev.type === "status") {
            if (ev.type === "status" && ev.text) {
              statusLine = String(ev.text);
            } else if (ev.type === "tool" && ev.name) {
              const phase = String(ev.phase || "start");
              const n = String(ev.name);
              const title = ev.title ? "（" + String(ev.title) + "）" : "";
              if (phase === "end") {
                statusLine =
                  ev.ok === false
                    ? "工具 " + n + " 失败"
                    : "已调用 " + n + title;
              } else {
                statusLine = "正在调用 " + n + title + "…";
              }
            }
            sawStatusEvent = true;
            appendRunLog(statusLine);
            paintStatus();
          } else if (ev.type === "delta" && ev.text) {
            sawDelta = true;
            // Pace every frame — many deltas often arrive in one TCP/read batch.
            await appendAssistantVisible(assistantEl, fullRef, String(ev.text), {
              statusLine,
            });
            await sleepMs(40);
          } else if (ev.type === "done") {
            lastEv = ev;
            streamDone = true;
            const finalText = String(ev.content || fullRef.value || "");
            if (finalText) {
              if (finalText.startsWith(fullRef.value) && finalText.length > fullRef.value.length) {
                await appendAssistantVisible(
                  assistantEl,
                  fullRef,
                  finalText.slice(fullRef.value.length),
                  { fail: ev.ok === false, statusLine }
                );
              } else if (!fullRef.value) {
                await appendAssistantVisible(assistantEl, fullRef, finalText, {
                  fail: ev.ok === false,
                  statusLine,
                });
              } else if (finalText !== fullRef.value) {
                fullRef.value = finalText;
              }
            }
            if (ev.ok === false && ev.error && ev.error.message && assistantEl) {
              if (!fullRef.value.includes(ev.error.message)) {
                fullRef.value += "\n(" + ev.error.message + ")";
              }
            }
            statusLine = "";
            fillWbMsg(assistantEl, "assistant", fullRef.value, {
              fail: ev.ok === false,
            });
            if (typeof ev.wiki_hit === "boolean") {
              setWikiHint(ev.wiki_hit, ev.wiki_paths || []);
            }
            if (ev.oc_session_ref || ev.can_continue) {
              updateContinueBtn(true);
            }
            appendRunLog(
              ev.cancelled
                ? "已停止"
                : ev.ok === false
                  ? "结束（失败）"
                  : "完成"
            );
            finishRunPanel(ev, jobId);
            return ev;
          }
        }
      }
    } finally {
      streamDone = true;
      clearInterval(reconcile);
    }
    if (assistantEl && fullRef.value) {
      fillWbMsg(assistantEl, "assistant", fullRef.value, {});
    }
    return lastEv || { ok: true, content: fullRef.value };
  }

  async function newWorkbenchSession() {
    const preset = document.getElementById("wb-preset").value || "local";
    const sel = await api("POST", "/api/settings/workbench-presets/select", {
      preset_id: preset,
    });
    if (!sel.ok) {
      console.warn("preset select", sel);
    }
    const out = await api("POST", "/api/sessions", {
      title: "会话 " + new Date().toLocaleString(),
      preset_id: preset,
    });
    if (out.ok && out.data) {
      wbSessionId = out.data.id;
      await refreshWorkbench();
    } else {
      alert((out.error && out.error.message) || "创建失败");
    }
  }

  async function runWorkbenchJob(out, userText) {
    const jobId = out.data && out.data.job_id;
    renderAgentResult(
      out.ok
        ? { ok: true, data: { summary: "已入队 " + (jobId || "") } }
        : out
    );
    const box = document.getElementById("wb-messages");
    if (box) {
      if (userText) {
        const u = document.createElement("div");
        fillWbMsg(u, "user", userText);
        box.appendChild(u);
      }
      const a = document.createElement("div");
      fillWbMsg(a, "assistant", "…", {
        streaming: true,
        statusLine: "OpenCode 启动中…",
      });
      box.appendChild(a);
      box.scrollTop = box.scrollHeight;
      if (out.ok && jobId) {
        try {
          await consumeJobSse(jobId, a);
        } catch (e) {
          fillWbMsg(
            a,
            "assistant",
            "(流式失败，回退刷新) " + String(e && e.message ? e.message : e),
            { fail: true }
          );
          if (wbPollTimer) clearInterval(wbPollTimer);
          wbPollTimer = setInterval(async () => {
            await openWorkbenchSession(wbSessionId);
            const q = await api("GET", "/api/sessions/queue");
            if (q.ok && (q.data.running || 0) === 0 && (q.data.queued || 0) === 0) {
              clearInterval(wbPollTimer);
              wbPollTimer = null;
              refreshWorkbench();
            }
          }, 1200);
          return;
        }
      }
    }
    await openWorkbenchSession(wbSessionId);
    refreshWorkbench();
  }

  async function sendWorkbenchMessage() {
    const input = document.getElementById("wb-input");
    const text = (input && input.value.trim()) || "";
    if (!wbSessionId || !text) return;
    const preset = document.getElementById("wb-preset").value || "local";
    await api("POST", "/api/settings/workbench-presets/select", { preset_id: preset });
    const out = await api("POST", "/api/sessions/" + encodeURIComponent(wbSessionId) + "/messages", {
      content: text,
    });
    if (input) input.value = "";
    await runWorkbenchJob(out, text);
  }

  async function continueWorkbenchSession() {
    if (!wbSessionId) return;
    const btn = document.getElementById("btn-wb-continue");
    if (btn) btn.disabled = true;
    const input = document.getElementById("wb-input");
    const extra = (input && input.value.trim()) || "";
    const body = extra ? { message: extra } : {};
    if (input && extra) input.value = "";
    const out = await api(
      "POST",
      "/api/sessions/" + encodeURIComponent(wbSessionId) + "/continue",
      body
    );
    if (!out.ok) {
      alert((out.error && out.error.message) || "无法继续（无 OC 会话）");
      updateContinueBtn(false);
      return;
    }
    await runWorkbenchJob(out, extra || "（继续）");
  }

  const GRAPH_DIR_COLORS = {
    topics: "#1877f2",
    concepts: "#42b72a",
    entities: "#f7b928",
    decisions: "#e74c3c",
    questions: "#9b59b6",
    references: "#00a2f0",
    memories: "#e67e22",
    experiences: "#1abc9c",
    profiles: "#7f8c8d",
    other: "#bcc0c4",
  };
  let graphSelectedCats = [];
  let graphPreviewPath = null;
  let graphMetaCats = [];
  let graphCtxIgnoreClickUntil = 0;
  let graphModalPath = null;
  let graphModalPageCats = [];
  /** Extra tags added in modal before save (plain labels). */
  let graphModalExtraTags = [];

  function graphColorFor(catId) {
    if (!catId) return GRAPH_DIR_COLORS.other;
    if (GRAPH_DIR_COLORS[catId]) return GRAPH_DIR_COLORS[catId];
    if (String(catId).startsWith("custom:")) {
      let h = 0;
      const s = String(catId);
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
      const hue = h % 360;
      return "hsl(" + hue + " 55% 45%)";
    }
    return GRAPH_DIR_COLORS.other;
  }

  function graphSelectedFromChecks() {
    const box = document.getElementById("graph-cat-checks");
    if (!box) return [];
    return Array.from(box.querySelectorAll("input[type=checkbox]:checked")).map(
      (el) => el.value
    );
  }

  function renderGraphCatChecks(metaCats) {
    const box = document.getElementById("graph-cat-checks");
    if (!box) return;
    graphMetaCats = metaCats || [];
    const prev = new Set(graphSelectedCats);
    box.innerHTML = "";
    for (const c of graphMetaCats) {
      const id = c.id;
      const lab = document.createElement("label");
      lab.className = "form-check form-check-inline mb-0";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "form-check-input";
      cb.value = id;
      cb.checked = prev.has(id);
      cb.onchange = () => {
        graphSelectedCats = graphSelectedFromChecks();
        refreshWikiGraph();
      };
      const span = document.createElement("span");
      span.className = "form-check-label";
      span.innerHTML =
        '<span class="graph-legend-swatch" style="background:' +
        esc(graphColorFor(id)) +
        '"></span> ' +
        esc(c.label || id) +
        " (" +
        (c.member_count || 0) +
        ")";
      lab.appendChild(cb);
      lab.appendChild(span);
      box.appendChild(lab);
    }
  }

  function renderGraphLegend(metaCats, view) {
    const el = document.getElementById("graph-legend");
    if (!el) return;
    if (view === "overview") {
      el.innerHTML =
        '<span class="text-secondary">总览：大类节点 · 边粗细 = 跨类链接数 · 勾选上方类别展开页级图</span>';
      return;
    }
    el.innerHTML = (metaCats || [])
      .filter((c) => graphSelectedCats.indexOf(c.id) >= 0)
      .map(
        (c) =>
          '<span class="graph-legend-item"><span class="graph-legend-swatch" style="background:' +
          esc(graphColorFor(c.id)) +
          '"></span>' +
          esc(c.label || c.id) +
          "</span>"
      )
      .join("");
  }

  function setGraphCatEditor(path, categories, title) {
    graphPreviewPath = path || null;
    const inp = document.getElementById("graph-cat-input");
    const titleInp = document.getElementById("graph-title-input");
    const hint = document.getElementById("graph-cat-edit-hint");
    if (inp) inp.value = path ? (categories || []).join(", ") : "";
    if (titleInp) titleInp.value = path ? title || "" : "";
    if (hint) {
      hint.textContent = path
        ? "当前节点 · " + path + "（右键可改分类 / 名称）"
        : "右键页面节点可改分类 / 名称";
    }
  }

  function customTagFromCatId(id) {
    const s = String(id || "");
    if (s.startsWith("custom:")) return s.slice("custom:".length);
    return "";
  }

  function hideGraphCtxMenu() {
    const menu = document.getElementById("graph-ctx-menu");
    if (!menu) return;
    menu.classList.add("d-none");
    menu.setAttribute("aria-hidden", "true");
  }

  function showGraphCtxMenu(x, y, path, categories, title) {
    const menu = document.getElementById("graph-ctx-menu");
    if (!menu || !path) return;
    graphPreviewPath = path;
    setGraphCatEditor(path, categories, title);
    const pad = 4;
    const vw = window.innerWidth || 800;
    const vh = window.innerHeight || 600;
    menu.classList.remove("d-none");
    menu.setAttribute("aria-hidden", "false");
    // measure after visible
    const mw = menu.offsetWidth || 160;
    const mh = menu.offsetHeight || 72;
    let left = x;
    let top = y;
    if (left + mw > vw - pad) left = Math.max(pad, vw - mw - pad);
    if (top + mh > vh - pad) top = Math.max(pad, vh - mh - pad);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    graphCtxIgnoreClickUntil = Date.now() + 350;
  }

  function resetLwModalCardPosition(cardId) {
    const card = document.getElementById(cardId);
    if (!card) return;
    card.classList.remove("lw-modal-card--dragging");
    card.style.left = "";
    card.style.top = "";
    card.style.width = "";
  }

  function closeLwModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("d-none");
    if (id === "graph-cat-modal") resetLwModalCardPosition("graph-cat-modal-card");
    if (id === "graph-title-modal")
      resetLwModalCardPosition("graph-title-modal-card");
  }

  function openLwModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (id === "graph-cat-modal") resetLwModalCardPosition("graph-cat-modal-card");
    if (id === "graph-title-modal")
      resetLwModalCardPosition("graph-title-modal-card");
    el.classList.remove("d-none");
  }

  function collectCustomTagOptions(pageCats) {
    const tags = [];
    const seen = new Set();
    const add = (t) => {
      const tag = String(t || "").trim();
      if (!tag || seen.has(tag)) return;
      seen.add(tag);
      tags.push(tag);
    };
    for (const c of graphMetaCats || []) {
      const t = customTagFromCatId(c.id);
      if (t) add(t);
      else if (c.id && String(c.id).indexOf(":") < 0 && !DIR_CAT_LABELS[c.id]) {
        // already a plain custom-looking id (unlikely)
      }
    }
    for (const t of pageCats || []) add(t);
    for (const t of graphModalExtraTags || []) add(t);
    return tags;
  }

  function getGraphCatModalSelected() {
    const sel = document.getElementById("graph-cat-modal-select");
    if (sel && sel.options.length) {
      return Array.from(sel.selectedOptions).map((o) => o.value);
    }
    return Array.from(
      document.querySelectorAll(".graph-cat-modal-cb:checked")
    ).map((el) => el.value);
  }

  function syncGraphCatModalSelect(selected) {
    const sel = document.getElementById("graph-cat-modal-select");
    if (!sel) return;
    const want = new Set(selected || []);
    Array.from(sel.options).forEach((opt) => {
      opt.selected = want.has(opt.value);
    });
  }

  function syncGraphCatModalChecks(selected) {
    const want = new Set(selected || []);
    document.querySelectorAll(".graph-cat-modal-cb").forEach((cb) => {
      cb.checked = want.has(cb.value);
    });
  }

  function renderGraphCatModalList(selected) {
    const box = document.getElementById("graph-cat-modal-list");
    const sel = document.getElementById("graph-cat-modal-select");
    const selSet = new Set(selected || []);
    const tags = collectCustomTagOptions(graphModalPageCats);
    if (sel) {
      sel.innerHTML = "";
      for (const tag of tags) {
        const opt = document.createElement("option");
        opt.value = tag;
        opt.textContent = tag;
        opt.selected = selSet.has(tag);
        sel.appendChild(opt);
      }
    }
    if (!box) return;
    box.innerHTML = "";
    if (!tags.length) {
      box.innerHTML =
        '<div class="small text-secondary">暂无自定义分类，可在下方新建补缺</div>';
      return;
    }
    for (const tag of tags) {
      const lab = document.createElement("label");
      lab.className = "form-check mb-1";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "form-check-input graph-cat-modal-cb";
      cb.value = tag;
      cb.checked = selSet.has(tag);
      cb.onchange = () => {
        const cur = Array.from(
          document.querySelectorAll(".graph-cat-modal-cb:checked")
        ).map((el) => el.value);
        syncGraphCatModalSelect(cur);
      };
      const span = document.createElement("span");
      span.className = "form-check-label";
      span.textContent = tag;
      lab.appendChild(cb);
      lab.appendChild(span);
      box.appendChild(lab);
    }
  }

  function openGraphCatModal(path, categories) {
    graphModalPath = path;
    graphModalPageCats = (categories || []).slice();
    graphModalExtraTags = [];
    const pathEl = document.getElementById("graph-cat-modal-path");
    const err = document.getElementById("graph-cat-modal-err");
    const neu = document.getElementById("graph-cat-new-input");
    if (pathEl) pathEl.textContent = path || "";
    if (err) err.textContent = "";
    if (neu) neu.value = "";
    renderGraphCatModalList(graphModalPageCats);
    openLwModal("graph-cat-modal");
  }

  function openGraphTitleModal(path, title) {
    graphModalPath = path;
    const pathEl = document.getElementById("graph-title-modal-path");
    const inp = document.getElementById("graph-modal-title-input");
    const err = document.getElementById("graph-title-modal-err");
    if (pathEl) pathEl.textContent = path || "";
    if (inp) inp.value = title || "";
    if (err) err.textContent = "";
    openLwModal("graph-title-modal");
    if (inp) {
      inp.focus();
      inp.select();
    }
  }

  function addGraphCatFromModalInput() {
    const neu = document.getElementById("graph-cat-new-input");
    const err = document.getElementById("graph-cat-modal-err");
    const raw = ((neu && neu.value) || "").trim().replace(/:/g, "·");
    if (!raw) {
      if (err) err.textContent = "分类名不能为空";
      return;
    }
    const existing = collectCustomTagOptions(graphModalPageCats);
    if (existing.indexOf(raw) >= 0) {
      if (err) err.textContent = "已在列表中，已勾选";
      const checked = getGraphCatModalSelected();
      if (checked.indexOf(raw) < 0) checked.push(raw);
      renderGraphCatModalList(checked);
      if (neu) neu.value = "";
      return;
    }
    if (err) err.textContent = "";
    if (graphModalExtraTags.indexOf(raw) < 0) graphModalExtraTags.push(raw);
    const checked = getGraphCatModalSelected();
    if (checked.indexOf(raw) < 0) checked.push(raw);
    renderGraphCatModalList(checked);
    if (neu) neu.value = "";
  }

  async function saveGraphCatModal() {
    if (!graphModalPath) return;
    const err = document.getElementById("graph-cat-modal-err");
    const cats = getGraphCatModalSelected();
    const res = await api("POST", "/api/pages/categories", {
      path: graphModalPath,
      categories: cats,
    });
    if (!res.ok) {
      if (err)
        err.textContent =
          "保存失败：" + ((res.error && res.error.message) || "error");
      return;
    }
    closeLwModal("graph-cat-modal");
    const hint = document.getElementById("graph-cat-edit-hint");
    if (hint) hint.textContent = "已保存分类 · " + graphModalPath;
    setGraphCatEditor(
      graphModalPath,
      (res.data && res.data.categories) || cats,
      (document.getElementById("graph-title-input") || {}).value || ""
    );
    refreshWikiGraph();
    refreshPages();
  }

  async function deleteGraphPageFromCtx() {
    hideGraphCtxMenu();
    const path = graphPreviewPath;
    if (!path) return;
    const title =
      (document.getElementById("graph-title-input") || {}).value || path;
    if (!confirm("删除页面「" + title + "」？\n" + path)) return;
    const out = await api("POST", "/api/pages/delete", { path });
    if (!out.ok) {
      alert((out.error && out.error.message) || "删除失败");
      return;
    }
    const hint = document.getElementById("graph-cat-edit-hint");
    if (hint) hint.textContent = "已删除 · " + path;
    const prev = document.getElementById("page-preview-graph");
    if (prev) prev.textContent = "页面已删除";
    if (wbPagePath === path) {
      wbPagePath = null;
      const ta = document.getElementById("page-preview");
      if (ta) ta.value = "";
      const btn = document.getElementById("btn-save-page");
      if (btn) btn.disabled = true;
      if (typeof clearEgoGraph === "function") clearEgoGraph();
    }
    graphPreviewPath = null;
    setGraphCatEditor(null, [], "");
    refreshWikiGraph();
    refreshPages();
  }

  function bindLwModalDrag() {
    let drag = null;
    document.querySelectorAll(".lw-modal-drag-handle").forEach((handle) => {
      handle.addEventListener("mousedown", (ev) => {
        if (ev.button !== 0) return;
        if (ev.target.closest && ev.target.closest("[data-lw-modal-close]"))
          return;
        if (ev.target.classList && ev.target.classList.contains("btn-close"))
          return;
        const cardId = handle.getAttribute("data-lw-drag-for");
        const card = cardId && document.getElementById(cardId);
        if (!card) return;
        ev.preventDefault();
        const rect = card.getBoundingClientRect();
        card.classList.add("lw-modal-card--dragging");
        card.style.width = rect.width + "px";
        card.style.left = rect.left + "px";
        card.style.top = rect.top + "px";
        drag = {
          card,
          ox: ev.clientX - rect.left,
          oy: ev.clientY - rect.top,
        };
      });
    });
    document.addEventListener("mousemove", (ev) => {
      if (!drag) return;
      const pad = 8;
      const w = drag.card.offsetWidth || 320;
      const h = drag.card.offsetHeight || 200;
      let left = ev.clientX - drag.ox;
      let top = ev.clientY - drag.oy;
      left = Math.max(pad, Math.min(left, (window.innerWidth || 800) - w - pad));
      top = Math.max(pad, Math.min(top, (window.innerHeight || 600) - h - pad));
      drag.card.style.left = left + "px";
      drag.card.style.top = top + "px";
    });
    document.addEventListener("mouseup", () => {
      drag = null;
    });
  }

  async function saveGraphTitleModal() {
    if (!graphModalPath) return;
    const inp = document.getElementById("graph-modal-title-input");
    const err = document.getElementById("graph-title-modal-err");
    const title = ((inp && inp.value) || "").trim();
    if (!title) {
      if (err) err.textContent = "名称不能为空";
      return;
    }
    const res = await api("POST", "/api/pages/title", {
      path: graphModalPath,
      title,
    });
    if (!res.ok) {
      if (err)
        err.textContent =
          "保存失败：" + ((res.error && res.error.message) || "error");
      return;
    }
    closeLwModal("graph-title-modal");
    const hint = document.getElementById("graph-cat-edit-hint");
    if (hint) hint.textContent = "已保存名称 · " + graphModalPath;
    setGraphCatEditor(
      graphModalPath,
      ((document.getElementById("graph-cat-input") || {}).value || "")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      title
    );
    refreshWikiGraph();
    refreshPages();
  }

  async function saveGraphTitle() {
    // legacy side panel hook (hidden); prefer modal
    openGraphTitleModal(
      graphPreviewPath,
      (document.getElementById("graph-title-input") || {}).value || ""
    );
  }

  async function saveGraphCategories() {
    openGraphCatModal(
      graphPreviewPath,
      ((document.getElementById("graph-cat-input") || {}).value || "")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean)
    );
  }

  function onGraphPageContext(ev, nodes) {
    const oe = ev.originalEvent || ev;
    if (oe && typeof oe.preventDefault === "function") oe.preventDefault();
    if (oe && typeof oe.stopPropagation === "function") oe.stopPropagation();
    const target = ev.target;
    if (!target || typeof target.data !== "function") return;
    const kind = target.data("kind");
    if (kind !== "page") {
      hideGraphCtxMenu();
      return;
    }
    const path = target.data("path");
    if (!path) return;
    const n = (nodes || []).find((x) => x.path === path || x.id === path);
    const x = oe && oe.clientX != null ? oe.clientX : 0;
    const y = oe && oe.clientY != null ? oe.clientY : 0;
    showPagePreview(path, "page-preview-graph");
    showGraphCtxMenu(
      x,
      y,
      path,
      (n && n.categories) || [],
      (n && (n.title || n.label)) || target.data("label") || ""
    );
  }

  async function refreshWikiGraph() {
    const host = document.getElementById("wiki-graph-cy");
    const statsEl = document.getElementById("graph-stats");
    const emptyHint = document.getElementById("graph-empty-hint");
    if (!host) return;
    if (wikiCy) {
      wikiCy.destroy();
      wikiCy = null;
    }
    if (emptyHint) {
      emptyHint.classList.add("d-none");
      emptyHint.textContent = "";
    }
    if (typeof cytoscape !== "function") {
      host.innerHTML =
        '<div class="p-3 text-danger">cytoscape.js 未加载（检查 CDN / 网络）</div>';
      return;
    }
    graphSelectedCats = graphSelectedFromChecks();
    const weakEl = document.getElementById("graph-weak");
    const hideEl = document.getElementById("graph-hide-isolates");
    const qEl = document.getElementById("graph-search");
    const weak = weakEl && weakEl.checked ? "1" : "0";
    const hide = !hideEl || hideEl.checked ? "1" : "0";
    const q = (qEl && qEl.value.trim()) || "";
    const expanded = graphSelectedCats.length > 0;
    let url =
      "/api/wiki/graph?view=" +
      (expanded ? "expanded" : "overview") +
      "&weak=" +
      weak +
      "&hide_isolates=" +
      hide +
      "&cluster=1";
    if (expanded) url += "&cats=" + encodeURIComponent(graphSelectedCats.join(","));
    if (q) url += "&q=" + encodeURIComponent(q);
    const res = await api("GET", url);
    if (!res.ok) {
      host.innerHTML =
        '<div class="p-3 text-danger">' +
        esc((res.error && res.error.message) || "图谱加载失败") +
        "</div>";
      if (statsEl) statsEl.textContent = "";
      return;
    }
    const g = res.data || {};
    const nodes = g.nodes || [];
    const edges = g.edges || [];
    const st = g.stats || {};
    const meta = g.meta || {};
    const view = g.view || (expanded ? "expanded" : "overview");
    if (!graphMetaCats.length || !document.getElementById("graph-cat-checks").children.length) {
      renderGraphCatChecks(meta.categories || []);
    } else if ((meta.categories || []).length) {
      // refresh labels/counts without wiping checks mid-render if already built once
      const sel = new Set(graphSelectedCats);
      renderGraphCatChecks(meta.categories || []);
      graphSelectedCats = Array.from(sel);
      // restore checks after rebuild
      const box = document.getElementById("graph-cat-checks");
      if (box) {
        box.querySelectorAll("input[type=checkbox]").forEach((el) => {
          el.checked = sel.has(el.value);
        });
      }
    }
    renderGraphLegend(meta.categories || [], view);
    const nCount = st.nodes != null ? st.nodes : nodes.length;
    const eCount = st.edges != null ? st.edges : edges.length;
    const uCount = st.unresolved != null ? st.unresolved : 0;
    if (statsEl) {
      statsEl.textContent =
        (view === "overview" ? "总览 · " : "展开 · ") +
        "节点 " +
        nCount +
        " · 边 " +
        eCount +
        " · 未解析 " +
        uCount +
        (meta.weak_edge_count != null
          ? " · 弱边库 " + meta.weak_edge_count
          : "");
    }
    if (!nodes.length) {
      host.innerHTML =
        '<div class="p-4 text-secondary">暂无 wiki 页面。导入内容或在 wiki/ 下添加 .md 后刷新。</div>';
      return;
    }
    if (eCount === 0 && emptyHint) {
      emptyHint.classList.remove("d-none");
      emptyHint.innerHTML =
        view === "overview"
          ? "大类之间暂无跨类链接。展开某一类可看类内页；或开启「弱边」后同目录/同标签会产生虚线边。"
          : "当前没有连线：强边来自 <code>[[wikilink]]</code>；也可开启「弱边」（同目录/同簇/同标签）。";
    }
    host.innerHTML = "";
    const elements = nodes
      .map((n) => {
        const isCat = n.kind === "category";
        const catId = isCat
          ? n.category_id || String(n.id || "").replace(/^cat:/, "")
          : n.dir_category || "other";
        const cluster =
          !isCat && n.clusters && n.clusters[0]
            ? "cl:" + (n.dir_category || "") + ":" + n.clusters[0]
            : undefined;
        return {
          data: {
            id: n.id,
            label: n.label || n.title || n.id,
            path: n.path || "",
            kind: n.kind || "page",
            category_id: catId,
            color: graphColorFor(catId),
            weight: n.member_count || 1,
            parent: cluster,
            match: n.match ? 1 : 0,
          },
        };
      })
      .concat(
        // compound parents for clusters
        (() => {
          const parents = {};
          for (const n of nodes) {
            if (n.kind === "category") continue;
            if (n.clusters && n.clusters[0]) {
              const pid = "cl:" + (n.dir_category || "") + ":" + n.clusters[0];
              parents[pid] = n.clusters[0];
            }
          }
          return Object.keys(parents).map((pid) => ({
            data: { id: pid, label: parents[pid], kind: "cluster" },
          }));
        })()
      )
      .concat(
        edges.map((e, i) => ({
          data: {
            id: "e" + i + "-" + e.source + "-" + e.target,
            source: e.source,
            target: e.target,
            kind: e.kind || "wikilink",
            weight: e.weight || 1,
          },
        }))
      );
    wikiCy = cytoscape({
      container: host,
      elements,
      style: [
        {
          selector: "node[kind='category']",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            color: "#050505",
            "font-size": 12,
            "font-weight": 600,
            "text-valign": "center",
            width: 56,
            height: 56,
            "text-wrap": "wrap",
            "text-max-width": 72,
          },
        },
        {
          selector: "node[kind='cluster']",
          style: {
            label: "data(label)",
            "background-opacity": 0.08,
            "border-width": 1,
            "border-color": "#bcc0c4",
            "font-size": 10,
            color: "#65676b",
            "text-valign": "top",
            padding: 12,
          },
        },
        {
          selector: "node[kind='page']",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            color: "#050505",
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 6,
            width: 26,
            height: 26,
            "text-wrap": "ellipsis",
            "text-max-width": 80,
          },
        },
        {
          selector: "edge[kind='wikilink']",
          style: {
            width: 1.5,
            "line-color": "#8a8d91",
            "target-arrow-color": "#8a8d91",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
        {
          selector: "edge[kind='weak']",
          style: {
            width: 1,
            "line-color": "#c5c9ce",
            "line-style": "dashed",
            "target-arrow-shape": "none",
            "curve-style": "bezier",
          },
        },
        {
          selector: "edge[kind='category_summary']",
          style: {
            width: "mapData(weight, 1, 20, 2, 8)",
            "line-color": "#1877f2",
            "target-arrow-shape": "none",
            "curve-style": "bezier",
            label: "data(weight)",
            "font-size": 10,
            color: "#65676b",
            "text-rotation": "autorotate",
          },
        },
      ],
      layout: pickGraphLayout(view, nodes, graphSelectedCats),
    });
    wikiCy.on("tap", "node", (ev) => {
      const kind = ev.target.data("kind");
      if (kind === "category") {
        const cid = ev.target.data("category_id");
        if (!cid) return;
        if (graphSelectedCats.indexOf(cid) < 0) graphSelectedCats.push(cid);
        const box = document.getElementById("graph-cat-checks");
        if (box) {
          box.querySelectorAll("input[type=checkbox]").forEach((el) => {
            if (el.value === cid) el.checked = true;
          });
        }
        refreshWikiGraph();
        return;
      }
      if (kind === "cluster") return;
      const path = ev.target.data("path");
      if (path) {
        showPagePreview(path, "page-preview-graph");
        const n = nodes.find((x) => x.path === path || x.id === path);
        setGraphCatEditor(
          path,
          (n && n.categories) || [],
          (n && (n.title || n.label)) || ""
        );
      }
    });
    wikiCy.on("cxttap", "node", (ev) => onGraphPageContext(ev, nodes));
    if (wikiCy.container()) {
      const hostEl = wikiCy.container();
      hostEl.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (!wikiCy) return;
        // Dual-bind show when cxttap misses; never hide on miss (avoids racing cxttap).
        let node = null;
        try {
          const rect = hostEl.getBoundingClientRect();
          const rx = e.clientX - rect.left;
          const ry = e.clientY - rect.top;
          const hits = wikiCy.elementsAtPoint
            ? wikiCy.elementsAtPoint(rx, ry)
            : null;
          if (hits && hits.length) {
            const n0 = hits.filter((x) => x.isNode && x.isNode());
            if (n0.length) node = n0[0];
          }
        } catch (_) {
          node = null;
        }
        if (!node) return;
        onGraphPageContext({ target: node, originalEvent: e }, nodes);
      });
    }
  }

  function pickGraphLayout(view, nodes, selectedCats) {
    // R3.28 Q4=B: overview = grid/concentric; expanded = breadthfirst/grid;
    // cose only for single class with <40 nodes.
    const n = (nodes && nodes.length) || 0;
    if (view === "overview") {
      return {
        name: n <= 12 ? "concentric" : "grid",
        animate: false,
        padding: 28,
        concentric: (node) => Number(node.data("member_count") || 1),
        levelWidth: () => 2,
        avoidOverlap: true,
        condense: true,
      };
    }
    const cats = selectedCats || [];
    if (cats.length === 1 && n > 0 && n < 40) {
      return { name: "cose", animate: false, padding: 28, nodeOverlap: 20 };
    }
    if (n > 80) {
      return { name: "grid", animate: false, padding: 28, avoidOverlap: true };
    }
    return {
      name: "breadthfirst",
      animate: false,
      padding: 28,
      directed: false,
      spacingFactor: 1.15,
      avoidOverlap: true,
    };
  }

  function formatCompileResult(label, out) {
    const d = out && out.data != null ? out.data : out;
    const dedup = (d && d.dedup) || {};
    const risk = (d && d.risk) || {};
    const auto = !!(d && d.auto_accepted);
    const lines = [label];
    if (dedup.message) lines.push(String(dedup.message));
    if (auto) {
      lines.push("已自动入库（低风险）。可在「知识库」查看。");
    } else {
      const reasons = (risk.reasons || []).join(", ") || "需人工确认";
      lines.push("未自动入库（" + reasons + "）。已切到本面板「待确认」Tab。");
    }
    lines.push("");
    lines.push(JSON.stringify(out, null, 2));
    return lines.join("\n");
  }

  async function refreshSources() {
    const ul = document.getElementById("source-list");
    ul.innerHTML = "";
    const data = await api("GET", "/api/sources");
    if (!data.ok) {
      ul.innerHTML =
        '<li class="list-group-item text-danger">' +
        esc((data.error && data.error.message) || "error") +
        "</li>";
      return;
    }
    const items = data.data || [];
    if (!items.length) {
      ul.innerHTML = '<li class="list-group-item">暂无来源</li>';
      return;
    }
    for (const s of items) {
      const li = document.createElement("li");
      li.className = "list-group-item card-row";
      const meta = document.createElement("div");
      meta.innerHTML =
        "<strong>" +
        esc(s.title || s.filename) +
        '</strong><br/><span class="text-secondary small">' +
        esc(s.source_uid) +
        " · " +
        esc(s.format) +
        "</span>";
      const actions = document.createElement("div");
      actions.className = "d-flex flex-wrap gap-2";
      const compile = document.createElement("button");
      compile.className = "btn btn-sm btn-primary";
      compile.textContent = "模型整理";
      compile.onclick = async () => {
        try {
          const out = await api("POST", "/api/compile", {
            source_uid: s.source_uid,
            version: s.version,
            mode: "model",
          });
          if (!out.ok) {
            throw new Error(
              (out.error && out.error.message) || "模型整理失败"
            );
          }
          document.getElementById("import-out").textContent = formatCompileResult(
            "模型整理完成（knowledge-ingest / knowledge_compile）。",
            out
          );
          afterCompileNavigate(out);
        } catch (e) {
          document.getElementById("import-out").textContent =
            "模型整理失败（请检查设置·六路 knowledge_compile / Key）。\n\n" +
            String(e && e.message ? e.message : e);
        }
      };
      const weak = document.createElement("button");
      weak.className = "btn btn-sm btn-outline-secondary";
      weak.textContent = "弱整理（启发式）";
      weak.title = "不经大模型；仅显式降级";
      weak.onclick = async () => {
        try {
          const out = await api("POST", "/api/compile", {
            source_uid: s.source_uid,
            version: s.version,
            mode: "heuristic",
          });
          if (!out.ok) {
            throw new Error((out.error && out.error.message) || "弱整理失败");
          }
          document.getElementById("import-out").textContent = formatCompileResult(
            "弱整理完成（启发式 + knowledge-ingest 字段）。",
            out
          );
          afterCompileNavigate(out);
        } catch (e) {
          document.getElementById("import-out").textContent =
            "弱整理失败：\n" + String(e && e.message ? e.message : e);
        }
      };
      actions.appendChild(compile);
      actions.appendChild(weak);
      li.appendChild(meta);
      li.appendChild(actions);
      ul.appendChild(li);
    }
  }

  async function doImport() {
    const input = document.getElementById("import-file");
    const file = input.files && input.files[0];
    if (!file) {
      document.getElementById("import-out").textContent = "请选择文件";
      return;
    }
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);
    const out = await api("POST", "/api/import", {
      filename: file.name,
      content_b64: b64,
    });
    document.getElementById("import-out").textContent = JSON.stringify(out, null, 2);
    refreshSources();
  }

  function renderAgentResult(out) {
    const card = document.getElementById("agent-result-card");
    const raw = document.getElementById("agent-out");
    if (raw) raw.textContent = JSON.stringify(out, null, 2);
    if (!card) return;
    const ok = !!(out && out.ok);
    const d = (out && out.data) || {};
    const err = (out && out.error) || {};
    const summary =
      d.summary ||
      d.message ||
      d.output ||
      (typeof d.result === "string" ? d.result : "") ||
      err.message ||
      (ok ? "执行完成" : "执行失败");
    const clip =
      typeof summary === "string"
        ? summary.slice(0, 400)
        : JSON.stringify(summary).slice(0, 400);
    card.innerHTML =
      '<div class="card agent-result-card border-' +
      (ok ? "success" : "danger") +
      '"><div class="card-body">' +
      '<div class="card-title fw-semibold ' +
      (ok ? "text-success" : "text-danger") +
      '">' +
      (ok ? "成功" : "失败") +
      "</div>" +
      '<p class="card-text mb-0 small">' +
      esc(clip) +
      "</p>" +
      (d.runtime || d.runtime_used
        ? '<p class="text-secondary small mt-2 mb-0">runtime: ' +
          esc(d.runtime || d.runtime_used) +
          "</p>"
        : "") +
      "</div></div>";
  }

  async function runAgent() {
    const goal = document.getElementById("agent-goal").value.trim();
    const runtime = document.getElementById("agent-runtime").value || "opencode";
    const card = document.getElementById("agent-result-card");
    if (card) {
      card.innerHTML =
        '<div class="card border-secondary"><div class="card-body text-secondary">执行中…</div></div>';
    }
    const out = await api("POST", "/api/agent/exec", { goal, runtime });
    renderAgentResult(out);
  }

  async function refreshPersonal() {
    document.getElementById("personal-out").textContent = JSON.stringify(
      await api("GET", "/api/personal/context"),
      null,
      2
    );
  }

  document.getElementById("tabs").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-panel]");
    if (!btn) return;
    showPanel(btn.dataset.panel);
    const n = btn.dataset.panel;
    if (n === "status") refreshStatus();
    if (n === "settings") loadSettings();
    if (n === "workspace") {
      refreshPages();
      const graphPane = document.getElementById("pane-wiki-graph");
      if (graphPane && graphPane.classList.contains("active")) refreshWikiGraph();
    }
    if (n === "import") {
      showImportSubTab("sources");
      refreshSources();
    }
    if (n === "personal") refreshPersonal();
    if (n === "evolution") refreshEvolution();
    if (n === "agent") refreshWorkbench();
  });

  const tabWikiGraph = document.getElementById("tab-wiki-graph");
  if (tabWikiGraph) {
    tabWikiGraph.addEventListener("shown.bs.tab", () => refreshWikiGraph());
  }
  const tabImportProposals = document.getElementById("tab-import-proposals");
  if (tabImportProposals) {
    tabImportProposals.addEventListener("shown.bs.tab", () => refreshProposals());
  }

  document.getElementById("btn-refresh-status").onclick = refreshStatus;
  document.getElementById("btn-load-settings").onclick = loadSettings;
  document.getElementById("btn-probe-models").onclick = probeModels;
  document.getElementById("btn-sync-models").onclick = syncModels;
  document.getElementById("btn-save-models").onclick = saveModelsForm;
  document.getElementById("btn-refresh-ollama").onclick = refreshOllama;
  const btnSaveProv = document.getElementById("btn-save-one-provider");
  if (btnSaveProv) btnSaveProv.onclick = saveOneProvider;
  document.getElementById("btn-save-autonomy").onclick = saveAutonomy;
  document.getElementById("btn-skills-search").onclick = searchSkills;
  const btnBootOffice = document.getElementById("btn-skills-bootstrap-office");
  if (btnBootOffice) btnBootOffice.onclick = bootstrapOfficeSkills;
  document.getElementById("btn-skills-refresh").onclick = refreshSkillsPanel;
  document.getElementById("btn-skills-install-url").onclick = () => installFromSource("skill");
  document.getElementById("btn-harness-install-url").onclick = () =>
    installFromSource("harness");
  const btnGhSave = document.getElementById("btn-skills-github-token-save");
  if (btnGhSave) btnGhSave.onclick = saveGithubToken;
  const btnGhClear = document.getElementById("btn-skills-github-token-clear");
  if (btnGhClear) btnGhClear.onclick = clearGithubToken;
  wireSkillsSearchInstall();
  refreshGithubTokenStatus();
  document.getElementById("btn-refresh-proposals").onclick = refreshProposals;
  const btnDedupScan = document.getElementById("btn-dedup-scan");
  if (btnDedupScan) {
    btnDedupScan.onclick = async () => {
      btnDedupScan.disabled = true;
      try {
        const res = await api("POST", "/api/wiki/dedup-scan", { apply: true });
        const d = (res && res.data) || {};
        alert(
          "去重扫描完成：合并 " +
            ((d.merged && d.merged.length) || 0) +
            " · 提案 " +
            ((d.proposals && d.proposals.length) || 0) +
            " · 跳过 " +
            ((d.skipped && d.skipped.length) || 0)
        );
        refreshProposals();
        refreshPages();
      } catch (e) {
        alert("去重扫描失败：" + (e && e.message ? e.message : e));
      } finally {
        btnDedupScan.disabled = false;
      }
    };
  }
  const showAllEl = document.getElementById("proposals-show-all");
  if (showAllEl) {
    showAllEl.onchange = () => {
      showAllProposals = !!showAllEl.checked;
      refreshProposals();
    };
  }
  const btnBatchAccept = document.getElementById("btn-batch-accept");
  if (btnBatchAccept) btnBatchAccept.onclick = () => batchReview("accept");
  const btnBatchReject = document.getElementById("btn-batch-reject");
  if (btnBatchReject) btnBatchReject.onclick = () => batchReview("reject");
  document.getElementById("btn-refresh-pages").onclick = refreshPages;
  const wikiPagesSelectAll = document.getElementById("wiki-pages-select-all");
  if (wikiPagesSelectAll) {
    wikiPagesSelectAll.onchange = () => {
      const on = !!wikiPagesSelectAll.checked;
      document.querySelectorAll("#page-list .wiki-page-cb").forEach((cb) => {
        cb.checked = on;
      });
      wikiPagesSelectAll.indeterminate = false;
      syncWikiPagesSelectAllState();
    };
  }
  const btnWikiPagesDelete = document.getElementById("btn-wiki-pages-delete");
  if (btnWikiPagesDelete) btnWikiPagesDelete.onclick = deleteSelectedWikiPages;
  const pageTypeFilter = document.getElementById("page-type-filter");
  if (pageTypeFilter) {
    pageTypeFilter.onchange = () => {
      wikiPageFilter = pageTypeFilter.value || "";
      renderPageList();
    };
  }
  const btnSavePage = document.getElementById("btn-save-page");
  if (btnSavePage) btnSavePage.onclick = savePage;
  const btnRefreshGraph = document.getElementById("btn-refresh-graph");
  if (btnRefreshGraph) btnRefreshGraph.onclick = refreshWikiGraph;
  const btnGraphSaveTitle = document.getElementById("btn-graph-save-title");
  if (btnGraphSaveTitle) btnGraphSaveTitle.onclick = saveGraphTitle;
  const ctxCats = document.getElementById("graph-ctx-edit-cats");
  if (ctxCats) {
    ctxCats.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      hideGraphCtxMenu();
      const raw = (document.getElementById("graph-cat-input") || {}).value || "";
      openGraphCatModal(
        graphPreviewPath,
        raw
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean)
      );
    };
  }
  const ctxTitle = document.getElementById("graph-ctx-edit-title");
  if (ctxTitle) {
    ctxTitle.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      hideGraphCtxMenu();
      openGraphTitleModal(
        graphPreviewPath,
        (document.getElementById("graph-title-input") || {}).value || ""
      );
    };
  }
  const ctxDel = document.getElementById("graph-ctx-delete");
  if (ctxDel) {
    ctxDel.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteGraphPageFromCtx();
    };
  }
  document.addEventListener("click", (ev) => {
    if (Date.now() < graphCtxIgnoreClickUntil) return;
    const menu = document.getElementById("graph-ctx-menu");
    if (menu && !menu.classList.contains("d-none") && !menu.contains(ev.target)) {
      hideGraphCtxMenu();
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    hideGraphCtxMenu();
    closeLwModal("graph-cat-modal");
    closeLwModal("graph-title-modal");
  });
  document.querySelectorAll("[data-lw-modal-close]").forEach((el) => {
    el.addEventListener("click", () => {
      closeLwModal(el.getAttribute("data-lw-modal-close"));
    });
  });
  const catSelect = document.getElementById("graph-cat-modal-select");
  if (catSelect) {
    catSelect.addEventListener("change", () => {
      syncGraphCatModalChecks(
        Array.from(catSelect.selectedOptions).map((o) => o.value)
      );
    });
  }
  const btnCatAdd = document.getElementById("btn-graph-cat-add");
  if (btnCatAdd) btnCatAdd.onclick = addGraphCatFromModalInput;
  const catNewInp = document.getElementById("graph-cat-new-input");
  if (catNewInp) {
    catNewInp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        addGraphCatFromModalInput();
      }
    });
  }
  const btnCatModalSave = document.getElementById("btn-graph-cat-modal-save");
  if (btnCatModalSave) btnCatModalSave.onclick = saveGraphCatModal;
  const btnTitleModalSave = document.getElementById("btn-graph-title-modal-save");
  if (btnTitleModalSave) btnTitleModalSave.onclick = saveGraphTitleModal;
  bindLwModalDrag();
  const graphWeak = document.getElementById("graph-weak");
  if (graphWeak) graphWeak.onchange = () => refreshWikiGraph();
  const graphHide = document.getElementById("graph-hide-isolates");
  if (graphHide) graphHide.onchange = () => refreshWikiGraph();
  const graphSearch = document.getElementById("graph-search");
  if (graphSearch) {
    let t = null;
    graphSearch.oninput = () => {
      clearTimeout(t);
      t = setTimeout(() => refreshWikiGraph(), 280);
    };
  }
  const btnGraphSaveCats = document.getElementById("btn-graph-save-cats");
  if (btnGraphSaveCats) btnGraphSaveCats.onclick = saveGraphCategories;
  document.getElementById("btn-refresh-sources").onclick = refreshSources;
  document.getElementById("btn-import").onclick = doImport;
  document.getElementById("btn-agent-run").onclick = runAgent;
  const btnWbNew = document.getElementById("btn-wb-new");
  if (btnWbNew) btnWbNew.onclick = newWorkbenchSession;
  const btnWbRefresh = document.getElementById("btn-wb-refresh");
  if (btnWbRefresh) btnWbRefresh.onclick = refreshWorkbench;
  const btnWbSend = document.getElementById("btn-wb-send");
  if (btnWbSend) btnWbSend.onclick = sendWorkbenchMessage;
  const btnWbContinue = document.getElementById("btn-wb-continue");
  if (btnWbContinue) btnWbContinue.onclick = continueWorkbenchSession;
  const btnWbStop = document.getElementById("btn-wb-stop");
  if (btnWbStop) btnWbStop.onclick = stopActiveJob;
  const wbSessionsSelectAll = document.getElementById("wb-sessions-select-all");
  if (wbSessionsSelectAll) {
    wbSessionsSelectAll.onchange = () => {
      const on = !!wbSessionsSelectAll.checked;
      document.querySelectorAll("#wb-session-list .wb-session-cb").forEach((cb) => {
        cb.checked = on;
      });
      wbSessionsSelectAll.indeterminate = false;
      syncWbSessionsSelectAllState();
    };
  }
  const btnWbSessionsDelete = document.getElementById("btn-wb-sessions-delete");
  if (btnWbSessionsDelete) btnWbSessionsDelete.onclick = deleteSelectedSessions;
  const btnWbDelete = document.getElementById("btn-wb-delete");
  if (btnWbDelete) {
    btnWbDelete.onclick = async () => {
      if (!wbSessionId) return;
      if (!confirm("删除当前会话？")) return;
      await api("DELETE", "/api/sessions/" + encodeURIComponent(wbSessionId));
      clearWorkbenchSessionIf(wbSessionId);
      refreshWorkbench();
    };
  }
  const btnWbClear = document.getElementById("btn-wb-clear");
  if (btnWbClear) {
    btnWbClear.onclick = async () => {
      if (!confirm("清空全部会话？可保留已提炼引用。")) return;
      const keep = confirm("是否保留已提炼过的会话？\n确定=保留 · 取消=全部删除");
      await api("POST", "/api/sessions/clear", {
        confirm: true,
        keep_extracted: keep,
      });
      wbSessionId = null;
      refreshWorkbench();
    };
  }
  const btnSaveWbPresets = document.getElementById("btn-save-wb-presets");
  if (btnSaveWbPresets) btnSaveWbPresets.onclick = saveWorkbenchPresetsForm;
  const btnOcStatus = document.getElementById("btn-oc-status");
  if (btnOcStatus) btnOcStatus.onclick = refreshOcStatus;
  const btnOcCheck = document.getElementById("btn-oc-check");
  if (btnOcCheck) btnOcCheck.onclick = checkOcUpdate;
  const wbInput = document.getElementById("wb-input");
  if (wbInput) {
    wbInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        sendWorkbenchMessage();
      }
    });
  }
  const wbPreset = document.getElementById("wb-preset");
  if (wbPreset) {
    wbPreset.onchange = async () => {
      await api("POST", "/api/settings/workbench-presets/select", {
        preset_id: wbPreset.value,
      });
    };
  }
  document.getElementById("btn-personal-ctx").onclick = refreshPersonal;
  document.getElementById("btn-evo-status").onclick = refreshEvolution;
  document.getElementById("btn-evo-pause").onclick = async () => {
    document.getElementById("evolution-out").textContent = JSON.stringify(
      await api("POST", "/api/evolution/pause", { pause: true }),
      null,
      2
    );
  };
  document.getElementById("btn-evo-resume").onclick = async () => {
    document.getElementById("evolution-out").textContent = JSON.stringify(
      await api("POST", "/api/evolution/pause", { pause: false }),
      null,
      2
    );
  };
  document.getElementById("btn-nightly-run").onclick = async () => {
    document.getElementById("evolution-out").textContent = JSON.stringify(
      await api("POST", "/api/nightly/run", {}),
      null,
      2
    );
  };
  document.getElementById("btn-evo-rollback").onclick = async () => {
    const version_id = document.getElementById("evo-rollback-id").value.trim();
    document.getElementById("evolution-out").textContent = JSON.stringify(
      await api("POST", "/api/evolution/rollback", { version_id }),
      null,
      2
    );
  };
  document.getElementById("btn-save-token").onclick = () => {
    setToken(document.getElementById("token-input").value.trim());
    dbg("Token 已保存到 localStorage");
  };
  document.getElementById("btn-fetch-token").onclick = async () => {
    const out = await fetchLocalToken();
    await refreshStatus();
    if (!out.ok) {
      document.getElementById("status-out").textContent = JSON.stringify(out, null, 2);
    }
  };
  document.getElementById("btn-fetch-token-settings").onclick = async () => {
    const out = await fetchLocalToken();
    dbg(out.ok ? "已从服务读取 Token 并写入 localStorage" : out);
    renderTokenDisplay();
  };
  document.getElementById("btn-copy-token").onclick = async () => {
    const t = tokenPlain || token();
    if (!t) return;
    try {
      await navigator.clipboard.writeText(t);
    } catch (_) {
      /* ignore */
    }
  };
  document.getElementById("btn-toggle-token").onclick = () => {
    tokenVisible = !tokenVisible;
    renderTokenDisplay();
  };

  tokenPlain = token();
  renderTokenDisplay();
  showPanel("agent");
  refreshWorkbench();
  refreshStatus();
})();
