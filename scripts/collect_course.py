"""Semi-automatic authorized Xuexitong/Chaoxing course collector."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from extract_assignments import extract_assignment
from manifest import CourseManifest
from selectors import EXTRACT_LINKS_SCRIPT, guess_filename, is_assignment_link, is_material_link, safe_filename, slugify

DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_HOME = "https://i.chaoxing.com/"
COURSE_TAB_LABELS = ("\u4f5c\u4e1a", "\u8d44\u6599", "\u4efb\u52a1", "\u7ae0\u8282")

CLICK_VISIBLE_TEXT_SCRIPT = r"""
(labels) => {
  const norm = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const nodes = Array.from(document.querySelectorAll('a, button, [onclick], [role="tab"], [role="link"], li, span, div'));
  for (const label of labels) {
    for (const el of nodes) {
      const text = norm(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label'));
      const title = norm(el.getAttribute('title') || el.getAttribute('aria-label'));
      if (!visible(el) || (text !== label && title !== label)) {
        continue;
      }
      const clickable = el.closest('a, button, [onclick], [role="tab"], [role="link"], li') || el;
      clickable.scrollIntoView({block: 'center', inline: 'center'});
      clickable.click();
      return {clicked: true, label, text, title, tag: clickable.tagName, url: location.href};
    }
  }
  return {clicked: false};
}
"""

CLICK_INVENTORY_ITEM_SCRIPT = r"""
(target) => {
  const norm = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const targetText = norm(target.text);
  const targetTitle = norm(target.title);
  const targetOnclick = norm(target.onclick);
  const nodes = Array.from(document.querySelectorAll('a, button, [onclick], [role="tab"], [role="link"], li, span, div'));
  for (const el of nodes) {
    const text = norm(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label'));
    const title = norm(el.getAttribute('title') || el.getAttribute('aria-label'));
    const onclick = norm(el.getAttribute('onclick'));
    const onclickMatch = targetOnclick && onclick === targetOnclick;
    const textMatch = targetText && text === targetText;
    const titleMatch = targetTitle && title === targetTitle;
    const matchesTarget = targetOnclick ? (onclickMatch && (textMatch || titleMatch || !targetText)) : (textMatch || titleMatch);
    if (!visible(el) || !matchesTarget) {
      continue;
    }
    const clickable = el.closest('a, button, [onclick], [role="tab"], [role="link"], li') || el;
    clickable.scrollIntoView({block: 'center', inline: 'center'});
    clickable.click();
    return {clicked: true, text, title, onclick, tag: clickable.tagName, url: location.href};
  }
  return {clicked: false};
}
"""

EVAL_ONCLICK_SCRIPT = r"""
(code) => {
  const before = location.href;
  try {
    const result = Function(code)();
    return {clicked: true, mode: 'eval-onclick', result: String(result ?? ''), url: location.href, before};
  } catch (error) {
    return {clicked: false, mode: 'eval-onclick', error: String(error), url: location.href, before};
  }
}
"""


def log(message: str) -> None:
    print(message, flush=True)


def wait_for_user(message: str) -> None:
    try:
        input(message)
    except EOFError:
        log("No interactive input stream available; continuing.")


def ensure_course_dirs(course_dir: Path) -> None:
    for rel in (
        "raw/materials",
        "raw/assignments_html",
        "assignments_md",
        "materials_md",
        "assets",
        "manifests",
        "generated",
        "output",
        "work/downloads",
    ):
        (course_dir / rel).mkdir(parents=True, exist_ok=True)


def local_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def looks_like_login_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "passport" in lowered or "login" in lowered


def course_signal_score(links: list[dict[str, Any]]) -> int:
    score = 0
    for item in links:
        text = item.get("text") or ""
        onclick = (item.get("onclick") or "").lower()
        page_url = item.get("source_page_url") or ""
        frame_url = item.get("source_frame_url") or ""
        if looks_like_login_url(page_url) or looks_like_login_url(frame_url):
            continue
        if "mooc2-ans" in page_url or "mooc2" in frame_url:
            score += 1
        if text in {"\u4f5c\u4e1a", "\u8d44\u6599", "\u4efb\u52a1", "\u7ae0\u8282"}:
            score += 3
        if "gotask" in onclick or "toopen" in onclick or "toold" in onclick:
            score += 2
    return score


def wait_for_course_context(context: Any, seconds: int = 480) -> list[dict[str, Any]]:
    deadline = datetime.now().timestamp() + max(0, seconds)
    last_report = 0.0
    best_links: list[dict[str, Any]] = []
    best_score = 0
    while datetime.now().timestamp() < deadline:
        try:
            links = collect_context_links(context)
            score = course_signal_score(links)
            if score > best_score:
                best_score = score
                best_links = links
            if score >= 6:
                return links
            now = datetime.now().timestamp()
            if now - last_report >= 12:
                urls = [getattr(page, "url", "") for page in context.pages]
                login_pages = sum(1 for url in urls if looks_like_login_url(url))
                log(f"Waiting for course page... pages={len(urls)} login_pages={login_pages} links={len(links)} score={score}")
                last_report = now
        except Exception as exc:
            log(f"Waiting warning: {exc}")
        try:
            context.pages[0].wait_for_timeout(3000)
        except Exception:
            pass
    return best_links


def detect_course_meta(context: Any, course_dir: Path, args: argparse.Namespace, links: list[dict[str, Any]]) -> dict[str, Any]:
    titles: list[str] = []
    urls: list[str] = []
    for item in links:
        title = (item.get("source_page_title") or "").strip()
        url = (item.get("source_page_url") or item.get("source_frame_url") or "").strip()
        if title and not looks_like_login_url(url) and title not in titles:
            titles.append(title)
        if url and "mooc" in url and not looks_like_login_url(url) and url not in urls:
            urls.append(url)
    for page in context.pages:
        try:
            title = page.title().strip()
            if title and not looks_like_login_url(page.url) and title not in titles:
                titles.append(title)
            if page.url and "mooc" in page.url and not looks_like_login_url(page.url) and page.url not in urls:
                urls.append(page.url)
        except Exception:
            continue
    course_title = args.course_name if args.course_name != "Xuexitong Course" else ""
    if not course_title:
        course_title = next((title for title in titles if title not in {"\u4e2a\u4eba\u7a7a\u95f4", "\u8bfe\u7a0b"}), "")
    course_title = course_title or course_dir.name
    return {
        "course_title": course_title,
        "course_slug": course_dir.name,
        "course_dir": str(course_dir),
        "source_url": urls[0] if urls else (args.course_url or ""),
        "profile_dir": str(Path(args.profile_dir).resolve()),
        "collected_at": local_now(),
        "detected_titles": titles[:10],
        "detected_urls": urls[:10],
    }


def write_course_meta(course_dir: Path, context: Any, args: argparse.Namespace, links: list[dict[str, Any]]) -> dict[str, Any]:
    meta = detect_course_meta(context, course_dir, args, links)
    path = course_dir / "course_meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return meta


def write_last_course_dir(course_dir: Path, args: argparse.Namespace) -> None:
    marker = Path(args.data_root).resolve() / ".last_course_dir"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(course_dir.resolve()), encoding="utf-8", newline="\n")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique path for {path}")


def repair_mojibake_filename(value: str) -> str:
    if not value:
        return ""
    text = unquote(value)
    replacements = {
        "1\u20444": "\u00bc",
        "3\u20444": "\u00be",
        "1\u20442": "\u00bd",
    }
    normalized = text
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    candidates = [normalized, text]
    for candidate in candidates:
        try:
            decoded = candidate.encode("latin1").decode("utf-8")
        except UnicodeError:
            continue
        if any("\u4e00" <= char <= "\u9fff" for char in decoded):
            return decoded
    return text


def parse_content_disposition_filename(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, flags=re.IGNORECASE)
    if match:
        return safe_filename(repair_mojibake_filename(match.group(1)), "")
    match = re.search(r'filename="?([^";]+)"?', value, flags=re.IGNORECASE)
    if match:
        return safe_filename(repair_mojibake_filename(match.group(1)), "")
    return ""


def collect_links(page: Any) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for frame in page.frames:
        try:
            rows = frame.evaluate(EXTRACT_LINKS_SCRIPT)
        except Exception:
            continue
        for row in rows:
            raw_href = (row.get("href") or "").strip()
            text = (row.get("text") or row.get("title") or row.get("download") or "").strip()
            href = urljoin(frame.url, raw_href) if raw_href and not raw_href.lower().startswith("javascript:") else raw_href
            key = f"{text}\n{href}\n{row.get('onclick') or ''}"
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "text": text or href or "untitled link",
                    "href": href,
                    "raw_href": raw_href,
                    "title": row.get("title") or "",
                    "download": row.get("download") or "",
                    "onclick": row.get("onclick") or "",
                    "data": row.get("data") or "",
                    "source_frame_url": frame.url,
                }
            )
    return links


def collect_context_links(context: Any) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in list(context.pages):
        try:
            auto_scroll(page, rounds=1, pause_ms=250)
            page_links = collect_links(page)
        except Exception:
            continue
        for item in page_links:
            key = f"{item.get('text') or ''}\n{item.get('href') or ''}\n{item.get('onclick') or ''}\n{item.get('source_frame_url') or ''}"
            if key in seen:
                continue
            seen.add(key)
            item["source_page_url"] = page.url
            item["source_page_title"] = page.title()
            links.append(item)
    return links


def link_key(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            item.get("text") or "",
            item.get("href") or "",
            item.get("onclick") or "",
            item.get("source_frame_url") or "",
        ]
    )


def merge_links(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = link_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def is_assignment_candidate(item: dict[str, Any]) -> bool:
    text = (item.get("text") or item.get("title") or "").strip()
    href = item.get("href") or ""
    onclick = (item.get("onclick") or "").lower()
    lowered_href = href.lower().strip()
    generic_tabs = {'作业', '考试', '任务', '资料', '章节'}
    if text in generic_tabs and "gotask" not in onclick:
        if not href or lowered_href.startswith("javascript:") or "mycourse/" in lowered_href:
            return False
    if "gotask" in onclick and text:
        return True
    if lowered_href.startswith(("http://", "https://")):
        return is_assignment_link(text, href)
    return False


def is_material_candidate(item: dict[str, Any]) -> bool:
    text = item.get("text") or item.get("title") or ""
    href = (item.get("href") or "").strip()
    lowered = href.lower()
    if not href or lowered.startswith("javascript:"):
        return False
    if "mycourse/" in lowered and "downloaddata" not in lowered:
        return False
    if lowered.startswith(("http://", "https://")):
        ext = Path(unquote(urlparse(href).path)).suffix.lower()
        if "downloaddata" in lowered or ext in {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"}:
            return True
        return False
    return is_material_link(text, href)


def classify_links(links: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    material_links = [item for item in links if is_material_candidate(item)]
    assignment_links = [item for item in links if is_assignment_candidate(item)]
    return material_links, assignment_links


def material_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    href = (item.get("href") or "").lower()
    text = item.get("text") or item.get("title") or ""
    ext = Path(unquote(urlparse(href).path)).suffix.lower() if href else ""
    if href.startswith(("http://", "https://")) and ("downloaddata" in href or ext):
        return (0, text)
    if href.startswith(("http://", "https://")):
        return (1, text)
    if item.get("onclick"):
        return (2, text)
    return (9, text)


def assignment_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    text = item.get("text") or item.get("title") or ""
    onclick = (item.get("onclick") or "").lower()
    if "gotask" in onclick and "\u5df2\u5b8c\u6210" in text:
        return (0, text)
    if "gotask" in onclick:
        return (1, text)
    if item.get("onclick"):
        return (2, text)
    return (9, text)


def sort_candidates(material_links: list[dict[str, Any]], assignment_links: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return sorted(material_links, key=material_sort_key), sorted(assignment_links, key=assignment_sort_key)


def page_score_from_links(page: Any, links: list[dict[str, Any]]) -> int:
    try:
        page_url = page.url
    except Exception:
        return 0
    score = 0
    for item in links:
        if item.get("source_page_url") == page_url:
            score += 1
        if "mooc2-ans" in page_url and item.get("source_page_title"):
            score += 1
    return score


def best_page_for_links(context: Any, links: list[dict[str, Any]] | None = None) -> Any | None:
    pages = list(context.pages)
    if not pages:
        return None
    links = links or []
    scored = sorted(((page_score_from_links(page, links), index, page) for index, page in enumerate(pages)), reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2]
    for page in pages:
        try:
            if "mooc" in page.url or "chaoxing" in page.url:
                return page
        except Exception:
            continue
    return pages[0]


def click_visible_text(context: Any, labels: tuple[str, ...] | list[str], base_links: list[dict[str, Any]] | None = None, timeout_ms: int = 12000) -> dict[str, Any]:
    pages = list(context.pages)
    focus = best_page_for_links(context, base_links or [])
    if focus in pages:
        pages.remove(focus)
        pages.insert(0, focus)
    for page in pages:
        try:
            page.bring_to_front()
        except Exception:
            pass
        for frame in list(page.frames):
            try:
                result = frame.evaluate(CLICK_VISIBLE_TEXT_SCRIPT, list(labels))
            except Exception:
                continue
            if result and result.get("clicked"):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8000))
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 6000))
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                result["page_url"] = page.url
                result["frame_url"] = frame.url
                return result
    return {"clicked": False, "labels": list(labels)}


def write_stage_inventory(
    course_dir: Path,
    stage: str,
    links: list[dict[str, Any]],
    material_links: list[dict[str, Any]],
    assignment_links: list[dict[str, Any]],
    click_result: dict[str, Any] | None = None,
) -> Path:
    stage_names = {"\u4f5c\u4e1a": "assignments", "\u8d44\u6599": "materials", "\u4efb\u52a1": "tasks", "\u7ae0\u8282": "chapters"}
    stage_slug = stage_names.get(stage, slugify(stage, "stage"))
    payload = {
        "stage": stage,
        "click_result": click_result or {},
        "all_links": links,
        "material_links": material_links,
        "assignment_links": assignment_links,
    }
    path = course_dir / "manifests" / f"tab_inventory_{stage_slug}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return path


def is_material_folder_candidate(item: dict[str, Any]) -> bool:
    text = (item.get("text") or item.get("title") or "").lower()
    onclick = (item.get("onclick") or "").lower()
    if "toopen(" not in onclick or "afolder" not in onclick:
        return False
    if ".zip" in text or ".rar" in text:
        return False
    return True


def explore_material_folders(context: Any, course_dir: Path, base_links: list[dict[str, Any]], timeout_ms: int = 12000, limit: int = 80) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    queue = [item for item in base_links if is_material_folder_candidate(item)]
    seen_folders: set[str] = set()
    clicks = 0
    while queue and clicks < limit:
        item = queue.pop(0)
        key = link_key(item)
        if key in seen_folders:
            continue
        seen_folders.add(key)
        click_result, _target_page = click_inventory_item(context, item, timeout_ms=timeout_ms)
        if not click_result.get("clicked"):
            continue
        clicks += 1
        links = collect_context_links(context)
        material_links, assignment_links = classify_links(links)
        stage = "materials-folder-" + safe_filename(item.get("text") or item.get("title") or "folder", "folder")
        write_stage_inventory(course_dir, stage, links, material_links, assignment_links, click_result)
        merged = merge_links(merged, links)
        new_folders = [candidate for candidate in links if is_material_folder_candidate(candidate) and link_key(candidate) not in seen_folders]
        queue = new_folders + queue
    return merged


def explore_course_tabs(context: Any, course_dir: Path, timeout_ms: int = 12000, labels: tuple[str, ...] = COURSE_TAB_LABELS) -> list[dict[str, Any]]:
    merged = collect_context_links(context)
    for label in labels:
        click_result = click_visible_text(context, (label,), merged, timeout_ms=timeout_ms)
        if not click_result.get("clicked"):
            continue
        links = collect_context_links(context)
        material_links, assignment_links = classify_links(links)
        write_stage_inventory(course_dir, label, links, material_links, assignment_links, click_result)
        merged = merge_links(merged, links)
        if label == "\u8d44\u6599":
            folder_links = explore_material_folders(context, course_dir, links, timeout_ms=timeout_ms)
            merged = merge_links(merged, folder_links)
    return merged


def auto_scroll(page: Any, rounds: int = 4, pause_ms: int = 600) -> None:
    for _ in range(max(0, rounds)):
        try:
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(pause_ms)
        except Exception:
            return


def print_candidates(title: str, links: list[dict[str, Any]], limit: int = 80) -> None:
    log(f"\n{title}: {len(links)}")
    for index, item in enumerate(links[:limit], start=1):
        label = item.get("text") or item.get("href") or "untitled"
        href = item.get("href") or ""
        log(f"  {index:02d}. {label[:90]} :: {href[:140]}")
    if len(links) > limit:
        log(f"  ... {len(links) - limit} more")


def should_confirm(args: argparse.Namespace) -> bool:
    return not args.yes and not args.list_only


def response_filename(headers: dict[str, str], title: str, url: str) -> str:
    disposition = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    name = parse_content_disposition_filename(disposition)
    if name:
        return name
    return guess_filename(title, url)


def download_material(context: Any, item: dict[str, Any], course_dir: Path, timeout_ms: int) -> Path:
    url = item.get("href") or ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("material link is not a direct HTTP(S) URL; keep it in manifest for manual handling")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept": "application/octet-stream,application/pdf,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,*/*",
    }
    referer = item.get("source_frame_url") or item.get("source_page_url") or ""
    if referer:
        headers["Referer"] = referer
    max_mb = float(os.environ.get("QIMOKAISI_MAX_MATERIAL_MB", "200"))
    max_bytes = int(max_mb * 1024 * 1024) if max_mb > 0 else 0
    probe_url = url
    try:
        redirect_probe = context.request.get(url, timeout=min(timeout_ms, 30000), headers=headers, max_redirects=0)
        location = redirect_probe.headers.get("location") or redirect_probe.headers.get("Location") or ""
        if 300 <= redirect_probe.status < 400 and location:
            probe_url = urljoin(url, location)
    except TypeError:
        probe_url = url
    except Exception:
        probe_url = url
    head_headers: dict[str, str] = {}
    try:
        head_response = context.request.head(probe_url, timeout=min(timeout_ms, 30000), headers=headers)
        head_headers = dict(head_response.headers)
        size_text = head_response.headers.get("content-length") or "0"
        size = int(size_text) if size_text.isdigit() else 0
        if max_bytes and size and size > max_bytes:
            size_mb = size / 1024 / 1024
            raise RuntimeError(f"material is {size_mb:.1f} MB, above QIMOKAISI_MAX_MATERIAL_MB={max_mb:g}")
    except RuntimeError:
        raise
    except Exception:
        head_headers = {}
    response = context.request.get(url, timeout=timeout_ms, headers=headers)
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status} while downloading")
    size_text = response.headers.get("content-length") or "0"
    size = int(size_text) if size_text.isdigit() else 0
    if max_bytes and size and size > max_bytes:
        size_mb = size / 1024 / 1024
        raise RuntimeError(f"material is {size_mb:.1f} MB, above QIMOKAISI_MAX_MATERIAL_MB={max_mb:g}")
    body = response.body()
    headers_for_name = dict(response.headers)
    headers_for_name.update({k: v for k, v in head_headers.items() if k not in headers_for_name})
    filename = response_filename(headers_for_name, item.get("text") or "material", url)
    if not Path(filename).suffix:
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = {
            "application/pdf": ".pdf",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }.get(content_type, "")
        filename = guess_filename(item.get("text") or "material", url, ext)
    destination = unique_path(course_dir / "raw" / "materials" / safe_filename(filename, "material"))
    destination.write_bytes(body)
    return destination


def save_assignment_capture(html_text: str, title: str, source_url: str, course_dir: Path) -> tuple[Path, Path, Path]:
    safe_title = safe_filename(title or "assignment", "assignment")
    html_path = unique_path(course_dir / "raw" / "assignments_html" / f"{safe_title}.html")
    md_path = course_dir / "assignments_md" / f"{html_path.stem}.md"
    questions_path = course_dir / "assignments_md" / f"{html_path.stem}.questions.json"
    html_path.write_text(html_text, encoding="utf-8", newline="\n")
    result = extract_assignment(html_text, title=safe_title, source_url=source_url)
    md_path.write_text(result["markdown"], encoding="utf-8", newline="\n")
    questions_payload = {
        "title": result["title"],
        "source_url": result["source_url"],
        "html_path": str(html_path),
        "markdown_path": str(md_path),
        "questions": result["questions"],
    }
    questions_path.write_text(json.dumps(questions_payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return html_path, md_path, questions_path


def combined_page_html(page: Any) -> str:
    parts: list[str] = []
    try:
        parts.append(f"<!-- page: {page.url} title: {page.title()} -->\n" + page.content())
    except Exception:
        pass
    for frame in list(page.frames):
        try:
            parts.append(f"\n<!-- frame: {frame.url} -->\n" + frame.content())
        except Exception:
            continue
    return "\n".join(parts)


def click_inventory_item(context: Any, item: dict[str, Any], timeout_ms: int = 12000) -> tuple[dict[str, Any], Any | None]:
    pages = list(context.pages)
    before_pages = set(pages)
    focus = best_page_for_links(context, [item])
    if focus in pages:
        pages.remove(focus)
        pages.insert(0, focus)
    target = {
        "text": item.get("text") or "",
        "title": item.get("title") or "",
        "onclick": item.get("onclick") or "",
    }
    for page in pages:
        frames = list(page.frames)
        source_frame_url = item.get("source_frame_url") or ""
        frames.sort(key=lambda frame: 0 if source_frame_url and frame.url == source_frame_url else 1)
        for frame in frames:
            try:
                result = frame.evaluate(CLICK_INVENTORY_ITEM_SCRIPT, target)
            except Exception:
                continue
            if result and result.get("clicked"):
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8000))
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 6000))
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                new_pages = [pg for pg in context.pages if pg not in before_pages]
                target_page = new_pages[-1] if new_pages else page
                result["page_url"] = target_page.url
                result["frame_url"] = frame.url
                return result, target_page
    onclick = item.get("onclick") or ""
    if "toOpen(" in onclick:
        for page in pages:
            frames = list(page.frames)
            source_frame_url = item.get("source_frame_url") or ""
            frames.sort(key=lambda frame: 0 if source_frame_url and frame.url == source_frame_url else 1)
            for frame in frames:
                if source_frame_url and frame.url != source_frame_url:
                    continue
                try:
                    result = frame.evaluate(EVAL_ONCLICK_SCRIPT, onclick)
                except Exception as exc:
                    result = {"clicked": False, "error": str(exc), "mode": "eval-onclick"}
                if result and result.get("clicked"):
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
                    except PlaywrightTimeoutError:
                        pass
                    try:
                        page.wait_for_timeout(2500)
                    except Exception:
                        pass
                    result["page_url"] = page.url
                    result["frame_url"] = frame.url
                    return result, page
    return {"clicked": False}, None


def capture_assignment_from_browser(context: Any, item: dict[str, Any], course_dir: Path, timeout_ms: int, pause: bool) -> tuple[Path, Path, Path]:
    click_result, target_page = click_inventory_item(context, item, timeout_ms=timeout_ms)
    if not click_result.get("clicked") or target_page is None:
        raise RuntimeError("assignment item is not a direct URL and could not be clicked in the current browser context")
    if pause:
        wait_for_user("Assignment page is open. Expand dynamic content if needed, then press Enter to continue: ")
    title = item.get("title") or item.get("text") or target_page.title() or "assignment"
    html_text = combined_page_html(target_page)
    source_url = click_result.get("page_url") or target_page.url
    return save_assignment_capture(html_text, title, source_url, course_dir)


def capture_assignment(context: Any, item: dict[str, Any], course_dir: Path, timeout_ms: int, pause: bool) -> tuple[Path, Path, Path]:
    url = item.get("href") or ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return capture_assignment_from_browser(context, item, course_dir, timeout_ms, pause)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 12000))
        except PlaywrightTimeoutError:
            pass
        if pause:
            wait_for_user("Assignment page is open. Expand dynamic content if needed, then press Enter to continue: ")
        title = item.get("text") or page.title() or "assignment"
        html_text = combined_page_html(page)
        return save_assignment_capture(html_text, title, url, course_dir)
    finally:
        page.close()


def write_link_inventory(course_dir: Path, links: list[dict[str, Any]], material_links: list[dict[str, Any]], assignment_links: list[dict[str, Any]]) -> None:
    inventory = {
        "all_links": links,
        "material_links": material_links,
        "assignment_links": assignment_links,
    }
    path = course_dir / "manifests" / "link_inventory.json"
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def make_course_dir(args: argparse.Namespace) -> Path:
    if args.course:
        return Path(args.course).resolve()
    slug = args.course_slug or slugify(args.course_name or "xuexitong-course")
    return (Path(args.data_root).resolve() / slug)


def main() -> int:
    parser = argparse.ArgumentParser(description="Semi-automatic collector for authorized Xuexitong/Chaoxing courses.")
    parser.add_argument("--course-url", default="", help="Course URL to open. If omitted, opens Xuexitong home and asks you to navigate.")
    parser.add_argument("--course-name", default="Xuexitong Course", help="Human-readable course name used for the local folder slug.")
    parser.add_argument("--course-slug", default="", help="Optional stable course folder slug.")
    parser.add_argument("--course", default="", help="Explicit course directory. Overrides --data-root/--course-name.")
    parser.add_argument("--data-root", default="data/courses", help="Root directory for local course databases.")
    parser.add_argument("--profile-dir", default="data/browser-profile", help="Dedicated Chrome profile directory.")
    parser.add_argument("--chrome-path", default=DEFAULT_CHROME, help="Path to local Chrome executable.")
    parser.add_argument("--download-timeout-ms", type=int, default=60000)
    parser.add_argument("--login-wait-seconds", type=int, default=480, help="Seconds to wait for an existing profile login or manual course navigation before prompting.")
    parser.add_argument("--max-material-mb", type=float, default=200, help="Skip direct material downloads larger than this size. Set 0 to disable the guard.")
    parser.add_argument("--material-limit", type=int, default=0, help="Optional max material downloads for smoke tests.")
    parser.add_argument("--assignment-limit", type=int, default=0, help="Optional max assignment captures for smoke tests.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation after listing candidates.")
    parser.add_argument("--list-only", action="store_true", help="Only list candidates and write manifests; do not download or capture.")
    parser.add_argument("--skip-login-wait", action="store_true", help="Do not pause for manual login/navigation after opening Chrome.")
    parser.add_argument("--no-explore-tabs", action="store_true", help="Do not click safe course tabs such as assignments/materials before listing candidates.")
    parser.add_argument("--pause-each-assignment", action="store_true", help="Pause on each assignment page so the user can expand dynamic content.")
    args = parser.parse_args()
    if args.max_material_mb and args.max_material_mb > 0:
        os.environ["QIMOKAISI_MAX_MATERIAL_MB"] = str(args.max_material_mb)

    course_dir = make_course_dir(args)
    ensure_course_dirs(course_dir)
    profile_dir = Path(args.profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    manifest = CourseManifest(course_dir / "manifests" / "course_manifest.json")

    chrome_path = Path(args.chrome_path)
    executable_path = str(chrome_path) if chrome_path.exists() else None
    if executable_path is None:
        log(f"Chrome executable not found at {chrome_path}; Playwright will try its default browser resolution.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=executable_path,
            headless=False,
            accept_downloads=True,
            downloads_path=str((course_dir / "work" / "downloads").resolve()),
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            target_url = args.course_url or DEFAULT_HOME
            log(f"Opening: {target_url}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=args.download_timeout_ms)
        except PlaywrightTimeoutError:
            log("Initial navigation timed out; continuing with the visible page.")

        pre_links: list[dict[str, Any]] = []
        if not args.skip_login_wait:
            pre_links = wait_for_course_context(context, seconds=args.login_wait_seconds)
            if course_signal_score(pre_links) < 6:
                wait_for_user("Log in and navigate to the target course in Chrome, then press Enter to continue: ")

        auto_scroll(page)
        links = collect_context_links(context)
        if not args.no_explore_tabs:
            log("Exploring safe course tabs: assignments/materials/tasks/chapters")
            links = explore_course_tabs(context, course_dir, timeout_ms=args.download_timeout_ms)
        material_links, assignment_links = classify_links(links)
        material_links, assignment_links = sort_candidates(material_links, assignment_links)
        if not args.course and not args.course_slug and args.course_name == "Xuexitong Course":
            detected_meta = detect_course_meta(context, course_dir, args, links)
            detected_slug = slugify(detected_meta.get("course_title") or course_dir.name, "xuexitong-course")
            auto_course_dir = (Path(args.data_root).resolve() / detected_slug)
            if auto_course_dir.resolve() != course_dir.resolve():
                course_dir = auto_course_dir
                ensure_course_dirs(course_dir)
                manifest = CourseManifest(course_dir / "manifests" / "course_manifest.json")
                log(f"Auto course directory: {course_dir}")
        course_meta = write_course_meta(course_dir, context, args, links)
        write_last_course_dir(course_dir, args)
        log(f"Course meta: {course_meta.get('course_title')} -> {course_dir}")

        if args.material_limit > 0:
            material_links = material_links[: args.material_limit]
        if args.assignment_limit > 0:
            assignment_links = assignment_links[: args.assignment_limit]

        print_candidates("Candidate material links", material_links)
        print_candidates("Candidate assignment/quiz links", assignment_links)
        write_link_inventory(course_dir, links, material_links, assignment_links)

        for item in material_links:
            manifest.upsert("material", item.get("text") or "material", item.get("href") or "", item.get("source_frame_url") or "", item)
        for item in assignment_links:
            manifest.upsert("assignment", item.get("text") or "assignment", item.get("href") or "", item.get("source_frame_url") or "", item)
        manifest.save()

        if args.list_only:
            log(f"List-only mode complete: {course_dir}")
            context.close()
            return 0

        if should_confirm(args):
            answer = input("Continue downloading materials and capturing assignments? Type y to continue: ").strip().lower()
            if answer not in {"y", "yes"}:
                log("Cancelled by user after listing candidates.")
                context.close()
                return 0

        for item in material_links:
            manifest_item = manifest.upsert("material", item.get("text") or "material", item.get("href") or "", item.get("source_frame_url") or "", item)
            if manifest_item.status == "done":
                log(f"SKIP material already done: {manifest_item.title}")
                continue
            try:
                path = download_material(context, item, course_dir, args.download_timeout_ms)
                manifest.mark_done(manifest_item.id, path, course_dir)
                log(f"DONE material: {path}")
            except Exception as exc:
                manifest.mark_failed(manifest_item.id, str(exc))
                log(f"FAILED material: {manifest_item.title}: {exc}")
            manifest.save()

        for item in assignment_links:
            manifest_item = manifest.upsert("assignment", item.get("text") or "assignment", item.get("href") or "", item.get("source_frame_url") or "", item)
            if manifest_item.status == "done":
                log(f"SKIP assignment already done: {manifest_item.title}")
                continue
            try:
                _html_path, md_path, _questions_path = capture_assignment(
                    context, item, course_dir, args.download_timeout_ms, args.pause_each_assignment
                )
                manifest.mark_done(manifest_item.id, md_path, course_dir)
                log(f"DONE assignment: {md_path}")
            except Exception as exc:
                manifest.mark_failed(manifest_item.id, str(exc))
                log(f"FAILED assignment: {manifest_item.title}: {exc}")
            manifest.save()

        context.close()

    write_last_course_dir(course_dir, args)
    log(f"Course collection complete: {course_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
