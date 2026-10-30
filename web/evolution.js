/**
 * LocalWiki 自进化页面（LW-46 / §23.14）
 * 能力分组：学习记录 / 改进提案 / 已应用 / 效果趋势 / 设置
 */
(function (global) {
  "use strict";

  var TABS = [
    { id: "learnings", label: "学习记录" },
    { id: "proposals", label: "改进提案" },
    { id: "applied", label: "已应用" },
    { id: "metrics", label: "效果趋势" },
    { id: "settings", label: "设置" },
  ];

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function filterLearnings(rows, filters) {
    filters = filters || {};
    return (rows || []).filter(function (r) {
      if (filters.kind && r.kind !== filters.kind) return false;
      if (filters.area && r.area !== filters.area) return false;
      if (filters.status && r.status !== filters.status) return false;
      if (filters.pattern_key && String(r.pattern_key || "") !== filters.pattern_key)
        return false;
      return true;
    });
  }

  function renderTabs(activeId, mountEl) {
    var html = ['<nav class="evo-tabs" data-group="能力">'];
    TABS.forEach(function (t) {
      html.push(
        '<button type="button" class="evo-tab' +
          (t.id === activeId ? " active" : "") +
          '" data-tab="' +
          escapeHtml(t.id) +
          '">' +
          escapeHtml(t.label) +
          "</button>"
      );
    });
    html.push("</nav>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  function renderLearnings(rows, filters, mountEl) {
    var filtered = filterLearnings(rows, filters);
    var html = ['<section class="evo-learnings"><h2>学习记录</h2>'];
    html.push(
      "<p>筛选：类型 / 领域 / 状态 / Pattern-Key（共 " +
        filtered.length +
        " 条）</p>"
    );
    html.push("<ul>");
    filtered.forEach(function (r) {
      html.push("<li>");
      html.push("<code>" + escapeHtml(r.record_id || "") + "</code> ");
      html.push(escapeHtml(r.kind || "") + " · " + escapeHtml(r.area || "") + " · ");
      html.push(escapeHtml(r.status || "") + " · ");
      html.push("<em>" + escapeHtml(r.pattern_key || "") + "</em>");
      html.push("<p>" + escapeHtml(r.summary || "") + "</p>");
      html.push("</li>");
    });
    html.push("</ul></section>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  function renderProposals(proposals, mountEl) {
    var html = ['<section class="evo-proposals"><h2>改进提案</h2><ul>'];
    (proposals || []).forEach(function (p) {
      html.push("<li data-uid='" + escapeHtml(p.proposal_uid || "") + "'>");
      html.push("<strong>" + escapeHtml(p.proposal_uid || "") + "</strong> ");
      html.push(escapeHtml(p.risk_tier || "") + " · " + escapeHtml(p.status || ""));
      html.push("<pre>" + escapeHtml(p.diff || "") + "</pre>");
      if (p.eval_result) {
        html.push(
          "<details><summary>评估结果</summary><pre>" +
            escapeHtml(JSON.stringify(p.eval_result, null, 2)) +
            "</pre></details>"
        );
      }
      html.push(
        '<button type="button" data-action="approve">批准</button> ' +
          '<button type="button" data-action="reject">拒绝</button>'
      );
      html.push("</li>");
    });
    html.push("</ul></section>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  function renderApplied(versions, mountEl) {
    var html = ['<section class="evo-applied"><h2>已应用</h2><ul>'];
    (versions || []).forEach(function (v) {
      html.push("<li>");
      html.push("<code>" + escapeHtml(v.version || "") + "</code> ");
      html.push(escapeHtml(v.proposal_uid || "") + " · " + escapeHtml(v.status || ""));
      html.push(
        ' <button type="button" data-action="rollback" data-version="' +
          escapeHtml(v.version || "") +
          '">回滚</button>'
      );
      html.push("</li>");
    });
    html.push("</ul></section>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  function renderMetrics(series, mountEl) {
    var html = ['<section class="evo-metrics"><h2>效果趋势</h2>'];
    html.push("<ul>");
    ["task_success", "user_edit_rate", "tool_error_rate"].forEach(function (k) {
      var pts = (series && series[k]) || [];
      html.push(
        "<li>" +
          escapeHtml(k) +
          ": " +
          escapeHtml(pts.map(String).join(", ") || "(暂无)") +
          "</li>"
      );
    });
    html.push("</ul></section>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  function renderSettings(settings, mountEl) {
    settings = settings || {};
    var html = ['<section class="evo-settings"><h2>设置</h2><ul>'];
    html.push("<li>自主级别：" + escapeHtml(settings.level || "E1") + "</li>");
    html.push("<li>每日预算：" + escapeHtml(settings.daily_budget || "default") + "</li>");
    html.push(
      "<li>监测期：" +
        escapeHtml(settings.monitor_tasks || 20) +
        " 任务 / " +
        escapeHtml(settings.monitor_days || 7) +
        " 天</li>"
    );
    html.push("<li>规则上限：" + escapeHtml(settings.max_rules || 50) + "</li>");
    html.push("</ul></section>");
    if (mountEl) mountEl.innerHTML = html.join("");
    return html.join("");
  }

  /**
   * Mount full 自进化 page shell into mountEl.
   * @param {object} data
   * @param {HTMLElement} mountEl
   * @param {string} [activeTab]
   */
  function renderEvolutionPage(data, mountEl, activeTab) {
    data = data || {};
    activeTab = activeTab || "learnings";
    var wrap = document.createElement
      ? null
      : null;
    var html = ['<div class="evolution-page" data-section="能力">'];
    html.push("<h1>自进化</h1>");
    html.push(renderTabs(activeTab));
    html.push('<div class="evo-panel">');
    if (activeTab === "learnings") {
      html.push(renderLearnings(data.learnings, data.filters));
    } else if (activeTab === "proposals") {
      html.push(renderProposals(data.proposals));
    } else if (activeTab === "applied") {
      html.push(renderApplied(data.versions));
    } else if (activeTab === "metrics") {
      html.push(renderMetrics(data.metrics));
    } else if (activeTab === "settings") {
      html.push(renderSettings(data.settings));
    }
    html.push("</div></div>");
    var out = html.join("");
    if (mountEl) mountEl.innerHTML = out;
    return out;
  }

  var api = {
    TABS: TABS,
    escapeHtml: escapeHtml,
    filterLearnings: filterLearnings,
    renderTabs: renderTabs,
    renderLearnings: renderLearnings,
    renderProposals: renderProposals,
    renderApplied: renderApplied,
    renderMetrics: renderMetrics,
    renderSettings: renderSettings,
    renderEvolutionPage: renderEvolutionPage,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  global.LocalWikiEvolution = api;
})(typeof window !== "undefined" ? window : globalThis);
