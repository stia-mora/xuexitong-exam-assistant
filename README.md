# 学习通期末复习助手 (xuexitong-exam-assistant)

把学习通/超星课程资料采集下来，先由智能体 LLM 从课件/老师重点提炼 **知识库**，再逐题审核候选题、补答案/解析、按知识点补足题量，最后用 `templates/` 中的稳定三页模板生成“先学知识点再练题”的期末复习 HTML。

## 快速开始

### 1. 环境准备

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_env.ps1
```

### 2. 采集课程并准备候选题

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py
```

流水线会采集资料、转换 Markdown、建库、准备上下文，并生成：

- `generated/question_candidates.json` — 学习通/课件提取的候选题，不是最终题库
- `generated/teacher_knowledge_seeds.json` — 老师 PPT/资料中的原始知识点种子，不是最终知识库
- `generated/codex_context.md` — 供 LLM 审核时阅读的课程上下文

如果还没有 `knowledge_bank.json`、`question_audit.json` 和已审核 `question_bank.json`，流水线会停在等待 LLM 审核的状态，不会生成最终 HTML。

### 3. 智能体 LLM 提炼知识库

先读取 `teacher_knowledge_seeds.json`、`materials_md/` 和可选的 `input/teacher_focus.md`，生成 `generated/knowledge_bank.json`。每个知识点必须包含学习目标、核心要点、公式/例子、易错点、考试提示、来源引用，并标记 `reviewed_by_llm: true`、`quality_status: approved`。

### 4. 智能体 LLM 逐题审核

逐条阅读 `question_candidates.json`，每题写入一条 `question_audit.json` 记录。处理优先级：

1. `reuse_full`：题干、答案、解析都合格，复用。
2. `add_analysis`：题干和答案可信，LLM 补高质量解析。
3. `infer_answer`：题干/选项可信但答案缺失，LLM 基于课件推断答案并标注置信度。
4. `discard_noisy`：课件原文、平台噪声、无法判断答案的题丢弃。
5. `teacher_knowledge_generated`：审核通过题不足 150 时，基于 `knowledge_bank.json` 中的知识点补题；新题也要复审。

最终审核通过题数必须不少于 150，目标约 180，通常不超过 220。

### 5. 渲染最终 HTML

```powershell
conda run -n qimokaisi-exam python scripts/generate_exam_pages.py --course data/courses/<课程目录>
```

渲染前会强制校验：

- 每题都有 `audit_id`
- 每题都有答案和解析
- 每题 `reviewed_by_llm: true`
- 每题 `quality_status: approved`
- 每题都有 `knowledge_ids`
- `question_bank.summary.knowledge_bank_hash` 与当前 `knowledge_bank.json` 一致
- 审核通过题数 `>= 150`
- 不含 `资料未提供完整解析`、`参考答案待补充` 等旧版弱文本

最终输出：

- `output/practice.html` — 教学复习入口
- `output/questions.html` — 全部题库
- `output/mock_exam.html` — 从已审核题库抽取的模拟卷

## 目录结构

```text
xuexitong-exam-assistant/
├── SKILL.md
├── AGENT.md
├── scripts/
│   ├── run_course_pipeline.py
│   ├── prepare_question_candidates.py
│   ├── generate_exam_pages.py
│   └── ...
├── templates/
│   ├── practice.html
│   ├── questions.html
│   └── mock_exam.html
├── references/
│   ├── llm-question-audit.md
│   ├── llm-knowledge-bank.md
│   ├── data-cleaning.md
│   └── multi-page-generation.md
└── data/
    ├── courses/<课程名>/
    └── browser-profile/
```

## 核心约束

- 最终题库必须来自逐题 LLM 审核，不允许脚本批量兜底通过。
- 最终知识库必须先于题库生成，题目必须绑定 `knowledge_ids`。
- `practice.html` 必须先展示每章知识点学习卡片，再展示精选练习。
- 学习通原题优先；缺解析补解析；缺答案可推断但必须标注依据和置信度。
- 题量不足时，才基于已审核知识库生成新题。
- `templates/` 是最终 HTML 的唯一结构和视觉标准。
- `practice.generated.html` 只允许作为调试遗留页，不是最终入口。

## 许可证

MIT
