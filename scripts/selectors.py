"""Selectors and heuristics for semi-automatic Chaoxing/Xuexitong collection.

The filename is intentionally kept as `selectors.py` per the project plan. Because
that shadows Python's standard-library module when scripts run by path, this file
first exposes stdlib selectors symbols so modules such as subprocess keep working.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import re
import sysconfig as _sysconfig
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

_stdlib_selectors = Path(_sysconfig.get_path("stdlib")) / "selectors.py"
if _stdlib_selectors.exists():
    _spec = _importlib_util.spec_from_file_location("_stdlib_selectors", _stdlib_selectors)
    if _spec and _spec.loader:
        _module = _importlib_util.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        for _name in dir(_module):
            if _name not in globals():
                globals()[_name] = getattr(_module, _name)

MATERIAL_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".zip",
    ".rar",
}

MATERIAL_KEYWORDS = (
    "\u8bfe\u4ef6",  # courseware
    "ppt",
    "pptx",
    "pdf",
    "\u8d44\u6599",  # material
    "\u8bb2\u4e49",  # handout
    "\u9644\u4ef6",  # attachment
    "\u4e0b\u8f7d",  # download
    "\u6587\u6863",  # document
    "\u7ae0\u8282\u8d44\u6599",
    "\u8bfe\u7a0b\u8d44\u6599",
)

ASSIGNMENT_KEYWORDS = (
    "\u4f5c\u4e1a",  # assignment
    "\u6d4b\u9a8c",  # quiz
    "\u8003\u8bd5",  # exam
    "\u7ec3\u4e60",  # practice
    "\u4e60\u9898",  # exercises
    "\u4efb\u52a1",  # task
    "\u7b54\u9898",  # answer questions
    "\u9898\u76ee",  # question
)

NOISE_LINK_KEYWORDS = (
    "\u9000\u51fa",
    "\u6ce8\u9500",
    "\u5220\u9664",
    "\u79fb\u9664",
    "\u9000\u8bfe",
    "\u5ba2\u670d",
    "\u5e2e\u52a9",
)

EXTRACT_LINKS_SCRIPT = r"""
() => {
  const nodes = Array.from(document.querySelectorAll('a, [role="link"], button, [onclick], [data]'));
  return nodes.map((el, index) => {
    const text = (el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    const data = el.getAttribute('data') || '';
    const href = el.href || el.getAttribute('href') || el.getAttribute('data-url') || el.getAttribute('data-href') || data || '';
    const title = el.getAttribute('title') || el.getAttribute('aria-label') || '';
    const download = el.getAttribute('download') || '';
    const onclick = el.getAttribute('onclick') || '';
    return { index, text, href, title, download, onclick, data };
  }).filter(item => item.text || item.href || item.onclick || item.data);
}
"""


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extension_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    suffix = Path(unquote(parsed.path)).suffix.lower()
    return suffix


def has_noise_text(text: str) -> bool:
    lowered = normalize_space(text).lower()
    return any(keyword in lowered for keyword in NOISE_LINK_KEYWORDS)


def is_assignment_link(text: str, href: str) -> bool:
    if not normalize_space(href) or has_noise_text(text):
        return False
    lowered = f"{text} {href}".lower()
    return any(keyword in lowered for keyword in ASSIGNMENT_KEYWORDS)


def is_material_link(text: str, href: str) -> bool:
    if not normalize_space(href) or has_noise_text(text):
        return False
    lowered = f"{text} {href}".lower()
    # Xuexitong sometimes exposes browser/client update links that contain download text.
    if "brower.html" in lowered or "browser.html" in lowered or "static/brower" in lowered:
        return False
    ext = extension_from_url(href)
    if ext in MATERIAL_EXTENSIONS:
        return True
    return any(keyword in lowered for keyword in MATERIAL_KEYWORDS) and not is_assignment_link(text, href)


def slugify(value: str, fallback: str = "course") -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.strip().lower()
    normalized = re.sub(r"[\\/:*?\"<>|]+", "-", normalized)
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-._ ")
    return normalized or fallback


def safe_filename(name: str, fallback: str = "file") -> str:
    normalized = unicodedata.normalize("NFKC", name or "")
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return normalized[:160] or fallback


def guess_filename(title: str, url: str, default_ext: str = "") -> str:
    parsed = urlparse(url or "")
    path_name = safe_filename(unquote(Path(parsed.path).name), "")
    if path_name and Path(path_name).suffix:
        return path_name
    ext = extension_from_url(url) or default_ext
    base = safe_filename(title, "material")
    if ext and not base.lower().endswith(ext.lower()):
        return base + ext
    return base
