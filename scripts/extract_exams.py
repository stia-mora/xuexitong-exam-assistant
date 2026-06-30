"""Extract accessible Xuexitong/Chaoxing exam review pages into questions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from extract_assignments import (
    clean_markdown,
    clean_text,
    extract_images,
    extract_options_from_dom,
    html_fragment_to_markdown,
    normalize_option_label,
    soup_without_noise,
)


QUESTION_TYPE_RE = re.compile(r"(单选题|多选题|判断题|填空题|简答题|论述题|设计题|问答题|主观题|材料题)")
QUESTION_NUMBER_RE = re.compile(r"^\s*(?:第\s*)?(?P<num>\d{1,3})\s*(?:题|[.、．)])")
OPTION_RE = re.compile(r"^\s*(?P<label>[A-HＡ-Ｈ])\s*[.、．)]\s*(?P<body>.+)$", re.I)
POINTS_RE = re.compile(r"(?P<points>\d+(?:\.\d+)?)\s*分")
UNAVAILABLE_MARKERS = (
    "404",
    "400",
    "页面不存在",
    "页面暂时不能访问",
    "未开放",
    "暂未开放",
    "没有权限",
    "无权访问",
    "暂无考试",
    "暂无数据",
    "考试未开始",
    "老师未开放",
    "不可查看",
    "您没有查看权限",
)
NOISE_LINE_RE = re.compile(r"^(AI讲解|智能分析|待批阅|返回|题量[:：]|满分[:：]|考试时间[:：]|知识点[:：]?)$")
ANSWER_SIGNAL_RE = re.compile(r"(我的答案|正确答案|参考答案|答案解析|解析)")


class ExamUnavailableError(RuntimeError):
    """Raised when an exam page is accessible but contains no usable questions."""


def visible_text(html_text: str) -> str:
    soup = soup_without_noise(html_text)
    for selector in [".answerCard", ".dtk", ".rightCard", ".nav", ".fixed", ".sideBar"]:
        for node in soup.select(selector):
            node.decompose()
    return clean_markdown(soup.get_text("\n", strip=True))


def detect_unavailable(html_text: str) -> str:
    text = visible_text(html_text)
    has_question_signal = bool(QUESTION_NUMBER_RE.search(text) or QUESTION_TYPE_RE.search(text))
    for marker in UNAVAILABLE_MARKERS:
        if marker in text and not has_question_signal:
            return marker
    return ""


def extract_exam_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ["h1", ".exam-title", ".paper-title", ".test-title", ".title", "title"]:
        node = soup.select_one(selector)
        text = clean_text(node.get_text(" ", strip=True) if node else "")
        if text and text not in {"考试详情", "查看详情"}:
            return text[:160]
    return clean_text(fallback or "exam")


def full_node_lines(node: Tag) -> list[str]:
    text = node.get_text("\n", strip=True)
    lines = [clean_text(line) for line in text.splitlines()]
    return [line for line in lines if line and not NOISE_LINE_RE.match(line)]


def clean_stem(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text)
    text = re.sub(r"^\s*\d{1,3}\s*[.、．)]\s*", "", text)
    text = re.sub(r"^[（(]\s*[^）)]*题[^）)]*[）)]\s*", "", text)
    text = re.sub(r"^\s*\(?\s*[^()（）]*题\s*,?\s*\d+(?:\.\d+)?\s*分\s*\)?\s*", "", text)
    return clean_text(text)


def find_question_nodes(soup: BeautifulSoup) -> list[Tag]:
    selectors = [
        ".questionLi[typename]",
        "div[id^='question'][typename]",
        ".questionLi",
        ".TiMu div[id^='question']",
        ".paperQuestion",
        ".questionItem",
        ".question-item",
        ".mark_item",
    ]
    nodes: list[Tag] = []
    seen: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            if not isinstance(node, Tag):
                continue
            if id(node) in seen:
                continue
            text = clean_text(node.get_text(" ", strip=True))
            if not text or not (QUESTION_NUMBER_RE.search(text) or QUESTION_TYPE_RE.search(text)):
                continue
            if any(existing in node.parents for existing in nodes):
                continue
            nodes.append(node)
            seen.add(id(node))
    return nodes


def parse_options_from_lines(lines: list[str]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = OPTION_RE.match(line)
        if match:
            label = normalize_option_label(match.group("label"))
            body = clean_text(match.group("body"))
        else:
            label_match = re.match(r"^\s*([A-HＡ-Ｈ])\s*[.、．)]?\s*$", line, re.I)
            if not label_match or index + 1 >= len(lines):
                continue
            label = normalize_option_label(label_match.group(1))
            body = clean_text(lines[index + 1])
            if not body or OPTION_RE.match(body) or ANSWER_SIGNAL_RE.search(body) or QUESTION_NUMBER_RE.match(body):
                continue
        if not label or not body:
            continue
        if label in seen:
            continue
        seen.add(label)
        options.append({"label": label, "text": body, "images": []})
    return options


def slice_after_label(text: str, labels: tuple[str, ...], stop_labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    stop_pattern = "|".join(re.escape(label) for label in stop_labels)
    match = re.search(rf"(?:{label_pattern})\s*[:：]\s*(?P<body>.+?)(?=(?:{stop_pattern})\s*[:：]|$)", text, flags=re.S)
    return clean_text(match.group("body")) if match else ""


def extract_answers(lines: list[str]) -> tuple[str, str, str]:
    text = "\n".join(lines)
    my_answer = slice_after_label(text, ("我的答案",), ("正确答案", "答案解析", "解析", "知识点", "AI讲解"))
    answer = slice_after_label(text, ("正确答案", "参考答案"), ("答案解析", "解析", "知识点", "AI讲解"))
    if not answer:
        answer = slice_after_label(text, ("答案",), ("答案解析", "解析", "知识点", "AI讲解"))
    explanation = slice_after_label(text, ("答案解析", "解析"), ("知识点", "AI讲解"))
    return my_answer, answer, explanation


def clean_answer_value(answer: str, q_type: str) -> str:
    text = clean_text(answer)
    text = re.split(r"(?:知识点|教师批语|AI讲解)\s*[:：]?", text, maxsplit=1)[0]
    text = clean_text(text)
    if text in {"教师批语", "教师批语："}:
        return ""
    if q_type in {"单选题", "多选题", "选择题"}:
        match = re.match(r"^([A-HＡ-Ｈ]+)", text, re.I)
        if match:
            return normalize_option_label(match.group(1))
    if q_type == "判断题":
        match = re.match(r"^(对|错|正确|错误|√|×)", text)
        if match:
            value = match.group(1)
            return "对" if value in {"对", "正确", "√"} else "错"
    return text


def extract_question_type(node: Tag, lines: list[str]) -> str:
    for value in [node.get("typename") or "", node.get("data-type") or ""]:
        text = clean_text(value)
        if text:
            match = QUESTION_TYPE_RE.search(text)
            return match.group(1) if match else text
    joined = " ".join(lines[:4])
    match = QUESTION_TYPE_RE.search(joined)
    return match.group(1) if match else "unknown"


def extract_question_number(lines: list[str], fallback: int) -> int:
    joined = " ".join(lines[:3])
    match = QUESTION_NUMBER_RE.search(joined)
    if match:
        try:
            return int(match.group("num"))
        except Exception:
            pass
    return fallback


def extract_points(lines: list[str]) -> str:
    joined = " ".join(lines[:4])
    match = POINTS_RE.search(joined)
    return match.group("points") if match else ""


def extract_stem(node: Tag, lines: list[str], source_url: str) -> str:
    for selector in [".mark_name", ".question-stem", ".stem", ".stemContent", ".subject", ".q-stem"]:
        candidate = node.select_one(selector)
        if candidate:
            text = html_fragment_to_markdown(candidate, base_url=source_url)
            text = clean_stem(text)
            if text:
                return text
    stem_lines: list[str] = []
    for line in lines:
        if OPTION_RE.match(line) or any(label in line for label in ("我的答案", "正确答案", "答案解析", "解析")):
            break
        stem_lines.append(line)
    return clean_stem(" ".join(stem_lines))


def extract_questions_from_dom(html_text: str, source_url: str = "") -> list[dict[str, Any]]:
    soup = soup_without_noise(html_text)
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(find_question_nodes(soup), start=1):
        lines = full_node_lines(node)
        q_number = extract_question_number(lines, index)
        q_type = extract_question_type(node, lines)
        stem = extract_stem(node, lines, source_url)
        options = extract_options_from_dom(node, base_url=source_url) or parse_options_from_lines(lines)
        my_answer, answer, explanation = extract_answers(lines)
        my_answer = clean_answer_value(my_answer, q_type)
        answer = clean_answer_value(answer, q_type)
        images = extract_images(node, base_url=source_url)
        if not stem and images:
            stem = "（图片题，见题干图片）"
        dedupe_key = f"{q_type}|{stem[:160]}"
        if not stem or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        quality_flags: list[str] = []
        if not answer:
            quality_flags.append("missing_answer")
        if not explanation:
            quality_flags.append("missing_analysis")
        if q_type in {"单选题", "多选题", "判断题"} and not options:
            quality_flags.append("missing_options")
        questions.append(
            {
                "id": f"q{len(questions) + 1:04d}",
                "number": q_number,
                "type": q_type,
                "points": extract_points(lines),
                "question": stem,
                "options": options,
                "my_answer": my_answer,
                "answer": answer,
                "explanation": explanation,
                "images": images,
                "source_dom_id": node.get("id") or "",
                "source_qid": str(node.get("data") or node.get("qid") or ""),
                "extraction_method": "xuexitong_exam_dom",
                "quality_flags": quality_flags,
            }
        )
    return questions


def extract_questions_from_text(text: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        lines = current.pop("_lines", [])
        my_answer, answer, explanation = extract_answers(lines)
        current["my_answer"] = my_answer
        current["answer"] = clean_answer_value(answer, current.get("type") or "")
        current["explanation"] = explanation
        current["options"] = parse_options_from_lines(lines)
        current["question"] = clean_stem(current.get("question") or "")
        flags: list[str] = ["text_fallback"]
        if not current["answer"]:
            flags.append("missing_answer")
        if not current["explanation"]:
            flags.append("missing_analysis")
        current["quality_flags"] = flags
        if current["question"]:
            current["id"] = f"q{len(questions) + 1:04d}"
            questions.append(current)
        current = None

    for raw in text.splitlines():
        line = clean_text(raw)
        if not line or NOISE_LINE_RE.match(line):
            continue
        match = QUESTION_NUMBER_RE.match(line)
        if match:
            if not (QUESTION_TYPE_RE.search(line) or ANSWER_SIGNAL_RE.search(line)):
                continue
            flush()
            current = {
                "number": int(match.group("num")),
                "type": QUESTION_TYPE_RE.search(line).group(1) if QUESTION_TYPE_RE.search(line) else "unknown",
                "points": POINTS_RE.search(line).group("points") if POINTS_RE.search(line) else "",
                "question": clean_stem(line),
                "_lines": [line],
                "images": [],
                "extraction_method": "xuexitong_exam_text",
            }
            continue
        if current is not None:
            current["_lines"].append(line)
            if not OPTION_RE.match(line) and not any(label in line for label in ("我的答案", "正确答案", "答案解析", "解析", "知识点")):
                current["question"] = clean_text(f"{current.get('question', '')} {line}")
    flush()
    return questions


def questions_to_markdown(title: str, questions: list[dict[str, Any]], source_url: str = "") -> str:
    lines = [f"# {title}", ""]
    if source_url:
        lines.extend([f"Source URL: {source_url}", ""])
    if not questions:
        lines.append("未提取到考试题。")
        return "\n".join(lines).strip() + "\n"
    for question in questions:
        points = f", {question.get('points')} 分" if question.get("points") else ""
        lines.append(f"### {question.get('number')}. ({question.get('type')}{points}) {question.get('question')}")
        for option in question.get("options") or []:
            lines.append(f"- {option.get('label')}. {option.get('text')}")
        if question.get("my_answer"):
            lines.append(f"我的答案：{question['my_answer']}")
        if question.get("answer"):
            lines.append(f"正确答案：{question['answer']}")
        if question.get("explanation"):
            lines.append(f"答案解析：{question['explanation']}")
        if question.get("quality_flags"):
            lines.append(f"提取标记：{', '.join(question['quality_flags'])}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def extract_exam(html_text: str, title: str = "", source_url: str = "") -> dict[str, Any]:
    unavailable_reason = detect_unavailable(html_text)
    soup = soup_without_noise(html_text)
    final_title = extract_exam_title(soup, title)
    questions = extract_questions_from_dom(html_text, source_url=source_url)
    text = visible_text(html_text)
    if not questions and not unavailable_reason and QUESTION_TYPE_RE.search(text) and ANSWER_SIGNAL_RE.search(text):
        questions = extract_questions_from_text(text)
    if unavailable_reason and not questions:
        raise ExamUnavailableError(unavailable_reason)
    if not questions:
        raise ExamUnavailableError("parsed 0 exam questions")
    markdown = questions_to_markdown(final_title, questions, source_url=source_url)
    return {
        "title": final_title,
        "source_url": source_url,
        "questions": questions,
        "markdown": markdown,
        "status": "done",
        "question_count": len(questions),
    }


def write_exam_outputs(html_path: Path, md_path: Path, questions_path: Path, title: str = "", source_url: str = "") -> dict[str, Any]:
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    result = extract_exam(html_text, title=title or html_path.stem, source_url=source_url)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    questions_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(result["markdown"], encoding="utf-8", newline="\n")
    payload = {
        "schema_version": 1,
        "title": result["title"],
        "source_url": result["source_url"],
        "html_path": str(html_path),
        "markdown_path": str(md_path),
        "questions": result["questions"],
        "question_count": len(result["questions"]),
        "status": "done",
    }
    questions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert captured Xuexitong exam HTML pages to Markdown and structured question JSON.")
    parser.add_argument("--html", required=True, help="Captured exam HTML file.")
    parser.add_argument("--output-dir", required=True, help="Directory for Markdown and question JSON outputs.")
    parser.add_argument("--title", default="")
    parser.add_argument("--source-url", default="")
    args = parser.parse_args()
    html_path = Path(args.html).resolve()
    output_dir = Path(args.output_dir).resolve()
    md_path = output_dir / f"{html_path.stem}.md"
    questions_path = output_dir / f"{html_path.stem}.questions.json"
    payload = write_exam_outputs(html_path, md_path, questions_path, title=args.title, source_url=args.source_url)
    print(f"exam_questions={payload['question_count']} markdown={md_path} json={questions_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
