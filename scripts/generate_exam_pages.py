"""Build the multi-page static final-review package.

The crawler and DB builder collect course facts.  This script turns those facts
into study-facing artifacts:

- generated/question_bank.json
- generated/teacher_mock_candidates.json
- generated/mock_exam.json
- output/practice.html
- output/questions.html
- output/mock_exam.html
- output/teacher_mock_analysis.html, only when a teacher mock paper is selected

It does not call remote AI services and it does not try to reveal hidden answers.
When answers are unavailable in the collected material, generated explanations
are marked as inferred/review prompts instead of presented as platform answers.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, chapter_sort_key, infer_chapter


QUESTIONY_SOURCE_RE = re.compile(
    r"(题库|习题|练习|测试|测验|作业|试卷|模拟|样卷|真题|考试|quiz|exam|mock|sample)",
    re.I,
)
TEACHER_MOCK_RE = re.compile(r"(模拟卷|样卷|期末试卷|考试卷|mock|sample\s*exam)", re.I)
TYPE_RE = re.compile(r"(单选题|多选题|选择题|判断题|填空题|简答题|论述题|问答题|材料题|计算题|证明题|主观题)")
NUMBERED_QUESTION_RE = re.compile(
    r"^\s*(?:第\s*)?(?P<num>\d{1,3}|[一二三四五六七八九十百]{1,5})\s*(?:题|[.、．)])\s*(?P<body>.+)?$"
)
TYPED_QUESTION_RE = re.compile(
    r"^\s*[【\[]?\s*(?P<type>单选题|多选题|选择题|判断题|填空题|简答题|论述题|问答题|材料题|计算题|证明题|主观题)\s*[】\]]?\s*(?P<body>.+)?$"
)
OPTION_RE = re.compile(r"^\s*(?P<label>[A-HＡ-Ｈ])\s*[.、．)]\s*(?P<body>.+)$", re.I)
ANSWER_RE = re.compile(r"(?:正确答案|参考答案|答案|我的答案)\s*[:：]\s*(?P<body>.+)")
EXPLANATION_RE = re.compile(r"(?:解析|答案解析|讲解|分析)\s*[:：]\s*(?P<body>.+)")
HEADING_RE = re.compile(r"^#{1,4}\s+(.+)")

DEFAULT_MOCK_BLUEPRINT = {"选择题": 30, "填空题": 10, "判断题": 15, "主观题": 5}
TYPE_ORDER = ["选择题", "填空题", "判断题", "主观题"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def read_text(path: Path, limit: int = 0) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] if limit and len(text) > limit else text


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def rel(path: Path | str, base: Path) -> str:
    p = Path(path)
    try:
        if not p.is_absolute():
            p = base / p
        return p.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def first_heading(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)", line.strip())
        if match:
            return clean_text(match.group(1))[:200]
    return fallback


def extract_headings(markdown: str, limit: int = 80) -> list[str]:
    headings: list[str] = []
    for line in markdown.splitlines():
        match = HEADING_RE.match(line.strip())
        if not match:
            continue
        title = clean_text(re.sub(r"[`*_#]", "", match.group(1)))
        if len(title) < 2 or title.lower().startswith("source file"):
            continue
        if TYPE_RE.fullmatch(title):
            continue
        if title not in headings:
            headings.append(title[:160])
        if len(headings) >= limit:
            break
    return headings


def normalize_option_label(label: str) -> str:
    table = str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH")
    return (label or "").upper().translate(table).strip()


def canonical_type(value: str, question: str = "", options: list[dict[str, Any]] | None = None) -> str:
    text = clean_text(value)
    haystack = f"{text} {question}"
    if any(token in haystack for token in ("单选", "多选", "选择")) or options:
        return "选择题"
    if "判断" in haystack:
        return "判断题"
    if "填空" in haystack or "____" in question or "（ ）" in question or "( )" in question:
        return "填空题"
    if any(token in haystack for token in ("简答", "论述", "问答", "材料", "计算", "证明", "主观")):
        return "主观题"
    return "主观题"


def display_type(value: str, question: str = "", options: list[dict[str, Any]] | None = None) -> str:
    text = clean_text(value)
    if text and text.lower() != "unknown":
        return text
    return canonical_type(text, question=question, options=options)


def source_priority(label: str, ai_generated: bool) -> int:
    if ai_generated:
        return 90
    if "老师模拟卷" in label or "历年真题" in label:
        return 0
    if "老师PPT" in label:
        return 10
    if "平时作业" in label:
        return 20
    return 50


def is_question_like(body: str, current_type: str = "") -> bool:
    text = clean_text(body)
    if not text:
        return False
    if current_type:
        return True
    cues = ("？", "?", "（", "(", "____", "下列", "哪", "是否", "判断", "简述", "说明", "计算", "证明")
    return any(cue in text for cue in cues)


def normalize_options(options: Any) -> list[dict[str, Any]]:
    if not options:
        return []
    if isinstance(options, dict):
        iterable = [{"label": key, "text": value} for key, value in options.items()]
    else:
        iterable = options
    normalized: list[dict[str, Any]] = []
    for index, option in enumerate(iterable, start=1):
        if isinstance(option, dict):
            label = normalize_option_label(str(option.get("label") or chr(64 + index)))
            text = clean_text(option.get("text") or option.get("content") or "")
            images = option.get("images") if isinstance(option.get("images"), list) else []
        else:
            label = chr(64 + index)
            text = clean_text(option)
            images = []
        if text or images:
            normalized.append({"label": label, "text": text, "images": images})
    return normalized


def fallback_explanation(question_type: str, chapter_title: str, ai_inferred: bool = False) -> str:
    prefix = "资料未提供完整解析，以下为复习导向提示：" if ai_inferred else "解析："
    canonical = canonical_type(question_type)
    if canonical == "选择题":
        return f"{prefix}先定位本题对应的概念边界，再比较选项中的关键词、适用条件和例外情况。"
    if canonical == "判断题":
        return f"{prefix}判断题优先检查绝对化表述、适用条件和反例；必要时回到 {chapter_title} 的定义。"
    if canonical == "填空题":
        return f"{prefix}填空题通常考标准术语，复习时要背准确关键词，避免只写近义描述。"
    return f"{prefix}主观题按“定义/原理 -> 条件/步骤 -> 结论/得分点”组织答案，并补充常见失分点。"


def make_question_record(
    *,
    course_dir: Path,
    question: str,
    q_type: str,
    chapter: dict[str, Any],
    options: Any = None,
    answer: str = "",
    explanation: str = "",
    source_label: str,
    source_refs: list[str] | None = None,
    source_kind: str = "",
    ai_generated: bool = False,
    teacher_mock: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_options = normalize_options(options)
    q_text = clean_text(question)
    display = display_type(q_type, question=q_text, options=normalized_options)
    canonical = canonical_type(display, question=q_text, options=normalized_options)
    refs = [ref for ref in (source_refs or []) if ref]
    if not answer:
        answer = "参考答案待补充" if not ai_generated else "参考答案见解析"
    if not explanation:
        explanation = fallback_explanation(display, chapter.get("title") or UNKNOWN_CHAPTER, ai_inferred=not ai_generated)
    record_id = sha1_text("\n".join([q_text, display, "|".join(refs), source_label]))[:24]
    data = {
        "id": record_id,
        "type": display,
        "canonical_type": canonical,
        "chapter": chapter,
        "chapter_title": chapter.get("title") or UNKNOWN_CHAPTER,
        "question": q_text,
        "options": normalized_options,
        "answer": clean_text(answer),
        "explanation": clean_text(explanation),
        "source_label": source_label,
        "source_refs": refs,
        "source_kind": source_kind,
        "ai_generated": bool(ai_generated),
        "teacher_mock": bool(teacher_mock),
        "metadata": metadata or {},
    }
    data["source_priority"] = source_priority(source_label, bool(ai_generated))
    return data


def load_course_meta(course_dir: Path) -> dict[str, Any]:
    meta = read_json(course_dir / "course_meta.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("course_title", course_dir.name)
    meta.setdefault("course_slug", course_dir.name)
    return meta


def load_assignment_questions(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "assignments_md").glob("*.questions.json"), key=lambda p: p.as_posix().casefold()):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        title = clean_text(payload.get("title") or path.stem)
        markdown_path = path.with_name(path.name.replace(".questions.json", ".md"))
        chapter = infer_chapter(title=title, path=rel(markdown_path, course_dir), headings=[])
        for item in payload.get("questions", []) or []:
            if not isinstance(item, dict):
                continue
            q_text = clean_text(item.get("question") or "")
            if not q_text:
                continue
            rows.append(
                make_question_record(
                    course_dir=course_dir,
                    question=q_text,
                    q_type=item.get("type") or "unknown",
                    chapter=chapter,
                    options=item.get("options") or [],
                    answer=item.get("answer") or "",
                    explanation=item.get("explanation") or "",
                    source_label="题源：平时作业",
                    source_refs=[rel(path, course_dir)],
                    source_kind="assignment",
                    metadata={
                        "assignment_title": title,
                        "number": item.get("number"),
                        "extraction_method": item.get("extraction_method") or payload.get("extraction_method") or "",
                    },
                )
            )
    return rows


def extract_questions_from_markdown_source(
    course_dir: Path,
    path: Path,
    markdown: str,
    *,
    source_label: str,
    teacher_mock: bool = False,
) -> list[dict[str, Any]]:
    rel_path = rel(path, course_dir)
    title = first_heading(markdown, path.stem)
    headings = extract_headings(markdown)
    chapter = infer_chapter(title=title, path=rel_path, headings=headings)
    questiony_source = bool(QUESTIONY_SOURCE_RE.search(f"{path.name} {title}"))
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_type = ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        q_text = clean_text(current.get("question") or "")
        options = normalize_options(current.get("options") or [])
        if not q_text or (not questiony_source and not current.get("typed_start")):
            current = None
            return
        if not is_question_like(q_text, current.get("type") or "") and not options:
            current = None
            return
        records.append(
            make_question_record(
                course_dir=course_dir,
                question=q_text,
                q_type=current.get("type") or current_type or "unknown",
                chapter=chapter,
                options=options,
                answer=current.get("answer") or "",
                explanation=current.get("explanation") or "",
                source_label=source_label,
                source_refs=[rel_path],
                source_kind="material",
                teacher_mock=teacher_mock,
                metadata={"material_title": title, "line": current.get("line", 0)},
            )
        )
        current = None

    for line_no, raw in enumerate(markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        line = clean_text(re.sub(r"^[>*\-]\s*", "", raw))
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            heading_text = clean_text(heading.group(1))
            type_match = TYPE_RE.search(heading_text)
            if type_match:
                current_type = type_match.group(1)
            continue
        type_match = TYPE_RE.fullmatch(line)
        if type_match:
            current_type = type_match.group(1)
            continue

        answer_match = ANSWER_RE.search(line)
        explanation_match = EXPLANATION_RE.search(line)
        option_match = OPTION_RE.match(line)
        typed_match = TYPED_QUESTION_RE.match(line)
        numbered_match = NUMBERED_QUESTION_RE.match(line)

        if answer_match and current is not None:
            current["answer"] = answer_match.group("body").strip()
            continue
        if explanation_match and current is not None:
            current["explanation"] = explanation_match.group("body").strip()
            continue
        if option_match and current is not None:
            current.setdefault("options", []).append(
                {"label": normalize_option_label(option_match.group("label")), "text": clean_text(option_match.group("body"))}
            )
            continue

        starts_question = False
        q_type = current_type
        body = ""
        typed_start = False
        if typed_match:
            q_type = typed_match.group("type")
            body = clean_text(typed_match.group("body") or "")
            starts_question = bool(body) or questiony_source
            typed_start = True
        elif numbered_match:
            body = clean_text(numbered_match.group("body") or "")
            starts_question = questiony_source and is_question_like(body, current_type)

        if starts_question:
            flush()
            current = {"type": q_type or "unknown", "question": body, "options": [], "line": line_no, "typed_start": typed_start}
            continue

        if current is not None and len(line) < 240 and not line.startswith("#"):
            current["question"] = clean_text(f"{current.get('question', '')} {line}")

    flush()
    return dedupe_records(records)


def load_material_questions(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "materials_md").glob("*.md"), key=lambda p: p.as_posix().casefold()):
        markdown = read_text(path)
        title = first_heading(markdown, path.stem)
        is_mock = bool(TEACHER_MOCK_RE.search(f"{path.name} {title}"))
        label = "题源：老师模拟卷" if is_mock else "题源：老师PPT"
        rows.extend(extract_questions_from_markdown_source(course_dir, path, markdown, source_label=label, teacher_mock=is_mock))
    return rows


def collect_concepts(course_dir: Path) -> dict[str, list[dict[str, Any]]]:
    concepts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((course_dir / "materials_md").glob("*.md"), key=lambda p: p.as_posix().casefold()):
        markdown = read_text(path)
        title = first_heading(markdown, path.stem)
        headings = extract_headings(markdown, limit=60)
        chapter = infer_chapter(title=title, path=rel(path, course_dir), headings=headings)
        chapter_key = chapter.get("key") or "chapter-unknown"
        candidates = headings or [title]
        for heading in candidates:
            text = clean_text(heading)
            if len(text) < 2 or TYPE_RE.search(text):
                continue
            item = {"title": text, "chapter": chapter, "source_refs": [rel(path, course_dir)]}
            if item not in concepts[chapter_key]:
                concepts[chapter_key].append(item)
    if not concepts:
        chapter = infer_chapter()
        concepts[chapter.get("key") or "chapter-unknown"].append(
            {"title": "课程综合复习", "chapter": chapter, "source_refs": []}
        )
    return concepts


def make_ai_question(concept: dict[str, Any], q_type: str, seed: int) -> dict[str, Any]:
    title = concept.get("title") or "课程核心概念"
    chapter = concept.get("chapter") or infer_chapter()
    refs = concept.get("source_refs") or []
    if q_type == "选择题":
        question = f"关于“{title}”，复习时最应该优先掌握的是哪一项？"
        options = [
            {"label": "A", "text": "核心定义、适用条件、常见考法和易错边界"},
            {"label": "B", "text": "只记住标题，不需要理解条件"},
            {"label": "C", "text": "只背一个孤立例子即可"},
            {"label": "D", "text": "完全依赖考场临场发挥"},
        ]
        answer = "A"
        explanation = f"AI补充题。围绕“{title}”复习时，要把定义、条件、考法和失分点连在一起，选择题常用边界词设置干扰。"
    elif q_type == "判断题":
        question = f"判断：复习“{title}”时，只记结论、不写适用条件，也能稳定拿到主观题步骤分。"
        options = []
        answer = "错"
        explanation = f"AI补充题。多数课程的得分点包括条件、依据和步骤，只写结论容易丢分。"
    elif q_type == "填空题":
        question = f"“{title}”的复习答案框架通常应包含核心定义、____和常见失分点。"
        options = []
        answer = "适用条件/关键步骤/判定依据"
        explanation = f"AI补充题。填空题优先记标准术语；若课程材料有固定表述，以材料原文为准。"
    else:
        question = f"简述“{title}”的核心含义、常见考法和答题得分点。"
        options = []
        answer = f"参考框架：先写“{title}”的定义或核心原理，再写适用条件/关键步骤，最后补充常见考法和易错点。"
        explanation = "AI补充题。主观题按“定义/原理 -> 条件/步骤 -> 结论/失分点”组织，能优先拿步骤分。"
    return make_question_record(
        course_dir=Path("."),
        question=question,
        q_type=q_type,
        chapter=chapter,
        options=options,
        answer=answer,
        explanation=explanation,
        source_label="题源：AI补充",
        source_refs=refs,
        source_kind="ai_fill",
        ai_generated=True,
        metadata={"concept": title, "seed": seed},
    )


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in records:
        key = sha1_text(clean_text(item.get("question") or "").casefold())[:24]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_question_bank(course_dir: Path, questions_per_chapter: int, ai_fill_min_per_chapter: int) -> list[dict[str, Any]]:
    raw_items = load_assignment_questions(course_dir) + load_material_questions(course_dir)
    raw_items = dedupe_records(raw_items)
    concepts = collect_concepts(course_dir)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw_items:
        chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else infer_chapter()
        grouped[chapter.get("key") or "chapter-unknown"].append(item)

    for chapter_key, rows in list(grouped.items()):
        if chapter_key in concepts or not rows:
            continue
        chapter = rows[0].get("chapter") if isinstance(rows[0].get("chapter"), dict) else infer_chapter()
        concepts[chapter_key].append(
            {
                "title": chapter.get("title") or "课程综合复习",
                "chapter": chapter,
                "source_refs": rows[0].get("source_refs") or [],
            }
        )

    for chapter_key, concept_rows in concepts.items():
        current_count = len(grouped.get(chapter_key, []))
        target = min(max(ai_fill_min_per_chapter, 0), max(questions_per_chapter, 0))
        if current_count >= target:
            continue
        cycle = ["选择题", "判断题", "填空题", "主观题"]
        seed = 0
        while len(grouped[chapter_key]) < target:
            concept = concept_rows[seed % len(concept_rows)]
            q_type = cycle[seed % len(cycle)]
            grouped[chapter_key].append(make_ai_question(concept, q_type, seed))
            seed += 1

    limited: list[dict[str, Any]] = []
    all_keys = set(grouped) | set(concepts)
    for chapter_key in sorted(all_keys, key=lambda key: chapter_sort_key((grouped.get(key) or [concepts.get(key, [{}])[0]] or [{}])[0].get("chapter"))):
        rows = grouped.get(chapter_key, [])
        rows.sort(key=lambda item: (item.get("source_priority", 50), item.get("ai_generated", False), item.get("canonical_type", ""), item.get("id", "")))
        limited.extend(rows[:questions_per_chapter] if questions_per_chapter > 0 else rows)

    for index, item in enumerate(limited, start=1):
        item["bank_index"] = index
    return limited


def material_mock_candidates(course_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted((course_dir / "materials_md").glob("*.md"), key=lambda p: p.as_posix().casefold()):
        markdown = read_text(path)
        title = first_heading(markdown, path.stem)
        text = f"{path.name} {title}"
        if not TEACHER_MOCK_RE.search(text):
            continue
        questions = extract_questions_from_markdown_source(
            course_dir,
            path,
            markdown,
            source_label="题源：老师模拟卷",
            teacher_mock=True,
        )
        type_counts = Counter(item.get("canonical_type") or canonical_type(item.get("type", "")) for item in questions)
        candidates.append(
            {
                "path": rel(path, course_dir),
                "title": title,
                "question_count": len(questions),
                "type_counts": dict(type_counts),
                "keyword_matched": True,
                "selected": False,
                "selection_reason": "",
                "questions": questions,
            }
        )
    return candidates


def resolve_teacher_mock(course_dir: Path, candidates: list[dict[str, Any]], selector: str) -> dict[str, Any] | None:
    if not selector:
        viable = [item for item in candidates if item.get("question_count", 0) > 0]
        if len(viable) == 1:
            viable[0]["selected"] = True
            viable[0]["selection_reason"] = "auto_selected_single_candidate"
            return viable[0]
        return None

    wanted = selector.strip().replace("\\", "/").casefold()
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        path = str(candidate.get("path") or "").casefold()
        stem = Path(path).stem.casefold()
        title = str(candidate.get("title") or "").casefold()
        if wanted in {path, stem, title} or wanted in path or wanted in title:
            matches.append(candidate)
    if matches:
        matches.sort(key=lambda item: (str(item.get("path", "")).casefold()))
        matches[0]["selected"] = True
        matches[0]["selection_reason"] = "manual_selector"
        return matches[0]

    manual_path = Path(selector)
    if not manual_path.is_absolute():
        manual_path = course_dir / selector
    if manual_path.exists():
        markdown = read_text(manual_path)
        title = first_heading(markdown, manual_path.stem)
        questions = extract_questions_from_markdown_source(
            course_dir,
            manual_path,
            markdown,
            source_label="题源：老师模拟卷",
            teacher_mock=True,
        )
        candidate = {
            "path": rel(manual_path, course_dir),
            "title": title,
            "question_count": len(questions),
            "type_counts": dict(Counter(item.get("canonical_type") for item in questions)),
            "keyword_matched": False,
            "selected": True,
            "selection_reason": "manual_path",
            "questions": questions,
        }
        candidates.append(candidate)
        return candidate
    return None


def mock_blueprint(selected_teacher_mock: dict[str, Any] | None) -> dict[str, int]:
    if selected_teacher_mock:
        counts = selected_teacher_mock.get("type_counts") or {}
        blueprint = {q_type: int(counts.get(q_type, 0)) for q_type in TYPE_ORDER if int(counts.get(q_type, 0))}
        if blueprint:
            return blueprint
    return dict(DEFAULT_MOCK_BLUEPRINT)


def build_mock_exam(
    course_dir: Path,
    question_bank: list[dict[str, Any]],
    selected_teacher_mock: dict[str, Any] | None,
) -> dict[str, Any]:
    blueprint = mock_blueprint(selected_teacher_mock)
    concepts = [item for rows in collect_concepts(course_dir).values() for item in rows]
    excluded_refs = set()
    if selected_teacher_mock:
        excluded_refs.add(selected_teacher_mock.get("path") or "")

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in question_bank:
        refs = set(item.get("source_refs") or [])
        if refs & excluded_refs:
            continue
        by_type[item.get("canonical_type") or canonical_type(item.get("type", ""))].append(item)
    for rows in by_type.values():
        rows.sort(key=lambda item: (item.get("ai_generated", False), item.get("source_priority", 50), item.get("id", "")))

    selected: list[dict[str, Any]] = []
    for q_type in TYPE_ORDER:
        target = int(blueprint.get(q_type, 0))
        if target <= 0:
            continue
        pool = by_type.get(q_type, [])
        taken = 0
        for item in pool[:target]:
            copied = dict(item)
            copied["mock_id"] = f"M{len(selected) + 1:03d}"
            copied.setdefault("metadata", {})
            copied["metadata"] = dict(copied["metadata"], mock_basis="question_bank")
            selected.append(copied)
            taken += 1
        seed = 0
        while taken < target:
            concept = concepts[(len(selected) + seed) % len(concepts)] if concepts else {"title": "课程综合复习", "chapter": infer_chapter(), "source_refs": []}
            generated = make_ai_question(concept, q_type, seed + taken)
            generated["mock_id"] = f"M{len(selected) + 1:03d}"
            generated["metadata"] = dict(generated.get("metadata") or {}, mock_basis="ai_fill_for_mock")
            selected.append(generated)
            taken += 1
            seed += 1

    return {
        "schema_version": 1,
        "updated_at": utc_now(),
        "blueprint": blueprint,
        "source": "teacher_mock_structure" if selected_teacher_mock else "default_structure",
        "teacher_mock": {
            "path": selected_teacher_mock.get("path"),
            "title": selected_teacher_mock.get("title"),
        } if selected_teacher_mock else None,
        "items": selected,
    }


def load_db_counts(course_dir: Path) -> dict[str, int]:
    db_path = course_dir / "course.db"
    counts: dict[str, int] = {}
    if not db_path.exists():
        return counts
    conn = sqlite3.connect(db_path)
    try:
        for table in ("documents", "assignments", "questions", "concepts", "practice_items"):
            try:
                counts[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def render_options(options: list[dict[str, Any]]) -> str:
    if not options:
        return ""
    rows = ['<ol class="options">']
    for option in options:
        rows.append(
            f'<li><span class="option-label">{esc(option.get("label"))}</span><span>{esc(option.get("text"))}</span></li>'
        )
    rows.append("</ol>")
    return "\n".join(rows)


def render_sources(item: dict[str, Any]) -> str:
    refs = item.get("source_refs") or []
    chips = [f'<span>{esc(item.get("source_label") or "")}</span>']
    for ref in refs[:3]:
        chips.append(f"<code>{esc(ref)}</code>")
    if item.get("ai_generated"):
        chips.append("<span>AI补充</span>")
    return '<div class="sources">' + "".join(chips) + "</div>"


def render_question_card(item: dict[str, Any], index: int, prefix: str = "Q") -> str:
    answer = item.get("answer") or "参考答案待补充"
    explanation = item.get("explanation") or fallback_explanation(item.get("type", ""), item.get("chapter_title", UNKNOWN_CHAPTER), True)
    search = " ".join(
        [
            item.get("question") or "",
            item.get("answer") or "",
            item.get("explanation") or "",
            item.get("chapter_title") or "",
            item.get("source_label") or "",
            item.get("type") or "",
        ]
    )
    return f"""
<article class="question-card" data-type="{esc(item.get('canonical_type'))}" data-source="{esc(item.get('source_label'))}" data-search="{esc(search)}">
  <div class="q-topline">
    <span>{esc(prefix)}{index:03d}</span>
    <span>{esc(item.get('type') or item.get('canonical_type'))}</span>
  </div>
  <h3>{esc(item.get('question'))}</h3>
  {render_options(item.get('options') or [])}
  <details class="answer"><summary>查看答案与解析</summary>
    <p><strong>答案：</strong>{esc(answer)}</p>
    <p><strong>解析：</strong>{esc(explanation)}</p>
  </details>
  {render_sources(item)}
</article>
"""


def common_css() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f5f7f6;
  --panel: #ffffff;
  --ink: #1c2528;
  --muted: #607077;
  --line: #dce4e1;
  --accent: #176b63;
  --blue: #335f96;
  --amber: #a86416;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; line-height: 1.65; letter-spacing: 0; }
.shell { width: min(1160px, 100%); margin: 0 auto; padding: 24px 18px 60px; }
header { display: grid; gap: 12px; padding: 24px 0 18px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
h1 { margin: 0; font-size: 28px; line-height: 1.25; }
.subtle { color: var(--muted); margin: 0; }
.nav { display: flex; gap: 8px; flex-wrap: wrap; }
.nav a, .pill { display: inline-flex; align-items: center; min-height: 32px; padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); text-decoration: none; font-size: 13px; }
.nav a:hover, .nav a:focus { background: #eaf3ef; outline: none; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
.panel h2, section h2 { margin: 0 0 10px; font-size: 20px; line-height: 1.35; }
.stat strong { display: block; font-size: 26px; color: var(--accent); }
.controls { display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0; }
.search, select { min-height: 38px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); padding: 7px 10px; font: inherit; }
.search { min-width: min(460px, 100%); }
section { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--line); }
.chapter-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.question-list { display: grid; gap: 12px; }
.question-card { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 15px; }
.question-card.hidden { display: none; }
.q-topline { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 13px; margin-bottom: 8px; }
.question-card h3 { margin: 0 0 10px; font-size: 16px; line-height: 1.55; }
.options { display: grid; gap: 8px; list-style: none; padding: 0; margin: 0 0 10px; }
.options li { display: grid; grid-template-columns: 28px 1fr; gap: 8px; align-items: start; padding: 8px 10px; background: #f8faf8; border: 1px solid #e8eeeb; border-radius: 6px; }
.option-label { display: inline-grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; color: #fff; background: var(--blue); font-size: 12px; font-weight: 700; }
.answer { margin-top: 10px; padding: 10px 12px; background: #fff8ed; border-left: 3px solid var(--amber); border-radius: 6px; }
.answer summary { cursor: pointer; font-weight: 700; }
.answer p { margin: 8px 0 0; }
.sources { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; color: var(--muted); font-size: 12px; }
.sources span, .sources code { border: 1px solid var(--line); background: #f7f8f5; border-radius: 4px; padding: 2px 5px; overflow-wrap: anywhere; }
.empty { color: var(--muted); background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
@media (max-width: 680px) { .shell { padding: 18px 12px 48px; } h1 { font-size: 24px; } .chapter-heading { display: block; } }
"""


def common_script() -> str:
    return """
<script>
(() => {
  const search = document.querySelector('#search');
  const typeFilter = document.querySelector('#typeFilter');
  const sourceFilter = document.querySelector('#sourceFilter');
  const cards = Array.from(document.querySelectorAll('.question-card'));
  const sections = Array.from(document.querySelectorAll('section[data-section]'));
  const apply = () => {
    const q = (search && search.value || '').trim().toLowerCase();
    const type = typeFilter && typeFilter.value || '';
    const source = sourceFilter && sourceFilter.value || '';
    cards.forEach((card) => {
      const text = (card.dataset.search || '').toLowerCase();
      const okText = !q || text.includes(q);
      const okType = !type || card.dataset.type === type;
      const okSource = !source || card.dataset.source === source;
      card.classList.toggle('hidden', !(okText && okType && okSource));
    });
    sections.forEach((section) => {
      const visible = section.querySelectorAll('.question-card:not(.hidden)').length;
      section.classList.toggle('hidden', visible === 0);
    });
  };
  [search, typeFilter, sourceFilter].forEach((node) => node && node.addEventListener('input', apply));
  apply();
})();
</script>
"""


def nav_html(include_teacher: bool) -> str:
    links = [
        ('practice.html', '总入口'),
        ('questions.html', '题目页'),
        ('mock_exam.html', '模拟卷'),
    ]
    if include_teacher:
        links.append(('teacher_mock_analysis.html', '老师模拟卷解析'))
    links.append(('practice.generated.html', '脚本兜底页'))
    return '<nav class="nav">' + "".join(f'<a href="{href}">{label}</a>' for href, label in links) + "</nav>"


def render_questions_page(meta: dict[str, Any], question_bank: list[dict[str, Any]], include_teacher: bool) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapters: dict[str, dict[str, Any]] = {}
    for item in question_bank:
        chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else infer_chapter()
        key = chapter.get("key") or "chapter-unknown"
        grouped[key].append(item)
        chapters[key] = chapter
    type_options = sorted({item.get("canonical_type") or canonical_type(item.get("type", "")) for item in question_bank})
    source_options = sorted({item.get("source_label") or "" for item in question_bank if item.get("source_label")})
    sections: list[str] = []
    index = 1
    for key, chapter in sorted(chapters.items(), key=lambda item: chapter_sort_key(item[1])):
        rows = grouped.get(key, [])
        sections.append(f'<section data-section><div class="chapter-heading"><h2>{esc(chapter.get("title") or UNKNOWN_CHAPTER)}</h2><span class="pill">{len(rows)} 题</span></div><div class="question-list">')
        for item in rows:
            sections.append(render_question_card(item, index))
            index += 1
        sections.append("</div></section>")
    if not sections:
        sections.append('<p class="empty">暂未生成题库。请先运行课程采集、转换和题库生成流程。</p>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(meta.get('course_title'))} - 题目页</title>
  <style>{common_css()}</style>
</head>
<body>
<div class="shell">
  <header>
    <h1>{esc(meta.get('course_title'))} 题目页</h1>
    <p class="subtle">每章最多保留设定数量的题目，原题优先；资料不足时使用 AI 补充题并明确标记。</p>
    {nav_html(include_teacher)}
  </header>
  <div class="controls">
    <input id="search" class="search" type="search" placeholder="搜索题干、答案、解析、来源">
    <select id="typeFilter"><option value="">全部题型</option>{''.join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in type_options)}</select>
    <select id="sourceFilter"><option value="">全部题源</option>{''.join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in source_options)}</select>
  </div>
  {''.join(sections)}
</div>
{common_script()}
</body>
</html>
"""


def render_mock_exam_page(meta: dict[str, Any], mock_exam: dict[str, Any], include_teacher: bool) -> str:
    rows = mock_exam.get("items") or []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        grouped[item.get("canonical_type") or canonical_type(item.get("type", ""))].append(item)
    sections: list[str] = []
    index = 1
    for q_type in TYPE_ORDER:
        items = grouped.get(q_type, [])
        if not items:
            continue
        sections.append(f'<section data-section><div class="chapter-heading"><h2>{esc(q_type)}</h2><span class="pill">{len(items)} 题</span></div><div class="question-list">')
        for item in items:
            sections.append(render_question_card(item, index, prefix="M"))
            index += 1
        sections.append("</div></section>")
    blueprint_text = "，".join(f"{key}{value}题" for key, value in (mock_exam.get("blueprint") or {}).items())
    source_text = "按老师模拟卷题型结构生成" if mock_exam.get("source") == "teacher_mock_structure" else "未识别老师模拟卷，按默认 30/10/15/5 结构生成"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(meta.get('course_title'))} - 模拟卷</title>
  <style>{common_css()}</style>
</head>
<body>
<div class="shell">
  <header>
    <h1>{esc(meta.get('course_title'))} 模拟卷</h1>
    <p class="subtle">{esc(source_text)}。结构：{esc(blueprint_text)}</p>
    {nav_html(include_teacher)}
  </header>
  <div class="controls">
    <input id="search" class="search" type="search" placeholder="搜索题干、答案、解析、来源">
    <select id="typeFilter"><option value="">全部题型</option>{''.join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in TYPE_ORDER if grouped.get(t))}</select>
    <select id="sourceFilter"><option value="">全部题源</option>{''.join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in sorted({item.get('source_label') or '' for item in rows if item.get('source_label')}))}</select>
  </div>
  {''.join(sections) if sections else '<p class="empty">暂未生成模拟卷。</p>'}
</div>
{common_script()}
</body>
</html>
"""


def render_teacher_mock_analysis_page(meta: dict[str, Any], teacher_mock: dict[str, Any]) -> str:
    questions = teacher_mock.get("questions") or []
    type_counts = Counter(item.get("canonical_type") or canonical_type(item.get("type", "")) for item in questions)
    chapter_counts = Counter(item.get("chapter_title") or UNKNOWN_CHAPTER for item in questions)
    cards = "".join(render_question_card(item, index, prefix="T") for index, item in enumerate(questions, start=1))
    type_summary = "".join(f'<div class="panel stat"><strong>{count}</strong>{esc(q_type)}</div>' for q_type, count in type_counts.items())
    chapter_summary = "".join(f'<li>{esc(title)}：{count} 题</li>' for title, count in chapter_counts.most_common())
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(meta.get('course_title'))} - 老师模拟卷解析</title>
  <style>{common_css()}</style>
</head>
<body>
<div class="shell">
  <header>
    <h1>{esc(meta.get('course_title'))} 老师模拟卷解析</h1>
    <p class="subtle">资料：{esc(teacher_mock.get('title'))}；路径：{esc(teacher_mock.get('path'))}</p>
    {nav_html(True)}
  </header>
  <div class="grid">{type_summary or '<div class="panel">未识别到题型分布。</div>'}</div>
  <section>
    <h2>考点覆盖</h2>
    <div class="panel"><ul>{chapter_summary or '<li>暂未识别章节覆盖。</li>'}</ul></div>
  </section>
  <section data-section>
    <div class="chapter-heading"><h2>逐题解析</h2><span class="pill">{len(questions)} 题</span></div>
    <div class="question-list">{cards or '<p class="empty">该模拟卷候选暂未抽取到题目。</p>'}</div>
  </section>
</div>
</body>
</html>
"""


def render_index_page(
    meta: dict[str, Any],
    counts: dict[str, int],
    question_bank: list[dict[str, Any]],
    mock_exam: dict[str, Any],
    teacher_mock: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> str:
    include_teacher = teacher_mock is not None
    source_counts = Counter(item.get("source_label") or "未知题源" for item in question_bank)
    source_summary = "".join(f"<li>{esc(label)}：{count} 题</li>" for label, count in source_counts.items())
    candidate_lines = "".join(
        f"<li>{esc(item.get('title'))}：{item.get('question_count', 0)} 题，{esc(item.get('path'))}</li>"
        for item in candidates
    )
    teacher_panel = ""
    if include_teacher:
        teacher_panel = f'<a class="panel" href="teacher_mock_analysis.html"><h2>老师模拟卷解析</h2><p class="subtle">{esc(teacher_mock.get("title"))}</p></a>'
    elif candidates:
        teacher_panel = f'<div class="panel"><h2>老师模拟卷候选</h2><p class="subtle">检测到多个或未能唯一确认，请用 --teacher-mock 指定。</p><ul>{candidate_lines}</ul></div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(meta.get('course_title'))} - 复习入口</title>
  <style>{common_css()}.grid a.panel {{ color: inherit; text-decoration: none; }} .grid a.panel:hover {{ border-color: var(--accent); }}</style>
</head>
<body>
<div class="shell">
  <header>
    <h1>{esc(meta.get('course_title'))} 期末复习入口</h1>
    <p class="subtle">多页面静态复习包：题目页、模拟卷，以及条件生成的老师模拟卷解析。</p>
    {nav_html(include_teacher)}
  </header>
  <div class="grid">
    <div class="panel stat"><strong>{len(question_bank)}</strong>题目页题目</div>
    <div class="panel stat"><strong>{len(mock_exam.get('items') or [])}</strong>模拟卷题目</div>
    <div class="panel stat"><strong>{counts.get('documents', 0)}</strong>课程资料</div>
    <div class="panel stat"><strong>{counts.get('assignments', 0)}</strong>作业</div>
  </div>
  <section>
    <div class="grid">
      <a class="panel" href="questions.html"><h2>题目页</h2><p class="subtle">按章节限量汇总选择、判断、填空和主观题，答案解析可折叠查看。</p></a>
      <a class="panel" href="mock_exam.html"><h2>模拟卷</h2><p class="subtle">有老师模拟卷时按同型结构生成；否则按默认结构生成。</p></a>
      {teacher_panel}
      <a class="panel" href="practice.generated.html"><h2>脚本兜底页</h2><p class="subtle">保留旧的自动生成练习页，便于对照和排错。</p></a>
    </div>
  </section>
  <section>
    <h2>题源构成</h2>
    <div class="panel"><ul>{source_summary or '<li>暂未生成题源统计。</li>'}</ul></div>
  </section>
</div>
</body>
</html>
"""


def build_pages(
    course_dir: Path,
    *,
    teacher_mock_selector: str,
    questions_per_chapter: int,
    ai_fill_min_per_chapter: int,
) -> dict[str, Any]:
    meta = load_course_meta(course_dir)
    generated_dir = course_dir / "generated"
    output_dir = course_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)

    question_bank = build_question_bank(course_dir, questions_per_chapter, ai_fill_min_per_chapter)
    candidates = material_mock_candidates(course_dir)
    selected_teacher_mock = resolve_teacher_mock(course_dir, candidates, teacher_mock_selector)
    mock_exam = build_mock_exam(course_dir, question_bank, selected_teacher_mock)

    question_payload = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "questions_per_chapter": questions_per_chapter,
        "ai_fill_min_per_chapter": ai_fill_min_per_chapter,
        "items": question_bank,
    }
    candidates_payload = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "manual_selector": teacher_mock_selector,
        "selected": selected_teacher_mock.get("path") if selected_teacher_mock else "",
        "candidates": candidates,
    }
    write_json(generated_dir / "question_bank.json", question_payload)
    write_json(generated_dir / "teacher_mock_candidates.json", candidates_payload)
    write_json(generated_dir / "mock_exam.json", mock_exam)

    include_teacher = selected_teacher_mock is not None
    counts = load_db_counts(course_dir)
    write_text(output_dir / "questions.html", render_questions_page(meta, question_bank, include_teacher))
    write_text(output_dir / "mock_exam.html", render_mock_exam_page(meta, mock_exam, include_teacher))
    if include_teacher:
        write_text(output_dir / "teacher_mock_analysis.html", render_teacher_mock_analysis_page(meta, selected_teacher_mock))
    else:
        teacher_path = output_dir / "teacher_mock_analysis.html"
        if teacher_path.exists():
            teacher_path.unlink()
    write_text(output_dir / "practice.html", render_index_page(meta, counts, question_bank, mock_exam, selected_teacher_mock, candidates))

    return {
        "question_bank": len(question_bank),
        "mock_exam": len(mock_exam.get("items") or []),
        "teacher_mock": selected_teacher_mock.get("path") if selected_teacher_mock else "",
        "teacher_candidates": len(candidates),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate question bank, mock exam, and multi-page static review HTML.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--teacher-mock", default="", help="Optional teacher mock Markdown path, stem, or title to force selection.")
    parser.add_argument("--questions-per-chapter", type=int, default=80)
    parser.add_argument("--ai-fill-min-per-chapter", type=int, default=25)
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    result = build_pages(
        course_dir,
        teacher_mock_selector=args.teacher_mock,
        questions_per_chapter=max(args.questions_per_chapter, 0),
        ai_fill_min_per_chapter=max(args.ai_fill_min_per_chapter, 0),
    )
    print(
        "exam_pages="
        f"question_bank={result['question_bank']} "
        f"mock_exam={result['mock_exam']} "
        f"teacher_mock={result['teacher_mock'] or 'none'} "
        f"teacher_candidates={result['teacher_candidates']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
