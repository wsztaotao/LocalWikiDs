/**
 * LocalWiki Skill Hub UI helpers (LW-32 / §13).
 * Search results with expandable score breakdown; install confirmation;
 * tamper / disabled state display.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /**
   * @param {{query:string, expanded_terms:string[], results:Array}} searchResult
   * @param {HTMLElement} mountEl
   */
  function renderSearchResults(searchResult, mountEl) {
    if (!mountEl) return;
    var r = searchResult || {};
    var html = [];
    html.push('<div class="skills-search">');
    html.push("<h2>Skill 搜索</h2>");
    html.push(
      "<p>查询：" +
        escapeHtml(r.query || "") +
        " · 扩展词：" +
        escapeHtml((r.expanded_terms || []).join(", ")) +
        "</p>"
    );
    if (r.rate_limited) {
      html.push('<p class="text-warning">GitHub 搜索可能已限流，结果可能不完整。</p>');
    }
    var errs = r.source_errors || {};
    if (errs.github_code === "auth_required" || (!r.github_token_configured && errs.hint)) {
      html.push(
        '<p class="text-warning">代码搜索需 GitHub Token（上方可配置）；已尝试仓库搜索。配置 Token 后可发现更多含 SKILL.md 的技能。</p>'
      );
    }
    var errKeys = Object.keys(errs).filter(function (k) {
      return k !== "hint";
    });
    if (errKeys.length) {
      html.push('<p class="text-secondary small">源状态：');
      html.push(
        escapeHtml(
          errKeys
            .map(function (k) {
              return k + "=" + errs[k];
            })
            .join(" · ")
        )
      );
      html.push("</p>");
    }
    var results = r.results || [];
    if (!results.length) {
      html.push(
        '<p class="text-secondary">无相关可安装结果。请配置 GitHub Token 后重试，或换关键词 / 用下方 URL 安装。</p>'
      );
      html.push("</div>");
      mountEl.innerHTML = html.join("");
      return;
    }
    html.push('<ul class="skills-results">');
    results.forEach(function (item, idx) {
      var hit = item.hit || item;
      var bd = item.breakdown || {};
      html.push('<li class="skills-result" data-idx="' + idx + '">');
      html.push("<strong>" + escapeHtml(hit.name || item.name || "") + "</strong>");
      html.push(" <span class='kind'>" + escapeHtml(hit.kind || item.kind || "") + "</span>");
      if (hit.in_curated_index || item.in_curated_index) {
        html.push(" <span class='badge'>精选索引</span>");
      }
      html.push("<p>" + escapeHtml(hit.description || "") + "</p>");
      html.push(
        "<p>stars=" +
          escapeHtml(hit.stars != null ? hit.stars : item.stars) +
          " · score=" +
          escapeHtml(bd.score != null ? bd.score : item.score) +
          "</p>"
      );
      html.push("<details><summary>分项分数</summary><pre>");
      html.push(escapeHtml(JSON.stringify(bd, null, 2)));
      html.push("</pre></details>");
      var kind = String(hit.kind || item.kind || "agent_skill");
      var installKind = kind.indexOf("harness") >= 0 ? "harness" : "skill";
      var repo = String(hit.repo || "");
      var path = String(hit.path || "");
      var url = String(hit.url || "");
      var fakeLocal =
        repo.indexOf("localwiki/") === 0 || repo === "localwiki/voice-speech-hub";
      var canInstall =
        !fakeLocal && !!(repo || (url && url.indexOf("github.com") >= 0));
      html.push(
        '<button type="button" class="btn btn-sm btn-primary mt-1" data-action="install-request"' +
          ' data-name="' +
          escapeHtml(hit.name || "") +
          '" data-repo="' +
          escapeHtml(repo) +
          '" data-path="' +
          escapeHtml(path) +
          '" data-url="' +
          escapeHtml(url) +
          '" data-kind="' +
          escapeHtml(installKind) +
          '"' +
          (canInstall ? "" : " disabled title=\"无可安装源\"") +
          ">" +
          (canInstall ? "申请安装" : "不可安装") +
          "</button>"
      );
      html.push("</li>");
    });
    html.push("</ul></div>");
    mountEl.innerHTML = html.join("");
  }

  /**
   * @param {Array<{name:string,enabled:boolean,disabled_reason?:string,tree_sha256?:string}>} skills
   * @param {HTMLElement} mountEl
   */
  function renderInstalledSkills(skills, mountEl) {
    if (!mountEl) return;
    var html = ["<div class='skills-installed'><h2>已安装 Skill</h2><ul>"];
    (skills || []).forEach(function (s) {
      html.push("<li>");
      html.push("<strong>" + escapeHtml(s.name) + "</strong>");
      if (s.enabled === false) {
        html.push(" <span class='badge danger'>已禁用</span>");
        if (s.disabled_reason) {
          html.push(" <code>" + escapeHtml(s.disabled_reason) + "</code>");
        }
      } else {
        html.push(" <span class='badge ok'>启用</span>");
      }
      html.push("</li>");
    });
    html.push("</ul></div>");
    mountEl.innerHTML = html.join("");
  }

  /**
   * Confirm install UI payload (critical requires typing skill name).
   */
  function installConfirmPayload(scan, skillName, opts) {
    opts = opts || {};
    var maxLevel = (scan && scan.max_level) || "hint";
    var blocked = !!(scan && scan.block_install);
    return {
      skill_name: skillName,
      max_level: maxLevel,
      block_install: blocked,
      require_name_confirm: blocked,
      require_high_ack: maxLevel === "high",
      confirm_name: opts.confirm_name || "",
      acknowledge_high_risk: !!opts.acknowledge_high_risk,
      can_proceed:
        (!blocked || opts.confirm_name === skillName) &&
        (maxLevel !== "high" || opts.acknowledge_high_risk || blocked),
    };
  }

  global.LocalWikiSkills = {
    renderSearchResults: renderSearchResults,
    renderInstalledSkills: renderInstalledSkills,
    installConfirmPayload: installConfirmPayload,
  };
})(typeof window !== "undefined" ? window : globalThis);
