# 学习通期末复习助手 (xuexitong-exam-assistant)

把学习通/超星课程资料采集下来，按考试得分导向整理成知识点、题目和解析，生成可用的期末复习 HTML 页面包。

## 快速开始

### 环境准备（首次使用）

```powershell
# 在项目根目录执行
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_env.ps1
```

这会创建名为 `qimokaisi-exam` 的 conda 环境并安装所有依赖。

### 采集一门课程

```powershell
# 交互式选择课程（会在 Chrome 中打开学习通，你来选择课程）
conda run -n qimokaisi-exam python scripts/run_course_pipeline.py
```

采集完成后产出在 `data/courses/<课程名>/output/` 下，包含：
- `practice.html` — 教学复习页（主入口）
- `questions.html` — 完整题库
- `mock_exam.html` — 模拟卷

### 对已有课程重新出题

```powershell
# 建库 + 准备上下文
conda run -n qimokaisi-exam python scripts/build_course_db.py --course data/courses/<课程目录>
conda run -n qimokaisi-exam python scripts/prepare_review_context.py --course data/courses/<课程目录>

# 生成页面（⚠️ 会覆盖手写题库，先备份）
conda run -n qimokaisi-exam python scripts/generate_exam_pages.py --course data/courses/<课程目录>
```

## 工作流程

```
采集（Chrome 爬取课件+作业）
  → MinerU 转 Markdown
  → 建库（SQLite course.db）
  → 准备上下文（codex_context.md）
  → 出题建库（LLM 直接生成 150~200 题）
  → 生成三页面 HTML
```

### 关键原则

1. **LLM 题库兜底**：爬虫提取的 JSON 题目数据质量不可靠（答案大量缺失、题干含爬取残留），有效题占比 <50% 时必须放弃 JSON，直接用 LLM 知识重建题库。
2. **宁可 150 道精品题，不要 800 道垃圾题** — 每题必须有干净题干、正确答案、得分导向解析。
3. **题源优先级**：老师 PPT > 平时作业 > AI 补充。
4. **三页面分工**：
   - `practice.html` — 教学为主，每章知识精讲 + 精选题（P0 章节 20~30 题，P2 章节 10~15 题）
   - `questions.html` — 刷题为主，所有题目平铺展示、可搜索筛选
   - `mock_exam.html` — 模拟考试，30 单选 + 15 判断 + 10 填空 + 5 简答

## 目录结构

```
xuexitong-exam-assistant/
├── SKILL.md                  # Hermes Agent skill 定义（权威工作流）
├── AGENT.md                  # AI Agent 长期规则
├── README.md                 # 本文件
├── environment.yml           # conda 环境定义
├── LICENSE                   # MIT
├── scripts/                  # 核心脚本
│   ├── run_course_pipeline.py    # 主入口：一键采集全流程
│   ├── collect_course.py         # Chrome 爬虫
│   ├── convert_materials.py      # MinerU 转换 PPT/PDF→MD
│   ├── build_course_db.py        # 建 SQLite 课程库
│   ├── prepare_review_context.py # 准备复习上下文
│   ├── generate_exam_pages.py    # 生成三页面 HTML
│   ├── bootstrap_env.ps1         # 环境安装脚本
│   └── ...
├── templates/                # HTML 参考模板
│   ├── practice.html             # 教学复习页模板
│   ├── questions.html            # 题库页模板
│   └── mock_exam.html            # 模拟卷模板
├── references/               # 参考文档
│   ├── data-cleaning.md          # 数据清洗流程
│   ├── multi-page-generation.md  # 多页面生成说明
│   ├── os-review-knowledge-bank.md  # 操作系统知识点速查
│   └── practice-template.md      # 复习页模板说明
└── data/                     # 课程数据（gitignore）
    ├── courses/<课程名>/         # 每门课独立目录
    │   ├── raw/                  # 原始下载文件
    │   ├── materials_md/         # MinerU 转换后的 MD
    │   ├── assignments_md/       # 作业 MD + JSON
    │   ├── generated/            # 题库等中间产物
    │   ├── output/               # 最终 HTML 页面
    │   └── course.db             # SQLite 课程库
    └── browser-profile/          # Chrome 专用 profile
```

## 安装为 Hermes Agent Skill

在 Hermes Agent 中安装此 skill：

```bash
hermes skills install --path /path/to/xuexitong-exam-assistant
```

安装后 Hermes 会自动加载 `SKILL.md` 中的工作流规则，处理学习通课程复习任务。

## 常见问题

### Q: generate_exam_pages.py 产出的页面质量不好？
这是已知问题。脚本从 `course.db` 提取题目时可能把课件原文当作题目（mock_exam.html 含整段 PPT 文字）、practice.html 仅为导航页、解析为"资料未提供完整解析"。按 SKILL.md 中"手动生成页面"节的指导重新生成。

### Q: 题库解析太弱？
参考 `SKILL.md` 中"题库页常见格式缺陷与清洗流程"和"LLM 题库兜底规则"。核心流程：评估 JSON 质量 → 有效题 <50% 则放弃 → LLM 直出 150~200 题 → 手动生成 HTML。

### Q: 如何支持新的课程？
运行 `run_course_pipeline.py`，在 Chrome 中选择目标课程即可。Ch7（文件管理）和 Ch8（中断与系统调用）如果课件未覆盖，按老师重点手动补充。

## 许可证

MIT
