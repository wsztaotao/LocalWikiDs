# LocalWiki Agent 工作约定

## 全局闭环（强制）

所有新增、扩展、更新、修复必须遵循：

1. **Plan** → 提问澄清 → `docs/pdtd/<epic-id>/intent.md`（用户确认）
2. **Design** → `docs/pdtd/<epic-id>/spec.md`（用户确认）
3. **Build** → `docs/pdtd/<epic-id>/plan.md`（**用户确认后才能改代码**）
4. **Test/Deploy** → 可启动子 Agent 跑测；问题反馈用户  
   - **浏览器验证（全局强制）**：任一功能/补丁/修复实现后，必须打开浏览器对本机服务做点选验证，方可宣称可用或写 PASS；不得仅凭 pytest / API 冒烟。细则见 `.cursor/rules/localwiki-browser-verify.mdc`。

**四阶段均可回退**（用户显式指令）：归档为 `*.vN.md`，下游确认失效，已改码按 plan 回滚点处理。详见规则内「回退」节与各 epic 的 `ROLLBACK.md`。

细则见 Cursor 规则：`.cursor/rules/localwiki-pdtd-loop.mdc`。

## 任务队列

- 已完成的 v0.12 脚手架队列：`docs/cursor-tasks/queue.json`（46/46）
- **剩余工作队列**：`docs/cursor-tasks/remaining-queue.json`
- 编排说明：`docs/cursor-tasks/ORCHESTRATOR.md`

## 需求基线

- **实现范围（强制）**：`docs/pdtd/S0-personal-core/`（个人核心：Wiki + 记忆/经验/风格 + 薄 Agent + E1 进化）
- **能力目录（非强制 MVP）**：`doc/LocalWiki_v0.12.0_FullDesignAndImplementation.md`
- 测试诚实报告：`docs/TEST_REPORT.md`
