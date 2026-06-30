"""Build the local SQLite course database from Markdown, assignments, and generated practice JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import chapter_sort_key, infer_chapter


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path, limit: int = 0) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] if limit and len(text) > limit else text


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS course_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_files (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            source_url TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_file_id TEXT,
            title TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            markdown_path TEXT NOT NULL,
            text_excerpt TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_file_id) REFERENCES source_files(id)
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            markdown_path TEXT NOT NULL,
            source_url TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            assignment_id TEXT,
            question_text TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            answer TEXT,
            explanation TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(assignment_id) REFERENCES assignments(id)
        );
        CREATE TABLE IF NOT EXISTS concepts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS practice_items (
            id TEXT PRIMARY KEY,
            concept_id TEXT,
            concept_title TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            answer TEXT,
            explanation TEXT,
            difficulty TEXT NOT NULL DEFAULT 'medium',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(concept_id) REFERENCES concepts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
        CREATE INDEX IF NOT EXISTS idx_questions_assignment ON questions(assignment_id);
        CREATE INDEX IF NOT EXISTS idx_practice_concept ON practice_items(concept_title);
        """
    )


def clear_data(conn: sqlite3.Connection) -> None:
    for table in ("practice_items", "concepts", "questions", "assignments", "documents", "source_files", "course_meta"):
        conn.execute(f"DELETE FROM {table}")


def first_heading(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)", line.strip())
        if match:
            return match.group(1).strip()[:200]
    return fallback


def extract_headings(markdown: str, limit: int = 80) -> list[str]:
    headings: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^#{1,3}\s+(.+)", line.strip())
        if not match:
            continue
        title = re.sub(r"[`*_#]", "", match.group(1)).strip()
        if len(title) < 2 or title.lower().startswith("source file"):
            continue
        if title not in headings:
            headings.append(title[:160])
        if len(headings) >= limit:
            break
    return headings


def load_generated_list(course_dir: Path, filename: str, key: str) -> list[dict[str, Any]]:
    path = course_dir / "generated" / filename
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        values = data.get(key) or data.get("items") or []
        return [item for item in values if isinstance(item, dict)]
    return []


def load_course_meta(course_dir: Path) -> dict[str, Any]:
    path = course_dir / "course_meta.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "course_title": course_dir.name,
        "course_slug": course_dir.name,
        "course_dir": str(course_dir),
        "source_url": "",
        "collected_at": "",
    }


def insert_course_meta(conn: sqlite3.Connection, course_dir: Path) -> dict[str, Any]:
    meta = load_course_meta(course_dir)
    meta.setdefault("course_title", course_dir.name)
    meta.setdefault("course_slug", course_dir.name)
    for key, value in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO course_meta (key, value, updated_at) VALUES (?, ?, ?)",
            (str(key), json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value), utc_now()),
        )
    return meta


def update_courses_catalog(course_dir: Path, meta: dict[str, Any], counts: dict[str, int]) -> None:
    catalog_path = course_dir.parent / "courses_index.json"
    existing: list[dict[str, Any]] = []
    if catalog_path.exists():
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                existing = [item for item in payload.get("courses", []) if isinstance(item, dict)]
            elif isinstance(payload, list):
                existing = [item for item in payload if isinstance(item, dict)]
        except Exception:
            existing = []
    slug = meta.get("course_slug") or course_dir.name
    record = {
        "course_slug": slug,
        "course_title": meta.get("course_title") or course_dir.name,
        "course_dir": course_dir.as_posix(),
        "source_url": meta.get("source_url") or "",
        "updated_at": utc_now(),
        "counts": counts,
    }
    kept = [item for item in existing if item.get("course_slug") != slug and item.get("course_dir") != course_dir.as_posix()]
    kept.append(record)
    kept.sort(key=lambda item: (item.get("course_title") or "").casefold())
    catalog_path.write_text(json.dumps({"updated_at": utc_now(), "courses": kept}, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def insert_source(conn: sqlite3.Connection, course_dir: Path, path: Path, kind: str, title: str, source_url: str = "", metadata: dict[str, Any] | None = None) -> str:
    path_rel = rel(path, course_dir)
    source_hash = sha256_file(path) if path.exists() and path.is_file() else ""
    source_id = sha1_text(f"{kind}\n{path_rel}\n{source_hash}")[:24]
    conn.execute(
        """
        INSERT OR REPLACE INTO source_files
        (id, kind, title, path, sha256, source_url, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, kind, title, path_rel, source_hash, source_url, json_dumps(metadata or {}), utc_now()),
    )
    return source_id


def insert_documents(conn: sqlite3.Connection, course_dir: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for md_path in sorted((course_dir / "materials_md").glob("*.md")):
        markdown = read_text(md_path)
        title = first_heading(markdown, md_path.stem)
        path_rel = rel(md_path, course_dir)
        headings = extract_headings(markdown)
        chapter = infer_chapter(title=title, path=path_rel, headings=headings)
        source_id = insert_source(conn, course_dir, md_path, "material_markdown", title, metadata={"chapter": chapter})
        doc_id = sha1_text(f"document\n{path_rel}")[:24]
        conn.execute(
            """
            INSERT OR REPLACE INTO documents
            (id, source_file_id, title, doc_type, markdown_path, text_excerpt, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, source_id, title, "material", path_rel, markdown[:1200], json_dumps({"chapter": chapter}), utc_now()),
        )
        docs.append({"id": doc_id, "title": title, "path": path_rel, "headings": headings, "chapter": chapter})
    return docs


def load_question_payload(md_path: Path) -> dict[str, Any]:
    qpath = md_path.with_suffix(".questions.json")
    if qpath.exists():
        return json.loads(qpath.read_text(encoding="utf-8"))
    return {"questions": []}


def iter_assessment_markdown(course_dir: Path) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    rows.extend((path, "xxt_assignment") for path in sorted((course_dir / "assignments_md").glob("*.md")))
    rows.extend((path, "xxt_exam") for path in sorted((course_dir / "exams_md").glob("*.md")))
    return rows


def insert_assignments(conn: sqlite3.Connection, course_dir: Path) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for md_path, source_kind in iter_assessment_markdown(course_dir):
        markdown = read_text(md_path)
        payload = load_question_payload(md_path)
        title = payload.get("title") or first_heading(markdown, md_path.stem)
        source_url = payload.get("source_url") or ""
        assignment_id = sha1_text(f"{source_kind}\n{rel(md_path, course_dir)}")[:24]
        conn.execute(
            """
            INSERT OR REPLACE INTO assignments
            (id, title, markdown_path, source_url, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                title,
                rel(md_path, course_dir),
                source_url,
                json_dumps({"question_count": len(payload.get("questions", [])), "source_kind": source_kind}),
                utc_now(),
            ),
        )
        insert_source(conn, course_dir, md_path, "exam_markdown" if source_kind == "xxt_exam" else "assignment_markdown", title, source_url)
        for index, question in enumerate(payload.get("questions", []), start=1):
            q_text = question.get("question") or ""
            if not q_text.strip():
                continue
            question_id = sha1_text(f"question\n{assignment_id}\n{index}\n{q_text}")[:24]
            conn.execute(
                """
                INSERT OR REPLACE INTO questions
                (id, assignment_id, question_text, options_json, answer, explanation, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    assignment_id,
                    q_text,
                    json_dumps(question.get("options") or []),
                    question.get("answer") or "",
                    question.get("explanation") or "",
                    json_dumps({"type": question.get("type") or "unknown", "source_kind": source_kind}),
                    utc_now(),
                ),
            )
            collected.append(
                {
                    "assignment_id": assignment_id,
                    "assignment_title": title,
                    "source_kind": source_kind,
                    "question": question,
                    "path": rel(md_path, course_dir),
                }
            )
    return collected


def build_fallback_concepts(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    concepts: list[dict[str, Any]] = []
    seen: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapters: dict[str, dict[str, Any]] = {}
    for doc in docs:
        chapter = doc.get("chapter") if isinstance(doc.get("chapter"), dict) else infer_chapter(title=doc.get("title", ""), path=doc.get("path", ""))
        key = chapter.get("key") or "chapter-unknown"
        grouped[key].append(doc)
        chapters[key] = chapter

    for key, chapter in sorted(chapters.items(), key=lambda item: chapter_sort_key(item[1])):
        chapter_count = 0
        for doc in grouped.get(key, []):
            for heading in doc.get("headings", [])[:18]:
                dedupe_key = f"{key}\n{heading}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                concepts.append(
                    {
                        "title": heading,
                        "summary": f"Knowledge point from course material {doc['title']}; review it against the source Markdown.",
                        "source_refs": [doc["path"]],
                        "metadata": {"generated_by": "heading_fallback", "chapter": chapter},
                    }
                )
                chapter_count += 1
                if chapter_count >= 24:
                    break
            if chapter_count >= 24:
                break
            if len(concepts) >= 160:
                return concepts
    if not concepts:
        concepts.append(
            {
                "title": "Comprehensive Review",
                "summary": "Review across the collected materials and assignment questions.",
                "source_refs": [],
                "metadata": {"generated_by": "default_fallback", "chapter": infer_chapter()},
            }
        )
    return concepts


def infer_concept_chapter(item: dict[str, Any], docs_by_path: dict[str, dict[str, Any]], title: str) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    chapter = metadata.get("chapter")
    if isinstance(chapter, dict) and chapter.get("title"):
        return chapter
    refs = item.get("source_refs") or item.get("sources") or []
    if isinstance(refs, str):
        refs = [refs]
    for ref in refs:
        ref_text = str(ref).replace("\\", "/")
        doc = docs_by_path.get(ref_text)
        if doc and isinstance(doc.get("chapter"), dict):
            return doc["chapter"]
        for path, candidate in docs_by_path.items():
            if ref_text and (ref_text in path or path in ref_text) and isinstance(candidate.get("chapter"), dict):
                return candidate["chapter"]
    return infer_chapter(title=title, path=str(refs[0]) if refs else "")


def with_chapter_metadata(item: dict[str, Any], docs_by_path: dict[str, dict[str, Any]], title: str) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = dict(metadata)
    chapter = infer_concept_chapter(item, docs_by_path, title)
    metadata["chapter"] = chapter
    metadata["chapter_title"] = chapter.get("title")
    metadata["chapter_order"] = chapter.get("order")
    return metadata


def insert_concepts(conn: sqlite3.Connection, course_dir: Path, docs: list[dict[str, Any]]) -> dict[str, str]:
    generated = load_generated_list(course_dir, "concepts.json", "concepts")
    concepts = generated or build_fallback_concepts(docs)
    docs_by_path = {doc.get("path", ""): doc for doc in docs}
    title_to_id: dict[str, str] = {}
    for item in concepts:
        title = (item.get("title") or "Comprehensive Review").strip()[:200]
        summary = (item.get("summary") or "").strip() or "Summary pending."
        metadata = with_chapter_metadata(item, docs_by_path, title)
        metadata.setdefault("source", "generated" if generated else "fallback")
        concept_id = sha1_text(f"concept\n{title}")[:24]
        conn.execute(
            """
            INSERT OR REPLACE INTO concepts
            (id, title, summary, source_refs_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                concept_id,
                title,
                summary,
                json_dumps(item.get("source_refs") or item.get("sources") or []),
                json_dumps(metadata),
                utc_now(),
            ),
        )
        title_to_id[title] = concept_id
    return title_to_id


def insert_practice_items(conn: sqlite3.Connection, course_dir: Path, title_to_id: dict[str, str], questions: list[dict[str, Any]]) -> None:
    generated = load_generated_list(course_dir, "practice_items.json", "practice_items")
    if generated:
        items = generated
    else:
        items = []
        for record in questions:
            question = record["question"]
            items.append(
                {
                    "concept_title": record.get("assignment_title") or "Assignment Questions",
                    "question": question.get("question") or "",
                    "options": question.get("options") or [],
                    "answer": question.get("answer") or "",
                    "explanation": question.get("explanation") or "",
                    "difficulty": "medium",
                    "source_refs": [record.get("path", "")],
                    "metadata": {"generated_by": "assignment_fallback"},
                }
            )
        if not items:
            for title in list(title_to_id)[:40]:
                items.append(
                    {
                        "concept_title": title,
                        "question": f"Explain the core concept, common exam angle, and typical mistakes for: {title}.",
                        "options": [],
                        "answer": "Candidate only; final answer must be written during LLM per-question audit.",
                        "explanation": "This is a candidate review prompt generated from source headings. It must be audited before entering the final question bank.",
                        "difficulty": "medium",
                        "source_refs": [],
                        "metadata": {"generated_by": "concept_candidate"},
                    }
                )
    for index, item in enumerate(items, start=1):
        concept_title = (item.get("concept_title") or item.get("concept") or "Comprehensive Review").strip()[:200]
        concept_id = title_to_id.get(concept_title)
        question_text = (item.get("question") or "").strip()
        if not question_text:
            continue
        item_id = sha1_text(f"practice\n{index}\n{concept_title}\n{question_text}")[:24]
        conn.execute(
            """
            INSERT OR REPLACE INTO practice_items
            (id, concept_id, concept_title, question, options_json, answer, explanation, difficulty, source_refs_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                concept_id,
                concept_title,
                question_text,
                json_dumps(item.get("options") or []),
                item.get("answer") or "",
                item.get("explanation") or "",
                item.get("difficulty") or "medium",
                json_dumps(item.get("source_refs") or item.get("sources") or []),
                json_dumps(item.get("metadata") or {"source": "generated" if generated else "fallback"}),
                utc_now(),
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SQLite course.db from course Markdown and generated JSON.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    db_path = course_dir / "course.db"
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        clear_data(conn)
        meta = insert_course_meta(conn, course_dir)
        docs = insert_documents(conn, course_dir)
        questions = insert_assignments(conn, course_dir)
        title_to_id = insert_concepts(conn, course_dir, docs)
        insert_practice_items(conn, course_dir, title_to_id, questions)
        counts = {
            "documents": len(docs),
            "questions": len(questions),
            "concepts": len(title_to_id),
        }
        conn.commit()
        update_courses_catalog(course_dir, meta, counts)
    finally:
        conn.close()
    print(f"db={db_path} documents={len(docs)} questions={len(questions)} concepts={len(title_to_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
