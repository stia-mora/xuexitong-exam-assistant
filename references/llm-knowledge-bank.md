# LLM 知识库提炼流程

`generated/knowledge_bank.json` 是题库审核和复习页展示的前置产物。它必须由智能体 LLM 从课件、原始知识种子和可选老师重点中提炼，不得直接把标题列表当成知识点。

## 输入优先级

1. `input/teacher_focus.md`：老师或用户手动给的复习重点，最高优先级。
2. `generated/teacher_knowledge_seeds.json`：脚本从课件标题和段落提取的原始种子。
3. `materials_md/`：需要核对定义、公式、例子、易错点时回读原文。
4. `generated/codex_context.md`：课程整体压缩上下文。

## 输出要求

每个知识点必须能直接支持“先学后练”，并包含：

- `knowledge_id`
- `chapter`
- `title`
- `learning_goal`
- `key_points`
- `formula_examples`
- `pitfalls`
- `exam_tips`
- `review_priority`: `P0 | P1 | P2`
- `source_refs`
- `source_kind`: `teacher_focus | course_material`
- `reviewed_by_llm: true`
- `quality_status: approved`

`teacher_focus` 知识点应在同章中优先展示，并优先用于补题覆盖。

`review_priority` 必须由 LLM 明确给出：

- `P0`：必会、高频、老师强调、真实考试题反复出现或是后续题目的基础概念。
- `P1`：重要但频率略低，通常用于简答、判断、设计题中的支撑步骤。
- `P2`：补充覆盖或查漏补缺内容，仍需可学习、可出题，但复习顺序排在 P0/P1 后。

不得只用数字 `priority` 代替 `review_priority`。数字排序可以保留给同一 P 级内部排序，但最终页面显示和渲染校验以 `P0/P1/P2` 为准。

## 质量规则

- 只有标题、没有解释的种子必须扩写或丢弃，不能直接入库。
- `key_points` 写定义、判定条件、性质、步骤等可学习内容。
- `formula_examples` 至少给一个公式、符号化例子、判定例子或典型应用。
- `pitfalls` 写常见误解、易混概念或考试失分点。
- `exam_tips` 写题目中如何识别考点、答案该怎么组织。
- `source_refs` 必须指向课件 Markdown 或 `input/teacher_focus.md`。

## 与题库的关系

- `question_audit.json` 中每条 approved audit 必须绑定 `knowledge_ids`。
- `question_bank.json` 中每道最终题必须显式绑定 `knowledge_ids`。
- `teacher_knowledge_generated` 题只能来自已审核知识点。
- `question_bank.summary.knowledge_bank_hash` 必须匹配当前 `knowledge_bank.json`。
