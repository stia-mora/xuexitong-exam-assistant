"""Infer chapter metadata from course filenames, titles, and Markdown headings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

UNKNOWN_CHAPTER = "未分章资料"

_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def chinese_to_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[text]
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def clean_title(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\", "/")
    text = Path(text).stem if "/" in text or "." in Path(text).name else text
    text = re.sub(r"-[0-9a-fA-F]{8,}$", "", text)
    text = re.sub(r"[_\u3000]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -_.:：、\t\r\n")


def clean_label(value: str, limit: int = 40) -> str:
    label = clean_title(value)
    label = re.sub(r"^[\d一二两三四五六七八九十百]+[._\-、\s]+", "", label)
    label = re.sub(r"\.(?:pptx?|pdf|docx?|md)$", "", label, flags=re.I)
    label = label.strip(" -_.:：、")
    if len(label) > limit:
        label = label[:limit].rstrip()
    return label


def _chapter(order: int | None, label: str = "", source: str = "unknown") -> dict[str, Any]:
    if order is None:
        return {"key": "chapter-unknown", "title": UNKNOWN_CHAPTER, "order": 999999, "source": source}
    clean = clean_label(label)
    title = f"第 {order} 章" + (f" {clean}" if clean else "")
    return {"key": f"chapter-{order:04d}", "title": title, "order": order, "source": source}


def infer_chapter(title: str = "", path: str = "", headings: list[str] | None = None) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    if path:
        p = Path(str(path).replace("\\", "/"))
        candidates.append((clean_title(p.stem), "filename"))
        for parent in reversed(p.parts[:-1]):
            if parent and parent not in {"materials_md", "raw", "materials", "assignments_md"}:
                candidates.append((clean_title(parent), "folder"))
    if title:
        candidates.append((clean_title(title), "title"))
    for heading in headings or []:
        candidates.append((clean_title(heading), "heading"))

    seen: set[str] = set()
    for value, source in candidates:
        if not value or value in seen:
            continue
        seen.add(value)

        match = re.search(r"第\s*(?P<num>\d{1,3}|[零一二两三四五六七八九十百]+)\s*(?:章|章节|讲|单元|篇|课)\s*[:：、.\-\s]*(?P<label>[^#\n\r]{0,60})", value)
        if match:
            order = chinese_to_int(match.group("num"))
            return _chapter(order, match.group("label"), source)

        match = re.search(r"(?i)\b(?:chapter|unit|week|module|lecture)\s*(?P<num>\d{1,3})\b\s*[:：、.\-\s]*(?P<label>[^#\n\r]{0,60})", value)
        if match:
            return _chapter(int(match.group("num")), match.group("label"), source)

        if source in {"filename", "folder"}:
            match = re.match(r"^\s*(?P<num>\d{1,3})(?:[._\-]\d{1,3})?\s*[._\-、\s]*(?P<label>[^#\n\r]{0,60})", value)
            if match:
                num = int(match.group("num"))
                if 0 < num < 200:
                    label = match.group("label") if source == "folder" else ""
                    return _chapter(num, label, source)

    return _chapter(None)


def chapter_sort_key(chapter: dict[str, Any] | None) -> tuple[int, str]:
    data = chapter or {}
    try:
        order = int(data.get("order", 999999))
    except Exception:
        order = 999999
    return order, str(data.get("title") or UNKNOWN_CHAPTER)
