"""Optional NotebookLM enrichment for a collected course.

This script keeps NotebookLM as an opt-in enhancer. It uploads deterministic
Markdown bundles derived from local converted course sources, downloads generated
study artifacts, and normalizes quiz/flashcard outputs into the existing
``generated/practice_items.json`` handoff consumed by ``build_course_db.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, chapter_sort_key, infer_chapter

DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_MAX_BUNDLE_CHARS = 180_000
NOTEBOOK_TITLE_PREFIX = "Qimokaisi Final Review"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def safe_slug(value: str, fallback: str = "bundle") -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value or "").strip(".-_")
    return (text or fallback)[:80]


def rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def object_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("source_id") or value.get("artifact_id") or "")
    return str(getattr(value, "id", "") or getattr(value, "source_id", "") or getattr(value, "artifact_id", "") or "")


def object_title(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("title") or value.get("name") or "")
    return str(getattr(value, "title", "") or getattr(value, "name", "") or "")


def status_task_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("task_id") or value.get("id") or "")
    return str(getattr(value, "task_id", "") or getattr(value, "id", "") or "")


def artifact_identifier(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("artifact_id") or value.get("id") or value.get("task_id") or "")
    return str(getattr(value, "artifact_id", "") or getattr(value, "id", "") or getattr(value, "task_id", "") or "")


def status_is_complete(value: Any) -> bool:
    flag = getattr(value, "is_complete", None)
    if isinstance(flag, bool):
        return flag
    if callable(flag):
        return bool(flag())
    status = getattr(value, "status", None)
    if status is None and isinstance(value, dict):
        status = value.get("status")
    return str(status or "").lower() in {"completed", "complete", "done", "ready"}


def find_workspace_root(course_dir: Path) -> Path:
    env_root = os.environ.get("QIMOKAISI_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    for parent in [course_dir.resolve(), *course_dir.resolve().parents]:
        if (parent / ".git").exists():
            return parent
    if course_dir.parent.name == "courses" and course_dir.parent.parent.name == "data":
        return course_dir.parent.parent.parent.resolve()
    return Path.cwd().resolve()


def ensure_notebooklm_home(course_dir: Path) -> Path:
    workspace_root = find_workspace_root(course_dir)
    home = Path(os.environ.get("NOTEBOOKLM_HOME") or workspace_root / "data" / "notebooklm").resolve()
    os.environ.setdefault("NOTEBOOKLM_HOME", str(home))
    os.environ.setdefault("NOTEBOOKLM_HL", DEFAULT_LANGUAGE)
    home.mkdir(parents=True, exist_ok=True)
    return home


def first_heading(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)", line.strip())
        if match:
            return clean_line(match.group(1))[:200]
    return fallback


def extract_headings(markdown: str, limit: int = 80) -> list[str]:
    headings: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^#{1,3}\s+(.+)", line.strip())
        if not match:
            continue
        title = clean_line(re.sub(r"[`*_#]", "", match.group(1)))
        if len(title) < 2 or title.lower().startswith("source file"):
            continue
        if title not in headings:
            headings.append(title[:160])
        if len(headings) >= limit:
            break
    return headings


def load_course_meta(course_dir: Path) -> dict[str, Any]:
    meta = read_json(course_dir / "course_meta.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("course_title", course_dir.name)
    meta.setdefault("course_slug", course_dir.name)
    return meta


def collect_markdown_sources(course_dir: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for root_name, kind in (("materials_md", "material"), ("assignments_md", "assignment")):
        root = course_dir / root_name
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md"), key=lambda item: item.as_posix().casefold()):
            markdown = read_text(path)
            title = first_heading(markdown, path.stem)
            headings = extract_headings(markdown)
            rel_path = rel(path, course_dir)
            if kind == "assignment":
                chapter = {"key": "assignments", "title": "Assignments", "order": 900000}
            else:
                chapter = infer_chapter(title=title, path=rel_path, headings=headings)
            sources.append(
                {
                    "kind": kind,
                    "path": path,
                    "rel_path": rel_path,
                    "title": title,
                    "headings": headings,
                    "chapter": chapter,
                    "markdown": markdown,
                    "sha256": sha256_text(markdown),
                }
            )
    return sources


def source_section(source: dict[str, Any]) -> str:
    headings = source.get("headings") or []
    heading_text = "\n".join(f"- {heading}" for heading in headings[:40]) or "- No headings extracted"
    return "\n".join(
        [
            f"# {source['title']}",
            "",
            f"Source path: `{source['rel_path']}`",
            f"Source kind: `{source['kind']}`",
            f"Source sha256: `{source['sha256']}`",
            "",
            "## Extracted headings",
            heading_text,
            "",
            "## Source Markdown",
            source["markdown"].strip(),
            "",
        ]
    ).strip() + "\n"


def build_bundle_records(course_dir: Path, max_chars: int) -> list[dict[str, Any]]:
    meta = load_course_meta(course_dir)
    sources = collect_markdown_sources(course_dir)
    grouped: dict[str, dict[str, Any]] = {}
    for source in sources:
        chapter = source.get("chapter") if isinstance(source.get("chapter"), dict) else {}
        key = str(chapter.get("key") or source.get("kind") or "unknown")
        grouped.setdefault(key, {"chapter": chapter, "sources": []})["sources"].append(source)

    records: list[dict[str, Any]] = []
    sorted_groups = sorted(grouped.values(), key=lambda item: chapter_sort_key(item.get("chapter") or {}))
    for group_index, group in enumerate(sorted_groups, start=1):
        chapter = group.get("chapter") or {}
        chapter_title = chapter.get("title") or UNKNOWN_CHAPTER
        part_sections: list[str] = []
        part_chars = 0
        part_index = 1
        for source in group["sources"]:
            section = source_section(source)
            if part_sections and part_chars + len(section) > max_chars:
                records.append(make_bundle_record(meta, chapter_title, group_index, part_index, part_sections))
                part_index += 1
                part_sections = []
                part_chars = 0
            part_sections.append(section)
            part_chars += len(section)
        if part_sections:
            records.append(make_bundle_record(meta, chapter_title, group_index, part_index, part_sections))
    if not records:
        title = meta.get("course_title") or course_dir.name
        records.append(
            {
                "title": f"{title} - empty source bundle",
                "stem": "bundle-001-empty-source-bundle",
                "content": f"# {title}\n\nNo converted Markdown sources were found.\n",
            }
        )
    return records


def make_bundle_record(meta: dict[str, Any], chapter_title: str, group_index: int, part_index: int, sections: list[str]) -> dict[str, Any]:
    course_title = meta.get("course_title") or meta.get("course_slug") or "course"
    title = f"{course_title} - {chapter_title}"
    if part_index > 1:
        title += f" part {part_index}"
    content = "\n\n".join(
        [
            f"# {title}",
            "",
            "This file is a deterministic local Markdown bundle generated for optional NotebookLM enrichment.",
            "It contains only converted Markdown and assignment text already collected from authorized course access.",
            "Original local source paths are preserved in each section for citation back to the local course archive.",
            "",
            *sections,
        ]
    ).strip() + "\n"
    stem = f"bundle-{group_index:03d}-{part_index:02d}-{safe_slug(chapter_title)}"
    return {"title": title, "stem": stem, "content": content}


def write_bundles(course_dir: Path, max_chars: int) -> list[dict[str, Any]]:
    output_dir = course_dir / "generated" / "notebooklm" / "bundles"
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.md"):
        old.unlink()
    bundles: list[dict[str, Any]] = []
    for record in build_bundle_records(course_dir, max_chars=max_chars):
        path = output_dir / f"{record['stem']}.md"
        path.write_text(record["content"], encoding="utf-8", newline="\n")
        bundles.append(
            {
                "title": record["title"],
                "path": rel(path, course_dir),
                "abs_path": str(path.resolve()),
                "sha256": sha256_text(record["content"]),
                "chars": len(record["content"]),
            }
        )
    return bundles


def find_records(value: Any, preferred_keys: tuple[str, ...]) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in preferred_keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        for nested in value.values():
            found = find_records(nested, preferred_keys)
            if found:
                return found
    return []


def normalize_options(raw_options: Any) -> list[dict[str, str]]:
    if isinstance(raw_options, dict):
        return [{"label": str(key), "text": clean_line(str(value))} for key, value in raw_options.items()]
    if not isinstance(raw_options, list):
        return []
    options: list[dict[str, str]] = []
    for index, option in enumerate(raw_options, start=1):
        if isinstance(option, dict):
            label = option.get("label") or option.get("letter") or option.get("key") or chr(64 + index)
            text = option.get("text") or option.get("content") or option.get("option") or option.get("value") or ""
            if not text and len(option) == 1:
                key, value = next(iter(option.items()))
                label = label or key
                text = value
        else:
            label = chr(64 + index)
            text = option
        text = clean_line(str(text))
        if text:
            options.append({"label": str(label), "text": text})
    return options


def normalize_quiz_payload(payload: Any) -> list[dict[str, Any]]:
    rows = find_records(payload, ("questions", "quiz", "items", "data"))
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        question = clean_line(str(row.get("question") or row.get("prompt") or row.get("question_text") or row.get("q") or ""))
        if not question:
            continue
        raw_options = row.get("answerOptions") or row.get("options") or row.get("choices") or row.get("answers")
        options = normalize_options(raw_options)
        correct_options: list[str] = []
        rationales: list[str] = []
        if isinstance(raw_options, list):
            for opt_index, option in enumerate(raw_options, start=1):
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label") or option.get("letter") or option.get("key") or chr(64 + opt_index))
                option_text = clean_line(str(option.get("text") or option.get("content") or option.get("option") or option.get("value") or ""))
                rationale = clean_line(str(option.get("rationale") or option.get("explanation") or ""))
                if option.get("isCorrect") or option.get("correct") is True:
                    correct_options.append(f"{label}. {option_text}" if option_text else label)
                    if rationale:
                        rationales.append(rationale)
        answer = row.get("answer") or row.get("correct_answer") or row.get("correctAnswer") or row.get("correct") or ""
        explanation = row.get("explanation") or row.get("rationale") or row.get("reason") or ""
        if correct_options:
            answer = "; ".join(correct_options)
        if rationales:
            explanation = " ".join(rationales)
        if row.get("hint"):
            hint = clean_line(str(row.get("hint")))
            explanation = (clean_line(str(explanation)) + f" Hint: {hint}").strip()
        items.append(
            {
                "concept_title": "NotebookLM Quiz",
                "question": question,
                "options": options,
                "answer": clean_line(str(answer)),
                "explanation": clean_line(str(explanation)),
                "difficulty": clean_line(str(row.get("difficulty") or "medium")).lower() or "medium",
                "source_refs": ["generated/notebooklm/quiz.json"],
                "metadata": {"source": "notebooklm", "artifact": "quiz", "index": index},
            }
        )
    return items


def normalize_flashcard_payload(payload: Any) -> list[dict[str, Any]]:
    rows = find_records(payload, ("flashcards", "cards", "items", "data"))
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        front = clean_line(str(row.get("front") or row.get("f") or row.get("term") or row.get("question") or row.get("prompt") or ""))
        back = clean_line(str(row.get("back") or row.get("b") or row.get("definition") or row.get("answer") or row.get("content") or ""))
        if not front or not back:
            continue
        items.append(
            {
                "concept_title": "NotebookLM Flashcards",
                "question": f"Explain or recall: {front}",
                "options": [],
                "answer": back,
                "explanation": back,
                "difficulty": "medium",
                "source_refs": ["generated/notebooklm/flashcards.json"],
                "metadata": {"source": "notebooklm", "artifact": "flashcards", "index": index, "front": front},
            }
        )
    return items


def write_normalized_practice(course_dir: Path, update_top_level: bool) -> int:
    nb_dir = course_dir / "generated" / "notebooklm"
    quiz_payload = read_json(nb_dir / "quiz.json", {})
    flashcard_payload = read_json(nb_dir / "flashcards.json", {})
    items = normalize_quiz_payload(quiz_payload) + normalize_flashcard_payload(flashcard_payload)
    payload = {"schema_version": 1, "updated_at": utc_now(), "practice_items": items}
    write_json(nb_dir / "practice_items.json", payload)
    if update_top_level and items:
        top_level = course_dir / "generated" / "practice_items.json"
        existing_payload = read_json(top_level, {})
        existing_rows = find_records(existing_payload, ("practice_items", "items"))
        kept = []
        for row in existing_rows:
            metadata = row.get("metadata") if isinstance(row, dict) else {}
            if isinstance(metadata, dict) and metadata.get("source") == "notebooklm":
                continue
            kept.append(row)
        write_json(top_level, {"schema_version": 1, "updated_at": utc_now(), "practice_items": kept + items})
    return len(items)


def make_base_manifest(course_dir: Path, args: argparse.Namespace, bundles: list[dict[str, Any]]) -> dict[str, Any]:
    meta = load_course_meta(course_dir)
    return {
        "schema_version": 1,
        "updated_at": utc_now(),
        "status": "dry_run" if args.dry_run else "started",
        "dry_run": bool(args.dry_run),
        "course_dir": str(course_dir),
        "course_title": meta.get("course_title") or course_dir.name,
        "language": args.language,
        "notebooklm_home": str(ensure_notebooklm_home(course_dir)),
        "profile": args.profile or "default",
        "notebook": {"id": "", "title": args.notebook_title or default_notebook_title(course_dir)},
        "bundles": bundles,
        "artifacts": {},
        "outputs": {
            "study_guide": "generated/notebooklm/study_guide.md",
            "quiz": "generated/notebooklm/quiz.json",
            "flashcards": "generated/notebooklm/flashcards.json",
            "mind_map": "generated/notebooklm/mind_map.json",
            "practice_items": "generated/notebooklm/practice_items.json",
        },
        "errors": [],
    }


def default_notebook_title(course_dir: Path) -> str:
    meta = load_course_meta(course_dir)
    return f"{NOTEBOOK_TITLE_PREFIX}: {meta.get('course_title') or course_dir.name}"


async def ensure_notebook(client: Any, title: str, manifest: dict[str, Any]) -> Any:
    existing_id = ((manifest.get("notebook") or {}).get("id") or "").strip()
    if existing_id:
        try:
            notebook = await client.notebooks.get(existing_id)
            if notebook:
                return notebook
        except Exception:
            pass
    for notebook in await client.notebooks.list():
        if object_title(notebook) == title:
            return notebook
    return await client.notebooks.create(title)


async def run_generation(client: Any, notebook_id: str, source_ids: list[str], out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    from notebooklm import QuizDifficulty, QuizQuantity, ReportFormat

    artifacts: dict[str, Any] = {}
    selected_sources = source_ids or None
    study_path = out_dir / "study_guide.md"
    quiz_path = out_dir / "quiz.json"
    flashcards_path = out_dir / "flashcards.json"
    mind_path = out_dir / "mind_map.json"

    report_status = await client.artifacts.generate_report(
        notebook_id,
        report_format=ReportFormat.STUDY_GUIDE,
        source_ids=selected_sources,
        language=args.language,
        extra_instructions="Use Simplified Chinese. Focus on final exam review priorities, common traps, and source-grounded structure.",
    )
    final = await client.artifacts.wait_for_completion(notebook_id, status_task_id(report_status), timeout=args.artifact_timeout)
    if not status_is_complete(final):
        raise RuntimeError(f"NotebookLM study guide generation did not complete: {final}")
    report_id = artifact_identifier(final) or artifact_identifier(report_status)
    await client.artifacts.download_report(notebook_id, str(study_path), artifact_id=report_id or None)
    artifacts["study_guide"] = {"artifact_id": report_id, "path": "generated/notebooklm/study_guide.md"}

    quiz_status = await client.artifacts.generate_quiz(
        notebook_id,
        source_ids=selected_sources,
        instructions="Use Simplified Chinese. Create final-exam style questions from the uploaded course bundles.",
        quantity=QuizQuantity.MORE,
        difficulty=QuizDifficulty.MEDIUM,
    )
    final = await client.artifacts.wait_for_completion(notebook_id, status_task_id(quiz_status), timeout=args.artifact_timeout)
    if not status_is_complete(final):
        raise RuntimeError(f"NotebookLM quiz generation did not complete: {final}")
    quiz_id = artifact_identifier(final) or artifact_identifier(quiz_status)
    await client.artifacts.download_quiz(notebook_id, str(quiz_path), artifact_id=quiz_id or None, output_format="json")
    artifacts["quiz"] = {"artifact_id": quiz_id, "path": "generated/notebooklm/quiz.json"}

    cards_status = await client.artifacts.generate_flashcards(
        notebook_id,
        source_ids=selected_sources,
        instructions="Use Simplified Chinese. Make concise exam-review flashcards from the uploaded course bundles.",
        quantity=QuizQuantity.MORE,
        difficulty=QuizDifficulty.MEDIUM,
    )
    final = await client.artifacts.wait_for_completion(notebook_id, status_task_id(cards_status), timeout=args.artifact_timeout)
    if not status_is_complete(final):
        raise RuntimeError(f"NotebookLM flashcards generation did not complete: {final}")
    cards_id = artifact_identifier(final) or artifact_identifier(cards_status)
    await client.artifacts.download_flashcards(notebook_id, str(flashcards_path), artifact_id=cards_id or None, output_format="json")
    artifacts["flashcards"] = {"artifact_id": cards_id, "path": "generated/notebooklm/flashcards.json"}

    mind_map = await client.artifacts.generate_mind_map(notebook_id, source_ids=selected_sources)
    write_json(mind_path, mind_map)
    artifacts["mind_map"] = {"artifact_id": "", "path": "generated/notebooklm/mind_map.json"}
    return artifacts


async def run_live(course_dir: Path, args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        from notebooklm import NotebookLMClient
    except Exception as exc:
        raise RuntimeError("notebooklm-py is not installed. Run bootstrap_env.ps1 or pip install notebooklm-py[browser]==0.5.0.") from exc

    out_dir = course_dir / "generated" / "notebooklm"
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = read_json(out_dir / "manifest.json", {})
    previous_bundles = {item.get("sha256"): item for item in previous.get("bundles", []) if isinstance(item, dict)}
    title = args.notebook_title or default_notebook_title(course_dir)
    manifest["notebook"] = {"id": ((previous.get("notebook") or {}).get("id") or ""), "title": title}

    async with NotebookLMClient.from_storage(profile=args.profile or None) as client:
        notebook = await ensure_notebook(client, title, manifest)
        notebook_id = object_id(notebook)
        if not notebook_id:
            raise RuntimeError("NotebookLM returned a notebook without an id")
        manifest["notebook"] = {"id": notebook_id, "title": object_title(notebook) or title}
        source_ids: list[str] = []
        updated_bundles: list[dict[str, Any]] = []
        for bundle in manifest["bundles"]:
            record = dict(bundle)
            previous_record = previous_bundles.get(bundle["sha256"])
            if previous_record and previous_record.get("source_id"):
                record["source_id"] = previous_record["source_id"]
                record["uploaded"] = False
                record["skipped_reason"] = "same bundle sha256 already uploaded"
                source_ids.append(record["source_id"])
                updated_bundles.append(record)
                continue
            source = await client.sources.add_file(
                notebook_id,
                Path(bundle["abs_path"]),
                wait=False,
                title=bundle["title"],
            )
            source_id = object_id(source)
            if source_id:
                await client.sources.wait_until_ready(notebook_id, source_id, timeout=args.source_timeout)
            record["source_id"] = source_id
            record["uploaded"] = True
            source_ids.append(source_id)
            updated_bundles.append(record)
        manifest["bundles"] = updated_bundles
        manifest["artifacts"] = await run_generation(client, notebook_id, source_ids, out_dir, args)
    normalized_count = write_normalized_practice(course_dir, update_top_level=True)
    manifest["normalized"] = {"practice_items": normalized_count}
    manifest["status"] = "completed"
    manifest["updated_at"] = utc_now()
    return manifest


def run_sync(course_dir: Path, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    ensure_notebooklm_home(course_dir)
    bundles = write_bundles(course_dir, max_chars=args.max_bundle_chars)
    manifest = make_base_manifest(course_dir, args, bundles)
    manifest_path = course_dir / "generated" / "notebooklm" / "manifest.json"
    if args.dry_run:
        normalized_count = write_normalized_practice(course_dir, update_top_level=False)
        manifest["normalized"] = {"practice_items": normalized_count}
        manifest["status"] = "dry_run"
        write_json(manifest_path, manifest)
        return 0, manifest
    try:
        manifest = asyncio.run(run_live(course_dir, args, manifest))
        write_json(manifest_path, manifest)
        return 0, manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["updated_at"] = utc_now()
        manifest["errors"].append({"message": str(exc), "traceback": traceback.format_exc()})
        write_json(manifest_path, manifest)
        return (1 if args.strict else 0), manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Optionally sync converted course Markdown to NotebookLM and import generated study artifacts.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--profile", default="", help="NotebookLM profile name. Empty uses the active/default profile.")
    parser.add_argument("--notebook-title", default="", help="Override NotebookLM notebook title.")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="NotebookLM artifact language, default zh-CN.")
    parser.add_argument("--dry-run", action="store_true", help="Build local bundles and manifest without importing notebooklm or contacting Google.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when NotebookLM sync fails.")
    parser.add_argument("--max-bundle-chars", type=int, default=DEFAULT_MAX_BUNDLE_CHARS)
    parser.add_argument("--source-timeout", type=float, default=240.0)
    parser.add_argument("--artifact-timeout", type=float, default=420.0)
    args = parser.parse_args()

    course_dir = Path(args.course).expanduser().resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    exit_code, manifest = run_sync(course_dir, args)
    print(f"notebooklm_status={manifest.get('status')} course={course_dir}")
    print(f"manifest={course_dir / 'generated' / 'notebooklm' / 'manifest.json'}")
    if manifest.get("errors"):
        print("notebooklm_error=" + str(manifest["errors"][-1].get("message", "")))
        print("hint=Run `notebooklm login` and `notebooklm auth check --test --json`, or rerun with --dry-run for local-only validation.")
    print(f"bundles={len(manifest.get('bundles', []))} practice_items={manifest.get('normalized', {}).get('practice_items', 0)}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
