"""Render a local static fallback practice page from course.db."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, chapter_sort_key


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def load_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except Exception:
        return fallback


def read_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def fetch_meta(conn: sqlite3.Connection, course_dir: Path) -> dict[str, str]:
    meta = {"course_title": course_dir.name, "course_slug": course_dir.name}
    try:
        rows = conn.execute("SELECT key, value FROM course_meta").fetchall()
    except sqlite3.Error:
        return meta
    for row in rows:
        meta[row["key"]] = row["value"]
    return meta


def fetch_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("documents", "assignments", "questions", "concepts", "practice_items"):
        try:
            counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = 0
    return counts


def fetch_data(db_path: Path, course_dir: Path) -> tuple[dict[str, str], dict[str, int], list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        meta = fetch_meta(conn, course_dir)
        counts = fetch_counts(conn)
        concepts = [dict(row) for row in conn.execute("SELECT * FROM concepts ORDER BY title")]
        items = [dict(row) for row in conn.execute("SELECT * FROM practice_items ORDER BY concept_title, difficulty, id")]
    finally:
        conn.close()
    for item in items:
        item["options"] = load_json(item.get("options_json", "[]"), [])
        item["source_refs"] = load_json(item.get("source_refs_json", "[]"), [])
    for concept in concepts:
        concept["source_refs"] = load_json(concept.get("source_refs_json", "[]"), [])
        concept["metadata"] = load_json(concept.get("metadata_json", "{}"), {})
    return meta, counts, concepts, items


def render_options(options: Any) -> str:
    if not options:
        return ""
    rows: list[str] = ['<ol class="options">']
    iterable = [{"label": key, "text": value} for key, value in options.items()] if isinstance(options, dict) else options
    for index, option in enumerate(iterable, start=1):
        if isinstance(option, dict):
            label = option.get("label") or chr(64 + index)
            text = option.get("text") or option.get("content") or ""
        else:
            label = chr(64 + index)
            text = str(option)
        rows.append(f'<li><span class="option-label">{esc(label)}</span><span>{esc(text)}</span></li>')
    rows.append("</ol>")
    return "\n".join(rows)


def render_sources(source_refs: list[str]) -> str:
    if not source_refs:
        return ""
    rows = ['<div class="sources" aria-label="来源">']
    for ref in source_refs[:4]:
        rows.append(f'<code>{esc(ref)}</code>')
    rows.append("</div>")
    return "\n".join(rows)


def concept_anchor(title: str) -> str:
    anchor = "".join(ch.lower() if ch.isalnum() else "-" for ch in title).strip("-")[:80]
    return "concept-" + (anchor or "review")


def difficulty_label(value: str) -> str:
    return {"easy": "基础", "medium": "中等", "hard": "提高"}.get((value or "medium").lower(), value or "中等")


def render_notebooklm_links(course_dir: Path) -> str:
    nb_dir = course_dir / "generated" / "notebooklm"
    manifest = read_json_file(nb_dir / "manifest.json", {})
    files = [
        ("Study guide", nb_dir / "study_guide.md"),
        ("Quiz JSON", nb_dir / "quiz.json"),
        ("Flashcards JSON", nb_dir / "flashcards.json"),
        ("Mind map JSON", nb_dir / "mind_map.json"),
        ("Sync manifest", nb_dir / "manifest.json"),
    ]
    existing = [(label, path) for label, path in files if path.exists()]
    if not existing and not manifest:
        return ""
    status = manifest.get("status", "unknown") if isinstance(manifest, dict) else "unknown"
    normalized = manifest.get("normalized", {}) if isinstance(manifest, dict) else {}
    item_count = normalized.get("practice_items", 0) if isinstance(normalized, dict) else 0
    rows = [
        '<section class="outline-section" id="notebooklm-artifacts">',
        '<div class="section-heading"><div><p class="eyebrow">NotebookLM</p><h2>Optional study artifacts</h2></div>'
        f'<span class="count">{esc(status)}</span></div>',
        f'<p class="summary">Imported NotebookLM practice items: {esc(item_count)}. These files are local copies of generated artifacts.</p>',
        '<div class="outline-list">',
    ]
    for label, path in existing:
        try:
            href = path.resolve().as_uri()
        except ValueError:
            href = path.as_posix()
        rows.append(f'<div><strong>{esc(label)}</strong><div class="sources"><a href="{esc(href)}">{esc(path.name)}</a></div></div>')
    if not existing:
        rows.append('<div><strong>No files yet</strong><div class="sources"><code>Run with --notebooklm or --notebooklm-dry-run</code></div></div>')
    rows.append('</div></section>')
    return "".join(rows)


def build_html(course_dir: Path, meta: dict[str, str], counts: dict[str, int], concepts: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    by_concept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_concept[item.get("concept_title") or "综合复习"].append(item)

    concept_lookup = {concept["title"]: concept for concept in concepts}

    def chapter_for_title(title: str) -> dict[str, Any]:
        metadata = concept_lookup.get(title, {}).get("metadata") or {}
        if isinstance(metadata, dict):
            chapter = metadata.get("chapter")
            if isinstance(chapter, dict) and chapter.get("title"):
                return chapter
            if metadata.get("chapter_title"):
                return {"title": metadata.get("chapter_title"), "order": metadata.get("chapter_order", 999999)}
        return {"title": UNKNOWN_CHAPTER, "order": 999999}

    active_titles = [title for title, rows in by_concept.items() if rows]
    for concept in concepts:
        title = concept.get("title") or ""
        if title in by_concept and title not in active_titles:
            active_titles.append(title)
    active_titles.sort(key=lambda title: (*chapter_sort_key(chapter_for_title(title)), title.casefold()))
    outline_titles = [concept.get("title") or "" for concept in concepts if (concept.get("title") or "") not in active_titles]
    outline_titles.sort(key=lambda title: (*chapter_sort_key(chapter_for_title(title)), title.casefold()))

    nav_rows = []
    last_chapter = None
    for title in active_titles:
        chapter = chapter_for_title(title)
        chapter_title = chapter.get("title") or UNKNOWN_CHAPTER
        if chapter_title != last_chapter:
            nav_rows.append(f'<span class="nav-chapter">{esc(chapter_title)}</span>')
            last_chapter = chapter_title
        nav_rows.append(f'<a href="#{esc(concept_anchor(title))}"><span>{esc(title)}</span><strong>{len(by_concept.get(title, []))}</strong></a>')

    sections: list[str] = []
    for title in active_titles:
        rows = by_concept.get(title, [])
        concept = concept_lookup.get(title, {})
        sections.append(f'<section class="concept-section" id="{esc(concept_anchor(title))}" data-concept="{esc(title)}">')
        sections.append('<div class="section-heading">')
        sections.append(f'<div><p class="eyebrow">知识点</p><h2>{esc(title)}</h2></div>')
        sections.append(f'<span class="count">{len(rows)} 题</span>')
        sections.append('</div>')
        if concept.get("summary"):
            sections.append(f'<p class="summary">{esc(concept.get("summary"))}</p>')
        sections.append(render_sources(concept.get("source_refs") or []))
        for item in rows:
            item_id = esc(item.get("id"))
            difficulty = esc(item.get("difficulty") or "medium")
            searchable = esc(" ".join([title, item.get("question") or "", item.get("answer") or "", item.get("explanation") or ""]))
            sections.append(f'<article class="practice-item" data-item-id="{item_id}" data-difficulty="{difficulty}" data-search="{searchable}">')
            sections.append('<div class="item-topline">')
            sections.append(f'<span class="difficulty">{esc(difficulty_label(difficulty))}</span>')
            sections.append('<button class="flag-button" type="button" aria-pressed="false">标记</button>')
            sections.append('</div>')
            sections.append(f'<h3>{esc(item.get("question"))}</h3>')
            sections.append(render_options(item.get("options")))
            answer = item.get("answer") or "待补充"
            explanation = item.get("explanation") or "暂无解析"
            sections.append('<details class="answer"><summary>答案与解析</summary>')
            sections.append(f'<p><strong>答案：</strong>{esc(answer)}</p>')
            sections.append(f'<p><strong>解析：</strong>{esc(explanation)}</p>')
            sections.append('</details>')
            sections.append(render_sources(item.get("source_refs") or []))
            sections.append('</article>')
        sections.append('</section>')

    outline = []
    if outline_titles:
        outline.append('<section class="outline-section" id="source-outline">')
        outline.append('<div class="section-heading"><div><p class="eyebrow">资料大纲</p><h2>课件标题索引</h2></div><span class="count">' + str(len(outline_titles)) + ' 项</span></div>')
        outline.append('<div class="outline-list">')
        for title in outline_titles[:160]:
            concept = concept_lookup.get(title, {})
            refs = concept.get("source_refs") or []
            outline.append(f'<div><strong>{esc(title)}</strong>{render_sources(refs)}</div>')
        outline.append('</div></section>')

    course_title = meta.get("course_title") or course_dir.name
    source_url = meta.get("source_url") or ""
    css = r'''
:root {
  color-scheme: light;
  --ink: #182022;
  --muted: #5d6b70;
  --line: #dbe2e0;
  --paper: #f4f6f3;
  --panel: #ffffff;
  --teal: #176b63;
  --blue: #355f9d;
  --amber: #a86416;
  --rose: #a43f55;
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; background: var(--paper); color: var(--ink); letter-spacing: 0; }
.app { display: grid; grid-template-columns: 286px minmax(0, 1fr); min-height: 100vh; }
aside { position: sticky; top: 0; height: 100vh; overflow: auto; padding: 24px 16px; border-right: 1px solid var(--line); background: #fff; }
.brand h1 { margin: 0 0 8px; font-size: 22px; line-height: 1.25; }
.brand p { margin: 0 0 18px; color: var(--muted); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.side-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 18px; }
.side-stats span { padding: 9px 10px; border: 1px solid var(--line); border-radius: 6px; background: #f9faf7; font-size: 12px; color: var(--muted); }
.side-stats strong { display: block; color: var(--ink); font-size: 17px; }
nav { display: grid; gap: 5px; }
nav a { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; min-height: 36px; padding: 8px 10px; color: var(--ink); text-decoration: none; border-radius: 6px; }
.nav-chapter { margin: 12px 8px 4px; color: var(--teal); font-size: 12px; font-weight: 700; }
nav a:hover, nav a:focus { background: #eaf3ef; outline: none; }
nav strong { color: var(--teal); font-size: 12px; }
main { width: min(1180px, 100%); padding: 28px 32px 70px; }
.topbar { display: grid; gap: 16px; margin-bottom: 20px; }
.topbar h2 { margin: 0; font-size: 28px; line-height: 1.2; }
.meta-row, .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.badge { border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 6px 10px; color: var(--muted); font-size: 12px; }
.search { min-width: min(420px, 100%); min-height: 38px; border: 1px solid var(--line); border-radius: 6px; padding: 7px 10px; background: #fff; color: var(--ink); font: inherit; }
.filter-button { min-height: 38px; border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 6px; padding: 7px 12px; cursor: pointer; }
.filter-button[aria-pressed="true"] { background: var(--teal); color: #fff; border-color: var(--teal); }
.concept-section, .outline-section { padding: 22px 0 10px; border-top: 1px solid var(--line); }
.section-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.eyebrow { margin: 0 0 4px; color: var(--teal); font-size: 11px; font-weight: 700; }
.section-heading h2 { margin: 0; font-size: 21px; line-height: 1.3; }
.count, .difficulty { display: inline-flex; align-items: center; min-height: 24px; border-radius: 999px; padding: 3px 9px; background: #edf1ee; color: var(--muted); font-size: 12px; white-space: nowrap; }
.summary { max-width: 860px; color: var(--muted); line-height: 1.7; }
.practice-item { margin: 14px 0; padding: 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 1px 2px rgba(24, 32, 34, 0.04); }
.practice-item.is-flagged { border-color: var(--rose); box-shadow: 0 0 0 3px rgba(164, 63, 85, 0.12); }
.item-topline { display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-bottom: 10px; }
.flag-button { min-height: 32px; border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 6px; padding: 5px 10px; cursor: pointer; }
.flag-button[aria-pressed="true"] { color: #fff; background: var(--rose); border-color: var(--rose); }
.practice-item h3 { margin: 0 0 12px; font-size: 16px; line-height: 1.55; }
.options { display: grid; gap: 8px; margin: 0 0 12px; padding: 0; list-style: none; }
.options li { display: grid; grid-template-columns: 28px 1fr; gap: 8px; align-items: start; padding: 8px 10px; background: #f9faf8; border: 1px solid #e7ece9; border-radius: 6px; }
.option-label { display: inline-grid; place-items: center; width: 24px; height: 24px; color: #fff; background: var(--blue); border-radius: 50%; font-size: 12px; font-weight: 700; }
.answer { margin-top: 12px; padding: 12px 14px; background: #fff8ed; border-left: 3px solid var(--amber); border-radius: 6px; }
.answer summary { cursor: pointer; font-weight: 700; }
.answer p { margin: 10px 0 0; line-height: 1.65; }
.sources { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; color: var(--muted); font-size: 12px; }
.sources code { max-width: 100%; overflow-wrap: anywhere; border: 1px solid var(--line); background: #f7f8f5; border-radius: 4px; padding: 2px 5px; }
.outline-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 14px; }
.outline-list > div { padding: 11px 12px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.hidden { display: none !important; }
.empty-state { padding: 18px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--muted); }
@media (max-width: 860px) { .app { grid-template-columns: 1fr; } aside { position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); } main { padding: 22px 16px 48px; } .topbar h2 { font-size: 24px; } }
'''
    script = r'''
<script>
(() => {
  const key = 'xuexitong-practice-flags:' + location.pathname;
  const readFlags = () => { try { return new Set(JSON.parse(localStorage.getItem(key) || '[]')); } catch { return new Set(); } };
  const writeFlags = (flags) => localStorage.setItem(key, JSON.stringify(Array.from(flags)));
  const flags = readFlags();
  const items = Array.from(document.querySelectorAll('.practice-item'));
  const sections = Array.from(document.querySelectorAll('.concept-section'));
  const search = document.querySelector('#search');
  const filterButtons = Array.from(document.querySelectorAll('[data-filter]'));
  let filter = 'all';
  const applyFlags = (item) => {
    const id = item.dataset.itemId;
    const button = item.querySelector('.flag-button');
    const active = flags.has(id);
    item.classList.toggle('is-flagged', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.textContent = active ? '已标记' : '标记';
  };
  const applyFilter = () => {
    const q = (search.value || '').trim().toLowerCase();
    items.forEach((item) => {
      const flagged = flags.has(item.dataset.itemId);
      const text = (item.dataset.search || '').toLowerCase();
      const matchesQuery = !q || text.includes(q);
      const matchesFlag = filter !== 'flagged' || flagged;
      item.classList.toggle('hidden', !(matchesQuery && matchesFlag));
    });
    sections.forEach((section) => {
      const visible = section.querySelectorAll('.practice-item:not(.hidden)').length;
      section.classList.toggle('hidden', visible === 0);
    });
  };
  items.forEach((item) => {
    const button = item.querySelector('.flag-button');
    button.addEventListener('click', () => {
      const id = item.dataset.itemId;
      if (flags.has(id)) flags.delete(id); else flags.add(id);
      writeFlags(flags);
      applyFlags(item);
      applyFilter();
    });
    applyFlags(item);
  });
  filterButtons.forEach((button) => button.addEventListener('click', () => {
    filter = button.dataset.filter;
    filterButtons.forEach((btn) => btn.setAttribute('aria-pressed', btn === button ? 'true' : 'false'));
    applyFilter();
  }));
  search.addEventListener('input', applyFilter);
  applyFilter();
})();
</script>
'''
    nav_html = "".join(nav_rows) or '<a href="#source-outline"><span>资料大纲</span><strong>0</strong></a>'
    content_html = "".join(sections) or '<p class="empty-state">暂无题目。请先运行作业采集和建库。</p>'
    notebooklm_html = render_notebooklm_links(course_dir)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(course_title)} - 期末练习</title>
  <style>{css}</style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <h1>{esc(course_title)}</h1>
        <p>{esc(source_url)}</p>
      </div>
      <div class="side-stats">
        <span><strong>{counts.get('documents', 0)}</strong>资料</span>
        <span><strong>{counts.get('questions', len(items))}</strong>题目</span>
        <span><strong>{counts.get('assignments', 0)}</strong>作业</span>
        <span><strong>{len(active_titles)}</strong>题组</span>
      </div>
      <nav aria-label="知识点导航">{nav_html}<a href="#source-outline"><span>资料大纲</span><strong>{len(outline_titles)}</strong></a></nav>
    </aside>
    <main>
      <div class="topbar">
        <div>
          <h2>期末复习工作台</h2>
          <div class="meta-row">
            <span class="badge">{counts.get('concepts', len(concepts))} 个知识点</span>
            <span class="badge">{len(items)} 道练习</span>
            <span class="badge">本地离线页面</span>
          </div>
        </div>
        <div class="controls">
          <input id="search" class="search" type="search" placeholder="搜索题干、答案、知识点">
          <button class="filter-button" type="button" data-filter="all" aria-pressed="true">全部</button>
          <button class="filter-button" type="button" data-filter="flagged" aria-pressed="false">错题/标记</button>
        </div>
      </div>
      {content_html}
      {notebooklm_html}
      {''.join(outline)}
    </main>
  </div>
  {script}
</body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a script-generated fallback HTML page from course.db. Codex should author the final practice.html after reviewing course Markdown.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--output", default="", help="Optional output HTML path. Pipeline uses <course>/output/practice.generated.html as the fallback page.")
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    db_path = course_dir / "course.db"
    if not db_path.exists():
        raise SystemExit(f"course.db does not exist. Run build_course_db.py first: {db_path}")
    meta, counts, concepts, items = fetch_data(db_path, course_dir)
    output = Path(args.output).resolve() if args.output else course_dir / "output" / "practice.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(course_dir, meta, counts, concepts, items), encoding="utf-8", newline="\n")
    print(f"html={output} concepts={len(concepts)} practice_items={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())