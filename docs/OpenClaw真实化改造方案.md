# OpenClaw 真实化改造方案

## 1. 文档目的

本文档用于回答一个具体问题：当前仓库已经接入真实 `OpenClaw gateway`，但为什么仍不能称为“真实 OpenClaw 驱动系统”，以及接下来应如何改造，才能在**不改 OpenClaw 内核**的前提下，把主链路收口为“真实 OpenClaw 规划 -> 统一网关执行”的正式方案。

本文不是泛化的待办清单，而是面向当前仓库的技术改造方案。文档目标有三项：

1. 给出“什么才算真实 OpenClaw”的可验证定义。
2. 识别当前实现中阻碍真实性的关键结构问题。
3. 输出一套按阶段推进、带验收条件的改造计划，作为后续代码实施的依据。

## 2. 结论先行

当前系统已经具备真实 `OpenClaw gateway` 接入能力，但还不是“真实 OpenClaw 驱动系统”。更准确的定义是：

**真实 OpenClaw 连接 + 演示化规划约束 + 真实网关执行**

这意味着系统前半段已经能通过 WebSocket 连接官方 `OpenClaw gateway`，并要求其输出结构化 JSON；但真正的调用计划仍然主要由场景 `fixture` 决定，系统在 OpenClaw 输出不符合预设时会自动修复或降级，因此 OpenClaw 还不是规划权威。

如果目标只是“演示稳定可跑”，当前方案是合理折中；如果目标是“严格满足最终需求文档，证明 OpenClaw 是核心智能体和结构化调用入口”，就必须完成真实化改造。

## 3. 什么才算“真实 OpenClaw”

在当前项目边界内，“真实 OpenClaw”不能简单理解成“仓库里跑着官方命令”或者“已经连上 WebSocket”。真正需要满足的是**规划权、协议权、执行权的职责分离**。

### 3.1 本项目里可接受的真实定义

由于当前可用接口是 `OpenClaw gateway` 的 WebSocket 聊天协议，而不是原生函数调用 API，因此本项目能达到的最高真实性应定义为：

`用户任务 -> OpenClaw 输出结构化计划 -> 计划被适配层校验 -> 网关执行 -> 防御与审计生效`

这里的“真实”指的是：

1. 结构化调用计划的**来源**必须是 OpenClaw，而不是场景预设步骤。
2. 网关只负责校验、阻断、验签、验权和执行，**不替 OpenClaw 改写计划**。
3. 场景 `fixture` 只能作为评测基准和实验标签，**不能再兼任执行脚本**。
4. 正式模式下不允许无痕 fallback，不允许在 OpenClaw 失败后悄悄切到本地 planner 继续冒充真实执行。

### 3.2 不属于本次目标的“更强真实性”

以下能力更强，但不应混淆为本阶段必须交付项：

1. 改造 `OpenClaw` 内核，让其原生支持工具调用。
2. 把网关注册为 OpenClaw 内部函数，而不是通过 JSON 输出做外部适配。
3. 让 OpenClaw 的设备配对身份直接等同于业务签名身份。

这些能力属于更深的产品级集成，不是当前课题边界内的必要条件。当前更现实也更可交付的目标，是把**规划权真正交还给 OpenClaw**。

## 4. 当前实现的真实性缺口

当前系统的缺口不是“没有代码”，而是几条关键链路仍然保持演示化实现。下面按影响程度排序。

### 4.1 OpenClaw 还不是规划权威

当前 `OpenClawFacade` 虽然会构造 prompt 并向真实 OpenClaw 发送消息，但 prompt 中的候选动作来自 `scenario.steps`，等于先把答案空间收窄成预设剧本，再让 OpenClaw 在剧本中回填 JSON。这会带来两个后果：

1. OpenClaw 没有真正的规划自由，只是在“被允许的答案集合”里选择。
2. 场景设计者而不是 OpenClaw 决定了系统最终会调用哪些动作。

更关键的是，如果 OpenClaw 返回的 `calls` 与 `scenario.steps` 不一致，系统会执行 `_repair_plan_against_scenario()`，把计划修回预设值。只要这条逻辑存在，OpenClaw 就不能被视为规划权威。

### 4.2 场景 fixture 同时承担“测试 oracle”和“执行脚本”

`data/fixtures/scenarios.json` 当前既定义实验场景、预期结果，也直接携带 `steps`。`DemoOrchestrator` 获取 `OpenClawPlan` 后，最终执行的就是这些结构化 `calls`。这使得场景文件同时承担了两种互相冲突的职责：

1. 作为实验评测标准，说明什么行为是正常的，什么行为应被拦截。
2. 作为系统执行来源，直接告诉系统下一步该做什么。

这会导致实验口径失真。系统看上去在“测试 OpenClaw 是否会做出危险规划”，实际上是在“执行我们已经写好的危险规划，再观察防御是否拦截”。

### 4.3 fallback 目前是“功能兜底”，不是“显式模式选择”

当前设计允许三种 fallback：

1. `use_real_openclaw = false` 时直接使用本地 planner。
2. OpenClaw 连接失败时降级到 `_degraded_plan()`。
3. OpenClaw 输出非结构化或不符合合同时，改用 `_contract_repaired_plan()`。

这在演示阶段是合理的，因为可以提高稳定性。但如果正式模式仍保留这种自动降级，外部观察者无法区分：

1. 本轮调用是否真由 OpenClaw 规划。
2. 本轮结果是否只是本地预设方案在继续执行。

因此 fallback 不能继续以默认行为存在，只能作为显式模式或调试模式存在。

### 4.4 状态修改仍然存在演示旁路

虽然 `/api/state/update` 已经要求传入签名后的 `CallAppRequest`，方向是对的，但 `/api/demo/state/request` 仍会在服务端直接构造并签名状态修改请求。这条路径在演示上很方便，但会削弱“所有受保护动作都统一纳入受控协议”的论证。

问题不在于这个接口存在，而在于它当前仍属于默认公开能力。真实化之后，它必须降格为：

1. 调试辅助接口。
2. 明确标记为 `demo/debug only`。
3. 不再作为正式链路的一部分。

### 4.5 测试口径仍围绕 fixture 执行，而非真实规划

当前测试大部分使用 `use_real_openclaw = false`，这说明测试重点放在网关、防御和状态保护，而不是 OpenClaw 规划层本身。这本身没错，但如果要宣称已经完成“真实 OpenClaw 化”，测试体系就必须新增以下能力：

1. 验证 OpenClaw 输出的 JSON 是否合法。
2. 验证 OpenClaw 输出的动作是否落在允许目录内。
3. 验证 OpenClaw 规划失败时系统是否按真实模式 fail-closed。
4. 验证实验统计是否基于 OpenClaw 的真实规划结果，而不是基于场景预设脚本。

### 4.6 当前参数供给方式仍然依赖“答案型提示”

当前 prompt 不只是把候选动作锁定了，还把 `resource_hints` 直接从 `scenario.steps` 派生出来，其中包含 `app / action / args`。这实际上把“本轮应该怎么做”的关键信息提前塞回了 planner 上下文。

这会产生一个隐藏问题：即使后续删除了 `candidate_actions`，如果仍然保留这种“从预设步骤反推资源提示”的方式，系统依然会在更隐蔽的层面把正确答案泄漏给 OpenClaw。这样做出来的 planner 看起来更自由，实质上还是在吃剧本。

真实化之后，planner 上下文里可以保留资源提示，但资源提示必须改成**非答案型资源清单**，例如：

1. 可读取的邮件 `message_id` 列表。
2. 可读取的相册 `asset_id` 列表。
3. 默认账户名、可查询城市名。
4. 当前受保护状态目标清单。

它不能再以“某个场景最终应调用什么动作、传什么参数”的形式出现。

### 4.7 规划层目前被运行模式污染

当前 `_build_prompt()` 会把 `mode` 直接写进 prompt。对于调试来说这很方便，但对于真实性和实验口径来说，这是一个结构性问题。

如果 OpenClaw 在规划时已经知道当前是 `off / guard_only / full` 哪种模式，它完全可能根据防御强弱改变自己的规划行为。这样一来，三模式对比实验就不再是“同一份攻击计划在不同防御层下的执行后果对比”，而会变成“模型在不同模式下生成了不同计划”的混合实验。

因此，真实化之后需要把“planner 输入”和“execution mode”分离：

1. 常规正式模式下，planner prompt 不应暴露防御模式。
2. 三模式消融应复放同一份真实计划，而不是让 planner 在三种模式下分别重算。
3. 如果后续要研究“攻击者感知防御后的自适应规划”，那应单独作为扩展实验，不应污染主实验口径。

## 5. 改造目标与非目标

本次改造建议按“主链路真实性”收口，而不是按“再接更多能力”扩散。

### 5.1 改造目标

本次改造的直接目标应固定为五条：

1. OpenClaw 成为结构化计划的唯一正式来源。
2. 场景 fixture 不再决定执行步骤，只负责定义场景输入和评测 oracle。
3. 网关执行完全以 OpenClaw 真实输出为依据，不再做合同修复式替换。
4. 受保护状态写入纳入同一套正式协议，演示旁路退出主链路。
5. 测试、实验、演示三套口径统一，能证明“本次执行是否真由 OpenClaw 规划”。

### 5.2 非目标

以下内容不应在本轮里与“真实 OpenClaw 化”绑死，否则工作量会失控：

1. 重写 `FISCO BCOS` 为完全链上信任根。
2. 新增更多虚拟 App。
3. 重做前端视觉设计。
4. 追求 OpenClaw 原生工具调用接口。

这些都重要，但它们不决定“OpenClaw 是否真实成为规划入口”。

## 6. 真实化改造的核心原则

为了保证改造完成后确实能称为“真实 OpenClaw”，整个方案必须遵守下面六条原则。

### 6.1 规划来源唯一原则

正式模式下，`OpenClawPlan.calls` 必须只来自真实 OpenClaw 输出的解析结果。系统可以校验它、拒绝它，但不能把它替换成 `scenario.steps` 或其他本地预设计划。

### 6.2 fixture 退居 oracle 原则

场景文件必须从“执行驱动器”变成“评测 oracle”。它可以定义：

1. 用户任务。
2. 场景背景。
3. 允许动作集合。
4. 禁止动作集合。
5. 预期实验结果。

但它不能再直接定义本轮必须执行的 `calls`。

### 6.3 正式模式 fail-closed 原则

真实模式下，OpenClaw 连接失败、输出非法 JSON、输出未知动作、输出参数不合法时，系统应明确返回规划失败，而不是自动切成本地 planner 继续执行。只有明确命名的 `demo_safe_mode` 或等价调试模式才允许 fallback。

### 6.3.1 规划输入不得携带答案原则

正式模式下，planner 上下文可以提供资源存在性、资源标识、系统能力边界和安全约束，但不得直接泄漏“推荐动作序列”或“标准答案参数”。否则即使系统不再显式依赖 `scenario.steps`，本质上仍然是剧本式驱动。

### 6.4 网关只做校验和执行原则

网关可以做：

1. 结构校验。
2. 参数 schema 校验。
3. guard 检测。
4. 链上验签验权。
5. 审计记录。

但它不能回头决定“其实本轮更应该调另一个动作”。一旦网关拥有计划替换权，OpenClaw 真实性就再次被破坏。

### 6.5 统一协议原则

普通 App 调用和状态修改调用都必须落到同一套 `call_app_api` 协议上。只要某类高风险动作还能通过单独捷径绕开，就不能说系统已经完成真实主链路闭环。

### 6.6 可观测性原则

系统必须能在返回结果和审计记录中清楚表明：

1. 本轮是否由真实 OpenClaw 规划。
2. 是否发生过 fallback。
3. 计划是否被拒绝。
4. 拒绝发生在规划层、guard 层、chain 层还是 state 层。

没有这类可观测性，后续实验统计会混淆“规划失败”和“执行被拦截”。

## 7. 推荐架构调整

### 7.1 场景模型调整

`ScenarioDefinition` 需要从“脚本模型”调整为“输入 + oracle 模型”。建议把当前 `steps` 拆分为两类信息：

1. `planning_constraints`
   用于告诉 OpenClaw 场景里有哪些资源、哪些标识符存在、哪些对象属于受保护状态，以及调用预算、资源边界等非答案型约束。
2. `evaluation_oracle`
   用于定义正常情况下允许出现的动作、禁止出现的动作、不同模式下预期最终状态。

这样做之后，场景文件仍然可以用于实验和论文，但不会再直接主导实际执行链路。

这里要特别避免一个误区：`planning_constraints` 不是新的 `steps`。它不应写成“你本场景应该先读邮件再转账”这种答案型提示，而应写成“当前存在 `mail-001`、`mail-attack-001` 这两封邮件，可查询城市为北京，可写受保护状态目标为 `prompt/shared.txt`”这种环境信息。

### 7.2 OpenClaw 适配层调整

`OpenClawFacade` 是本次改造的主战场。它需要从“带剧本的 JSON 生成器”改成“真实规划适配器”。建议拆成四个职责：

1. `build_planner_prompt()`
   从动作注册表动态生成工具目录和参数说明，不再从 `scenario.steps` 生成候选动作脚本。
2. `parse_planner_output()`
   只负责把 OpenClaw 输出解析成 `OpenClawPlan`。
3. `validate_planner_output()`
   校验动作存在性、参数结构、资源类型合法性、动作数量约束、受保护状态目标是否合法。
4. `planning_mode_policy()`
   统一管理正式模式、调试模式、fallback 模式。

当前 `_repair_plan_against_scenario()` 和 `_contract_repaired_plan()` 的职责都应退出正式模式，改成显式调试工具或诊断分支。

除此之外，还应补一层“输出规模与资源预算校验”。原因是当前 planner 输出是纯 JSON 文本，如果不限制它的步骤数量、参数体积和状态写入内容长度，OpenClaw 可能会生成形式合法但工程上不可控的计划。建议至少增加：

1. 最大步骤数限制。
2. 单步参数大小限制。
3. 状态写入内容大小限制。
4. 必填资源标识符存在性校验。

这不是在替 OpenClaw 决策，而是在保证 planner 输出能被系统稳定承接。

### 7.3 工具目录生成方式调整

动作目录不应再来自 `scenario.steps`，而应来自 `ACTION_SPECS`。因为只有 `ACTION_SPECS` 才是当前系统里真正统一的动作契约，里面已经包含：

1. `resource_type`
2. `app`
3. `action`
4. 参数 schema
5. 风险等级
6. 是否需要审批
7. 链失败时是否允许降级

真实化之后，OpenClaw prompt 应根据完整动作目录构造，并附带场景上下文限制，而不是直接告诉它“本场景候选动作只有这几个”。

不过这里也不能简单理解成“把所有动作目录无条件全量丢给模型就结束了”。如果缺少资源级提示，OpenClaw 可能知道有 `mail.read_message`，却不知道应读取哪个 `message_id`；知道有 `gallery.read_asset`，却不知道当前存在哪些 `asset_id`。因此真实化后的 planner 输入应同时包含两类信息：

1. 全局动作契约：来自 `ACTION_SPECS`。
2. 场景资源清单：来自非答案型的 `planning_constraints`。

只有这样，OpenClaw 才是在真实环境描述上做规划，而不是在空白中猜参数。

### 7.4 执行编排调整

`DemoOrchestrator` 需要从“执行 fixture step”改成“执行 OpenClaw plan，并用 fixture 做评估”。这意味着：

1. `_execute_plan()` 可以保留，执行对象仍是 `openclaw_plan.calls`。
2. `run_demo()` 中的场景 `fixture` 不再用于构造调用，而用于对实际 `OpenClawPlan` 做实验打分。
3. `mode_compare` 仍然可用，但应比较同一份真实计划在不同模式下的执行后果，而不是比较预设脚本在不同模式下的执行后果。

这一点非常关键。只有同一份真实计划在三种模式下复放，消融实验才有解释力。

这里还应新增一个边界：默认实验应使用**模式无关的 planner 输入**。也就是说，`mode_compare` 应先在一个统一 planner 上下文中拿到计划，再把同一计划分别送入 `off / guard_only / full`。只有这样，实验才真正对比的是防御层差异，而不是 planner 在不同模式下的输出差异。

### 7.5 API 与演示接口调整

`/api/demo/run` 需要补充更清晰的模式语义。建议至少区分三类：

1. `real_strict`
   真实 OpenClaw 模式，规划失败即失败。
2. `real_debug`
   真实 OpenClaw 模式，但允许保留原始回复、解析诊断和有限 fallback 信息，方便开发。
3. `demo_safe`
   允许 fallback 的演示保底模式。

当前的 `use_real_openclaw: bool` 已经不够表达这些差异。布尔开关无法区分“真实但严格”和“真实但可降级”。

### 7.6 状态修改入口调整

正式模式下，前端不应再默认走 `/api/demo/state/request` 来让服务端代签。更合理的处理是：

1. 保留该接口，但在 UI 上只作为调试工具显示。
2. 正式演示路径中，状态修改必须来自 OpenClaw 真实计划，或来自显式上传的已签名请求。
3. 审计视图里明确区分“planner generated request”和“manual debug request”。

这样可以保住演示便利性，同时不污染正式主链路。

这里还需要补一个现实问题：当前 `state.update_*` 的参数模型要求直接提交完整 `content`。对于真正由 OpenClaw 规划的路径，这个设计可能过重，因为它要求模型在一次 JSON 输出里同时完成“决定要改什么”和“生成完整替换文本”两件事。为了让真实化后的 planner 更稳定，建议把状态写入动作进一步拆成以下两种之一：

1. `patch` 型写入
   让 planner 生成受限补丁而不是完整文件内容。
2. `template_update` 型写入
   让 planner 只提交结构化变更意图，再由系统在受控模板上生成最终文本。

如果继续沿用“全量 `content` 替换”的 schema，也可以上线，但会显著提高 planner 输出失真的概率，尤其是在共享提示词和长期记忆较长时。

### 7.7 身份与签名边界需要显式化

真实化改造里还有一个容易混淆的点：OpenClaw 的 WebSocket 配对身份和网关调用签名身份不必是同一个实体。

在当前架构下，更合理的做法是：

1. WebSocket 配对身份只负责连接真实 OpenClaw。
2. 网关调用签名身份由 `planner_adapter` 或等价 DID 承担。
3. 审计中明确记录“谁负责规划”和“谁负责发起签名请求”。

这不削弱真实性。相反，它能让系统更清楚地表达：OpenClaw 是规划源，统一网关请求由受控适配层代表系统发起。若不把这层边界写清楚，后续很容易把“服务端代签”与“适配层代表签名”混为一谈。

## 8. 分阶段实施计划

### 阶段 1：去剧本化，但暂时不去保底

这一阶段的目标是先把“真实规划空间”打开，但不立即牺牲演示稳定性。

核心工作：

1. 调整 `ScenarioDefinition`，让 `steps` 退出执行角色，新增 oracle 字段。
2. 改写 `OpenClawFacade._build_prompt()`，从 `ACTION_SPECS` 生成完整动作目录。
3. 删除 prompt 中对 `candidate_actions` 的强绑定。
4. 保留 fallback，但在结果中明确标记本轮是否由 fallback 生成。
5. 新增审计字段，记录 `planning_source = openclaw | local_fallback | debug_manual`。
6. 把 `resource_hints` 从“预设步骤派生信息”改成“非答案型资源清单”。

阶段验收标准：

1. 正常场景不再依赖 `scenario.steps` 仍能得到可执行计划。
2. 实验与页面能展示本轮计划来源。
3. 场景文件不再直接决定执行动作。
4. planner 上下文中不再泄漏预设动作序列和预设参数。

### 阶段 2：去合同修复，建立正式模式

这一阶段的目标是让 OpenClaw 真正拥有计划决定权。

核心工作：

1. 删除 `_repair_plan_against_scenario()` 在正式模式下的调用。
2. 删除 `_contract_repaired_plan()` 在正式模式下的执行替换逻辑。
3. 解析失败、未知动作、非法参数时统一返回 `planning_failed`。
4. 引入新的 planner 运行模式枚举，替代 `use_real_openclaw` 布尔值。
5. 页面和审计日志中显示 `planning_failed_reason`。
6. 去掉 planner prompt 里的防御模式泄漏，改为模式无关输入。

阶段验收标准：

1. 正式模式下再也不会出现“OpenClaw 说 A，系统却执行 B”的情况。
2. OpenClaw 非法输出会被明确拒绝，而不是静默修复。
3. 一轮执行是否真实来自 OpenClaw，可以从响应和审计中直接看出。
4. 三模式对比默认复放同一份真实计划，而不是三次重新规划。

### 阶段 3：收口旁路，统一正式协议

这一阶段的目标是去掉剩余的演示捷径，让正式链路具备闭环性。

核心工作：

1. 降级 `/api/demo/state/request` 为调试接口。
2. 正式演示 UI 默认不展示服务端代签入口。
3. 为状态修改请求增加来源标签和正式路径校验。
4. 重写相关测试，使其不再默认依赖调试接口完成高风险状态写入。
5. 评估是否将 `state.update_*` 的参数从 `content` 全量替换调整为 `patch` 或模板化更新。

阶段验收标准：

1. 正式模式下所有高风险写入都通过统一 `CallAppRequest` 协议。
2. 前端演示不会在默认路径中绕过真实 planner 或统一协议。
3. 审计中可以区分正式请求和调试请求。
4. 状态写入动作在真实 planner 下具备可用的参数模型，不依赖超长自由文本替换。

### 阶段 4：重建测试与实验口径

这一阶段的目标是让“真实 OpenClaw 化”能够被证明，而不是只靠口头描述。

核心工作：

1. 新增 planner 层单元测试，覆盖合法 JSON、非法 JSON、未知动作、非法参数、空计划等情况。
2. 新增端到端测试，验证真实模式下不会发生计划替换。
3. 重写实验统计逻辑，让 `fixture` 只负责 oracle 打分。
4. 在实验导出里记录真实 OpenClaw 的原始回复、解析状态、计划来源和失败原因。
5. 增加针对“planner 输入不泄漏答案”的回归检查。

阶段验收标准：

1. 测试能证明 planner 真实性，而不仅是证明网关能执行。
2. 实验记录能回答“是 OpenClaw 规划错误，还是防御把它拦住了”。
3. 论文中的实验口径不再建立在预设脚本之上。
4. 测试能够发现 prompt 或资源提示再次演变回“隐性剧本”的回归。

## 9. 按文件拆解的改造清单

### 9.1 [app/core/openclaw.py](/home/chelizi/project/openq/app/core/openclaw.py)

这是主改造点，建议顺序如下：

1. 新增 planner 运行模式枚举，替代 `use_real_openclaw: bool`。
2. 把 `_build_prompt()` 改为基于 `ACTION_SPECS` 的完整动作目录描述。
3. 删除对 `scenario.steps` 的候选动作锁定。
4. 拆分解析、校验、运行策略三个阶段。
5. 让 `_local_plan()` 只在 `demo_safe` 或调试模式下可用。
6. 让 `_repair_plan_against_scenario()` 退出正式执行链路。
7. 增加更细的规划诊断字段，例如 `planning_source`、`planning_valid`、`planning_error`。
8. 移除或隔离 `mode` 对 planner prompt 的直接影响。
9. 把 `resource_hints` 重构为非答案型资源清单生成逻辑。

### 9.2 [app/schemas/models.py](/home/chelizi/project/openq/app/schemas/models.py)

当前 `OpenClawPlan` 结构不够表达真实化后的状态，建议补充：

1. `planning_source`
2. `planning_valid`
3. `planning_error`
4. `planner_mode`
5. `diagnostics`

同时建议把 `DemoRunRequest.use_real_openclaw` 替换为更明确的 planner 模式字段。

如果后续要支持更严格的真实性审计，建议再补充：

1. `plan_hash`
2. `planning_session_id`
3. `planning_input_profile`

这样后续可以明确复现“同一轮实验到底使用了哪份 planner 输入、哪份 planner 输出”。

### 9.3 [data/fixtures/scenarios.json](/home/chelizi/project/openq/data/fixtures/scenarios.json)

建议把当前 `steps` 重构为：

1. `oracle_allowed_actions`
2. `oracle_forbidden_actions`
3. `oracle_expected_risk`
4. `oracle_expected_by_mode`

如果需要保留旧 `steps` 兼容阶段过渡，也应明确标注为 `legacy_script_steps`，避免继续误用。

除此之外，建议新增一组**非答案型资源字段**，例如：

1. `available_message_ids`
2. `available_asset_ids`
3. `available_cities`
4. `protected_state_targets`

这些字段属于 planner 输入，不属于 oracle，也不应由执行脚本反推生成。

### 9.4 [app/core/experiments.py](/home/chelizi/project/openq/app/core/experiments.py)

这里需要完成“从执行剧本到评测真实计划”的角色切换：

1. `run_demo()` 只负责获取真实 `OpenClawPlan` 并执行。
2. `mode_compare` 复放同一份真实计划。
3. 实验导出新增 planner 真实性相关指标。
4. 场景 oracle 用于判断计划是否越权、是否偏离用户目标、是否命中危险动作。
5. planner 失败应作为独立实验结果类型，而不是混进执行拦截或系统错误。

### 9.5 [app/api/routes.py](/home/chelizi/project/openq/app/api/routes.py)

建议：

1. 替换 `use_real_openclaw` 的对外接口语义。
2. 补充 planner 模式到响应与审计。
3. 降级 `/api/demo/state/request` 的角色，限制其只在调试场景可见。
4. 为请求 trace 明确记录真实规划来源。
5. 在响应中区分 `planning_failed` 和 `execution_blocked`。

### 9.6 [tests/test_openq.py](/home/chelizi/project/openq/tests/test_openq.py)

建议按四层重写测试结构：

1. planner parsing tests
2. planner validation tests
3. gateway execution tests
4. end-to-end real planner tests
5. prompt leakage regression tests

当前大量 `use_real_openclaw = false` 的测试可以保留，但应明确归类为“网关/防御层测试”，不能再被视为“真实 OpenClaw 证明”。

## 10. 何时才能宣称“已经改成真实 OpenClaw”

只有当以下条件同时满足，才建议在文档、答辩或对外描述中宣称“系统已经改造成真实 OpenClaw 驱动”：

1. 正式模式下，`OpenClawPlan.calls` 只来自真实 OpenClaw 输出。
2. 系统不会在正式模式下把 OpenClaw 输出替换成 fixture 预设步骤。
3. 场景 fixture 已退居 oracle，不再决定执行动作。
4. OpenClaw 输出非法时系统会明确失败，而不是自动切本地 planner 冒充成功。
5. 受保护状态写入已统一纳入正式协议，默认演示路径不存在旁路。
6. 审计与返回结果能区分真实规划、fallback 规划和调试请求。
7. 测试与实验已经能直接验证上述行为。
8. planner 输入中不存在由预设脚本反推出来的答案型提示。
9. 三模式主实验默认基于同一份真实计划复放。

只要其中任何一条没有满足，系统最多只能说是“接入了真实 OpenClaw”，不能说“已经完成真实 OpenClaw 化”。

## 11. 风险与取舍

真实化改造最大的代价不是开发量，而是**稳定性与可重复性下降**。一旦规划权从 `fixture` 回到 OpenClaw，系统就会面临三个新的工程风险：

1. 模型输出漂移导致实验复现性下降。
2. 正式模式 fail-closed 后，演示失败概率上升。
3. 论文实验需要更细地解释“规划错误”和“执行拦截”的区别。

因此推荐的策略不是“一步切到最真”，而是：

1. 先完成结构真实化。
2. 再建立严格模式与演示模式并存。
3. 最后根据答辩场景选择默认入口。

也就是说，项目应该同时保留两种能力：

1. `real_strict`
   用于证明系统主链路真实性和论文论证。
2. `demo_safe`
   用于现场保底，避免因为模型临场输出失稳导致整套演示失败。

只要两种模式边界清晰，这不是自相矛盾，而是工程上合理的双轨设计。

## 12. 推荐执行顺序

综合当前仓库结构和改造风险，建议按下面顺序推进：

1. 先改 `schemas` 和 `fixtures`，把数据模型从“脚本”改成“oracle”。
2. 再改 `OpenClawFacade`，去掉候选动作锁定和合同修复。
3. 接着改 `experiments` 和 `routes`，让正式模式、调试模式、演示模式语义分离。
4. 最后重写测试和实验导出，补足真实性证明。

如果反过来先动前端或先补实验，会把很多旧口径继续固化，后面返工更大。

## 13. 逐步 TODO 清单

下面这份清单不再按阶段分组，而是按实际实施依赖顺序展开。执行时建议严格自上而下推进，避免先改后端行为、再回头重写数据模型，导致二次返工。

### 13.1 文档与口径先收口

1. 在实现前先统一团队口径：本项目中的“真实 OpenClaw”定义为“真实 OpenClaw 负责规划，适配层负责解析与校验，网关负责执行与防御”，而不是“OpenClaw 原生工具调用内核集成”。
2. 在后续实现过程中，任何新设计都按这个定义判断，不再引入“看起来更真、实际改变课题边界”的目标。
3. 明确保留两种运行目标：
   `real_strict` 用于真实性和论文论证；
   `demo_safe` 用于演示保底。
4. 在文档层先确定：三模式消融实验默认基于同一份 planner 输出复放，不允许把 planner 结果和防御模式耦合在一起。

### 13.2 先改数据模型，不先改业务逻辑

1. 修改 [app/schemas/models.py](/home/chelizi/project/openq/app/schemas/models.py) 中 `DemoRunRequest`，用明确的 planner 模式字段替换 `use_real_openclaw: bool`。
2. 修改 [app/schemas/models.py](/home/chelizi/project/openq/app/schemas/models.py) 中 `OpenClawPlan`，增加：
   `planning_source`
   `planning_valid`
   `planning_error`
   `planner_mode`
   `diagnostics`
3. 视需要继续补充：
   `plan_hash`
   `planning_session_id`
   `planning_input_profile`
4. 在响应模型里区分两类失败：
   `planning_failed`
   `execution_blocked`
5. 确保新增字段不会破坏现有序列化与前端读取逻辑，必要时先保留兼容字段。

### 13.3 重构场景 fixture，让它退出执行主链路

1. 修改 [data/fixtures/scenarios.json](/home/chelizi/project/openq/data/fixtures/scenarios.json)，不要再让 `steps` 作为正式执行来源。
2. 为每个场景新增 oracle 字段，例如：
   `oracle_allowed_actions`
   `oracle_forbidden_actions`
   `oracle_expected_by_mode`
   `oracle_expected_risk`
3. 为每个场景新增非答案型资源字段，例如：
   `available_message_ids`
   `available_asset_ids`
   `available_cities`
   `protected_state_targets`
4. 如果短期内需要兼容旧逻辑，把旧 `steps` 改名为 `legacy_script_steps`，明确标识为迁移期间字段。
5. 在代码里加入保护：任何正式模式都不允许再读取 `legacy_script_steps` 作为执行计划。
6. 增加一条检查规则：fixture 中禁止出现“答案型 planning_constraints”，例如直接写出本轮应执行的动作序列和参数。

### 13.4 重写 planner prompt 生成逻辑

1. 修改 [app/core/openclaw.py](/home/chelizi/project/openq/app/core/openclaw.py) 的 `_build_prompt()`。
2. 去掉基于 `scenario.steps` 生成的 `candidate_actions`。
3. 去掉基于 `scenario.steps` 反推的 `resource_hints`。
4. 改为从 [app/core/actions.py](/home/chelizi/project/openq/app/core/actions.py) 的 `ACTION_SPECS` 动态生成完整动作目录。
5. 为每个动作输出：
   `resource_type`
   `app`
   `action`
   参数要求
   风险说明
   受保护状态约束
6. 把场景输入改成“资源存在性提示”，例如某些 `message_id`、`asset_id`、`city` 当前可用，但不要提示该用哪一个。
7. 去掉 planner prompt 中对运行模式 `off / guard_only / full` 的直接暴露。
8. 在 prompt 中继续明确：
   只能输出 JSON
   只能输出 `call_app_api`
   不能访问工作区
   不能假设本地文件存在
9. 补充 planner 输出预算约束：
   最大步骤数
   单步参数大小
   状态写入内容大小
10. 确保 prompt 中不存在任何“从 oracle 逆推出标准答案”的内容。

### 13.5 拆分 OpenClawFacade 内部职责

1. 在 [app/core/openclaw.py](/home/chelizi/project/openq/app/core/openclaw.py) 中把当前大块逻辑拆成独立函数：
   `build_planner_prompt`
   `parse_planner_output`
   `validate_planner_output`
   `planning_mode_policy`
2. 保留现有 WebSocket 连接逻辑，不要在这一步重写 `OpenClawClient`。
3. 让 `parse_planner_output` 只负责从原始回复中提取 JSON。
4. 让 `validate_planner_output` 负责：
   动作是否存在
   `tool_name` 是否合法
   参数是否符合 schema
   状态目标是否匹配动作
   资源标识符是否在场景资源清单中
5. 验证失败时返回明确的 `planning_error`，不要隐式修复。
6. 在 `OpenClawPlan` 中记录 `raw_response`、`planning_error`、`planning_valid`。

### 13.6 删除正式模式下的伪真实性行为

1. 在 [app/core/openclaw.py](/home/chelizi/project/openq/app/core/openclaw.py) 中让 `_repair_plan_against_scenario()` 退出正式模式。
2. 让 `_contract_repaired_plan()` 退出正式模式。
3. 让 `_local_plan()` 只保留给 `demo_safe` 或显式调试模式使用。
4. 正式模式下出现以下情况时直接返回 `planning_failed`：
   连接失败
   返回空内容
   非 JSON
   未知动作
   非法参数
   超预算计划
5. 在审计里明确记录：
   `planning_source = openclaw | local_fallback | debug_manual`
6. 在响应里明确记录：
   本轮是否 fallback
   fallback 原因
   是否属于正式模式

### 13.7 重新定义 planner 模式

1. 在 [app/schemas/models.py](/home/chelizi/project/openq/app/schemas/models.py) 中新增 planner 模式枚举。
2. 推荐至少支持：
   `real_strict`
   `real_debug`
   `demo_safe`
3. 修改 [app/api/routes.py](/home/chelizi/project/openq/app/api/routes.py) 的 `/api/demo/run`，接收新模式字段。
4. 修改 [app/core/experiments.py](/home/chelizi/project/openq/app/core/experiments.py)，让实验运行显式指定 planner 模式。
5. 保证 `real_strict` 下没有自动 fallback。
6. 保证 `real_debug` 下可保留诊断信息，但不能伪装成真实成功执行。
7. 保证 `demo_safe` 下允许 fallback，但必须可观测。

### 13.8 收口编排逻辑，让 orchestration 真正执行 planner 结果

1. 修改 [app/core/experiments.py](/home/chelizi/project/openq/app/core/experiments.py) 中 `run_demo()`。
2. 确保 `_execute_plan()` 只执行 `openclaw_plan.calls`，不再从 fixture 取正式步骤。
3. 确保 `mode_compare` 复用同一份 `OpenClawPlan`。
4. 把场景 fixture 的职责改成“计划评估”和“结果判定”。
5. 如果 planner 失败，实验记录应进入独立状态，而不是冒充成系统执行错误。
6. 对每一轮实验记录：
   `planner_mode`
   `planning_source`
   `planning_valid`
   `planning_error`
   `plan_hash`
7. 保证审计链路能同时看到 planner 结果和最终执行结果。

### 13.9 处理状态写入动作的可用性问题

1. 评估 [app/core/actions.py](/home/chelizi/project/openq/app/core/actions.py) 中 `state.update_*` 是否继续使用“全量 `content` 替换”。
2. 如果继续全量替换，给 planner 增加更严格的长度和内容约束。
3. 如果改造，优先评估两种方案：
   `patch` 型写入
   `template_update` 型写入
4. 不管采用哪种方案，都要保证统一协议不变，仍然走 `call_app_api`。
5. 修改 [app/core/gateway.py](/home/chelizi/project/openq/app/core/gateway.py) 与 [app/core/actions.py](/home/chelizi/project/openq/app/core/actions.py) 的校验逻辑，承接新的状态写入参数模型。
6. 确保状态写入仍然受完整性检测、审批、回滚与审计保护。

### 13.10 收口状态修改演示旁路

1. 修改 [app/api/routes.py](/home/chelizi/project/openq/app/api/routes.py) 中 `/api/demo/state/request` 的定位。
2. 在接口语义和文档中明确：该接口仅供调试，不属于正式链路。
3. 前端默认界面不再展示服务端代签入口。
4. 审计中为这类请求打上 `debug_manual` 或等价标签。
5. 正式模式下的状态修改必须来自：
   真实 planner
   或外部显式已签名请求
6. 避免后续把调试接口误接回主页面默认路径。

### 13.11 明确签名身份边界

1. 审查 [app/core/request_builder.py](/home/chelizi/project/openq/app/core/request_builder.py) 中当前默认签名身份 `agent` 的语义。
2. 将其改成更清晰的系统身份，例如：
   `planner_adapter`
   `openclaw_planner`
3. 在文档与代码注释里明确：
   WebSocket 配对身份只负责连接 OpenClaw；
   业务签名 DID 代表受控适配层发起调用。
4. 在审计数据中同时记录：
   planner 来源
   请求签名 DID
5. 避免把“适配层代表系统签名”误判成“服务端偷偷代签”。

### 13.12 补强 API 返回与审计可观测性

1. 修改 [app/api/routes.py](/home/chelizi/project/openq/app/api/routes.py) 和相关 response model。
2. 在 `/api/demo/run` 返回中新增：
   `planning_source`
   `planning_valid`
   `planning_error`
   `planner_mode`
3. 在请求 trace 中记录 planner 相关字段。
4. 在审计里显式区分：
   `planning_failed`
   `execution_blocked`
   `execution_error`
   `executed`
5. 让页面可以直接展示本轮是否是真实 OpenClaw 规划、是否 fallback、为什么失败。

### 13.13 重写测试结构

1. 重构 [tests/test_openq.py](/home/chelizi/project/openq/tests/test_openq.py)，把现有测试按层归类。
2. 新增 planner parsing tests：
   JSON 提取
   fenced JSON
   无 JSON
   非法字段
3. 新增 planner validation tests：
   未知动作
   参数错误
   非法状态目标
   超预算计划
   资源标识不存在
4. 新增 strict mode tests：
   OpenClaw 失败时必须 `planning_failed`
   不允许自动 fallback
5. 新增 mode compare tests：
   同一份真实计划在三模式下复放
   不重复规划
6. 新增 prompt leakage regression tests：
   确保 planner 输入不再包含从 `legacy_script_steps` 派生出的答案型信息。
7. 保留现有网关/防御层测试，但明确它们不是“真实 OpenClaw 证明测试”。

### 13.14 重写实验统计口径

1. 修改 [app/core/experiments.py](/home/chelizi/project/openq/app/core/experiments.py) 中实验记录模型和导出逻辑。
2. 让 `fixture` 只负责 oracle 打分，不负责生成执行脚本。
3. 在实验结果中新增：
   `planning_source`
   `planning_valid`
   `planning_error`
   `plan_hash`
   `planner_mode`
4. 把“planner 失败”与“guard 拦截”区分开。
5. 把“chain 拦截”与“state 拦截”区分开。
6. 确保论文实验能回答：
   OpenClaw 本轮计划了什么；
   防御是否拦截；
   若失败，是 planner 失败还是执行侧拦截。

### 13.15 最后做真实性验收

1. 用 `real_strict` 跑一轮正常样本，确认没有读取 `legacy_script_steps`。
2. 用 `real_strict` 跑一轮恶意样本，确认 planner 结果真实进入网关。
3. 故意制造非法 planner 输出，确认系统返回 `planning_failed`，而不是修复后继续执行。
4. 跑一轮 `mode_compare`，确认三个模式复放的是同一份 `plan_hash`。
5. 检查审计记录，确认能区分：
   真实 planner
   fallback planner
   debug manual
6. 检查前端默认路径，确认没有服务端代签捷径。
7. 检查测试集，确认新增了 prompt leakage 回归测试。
8. 只有在上述检查全部通过后，才更新对外口径为“已完成真实 OpenClaw 化”。

## 14. 最终建议

从工程收益和课题完成度看，`OpenClaw` 真实化是值得做的，而且优先级高。它不是锦上添花，而是当前仓库从“高质量演示系统”迈向“符合最终需求的正式系统”的关键一步。

但这项改造不能理解成“把连接接得更深”，而应理解成：

**把规划权从场景脚本手里拿回来，真正交给 OpenClaw。**

只有围绕这个核心去改，最终结果才会是“真实 OpenClaw”；否则无论页面、链、审计做得多完整，主链路仍然只是演示化闭环。

## 依据来源

### 本地文档

1. `docs/最终版需求文档.md`
   用于确认最终需求中对 OpenClaw 的定位是“核心智能体”和“结构化调用入口”。
2. `docs/最终版实现文档.md`
   用于确认正式请求链路、统一协议要求和模块边界。
3. `docs/深度Review与最终收敛计划.md`
   用于对照当前仓库已识别出的真实性缺口，特别是 OpenClaw 规划权不真实的问题。

### 仓库代码与配置

1. [app/core/openclaw.py](/home/chelizi/project/openq/app/core/openclaw.py)
   支撑本文关于真实 gateway 接入、候选动作锁定、本地 planner fallback、合同修复等判断。
2. [app/core/experiments.py](/home/chelizi/project/openq/app/core/experiments.py)
   支撑本文关于 `OpenClawPlan` 如何进入执行与实验链路的分析。
3. [app/core/request_builder.py](/home/chelizi/project/openq/app/core/request_builder.py)
   支撑本文关于调用签名身份和请求构造职责的分析。
4. [app/core/gateway.py](/home/chelizi/project/openq/app/core/gateway.py)
   支撑本文关于网关已经具备执行、防御和审计能力，无需重写后半链路的判断。
5. [app/core/actions.py](/home/chelizi/project/openq/app/core/actions.py)
   支撑本文关于完整动作目录应来自统一动作注册表，而不是来自场景 `steps` 的建议。
6. [app/api/routes.py](/home/chelizi/project/openq/app/api/routes.py)
   支撑本文关于调试旁路、状态更新入口和服务端代签辅助接口的分析。
7. [app/schemas/models.py](/home/chelizi/project/openq/app/schemas/models.py)
   支撑本文关于 planner 模式表达能力不足、需要扩展 `OpenClawPlan` 结构的建议。
8. [data/fixtures/scenarios.json](/home/chelizi/project/openq/data/fixtures/scenarios.json)
   支撑本文关于 fixture 同时承担执行脚本与评测 oracle 两种职责的判断。
9. [tests/test_openq.py](/home/chelizi/project/openq/tests/test_openq.py)
   支撑本文关于当前测试重心仍主要放在网关/防御，而非真实规划验证的判断。

### 运行与部署材料

1. [README.md](/home/chelizi/project/openq/README.md)
   用于确认当前仓库对外描述为“OpenClaw + 统一网关 + 虚拟 App + 双层防御 + 演示端”的单一主工程。
2. [docs/部署与迁移说明.md](/home/chelizi/project/openq/docs/部署与迁移说明.md)
   用于确认 OpenClaw 当前作为外部官方成品接入，而不是内置到仓库中的实现。
3. [scripts/openq-up.sh](/home/chelizi/project/openq/scripts/openq-up.sh)
   用于确认项目当前通过 `openclaw gateway run` 拉起真实 gateway 进程。
