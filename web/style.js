/**
 * LocalWiki style page helpers (LW-24 / §10.3, §10.6).
 * - Side-by-side preview: with style vs without style
 * - Optional OCEAN panel (default off; never drives features)
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
   * @param {{question:string, with_style:{label:string, system_addon:string, answer?:string}, without_style:{label:string, system_addon:string, answer?:string}}} preview
   * @param {HTMLElement} mountEl
   */
  function renderStylePreview(preview, mountEl) {
    if (!mountEl) return;
    var p = preview || {};
    var withS = p.with_style || {};
    var withoutS = p.without_style || {};
    var html = [];
    html.push('<div class="style-preview">');
    html.push("<h2>风格预览</h2>");
    html.push("<p>示例问题：" + escapeHtml(p.question || "") + "</p>");
    html.push('<div class="style-preview-cols">');
    html.push('<section class="style-preview-col">');
    html.push("<h3>" + escapeHtml(withS.label || "使用风格") + "</h3>");
    if (withS.system_addon) {
      html.push(
        '<p class="style-addon"><code>' +
          escapeHtml(withS.system_addon) +
          "</code></p>"
      );
    }
    html.push(
      '<div class="style-answer">' +
        escapeHtml(withS.answer || "（等待模型回答）") +
        "</div>"
    );
    html.push("</section>");
    html.push('<section class="style-preview-col">');
    html.push("<h3>" + escapeHtml(withoutS.label || "不使用风格") + "</h3>");
    if (withoutS.system_addon) {
      html.push(
        '<p class="style-addon"><code>' +
          escapeHtml(withoutS.system_addon) +
          "</code></p>"
      );
    }
    html.push(
      '<div class="style-answer">' +
        escapeHtml(withoutS.answer || "（等待模型回答）") +
        "</div>"
    );
    html.push("</section>");
    html.push("</div></div>");
    mountEl.innerHTML = html.join("");
  }

  /**
   * @param {{enabled:boolean, drives_features?:boolean, scores?:Array, disclaimer?:string}} oceanReport
   * @param {HTMLElement} mountEl
   */
  function renderOceanPanel(oceanReport, mountEl) {
    if (!mountEl) return;
    var r = oceanReport || {};
    var html = [];
    html.push('<div class="ocean-panel">');
    html.push("<h2>OCEAN 参考视图（可选）</h2>");
    if (!r.enabled) {
      html.push(
        "<p>默认关闭。开启后仅用于理解与校准工作风格，<strong>不驱动任何功能</strong>。</p>"
      );
      html.push('<button type="button" data-ocean-action="enable">开启 OCEAN</button>');
    } else {
      html.push(
        "<p class=\"ocean-disclaimer\">" +
          escapeHtml(
            r.disclaimer ||
              "文本推断，不是心理测评。结果不直接驱动任何功能。"
          ) +
          "</p>"
      );
      if (r.drives_features) {
        html.push(
          '<p class="ocean-warn">错误：drives_features 应为 false</p>'
        );
      } else {
        html.push("<p>drives_features = false（运行时忽略这些分数）</p>");
      }
      html.push("<ul>");
      (r.scores || []).forEach(function (s) {
        html.push(
          "<li>" +
            escapeHtml(s.trait) +
            ": " +
            escapeHtml(String(s.value)) +
            " (" +
            escapeHtml(s.label || s.source || "") +
            ")</li>"
        );
      });
      html.push("</ul>");
      html.push('<button type="button" data-ocean-action="disable">关闭 OCEAN</button>');
    }
    html.push("</div>");
    mountEl.innerHTML = html.join("");
  }

  function buildPreviewPayload(question, stylePrompt) {
    return {
      question: question || "",
      with_style: {
        label: "使用风格",
        system_addon: stylePrompt || "",
      },
      without_style: {
        label: "不使用风格",
        system_addon: "",
      },
    };
  }

  var api = {
    renderStylePreview: renderStylePreview,
    renderOceanPanel: renderOceanPanel,
    buildPreviewPayload: buildPreviewPayload,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  global.LocalWikiStyle = api;
})(typeof window !== "undefined" ? window : globalThis);
