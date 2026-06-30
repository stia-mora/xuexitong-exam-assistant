---
name: xuexitong-exam-assistant
description: 学习通/超星期末复习助手。采集授权课程资料、作业和已开放考试详情，先从课件/老师重点提炼 LLM 审核知识库，再优先审核考试/作业候选题、补答案/解析、按知识点补足不少于 150 道高质量题，并用 templates/ 稳定三页模板生成先学后练复习 HTML。Use when the user asks to choose or review a Chaoxing/Xuexitong/学习通 course, collect course materials/exams, extract a knowledge bank, audit extracted questions, build a final exam question bank, or generate exam-focused practice/mock HTML.
---

# 学习通期末复习助手

本 skill 的目标是生成稳定、可学习、可刷题的期末复习包。核心原则：**先从课件/老师重点提炼知识库，再用知识库约束逐题审核和补题；最终 HTML 必须先学知识点再练题，并只使用 `templates/` 中的稳定模板。**

## Safety Contract

- 只采集用户有权访问的课程、课件、作业和已经开放的考试/试卷详情。
- 不绕过 CAPTCHA、付费墙、DRM、隐藏答案控制、速率限制或平台访问控制。
- 不读取、导出、打印、提交密码、cookie、token 或浏览器存储。Chrome 登录态只能留在 gitignored 的本地 profile 中。
- 课程数据默认保存在 `data/courses/<course_slug>/`；Chrome profile 默认保存在 `data/browser-profile/`。
- NotebookLM 只能显式 opt-in，且只上传转换后的 Markdown bundle。

## Core Workflow

1. **采集与转换**：运行主流水线采集学习通资料、作业和可访问考试详情，转换 Markdown，构建 `course.db` 和 `generated/codex_context.md`。考试题是高优先级增强项；无考试 tab、老师未开放、无权限、空页面或解析 0 题时默认非阻塞后退。
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

Exam collection is enabled by default and writes `raw/exams_html/` plus `exams_md/*.questions.json` when visible exam pages are accessible. Useful switches:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --exam-limit 3 --pause-each-exam
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --skip-exams
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --require-exams
```

Use `--require-exams` only for debugging exam extraction; normal course builds must continue when exams are unavailable and record the reason in `output/crawl_report.md`.

If the automatic exam tab explorer finds the exam list but cannot open the detail page, use the Chrome skill as a fallback with the user's existing logged-in tab: open/claim the visible Chaoxing exam detail or review page, save the full DOM HTML into `raw/exams_html/<paper>.browser.html`, then run:

```powershell
conda run -n qimokaisi-exam python scripts/extract_exams.py --html data/courses/<course_slug>/raw/exams_html/<paper>.browser.html --output-dir data/courses/<course_slug>/exams_md --title "<paper title>" --source-url "<current Chrome URL>"
conda run -n qimokaisi-exam python scripts/build_course_db.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/prepare_review_context.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/prepare_question_candidates.py --course data/courses/<course_slug>
```

This fallback is still permission-respecting: only capture pages already visible to the logged-in user. If Chrome cannot access a paper because the teacher has not opened it, record the nonfatal reason and continue with knowledge-bank question generation.

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
- `review_priority`: `P0 | P1 | P2`
- `source_refs`
- `source_kind`: `teacher_focus | course_material`
- `reviewed_by_llm: true`
- `quality_status: approved`

`generated/teacher_knowledge_seeds.json` is only raw evidence. A heading-only seed may not be displayed directly as a final knowledge point.

`review_priority` must be assigned by the LLM while building the knowledge bank: `P0` means must-master/high-frequency or teacher-emphasized, `P1` means important supporting exam content, and `P2` means lower-frequency material for coverage and cleanup. The practice page renders these priorities before chapter sections and on each knowledge card.

## Per-Question LLM Audit Contract

Every item in `question_candidates.json` must receive one audit record. Do not bulk-approve by category.

Candidate priority is: `xxt_exam` from `exams_md/*.questions.json` first, then `xxt_assignment`, then material-derived candidates. Real exam questions may be reused, supplemented with analysis, or have answers inferred, but they still require one audit record per question.

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
- `source_kind`: `xxt_exam_reused | xxt_exam_analysis_llm | xxt_exam_answer_inferred | xxt_reused | xxt_analysis_llm | xxt_answer_inferred | teacher_knowledge_generated`
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
- `practice.html` must display each chapter as “知识点学习” first, then “精选练习”; teacher focus cards and P0/P1/P2 priority tags are shown before course-material ordering.
- `practice.html` chapter navigation must be sticky at the top of the page so learners can jump chapters while scrolling.
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
- `raw/exams_html/`: captured accessible exam/review HTML pages.
- `exams_md/`: accessible exam Markdown and extracted `.questions.json`.
- `assignments_md/`: assignment Markdown and extracted question JSON.
- `materials_md/`: MinerU-converted course Markdown.
- `generated/codex_context.md`: compact context for LLM audit.
- `generated/question_candidates.json`: raw candidates, not final.
- `generated/teacher_knowledge_seeds.json`: raw teacher/course knowledge seeds, not final.
- `generated/knowledge_bank.json`: mandatory LLM-reviewed knowledge bank for learning display and question generation.
- `generated/question_audit.json`: mandatory per-question audit ledger.
- `generated/question_bank.json`: final audited bank.
- `output/practice.html`, `output/questions.html`, `output/mock_exam.html`: final stable HTML package.
