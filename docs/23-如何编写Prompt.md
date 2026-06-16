# 如何编写 Prompt

本文档总结 Faber API 中 prompt 的编写规范、设计原则和可复用模式。它基于 `app/domain/services/prompts/` 下的实际实现（`system.py`、`planner.py`、`react.py`），供后续新增或调整 Agent、工具、流程时参考。

---

## 1. 为什么 Prompt 质量很重要

在 Faber 中，LLM 同时承担多个角色：

- **Planner**：把用户请求拆成可执行的计划。
- **ReAct Executor**：根据当前 step 选择工具、执行、返回结果。
- **Summarizer**：汇总所有结果，生成最终回复。
- **Reflector**：在工具循环中自检是否跑偏。

每个角色的输出都会被解析成结构化数据（JSON），并驱动后续流程。如果 prompt 写得模糊、约束不清，LLM 可能：

- 返回无法解析的 JSON；
- 偏离全局目标，陷入无关细节；
- 用错误的语言回复；
- 该调用工具时直接输出文本，或该输出结果时继续调用工具；
- 把待办清单当成最终结果交给用户。

因此，prompt 必须**结构清晰、约束明确、可验证、可复现**。

---

## 2. 项目中的 Prompt 组织方式

```
app/domain/services/prompts/
├── system.py          # 所有 Agent 共享的 SYSTEM_PROMPT
├── planner.py         # PlannerAgent 的 role prompt + create/update plan prompt
├── react.py           # ReActAgent 的 role prompt + execute/summarize/reflection prompt
└── en/                # 英文版本（与中文版本结构一致）
    ├── system.py
    ├── planner.py
    └── react.py
```

组合规则：

| Agent | 实际发送给 LLM 的 system + user 组合 |
|---|---|
| PlannerAgent.create_plan | `SYSTEM_PROMPT + PLANNER_SYSTEM_PROMPT + CREATE_PLAN_PROMPT` |
| PlannerAgent.update_plan | `SYSTEM_PROMPT + PLANNER_SYSTEM_PROMPT + UPDATE_PLAN_PROMPT` |
| ReActAgent.execute_step | `SYSTEM_PROMPT + REACT_SYSTEM_PROMPT + EXECUTION_PROMPT` |
| ReActAgent.summarize | `SYSTEM_PROMPT + REACT_SYSTEM_PROMPT + SUMMARIZE_PROMPT` |
| BaseAgent._build_reflection_message | `SYSTEM_PROMPT + REACT_SYSTEM_PROMPT + REFLECTION_PROMPT`（作为 user message 插入） |

**设计要点：**

1. `SYSTEM_PROMPT` 放**通用能力、全局规则、沙箱环境、输出风格**。
2. `*_SYSTEM_PROMPT` 放**角色定义和核心工作流**。
3. 具体任务 prompt（`CREATE_PLAN_PROMPT`、`EXECUTION_PROMPT` 等）放**输入变量、格式要求、当前上下文**。

---

## 3. 通用编写原则

### 3.1 一条 prompt 只解决一个问题

不要把「规划 + 执行 + 汇总」塞到同一个 prompt 里。Planner 只负责产出计划，ReAct 只负责执行当前 step，Summarizer 只负责汇总。职责单一，LLM 才不容易混淆输出格式。

### 3.2 用结构化标签划分语义块

在 `SYSTEM_PROMPT` 中，我们用 XML 风格的标签把不同规则分组：

```text
<intro>...</intro>
<language_settings>...</language_settings>
<system_capability>...</system_capability>
<file_rules>...</file_rules>
<search_rules>...</search_rules>
<browser_rules>...</browser_rules>
<shell_rules>...</shell_rules>
<coding_rules>...</coding_rules>
<writing_rules>...</writing_rules>
<sandbox_environment>...</sandbox_environment>
<important_notes>...</important_notes>
```

**好处：**

- LLM 更容易识别当前规则属于哪个领域；
- 后续增删规则时不会破坏其他块；
- 便于通过单元测试或正则检查是否漏掉某个块。

### 3.3 明确输出格式，并给出示例

不要只说「返回 JSON」。要给出：

1. TypeScript / JSON Schema 接口定义；
2. 一个完整可运行的 JSON 示例；
3. 每个字段的必填/可选、类型、含义；
4. 异常情况的返回值（例如步骤不可行时 steps 为空数组）。

**示例（CREATE_PLAN_PROMPT）：**

```text
返回格式要求：
- 必须返回符合以下 TypeScript 接口定义的 JSON 格式
- 必须包含指定的所有必填字段
- 如果判定任务不可行, 则"steps"返回空数组，"goal"返回空字符串

TypeScript 接口定义：
```typescript
interface CreatePlanResponse {
  message: string;
  language: string;
  steps: Array<{
    id: string;
    description: string;
    success_criteria?: string;
  }>;
  goal: string;
  title: string;
}
```

JSON 输出示例:
{
  "message": "用户回复消息",
  "goal": "目标描述",
  "title": "任务标题",
  "language": "zh",
  "steps": [
    { "id": "1", "description": "步骤1描述", "success_criteria": "..." }
  ]
}
```

### 3.4 用变量占位符保持 prompt 可复用

所有动态内容都用 `{variable}` 占位，由代码在运行时填充。常见变量：

| 变量 | 出现位置 | 含义 |
|---|---|---|
| `{message}` | 所有任务 prompt | 用户原始消息 |
| `{attachments}` | 所有任务 prompt | 用户附件 |
| `{language}` | EXECUTION_PROMPT | 工作语言 |
| `{step}` / `{success_criteria}` | EXECUTION_PROMPT | 当前 step 描述与验收标准 |
| `{title}` / `{goal}` | plan context | 任务标题与目标 |
| `{current_step_index}` / `{total_steps}` | plan context | 当前进度 |
| `{completed_steps}` / `{remaining_steps}` | plan context | 已完成/待执行步骤列表 |
| `{plan}` | UPDATE_PLAN_PROMPT | 完整计划 |

**注意：** 在 Python 三引号字符串中使用 `{` 时，如果要保留字面量（例如 JSON 示例中的 `{{`），需要对每个大括号再转义一次，写成 `{{` 和 `}}`。

### 3.5 反复强调关键约束

对于容易出错的点，不要只讲一次，要在多个位置重复：

- `SYSTEM_PROMPT.<language_settings>` 强调工作语言；
- `EXECUTION_PROMPT` 再次强调「必须使用用户消息中使用的语言」；
- `EXECUTION_PROMPT` 反复强调「是你来执行，不是用户」「直接交付最终结果」。

### 3.6 给出可验证的验收标准

每个 step 都要带 `success_criteria`，让 LLM 在执行时知道「怎样算完成」。

**差的写法：**

```text
步骤：搜索 LangGraph 示例项目
```

**好的写法：**

```text
步骤：搜索适合初学者的 LangGraph 官方示例项目
success_criteria: 获得至少一个官方示例项目的名称和 GitHub 地址
```

---

## 4. 不同类型 Prompt 的编写方法

### 4.1 System Prompt：定义 Agent 的身份与边界

System Prompt 是「长期记忆」，负责告诉 LLM：

- 你是谁（Faber，一个 AI 智能体）；
- 你能做什么（访问沙箱、使用工具、浏览网页、写代码）；
- 你不能做什么（不读取非文本文件、不用列表格式写作等）；
- 默认工作语言；
- 沙箱环境信息。

**编写 checklist：**

- [ ] 明确 Agent 名称和定位；
- [ ] 列出核心能力（搜索、浏览器、Shell、文件、MCP、A2A）；
- [ ] 列出硬约束（文件规则、搜索规则、浏览器规则、Shell 规则、编码规则、写作规则）；
- [ ] 说明沙箱环境（OS、用户、Python/Node 版本）；
- [ ] 用 `<important_notes>` 强调最高优先级指令（亲自执行、交付结果而非计划）。

### 4.2 Role Prompt：定义单次调用的工作流程

Role Prompt 比 System Prompt 更具体，告诉 Agent 本次调用的核心任务。例如 `PLANNER_SYSTEM_PROMPT`：

```text
你是一个任务规划智能体 (Task Planner Agent), 你需要为任务创建或更新计划:
1. 分析用户的消息并理解用户的需求;
2. 确定完成任务需要使用哪些工具;
3. 根据用户的消息确定工作语言;
4. 生成计划的目标和步骤;
```

**编写要点：**

- 用 3-5 条清晰步骤描述核心工作流；
- 不要重复 System Prompt 已经讲过的规则；
- 突出本角色与其他角色的区别。

### 4.3 Plan Prompt：把用户需求拆成可执行步骤

`CREATE_PLAN_PROMPT` 和 `UPDATE_PLAN_PROMPT` 负责产出 `Plan` 对象。

**CREATE_PLAN_PROMPT 设计要点：**

1. **输入**：用户消息 + 附件；
2. **输出**：包含 `message`、`language`、`steps`、`goal`、`title` 的 JSON；
3. **约束**：
   - 使用用户消息的语言；
   - 步骤原子、独立；
   - 每个步骤带 `success_criteria`；
   - 不可行时返回空 steps。

**UPDATE_PLAN_PROMPT 设计要点：**

1. **输入**：当前 step 执行结果 + 完整计划；
2. **输出**：更新后的未完成 steps；
3. **约束**：
   - 不能改 goal；
   - 不改已完成步骤；
   - 根据执行结果增删改后续步骤；
   - 保留或补充 `success_criteria`。

### 4.4 Execution Prompt：让 LLM 选择工具并执行

`EXECUTION_PROMPT` 是 ReAct 循环的核心。它必须让 LLM 同时做到：

- 看到全局目标，不跑偏；
- 聚焦当前 step；
- 知道何时调用工具、何时输出结果；
- 知道何时问用户；
- 按固定 JSON 格式返回。

**推荐结构：**

```text
1. 全局上下文（标题、目标、进度、已完成/待执行步骤）
2. 当前任务与验收标准
3. 行为约束（亲自执行、使用工作语言、通报进度、询问用户等）
4. 返回格式（TypeScript 接口 + JSON 示例）
5. 输入信息（message、attachments、language、task）
```

**关键技巧：**

- 用 `=== 任务全局上下文 ===` 这样的视觉分隔线，让 LLM 明确区分全局与局部；
- 在「注意事项」里把最高频的 bad case 列出来（例如不要交付待办清单）；
- 要求 `message_notify_user` 工具汇报进度，这样前端能实时看到 Agent 在做什么。

### 4.5 Reflection Prompt：防止工具循环跑偏

`REFLECTION_PROMPT` 每隔 `reflection_interval` 轮工具调用被插入一次，强制 LLM 自检。

**设计要点：**

1. 让 LLM 暂停当前操作；
2. 回顾任务标题、目标、当前进度；
3. 回答 4 个反思问题；
4. 支持提前完成标记（如 `[EARLY_COMPLETE]`）。

**为什么有效：**

- 长工具循环容易让 LLM 陷入局部优化（例如在浏览器里不断滚动）；
- 定期提醒全局目标，相当于给 LLM 一个「抬头看路」的机制；
- 提前完成标记让系统有机会跳过剩余步骤，减少无效调用。

### 4.6 Summarize Prompt：生成最终交付物

`SUMMARIZE_PROMPT` 在所有 step 完成后调用，负责生成最终回复。

**设计要点：**

1. 强调「最终结果」而非中间过程；
2. 要求详细解释；
3. 要求通过 `attachments` 交付生成的文件；
4. 给出 JSON 格式（`message` + `attachments`）。

---

## 5. JSON 格式约束设计

### 5.1 为什么要强制 JSON

Faber 的后续流程依赖结构化数据：

- Plan 需要解析成 `Plan` 模型；
- Step 结果需要解析成 `StepResult`；
- Summarize 结果需要解析成 `Message`。

如果 LLM 输出自由文本，解析会失败，导致 ErrorEvent。

### 5.2 如何写 JSON 约束

**推荐写法：**

```text
返回格式要求：
- 必须返回符合以下 TypeScript 接口定义的 JSON 格式
- 必须包含所有指定的必填字段
- 不要添加任何解释性文字，只返回 JSON

TypeScript 接口定义：
```typescript
interface Response {
  success: boolean;
  result: string;
  attachments: string[];
}
```

JSON 输出示例：
{
  "success": true,
  "result": "已完成...",
  "attachments": ["/tmp/report.md"]
}
```

**注意事项：**

- 用 TypeScript 接口而不是 JSON Schema，LLM 对 TypeScript 注释的理解更好；
- 明确标注必填/可选字段；
- 给出边界值示例（空数组、空字符串）。

### 5.3 解析失败的兜底

即使 prompt 写得再好，LLM 偶尔也会输出非 JSON。代码层需要：

- 使用 `JSONParser` 尝试修复和解析；
- 失败时捕获异常，生成 `ErrorEvent`；
- 记录原始输出，便于后续优化 prompt。

---

## 6. 变量插值与模板引擎

### 6.1 使用 Python f-string 风格

项目中直接用 Python 三引号字符串 + `.format()` 或 f-string 插值：

```python
CREATE_PLAN_PROMPT = """
你现在正在根据用户的消息创建一个计划:
{message}
...
用户消息:
{message}

附件:
{attachments}
"""

prompt = CREATE_PLAN_PROMPT.format(
    message=message,
    attachments=attachments,
)
```

### 6.2 转义大括号

JSON 示例中的 `{` 和 `}` 会被 format 当成占位符。要保留字面量，需要双写：

```python
JSON_EXAMPLE = """
{{
  "message": "...",
  "steps": [{{ "id": "1" }}]
}}
"""
```

实际渲染后变成：

```json
{
  "message": "...",
  "steps": [{ "id": "1" }]
}
```

### 6.3 复杂对象的序列化

对于 `plan`、`step`、`completed_steps` 等复杂对象，先在代码里序列化成字符串再插入：

```python
plan_context = {
    "completed_steps": "\n".join([f"- [完成] {s.description}" for s in completed]),
    "remaining_steps": "\n".join([f"- [待执行] {s.description}" for s in remaining]),
}
```

---

## 7. 语言一致性

### 7.1 三层语言控制

1. **System Prompt** 声明默认工作语言；
2. **Role/任务 Prompt** 要求使用用户消息中的语言；
3. **代码** 把检测到的 `language` 注入 prompt。

### 7.2 常见错误

- 只在 System Prompt 里写「使用中文」，但用户用英文提问时，LLM 仍用中文回复；
- 工具调用的自然语言参数用了错误语言（例如英文查询中文问题）；
- 最终回复语言与用户需求不一致。

### 7.3 推荐做法

```text
- **必须使用用户消息中使用的语言（Working Language）来执行任务和回复。**
- 工具调用（Tool calls）中的自然语言参数必须使用工作语言。
```

并在每次任务 prompt 中重复注入 `{language}` 变量。

---

## 8. 防跑偏设计

### 8.1 全局目标上下文

每个 `EXECUTION_PROMPT` 都包含：

```text
=== 任务全局上下文 ===
任务标题: {title}
任务目标: {goal}
总进度: {current_step_index} / {total_steps}
已完成步骤:
{completed_steps}
待执行步骤:
{remaining_steps}
====================
```

这相当于给 LLM 一个「导航仪」，让它知道自己在整个任务中的位置。

### 8.2 验收标准

每个 step 的 `success_criteria` 让 LLM 知道「这一步做到什么程度可以停」。

### 8.3 反思检查点

`REFLECTION_PROMPT` 定期插入，让 LLM 自问：

1. 是否对齐原始目标？
2. 是否陷入不必要的细节？
3. 下一步最高效的动作是什么？
4. 是否已提前达成目标？

### 8.4 单步迭代预算

代码层通过 `max_iterations_per_step` 限制单个 step 的工具调用次数，避免无限循环。prompt 层则要明确告诉 LLM「每次迭代原则上只选择一个工具调用」。

### 8.5 用户确认

对于关键决策或不明确信息，prompt 要求 LLM 调用 `message_ask_user`，而不是擅自决定。

---

## 9. 测试与迭代

### 9.1 Prompt 版本管理

- 所有 prompt 集中在 `app/domain/services/prompts/`；
- 提供中英文版本；
- 修改前建议复制一份备份或加注释说明变更原因。

### 9.2 测试方法

1. **单元测试**：用固定输入验证 LLM 输出能否被 `JSONParser` 正确解析；
2. **回归测试**：收集历史 bad case，修改 prompt 后重新跑一遍；
3. **A/B 测试**：对同一批任务分别用旧 prompt 和新 prompt，对比成功率和输出质量；
4. **日志分析**：查看解析失败、ErrorEvent、WaitEvent 的频率，定位 prompt 问题。

### 9.3 常见优化方向

| 问题 | 优化方向 |
|---|---|
| LLM 不调用工具，直接输出文本 | 在 prompt 中强化「直接通过工具去做」 |
| 输出 JSON 缺字段 | 在接口定义里加粗必填字段，示例中补全 |
| 语言不一致 | 在多处重复语言要求，并注入 `{language}` |
| 步骤描述太粗 | 加 `success_criteria`，明确验收标准 |
| 浏览器/Shell 陷入循环 | 降低 `reflection_interval`，增强反思 prompt |
| 汇总太简略 | 在 summarize prompt 中要求「越详细越好」 |

---

## 10. 完整示例：新增一个「数据分析」Agent 的 Prompt

假设要新增一个专门做数据分析的 Agent，可以按下面结构写 prompt。

### 10.1 System Prompt 片段

```text
<data_analysis_rules>
- 数据清洗前必须先查看原始数据的前 10 行和列类型
- 所有统计计算必须编写 Python 代码执行，禁止心算
- 可视化图表必须保存为 PNG 或 SVG 文件
- 分析结论必须标注数据来源和计算方法
</data_analysis_rules>
```

### 10.2 Role Prompt

```text
你是一个数据分析智能体（Data Analysis Agent），你需要：
1. 理解用户的数据分析目标和数据位置；
2. 使用 Python/Pandas 加载、清洗、分析数据；
3. 生成可视化图表并保存到沙箱；
4. 撰写包含方法、结果、结论的数据分析报告。
```

### 10.3 任务 Prompt

```text
请根据以下需求完成数据分析任务：
{task}

数据源：
{data_source}

要求：
- 输出必须包含数据概览、清洗步骤、分析方法、可视化图表路径、结论
- 所有中间结果保存到 /tmp/analysis/ 目录
- 使用中文回复

返回格式：
```typescript
interface AnalysisResult {
  summary: string;          // 分析摘要
  report_path: string;      // 报告文件路径
  charts: string[];         // 图表路径数组
}
```

JSON 输出示例：
{
  "summary": "...",
  "report_path": "/tmp/analysis/report.md",
  "charts": ["/tmp/analysis/chart1.png"]
}
```

---

## 11. 常见反模式

### 11.1 把规则和任务混在一起

**反模式：**

```text
你是一个助手，请帮我完成下面的任务，记得用中文，返回 JSON，步骤要原子化...
{task}
```

**改进：**

把规则拆到 System Prompt，任务 prompt 只放输入和输出格式。

### 11.2 只给接口定义，不给示例

**反模式：**

```text
返回 JSON，包含 message 和 attachments。
```

**改进：**

```text
TypeScript 接口：
interface Response { message: string; attachments: string[]; }

示例：
{ "message": "...", "attachments": ["/tmp/a.md"] }
```

### 11.3 约束只写一次

**反模式：**

在 System Prompt 里写了一句「使用中文」，后续不再重复。

**改进：**

在 System Prompt、Role Prompt、任务 prompt 中各重复一次语言要求。

### 11.4 不定义异常情况

**反模式：**

```text
请返回计划。
```

**改进：**

```text
如果任务不可行，steps 返回空数组，goal 返回空字符串。
```

---

## 12. 检查清单

在提交新的 prompt 前，逐项检查：

- [ ] 是否明确了 Agent 角色和职责边界？
- [ ] 是否用结构化标签分组了不同规则？
- [ ] 是否给出了完整的 TypeScript/JSON Schema 接口定义？
- [ ] 是否给出了可运行的 JSON 示例？
- [ ] 是否标明了必填和可选字段？
- [ ] 是否处理了异常情况（不可行、失败、空结果）？
- [ ] 是否重复强调了语言要求？
- [ ] 是否强调了「亲自执行」而非「告诉用户怎么做」？
- [ ] 是否包含防跑偏机制（全局上下文、反思、验收标准）？
- [ ] 变量占位符是否都正确转义了 `{}`？
- [ ] 是否已有对应的中文/英文版本？
- [ ] 是否通过了解析测试和至少一个实际任务测试？

---

## 13. 参考文件

| 文件 | 说明 |
|---|---|
| `app/domain/services/prompts/system.py` | 全局 System Prompt |
| `app/domain/services/prompts/planner.py` | Planner 角色 + create/update plan prompt |
| `app/domain/services/prompts/react.py` | ReAct 角色 + execute/summarize/reflection prompt |
| `app/domain/services/prompts/en/*.py` | 英文版本 |
| `app/domain/services/agents/base.py` | prompt 组装与工具循环 |
| `app/domain/services/agents/planner.py` | PlannerAgent 使用 prompt 的方式 |
| `app/domain/services/agents/react.py` | ReActAgent 使用 prompt 的方式 |

---

*文档更新时间：2026-06-17*
