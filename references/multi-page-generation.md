# HTML 页面生成参考

## 共享渲染函数

所有三个 HTML 页面必须使用同一套 `render_q()` 函数，不可各自手写。

```python
def render_q(q, n):
    p = []
    p.append('<div class="q"><div class="qn">' + str(n) + '. [' + q["type"] + ']</div>')
    p.append('<div class="qs">' + clean(q["stem"]) + '</div>')
    if q.get("options"):
        p.append('<div class="qo">')
        for i, opt in enumerate(q["options"]):
            t = fmt_opt(opt)
            if t: p.append('<span>' + chr(65+i) + '. ' + t + '</span>')
        p.append('</div>')
    a = clean(q.get("answer",""))
    an = q.get("analysis","")
    p.append('<details class="qa"><summary>查看答案与解析</summary>')
    if a: p.append('<div class="ans">答案：' + a + '</div>')
    if an: p.append('<div class="ana">' + an + '</div>')
    p.append('</details></div>')
    return "\n".join(p)
```

## CSS 变量规范

所有页面统一使用 `:root` 变量：

```css
:root {
  --bg: #f8f9fa;    /* 页面背景 */
  --c:  #fff;       /* 卡片背景 */
  --t:  #212529;    /* 正文颜色 */
  --m:  #6c757d;    /* 次要文字 */
  --b:  #dee2e6;    /* 边框 */
  --a:  #0d6efd;    /* 强调色(蓝色) */
  --s:  #198754;    /* 成功色(绿色,用于答案) */
}
```

## 答案折叠

禁止使用 `onclick` + JavaScript 切换。统一使用：

```html
<details class="qa">
  <summary>查看答案与解析</summary>
  <div class="ans">答案：C</div>
  <div class="ana">这题核心知识点：...</div>
</details>
```

## 垃圾过滤词表

学习通作业 JSON 中必须过滤的垃圾模式（检查 stem 和 options）：

```
作答记录, 提示 提示, 此附件仅支持打开, 知道了, 作业详情,
题量:, 满分:, 作答时间:, 智能分析, 未在报告中展开,
单选题（共, 判断题（共, 简答题（共, 论述题（共
```

## 生成顺序（严格）

1. 合并各章 JSON → `question_bank.json`
2. 从 `question_bank.json` 抽样 → `mock_exam.json`（必须先完成步骤1）
3. 从 `question_bank.json` + `mock_exam.json` → 渲染三个 HTML

## 模拟卷规范

- 结构固定：单选题(30) → 判断题(15) → 填空题(10) → 简答题(5)
- 编号从 1 开始独立计数，不与题库共享
- 数据源必须是 LLM 重建后的 question_bank.json
- 题型顺序不可调换（不要 选→填→判→主观）
