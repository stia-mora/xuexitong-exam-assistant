# Question Bank Data Cleaning

When `generate_exam_pages.py` writes `question_bank.json` from `course.db`, the output may have data quality issues beyond the explanation weakness covered in SKILL.md. This reference documents the cleaning patterns.

## Common Data Issues

### 1. Options in Raw Dict Format

The script's `options` field may contain dicts with `label`/`text`/`images` keys instead of flat strings:

```json
{"label": "A", "text": "对", "images": []}
```

**Fix:** Convert to `"A. 对"` format before rendering HTML:

```python
def extract_options(item):
    opts = item.get('options', [])
    result = []
    for o in opts:
        if isinstance(o, dict):
            label = o.get('label', '')
            text = o.get('text', '')
            if label and text: result.append(f'{label}. {text}')
        elif isinstance(o, str): result.append(o)
    return result
```

### 2. Missing Answers ("参考答案待补充")

Common in Ch3-5 where assignments had no answer data. The script emits the literal string `"参考答案待补充"` or `"正确答案："`.

**Fix (two-pass):**

**Pass 1** — Harvest answers from items that DO have them into a knowledge base:

```python
known_answers = {}
for item in items:
    ans = (item.get('answer', '') or '').strip()
    if ans and ans not in ('参考答案待补充', '正确答案：'):
        clean = clean_stem(item.get('question', ''))
        known_answers[clean] = ans
```

**Pass 2** — Fill missing answers by stem matching + manual supplement:

```python
manual_answers = {
    '对批处理作业，必须提供相应的作业控制信息': '对',
    '死锁是指系统中所有进程都处于阻塞状态': '错',
    # ... extend for common Ch3-5 patterns
}
known_answers.update(manual_answers)

for item in items:
    if not item.get('answer') or item['answer'] in ('参考答案待补充', '正确答案：'):
        clean = clean_stem(item.get('question', ''))
        # Try exact match, then containment match
        if clean in known_answers:
            item['answer'] = known_answers[clean]
        else:
            for key, val in known_answers.items():
                if len(key) > 15 and (key in clean or clean in key):
                    item['answer'] = val
                    break
```

**Judgment question inference fallback:** For 判断题 still without answers, use heuristics — absolute language ("所有", "一定", "必须") often signals 错:

```python
if item.get('canonical_type') == '判断题':
    if any(kw in clean for kw in ['所有', '一定', '必须', '只能', '都是']):
        item['answer'] = '错'
```

### 3. Double Source Label Prefix

Some items have `"题源：题源：平时作业"` — the source_label already contains the prefix.

```python
src = item.get('source_label', '')
if src.startswith('题源：题源：'):
    item['source_label'] = src[3:]  # Strip one prefix
```

### 4. Question Stem Contains Non-Question Content

Filter out items where the question field contains PPT slide content (image refs, calculation steps, teaching notes):

```python
def is_bad(item):
    q = item.get('question', '')
    if '![](../assets/' in q: return True
    if len(q) > 500: return True
    if any(kw in q for kw in [
        '是否会导致', '优点和缺点', '虽然严格来说',
        '注意：本例', '周转时间=', 'Job First)',
        '因此，调度顺序', '带权周转时间', '平均周转时间',
        '抢占式？非抢占式'
    ]): return True
    return False
```

### 5. Deduplication Within Chapters

Ch5 often has two identical test submissions. Dedup by cleaned stem (first 150-200 chars):

```python
seen = {}
deduped = []
for item in items:
    key = clean_stem(item.get('question', ''))[:200]
    if key not in seen:
        seen[key] = True
        deduped.append(item)
```

### 6. Chapter Classification for "Other" Items

Items with chapter_title containing "未分章" or not matching known patterns need content-based classification:

```python
def assign_chapter(item):
    ct = item.get('chapter_title', '')
    q = item.get('question', '')
    if '第 1 章' in ct or '引论' in ct: return '第1章 操作系统引论'
    if '第 2 章' in ct: return '第2章 处理机管理'
    # ... etc
    
    # Fallback: keyword-based
    if any(kw in q for kw in ['死锁','调度','银行家','FCFS','SJF']):
        return '第3章 处理机调度与死锁'
    if any(kw in q for kw in ['存储','分页','分段','重定位','虚拟','页']):
        return '第4章 存储管理'
    if any(kw in q for kw in ['设备','通道','SPOOL','磁盘','缓冲']):
        return '第5章 设备管理'
    return '第2章 处理机管理'  # safest default
```

## Full Cleaning Pipeline Order

1. Filter out non-question content (`is_bad()`)
2. Fill missing answers (harvest + manual + inference)
3. Fix option formatting (`extract_options()`)
4. Fix source labels (strip double prefix)
5. Assign chapters (content-based fallback)
6. Deduplicate within chapters (stem key)
7. Filter out items still without answers
8. Sort by chapter order
9. Save to `generated/question_bank_clean.json`
10. Generate `questions.html` from clean data
