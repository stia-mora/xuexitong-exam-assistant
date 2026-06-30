# Candidate Data Cleaning

`prepare_question_candidates.py` 只负责收集候选题，不负责判定最终可用。清洗的目标是给 LLM 逐题审核提供高信号输入，并标注风险。

## 候选题来源优先级

1. `exams_md/*.questions.json`：已开放考试/试卷详情页提取的真实题，`source_kind=xxt_exam`，优先进入 LLM audit。
2. `assignments_md/*.questions.json`：作业和练习题，`source_kind=xxt_assignment`。
3. `materials_md/` 与老师重点：只能作为材料候选或知识点补题依据。

考试页采集失败不是清洗失败。采集器会在 manifest 中记录 `unavailable` 或 `failed_nonfatal`，并在 `output/crawl_report.md` 写明未开放、无权限、空页面或解析 0 题等原因。没有考试题时，后续仍按“作业候选 → 知识点补题 → 150 题下限”继续。

当自动采集打不开详情页，但用户 Chrome 里已经能看到“考试详情/查看详情”页面时，可以保存当前 DOM 为 `raw/exams_html/*.browser.html`，再用 `scripts/extract_exams.py` 转成 `exams_md/*.questions.json`。这类浏览器兜底文件与自动采集结果同等处理，仍以 `source_kind=xxt_exam` 优先进入逐题 LLM 审核。

## 常见质量标记

- `missing_answer`：候选题没有可靠答案。
- `missing_analysis`：候选题没有解析或解析为空。
- `missing_options`：选择题缺少选项。
- `possible_noise`：题干像课件原文、平台 UI、图片链接或作答记录。

这些标记不是最终结论。智能体仍必须逐题审核，并在 `question_audit.json` 中写明决策。

## 噪声模式

审核时优先丢弃以下内容：

```text
作答记录, 我的答案, 此附件仅支持打开, 作业详情,
题量:, 满分:, 作答时间:, 智能分析, 未在报告中展开,
![](../assets/, 单选题（共, 判断题（共, 简答题（共
```

题干超过 500 字、包含整段讲义说明、计算过程正文、表格残片时，也通常应 `discard_noisy`。

## 答案与解析处理

- 有答案无解析：使用 `add_analysis`，保留原答案，LLM 补得分导向解析。
- 无答案但题干/选项可信：使用 `infer_answer`，结合 `codex_context.md` 和 `materials_md/` 推断答案，并标注置信度。
- 无答案且无法可靠推断：使用 `discard_noisy`，不得进入最终题库。

## 去重

按清洗后的题干去重。若重复题中某一条有答案或解析，优先保留信息更完整的版本，并在 audit 的 `quality_notes` 说明合并来源。

## 章节归类

优先使用候选题自带 chapter。若缺失，按 source_refs 对应课件/作业归类；仍无法判断时，用题干关键词归类，并在 audit 说明。

## 最终校验

最终 `question_bank.json` 不能包含：

- 缺答案题
- 缺解析题
- 未审核题
- 弱占位解析
- 平台噪声或课件原文误抓题
