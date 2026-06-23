---
name: xuexitong-exam-assistant
description: 学习通期末复习助手。采集学习通/超星课程课件和作业 → MinerU转Markdown → 建本地课程数据库 → 清洗材料提知识点 → 按题源优先级出题 → 得分导向解析 → 生成期末复习HTML。Use when the user asks to choose a Chaoxing/Xuexitong/学习通 course for final exam review, says "选择一个学习通课程进行复习", or wants to collect course materials and generate exam-focused review content.
---

# 学习通期末复习助手

把学习通课程资料采集下来，按考试得分导向整理成知识点、题目和解析，生成可用的期末复习 HTML。

## Safety Contract

- Collect only courses and files the user is authorized to access.
- Do not bypass CAPTCHA, paywalls, DRM, rate limits, hidden-answer controls, or platform access controls.
- Do not ask for, print, or store passwords, cookies, tokens, or browser storage.
- Keep all course data local under the selected workspace, normally `data/courses/<course_slug>/`.
- Reuse the dedicated Chrome profile at `data/browser-profile/`; let the user handle login, QR scan, CAPTCHA, popups, and revalidation in Chrome.

---

# 第一阶段：采集

## Workflow

When the user says `选择一个学习通课程进行复习` or asks to review a course:

1. Run the pipeline from the cloned repository or selected workspace root. Do not pass `--course-url` when the user wants to choose in Chrome.
2. Tell the user Chrome is open and ask them to enter the target course page. The collector waits for the course and then crawls visible materials and assignments.
3. If login expires, CAPTCHA appears, navigation is blocked, or a popup needs action, pause and let the user operate Chrome.
4. After the script finishes, read `output/crawl_report.md` and report: course title, course directory, downloaded files and sizes, skipped/failed items, assignment pages, question counts, conversion results, database counts, and generated outputs.

## Commands

Bootstrap the environment once from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_env.ps1
```

Interactive course selection:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py
```

Known course URL:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --course-url "<course URL>" --course-name "<course name>"
```

If the skill was installed globally or outside the repository, run from the desired workspace and pass an explicit workspace root when useful:

```powershell
conda run -n qimokaisi-exam python <path-to-skill>/scripts/run_course_pipeline.py --workspace-root "E:\path\to\workspace"
```

Optional MinerU override for users with an existing OCR environment:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --mineru-bin conda:data_pipeline
```

The pipeline collects materials, converts them with MinerU, builds `course.db`, prepares `generated/codex_context.md`, writes `output/final_review_seed.md`, renders a fallback page at `output/practice.generated.html`, builds the multi-page exam package (`practice.html`, `questions.html`, `mock_exam.html`, optional `teacher_mock_analysis.html`), and writes `output/crawl_report.md`.

## 采集故障恢复

流水线可能因以下原因在转换步骤失败。出问题后不要重跑全流水线，而是诊断 → 修复 → 单步恢复。

### 常见故障

| 故障 | 表现 | 修复 |
|------|------|------|
| **课件在 RAR/ZIP 压缩包里** | `raw/materials/` 只有 `.rar`/`.zip`，`materials_md/` 几乎为空 | 用 7z/WinRAR 解压到 `raw/materials/`，然后重跑 `convert_materials.py` |
| **`.doc` 格式不支持** | MinerU 报 `No supported documents found` | MinerU 只支持 `.docx`。如果有 LibreOffice 可转格式；否则若作业 JSON 已有题目数据可跳过 |
| **PPTX 转换超时** | 大文件（>10 MB）转换超时 | 增大 `--office-timeout`（默认 240 秒不够时用 480） |

### RAR 解压

在 Windows 上查找 7z：

```bash
# 搜索 7z
where 7z
# 常见路径：C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\7z.exe
# 也可用 WinRAR：C:\Program Files\WinRAR\UnRAR.exe
```

解压并移动课件：

```bash
cd "data/courses/<course_slug>/raw/materials"
mkdir -p 课件解压
"/c/path/to/7z" x -y -o"课件解压" "课件文件.rar"
mv 课件解压/*/*.pptx .   # 移到 materials 目录
```

### 恢复转换

修复问题后，单独重跑转换步骤（不重跑采集）：

```bash
conda run -n qimokaisi-exam python scripts/convert_materials.py --course data/courses/<course_slug> --backend pipeline --method ocr --lang ch --office-timeout 480
```

转换成功后会继续建库 `build_course_db.py` 和后续步骤。

## Optional NotebookLM Enrichment

NotebookLM is an opt-in enhancer. It uploads only converted local Markdown bundles, not the original PPT/PDF files, and stores NotebookLM auth/profile state under `data/notebooklm/` when `NOTEBOOKLM_HOME` is not already set.

Dry-run bundle validation:

```powershell
conda run -n qimokaisi-exam python scripts/notebooklm_sync.py --course data/courses/<course_slug> --dry-run
```

Full pipeline with NotebookLM enabled:

```powershell
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py --course data/courses/<course_slug> --skip-collect --skip-convert --notebooklm
```

Useful flags:

```text
--notebooklm-profile <profile>   Use a named NotebookLM profile.
--notebooklm-dry-run             Build bundles/manifest without contacting Google.
--notebooklm-strict              Fail the pipeline if NotebookLM sync fails.
```

Before live sync, authenticate locally with NotebookLM's own CLI:

```powershell
conda run -n qimokaisi-exam notebooklm login
conda run -n qimokaisi-exam notebooklm auth check --test --json
```

Generated outputs live under `generated/notebooklm/`; normalized quiz/flashcards are copied into `generated/practice_items.json` only when live NotebookLM items are available, then `build_course_db.py` imports them into the local practice page.

## Step-by-Step Tools

Use these only when debugging or resuming a partial run:

```powershell
conda run -n qimokaisi-exam python scripts/collect_course.py
conda run -n qimokaisi-exam python scripts/convert_materials.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/build_course_db.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/prepare_review_context.py --course data/courses/<course_slug>
conda run -n qimokaisi-exam python scripts/report_course_collection.py --course data/courses/<course_slug>
```

Render only the script fallback page:

```powershell
conda run -n qimokaisi-exam python scripts/render_practice_html.py --course data/courses/<course_slug> --output data/courses/<course_slug>/output/practice.generated.html
```

Generate only the multi-page exam package:

```powershell
conda run -n qimokaisi-exam python scripts/generate_exam_pages.py --course data/courses/<course_slug>
```

**⚠️ 此脚本会从 `course.db` 重新提取题目并覆盖 `generated/question_bank.json`。** 如果你已手写了高质量题库，先备份。

**⚠️ `generate_exam_pages.py` 已知产出质量问题：**

该脚本产出的页面质量因课程而异，不可盲目接受。运行后必须检查：

| 页面 | 常见问题 | 触发条件 |
|------|---------|---------|
| `mock_exam.html` | 题目是整段课件原文（含图片链接、表格、计算过程），不是考试题 | 脚本从 `materials_md/` 提取了非题目内容 |
| `practice.html` | 仅 5 KB 导航页（只有入口卡片），无章节知识介绍和嵌入式练习 | 默认产出就是导航页，脚本不生成教学型 practice.html |
| `questions.html` | 解析是兜底文本（"资料未提供完整解析"） | 256/278 题的解析来自 DB 的弱数据 |

**当以上问题出现时，执行手动生成流程（见下方"手动生成页面"节）。** 

如果你已手写了高质量题库，先备份：

```powershell
# 备份手写题库
cp data/courses/<course_slug>/generated/question_bank.json data/courses/<course_slug>/generated/question_bank_manual.json
# 运行脚本生成 HTML 页面结构
conda run -n qimokaisi-exam python scripts/generate_exam_pages.py --course data/courses/<course_slug>
# 脚本运行后手写题库已被覆盖；用备份恢复或手动合并解析
```

Useful exam-page flags:

```text
--teacher-mock <path|stem|title>    Force which material is the teacher mock paper.
--questions-per-chapter 80          Keep at most this many question-bank items per chapter.
--ai-fill-min-per-chapter 25        Fill sparse chapters to this many items with AI/Codex-style fallback questions.
--skip-exam-pages                   Pipeline flag: skip question_bank/mock_exam/multi-page HTML generation.
```

---

# 第二阶段：复习产出

采集完成后，进入复习产出阶段。以下规则是核心方法论，必须严格遵守。

## ⚠️ LLM 题库兜底规则（最高优先级）

**从 `assignments_md/*.questions.json` 提取的原始题库数据质量不可靠，几乎必然存在以下问题：**

- 大部分题目的答案字段为空、仅含单字母（"B"）或无意义的"正确答案："
- 题干混杂爬取残留（"作答记录""我的答案""此附件仅支持打开"等）
- 选项格式不统一（dict/string 混合，含应答标记混入）
- 解析字段普遍缺失

**因此，不得直接使用 JSON 提取的原始数据作为最终题库。处理流程如下：**

1. 先尝试从 JSON 提取，快速评估数据质量：检查有答案的比例、有完整题干的比例。
2. 如果有效题目占比低于 50%（几乎必然），**放弃 JSON 数据，直接用 LLM 知识重建题库**。
3. LLM 重建时：
   - 基于已阅读的课件材料（`codex_context.md`、`materials_md/`）中的知识点和章节结构
   - 覆盖所有章节，重点章节（P0）题量 > 次要章节（P2）
   - 目标总量 150~200 题，覆盖选择、判断、填空、简答四种题型
   - **每道题必须**包含：干净题干、选项（如有）、明确正确答案、得分导向解析
   - 解析按本 skill 的解析规则写（客观题补核心知识点+易混点，主观题补答题核心点+必须出现+常见失分点）
4. 将 LLM 生成的题库写入 `generated/question_bank.json`，覆盖原始提取数据。
5. 题源标记使用 `老师PPT`（基于课件知识点的题）或 `AI补充`（课件未覆盖但属于课程范围内的合理推断）。

**核心原则：宁可要 150 道每题都有答案和解析的精品题，不要 800 道缺胳膊少腿的垃圾题。**

### LLM 题库生成执行注意事项

**⚠️ `generate_exam_pages.py` 会从 `course.db` 重新提取题库并覆盖 `question_bank.json`** — 所有手写解析在脚本运行后丢失。正确流程有两种选择：

**方案A（推荐 — 接受脚本生成的答案+解析）**：建库后直接运行 `generate_exam_pages.py`，脚本从 DB 提取题目并自动生成解析。优点：无额外工作量，278题都有答案。缺点：256/278 解析是兜底文本（非知识点解析）。

**方案B（追求高质量解析）**：LLM 手写出题后，**先备份 `question_bank.json`**，再运行 `generate_exam_pages.py` 生成 HTML 页面结构。然后用备份的手写解析替换 HTML 中对应题目的弱解析。

**150~200 题的大型 JSON 生成实操**：
- 单次 `execute_code` 可能因 token 限制无法完成全部出题。将出题拆成多轮：Ch1+Ch2 → 保存 → Ch3 → 保存 → Ch4+Ch5 → 保存。
- **每轮末尾必须 `json.dump` 到文件**（不能只存在 Python 变量中），否则下一轮 `execute_code` 从空文件读取会丢失前轮数据。这是本 session 踩过的坑：第一轮内存生成了 Ch1 的 40 题但未保存，第二轮读取的 `{"questions":[]}` 只有空数组，导致 Ch1 数据永久丢失。
- **拆轮验证**：每轮读取上轮保存的文件 → `Q = json.load(f)['questions']` → 追加新题 → 保存。加载后立即打印 `len(Q)` 确认加载成功再开始出题。
- Python 字符串中的中文双引号（如 `"用户观点"`）会破坏 Python 语法。统一使用单引号包裹 Python 字符串，避免内嵌未转义双引号。
- 每轮执行后立即验证 `len(questions)` 和章节分布，发现异常即刻修复。

### 手动生成页面（当 `generate_exam_pages.py` 产出不合格时）

**触发条件：** `mock_exam.html` 含课件原文而非考试题、`practice.html` 只有导航页无教学内容。

**流程：**

1. **保留 `questions.html`** — 脚本生成的题库页通常可用（有搜索/筛选/折叠功能），即使解析偏弱也有正确答案。

2. **手动构建 `practice.html`** — 参照 `templates/practice.html` 的 CSS 和结构。必须包含：
   - 顶部入口双卡片（题库+模拟卷链接）
   - 采集统计摘要（stats 数字卡片）
   - P0/P1/P2 三栏优先级图谱
   - 置顶导航 pill 按钮
   - 得分提示（黄色框，按题型分条）
   - **每章一个折叠区**：知识点小节（概念定义 + `<div class="tr">` 易错陷阱）+ 3~5 道嵌入式精选练习题（`<details>` 折叠答案）
   - 推荐复习路线
   - 来源索引

3. **手动构建 `mock_exam.html`** — 干净试卷，禁止出现课件原文/图片/表格。结构：
   - 一、单选题 30 题 → 二、判断题 15 题 → 三、填空题 10 题 → 四、简答题 5 题
   - 每题独立编号、`<details>` 折叠答案
   - 顶部标注总分和考试时间

4. **生成方式**：用 `execute_code` + Python 写 HTML，因为文件较大（~20 KB），避免通过 `write_file` 放大 token 消耗。`execute_code` 中 `json.dump` → `Path.write_text` 即可。

5. **CSS 要求**：变量驱动（`:root`）、答案使用原生 `<details><summary>`（禁用 JS 实现折叠）、配色克制、响应式。

**生成顺序**（按用户价值从高到低）：
① `practice.html` — 教学复习页（含所有知识精讲+精选题），用户立即可开始系统学习
② `mock_exam.html` — 模拟卷（从精选题库中抽取编排），用户检测水平
③ `questions.html` — 题库清洗页（统一CSS、删废题、补答案、分章节），供查漏补缺
三页面必须统一 CSS 风格——以 `templates/practice.html` 的 `:root` 变量体系为基础。

**⚠️ 内容变更后必须全面审查（不可局部修改）**：当新增章节或知识点时，仅修改知识块是不够的。必须逐项检查并同步更新以下所有位置——

| 检查项 | practice.html | questions.html | mock_exam.html |
|--------|:--:|:--:|:--:|
| 复习路线 | ✅ | — | — |
| 优先级图谱(P0/P1/P2) | ✅ | — | — |
| 来源索引 | ✅ | — | — |
| 入口卡片统计 | ✅ | ✅ | — |
| 统计摘要(章节数/题数) | ✅ | — | — |
| 导航 pill 按钮 | ✅ | ✅ | ✅ |
| 时间段分配 | ✅ | — | ✅ |
| 新章节知识块 | ✅ | — | — |
| 新章节练习题 | ✅ | ✅ | ✅(融入试卷结构) |

**本次 session 踩过的坑**：老师补充了文件管理和中断/系统调用两章后，先只改了 practice.html 的知识块——复习路线、优先级图谱、来源索引、题库页、模拟卷全部遗漏。用户明确要求"整体审查，不要偷懒"。

### 处理老师提供的复习重点文件

当用户提供老师的复习重点文档（.docx/.pdf/图片等）时，这是高价值补充材料。处理流程：

1. **提取并对比**：读取文档内容，与已建立的课程知识体系逐模块对比，找出新增或差异内容。
2. **按老师重点修正**：将文档中的考点权重、易错点、定义表述同步到已有知识块。老师强调的考点即使课件未覆盖也要出题。
3. **新增章节**：如果文档列出了课件未覆盖但考试涉及的模块（如文件管理、中断），新建独立章节，包含完整知识块和练习题——与已有章节同等待遇。
4. **全局同步**：新增内容后执行上面的"全面审查清单"，确保复习路线、优先级图谱、模拟卷、题库页全部更新。

## 题源优先级

出题和判断重点时，按以下顺序优先取材，不可平均分配：

1. **历年真题** — 最高优先级（若有）
2. **老师 PPT** — 高优先级
3. **平时作业 / 阅读作业** — 中高优先级
4. **速成课题目** — 较低优先级
5. **AI 补充题** — 最低优先级，仅在材料不足时使用

速成课题目虽然出题优先级低，但在以下方面价值很高：提炼核心知识点、搭章节结构、总结重点和易错点、辅助判断复习顺序。

对于学习通课程，题源通常简化为：**老师PPT > 平时作业 > AI补充**（若无法获取历年真题和速成课）。

## 材料处理流程

### 第一步：先明确考试长相

开始深入复习前，优先确认这些信息：

- 考试有哪些题型
- 老师重点强调了什么
- 是否有常见证明题 / 简答题 / 客观题套路
- 用户要普通文本、HTML，还是两者都要

如果用户已经说过，不要重复问。如果用户没提、材料里也看不出来，默认按常见大学课程推测（选择、判断、填空、简答、计算/证明）。

### 第二步：读取材料

1. **先读 `generated/codex_context.md`** — 这是压缩后的课程 Markdown 和作业题汇总，用于快速建立全局认知。
2. 需要精确引用时，再深入 `materials_md/` 查看原始课件 Markdown。
3. 用 `assignments_md/*.questions.json` 推断真实作业题型和考点分布。
4. `output/final_review_seed.md` 仅供提示参考，不作为最终产出。
5. 如果材料很多，先列出所有 Markdown 文件的标题和大小，再按章节覆盖度、作业重叠度、高频术语、考试型概念筛选高信号文件。

### 第三步：清洗 Markdown

**清洗是必须步骤。复习材料不是档案存储。**

重点处理：
- 去乱码（OCR 残留、格式噪音）
- 去重复（同一概念多处出现时合并，保留最完整版本）
- 去低价值废话（过渡语、平台 UI 文字、无关导航等）
- 合并重复定义和结论
- 保留：定义、性质、定理、标准方法、常见考法、易错点

目标：**清晰、可背、可考**，不以"完整存档"为第一目标。

### 第四步：提取知识点

按章节组织知识点池。每个知识点标注：
- 来源材料（哪份 PPT / 哪章课件）
- 重要度评估（基于题源优先级和出现频次）
- 可能考法（选择题型匹配）

### 第五步：出题

**出题规则：**

- 先按真实考试题型出题，不做平均分配
- 每个知识点通常出 `1~6` 题
- 知识点越密、越高频、考法越多，题量越多
- 知识点越薄、越低频，题量越少
- 题型必须匹配知识点最可能考法：

| 知识点特征 | 匹配题型 |
|-----------|---------|
| 识记辨析型 | 选择、判断、填空 |
| 定义比较型 | 简答 |
| 推理证明构造型 | 主观题（证明/计算/分析） |

**题源标记：**

每道题保留轻量类别级题源标记，默认不要求追溯到文件级、页码级（除非用户明确要求）：

- `题源：历年真题`
- `题源：老师PPT`
- `题源：平时作业`
- `题源：速成课框架改编`
- `题源：AI补充`

## 解析规则

每道题解析都要服务考试拿分，不只是解释对错。尽量包含：

- 正确答案 / 参考答案
- 本题考哪个知识点
- 这类题的常见考法
- 考试时怎么拿分

### 客观题解析（选择、判断、填空）

重点补：
- **`这题核心知识点`** — 一句话说清考什么
- 关键定义 / 性质 / 判定依据
- **易混点** — 容易选错/判错的其他选项为何不对

### 主观题解析（简答、证明、分析、计算、构造）

重点补：
- **`这题答题核心点`** — 回答这道题最关键的一两句话
- **`必须出现`** — 哪些关键词、公式、步骤必须写，不写就扣分
- **`常见失分点`** — 哪里最容易出错、漏写、写反
- 偏得分导向的标准作答框架

## 得分导向提示

优先给可执行考试建议，不写鸡汤废话。例如：

- 看到判断题，先看边界条件和反例
- 看到简答题，先写定义，再写性质，再写结论
- 看到证明题，先写已知、求证和入口定理 / 定义
- 不会完整作答时，先写核心定义、关键性质、结论，先抢步骤分

## 输出规则

### HTML 输出（默认）

- 答案放在题目下方
- **答案默认折叠隐藏**，用户点击后再展开
- 保持静态本地页面，inline CSS/JS，不依赖服务器
- UI 风格：克制、学习导向，有清晰导航、可搜索、可折叠答案、可本地标记错题

### 普通文本输出

- 前面只放题目
- 后面统一放答案和解析
- 题目区和答案区要明确分开

## 最终页面结构（永久模板）

最终产出是一个多页面静态复习包，三个页面的分工和内容如下：

### 1. `output/practice.html` — 教学复习页（主入口）

定位：**教学为主，适合系统学习**。结构从上到下：

```
① 顶部入口卡（双卡片）→ 链接至 questions.html 和 mock_exam.html
② 采集证据摘要（stats 数字卡片 + 题源构成说明）
③ P0/P1/P2 优先级图谱（三栏彩色卡片）
④ 章节快速导航（pill 按钮）
⑤ 考试得分提示（黄色提示框，按题型分条）
⑥ 8章内容 × 每章：
   - 章节标题 + 优先级标签（折叠式 chapter-header）
   - 知识点小节（knowledge block：概念定义 + 易错陷阱 .trap）
   - 嵌入式练习题（直接展示在本页，用 <details> 折叠答案）
  题量按章节优先级分级，不要局限在少量题目：
  P0（必会）= 20~30 题，P1（高频）= 15~20 题，P2（加分）= 10~15 题
  重要知识点（如信号量机制、死锁、分页）一个知识点就出 3~5 道不同角度题
  参考：离散数学课程 practice.html 中 Ch2 命题逻辑一章就有 39 道精选题
⑦ 推荐复习路线（有序列表 + 小时分配）
⑧ 来源索引（课件 MD 文件列表 + 作业 JSON 列表）
```

CSS 要求：变量驱动（`:root`）、克制配色、响应式、答案使用原生 `<details><summary>`（不得用 onclick/JS 实现折叠）。

**参考模板**：离散数学课程的 `practice.html` 是成熟的教学型复习页蓝本。
结构为：header → 入口双卡片 → 统计摘要 → 三栏优先级图谱 → 置顶导航 → 得分提示 →
每章（折叠区 + 多个知识块 + 30~39 道精选题）→ 复习路线 → 来源索引。
处理新课程时以此为结构模板，替换课程内容即可。

### 2. `output/questions.html` — 题库页

定位：**刷题为主，适合查漏补缺**。

- 按章节分区，每章标题显示题型分布统计
- 所有题目平铺展示，每题下方 `<details>` 折叠答案
- 题型覆盖：单选、判断、填空、简答（按课程实际调整）
- 题源标记（小标签）：老师PPT / 平时作业 / AI补充
- 顶部和底部均有返回链接

**⚠️ 题库页常见格式缺陷与清洗流程（强制性逐题审核）：**

**智能体必须把 `questions.html` 中的每一道题逐条过一遍**，不得抽样跳过。`generate_exam_pages.py` 产出的 `questions.html` 和 `question_bank.json` 必有格式问题：

| 缺陷 | 表现 | 修复方法 |
|------|------|---------|
| 答案缺失 | "参考答案待补充" | 从已知正确数据或 LLM 知识补全；判断题可用规则推断（含"所有/一定/必须"→错） |
| 选项JSON原样 | `{'label': 'A', 'text': '对'}` 直接显示 | 从 `item.options` 提取`label.text`格式化为"A. 对" |
| 题源标签重复 | "题源：题源：平时作业" | 去重前缀 |
| 题目内容混杂 | "周转时间="、"![](../assets/" 等课件原文 | 删除（非考试题，是爬虫误抓的课件内容） |
| 题型标记错误 | 在 `questions.html` 中全部显示为"[选择题]" | 从 `canonical_type` 字段取正确类型（判断题/填空题/选择题/主观题） |

**清洗后需重新生成 `questions.html`**：用 `execute_code` + Python 读取清洗后的 `question_bank.json`，按上述模板生成HTML。三页面（practice/questions/mock_exam）必须统一CSS风格——以 `templates/practice.html` 的 `:root` 变量体系为基础。

### 3. `output/mock_exam.html` — 模拟卷页

定位：**模拟考试，适合考前检验**。

- 默认结构（无老师模拟卷时）：
  一、单选题 30 题（每题 2 分 = 60 分）
  二、判断题 15 题（每题 1 分 = 15 分）
  三、填空题 10 题（每题 1.5 分 = 15 分）
  四、简答题 5 题（每题 2 分 = 10 分）
  总分 100 分，考试时间 120 分钟
- 每题覆盖不同知识点，同知识点不重复出题
- 每题格式：`题号. [题型]` + 题干 + 选项 + `<details>` 答案
- 每部分有答题说明（如"请选出每题的唯一正确选项"）
- 确保编号从 1 开始独立编号（不与题库页共享计数器）
- 老师模拟卷检测：如发现老师发布的模拟试卷材料，按其题型结构生成同型卷；否则用默认配比

### 4. `output/teacher_mock_analysis.html`

仅在识别到老师模拟卷时生成，包含题型分布、章节覆盖、逐题解析。无模拟卷时不生成。

## 产出文件约定

- `generated/question_bank.json` — 题库中间文件，字段包含题型、章节、题干、选项、答案、解析、题源、是否 AI 补充。
- `generated/teacher_mock_candidates.json` — 老师模拟卷候选和选择结果。
- `generated/mock_exam.json` — 模拟卷结构化数据。
- `output/practice.html` — 多页面复习包入口。
- `output/questions.html` — 题目页。
- `output/mock_exam.html` — 模拟卷。
- `output/teacher_mock_analysis.html` — 可选，老师模拟卷解析页。
- `output/practice.generated.html` — 脚本兜底页，保留不覆盖
- `output/final_review_seed.md` — 脚本整理的复习草稿，仅供参考

---

# 附录

## Review Context Contract

- Prefer `generated/codex_context.md` for the first reading pass because it compresses course Markdown and homework questions.
- Use `materials_md/` for source-level definitions, examples, chapter structure, and citations.
- Use `assignments_md/*.questions.json` to infer real homework patterns. DOM extraction is primary; text fallback is marked as `extraction_method: text_fallback`.
- Use `output/final_review_seed.md` only as a hint.
- If there are many documents, first list converted Markdown titles and sizes, then inspect high-signal files by chapter coverage, assignment overlap, repeated terms, and exam-style concepts.

## Course Directory Contract

- `raw/materials/`: downloaded PPT/PPTX/PDF/DOC/DOCX/image files.
- `raw/assignments_html/`: captured assignment pages.
- `assignments_md/`: assignment Markdown and question JSON.
- `materials_md/`: MinerU-converted course material Markdown.
- `assets/`: copied images and converted resources referenced by Markdown.
- `manifests/`: manifests, failure records, and logs.
- `generated/`: compact Codex context, `question_bank.json`, `mock_exam.json`, `teacher_mock_candidates.json`, optional generated concepts/practice JSON, and optional `notebooklm/` artifacts.
- `course.db`: SQLite index over sources, documents, questions, concepts, and practice items.
- `output/practice.generated.html`: script-generated fallback practice page.
- `output/practice.html`: stable entry for the multi-page review package.
- `output/questions.html`: chapter-limited question bank page.
- `output/mock_exam.html`: generated mock exam page.
- `output/teacher_mock_analysis.html`: optional teacher mock analysis page.
- `output/crawl_report.md`: detailed collection/conversion report to quote back to the user.

## 运行后同步规则

当仓库里存在 `AGENT.md`、`CLAUDE.md`，或用户提到同类长期规则文件时，运行本 skill 后应默认做一次同步检查。

同步目标：把应长期生效的高优先级规则同步到 `AGENT.md / CLAUDE.md`，让以后不显式触发 skill 时也按同一套复习方法工作。

至少同步这些内容：题源优先级、材料处理流程（先清洗再提知识点再出题）、清洗原则、出题规则、题源标记规则、题答分离规则、客观题/主观题解析规则、得分导向规则。

同步策略：优先更新不并存冲突规则、保留其他无关长期规则、写成长期默认行为。

## Packaging Notes

- This is a standalone project. All scripts are under `scripts/`.
- `SKILL.md` is the authoritative workflow definition.
- `AGENT.md` contains long-term default rules for AI agents working in this repo.
- Course data is stored under `data/courses/<course_slug>/` and excluded from version control.
- `templates/practice.html`, `templates/questions.html`, `templates/mock_exam.html` — 参考模板，展示标准 HTML 结构和 CSS。生成新课程复习页时以此为准。
- `references/os-review-knowledge-bank.md` — 操作系统原理课程的知识点速查和题库生成参考。处理类似 OS 课程时可复用其中考点结构和解析格式。
