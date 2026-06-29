---
name: xuexitong-exam-assistant
description: 学习通/超星期末复习助手。采集授权课程资料和作业，先从课件/老师重点提炼 LLM 审核知识库，再逐题审核、补答案/解析、按知识点补足不少于 150 道高质量题，并用 templates/ 稳定三页模板生成先学后练复习 HTML。Use when the user asks to choose or review a Chaoxing/Xuexitong/学习通 course, collect course materials, extract a knowledge bank, audit extracted questions, build a final exam question bank, or generate exam-focused practice/mock HTML.
---

# 学习通期末复习助手

本 skill 的目标是生成稳定、可学习、可刷题的期末复习包。核心原则：**先从课件/老师重点提炼知识库，再用知识库约束逐题审核和补题；最终 HTML 必须先学知识点再练题，并只使用 `templates/` 中的稳定模板。**

## Safety Contract

- 只采集用户有权访问的课程、课件和作业。
- 不绕过 CAPTCHA、付费墙、DRM、隐藏答案控制、速率限制或平台访问控制。
- 不读取、导出、打印、提交密码、cookie、token 或浏览器存储。Chrome 登录态只能留在 gitignored 的本地 profile 中。
- 课程数据默认保存在 `data/courses/<course_slug>/`；Chrome profile 默认保存在 `data/browser-profile/`。
- NotebookLM 只能显式 opt-in，且只上传转换后的 Markdown bundle。

## Core Workflow

1. **采集与转换**：运行主流水线采集学习通资料、作业，转换 Markdown，构建 `course.db` 和 `generated/codex_context.md`。
2. **准备原始种子**：脚本生成 `generated/question_candidates.json` 和 `generated/teacher_knowledge_seeds.json`。这些只是原始候选，不是最终知识库或题库。
3. **LLM 提炼知识库**：智能体先读 `teacher_knowledge_seeds.json`、`materials_md/` 和可选 `input/teacher_focus.md`，写入 `generated/knowledge_bank.json`。老师重点优先级最高。
4. **LLM 逐题审核**：智能体逐题阅读候选题，写入 `generated/question_audit.json`，并从审核通过记录生成 `generated/question_bank.json`。每条 approved audit 和每道最终题都必须绑定 `knowledge_ids`。
5. **题量补足**：最终审核通过题数必须 `>=150`，目标约 `180`，通常不超过 `220`。不足时，只能基于 `knowledge_bank.json` 中审核通过的知识点生成新题；新题也必须进入 audit 并复审。
6. **模板渲染**：只有知识库、题库和 hash 绑定都通过校验后，运行 `generate_exam_pages.py` 渲染 `output/practice.html`、`output/questions.html`、`output/mock_exam.html`。

## Commands

Bootstrap once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_env.ps1
```

Interactive course collection and candidate preparation:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py
```

Known course URL:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --course-url "<course URL>" --course-name "<course name>"
```

Resume candidate preparation for an existing course:

```powershell
conda run -n qimokaisi-exam python scripts/build_course_db.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/prepare_review_context.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/prepare_question_candidates.py --course data/courses/<course_slug>
```

Render final pages after LLM audit:

```powershell
conda run -n qimokaisi-exam python scripts/generate_exam_pages.py --course data/courses/<course_slug>
```

`generate_exam_pages.py` will fail if `knowledge_bank.json` is missing/invalid, if `question_bank.summary.knowledge_bank_hash` is stale, if fewer than 150 approved questions exist, if any final question lacks `knowledge_ids`, `audit_id`, answer, analysis, `reviewed_by_llm: true`, or if weak legacy text appears.

## Knowledge Bank Contract

`generated/knowledge_bank.json` is mandatory before question audit/rendering. Each item must include:

- `knowledge_id`
- `chapter`
- `title`
- `learning_goal`
- `key_points`
- `formula_examples`
- `pitfalls`
- `exam_tips`
- `source_refs`
- `source_kind`: `teacher_focus | course_material`
- `reviewed_by_llm: true`
- `quality_status: approved`

`generated/teacher_knowledge_seeds.json` is only raw evidence. A heading-only seed may not be displayed directly as a final knowledge point.

## Per-Question LLM Audit Contract

Every item in `question_candidates.json` must receive one audit record. Do not bulk-approve by category.

Allowed decisions:

- `reuse_full`: 原题题干、答案、解析都合格，标准化后复用。
- `add_analysis`: 题干和答案可信，解析缺失或低质；LLM 补高质量得分导向解析。
- `infer_answer`: 题干/选项可信但答案缺失；LLM 基于题目和课件推断答案与解析，并标注 `answer_inferred: true` 和 `confidence`。
- `discard_noisy`: 课件原文、图片链接、作答记录、平台噪声、无法判断答案的题，不进入最终题库。
- `teacher_knowledge_generated`: 候选题不足时，LLM 基于老师 PPT/复习重点生成的新题；新题也必须逐题复审。

Required audit output:

```json
{
  "audit_id": "audit-0001",
  "source_id": "candidate id or generated seed id",
  "decision": "add_analysis",
  "original_stem": "...",
  "final_stem": "...",
  "final_answer": "...",
  "final_analysis": "...",
  "answer_inferred": false,
  "confidence": 0.92,
  "quality_notes": "补充得分导向解析",
  "knowledge_ids": ["K001"],
  "source_refs": ["materials_md/..."],
  "approved_for_bank": true,
  "reviewed_by_llm": true,
  "quality_status": "approved"
}
```

See `references/llm-question-audit.md` for the full batch workflow and final bank schema.

## Final Question Bank Contract

`generated/question_bank.json` must contain only approved, audited questions. Each item must include:

- `audit_id`
- `source_kind`: `xxt_reused | xxt_analysis_llm | xxt_answer_inferred | teacher_knowledge_generated`
- `stem` or `question`
- `type`
- `options`
- `answer`
- `analysis` or `explanation`
- `knowledge_ids`
- `reviewed_by_llm: true`
- `quality_status: approved`

`question_bank.summary.knowledge_bank_hash` must match the current validated `knowledge_bank.json`.

No final item may contain `资料未提供完整解析`、`参考答案待补充`、`正确答案：`、`参考答案见解析` or other weak placeholder text.

## Template Contract

- `templates/practice.html`、`templates/questions.html`、`templates/mock_exam.html` are the only visual/structural source of truth.
- Final HTML must keep the stable three-page structure, shared CSS style, chapter navigation, cross-links, and native `<details><summary>` answer folding.
- `practice.html` must display each chapter as “知识点学习” first, then “精选练习”; teacher focus cards are shown before course-material cards.
- `output/practice.generated.html` is legacy/debug-only and must not be treated as a final entry page.
- The final package must not include unaudited questions or old fallback links.

## Reference Files

- `references/llm-question-audit.md`: mandatory knowledge-first and per-question audit workflow and schemas.
- `references/llm-knowledge-bank.md`: mandatory knowledge extraction workflow and quality rules.
- `references/data-cleaning.md`: candidate quality flags and noise filtering.
- `references/multi-page-generation.md`: stable template rendering requirements.
- `references/practice-template.md`: practice page layout notes.

## Course Directory Contract

- `raw/materials/`: downloaded course files.
- `assignments_md/`: assignment Markdown and extracted question JSON.
- `materials_md/`: MinerU-converted course Markdown.
- `generated/codex_context.md`: compact context for LLM audit.
- `generated/question_candidates.json`: raw candidates, not final.
- `generated/teacher_knowledge_seeds.json`: raw teacher/course knowledge seeds, not final.
- `generated/knowledge_bank.json`: mandatory LLM-reviewed knowledge bank for learning display and question generation.
- `generated/question_audit.json`: mandatory per-question audit ledger.
- `generated/question_bank.json`: final audited bank.
- `output/practice.html`, `output/questions.html`, `output/mock_exam.html`: final stable HTML package.
