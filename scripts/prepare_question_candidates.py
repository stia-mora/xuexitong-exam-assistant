"""Prepare raw question candidates for per-question LLM audit.

The output of this script is intentionally not final study content.  It gathers
candidate questions and teacher knowledge seeds so an agent can review every
item, fill missing answers/analysis, discard noise, and write the audited final
question_bank.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, infer_chapter


QUESTIONY_SOURCE_RE = re.compile(r"(题库|习题|练习|测试|测验|作业|试卷|模拟|样卷|真题|考试|quiz|exam|mock|sample)", re.I)
PLATFORM_EXAM_NOISE_RE = re.compile(r"(exam-ans|mooc-exam-p\d|系统检测到你|强制收卷|切屏|页面不存在|暂时不能访问)", re.I)
TYPE_RE = re.compile(r"(单选题|多选题|选择题|判断题|填空题|简答题|论述题|问答题|材料题|计算题|证明题|主观题)")
NUMBERED_QUESTION_RE = re.compile(r"^\s*(?:第\s*)?(?P<num>\d{1,3}|[一二三四五六七八九十百]{1,5})\s*(?:题|[.、．)])\s*(?P<body>.+)?$")
TYPED_QUESTION_RE = re.compile(r"^\s*[【\[]?\s*(?P<type>单选题|多选题|选择题|判断题|填空题|简答题|论述题|问答题|材料题|计算题|证明题|主观题)\s*[】\]]?\s*(?P<body>.+)?$")
OPTION_RE = re.compile(r"^\s*(?P<label>[A-HＡ-Ｈ])\s*[.、．)]\s*(?P<body>.+)$", re.I)
ANSWER_RE = re.compile(r"(?:正确答案|参考答案|答案|我的答案)\s*[:：]\s*(?P<body>.+)")
EXPLANATION_RE = re.compile(r"(?:解析|答案解析|讲解|分析)\s*[:：]\s*(?P<body>.+)")
HEADING_RE = re.compile(r"^#{1,4}\s+(.+)")
NOISE_MARKERS = (
    "作答记录",
    "我的答案",
    "此附件仅支持打开",
    "作业详情",
    "题量:",
    "满分:",
    "智能分析",
    "未在报告中展开",
    "![](../assets/",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


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
        if len(title) < 2 or TYPE_RE.fullmatch(title):
            continue
        if title not in headings:
            headings.append(title[:160])
        if len(headings) >= limit:
            break
    return headings


def extract_heading_sections(markdown: str, fallback: str, limit: int = 80) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_title = clean_text(fallback)
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal body_lines
        title = clean_text(re.sub(r"[`*_#]", "", current_title))
        if len(title) >= 2 and not TYPE_RE.fullmatch(title):
            excerpt = clean_text(" ".join(body_lines))[:900]
            if title or excerpt:
                sections.append({"title": title[:160], "raw_excerpt": excerpt})
        body_lines = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading_match = HEADING_RE.match(line)
        if heading_match:
            if current_title or body_lines:
                flush()
            current_title = clean_text(heading_match.group(1))
            continue
        if not line or line.startswith("![]("):
            continue
        if any(marker in line for marker in NOISE_MARKERS):
            continue
        if len(line) > 500:
            continue
        body_lines.append(line)
        if len(body_lines) >= 10:
            flush()
            current_title = current_title or fallback
        if len(sections) >= limit:
            break
    if len(sections) < limit and (current_title or body_lines):
        flush()
    return sections[:limit]


def normalize_option_label(label: str) -> str:
    return (label or "").upper().translate(str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH")).strip()


def normalize_options(options: Any) -> list[dict[str, Any]]:
    if not options:
        return []
    if isinstance(options, dict):
        iterable = [{"label": key, "text": value} for key, value in options.items()]
    else:
        iterable = options
    rows: list[dict[str, Any]] = []
    for index, option in enumerate(iterable, start=1):
        if isinstance(option, dict):
            label = normalize_option_label(str(option.get("label") or chr(64 + index)))
            text = clean_text(option.get("text") or option.get("content") or "")
        else:
            raw = clean_text(option)
            match = OPTION_RE.match(raw)
            label = normalize_option_label(match.group("label")) if match else chr(64 + index)
            text = clean_text(match.group("body") if match else raw)
        if text:
            rows.append({"label": label, "text": text})
    return rows


def canonical_type(value: str, question: str = "", options: list[dict[str, Any]] | None = None) -> str:
    text = f"{value or ''} {question or ''}"
    if any(token in text for token in ("单选", "多选", "选择")) or options:
        return "选择题"
    if "判断" in text:
        return "判断题"
    if "填空" in text or "____" in text or "（ ）" in text or "( )" in text:
        return "填空题"
    return "主观题"


def quality_flags(stem: str, answer: str, analysis: str, options: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    if not answer or answer in {"正确答案：", "参考答案待补充"}:
        flags.append("missing_answer")
    if not analysis:
        flags.append("missing_analysis")
    if any(marker in stem for marker in NOISE_MARKERS) or len(stem) > 500:
        flags.append("possible_noise")
    if canonical_type("", stem, options) == "选择题" and not options:
        flags.append("missing_options")
    return flags


def candidate_record(
    *,
    course_dir: Path,
    stem: str,
    q_type: str,
    options: Any,
    answer: str,
    analysis: str,
    chapter: dict[str, Any],
    source_kind: str,
    source_refs: list[str],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = normalize_options(options)
    canonical = canonical_type(q_type, stem, opts)
    candidate_id = sha1_text("\n".join([stem, canonical, "|".join(source_refs), source_kind]))[:24]
    return {
        "id": candidate_id,
        "source_id": candidate_id,
        "original_stem": clean_text(stem),
        "type": q_type or canonical,
        "canonical_type": canonical,
        "options": opts,
        "original_answer": clean_text(answer),
        "original_analysis": clean_text(analysis),
        "chapter": chapter,
        "chapter_title": chapter.get("title") or UNKNOWN_CHAPTER,
        "source_kind": source_kind,
        "source_refs": source_refs,
        "quality_flags": quality_flags(stem, answer, analysis, opts),
        "metadata": metadata or {},
    }


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in records:
        key = sha1_text(clean_text(item.get("original_stem")).casefold())[:24]
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def load_assignment_candidates(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "assignments_md").glob("*.questions.json"), key=lambda p: p.as_posix().casefold()):
        if PLATFORM_EXAM_NOISE_RE.search(path.as_posix()):
            continue
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        title = clean_text(payload.get("title") or path.stem)
        if PLATFORM_EXAM_NOISE_RE.search(title):
            continue
        markdown_path = path.with_name(path.name.replace(".questions.json", ".md"))
        chapter = infer_chapter(title=title, path=rel(markdown_path, course_dir), headings=[])
        for item in payload.get("questions", []) or []:
            if not isinstance(item, dict):
                continue
            stem = clean_text(item.get("question") or "")
            if PLATFORM_EXAM_NOISE_RE.search(stem):
                continue
            if not stem:
                continue
            rows.append(
                candidate_record(
                    course_dir=course_dir,
                    stem=stem,
                    q_type=item.get("type") or "unknown",
                    options=item.get("options") or [],
                    answer=item.get("answer") or "",
                    analysis=item.get("explanation") or item.get("analysis") or "",
                    chapter=chapter,
                    source_kind="xxt_assignment",
                    source_refs=[rel(path, course_dir)],
                    metadata={
                        "assignment_title": title,
                        "number": item.get("number"),
                        "extraction_method": item.get("extraction_method") or payload.get("extraction_method") or "",
                    },
                )
            )
    return rows


def load_exam_candidates(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "exams_md").glob("*.questions.json"), key=lambda p: p.as_posix().casefold()):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        title = clean_text(payload.get("title") or path.stem)
        markdown_path = path.with_name(path.name.replace(".questions.json", ".md"))
        chapter = infer_chapter(title=title, path=rel(markdown_path, course_dir), headings=[])
        for item in payload.get("questions", []) or []:
            if not isinstance(item, dict):
                continue
            stem = clean_text(item.get("question") or "")
            if not stem:
                continue
            rows.append(
                candidate_record(
                    course_dir=course_dir,
                    stem=stem,
                    q_type=item.get("type") or "unknown",
                    options=item.get("options") or [],
                    answer=item.get("answer") or "",
                    analysis=item.get("explanation") or item.get("analysis") or "",
                    chapter=chapter,
                    source_kind="xxt_exam",
                    source_refs=[rel(path, course_dir)],
                    metadata={
                        "exam_title": title,
                        "number": item.get("number"),
                        "points": item.get("points") or "",
                        "my_answer": item.get("my_answer") or "",
                        "source_url": payload.get("source_url") or "",
                        "extraction_method": item.get("extraction_method") or payload.get("extraction_method") or "xuexitong_exam",
                    },
                )
            )
    return rows


def extract_markdown_candidates(course_dir: Path, path: Path, markdown: str) -> list[dict[str, Any]]:
    rel_path = rel(path, course_dir)
    title = first_heading(markdown, path.stem)
    headings = extract_headings(markdown)
    chapter = infer_chapter(title=title, path=rel_path, headings=headings)
    questiony_source = bool(QUESTIONY_SOURCE_RE.search(f"{path.name} {title}"))
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_type = ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        stem = clean_text(current.get("stem") or "")
        if not stem or (not questiony_source and not current.get("typed_start")):
            current = None
            return
        if not any(cue in stem for cue in ("？", "?", "（", "(", "____", "下列", "哪", "判断", "简述", "说明", "计算", "证明")) and not current.get("options"):
            current = None
            return
        rows.append(
            candidate_record(
                course_dir=course_dir,
                stem=stem,
                q_type=current.get("type") or current_type or "unknown",
                options=current.get("options") or [],
                answer=current.get("answer") or "",
                analysis=current.get("analysis") or "",
                chapter=chapter,
                source_kind="teacher_material_question",
                source_refs=[rel_path],
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
            type_match = TYPE_RE.search(heading.group(1))
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
            current["analysis"] = explanation_match.group("body").strip()
            continue
        if option_match and current is not None:
            current.setdefault("options", []).append({"label": normalize_option_label(option_match.group("label")), "text": clean_text(option_match.group("body"))})
            continue

        starts_question = False
        body = ""
        q_type = current_type
        typed_start = False
        if typed_match:
            q_type = typed_match.group("type")
            body = clean_text(typed_match.group("body") or "")
            starts_question = bool(body) or questiony_source
            typed_start = True
        elif numbered_match:
            body = clean_text(numbered_match.group("body") or "")
            starts_question = questiony_source and bool(body)
        if starts_question:
            flush()
            current = {"type": q_type or "unknown", "stem": body, "options": [], "line": line_no, "typed_start": typed_start}
            continue
        if current is not None and len(line) < 240 and not line.startswith("#"):
            current["stem"] = clean_text(f"{current.get('stem', '')} {line}")
    flush()
    return rows


def load_material_candidates(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "materials_md").glob("*.md"), key=lambda p: p.as_posix().casefold()):
        rows.extend(extract_markdown_candidates(course_dir, path, read_text(path)))
    return rows


def collect_knowledge_seeds(course_dir: Path) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_from_markdown(path: Path, source_kind: str, priority: int, limit: int) -> None:
        markdown = read_text(path)
        title = first_heading(markdown, path.stem)
        sections = extract_heading_sections(markdown, title, limit=limit)
        headings = [section["title"] for section in sections] or extract_headings(markdown, limit=limit) or [title]
        chapter = infer_chapter(title=title, path=rel(path, course_dir), headings=headings)
        for section in sections or [{"title": title, "raw_excerpt": ""}]:
            text = clean_text(section.get("title"))
            key = sha1_text(f"{source_kind}:{text}".casefold())[:24]
            if len(text) < 2 or key in seen:
                continue
            seen.add(key)
            raw_excerpt = clean_text(section.get("raw_excerpt"))[:900]
            seeds.append(
                {
                    "id": key,
                    "title": text,
                    "raw_excerpt": raw_excerpt,
                    "source_kind": source_kind,
                    "priority": priority,
                    "chapter": chapter,
                    "chapter_title": chapter.get("title") or UNKNOWN_CHAPTER,
                    "source_refs": [rel(path, course_dir)],
                    "needs_llm_expansion": len(raw_excerpt) < 40,
                }
            )

    teacher_focus = course_dir / "input" / "teacher_focus.md"
    if teacher_focus.exists():
        add_from_markdown(teacher_focus, source_kind="teacher_focus", priority=0, limit=160)
    for path in sorted((course_dir / "materials_md").glob("*.md"), key=lambda p: p.as_posix().casefold()):
        add_from_markdown(path, source_kind="course_material", priority=10, limit=100)
    return seeds


def prepare(course_dir: Path) -> dict[str, int]:
    exam_candidates = load_exam_candidates(course_dir)
    assignment_candidates = load_assignment_candidates(course_dir)
    material_candidates = load_material_candidates(course_dir)
    candidates = dedupe(exam_candidates + assignment_candidates + material_candidates)
    knowledge_seeds = collect_knowledge_seeds(course_dir)
    generated_dir = course_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    flag_counts = Counter(flag for item in candidates for flag in item.get("quality_flags", []))
    type_counts = Counter(item.get("canonical_type") or "unknown" for item in candidates)
    payload = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "purpose": "raw candidates for mandatory per-question LLM audit; not final study content",
        "items": candidates,
        "summary": {
            "candidate_count": len(candidates),
            "exam_candidate_count": len(exam_candidates),
            "assignment_candidate_count": len(assignment_candidates),
            "material_candidate_count": len(material_candidates),
            "type_counts": dict(type_counts),
            "quality_flag_counts": dict(flag_counts),
            "knowledge_seed_count": len(knowledge_seeds),
        },
    }
    write_json(generated_dir / "question_candidates.json", payload)
    write_json(generated_dir / "teacher_knowledge_seeds.json", {"schema_version": 1, "updated_at": utc_now(), "items": knowledge_seeds})
    return {
        "candidates": len(candidates),
        "exam_candidate_count": len(exam_candidates),
        "assignment_candidate_count": len(assignment_candidates),
        "material_candidate_count": len(material_candidates),
        "knowledge_seeds": len(knowledge_seeds),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare raw question candidates and teacher knowledge seeds for LLM audit.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    args = parser.parse_args()
    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    result = prepare(course_dir)
    print(f"question_candidates={result['candidates']} teacher_knowledge_seeds={result['knowledge_seeds']} course={course_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
