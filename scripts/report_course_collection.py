# -*- coding: utf-8 -*-
"""Write a detailed Markdown report for one Xuexitong course collection run."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, chapter_sort_key


def U(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


REPORT_TITLE = U(r"\u8bfe\u7a0b\u91c7\u96c6\u62a5\u544a")
OVERVIEW = U(r"\u6982\u89c8")
COURSE_DIR = U(r"\u8bfe\u7a0b\u76ee\u5f55")
SOURCE_PAGE = U(r"\u6765\u6e90\u9875\u9762")
GENERATED_AT = U(r"\u751f\u6210\u65f6\u95f4")
DATABASE = U(r"\u6570\u636e\u5e93")
DOWNLOADED_MATERIALS = U(r"\u5df2\u4e0b\u8f7d\u8d44\u6599")
NO_DOWNLOADED = U(r"\u6682\u65e0\u5df2\u4e0b\u8f7d\u8d44\u6599\u6587\u4ef6\u3002")
SKIPPED_MATERIALS = U(r"\u672a\u4e0b\u8f7d\u6216\u8df3\u8fc7\u7684\u8d44\u6599")
NO_SKIPPED = U(r"\u6682\u65e0\u672a\u4e0b\u8f7d/\u8df3\u8fc7\u8d44\u6599\u8bb0\u5f55\u3002")
CONVERSION_FAILURES = U(r"\u8f6c\u6362\u5931\u8d25\u8bb0\u5f55")
NO_CONVERSION_FAILURES = U(r"\u6682\u65e0\u8f6c\u6362\u5931\u8d25\u8bb0\u5f55\u3002")
ASSIGNMENTS = U(r"\u4f5c\u4e1a\u4e0e\u9898\u76ee")
NO_ASSIGNMENTS = U(r"\u6682\u65e0\u4f5c\u4e1a\u9898\u76ee\u3002")
EXAMS = U(r"\u8003\u8bd5\u91c7\u96c6\u72b6\u6001")
NO_EXAMS = U(r"\u672a\u53d1\u73b0\u53ef\u91c7\u96c6\u7684\u8003\u8bd5\u9898\uff0c\u6d41\u6c34\u7ebf\u5c06\u7ee7\u7eed\u4f7f\u7528\u4f5c\u4e1a\u5019\u9009\u548c\u8bfe\u4ef6\u77e5\u8bc6\u70b9\u8865\u9898\u3002")
EXAM_FALLBACK_HINT = U(r"\u8003\u8bd5\u9898\u662f\u9ad8\u4f18\u5148\u7ea7\u6765\u6e90\uff0c\u4f46\u4e0d\u662f\u751f\u6210\u590d\u4e60\u5305\u7684\u786c\u4f9d\u8d56\uff1b\u8001\u5e08\u672a\u5f00\u653e\u3001\u65e0\u6743\u9650\u6216\u89e3\u6790 0 \u9898\u65f6\u4e0d\u4f1a\u963b\u65ad\u540e\u7eed\u6d41\u7a0b\u3002")
OUTPUTS = U(r"\u5df2\u751f\u6210\u8f93\u51fa")
CHAPTER_KNOWLEDGE = U(r"\u77e5\u8bc6\u70b9\u7ae0\u8282\u5212\u5206")
NO_CHAPTER_KNOWLEDGE = U(r"\u6682\u672a\u751f\u6210\u77e5\u8bc6\u70b9\u7ae0\u8282\u4fe1\u606f\uff0c\u8bf7\u5148\u8fd0\u884c `build_course_db.py`\u3002")
CODEX_HINT = U(r"\u7ed9 Codex \u7684\u590d\u4e60\u63d0\u793a")
HINT_CONTEXT = U(r"\u5148\u8bfb `generated/codex_context.md`\uff0c\u518d\u6309\u77e5\u8bc6\u70b9\u8ffd\u8bfb `materials_md/` \u4e2d\u7684\u539f\u59cb Markdown\u3002")
HINT_FINAL = U(r"\u6700\u7ec8\u590d\u4e60\u8d44\u6599\u5e94\u8986\u76d6\uff1a\u6838\u5fc3\u6982\u5ff5\u3001\u5e38\u8003\u9898\u578b\u3001\u6613\u9519\u70b9\u3001\u4f5c\u4e1a\u9898\u5bf9\u5e94\u77e5\u8bc6\u70b9\u3001\u5206\u5c42\u7ec3\u4e60\u9898\u548c\u7b54\u6848\u89e3\u6790\u3002")
QUESTION_UNIT = U(r"\u9898")
MORE_FILES = U(r"\u8fd8\u6709")
NOT_EXPANDED_MATERIAL = U(r"\u4e2a\u8d44\u6599\u6587\u4ef6\u672a\u5728\u62a5\u544a\u4e2d\u5c55\u5f00\u3002")
NOT_EXPANDED_QUESTIONS = U(r"\u9898\u672a\u5728\u62a5\u544a\u4e2d\u5c55\u5f00\u3002")
NOT_EXPANDED_CONCEPTS = U(r"\u4e2a\u77e5\u8bc6\u70b9\u672a\u5728\u672c\u7ae0\u5c55\u5f00\u3002")
CHINESE_COLON = U(r"\uff1a")
SOURCE_LABEL = U(r"\u6765\u6e90\uff1a")
PLATFORM_EXAM_NOISE_RE = re.compile(r"(exam-ans|mooc-exam-p\d|系统检测到你|强制收卷|切屏|页面不存在|暂时不能访问)", re.I)


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            if isinstance(raw, dict):
                rows.append(raw)
        except Exception:
            rows.append({"status": "failed", "error": line})
    return rows


def rel(path: str | Path, base: Path) -> str:
    p = Path(path)
    try:
        if not p.is_absolute():
            p = base / p
        return p.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def norm_abs(path: str | Path, base: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    try:
        return str(p.resolve()).casefold()
    except Exception:
        return str(p).casefold()


def size_text(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    units = ["KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{size} B"


def path_size(path: Path) -> str:
    try:
        return size_text(path.stat().st_size)
    except Exception:
        return "unknown"


def db_counts(course_dir: Path) -> dict[str, int]:
    db_path = course_dir / "course.db"
    counts: dict[str, int] = {}
    if not db_path.exists():
        return counts
    conn = sqlite3.connect(db_path)
    try:
        for table in ("source_files", "documents", "assignments", "questions", "concepts", "practice_items"):
            try:
                counts[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except Exception:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def parse_json_value(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except Exception:
        return fallback


def summarize_chapter_concepts(course_dir: Path, max_per_chapter: int = 30) -> list[str]:
    db_path = course_dir / "course.db"
    if not db_path.exists():
        return []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapters: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT title, summary, source_refs_json, metadata_json FROM concepts ORDER BY title").fetchall()
    except Exception:
        conn.close()
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    for row in rows:
        metadata = parse_json_value(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            metadata = {}
        chapter = metadata.get("chapter") if isinstance(metadata.get("chapter"), dict) else {}
        title = metadata.get("chapter_title") or chapter.get("title") or UNKNOWN_CHAPTER
        order = metadata.get("chapter_order") or chapter.get("order") or 999999
        key = chapter.get("key") or f"chapter-{order}"
        chapter_info = {"title": title, "order": order, "key": key}
        chapters[key] = chapter_info
        refs = parse_json_value(row["source_refs_json"], [])
        grouped[key].append({"title": row["title"], "summary": row["summary"], "source_refs": refs if isinstance(refs, list) else []})

    lines: list[str] = []
    for key, chapter in sorted(chapters.items(), key=lambda item: chapter_sort_key(item[1])):
        rows = grouped.get(key, [])
        lines.extend([f"### {chapter.get('title') or UNKNOWN_CHAPTER}", ""])
        for index, item in enumerate(rows[:max_per_chapter], start=1):
            refs = item.get("source_refs") or []
            ref_text = ""
            if refs:
                ref_text = f" {SOURCE_LABEL}" + ", ".join(f"`{truncate(str(ref), 60)}`" for ref in refs[:3])
            lines.append(f"{index}. **{item['title']}**{CHINESE_COLON}{truncate(item.get('summary', ''), 120)}{ref_text}")
        if len(rows) > max_per_chapter:
            lines.append(f"- {MORE_FILES} {len(rows) - max_per_chapter} {NOT_EXPANDED_CONCEPTS}")
        lines.append("")
    return lines


def load_questions(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {"title": path.stem, "questions": []}
    payload.setdefault("title", path.stem)
    payload.setdefault("questions", [])
    return payload


def truncate(value: str, limit: int = 120) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def summarize_materials(course_dir: Path, max_items: int) -> tuple[list[str], list[str], list[str]]:
    manifest = read_json(course_dir / "manifests" / "course_manifest.json", {"items": []})
    conversion = read_json(course_dir / "manifests" / "conversion_manifest.json", {"items": []})
    conv_by_source = {
        norm_abs(item.get("source", ""), course_dir): item
        for item in conversion.get("items", [])
        if isinstance(item, dict) and item.get("source")
    }
    successful_sources = {
        source
        for source, item in conv_by_source.items()
        if item.get("status") in {"done", "skipped"}
    }
    raw_root = course_dir / "raw" / "materials"
    raw_files = sorted([p for p in raw_root.rglob("*") if p.is_file()], key=lambda p: str(p).casefold()) if raw_root.exists() else []

    downloaded: list[str] = []
    for index, path in enumerate(raw_files[:max_items], start=1):
        conv = conv_by_source.get(norm_abs(path, course_dir), {})
        conv_status = conv.get("status") or "not converted"
        md = conv.get("markdown") or ""
        md_part = f" -> `{rel(md, course_dir)}`" if md else ""
        downloaded.append(f"{index}. `{rel(path, course_dir)}` ({path_size(path)}), conversion: `{conv_status}`{md_part}")
    if len(raw_files) > max_items:
        downloaded.append(f"- {MORE_FILES} {len(raw_files) - max_items} {NOT_EXPANDED_MATERIAL}")

    downloaded_names = {p.name for p in raw_files} | {p.stem for p in raw_files}
    generic_material_titles = {'下载', '教师课件', '课件1', '资料'}
    failed: list[str] = []
    for item in manifest.get("items", []):
        if not isinstance(item, dict) or item.get("kind") != "material" or item.get("status") == "done":
            continue
        title = item.get("title") or item.get("metadata", {}).get("title") or "material"
        status = item.get("status") or "unknown"
        error = item.get("error") or ""
        url = item.get("url") or item.get("metadata", {}).get("href") or ""
        if title in downloaded_names:
            continue
        if title in generic_material_titles and ("not a direct HTTP" in error or "mycourse/" in url or url.startswith("javascript")):
            continue
        failed.append(f"- `{status}` {truncate(title, 80)}: {truncate(error, 180)}" + (f" ({truncate(url, 80)})" if url else ""))


    conversion_failed: list[str] = []
    for item in conversion.get("items", []):
        if not isinstance(item, dict) or item.get("status") not in {"failed", "error"}:
            continue
        conversion_failed.append(f"- `{rel(item.get('source', ''), course_dir)}`: {truncate(item.get('error', ''), 180)}")
    for item in read_jsonl(course_dir / "manifests" / "failed_items.jsonl"):
        source = item.get("source") or item.get("title") or "unknown"
        if norm_abs(source, course_dir) in successful_sources:
            continue
        conversion_failed.append(f"- `{rel(source, course_dir)}`: {truncate(item.get('error', ''), 180)}")

    return downloaded, failed, conversion_failed


def summarize_assignments(course_dir: Path, max_questions: int) -> tuple[list[str], int]:
    assignment_lines: list[str] = []
    total_questions = 0
    for index, path in enumerate(sorted((course_dir / "assignments_md").glob("*.questions.json")), start=1):
        if PLATFORM_EXAM_NOISE_RE.search(path.as_posix()):
            continue
        payload = load_questions(path)
        questions = payload.get("questions") or []
        total_questions += len(questions)
        md_path = path.with_name(path.name.replace(".questions.json", ".md"))
        title = payload.get("title") or path.stem
        if PLATFORM_EXAM_NOISE_RE.search(title):
            continue
        assignment_lines.append(f"{index}. {title}: {len(questions)} {QUESTION_UNIT}, Markdown `{rel(md_path, course_dir)}`, JSON `{rel(path, course_dir)}`")
        for q_index, question in enumerate(questions[:max_questions], start=1):
            text = question.get("question") if isinstance(question, dict) else str(question)
            assignment_lines.append(f"   - Q{q_index}: {truncate(text, 140)}")
        if len(questions) > max_questions:
            assignment_lines.append(f"   - {MORE_FILES} {len(questions) - max_questions} {NOT_EXPANDED_QUESTIONS}")
    return assignment_lines, total_questions


def summarize_exams(course_dir: Path, max_questions: int) -> tuple[list[str], int]:
    exam_lines: list[str] = []
    total_questions = 0
    question_files = sorted((course_dir / "exams_md").glob("*.questions.json"), key=lambda p: p.as_posix().casefold())
    for index, path in enumerate(question_files, start=1):
        payload = load_questions(path)
        questions = payload.get("questions") or []
        total_questions += len(questions)
        md_path = path.with_name(path.name.replace(".questions.json", ".md"))
        title = payload.get("title") or path.stem
        exam_lines.append(f"{index}. {title}: {len(questions)} {QUESTION_UNIT}, Markdown `{rel(md_path, course_dir)}`, JSON `{rel(path, course_dir)}`")
        for q_index, question in enumerate(questions[:max_questions], start=1):
            text = question.get("question") if isinstance(question, dict) else str(question)
            q_type = question.get("type") if isinstance(question, dict) else ""
            prefix = f"{q_type} " if q_type else ""
            exam_lines.append(f"   - Q{q_index}: {prefix}{truncate(text, 140)}")
        if len(questions) > max_questions:
            exam_lines.append(f"   - {MORE_FILES} {len(questions) - max_questions} {NOT_EXPANDED_QUESTIONS}")

    manifest = read_json(course_dir / "manifests" / "course_manifest.json", {"items": []})
    exam_items = [item for item in manifest.get("items", []) if isinstance(item, dict) and item.get("kind") == "exam"]
    if exam_items:
        status_counts = Counter(item.get("status") or "pending" for item in exam_items)
        summary = ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        exam_lines.insert(0, f"- manifest: {summary}")
        unavailable_rows = [
            item
            for item in exam_items
            if item.get("status") in {"unavailable", "failed_nonfatal", "failed"} or item.get("error")
        ]
        for item in unavailable_rows[:30]:
            title = item.get("title") or item.get("url") or "exam"
            status = item.get("status") or "unknown"
            reason = item.get("error") or item.get("metadata", {}).get("reason") or ""
            exam_lines.append(f"- `{status}` {truncate(title, 100)}: {truncate(reason, 220)}")
        if len(unavailable_rows) > 30:
            exam_lines.append(f"- {MORE_FILES} {len(unavailable_rows) - 30} 个不可用/失败考试记录未展开。")

    if not question_files:
        exam_lines.append(f"- {NO_EXAMS}")
    exam_lines.append(f"- {EXAM_FALLBACK_HINT}")
    return exam_lines, total_questions


def summarize_notebooklm(course_dir: Path) -> list[str]:
    manifest_path = course_dir / "generated" / "notebooklm" / "manifest.json"
    if not manifest_path.exists():
        return ["- Not enabled or not run: `generated/notebooklm/manifest.json` is missing."]
    manifest = read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        return ["- NotebookLM manifest exists but could not be parsed."]
    notebook = manifest.get("notebook") if isinstance(manifest.get("notebook"), dict) else {}
    normalized = manifest.get("normalized") if isinstance(manifest.get("normalized"), dict) else {}
    lines = [
        f"- status: `{manifest.get('status', 'unknown')}`",
        f"- profile: `{manifest.get('profile', 'default')}`",
        f"- notebook: `{notebook.get('title', '')}` `{notebook.get('id', '')}`",
        f"- bundles: {len(manifest.get('bundles', []) or [])}",
        f"- normalized practice_items: {normalized.get('practice_items', 0)}",
    ]
    errors = manifest.get("errors") or []
    if errors:
        last = errors[-1] if isinstance(errors[-1], dict) else {"message": str(errors[-1])}
        lines.append(f"- last error: {truncate(str(last.get('message', '')), 220)}")
        lines.append("- auth hint: run `notebooklm login` and `notebooklm auth check --test --json`, or use `--notebooklm-dry-run` for local validation.")
    for relative in (
        "generated/notebooklm/study_guide.md",
        "generated/notebooklm/quiz.json",
        "generated/notebooklm/flashcards.json",
        "generated/notebooklm/mind_map.json",
        "generated/notebooklm/practice_items.json",
        "generated/notebooklm/manifest.json",
    ):
        lines.append(output_exists(course_dir, relative))
    return lines


def output_exists(course_dir: Path, relative: str) -> str:
    path = course_dir / relative
    return f"- `{relative}` ({path_size(path)})" if path.exists() else f"- `{relative}` (missing)"


def build_report(course_dir: Path, max_items: int, max_questions: int) -> str:
    meta = read_json(course_dir / "course_meta.json", {})
    counts = db_counts(course_dir)
    title = meta.get("course_title") or course_dir.name
    downloaded, failed_materials, conversion_failed = summarize_materials(course_dir, max_items=max_items)
    exams, exam_questions = summarize_exams(course_dir, max_questions=max_questions)
    assignments, assignment_questions = summarize_assignments(course_dir, max_questions=max_questions)
    chapter_concepts = summarize_chapter_concepts(course_dir)

    lines = [
        f"# {title} {REPORT_TITLE}",
        "",
        f"## {OVERVIEW}",
        f"- {COURSE_DIR}: `{course_dir}`",
        f"- {SOURCE_PAGE}: {meta.get('source_url', '')}",
        f"- {GENERATED_AT}: {now_text()}",
        f"- {DATABASE}: source_files={counts.get('source_files', 0)}, documents={counts.get('documents', 0)}, assignments={counts.get('assignments', 0)}, questions={counts.get('questions', assignment_questions + exam_questions)}, concepts={counts.get('concepts', 0)}, practice_items={counts.get('practice_items', 0)}",
        "",
        f"## {CHAPTER_KNOWLEDGE}",
    ]
    lines.extend(chapter_concepts or [NO_CHAPTER_KNOWLEDGE])
    lines.extend(["", f"## {DOWNLOADED_MATERIALS}"])
    lines.extend(downloaded or [NO_DOWNLOADED])
    lines.extend(["", f"## {SKIPPED_MATERIALS}"])
    lines.extend(failed_materials or [NO_SKIPPED])
    lines.extend(["", f"## {CONVERSION_FAILURES}"])
    lines.extend(conversion_failed or [NO_CONVERSION_FAILURES])
    lines.extend(["", f"## {EXAMS}"])
    lines.extend(exams or [NO_EXAMS])
    lines.extend(["", f"## {ASSIGNMENTS}"])
    lines.extend(assignments or [NO_ASSIGNMENTS])
    lines.extend(["", f"## {OUTPUTS}"])
    lines.extend([
        output_exists(course_dir, "generated/codex_context.md"),
        output_exists(course_dir, "generated/question_candidates.json"),
        output_exists(course_dir, "generated/teacher_knowledge_seeds.json"),
        output_exists(course_dir, "generated/knowledge_bank.json"),
        output_exists(course_dir, "generated/question_audit.json"),
        output_exists(course_dir, "generated/question_bank.json"),
        output_exists(course_dir, "generated/mock_exam.json"),
        output_exists(course_dir, "output/final_review_seed.md"),
        output_exists(course_dir, "output/practice.html"),
        output_exists(course_dir, "output/questions.html"),
        output_exists(course_dir, "output/mock_exam.html"),
        output_exists(course_dir, "course.db"),
    ])
    lines.extend(["", "## NotebookLM Optional Enrichment"])
    lines.extend(summarize_notebooklm(course_dir))
    lines.extend([
        "",
        f"## {CODEX_HINT}",
        f"- {HINT_CONTEXT}",
        f"- {HINT_FINAL}",
    ])
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a detailed collection/conversion report for a course directory.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--output", default="", help="Optional output report path. Defaults to <course>/output/crawl_report.md.")
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument("--max-questions", type=int, default=5)
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    report = build_report(course_dir, max_items=args.max_items, max_questions=args.max_questions)
    output = Path(args.output).resolve() if args.output else course_dir / "output" / "crawl_report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
