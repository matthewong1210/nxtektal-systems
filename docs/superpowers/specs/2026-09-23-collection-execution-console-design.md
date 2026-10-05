# 第三步：练习场收球执行界面 · 交互方案（v3，3B 实现基线）

日期：2026-09-24（v1 2026-09-23，v2 2026-09-24）。3B 共同基线
`064b90456557acf78b769dfb12f3bf2ccf3cdf17`（Codex 正式交付的 3A，
`codex/collection-execution-3a`）。实现分支 `feat/collection-execution-console-3b`。

状态：Claude 的界面方案；3B 前端实现按本文进行。本文不是合同，合同由
Codex 维护于 `simulation/docs/contracts/collection-execution-v1/` 与
`apps/site-agent-console/lib/collection-executions.ts`。3A 自述
"DESIGN ONLY — NOT IMPLEMENTED"：路由、设备与 V3 会话在该基线上仍未实现，
因此组件测试只能用冻结的 SIMULATION 示例经真实 parser 驱动，不是真实联调。

3C 联调增量（2026-09-24，基线合入 Codex `869bb6c`）：读取健康改为独立
15 s 墙钟过期（`EXECUTION_READ_EXPIRY_MS`），与请求错误、模拟暂停无关；
读取循环提升为 `useCollectionExecutions()`，`PilotOperations` 用
`simulationClockFor(view)` 把新鲜的模拟时钟传给 `DispatchView`，排程表单按
`collectionExecutionSimulationNow` 比较日期，读取过期时禁用排程，无 V3 路由时
沿用墙钟规则；`DispatchPanel` 文案改为按服务报告显示传输方式，不再由
in_memory/MQTT 推断设备种类；新增人工介入、安全拒绝、PAUSED/ENDED、过期恢复
与后端生成 witness 夹具的验收测试。示例测试与真实联调分开记录。

v3 相对 v2 的变化：组件文件名定稿为 `CollectionExecutionPanel`（含纯渲染
`CollectionExecutionView`）、`ExecutionSessionStrip`、`ExecutionRecordCard`
（含 `BindingOnlyCard`）、`shared`；面板内唯一按钮是"Retry execution read"；
`CourseOperationsPanel.tsx:100` 文案按 Q5 保持原文；样式类名以
`.exec-*` 为前缀；测试文件为 `tests/execution-fixtures.ts`、
`tests/execution-panel.test.tsx`、`tests/execution-interaction.test.tsx`。

标注约定：`【已核实】` 本轮直接读取源码、合同或示例；`【未核实】` 仅来自
二手总结或推断，实施前必须再读。

## 0 范围与前提

- 首个执行里程碑 `RAW_COLLECTED_TO_ROBOT`（球已从区域进入机器人）不是任务
  成功。合同【已核实】：`SUCCEEDED` 要求 raw 与 unload 两组数量均
  `COMPLETE`、相等且为正、卸到绑定站点、终止不晚于期限、守恒与载量校验通过、
  无冲突且无设备保护，唯一成功原因 `UNLOADED_ALL_COLLECTED_BALLS`，且
  `success_display_allowed === (state === "SUCCEEDED")`。洗球、补库与
  Planning 人工 outcome 独立，不从执行数量复制。
- 新接口只有读【已核实】：`GET /api/v1/collection-executions`（快照）与
  设计中的 `GET …/requests/{request_id}`（回执恢复）；该命名空间下所有写
  方法 405。浏览器客户端 `createCollectionExecutionsClient(fetch).read()`
  只暴露 `read`。界面不增加执行、重试或恢复写入按钮；Planning 的确认与
  UNKNOWN 恢复流程保持原有所有权（`lib/planning-state.ts`、`PlanningPanel`）。
- 合同交付后 Claude 只改 React 组件、样式、交互测试。类型/parser/客户端
  （`lib/collection-executions.ts`）、schema、示例、合同测试与
  `tests/boundaries.test.ts` 的白名单（已加入精确路径
  `/api/v1/collection-executions`，无前缀通配）归 Codex【已核实】。
- 浏览器不计算准入、不判定完成、不推算数量、不填补缺失时间；只显示 parser
  通过的字段，缺失显示"未知 / 尚无证据"。非法枚举由严格 parser 拒绝整份
  快照（`invalid_collection_executions`），界面不得把非法状态渲染成有效卡片。
- 本步不修改实现、不推送、不合并；不修改 3A 工作目录。

### 0.1 合同要点【已核实，来自 README / architecture / parser / 8 份示例】

- 快照 `nxt-collection-executions/v1`：`environment=SIMULATION`；会话字段
  `series_id`、`session_id`、`round_id`、`round_index`、`engine_digest`、
  `config_digest`、`session_epoch_utc`、`control_interval_s`、
  `session_end_sim_t_s`；`session_state ∈ {ACTIVE, PAUSED, ENDED}`；
  `now_sim_t_s`、`simulation_time_utc`（= epoch + now）、`server_time_utc`
  （墙钟读取时间）、`replay_digest`；数组 `bindings`、`requests`、
  `receipts`、`executions`（后三者数量必须相等，parser 校验交叉引用）。
- Binding：Planning `plan_id/plan_version/confirmation_id`、Edge
  `schedule_id/task_id/task_content_digest/incarnation/robot_id/zone_id`、
  运行时 `runtime_robot_id/runtime_zone_id/handoff_station_id`、
  `max_execution_s`（由 travel+collect+return+unload 分钟按控制间隔向上取整，
  示例 660 s）、`cycle_evidence`（含 `source_kind` MEASURED/MANUAL_ESTIMATE、
  `source_ref`、观测/有效期）、`bound_at_sim_t_s`/`bound_at_utc`、
  `task_created_at_utc`（准入下界）。示例映射 `picker-01/Z1 → R1/NEAR_LEFT → H1`
  是合成夹具，不是物理别名。
- Request：`kind=EXECUTE_BOUND_COLLECTION`、`request_id`、`execution_id`、
  `binding_id`、`due_at_utc/expires_at_utc` 与一次换算的
  `eligible_sim_t_s/latest_start_sim_t_s`。Receipt：`request_id`、
  `execution_id`、`attempt_id`、`sequence`、`durable: true`。
- Record：`state ∈ {PENDING, RUNNING, SUCCEEDED, PARTIAL, REJECTED, MISSED,
  FAILED, INCONCLUSIVE}`；`stage ∈ {WAITING_FOR_POLICY_SLOT,
  TRAVEL_TO_COLLECTION, COLLECTING, RAW_COLLECTED_TO_ROBOT, TRAVEL_TO_UNLOAD,
  UNLOADING, UNLOADED_TO_STATION, TERMINAL}`；`reason` 封闭枚举（成功原因、
  `POLICY_SLOT_MISSED`、`INSUFFICIENT_SESSION_HORIZON`、`SAFETY_REJECTED`、
  10 个收球退出原因、`NOT_STARTED_AFTER_RESTART`、
  `INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME`、`IDENTITY_CONFLICT`、
  `TERMINAL_CONFLICT`、`REPLAY_MISMATCH`、`EVIDENCE_INCOMPLETE`）；
  `started_sim_t_s`、`execution_deadline_sim_t_s`（= start + max）、
  `terminal_sim_t_s`；`actions[]`（原策略动作 / 选定动作 / 选择类别 /
  SafetyShield 结果）；`runtime_evidence`；`edge_evidence`
  （`accepted`、`effective_state`、`reason`、`terminal_states`、
  `result_verification ∈ {VERIFIED, UNVERIFIED, CONFLICT}`）；
  `raw_quantity`/`unload_quantity`（`status ∈ {NOT_REACHED, COMPLETE,
  INCOMPLETE}`，`balls` 仅 COMPLETE 时为整数，`source=RANGE_SIMULATION_BALL_LEDGER`，
  `destination_id` 为机器人 / 站点）；`device_protection`
  （`protected`、`reasons[]`、`authorization_blocked`）；`conflicts`；
  `reconciliation=NOT_PERFORMED`；`success_display_allowed`。
- 非终态 `PENDING` 必须 `started_sim_t_s=null`、`stage=WAITING_FOR_POLICY_SLOT`、
  Edge `ACCEPTED`；`RUNNING` 必须已开始且 Edge `RUNNING`；终态
  `stage=TERMINAL` 且 `reason` 非空。`started_sim_t_s` 是分派被准入的模拟
  时刻，开始后的阶段可为 `TRAVEL_TO_COLLECTION`，因此它不是"开始捡球"。
- 时钟：业务时刻为 `session_epoch_utc + sim_t_s`；`server_time_utc` 只是读取
  元数据；浏览器服务新鲜度仍用 15 s 墙钟；`PAUSED` 与服务新鲜度互相独立。
- 8 份示例：success（44/44，`WAIT_SLOT` 开始、原策略 handoff 收敛）、
  duplicate-request（同内容回执复用；`RUNNING_CONTINUATION`）、policy-missed
  （`MISSED/POLICY_SLOT_MISSED`，两组数量 `NOT_REACHED`）、partial-preempted
  （`PARTIAL/POLICY_PREEMPTED`，raw 12 COMPLETE，unload NOT_REACHED，Edge
  FAILED `unknown:partial_execution`）、safety-rejected（`REJECTED/SAFETY_REJECTED`，
  Edge accepted 后 FAILED `unknown:safety_rejected`）、restart-unknown
  （`INCONCLUSIVE/INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME`，两组 `INCOMPLETE`，
  保护 `RESTART_UNKNOWN`）、identity-conflict（快照仅含 binding，无
  request/receipt/record；错误体 `collection_execution_conflict`）、
  terminal-conflict（`INCONCLUSIVE/TERMINAL_CONFLICT`，Edge `CONFLICT`，
  两组数量 44 COMPLETE 但禁止显示成功）。
- 错误码：`collection_execution_invalid_request / not_found /
  request_not_found / conflict / unavailable / result_unknown`；parser 失败
  抛 `invalid_collection_executions`。

### 0.2 现状要点【已核实，v1 已列，此处只留与 v2 相关的】

- 页面顺序：`CourseOperationsPanel` → `PilotOperations`（`PlanningPanel`、
  `DispatchView`）→ 旧面板。`CourseOperationsPanel` 已有"失败保留上一份快照
  并标 Read stale、按 series:session:round 的 key 重置选择"的模式。
- `ConfirmationSection` 未显示 `task_created_at_utc`；`DispatchView` 的任务卡
  来自 MockRobotDevice 协议演练，文案 "Local protocol double"；
  `CourseOperationsPanel.tsx:100` 声明任务控件不调度 18 洞会话。
- Planning 未决写入（request_id、原始请求体、unknown/recovering）保存在
  `PlanningPanel` 内的 controller 状态；它与执行面板无共享状态。

## 1 页面结构与拟修改组件

### 1.1 放置位置（v2 修订）

在 `PilotOperations` 中新增独立只读面板 `CollectionExecutionPanel`，作为
自己的 `dispatch-shell` 挂在 `PlanningPanel` 之后、`DispatchView` 之前。
它有自己的串行读取循环（沿 `CourseOperationsPanel` 模式：5 s 轮询、
AbortController、失败保留上一份有效快照并标 stale），不复用 Planning 的
写控制器，也不消费 scheduler health 作为门槛（面板没有写操作）。
Planning 面板不变；执行面板只按服务返回的 `confirmation_id`/`task_id` 显示
关联标识，不做启发式匹配。

面板顶部为会话条 `ExecutionSessionStrip`；其下每个 `executions[]` 记录一张
`ExecutionRecordCard`；只有 binding 而无 request 时显示"已绑定，尚无持久化
请求"卡（identity-conflict 示例形态）。

### 1.2 组件清单（v2 修订）

| 路径（`apps/site-agent-console/`） | 类型 | 负责 | 改动 | 本步可动 |
|---|---|---|---|---|
| `components/execution/CollectionExecutionPanel.tsx` | 新增 | Claude | 读取循环（`createCollectionExecutionsClient(fetch).read(signal)`）、视图状态（loading / UNAVAILABLE(404) / OFFLINE / STALE 保留上一份 / 合同校验失败）、会话条与卡片列表、空态；无任何写入或按钮除"重试读取"。 | 否 |
| `components/execution/ExecutionSessionStrip.tsx` | 新增 | Claude | 会话行：SIMULATION、series/session/round/round_index、`session_state` 徽章（运行 / 已暂停 / 已结束）、`now_sim_t_s` 与 `simulation_time_utc`、`session_end_sim_t_s`、`control_interval_s`、engine/config 摘要（折叠）。连接行：`server_time_utc`、最后一次成功读取的墙钟时间与已过秒数、STALE/OFFLINE。两行分开。 | 否 |
| `components/execution/ExecutionRecordCard.tsx` | 新增 | Claude | 六级证据梯（§2.1）、状态与原因徽章、两组数量（§2.3）、期限（eligible / latest_start / deadline / terminal，模拟秒）、Edge 证据与校验、设备保护与授权阻断、冲突标志、`reconciliation: NOT_PERFORMED` 说明、折叠的 `actions[]` 仲裁轨迹、binding 身份（Planning / Edge / 运行时映射，标"合成夹具映射"）。 | 否 |
| `components/execution/shared.tsx` | 新增 | Claude | 以合同封闭枚举为键的 label/tone 表：state、stage、reason、quantity status、edge effective_state、protection reason、selection；不设字符串包含判断；模拟秒格式化 `模拟 t = N s`，可选折算 UTC 仅为 `session_epoch_utc + t` 并标"折算"。 | 否 |
| `components/PilotOperations.tsx` | 修改 | Claude | 在 `PlanningPanel` 与 `DispatchView` 之间挂载 `CollectionExecutionPanel`；不向 `PlanningPanel` 传任何会话 key，不改变其挂载身份。 | 否 |
| `components/planning/PlanSection.tsx`（ConfirmationSection） | 修改 | Claude | 新增 "Admitted (lower bound)" 行显示 `task_created_at_utc`，注明"不是开工时间；执行证据见下方执行面板"；"No task yet…" 区分未到期与待联结。 | 否 |
| `components/planning/OutcomeSection.tsx` | 修改 | Claude | 文案：执行面板的数量是 `RANGE_SIMULATION_BALL_LEDGER` 的 SIMULATION 证据，不是 MEASURED/MANUAL_ESTIMATE；人工 COLLECTED/UNLOADED 记录与之并列，互不覆盖、不预填。 | 否 |
| `components/DispatchPanel.tsx` | 修改 | Claude | 任务卡与传输行加来源标签"协议演练（MockRobotDevice），非 V3 执行证据"；不扩展 `statusTone`。 | 否 |
| `components/CourseOperationsPanel.tsx` | 修改 | Claude | 第 100 行文案：V2 只读快照与 V3 执行面板分别说明；接线前保持原文。 | 否 |
| `app/globals.css` | 修改 | Claude | `.exec-ladder`（六级，窄屏一列，长摘要 `overflow-wrap:anywhere; min-width:0`）、`.exec-quantity`、`.exec-protection`、`.session-strip`；复用 `dispatch-record`、`rec-head`、`kv-grid`、`load-warning`、`trace-details`【类名存在性已核实，样式值未核实】。 | 否 |
| `README.md`（console） | 修改 | Claude | 新段：面板位置、只读路由、浏览器不做的事、PAUSED 与 stale 的区别。 | 否 |
| `tests/execution-panel.test.tsx`、`tests/execution-interaction.test.tsx` | 新增 | Claude | 静态渲染 + happy-dom 挂载交互测试（§5），夹具直接读取 8 份合同示例的 `snapshot.body`。 | 否 |
| `lib/collection-executions.ts`、`tests/collection-executions-contract.test.ts`、`tests/boundaries.test.ts`、schema、examples、README、architecture | — | Codex | 已在 3A 分支交付（观察到 064b904）；Claude 不改。 | — |
| 本文 | 修改 | Claude | 方案 v2。 | 是 |

## 2 状态展示：从计划确认到执行结果

### 2.1 六级证据梯（v2 修订）

每张卡自上而下六级，每级只由各自字段填充；缺失即"尚无证据"，只有合同
明确证明未发生时才显示否定词。

| 级 | 含义 | 字段 | 有证据时 | 无证据时 |
|---|---|---|---|---|
| 1 任务准入 | Edge `TASK_CREATED` | `binding.task_created_at_utc`、`bound_at_sim_t_s` | "已准入（下界）· 不是开工" | binding 缺失则整卡不存在 |
| 2 请求持久化 | 执行请求已落盘 | `receipts[]`：`sequence`、`durable`、`attempt_id` | "执行请求已持久化 #seq" | 无 request/receipt："尚无持久化请求"（identity-conflict 形态） |
| 3 设备受理 | Edge 设备接受任务 | `edge_evidence.accepted`、`effective_state` | "设备已受理 · Edge ACCEPTED/RUNNING/…" | `accepted=false`："设备未受理"（合同字段为 false 即证明） |
| 4 模拟作业开始 | 分派被准入 | `started_sim_t_s`、`runtime_evidence.start_admitted`、`assignment_accepted`、`assignment_id` | "作业已开始 · 模拟 t=120 s · 可能仍在前往区域（stage）" | `PENDING`："等待策略槽，尚未开始"；终态且 start=null："未开始（合同）"；其他："尚无开始证据" |
| 5 收球 | 球进入机器人 | `stage`（COLLECTING / RAW_COLLECTED_TO_ROBOT）、`raw_quantity`、`collection_exit_reason` | "收进机器人 R1：44 球 · COMPLETE" + 退出原因 | `NOT_REACHED`："未发生（合同）"；`INCOMPLETE`："证据不完整 · 数量未知"；进行中："收球中，数量待终止后给出" |
| 6 卸球 | 球进入绑定站点 | `stage`（TRAVEL_TO_UNLOAD / UNLOADING / UNLOADED_TO_STATION）、`unload_quantity` | "卸到站点 H1：44 球 · COMPLETE" | 同上三分法 |

终止行：`state` 徽章 + `reason` + `terminal_sim_t_s`；成功徽章只在
`state === "SUCCEEDED" && success_display_allowed` 时出现（parser 已保证二者
等价，界面仍读取该标志，不自行判定）。

### 2.2 状态表（v2 修订，按合同枚举）

| # | 合同状态 / 条件 | 界面呈现 | 不得暗示 |
|---|---|---|---|
| 1 | 计划已确认（Planning） | 现有 CONFIRMED 卡不变；执行面板无对应记录时不生成卡 | 已受理、已开始 |
| 2 | binding 存在、无 request | "已绑定，尚无持久化请求"（muted） | 已受理 |
| 3 | `PENDING` / `WAITING_FOR_POLICY_SLOT` | 徽章 PENDING（warn）"等待原策略让出 Wait 槽"；显示 eligible / latest_start 与当前 `now_sim_t_s`；级 3 "设备已受理" | 已开始、失败 |
| 4 | `RUNNING` + `TRAVEL_TO_COLLECTION` | 徽章 RUNNING（info）"作业已开始 · 前往区域"；级 5/6 "进行中" | 正在捡球、完成 |
| 5 | `RUNNING` + `COLLECTING` / `RAW_COLLECTED_TO_ROBOT` | "收球中" / "球已进入机器人（里程碑）"；数量按 `raw_quantity` 显示 | 任务成功 |
| 6 | `RUNNING` + `TRAVEL_TO_UNLOAD` / `UNLOADING` | "返回 / 卸球中" | 卸球完成 |
| 7 | `SUCCEEDED` + `success_display_allowed` | 徽章 SUCCEEDED（ok）"已收 44 球并全部卸到 H1"；两组数量并列；洗球 / 补库 / 人工 outcome 行"独立 · 未知" | 洗球、补库、库存补充完成 |
| 8 | `PARTIAL` | 徽章 PARTIAL（warn）+ `reason`（如 POLICY_PREEMPTED）；raw 12 COMPLETE，unload 按其 status（NOT_REACHED → "未发生（合同）"） | 完整完成；余量 |
| 9 | `FAILED` | 徽章 FAILED（bad）+ reason；start=null 时"未开始（合同）"；已开始时 raw 为 `COMPLETE 0`（合同证明零）或 NOT_REACHED | 零回收未经证明 |
| 10 | `REJECTED`（如 SAFETY_REJECTED） | 徽章 REJECTED（bad）+ reason + 拒绝发生的 `actions[]` tick（SafetyShield REJECTED 与 `safety_reason`）；Edge 行说明"受理后拒绝映射为 Edge FAILED unknown:safety_rejected" | 可重试 |
| 11 | `MISSED`（POLICY_SLOT_MISSED / INSUFFICIENT_SESSION_HORIZON） | 徽章 MISSED（bad）"原策略未让出槽位，已过最迟开始 t=300 s" / "会话剩余时长不足"；两组数量"未发生（合同）" | 过期等于停工；已开始 |
| 12 | `INCONCLUSIVE` + INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME | 徽章 INCONCLUSIVE（warn）"重启后结果未知"；两组 INCOMPLETE → "数量未知"；保护 `RESTART_UNKNOWN`、授权阻断 | 成功、失败、数量 0 |
| 13 | `INCONCLUSIVE` + TERMINAL_CONFLICT / REPLAY_MISMATCH | 徽章 INCONCLUSIVE（warn）+ "终止证据冲突"；Edge 行 `CONFLICT`、`terminal_states` 全部列出；数量若 COMPLETE 照常显示但标"不构成成功" | 成功 |
| 14 | 收球退出原因 ROBOT_FAULT / ESTOP_LATCHED / HUMAN_ASSISTANCE_REQUIRED | 原因徽章（bad）+ 保护行 "设备受保护 · 授权阻断 · 原因 …"；无"恢复"或"解除"按钮 | 人工可在页面解除 |
| 15 | `device_protection.protected` 任意来源 | 保护行常显；`authorization_blocked` 时说明"新的授权被阻断" | — |
| 16 | `conflicts.*` 任一为真 | 冲突标志逐项列出 | — |
| 17 | `edge_evidence.result_verification` | VERIFIED（ok）/ UNVERIFIED（warn）/ CONFLICT（bad）与 `verified` 并列 | UNVERIFIED 等于失败 |

### 2.3 两组数量（v2 修订）

每张卡固定两行，来源统一标 `RANGE_SIMULATION_BALL_LEDGER · SIMULATION`：

- 收进机器人 `raw_quantity.destination_id`：`COMPLETE` → "N 球"（含
  `assignment_id`、事件范围、摘要在折叠详情）；`NOT_REACHED` → "未发生
  （合同证明）"；`INCOMPLETE` → "证据不完整 · 数量未知"。
- 卸到站点 `unload_quantity.destination_id`：同上。
- 不显示差额、余量、百分比；不与 Planning outcome 比较；洗球、补库、人工
  outcome 各自一行"独立 · 未知 / 见 Planning 结果记录"。

### 2.4 连接正常 vs 模拟暂停

- 连接（墙钟）：本面板自己的读取结果。成功 → 显示 `server_time_utc` 与最后
  成功读取的本机时间；失败 → STALE（保留上一份有效快照，显示已过秒数）或
  OFFLINE（无快照）；parser 拒绝 → 视为读取失败，文案"服务返回的数据未通过
  合同校验"，同样保留上一份有效快照。
- 模拟（模拟钟）：`session_state` 徽章 ACTIVE / PAUSED / ENDED；PAUSED 时
  `now_sim_t_s` 冻结并注明"模拟已暂停 · 读取于 <墙钟>"；记录状态不因暂停
  改变。ENDED 时非终态记录不应存在（parser 校验）。
- 组合：连接正常且 PAUSED → 正常渲染 + 暂停徽章；连接 STALE 且上一帧 ACTIVE
  → 上一帧标 stale，不得写"运行中"。

## 3 关键交互（v2 修订，只读）

1. 读取：挂载后立即读，5 s 轮询，单飞（pending 时不重叠），卸载中止；
   "重试读取"按钮只触发一次读，不受 scheduler health 影响。
2. 等待：`PENDING` 卡随轮询更新 `now_sim_t_s`，显示 eligible / latest_start；
   无催促或重发控件。
3. 拒绝 / 错过：终态卡；原因与 `actions[]` 中相关 tick 可展开；无重试按钮，
   提示"新建计划并确认（Planning 面板）"。
4. 部分完成：两组数量按各自 status 显示；Planning 面板的人工记录入口不变、
   不预填。
5. 结果未知：`INCONCLUSIVE` 只读展示 + 保护行；没有恢复按钮。回执恢复路由
   `GET …/requests/{id}` 由后端 / 脚本使用，浏览器客户端不暴露。Planning 自身
   写入的 UNKNOWN 恢复流程原样保留在 `PlanningPanel`。
6. 暂停 / 恢复：只改会话条徽章与时钟文案；无控件。
7. 重启后：页面从 API 重建；记录按服务投影显示（restart-unknown 形态）。
8. 换轮次：`CollectionExecutionPanel` 以 `series:session:round` 为 key 重置
   自己的展开 / 选择状态；不触碰 `PlanningPanel`（不给它传会话 key），因此
   Planning 未决请求的 ID、原始请求体与恢复状态不受影响。

## 4 剩余差异与待确认（v2）

### 4.1 v1 方案与合同的差异（已按合同改）

| v1 方案 | 合同 / 用户修订 | v2 处理 |
|---|---|---|
| 在 PlanningPanel 内作步骤 5 | 独立只读面板 | 移到 `PilotOperations`，独立读取循环 |
| 可能有显式执行请求表单 | 浏览器 GET-only | 删除表单、WriteState 复用与所有写入 |
| 写入未知 → "按原请求 ID 恢复"按钮 | 恢复路由不在浏览器客户端 | 删除；Planning 的恢复流程原样保留 |
| "完整完成 = 球已收进机器人" | SUCCEEDED 需等量卸到站点 + `success_display_allowed` | 成功仅按合同标志；里程碑单独显示 |
| 三阶段条（受理 / 开工 / 完成） | 六级：准入、持久化、设备受理、作业开始、收球、卸球 | 六级证据梯 |
| "真实开工 = 开始收球" | `started_sim_t_s` 后可能仍在前往区域 | 级 4 文案 + stage 区分 |
| 等待原因枚举 | 合同只有 WAITING_FOR_POLICY_SLOT；错过为 MISSED | 按合同 |
| 独立"人工介入"状态 | 为收球退出原因 + 设备保护原因 | 原因徽章 + 保护行 |
| 独立"过期"状态 | 最迟开始 → MISSED；EXECUTION_TIMEOUT 为退出原因 | 按合同 |
| 缺失显示"未知" | NOT_REACHED 是合同明确的"未发生" | 三分法：未发生（合同）/ 数量未知 / 尚无证据 |
| 未识别状态显示 info 徽章 | parser 拒绝整份快照 | 读取失败 / 保留上一份并标过期 |

### 4.2 待 Codex 确认

- Q1 每个事件只有模拟秒（`started_sim_t_s` 等），没有对应 UTC。界面是否
  允许按 `session_epoch_utc + t` 折算显示（标"折算"），还是只显示秒？
- Q2 `HUMAN_ASSISTANCE_REQUIRED` / 设备保护与 `/api/v0/task-ops` 通知收件箱
  之间没有 `notification_id` 关联字段；界面只显示原因，不做跳转，是否符合预期？
- Q3 快照是"过滤后的任务投影"，`eligible_pending` 可能引用不在
  `executions[]` 中的其他任务；界面对这些候选只显示 id 与时间，不展开。
- Q4 `DispatchView`（MockRobotDevice 演练）是否与执行面板同时保留；若保留，
  两处任务卡需并列来源标签（本文已按保留处理）。
- Q5 `CourseOperationsPanel.tsx:100` 文案在 3B 接线前是否保持原文（本文按
  保持处理）。
- Q6 Codex 计划文档位于 `docs/superpowers/plans/`，本方案位于
  `docs/superpowers/specs/`；是否需要互相引用或合并到 3B 计划。
- Q7 正式交接提交与分支名（当前仅观察到 064b904，未经 Codex 确认）。

## 5 交互验收场景（v2）

类型：static = `renderToStaticMarkup`；mounted = happy-dom 挂载 + 假计时器 +
脚本化 fetch；browser = 人工核对。夹具直接取 8 份示例的 `snapshot.body`
经 Codex parser 解析后传入组件。

| ID | 场景 | 给定 | 操作 | 预期（可观察） | 类型 |
|---|---|---|---|---|---|
| S01 | 会话条 | success 示例 | 渲染 | series/session/round、"模拟 t = 660 s"、`simulation_time_utc`、ACTIVE；连接行含 `server_time_utc` | static |
| S02 | 暂停 | success 示例改 `session_state=PAUSED` | 渲染 | 徽章"模拟已暂停"，时钟注明冻结；无控件变化 | static |
| S03 | 暂停 vs stale | 首帧 ACTIVE，第二次读取网络失败 | 推进一次轮询 | 连接行 STALE + 已过秒数，上一帧内容保留，无"运行中"字样 | mounted |
| S04 | 合同校验失败 | 第二次读取返回未知 `state` | 推进一次轮询 | 保留上一份有效快照并标 stale，文案含"未通过合同校验"；页面无该非法记录的卡片 | mounted |
| S05 | 首帧即非法 | 首次读取返回未知枚举 | 渲染 | OFFLINE 形态 + 校验失败文案；不渲染任何卡片 | mounted |
| S06 | 不可用 | 路由 404 | 渲染 | UNAVAILABLE 徽章与说明；Planning 与任务面板不受影响 | mounted |
| S07 | 仅绑定 | identity-conflict 示例 | 渲染 | "已绑定，尚无持久化请求"卡；无状态徽章 | static |
| S08 | 准入下界 | success 示例 | 渲染 | 级 1 显示 `task_created_at_utc` 与"不是开工"；ConfirmationSection 同步显示该行 | static |
| S09 | 请求持久化 | success 示例 | 渲染 | 级 2 "已持久化 #1"，attempt_id | static |
| S10 | 等待中 | success 示例改为 PENDING（start null、stage WAITING、Edge ACCEPTED、now<latest_start） | 渲染 | PENDING 徽章、"尚未开始"、eligible/latest_start；级 5/6 "尚无证据" | static |
| S11 | 开始≠捡球 | RUNNING + stage TRAVEL_TO_COLLECTION | 渲染 | 级 4 "作业已开始 · 前往区域"；级 5 "进行中"；无数量 | static |
| S12 | 收球中 | RUNNING + stage COLLECTING | 渲染 | "收球中，数量待终止后给出"；不显示 0 | static |
| S13 | 成功 | success 示例 | 渲染 | SUCCEEDED 徽章、"44 球 → R1"、"44 球 → H1"、reason 文案；洗球/补库行"独立 · 未知" | static |
| S14 | 成功标志缺失防线 | success 示例改 `success_display_allowed=false` | 渲染 | parser 拒绝 → 走 S04/S05 路径，绝不出现 SUCCEEDED 徽章 | mounted |
| S15 | 部分完成 | partial-preempted 示例 | 渲染 | PARTIAL + POLICY_PREEMPTED；raw "12 球"；unload "未发生（合同）"；Edge 行 FAILED unknown:partial_execution | static |
| S16 | 错过 | policy-missed 示例 | 渲染 | MISSED + "已过最迟开始 t=300 s"；级 4 "未开始（合同）"；两组"未发生（合同）" | static |
| S17 | 安全拒绝 | safety-rejected 示例 | 渲染 | REJECTED + SAFETY_REJECTED；展开 actions 可见 t=120 s SafetyShield REJECTED 与原因 | static |
| S18 | 重启未知 | restart-unknown 示例 | 渲染 | INCONCLUSIVE；两组"数量未知"；保护 RESTART_UNKNOWN、授权阻断；无恢复按钮 | static |
| S19 | 终止冲突 | terminal-conflict 示例 | 渲染 | INCONCLUSIVE + TERMINAL_CONFLICT；Edge CONFLICT 与两项 terminal_states；数量 44/44 显示但标"不构成成功"；无 SUCCEEDED 徽章 | static |
| S20 | 重复请求 | duplicate-request 示例 | 渲染 | 单张卡；`RUNNING_CONTINUATION` tick 可展开；无第二条记录 | static |
| S21 | 双时限 | success 示例 | 渲染 | latest_start（不是停工令）与 deadline（start+660）分行标注 | static |
| S22 | 设备保护 | restart-unknown / terminal-conflict | 渲染 | 保护行常显，含原因与"授权阻断"；无解除控件 | static |
| S23 | 换轮次 | 第二帧 round_id 变化 | 推进一次轮询 | 执行面板展开状态重置；PlanningPanel 中已置 UNKNOWN 的写入横幅、request_id 与"Recover by request ID"按钮仍在 | mounted |
| S24 | 无写入 | 面板挂载全过程 | 检查 fetch 记录 | 对 `/api/v1/collection-executions` 只有 GET；无 POST | mounted |
| S25 | 轮询单飞 | 读取挂起 | 推进两次轮询 | 只有一个在途请求；释放后恢复 | mounted |
| S26 | 卸载中止 | 挂载后卸载 | unmount | 在途请求被 abort；不再发布 | mounted |
| S27 | 边界测试 | 新组件源码 | 运行 boundaries 测试 | 无禁用 token；仅使用白名单精确路径 | static |
| S28 | 未识别枚举不渲染 | 任意示例注入非法 stage | 渲染 | 不出现"未识别状态"卡片；只出现校验失败文案 | mounted |
| S29 | 320px 布局 | success 示例 | 浏览器 320px | 六级梯折为一列，长摘要换行不溢出 | browser |
| S30 | 文案一致性 | 成功示例 + Planning 面板 | 渲染 | OutcomeSection 提示"执行面板数量为 SIMULATION 证据，不是 MEASURED/MANUAL_ESTIMATE" | static |

## 6 本轮验证与限制

基线（起点 `ffb0aa6`，与本轮工作分开）：`npm test` 20 文件 / 222 项通过、
typecheck 通过、lint 0 错误 / 2 个原有警告。

本轮新增：仅本文修订。验证为 `.github/scripts/verify_repository.py` 与
`git diff --cached --check`（结果见交接报告）。未运行 Codex 3A 分支的测试
（该目录不修改，其自述验证以 Codex 为准）；未做浏览器检查。

限制：合同以 Codex 正式交接为准；3A 自述 DESIGN ONLY，路由与 V3 会话未实现，
因此 §5 的 mounted 场景在 3B 之前只能用脚本化 fetch 与示例夹具验证。

## 7 依赖与下一步

依赖：Codex 确认 3A 交接提交（Q7）；Q1–Q6 答复；3B 后端提供实际路由时的
真实 HTTP 联调。

拿到确认后的实施顺序：`shared.tsx` 枚举表 → `ExecutionRecordCard` 静态测试
（S07–S22、S30）→ `ExecutionSessionStrip`（S01–S02）→
`CollectionExecutionPanel` 挂载测试（S03–S06、S23–S26、S28）→ 文案修改 →
样式（S29）→ README。全程不改 `lib/`、schema、示例、合同测试与白名单。
