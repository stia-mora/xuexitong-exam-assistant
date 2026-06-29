"""Prepare compact Markdown context and a source-grounded review seed for Codex."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from chapter_utils import chapter_sort_key, infer_chapter


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def read_text(path: Path, limit: int = 0) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] if limit and len(text) > limit else text


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_headings(markdown: str, max_items: int = 120) -> list[str]:
    headings: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,3})\s+(.+)", line.strip())
        if not match:
            continue
        title = clean_line(re.sub(r"[`*_#]", "", match.group(2)))
        if len(title) < 2 or title.lower().startswith(("source file", "source sha")):
            continue
        if title not in headings:
            headings.append(title)
        if len(headings) >= max_items:
            break
    return headings


def extract_representative_paragraphs(markdown: str, max_items: int = 24, min_len: int = 40) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in markdown.splitlines():
        stripped = clean_line(line)
        if not stripped or stripped.startswith("!") or stripped.startswith("#") or stripped.startswith("Source "):
            if buffer:
                para = clean_line(" ".join(buffer))
                if len(para) >= min_len and para not in paragraphs:
                    paragraphs.append(para)
                buffer = []
            continue
        buffer.append(stripped)
        if len(" ".join(buffer)) > 240:
            para = clean_line(" ".join(buffer))
            if para not in paragraphs:
                paragraphs.append(para)
            buffer = []
        if len(paragraphs) >= max_items:
            break
    if buffer and len(paragraphs) < max_items:
        para = clean_line(" ".join(buffer))
        if len(para) >= min_len and para not in paragraphs:
            paragraphs.append(para)
    return paragraphs[:max_items]


def db_counts(db_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not db_path.exists():
        return counts
    conn = sqlite3.connect(db_path)
    try:
        for table in ("documents", "assignments", "questions", "concepts", "practice_items"):
            counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    return counts


def load_questions(course_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((course_dir / "assignments_md").glob("*.questions.json")):
        payload = read_json(path, {})
        title = payload.get("title") or path.stem
        source_url = payload.get("source_url") or ""
        for item in payload.get("questions", []):
            question = clean_line(item.get("question") or "")
            if not question:
                continue
            rows.append({
                "assignment": title,
                "question": question,
                "options": item.get("options") or [],
                "answer": item.get("answer") or "",
                "explanation": item.get("explanation") or "",
                "source_url": source_url,
            })
    return rows


def render_question(item: dict[str, Any], index: int) -> list[str]:
    lines = [f"{index}. {item['question']}"]
    for option in item.get("options", []):
        if isinstance(option, dict):
            label = option.get("label") or ""
            text = option.get("text") or ""
            lines.append(f"   - {label}. {text}" if label else f"   - {text}")
        else:
            lines.append(f"   - {option}")
    if item.get("answer"):
        lines.append(f"   - 答案：{item['answer']}")
    if item.get("explanation"):
        lines.append(f"   - 解析：{item['explanation']}")
    lines.append(f"   - 来源：{item.get('assignment', '')}")
    return lines


def build_context(course_dir: Path, max_materials: int = 12) -> tuple[str, str]:
    meta = read_json(course_dir / "course_meta.json", {})
    counts = db_counts(course_dir / "course.db")
    title = meta.get("course_title") or course_dir.name
    material_sections: list[str] = []
    all_headings: list[str] = []
    chapter_map: dict[str, dict[str, Any]] = {}
    material_paths = sorted((course_dir / "materials_md").glob("*.md"))

    material_cache: list[dict[str, Any]] = []
    for md_path in material_paths:
        markdown = read_text(md_path)
        headings = extract_headings(markdown)
        chapter = infer_chapter(title=headings[0] if headings else md_path.stem, path=md_path.relative_to(course_dir).as_posix(), headings=headings)
        key = chapter.get("key") or "chapter-unknown"
        chapter_map.setdefault(key, {"chapter": chapter, "materials": [], "headings": []})
        chapter_map[key]["materials"].append(md_path.relative_to(course_dir).as_posix())
        for heading in headings[:18]:
            if heading not in chapter_map[key]["headings"]:
                chapter_map[key]["headings"].append(heading)
            if heading not in all_headings:
                all_headings.append(heading)
        material_cache.append({"path": md_path, "markdown": markdown, "headings": headings, "chapter": chapter})

    for record in material_cache[:max_materials]:
        md_path = record["path"]
        markdown = record["markdown"]
        headings = record["headings"]
        paragraphs = extract_representative_paragraphs(markdown)
        material_sections.append("\n".join([
            f"### {md_path.stem}",
            "",
            f"章节：{record['chapter'].get('title')}",
            "",
            "核心标题：",
            *[f"- {heading}" for heading in headings[:30]],
            "",
            "代表性正文片段：",
            *[f"- {para}" for para in paragraphs[:12]],
        ]))

    chapter_sections: list[str] = []
    for entry in sorted(chapter_map.values(), key=lambda item: chapter_sort_key(item["chapter"])):
        chapter = entry["chapter"]
        chapter_sections.extend([f"### {chapter.get('title')}", ""])
        chapter_sections.append("资料文件：")
        chapter_sections.extend([f"- `{path}`" for path in entry["materials"][:30]])
        if len(entry["materials"]) > 30:
            chapter_sections.append(f"- 还有 {len(entry['materials']) - 30} 个资料文件未展开。")
        chapter_sections.append("")
        chapter_sections.append("知识点标题：")
        chapter_sections.extend([f"- {heading}" for heading in entry["headings"][:40]] or ["- 暂无可抽取标题。"])
        chapter_sections.append("")

    questions = load_questions(course_dir)
    grouped_questions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in questions:
        grouped_questions[item["assignment"]].append(item)

    context_lines = [
        f"# {title} Codex 复习上下文包",
        "",
        "## 课程元信息",
        f"- 课程目录：`{course_dir}`",
        f"- 来源页面：{meta.get('source_url', '')}",
        f"- 文档数：{counts.get('documents', 0)}",
        f"- 作业数：{counts.get('assignments', 0)}",
        f"- 题目数：{counts.get('questions', len(questions))}",
        "",
        "## 按章节材料索引",
        *(chapter_sections or ["暂无可按章节索引的材料 Markdown。"]),
        "",
        "## 材料压缩摘要",
        *(material_sections or ["暂无材料 Markdown。"]),
        "",
        "## 作业题目",
    ]
    q_index = 1
    for assignment, rows in grouped_questions.items():
        context_lines.extend(["", f"### {assignment}"])
        for item in rows:
            context_lines.extend(render_question(item, q_index))
            q_index += 1

    review_lines = [
        f"# {title} 期末复习重点与练习题",
        "",
        "## 一、按章节划分的优先复习知识点",
    ]
    if chapter_map:
        for entry in sorted(chapter_map.values(), key=lambda item: chapter_sort_key(item["chapter"])):
            review_lines.extend(["", f"### {entry['chapter'].get('title')}"])
            for index, heading in enumerate(entry["headings"][:30], start=1):
                review_lines.append(f"{index}. {heading}")
    else:
        review_lines.append("暂无可用课件标题，请先运行资料转换。")
    review_lines.extend(["", "## 二、作业原题整理"])
    for index, item in enumerate(questions, start=1):
        review_lines.extend(render_question(item, index))
    review_lines.extend([
        "",
        "## 三、Codex 精修提示",
        "请先基于 `generated/codex_context.md`、`generated/teacher_knowledge_seeds.json`、`materials_md/` 和可选 `input/teacher_focus.md` 提炼 `generated/knowledge_bank.json`；再逐题审核 `generated/question_candidates.json`：原题优先复用，缺解析补解析，缺答案可推断但需标注置信度；不足 150 道审核通过题时，只能按已审核知识点补题。每条 approved audit 和最终题都必须绑定 `knowledge_ids`，最终 HTML 只能由已审核 `knowledge_bank.json` 与 `question_bank.json` 渲染。",
    ])
    return "\n".join(context_lines).strip() + "\n", "\n".join(review_lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare compact context and review seed from course Markdown/DB.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--max-materials", type=int, default=12)
    args = parser.parse_args()
    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    context_md, review_md = build_context(course_dir, max_materials=args.max_materials)
    generated = course_dir / "generated"
    output = course_dir / "output"
    generated.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    context_path = generated / "codex_context.md"
    review_path = output / "final_review_seed.md"
    context_path.write_text(context_md, encoding="utf-8", newline="\n")
    review_path.write_text(review_md, encoding="utf-8", newline="\n")
    print(f"context={context_path}")
    print(f"review_seed={review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
