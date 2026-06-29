# Stable Multi-Page Generation

最终 HTML 只能由已审核的 `generated/knowledge_bank.json` 和 `generated/question_bank.json` 渲染。`generate_exam_pages.py` 不负责提炼知识点、出题、补答案、补解析，也不生成未审核题。

## 数据前置条件

渲染前必须满足：

- `generated/question_audit.json` 存在。
- `generated/question_bank.json` 存在。
- `generated/knowledge_bank.json` 存在，且每个知识点已 LLM 审核通过。
- `question_bank.summary.knowledge_bank_hash` 与当前知识库一致。
- 最终审核通过题数不少于 150。
- 每道最终题都有 `knowledge_ids`、`audit_id`、答案、解析、`reviewed_by_llm: true`、`quality_status: approved`。

## 模板来源

三页输出必须继承：

- `templates/practice.html`
- `templates/questions.html`
- `templates/mock_exam.html`

保留这些模板的页面密度、CSS 变量、章节导航、三页互链和 `<details><summary>` 折叠答案结构。

## 页面分工

- `practice.html`：教学复习入口，每章先展示知识点学习卡片，再展示章节精选练习和复习路线。
- `questions.html`：完整题库，按章节平铺展示全部已审核题。
- `mock_exam.html`：只从已审核题库抽题，不做临时补题。

## 禁止项

- 不得把 `practice.generated.html` 当最终入口。
- 不得出现 `资料未提供完整解析`、`参考答案待补充` 等弱文本。
- 不得渲染无 `audit_id` 的题。
- 不得渲染无 `knowledge_ids` 的题。
- 不得把 `teacher_knowledge_seeds.json` 的标题种子直接当成页面知识点。
- 不得在 HTML 渲染阶段生成新题。
