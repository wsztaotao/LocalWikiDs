/**
 * LocalWiki lint UI helpers (LW-07).
 * Renders lint report summaries; does not auto-modify wiki pages.
 */
(function (global) {
  "use strict";

  var CHECK_LABELS = {
    format: "格式",
    broken_links: "断链",
    orphan_pages: "孤立页",
    missing_concepts: "缺失概念",
    contradictions: "矛盾",
    stale_content: "过时",
    duplicates: "重复",
    source_gaps: "资料缺口",
    memory_experience_consistency: "记忆/经验一致性",
  };

  function groupByCheck(findings) {
    var groups = {};
    (findings || []).forEach(function (f) {
      var key = f.check || "unknown";
      if (!groups[key]) groups[key] = [];
      groups[key].push(f);
    });
    return groups;
  }

  function renderReport(report, mountEl) {
    if (!mountEl) return;
    var findings = (report && report.findings) || [];
    var groups = groupByCheck(findings);
    var html = [];
    html.push('<div class="lint-report">');
    html.push("<h2>知识库体检</h2>");
    html.push("<p>共 " + findings.length + " 项；不会自动改写页面。</p>");
    if (report && report.auto_modified_pages && report.auto_modified_pages.length) {
      html.push('<p class="lint-warn">意外：检测到自动修改列表（不应发生）</p>');
    }
    Object.keys(CHECK_LABELS).forEach(function (check) {
      var items = groups[check] || [];
      html.push('<section class="lint-check" data-check="' + check + '">');
      html.push(
        "<h3>" +
          CHECK_LABELS[check] +
          " <span class=\"count\">" +
          items.length +
          "</span></h3>"
      );
      if (!items.length) {
        html.push("<p class=\"ok\">无检出</p>");
      } else {
        html.push("<ul>");
        items.forEach(function (f) {
          html.push(
            "<li class=\"sev-" +
              (f.severity || "info") +
              "\">" +
              escapeHtml(f.message || "") +
              (f.path ? " <code>" + escapeHtml(f.path) + "</code>" : "") +
              (f.proposal
                ? ' <button type="button" data-proposal="' +
                  escapeHtml(f.proposal) +
                  '">查看提案</button>'
                : "") +
              "</li>"
          );
        });
        html.push("</ul>");
      }
      html.push("</section>");
    });
    html.push("</div>");
    mountEl.innerHTML = html.join("\n");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchAndRender(apiBase, mountEl) {
    var base = apiBase || "";
    var res = await fetch(base + "/api/wiki/lint", { method: "POST" });
    if (!res.ok) throw new Error("lint request failed: " + res.status);
    var report = await res.json();
    renderReport(report, mountEl);
    return report;
  }

  global.LocalWikiLint = {
    CHECK_LABELS: CHECK_LABELS,
    groupByCheck: groupByCheck,
    renderReport: renderReport,
    fetchAndRender: fetchAndRender,
  };
})(typeof window !== "undefined" ? window : globalThis);
