# LLM 逐题审核流程

本文件用于指导智能体先把课件/老师重点变成 `knowledge_bank.json`，再把 `question_candidates.json` 变成最终可渲染的 `question_audit.json` 和 `question_bank.json`。

## 输入文件

- `generated/codex_context.md`：课程材料压缩上下文，先读。
- `generated/question_candidates.json`：学习通考试、作业和课件中提取的候选题，每题都要审核。脚本按 `xxt_exam`、`xxt_assignment`、材料候选的顺序写入，考试题优先处理。
- `generated/teacher_knowledge_seeds.json`：老师 PPT/资料中的原始知识种子，不可直接作为页面知识点。
- `input/teacher_focus.md`：可选，老师或用户手动提供的复习重点，优先级最高。
- `generated/knowledge_bank.json`：LLM 审核后的知识库，题目审核和补题必须基于它。
- `materials_md/`：需要精确核对定义、术语或答案时再读。

## 审核顺序

1. 先读取 `teacher_knowledge_seeds.json`、`materials_md/` 和可选 `input/teacher_focus.md`，提炼 `knowledge_bank.json`。
2. 每个知识点必须有学习目标、核心要点、公式/例子、易错点、考试提示、来源引用和 `review_priority: P0|P1|P2`，并标记 `reviewed_by_llm: true`、`quality_status: approved`。
3. 读取候选题总数、题型分布、质量 flags。若存在 `source_kind=xxt_exam` 的候选题，先审核考试题；考试题仍不得跳过逐题审计。
4. 按候选题顺序逐题审核，不得批量通过；每条 approved audit 必须绑定 `knowledge_ids`。
5. 每审核一批就保存 `question_audit.json`，避免上下文中断丢失。
6. 根据审核通过记录生成 `question_bank.json`，每题必须显式包含 `knowledge_ids`。
7. 如果审核通过题数少于 150，只能按章节覆盖从 `knowledge_bank.json` 补题。
8. 新补题也必须写入 audit，且通过复审后才能进入 question_bank。
9. 达到至少 150 题后，优先补到约 180 题；超过 220 时按知识点覆盖、题源优先级和题目质量精选。

## 决策规则

### reuse_full

用于原题题干、答案、解析都合格的题。允许做格式标准化，不改变考点。

### add_analysis

用于题干和答案可信，但解析为空、过短、只有“正确答案”或不服务得分的题。LLM 必须补：

- 这题核心知识点
- 正确答案依据
- 易混点或常见失分点
- 考试拿分提示

### infer_answer

用于题干/选项可信但答案缺失的题。LLM 可根据题目、选项、课件上下文推断答案，但必须：

- 标注 `answer_inferred: true`
- 写 `confidence`，建议 0.60-0.95
- 在 `quality_notes` 写依据
- 若无法达到可靠判断，改为 `discard_noisy`

### discard_noisy

用于非题目内容或不可恢复内容，例如：

- 课件整段原文、公式推导过程被误抓成题
- 图片链接、表格残片、平台 UI 文本
- “作答记录”“我的答案”“此附件仅支持打开”等噪声
- 缺少足够信息且无法从课程材料推断答案

### teacher_knowledge_generated

用于审核通过题不足 150 时。生成新题必须基于 `knowledge_bank.json` 中审核通过的知识点，不得离开课程范围。每题仍需完整答案和得分导向解析，并绑定对应 `knowledge_ids`。

## 考试题后退规则

- `exams_md/*.questions.json` 中的候选题为最高优先级真实题源。
- 若自动采集无法进入试卷详情，但用户 Chrome 中已经打开可见的“考试详情/查看详情”页，可用 Chrome skill 保存当前 DOM HTML 到 `raw/exams_html/*.browser.html`，再运行 `scripts/extract_exams.py` 生成 `exams_md/*.questions.json`。
- 考试页不可访问、老师未开放、无权限、空页面或解析 0 题时，不得臆造“考试原题”；继续使用作业候选和知识点补题。
- `output/crawl_report.md` 会记录考试 manifest 状态。`unavailable` 与 `failed_nonfatal` 不是流水线失败，只是说明本轮没有可用考试题。
- 只有显式运行采集器的 `--require-exams` 调试开关时，考试题为 0 才能使采集失败。
- 考试候选进入最终题库时，按审核结果标注为 `xxt_exam_reused`、`xxt_exam_analysis_llm` 或 `xxt_exam_answer_inferred`。

## knowledge_bank.json 结构

推荐结构：
```json
{
  "schema_version": 1,
  "updated_at": "2026-06-29T00:00:00Z",
  "items": [
    {
      "knowledge_id": "K001",
      "title": "命题与真值",
      "chapter": {"key": "ch2", "title": "第2章 命题逻辑"},
      "learning_goal": "能判断一个语句是否为命题，并说明真值来源。",
      "key_points": ["命题必须是具有确定真值的陈述句"],
      "formula_examples": ["例：'地球是圆的' 是命题；'x>5' 在未给论域和值时不是命题"],
      "pitfalls": ["疑问句、祈使句、含自由变量的开放语句通常不是命题"],
      "exam_tips": ["判断题先看是否陈述句，再看真值是否确定"],
      "review_priority": "P0",
      "source_refs": ["materials_md/2_1_what is proposition.md"],
      "source_kind": "course_material",
      "reviewed_by_llm": true,
      "quality_status": "approved"
    }
  ]
}
```

`source_kind=teacher_focus` 的知识点来自 `input/teacher_focus.md`，在复习页中优先展示。

## question_audit.json 结构

推荐结构：

```json
{
  "schema_version": 1,
  "updated_at": "2026-06-29T00:00:00Z",
  "items": [
    {
      "audit_id": "audit-0001",
      "source_id": "candidate id",
      "decision": "add_analysis",
      "original_stem": "原题干",
      "final_stem": "清洗后的题干",
      "type": "单选题",
      "options": [{"label": "A", "text": "..."}],
      "final_answer": "C",
      "final_analysis": "这题核心知识点：...",
      "answer_inferred": false,
      "confidence": 0.94,
      "quality_notes": "原答案可信，补充得分导向解析。",
      "knowledge_ids": ["K001"],
      "source_refs": ["exams_md/example.questions.json"],
      "chapter": {"key": "ch1", "title": "第1章 ..."},
      "approved_for_bank": true,
      "reviewed_by_llm": true,
      "quality_status": "approved"
    }
  ]
}
```

丢弃题也要记录，`approved_for_bank` 为 `false`，`quality_status` 可为 `discarded`。

## question_bank.json 结构

推荐结构：

```json
{
  "schema_version": 1,
  "updated_at": "2026-06-29T00:00:00Z",
  "items": [
    {
      "id": "Q001",
      "audit_id": "audit-0001",
      "source_kind": "xxt_exam_analysis_llm",
      "type": "单选题",
      "stem": "题干",
      "options": [{"label": "A", "text": "..."}],
      "answer": "C",
      "analysis": "这题核心知识点：...",
      "knowledge_ids": ["K001"],
      "answer_inferred": false,
      "confidence": 0.94,
      "reviewed_by_llm": true,
      "quality_status": "approved",
      "source_refs": ["assignments_md/example.questions.json"]
    }
  ]
}
```

允许脚本从 audit 记录补齐题干、答案、解析，但 final bank 中必须显式包含 `audit_id` 和 `knowledge_ids`。`question_bank.summary.knowledge_bank_hash` 必须匹配当前 `knowledge_bank.json`，否则禁止渲染。

## 最终自检

渲染前确认：

- `question_candidates.json` 中每个 candidate id 都能在 audit 的 `source_id` 找到。
- `knowledge_bank.json` 中每个知识点都有学习目标、核心要点、公式/例子、易错点、考试提示、来源引用、`review_priority: P0|P1|P2` 和 LLM 审核标记。
- `question_bank.json` 中每题都有 audit_id 和 knowledge_ids，且对应 audit 为 approved。
- `question_bank.summary.knowledge_bank_hash` 与当前知识库一致。
- 审核通过题数不少于 150。
- 没有 `资料未提供完整解析`、`参考答案待补充`、`正确答案：`、`参考答案见解析`。
- 三页 HTML 只能由 `generate_exam_pages.py` 从审核题库渲染。
