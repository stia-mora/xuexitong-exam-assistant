# 学习通期末复习长期规则

本项目长期用于学习通/超星课程采集与期末复习包生成。默认遵守以下规则。

## 完整流程

```text
采集课件/作业 → MinerU 转 Markdown → 建 SQLite 数据库 → 准备 codex_context
→ 准备 question_candidates + teacher_knowledge_seeds
→ 智能体 LLM 先提炼 knowledge_bank
→ 基于 knowledge_bank 逐题审核并写 question_audit
→ 生成绑定 knowledge_ids 的最终 question_bank
→ 校验知识库 hash + 不少于 150 道审核通过题
→ 用 templates/ 渲染先学后练 practice/questions/mock_exam
```

## 知识库先行强约束

- `generated/knowledge_bank.json` 必须先于题库生成；`teacher_knowledge_seeds.json` 只是原始种子。
- 可选 `input/teacher_focus.md` 是最高优先级知识来源。
- 每个知识点必须有学习目标、核心要点、公式/例子、易错点、考试提示、来源引用、`reviewed_by_llm: true`、`quality_status: approved`。
- 只有标题、没有实质说明的知识种子不得直接进入复习页。
- `question_bank.summary.knowledge_bank_hash` 必须匹配当前知识库。

## 逐题 LLM 审核强约束

- `generated/question_candidates.json` 中每一道候选题都必须由智能体 LLM 逐题审核。
- 不得按章节、题源或题型批量通过。
- 被丢弃题也必须在 `generated/question_audit.json` 中记录原因。
- LLM 新生成题必须先进入 audit，再复审通过，才能进入最终题库。
- `generated/question_audit.json` 中每条 approved audit 必须绑定 `knowledge_ids`。
- `generated/question_bank.json` 每题必须有 `knowledge_ids`、`audit_id`、答案、解析、`reviewed_by_llm: true`、`quality_status: approved`。

## 题目处理优先级

1. `reuse_full`：学习通/课件原题题干、答案、解析都合格，标准化复用。
2. `add_analysis`：题干和答案可信，解析缺失或低质，由 LLM 补得分导向解析。
3. `infer_answer`：题干/选项可信但答案缺失，LLM 基于课件推断答案与解析，标注置信度。
4. `discard_noisy`：课件原文、平台噪声、图片链接、作答记录、无法判断答案的题丢弃。
5. `teacher_knowledge_generated`：审核通过题不足 150 时，基于已审核 `knowledge_bank.json` 补题。

## 题量要求

- 最终审核通过题数硬下限：150。
- 目标题量：约 180。
- 通常不超过 220；超过时按老师 PPT/作业/推断题/补充题优先级筛选。

## 解析规则

- 客观题：写清“这题核心知识点”、正确答案依据、易混点。
- 主观题：写清“答题核心点”、“必须出现”的关键词/步骤、“常见失分点”。
- 解析必须服务考试得分，不能使用 `资料未提供完整解析`、`参考答案待补充` 等弱文本。

## 输出规则

- 最终 HTML 只能使用 `templates/practice.html`、`templates/questions.html`、`templates/mock_exam.html` 的结构和风格。
- `practice.html` 必须每章先展示知识点学习卡片，再展示精选练习。
- 答案统一使用原生 `<details><summary>` 折叠。
- `practice.generated.html` 是遗留调试页，不得作为最终入口或质量基准。
