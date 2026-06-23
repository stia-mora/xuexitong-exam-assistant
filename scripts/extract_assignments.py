"""Convert captured Xuexitong assignment HTML pages to clean Markdown and question JSON."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

try:
    from markdownify import markdownify as md
except ModuleNotFoundError:
    def md(html_text: str, **_kwargs: object) -> str:
        return BeautifulSoup(html_text, "html.parser").get_text("\n", strip=True)

QUESTION_RE = re.compile(
    "^\\s*(?:\\u7b2c\\s*)?(?P<num>\\d{1,3}|[\\u4e00\\u4e8c\\u4e09\\u56db\\u4e94\\u516d\\u4e03\\u516b\\u4e5d\\u5341\\u767e]{1,5})\\s*(?:\\u9898|[.\\u3001\\uff0e)])\\s*(?P<body>.+)?$"
)
TYPED_QUESTION_RE = re.compile(
    "^\\s*[\\[\\u3010](?P<type>\\u5355\\u9009\\u9898|\\u591a\\u9009\\u9898|\\u5224\\u65ad\\u9898|\\u586b\\u7a7a\\u9898|\\u7b80\\u7b54\\u9898|\\u8bba\\u8ff0\\u9898|\\u8d44\\u6599\\u9898|\\u95ee\\u7b54\\u9898)[\\]\\u3011]\\s*(?P<body>.+)?$"
)
OPTION_RE = re.compile("^\\s*(?P<label>[A-H\\uff21-\\uff28])\\s*[.\\u3001\\uff0e)]\\s*(?P<body>.+)$", re.IGNORECASE)
ANSWER_RE = re.compile("(?:\\u6b63\\u786e\\u7b54\\u6848|\\u53c2\\u8003\\u7b54\\u6848|\\u7b54\\u6848|\\u6211\\u7684\\u7b54\\u6848)\\s*[:\\uff1a]\\s*(?P<body>.+)")
EXPLANATION_RE = re.compile("(?:\\u89e3\\u6790|\\u7b54\\u6848\\u89e3\\u6790|\\u8bb2\\u89e3)\\s*[:\\uff1a]\\s*(?P<body>.+)")
QUESTION_TYPE_RE = re.compile(r"[（(]\s*(?P<type>[^）)]+题)\s*[）)]")
QUESTION_NUMBER_RE = re.compile(r"^\s*(?P<num>\d{1,3})\s*[.、．)]?")
NOISE_LINE_RE = re.compile(
    r"^(提示|知道了|换一张|确定|取消|暂时保存|提交|作业|作业作答|点击上传|段落格式|字体|字号|名称|版本|描述|Pandas|NumPy|SciPy|C\s*语言编译配置|编译器版本|C语言标准)$",
    re.I,
)


def clean_markdown(text: str) -> str:
    lines: list[str] = []
    blank = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            if not blank:
                lines.append("")
            blank = True
            continue
        lines.append(line)
        blank = False
    return "\n".join(lines).strip() + "\n"


def clean_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"^\s*\d{1,3}\s*[.、．)]\s*", "", text)
    return text.strip()


def normalize_option_label(label: str) -> str:
    table = str.maketrans("\uff21\uff22\uff23\uff24\uff25\uff26\uff27\uff28", "ABCDEFGH")
    return (label or "").upper().translate(table).strip()


def normalize_url(src: str, base_url: str = "") -> str:
    value = (src or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if base_url:
        return urljoin(base_url, value)
    return value


def is_noise_text(text: str) -> bool:
    lines = [clean_text(line) for line in (text or "").splitlines() if clean_text(line)]
    if not lines:
        return True
    if all(NOISE_LINE_RE.match(line) for line in lines[:12]):
        return True
    joined = " ".join(lines)
    noisy_tokens = ["此附件仅支持打开", "Python 3.", "已安装的第三方库", "很抱歉，您所浏览的页面不存在", "mooc2-"]
    return any(token in joined for token in noisy_tokens) and len(joined) < 500


def soup_without_noise(html_text: str) -> BeautifulSoup:
    soup = BeautifulSoup(html_text, "html.parser")
    for node in soup(["script", "style", "noscript", "iframe"]):
        node.decompose()
    for selector in [
        ".AlertCon02", ".maskDiv", ".popDiv", ".popClose", ".codeDiv", ".editorToolBar",
        ".leftCard", ".dtk", ".answerCard", ".submit_div", ".workSubmit", ".fixedBottom",
    ]:
        for node in soup.select(selector):
            node.decompose()
    return soup


def node_text(node: Tag | None) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "html.parser")
    root = clone.find()
    if root is None:
        return ""
    for selector in [
        ".stem_answer", ".answerBg", ".stuAnswerArea", ".answerArea", ".workAttach", ".attach", ".attachNew",
        "input", "textarea", "button", "script", "style", ".clear", ".score", ".fontList", ".langList",
    ]:
        for child in root.select(selector):
            child.decompose()
    text = root.get_text("\n", strip=True)
    lines = [clean_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line and not NOISE_LINE_RE.match(line)]
    text = "\n".join(lines)
    text = QUESTION_TYPE_RE.sub("", text)
    text = re.sub(r"^\s*\d{1,3}\s*[.、．)]\s*", "", text).strip()
    return clean_markdown(text).strip()


def extract_images(node: Tag | None, base_url: str = "") -> list[dict[str, str]]:
    if node is None:
        return []
    images: list[dict[str, str]] = []
    for img in node.select("img"):
        src = normalize_url(img.get("data-original") or img.get("src") or "", base_url)
        if not src or "popClose" in src or src in {"https:", "http:"}:
            continue
        alt = clean_text(img.get("alt") or img.get("aria-label") or "")
        item = {"src": src}
        if alt:
            item["alt"] = alt
        if item not in images:
            images.append(item)
    return images


def html_fragment_to_markdown(node: Tag | None, base_url: str = "") -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "html.parser")
    root = clone.find()
    if root is None:
        return ""
    for selector in ["script", "style", "input", "textarea", "button", ".num_option", ".choiceAnswer", ".score"]:
        for child in root.select(selector):
            child.decompose()
    for img in root.select("img"):
        src = normalize_url(img.get("data-original") or img.get("src") or "", base_url)
        if src:
            img["src"] = src
    text = md(str(root), heading_style="ATX", bullets="-")
    return clean_markdown(text).strip()


def find_question_nodes(soup: BeautifulSoup) -> list[Tag]:
    selectors = [
        ".questionLi[typename]",
        "div[id^='question'][typename]",
        ".questionLi",
        ".TiMu div[id^='question']",
    ]
    nodes: list[Tag] = []
    seen: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            if not isinstance(node, Tag):
                continue
            ident = id(node)
            if ident in seen:
                continue
            has_stem = bool(node.select_one(".mark_name, .stem_answer, .answerBg"))
            if not has_stem:
                continue
            nodes.append(node)
            seen.add(ident)
    return nodes


def extract_question_number(node: Tag, fallback: int) -> int:
    for value in [node.get("aria-label") or "", node.get_text(" ", strip=True)[:40]]:
        match = re.search(r"题目\s*(\d{1,3})", value) or QUESTION_NUMBER_RE.search(value)
        if match:
            try:
                return int(match.group(1) if match.lastindex else match.group("num"))
            except Exception:
                pass
    return fallback


def extract_question_type(node: Tag, heading: Tag | None = None) -> str:
    for value in [node.get("typename") or "", node.get("data-type") or ""]:
        value = clean_text(value)
        if value:
            return value
    text = heading.get_text(" ", strip=True) if heading else node.get_text(" ", strip=True)[:80]
    match = QUESTION_TYPE_RE.search(text)
    return clean_text(match.group("type")) if match else "unknown"


def extract_options_from_dom(node: Tag, base_url: str = "") -> list[dict[str, Any]]:
    option_nodes = node.select(".stem_answer .answerBg, .stem_answer [role='radio'], .stem_answer [role='checkbox']")
    if not option_nodes:
        option_nodes = node.select(".answerBg")
    options: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    fallback_label = ord("A")
    for option_node in option_nodes:
        if not isinstance(option_node, Tag):
            continue
        label_node = option_node.select_one(".num_option, [class*='num_option']")
        label = normalize_option_label(label_node.get_text(" ", strip=True) if label_node else "")
        if not label:
            aria = option_node.get("aria-label") or ""
            match = re.match(r"\s*([A-HＡ-Ｈ])", aria)
            label = normalize_option_label(match.group(1)) if match else chr(fallback_label)
        fallback_label += 1
        if label in seen_labels:
            continue
        seen_labels.add(label)
        content_node = option_node.select_one(".answer_p") or option_node
        text = html_fragment_to_markdown(content_node, base_url=base_url)
        text = re.sub(rf"^\s*{re.escape(label)}\s*", "", text).strip()
        images = extract_images(content_node, base_url=base_url)
        if not text and images:
            text = " ".join(f"![option {label}]({img['src']})" for img in images)
        options.append({"label": label, "text": text, "images": images})
    return options


def extract_answer_text(node: Tag, qid: str = "") -> str:
    candidates: list[str] = []
    for selector in [".rightAnswer", ".correctAnswer", ".answerCorrect", ".standardAnswer", ".green", ".colorGreen"]:
        for item in node.select(selector):
            text = clean_text(item.get_text(" ", strip=True))
            if text and text not in candidates:
                candidates.append(text)
    for input_node in node.select("input[id^='answer'], input[name^='answer']"):
        input_id = str(input_node.get("id") or input_node.get("name") or "")
        if re.match(r"answertype", input_id, flags=re.I):
            continue
        if not re.match(r"answer\d+$", input_id, flags=re.I):
            continue
        value = clean_text(input_node.get("value") or "")
        if value and value not in candidates:
            candidates.append(value)
    for text in candidates:
        match = ANSWER_RE.search(text)
        if match:
            return match.group("body").strip()
    return "；".join(candidates)


def extract_explanation_text(node: Tag) -> str:
    candidates: list[str] = []
    for selector in [".analysis", ".answerAnalysis", ".jiexi", ".explain", ".Py_answer"]:
        for item in node.select(selector):
            text = clean_text(item.get_text(" ", strip=True))
            if text and text not in candidates:
                candidates.append(text)
    for text in candidates:
        match = EXPLANATION_RE.search(text)
        if match:
            return match.group("body").strip()
    return "；".join(candidates)


def extract_questions_from_dom(html_text: str, source_url: str = "") -> list[dict[str, Any]]:
    soup = soup_without_noise(html_text)
    questions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, node in enumerate(find_question_nodes(soup), start=1):
        heading = node.select_one("h3.mark_name, .mark_name")
        q_number = extract_question_number(node, index)
        q_type = extract_question_type(node, heading)
        question_markdown = node_text(heading) or node_text(node)
        options = extract_options_from_dom(node, base_url=source_url)
        images = extract_images(heading, base_url=source_url)
        if not question_markdown and images:
            question_markdown = "（图片题，见题干图片）"
        source_key = str(node.get("data") or node.get("qid") or node.get("id") or "")
        dedupe_key = source_key or f"{q_number}|{q_type}|{question_markdown[:120]}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        answer = extract_answer_text(node, qid=str(node.get("data") or ""))
        explanation = extract_explanation_text(node)
        quality_flags: list[str] = []
        if not question_markdown or is_noise_text(question_markdown):
            quality_flags.append("empty_or_noise_stem")
        if q_type in {"单选题", "多选题", "判断题"} and not options:
            quality_flags.append("choice_question_without_options")
        question = {
            "id": f"q{len(questions) + 1:04d}",
            "number": q_number,
            "type": q_type,
            "question": question_markdown,
            "options": options,
            "answer": answer,
            "explanation": explanation,
            "images": images,
            "source_dom_id": node.get("id") or "",
            "source_qid": str(node.get("data") or node.get("qid") or ""),
            "extraction_method": "xuexitong_dom",
            "quality_flags": quality_flags,
        }
        if "empty_or_noise_stem" not in quality_flags or images or options:
            questions.append(question)
    questions.sort(key=lambda item: (int(item.get("number") or 999999), item.get("id") or ""))
    for i, question in enumerate(questions, start=1):
        question["id"] = f"q{i:04d}"
    return questions


def visible_text_from_html(html_text: str) -> str:
    soup = soup_without_noise(html_text)
    return clean_markdown(soup.get_text("\n", strip=True))


def flush_question(questions: list[dict[str, Any]], current: dict[str, Any] | None) -> None:
    if not current:
        return
    text = "\n".join(current.pop("_lines", [])).strip()
    if text:
        current["question"] = text
    if current.get("question") or current.get("options"):
        if is_noise_text(current.get("question", "")):
            return
        current.setdefault("type", "unknown")
        current.setdefault("answer", "")
        current.setdefault("explanation", "")
        current.setdefault("extraction_method", "text_fallback")
        current.setdefault("quality_flags", ["text_fallback"])
        current["id"] = f"q{len(questions) + 1:04d}"
        questions.append(current)


def extract_questions_from_text(text: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_option: dict[str, str] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or NOISE_LINE_RE.match(line):
            continue

        typed = TYPED_QUESTION_RE.match(line)
        numbered = QUESTION_RE.match(line)
        option = OPTION_RE.match(line)
        answer = ANSWER_RE.search(line)
        explanation = EXPLANATION_RE.search(line)

        if typed or numbered:
            body = (typed.group("body") if typed else numbered.group("body")) or ""
            q_type = typed.group("type") if typed else "unknown"
            flush_question(questions, current)
            current = {"type": q_type, "question": body.strip(), "options": [], "_lines": []}
            last_option = None
            continue

        if current is None and (option or answer or explanation):
            current = {"type": "unknown", "question": "", "options": [], "_lines": []}

        if current is not None and option:
            last_option = {"label": normalize_option_label(option.group("label")), "text": option.group("body").strip(), "images": []}
            current.setdefault("options", []).append(last_option)
            continue

        if current is not None and answer:
            current["answer"] = answer.group("body").strip()
            continue

        if current is not None and explanation:
            current["explanation"] = explanation.group("body").strip()
            continue

        if current is not None:
            if last_option is not None and len(line) < 120 and not re.match(r"^[#>*-]", line):
                last_option["text"] = (last_option["text"] + " " + line).strip()
            else:
                current.setdefault("_lines", []).append(line)
                last_option = None

    flush_question(questions, current)
    return questions


def questions_to_markdown(title: str, questions: list[dict[str, Any]], source_url: str = "") -> str:
    lines = [f"# {title}", ""]
    if source_url:
        lines.extend([f"Source: {source_url}", ""])
    if not questions:
        lines.append("暂无可提取题目。")
        return clean_markdown("\n".join(lines))
    current_type = ""
    for question in questions:
        q_type = question.get("type") or "unknown"
        if q_type != current_type:
            current_type = q_type
            lines.extend(["", f"## {current_type}", ""])
        number = question.get("number") or len(lines)
        lines.append(f"### {number}. ({q_type}) {question.get('question', '').strip()}")
        for img in question.get("images") or []:
            lines.append(f"![题干图片]({img.get('src')})")
        options = question.get("options") or []
        if options:
            lines.append("")
            for option in options:
                text = option.get("text") or ""
                label = option.get("label") or ""
                lines.append(f"{label}. {text}".rstrip())
                for img in option.get("images") or []:
                    if img.get("src") and img.get("src") not in text:
                        lines.append(f"   ![选项{label}图片]({img.get('src')})")
        if question.get("answer"):
            lines.append(f"\n答案：{question['answer']}")
        if question.get("explanation"):
            lines.append(f"解析：{question['explanation']}")
        if question.get("quality_flags"):
            lines.append(f"提取标记：{', '.join(question['quality_flags'])}")
        lines.append("")
    return clean_markdown("\n".join(lines))


def html_to_markdown(html_text: str, title: str = "", source_url: str = "") -> str:
    questions = extract_questions_from_dom(html_text, source_url=source_url)
    if questions:
        return questions_to_markdown(title or "Xuexitong Assignment", questions, source_url=source_url)
    soup = soup_without_noise(html_text)
    page_title = title or (soup.title.get_text(" ", strip=True) if soup.title else "Xuexitong Assignment")
    body = soup.select_one(".TiMu") or soup.body or soup
    content = md(str(body), heading_style="ATX", bullets="-")
    header = [f"# {page_title}"]
    if source_url:
        header.append(f"\nSource: {source_url}")
    return clean_markdown("\n\n".join(header) + "\n\n" + content)


def extract_assignment(html_text: str, title: str = "", source_url: str = "") -> dict[str, Any]:
    questions = extract_questions_from_dom(html_text, source_url=source_url)
    method = "xuexitong_dom"
    if not questions:
        visible_text = visible_text_from_html(html_text)
        questions = extract_questions_from_text(visible_text)
        method = "text_fallback"
    markdown = questions_to_markdown(title or "Xuexitong Assignment", questions, source_url=source_url) if questions else html_to_markdown(html_text, title=title, source_url=source_url)
    return {
        "title": title or "Xuexitong Assignment",
        "source_url": source_url,
        "markdown": markdown,
        "questions": questions,
        "extraction_method": method,
    }


def write_assignment_outputs(html_path: Path, md_path: Path, questions_path: Path, title: str = "", source_url: str = "") -> dict[str, Any]:
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    result = extract_assignment(html_text, title=title or html_path.stem, source_url=source_url)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    questions_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(result["markdown"], encoding="utf-8", newline="\n")
    questions_payload = {
        "title": result["title"],
        "source_url": result["source_url"],
        "html_path": str(html_path),
        "markdown_path": str(md_path),
        "extraction_method": result.get("extraction_method") or "unknown",
        "question_count": len(result["questions"]),
        "questions": result["questions"],
    }
    questions_path.write_text(json.dumps(questions_payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return questions_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert captured assignment HTML files to Markdown and structured question JSON.")
    parser.add_argument("--html-dir", required=True, help="Directory containing captured assignment HTML files.")
    parser.add_argument("--output-dir", required=True, help="Directory for Markdown and question JSON outputs.")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even if they already exist.")
    args = parser.parse_args()

    html_dir = Path(args.html_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not html_dir.exists():
        raise SystemExit(f"HTML directory does not exist: {html_dir}")

    count = 0
    for html_path in sorted(html_dir.glob("*.html")):
        md_path = output_dir / f"{html_path.stem}.md"
        questions_path = output_dir / f"{html_path.stem}.questions.json"
        if md_path.exists() and questions_path.exists() and not args.force:
            continue
        write_assignment_outputs(html_path, md_path, questions_path)
        count += 1
    print(f"processed={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
