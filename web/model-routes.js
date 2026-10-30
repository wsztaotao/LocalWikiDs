/**
 * LocalWiki model routes UI helpers (LW-09).
 * Displays 13 feature routes; presets fill providers without downloading models.
 */
(function (global) {
  "use strict";

  var ROUTE_NAMES = [
    "chat",
    "agent",
    "knowledge_compile",
    "wiki_lint",
    "memory_extract",
    "memory_consolidate",
    "experience",
    "persona",
    "embedding",
    "vision",
    "skill_search_rerank",
    "nightly",
    "reflection",
  ];

  var ROUTE_LABELS = {
    chat: "对话",
    agent: "智能体",
    knowledge_compile: "知识编译",
    wiki_lint: "Wiki 体检",
    memory_extract: "记忆抽取",
    memory_consolidate: "记忆整合",
    experience: "经验",
    persona: "风格",
    embedding: "嵌入",
    vision: "视觉",
    skill_search_rerank: "Skill 重排",
    nightly: "夜间任务",
    reflection: "反思/自进化",
  };

  function applyPreset(config, preset, models) {
    // models: { local: "name", online: "name", embedding: "bge-m3" }
    models = models || {};
    var localModel = models.local || "";
    var onlineModel = models.online || "";
    var embedModel = models.embedding || "bge-m3";
    var providers = config.providers || {};
    var localProv = Object.keys(providers).find(function (k) {
      return providers[k].location === "local";
    });
    var onlineProv = Object.keys(providers).find(function (k) {
      return providers[k].location === "online";
    });
    localProv = localProv || "ollama_local";
    onlineProv = onlineProv || localProv;

    function ref(provider, model) {
      return { provider: provider, model: model || "", fallback: [] };
    }

    var routes = {};
    ROUTE_NAMES.forEach(function (name) {
      var useLocal =
        preset === "all_local" ||
        (preset === "hybrid" &&
          [
            "memory_extract",
            "memory_consolidate",
            "persona",
            "experience",
            "embedding",
            "reflection",
          ].indexOf(name) >= 0);
      if (preset === "all_online") useLocal = false;
      var provider = useLocal ? localProv : onlineProv;
      var model = name === "embedding" ? embedModel : useLocal ? localModel : onlineModel;
      routes[name] = {
        primary: { provider: provider, model: model },
        fallback: [],
      };
    });
    return Object.assign({}, config, { version: 2, routes: routes });
  }

  function renderRoutesTable(config, mountEl) {
    if (!mountEl) return;
    var routes = (config && config.routes) || {};
    var html = ['<table class="model-routes"><thead><tr>'];
    html.push("<th>路由</th><th>主模型</th><th>后备</th><th>隐私</th></tr></thead><tbody>");
    ROUTE_NAMES.forEach(function (name) {
      var r = routes[name] || { primary: {}, fallback: [] };
      var primary = (r.primary && r.primary.provider ? r.primary.provider + "/" : "") +
        ((r.primary && r.primary.model) || "(未选)");
      var fb = (r.fallback || [])
        .map(function (f) {
          return (f.provider || "") + "/" + (f.model || "");
        })
        .join(", ") || "—";
      html.push(
        "<tr data-route=\"" +
          name +
          "\"><td>" +
          (ROUTE_LABELS[name] || name) +
          "</td><td>" +
          primary +
          "</td><td>" +
          fb +
          "</td><td>" +
          (r.privacy || "") +
          "</td></tr>"
      );
    });
    html.push("</tbody></table>");
    mountEl.innerHTML = html.join("");
  }

  global.LocalWikiModelRoutes = {
    ROUTE_NAMES: ROUTE_NAMES,
    ROUTE_LABELS: ROUTE_LABELS,
    applyPreset: applyPreset,
    renderRoutesTable: renderRoutesTable,
  };
})(typeof window !== "undefined" ? window : globalThis);
