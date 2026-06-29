# practice.html 模板说明

此文件不是完整 HTML，而是结构骨架和关键 CSS 类说明。生成新课程时按此结构填充。

## 页面结构（从上到下）

```
① .ec (entry-cards) — 双卡片链接到 questions.html 和 mock_exam.html
② .summary — 学习路径摘要 (知识点数、审核题数、章节数)
③ .pm (priority-map) — .p.p0 / .p.p1 / .p.p2 三栏彩色优先级卡片
④ .nv (nav) — pill 按钮快速跳转各章节
⑤ .et (exam-tips) — 黄色提示框，考试得分技巧
⑥ N个 .ch 块 — 每章一个：
   ├─ .ch-h (clickable header) — 章节标题 + 优先级 badge
   └─ .ch-b (collapsible body)：
       ├─ 知识点学习区：
       │   ├─ 多个 .knowledge 卡片，来自 generated/knowledge_bank.json
       │   ├─ h4 — 知识点标题 + 老师重点/课件提炼来源
       │   ├─ p  — 学习目标、核心要点、公式/例子、考试提示
       │   └─ .trap — 易错陷阱 (⚠ 前缀)
       └─ .qs-sec (questions section)：
           ├─ h4 — "精选练习题"
           └─ 嵌入式题目 (render_q())
⑦ .route — 推荐复习路线 (ol + 小时分配)
⑧ .src — 来源索引
⑨ .footer
```

## 关键 CSS 类

- `.ch-h` — 章节头部，cursor:pointer，onclick 折叠/展开
- `.knowledge` — 知识点学习卡片，必须在精选练习之前渲染
- `.knowledge .trap` — 易错陷阱提示，红色文字+粉色背景，::before "⚠ 易错："
- `.et` — 考试得分提示，黄色背景 border: #ffe082
- `.p.p0` — P0 必会(红色 #dc3545)，.p.p1 (橙色 #fd7e14)，.p.p2 (蓝色 #0d6efd)
- `.ec a` — 入口卡片，网格布局，hover 上浮+阴影
- 答案使用原生 `<details class="qa"><summary>` — 不要 onclick
