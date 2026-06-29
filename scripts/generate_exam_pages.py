"""Render the audited final-review HTML package.

This script is intentionally not a question generator.  It only renders pages
after an agent/LLM has reviewed every candidate question, written
generated/question_audit.json, and produced generated/question_bank.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chapter_utils import UNKNOWN_CHAPTER, chapter_sort_key, infer_chapter


SCRIPT_DIR = Path(__file__).resolve().parent
MIN_APPROVED_QUESTIONS = 150
TARGET_QUESTIONS = 180
MAX_SOFT_QUESTIONS = 220
TYPE_ORDER = ["选择题", "判断题", "填空题", "主观题"]
DEFAULT_MOCK_BLUEPRINT = {"选择题": 30, "判断题": 15, "填空题": 10, "主观题": 5}
APPROVED_DECISIONS = {"reuse_full", "add_analysis", "infer_answer", "teacher_knowledge_generated"}
WEAK_TEXT_MARKERS = (
    "资料未提供完整解析",
    "参考答案待补充",
    "正确答案：",
    "参考答案见解析",
    "待补充",
    "Summary pending",
    "Refer to the collected course materials",
)


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_int(value: Any, default: int = 10) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def payload_items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        rows = [value]
    elif isinstance(value, list):
        rows = value
    else:
        rows = [value]
    cleaned: list[str] = []
    for item in rows:
        text = clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def normalize_id_list(value: Any) -> list[str]:
    ids: list[str] = []
    for text in normalize_text_list(value):
        for part in re.split(r"[,，;；\s]+", text):
            part = clean_text(part)
            if part and part not in ids:
                ids.append(part)
    return ids


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_options(options: Any) -> list[dict[str, str]]:
    if not options:
        return []
    if isinstance(options, dict):
        iterable = [{"label": key, "text": value} for key, value in options.items()]
    else:
        iterable = options
    rows: list[dict[str, str]] = []
    for index, option in enumerate(iterable, start=1):
        if isinstance(option, dict):
            label = clean_text(option.get("label") or chr(64 + index)).upper()
            text = clean_text(option.get("text") or option.get("content") or "")
        else:
            raw = clean_text(option)
            match = re.match(r"^([A-Ha-h])\s*[.、．)]\s*(.+)$", raw)
            label = match.group(1).upper() if match else chr(64 + index)
            text = clean_text(match.group(2) if match else raw)
        if text:
            rows.append({"label": label, "text": text})
    return rows


def canonical_type(value: str, question: str = "", options: list[dict[str, str]] | None = None) -> str:
    text = f"{value or ''} {question or ''}"
    if any(token in text for token in ("单选", "多选", "选择")) or options:
        return "选择题"
    if "判断" in text:
        return "判断题"
    if "填空" in text or "____" in text or "（ ）" in text or "( )" in text:
        return "填空题"
    return "主观题"


def display_type(value: str, canonical: str) -> str:
    text = clean_text(value)
    if text and text.lower() not in {"unknown", "未知"}:
        return text
    return canonical


def source_label(source_kind: str, explicit: str = "") -> str:
    if explicit:
        return explicit if explicit.startswith("题源：") else f"题源：{explicit}"
    table = {
        "xxt_reused": "题源：学习通原题",
        "xxt_analysis_llm": "题源：学习通原题+LLM补解析",
        "xxt_answer_inferred": "题源：学习通原题+LLM推断答案",
        "teacher_knowledge_generated": "题源：老师知识点生成",
    }
    return table.get(source_kind, "题源：已审核题库")


def load_course_meta(course_dir: Path) -> dict[str, Any]:
    meta = read_json(course_dir / "course_meta.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("course_title", course_dir.name)
    meta.setdefault("course_slug", course_dir.name)
    return meta


def normalize_knowledge_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else None
    if chapter is None:
        chapter = infer_chapter(title=item.get("chapter_title") or item.get("title") or "", path="")
    source_kind = clean_text(item.get("source_kind") or item.get("source_type") or item.get("source") or "course_material")
    if source_kind in {"teacher", "teacher_focus", "teacher_manual"}:
        source_kind = "teacher_focus"
    elif source_kind not in {"teacher_focus", "course_material"}:
        source_kind = "course_material"
    knowledge_id = clean_text(item.get("knowledge_id") or item.get("id") or f"K{index:04d}")
    normalized = {
        "knowledge_id": knowledge_id,
        "title": clean_text(item.get("title") or item.get("name")),
        "chapter": chapter,
        "chapter_title": chapter.get("title") or UNKNOWN_CHAPTER,
        "learning_goal": clean_text(item.get("learning_goal") or item.get("objective") or item.get("goal")),
        "key_points": normalize_text_list(item.get("key_points") or item.get("core_points") or item.get("points")),
        "formula_examples": normalize_text_list(item.get("formula_examples") or item.get("examples") or item.get("formulas")),
        "pitfalls": normalize_text_list(item.get("pitfalls") or item.get("traps") or item.get("common_mistakes")),
        "exam_tips": normalize_text_list(item.get("exam_tips") or item.get("test_tips") or item.get("tips")),
        "source_refs": normalize_text_list(item.get("source_refs") or item.get("refs")),
        "source_kind": source_kind,
        "priority": 0 if source_kind == "teacher_focus" else safe_int(item.get("priority"), 10),
        "reviewed_by_llm": item.get("reviewed_by_llm") is True,
        "quality_status": clean_text(item.get("quality_status") or ""),
    }
    return normalized


def knowledge_bank_hash(knowledge_items: list[dict[str, Any]]) -> str:
    stable_items = []
    for item in knowledge_items:
        stable_items.append(
            {
                "knowledge_id": item.get("knowledge_id"),
                "title": item.get("title"),
                "chapter": item.get("chapter"),
                "learning_goal": item.get("learning_goal"),
                "key_points": item.get("key_points"),
                "formula_examples": item.get("formula_examples"),
                "pitfalls": item.get("pitfalls"),
                "exam_tips": item.get("exam_tips"),
                "source_refs": item.get("source_refs"),
                "source_kind": item.get("source_kind"),
                "priority": item.get("priority"),
            }
        )
    return hashlib.sha1(canonical_json(stable_items).encode("utf-8")).hexdigest()


def load_validated_knowledge_bank(course_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    payload = read_json(course_dir / "generated" / "knowledge_bank.json", {})
    raw_items = payload_items(payload, "items", "knowledge", "knowledge_points")
    if not raw_items:
        raise SystemExit("Missing generated/knowledge_bank.json items. Extract and LLM-review the knowledge bank before question audit/rendering.")

    errors: list[str] = []
    normalized_items: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_items, start=1):
        normalized = normalize_knowledge_item(item, index)
        kid = normalized["knowledge_id"]
        if not kid:
            errors.append(f"knowledge #{index} missing knowledge_id")
        elif kid in by_id:
            errors.append(f"duplicate knowledge_id: {kid}")
        if weak_text(normalized["title"]):
            errors.append(f"knowledge #{index} missing title")
        if weak_text(normalized["learning_goal"]):
            errors.append(f"knowledge #{index} missing learning_goal")
        if not normalized["key_points"]:
            errors.append(f"knowledge #{index} missing key_points")
        if not normalized["formula_examples"]:
            errors.append(f"knowledge #{index} missing formula_examples")
        if not normalized["pitfalls"]:
            errors.append(f"knowledge #{index} missing pitfalls")
        if not normalized["exam_tips"]:
            errors.append(f"knowledge #{index} missing exam_tips")
        if not normalized["source_refs"]:
            errors.append(f"knowledge #{index} missing source_refs")
        if normalized["reviewed_by_llm"] is not True:
            errors.append(f"knowledge #{index} is not marked reviewed_by_llm")
        if normalized["quality_status"] != "approved":
            errors.append(f"knowledge #{index} quality_status must be approved")
        if kid:
            by_id[kid] = normalized
        normalized_items.append(normalized)

    normalized_items.sort(
        key=lambda item: (
            chapter_sort_key(item.get("chapter") or {}),
            safe_int(item.get("priority"), 10),
            clean_text(item.get("title")),
            clean_text(item.get("knowledge_id")),
        )
    )
    if errors:
        raise SystemExit("Knowledge bank validation failed:\n- " + "\n- ".join(errors[:80]))
    return normalized_items, by_id, knowledge_bank_hash(normalized_items)


def load_audit(course_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = read_json(course_dir / "generated" / "question_audit.json", {})
    records = payload_items(payload, "items", "audits", "records")
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(records, start=1):
        audit_id = clean_text(item.get("audit_id") or item.get("id") or f"audit-{index:04d}")
        item.setdefault("audit_id", audit_id)
        by_id[audit_id] = item
    return records, by_id


def weak_text(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return True
    return any(marker in text for marker in WEAK_TEXT_MARKERS)


def audit_approved(record: dict[str, Any]) -> bool:
    decision = clean_text(record.get("decision"))
    if decision == "discard_noisy" or record.get("quality_status") in {"discarded", "rejected"}:
        return False
    return (
        record.get("approved_for_bank") is True
        and record.get("reviewed_by_llm") is True
        and record.get("quality_status") == "approved"
        and decision in APPROVED_DECISIONS
    )


def normalize_bank_item(item: dict[str, Any], audit: dict[str, Any] | None, index: int) -> dict[str, Any]:
    audit = audit or {}
    stem = clean_text(
        item.get("stem")
        or item.get("question")
        or item.get("final_stem")
        or audit.get("final_stem")
        or audit.get("original_stem")
    )
    answer = clean_text(item.get("answer") or item.get("final_answer") or audit.get("final_answer"))
    analysis = clean_text(item.get("analysis") or item.get("explanation") or item.get("final_analysis") or audit.get("final_analysis"))
    options = normalize_options(item.get("options") or audit.get("options") or audit.get("final_options") or [])
    canonical = canonical_type(item.get("canonical_type") or item.get("type") or audit.get("type") or "", stem, options)
    chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else audit.get("chapter")
    if not isinstance(chapter, dict):
        chapter = infer_chapter(title=item.get("chapter_title") or audit.get("chapter_title") or "", path="")
    source_kind = clean_text(item.get("source_kind") or audit.get("decision") or audit.get("source_kind"))
    if source_kind == "add_analysis":
        source_kind = "xxt_analysis_llm"
    elif source_kind == "infer_answer":
        source_kind = "xxt_answer_inferred"
    elif source_kind == "reuse_full":
        source_kind = "xxt_reused"
    normalized = {
        "id": clean_text(item.get("id") or audit.get("source_id") or audit.get("candidate_id") or f"Q{index:04d}"),
        "audit_id": clean_text(item.get("audit_id") or audit.get("audit_id")),
        "bank_index": index,
        "type": display_type(item.get("type") or audit.get("type") or "", canonical),
        "canonical_type": canonical,
        "chapter": chapter,
        "chapter_title": chapter.get("title") or UNKNOWN_CHAPTER,
        "question": stem,
        "stem": stem,
        "options": options,
        "answer": answer,
        "analysis": analysis,
        "explanation": analysis,
        "source_kind": source_kind,
        "source_label": source_label(source_kind, item.get("source_label") or audit.get("source_label") or ""),
        "source_refs": item.get("source_refs") or audit.get("source_refs") or [],
        "knowledge_ids": normalize_id_list(item.get("knowledge_ids")),
        "audit_knowledge_ids": normalize_id_list(audit.get("knowledge_ids")),
        "answer_inferred": bool(item.get("answer_inferred") or audit.get("answer_inferred") or source_kind == "xxt_answer_inferred"),
        "confidence": item.get("confidence", audit.get("confidence", "")),
        "reviewed_by_llm": bool(item.get("reviewed_by_llm") is True or audit.get("reviewed_by_llm") is True or audit),
        "quality_status": clean_text(item.get("quality_status") or audit.get("quality_status") or "approved"),
    }
    return normalized


def validate_candidates_audited(course_dir: Path, audit_records: list[dict[str, Any]]) -> list[str]:
    payload = read_json(course_dir / "generated" / "question_candidates.json", {})
    candidates = payload_items(payload, "items", "candidates")
    if not candidates:
        return []
    audited_sources = {
        clean_text(record.get("source_id") or record.get("candidate_id") or record.get("original_id"))
        for record in audit_records
    }
    errors: list[str] = []
    for candidate in candidates:
        candidate_id = clean_text(candidate.get("id") or candidate.get("source_id"))
        if candidate_id and candidate_id not in audited_sources:
            errors.append(f"candidate not audited: {candidate_id}")
    return errors


def load_validated_question_bank(
    course_dir: Path,
    min_approved_questions: int,
    knowledge_by_id: dict[str, dict[str, Any]],
    expected_knowledge_hash: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    audit_records, audit_by_id = load_audit(course_dir)
    if not audit_records:
        raise SystemExit("Missing generated/question_audit.json. Run the LLM per-question audit before rendering HTML.")

    bank_payload = read_json(course_dir / "generated" / "question_bank.json", {})
    raw_bank = payload_items(bank_payload, "items", "questions")
    if not raw_bank:
        raise SystemExit("Missing generated/question_bank.json items. Write the audited final bank before rendering HTML.")

    errors = validate_candidates_audited(course_dir, audit_records)
    bank_summary = bank_payload.get("summary") if isinstance(bank_payload, dict) else {}
    actual_knowledge_hash = clean_text((bank_summary or {}).get("knowledge_bank_hash"))
    if not actual_knowledge_hash:
        errors.append("question_bank.summary missing knowledge_bank_hash")
    elif actual_knowledge_hash != expected_knowledge_hash:
        errors.append("question_bank.summary.knowledge_bank_hash does not match generated/knowledge_bank.json")
    for audit in audit_records:
        if audit_approved(audit):
            audit_kids = normalize_id_list(audit.get("knowledge_ids"))
            audit_id = clean_text(audit.get("audit_id"))
            if not audit_kids:
                errors.append(f"approved audit missing knowledge_ids: {audit_id}")
            for kid in audit_kids:
                if kid not in knowledge_by_id:
                    errors.append(f"approved audit references unknown knowledge_id {kid}: {audit_id}")
    warnings: list[str] = []
    approved: list[dict[str, Any]] = []
    for index, item in enumerate(raw_bank, start=1):
        audit_id = clean_text(item.get("audit_id"))
        audit = audit_by_id.get(audit_id)
        if not audit_id:
            errors.append(f"question #{index} missing audit_id")
            continue
        if not audit:
            errors.append(f"question #{index} audit_id not found in question_audit.json: {audit_id}")
            continue
        if not audit_approved(audit):
            errors.append(f"question #{index} audit record is not approved: {audit_id}")
            continue
        normalized = normalize_bank_item(item, audit, index)
        if not normalized["reviewed_by_llm"]:
            errors.append(f"question #{index} is not marked reviewed_by_llm: {audit_id}")
        if normalized["quality_status"] != "approved":
            errors.append(f"question #{index} quality_status must be approved: {audit_id}")
        if weak_text(normalized["question"]):
            errors.append(f"question #{index} has empty/weak stem: {audit_id}")
        if weak_text(normalized["answer"]):
            errors.append(f"question #{index} has empty/weak answer: {audit_id}")
        if weak_text(normalized["analysis"]):
            errors.append(f"question #{index} has empty/weak analysis: {audit_id}")
        item_kids = normalized.get("knowledge_ids") or []
        audit_kids = normalized.get("audit_knowledge_ids") or []
        if not item_kids:
            errors.append(f"question #{index} missing knowledge_ids: {audit_id}")
        if not audit_kids:
            errors.append(f"question #{index} audit missing knowledge_ids: {audit_id}")
        if item_kids and audit_kids and set(item_kids) != set(audit_kids):
            errors.append(f"question #{index} knowledge_ids mismatch audit record: {audit_id}")
        for kid in item_kids:
            if kid not in knowledge_by_id:
                errors.append(f"question #{index} references unknown knowledge_id {kid}: {audit_id}")
        approved.append(normalized)

    if len(approved) < min_approved_questions:
        errors.append(f"approved question count {len(approved)} is below required minimum {min_approved_questions}")
    if len(approved) > MAX_SOFT_QUESTIONS:
        warnings.append(f"approved question count {len(approved)} is above soft maximum {MAX_SOFT_QUESTIONS}; consider prioritizing the best items")
    if errors:
        raise SystemExit("Question bank validation failed:\n- " + "\n- ".join(errors[:80]))
    return approved, warnings


def template_style(course_dir: Path, filename: str) -> str:
    candidates = [Path.cwd(), SCRIPT_DIR.parent]
    candidates.extend(course_dir.parents)
    for root in candidates:
        template = root / "templates" / filename
        if template.exists():
            text = template.read_text(encoding="utf-8", errors="replace")
            break
    else:
        raise SystemExit(f"Template not found: templates/{filename}")
    match = re.search(r"<style>(.*?)</style>", text, re.S | re.I)
    if not match:
        return ""
    return match.group(1).strip()


def render_nav(chapters: list[dict[str, Any]]) -> str:
    links = ['<a href="practice.html">复习入口</a>', '<a href="questions.html">全部题库</a>', '<a href="mock_exam.html">模拟卷</a>']
    for index, chapter in enumerate(chapters, start=1):
        links.append(f'<a href="#ch{index}">{esc(chapter.get("title") or UNKNOWN_CHAPTER)}</a>')
    return '<div class="nv">' + "\n".join(links) + "</div>"


def group_by_chapter(question_bank: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapters: dict[str, dict[str, Any]] = {}
    for item in question_bank:
        chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else infer_chapter()
        key = chapter.get("key") or "chapter-unknown"
        grouped[key].append(item)
        chapters[key] = chapter
    ordered = [chapter for _, chapter in sorted(chapters.items(), key=lambda pair: chapter_sort_key(pair[1]))]
    return ordered, grouped


def group_knowledge_by_chapter(knowledge_bank: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in knowledge_bank:
        chapter = item.get("chapter") if isinstance(item.get("chapter"), dict) else infer_chapter()
        key = chapter.get("key") or "chapter-unknown"
        grouped[key].append(item)
    for key, rows in grouped.items():
        grouped[key] = sorted(
            rows,
            key=lambda item: (
                safe_int(item.get("priority"), 10),
                0 if item.get("source_kind") == "teacher_focus" else 1,
                clean_text(item.get("title")),
            ),
        )
    return grouped


def render_option_spans(options: list[dict[str, str]], with_label: bool = True) -> str:
    if not options:
        return ""
    rows = []
    for option in options:
        label = f"{esc(option.get('label'))}. " if with_label else ""
        rows.append(f"<span>{label}{esc(option.get('text'))}</span>")
    return "\n".join(rows)


def render_inline_list(label: str, values: list[str]) -> str:
    if not values:
        return ""
    return f"<p><strong>{esc(label)}：</strong>{esc('；'.join(values))}</p>"


def render_knowledge_card(item: dict[str, Any]) -> str:
    source = "老师重点" if item.get("source_kind") == "teacher_focus" else "课件提炼"
    refs = "，".join(item.get("source_refs") or [])
    pitfalls = item.get("pitfalls") or []
    trap_html = "".join(f'<div class="trap">{esc(text)}</div>' for text in pitfalls)
    refs_html = f'<p class="knowledge-ref">来源：{esc(refs)}</p>' if refs else ""
    return f"""<div class="knowledge" data-knowledge-id="{esc(item.get('knowledge_id'))}">
<h4>{esc(item.get('title'))}<span class="q-source">{esc(source)}</span></h4>
<p><strong>学习目标：</strong>{esc(item.get('learning_goal'))}</p>
{render_inline_list("核心要点", item.get("key_points") or [])}
{render_inline_list("公式/例子", item.get("formula_examples") or [])}
{trap_html}
{render_inline_list("考试提示", item.get("exam_tips") or [])}
{refs_html}
</div>"""


def render_question_for_template(item: dict[str, Any], index: int, *, mode: str = "questions") -> str:
    if mode == "mock":
        return f"""<div class="q"><div class="qn">{index}. [{esc(item.get('type'))}]</div>
<div class="qs">{esc(item.get('question'))}</div>
<div class="qo">
{render_option_spans(item.get('options') or [], with_label=True)}
</div>
<details class="qa"><summary>查看答案与解析</summary>
<div class="ans">答案：{esc(item.get('answer'))}</div>
<div class="ana">{esc(item.get('analysis'))}</div>
</details>
</div>"""
    if mode == "practice":
        return f"""<div class="question">
<div class="q-header"><span class="q-num">Q{index}</span><span class="q-source">{esc(item.get('source_label'))}</span></div>
<div class="q-text">{esc(item.get('question'))}</div>
<div class="q-options">{render_option_spans(item.get('options') or [], with_label=True)}</div>
<details class="q-answer"><summary>查看答案与解析</summary>
<div class="correct">答案：{esc(item.get('answer'))}</div>
<dl><dt>解析</dt><dd>{esc(item.get('analysis'))}</dd></dl>
</details>
</div>"""
    return f"""<div class="q">
<div class="qh"><span class="qn">Q{index}</span><span class="qt">{esc(item.get('type'))}</span><span class="qs">{esc(item.get('source_label'))}</span></div>
<div class="qst">{esc(item.get('question'))}</div>
<div class="qop">
{render_option_spans(item.get('options') or [], with_label=False)}
</div>
<details class="qd"><summary>查看答案与解析</summary>
<div class="ans"><strong>答案：</strong>{esc(item.get('answer'))}</div>
<div class="ana">{esc(item.get('analysis'))}</div>
</details>
</div>"""


def render_questions_page(course_dir: Path, meta: dict[str, Any], question_bank: list[dict[str, Any]]) -> str:
    style = template_style(course_dir, "questions.html")
    chapters, grouped = group_by_chapter(question_bank)
    sections: list[str] = []
    q_index = 1
    for chapter_index, chapter in enumerate(chapters, start=1):
        key = chapter.get("key") or "chapter-unknown"
        rows = grouped.get(key, [])
        type_counts = Counter(item.get("type") for item in rows)
        stats = "，".join(f"{name}({count})" for name, count in type_counts.items())
        sections.append(f'<div class="ch" id="ch{chapter_index}"><div class="chh"><h3>{esc(chapter.get("title") or UNKNOWN_CHAPTER)}</h3><span style="font-size:.8rem;color:var(--m)">{len(rows)}题 · {esc(stats)}</span></div>')
        for item in rows:
            sections.append(render_question_for_template(item, q_index))
            q_index += 1
        sections.append("</div>")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>题库 · {esc(meta.get('course_title'))}</title><style>
{style}
</style></head><body><div class="c"><div class="h"><h1>题库 · {esc(meta.get('course_title'))}</h1><div style="font-size:.85rem;opacity:.8;margin-top:4px">共 {len(question_bank)} 题 · 每题已逐题 LLM 审核</div><div style="margin-top:8px"><a href="practice.html">返回入口</a> | <a href="mock_exam.html">模拟卷</a></div></div>
{render_nav(chapters)}
{''.join(sections)}
<div class="bl"><a href="practice.html">返回复习入口</a></div></div></body></html>
"""


def render_practice_page(
    course_dir: Path,
    meta: dict[str, Any],
    question_bank: list[dict[str, Any]],
    knowledge_bank: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    style = template_style(course_dir, "practice.html")
    chapters, grouped = group_by_chapter(question_bank)
    knowledge_by_chapter = group_knowledge_by_chapter(knowledge_bank)
    knowledge_by_id = {item.get("knowledge_id"): item for item in knowledge_bank}
    source_counts = Counter(item.get("source_kind") for item in question_bank)
    priority_cards = []
    for label, keys in [
        ("P0 原题复用与补解析", ("xxt_reused", "xxt_analysis_llm")),
        ("P1 推断答案题", ("xxt_answer_inferred",)),
        ("P2 老师知识点生成", ("teacher_knowledge_generated",)),
    ]:
        count = sum(source_counts.get(key, 0) for key in keys)
        css = "p0" if "P0" in label else "p1" if "P1" in label else "p2"
        priority_cards.append(f'<div class="priority {css}"><h3>{esc(label)}</h3><ul><li>{count} 题</li><li>全部已逐题审核</li></ul></div>')
    chapter_blocks = []
    q_index = 1
    for chapter_index, chapter in enumerate(chapters, start=1):
        key = chapter.get("key") or "chapter-unknown"
        rows = grouped.get(key, [])
        preview = rows[: min(8, len(rows))]
        knowledge_rows = list(knowledge_by_chapter.get(key, []))
        seen_knowledge = {item.get("knowledge_id") for item in knowledge_rows}
        for row in rows:
            for kid in row.get("knowledge_ids") or []:
                if kid in knowledge_by_id and kid not in seen_knowledge:
                    knowledge_rows.append(knowledge_by_id[kid])
                    seen_knowledge.add(kid)
        knowledge_rows.sort(
            key=lambda item: (
                0 if item.get("source_kind") == "teacher_focus" else 1,
                safe_int(item.get("priority"), 10),
                clean_text(item.get("title")),
            )
        )
        knowledge_html = "".join(render_knowledge_card(item) for item in knowledge_rows)
        chapter_blocks.append(f"""<div class="chapter" id="ch{chapter_index}">
<div class="chapter-header"><h3>{esc(chapter.get('title') or UNKNOWN_CHAPTER)}</h3><span class="badge badge-p1">{len(rows)}题</span></div>
<div class="chapter-body">
<div class="questions-section"><h4>知识点学习</h4>{knowledge_html}</div>
<div class="questions-section"><h4>精选练习</h4>""")
        for item in preview:
            chapter_blocks.append(render_question_for_template(item, q_index, mode="practice"))
            q_index += 1
        chapter_blocks.append("</div></div></div>")
    warning_html = "".join(f"<li>{esc(item)}</li>" for item in warnings)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{esc(meta.get('course_title'))} 期末复习</title><style>
{style}
</style></head><body><div class="container">
<div class="header"><h1>{esc(meta.get('course_title'))} 期末复习</h1><div class="meta">{len(knowledge_bank)} 个知识点 · 最终题库 {len(question_bank)} 题 · 先学后练</div></div>
<div class="entry-cards"><a href="questions.html" class="entry-card"><div class="icon">题</div><h3>全部题库</h3><p>{len(question_bank)}题 · 每题含答案与解析</p></a><a href="mock_exam.html" class="entry-card"><div class="icon">卷</div><h3>模拟卷</h3><p>从已审核题库抽取，不生成未审核题</p></a></div>
<div class="summary"><h2>学习路径摘要</h2><div class="stats"><div class="stat"><strong>{len(knowledge_bank)}</strong> 审核知识点</div><div class="stat"><strong>{len(question_bank)}</strong> 审核通过题</div><div class="stat"><strong>{len(chapters)}</strong> 章节</div></div><p>流程已按“课件/老师重点 → 知识库 → 题库审核 → 先学后练页面”校验，题目均绑定知识点、audit_id、答案和解析。</p>{f'<ul>{warning_html}</ul>' if warning_html else ''}</div>
<div class="priority-map">{''.join(priority_cards)}</div>
<div class="nav">{''.join(f'<a href="#ch{i}">{esc(ch.get("title") or UNKNOWN_CHAPTER)}</a>' for i, ch in enumerate(chapters, start=1))}</div>
<div class="exam-tips"><h2>得分导向提示</h2><ul><li>先读每章知识点卡片，抓定义、判定条件、公式和易错点。</li><li>再做同章精选题，把知识点映射到题干关键词和标准答案。</li><li>最后到全部题库和模拟卷查漏补缺。</li></ul></div>
{''.join(chapter_blocks)}
<div class="review-route"><h2>推荐复习路线</h2><ol><li>按章节先学知识点卡片，尤其是老师重点和易错点。</li><li>完成本章精选练习，展开答案解析核对得分点。</li><li>再到 questions.html 全量刷题，最后用 mock_exam.html 限时自测。</li></ol></div>
</div></body></html>
"""


def build_mock_exam(question_bank: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in question_bank:
        by_type[item.get("canonical_type") or canonical_type(item.get("type", ""), item.get("question", ""), item.get("options") or [])].append(item)
    selected: list[dict[str, Any]] = []
    for q_type in TYPE_ORDER:
        target = DEFAULT_MOCK_BLUEPRINT[q_type]
        rows = by_type.get(q_type, [])
        for item in rows[:target]:
            copied = dict(item)
            copied["mock_id"] = f"M{len(selected) + 1:03d}"
            selected.append(copied)
    return {"schema_version": 2, "updated_at": utc_now(), "blueprint": DEFAULT_MOCK_BLUEPRINT, "source": "audited_question_bank", "items": selected}


def render_mock_exam_page(course_dir: Path, meta: dict[str, Any], mock_exam: dict[str, Any]) -> str:
    style = template_style(course_dir, "mock_exam.html")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in mock_exam.get("items") or []:
        grouped[item.get("canonical_type") or canonical_type(item.get("type", ""), item.get("question", ""), item.get("options") or [])].append(item)
    sections = []
    index = 1
    labels = {"选择题": "一、单选题", "判断题": "二、判断题", "填空题": "三、填空题", "主观题": "四、简答题"}
    hints = {
        "选择题": "请选出每题的唯一正确选项。",
        "判断题": "请判断题干表述是否正确。",
        "填空题": "请填写标准术语或关键短语。",
        "主观题": "请按得分点组织答案。",
    }
    for q_type in TYPE_ORDER:
        rows = grouped.get(q_type, [])
        if not rows:
            continue
        sections.append(f'<div class="sec"><div class="sh">{labels[q_type]}<span style="font-weight:normal;font-size:.85rem">（共{len(rows)}题）</span></div><div class="sd">{hints[q_type]}</div>')
        for item in rows:
            sections.append(render_question_for_template(item, index, mode="mock"))
            index += 1
        sections.append("</div>")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>模拟卷 · {esc(meta.get('course_title'))}</title><style>
{style}
</style></head><body><div class="w">
<div class="h"><h1>模拟卷</h1><div class="sub">{esc(meta.get('course_title'))}</div><div class="sub sec-hint">共{len(mock_exam.get('items') or [])}题 · 全部来自已审核题库</div><div class="nav"><a href="practice.html">返回复习入口</a> | <a href="questions.html">全部题库</a></div></div>
{''.join(sections)}
<div class="bl"><a href="practice.html">返回复习入口</a><a href="questions.html">全部题库</a></div>
</div></body></html>
"""


def build_pages(course_dir: Path, min_approved_questions: int) -> dict[str, Any]:
    meta = load_course_meta(course_dir)
    knowledge_bank, knowledge_by_id, current_knowledge_hash = load_validated_knowledge_bank(course_dir)
    question_bank, warnings = load_validated_question_bank(course_dir, min_approved_questions, knowledge_by_id, current_knowledge_hash)
    output_dir = course_dir / "output"
    generated_dir = course_dir / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    mock_exam = build_mock_exam(question_bank)
    write_json(generated_dir / "mock_exam.json", mock_exam)
    write_json(
        generated_dir / "render_validation.json",
        {
            "schema_version": 2,
            "updated_at": utc_now(),
            "knowledge_count": len(knowledge_bank),
            "knowledge_bank_hash": current_knowledge_hash,
            "question_count": len(question_bank),
            "warnings": warnings,
        },
    )
    write_text(output_dir / "practice.html", render_practice_page(course_dir, meta, question_bank, knowledge_bank, warnings))
    write_text(output_dir / "questions.html", render_questions_page(course_dir, meta, question_bank))
    write_text(output_dir / "mock_exam.html", render_mock_exam_page(course_dir, meta, mock_exam))
    return {"knowledge_bank": len(knowledge_bank), "question_bank": len(question_bank), "mock_exam": len(mock_exam.get("items") or []), "warnings": len(warnings)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render audited question_bank.json into the stable three-page HTML package.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--min-approved-questions", type=int, default=MIN_APPROVED_QUESTIONS)
    parser.add_argument("--teacher-mock", default="", help="Deprecated; mock exam now uses only audited question_bank.json.")
    parser.add_argument("--questions-per-chapter", type=int, default=0, help="Deprecated; kept for CLI compatibility.")
    parser.add_argument("--ai-fill-min-per-chapter", type=int, default=0, help="Deprecated; kept for CLI compatibility.")
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")
    result = build_pages(course_dir, max(args.min_approved_questions, 0))
    print(
        f"rendered audited pages course={course_dir} knowledge={result['knowledge_bank']} "
        f"questions={result['question_bank']} mock={result['mock_exam']} warnings={result['warnings']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
