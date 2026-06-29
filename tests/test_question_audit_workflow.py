from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_exam_pages.py"
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_exam_pages as renderer  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_course(
    question_count: int = 4,
    include_candidates: bool = True,
    audit_all_candidates: bool = True,
    include_knowledge_bank: bool = True,
    include_question_knowledge_ids: bool = True,
    stale_knowledge_hash: bool = False,
) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    course = Path(tmp.name) / "course"
    (course / "generated").mkdir(parents=True)
    (course / "output").mkdir()
    write_json(course / "course_meta.json", {"course_title": "测试课程"})
    chapter = {"key": "chapter-0001", "title": "第 1 章 测试知识", "order": 1}
    knowledge_items = [
        {
            "knowledge_id": "K001",
            "title": "课件核心概念",
            "chapter": chapter,
            "learning_goal": "掌握测试课程的核心定义和判定条件。",
            "key_points": ["核心定义需要能用自己的话说明", "判断题要先找限定条件"],
            "formula_examples": ["例：给出定义后判断一个对象是否满足条件"],
            "pitfalls": ["只背结论、不写依据会丢分"],
            "exam_tips": ["答题时写出定义、条件、结论三部分"],
            "source_refs": ["materials_md/test.md"],
            "source_kind": "course_material",
            "priority": 10,
            "reviewed_by_llm": True,
            "quality_status": "approved",
        },
        {
            "knowledge_id": "K002",
            "title": "老师强调重点",
            "chapter": chapter,
            "learning_goal": "优先掌握老师手动标出的高频考点。",
            "key_points": ["老师强调项优先于普通课件种子"],
            "formula_examples": ["例：同一章中老师重点应置顶显示"],
            "pitfalls": ["忽略老师重点会影响章节复习顺序"],
            "exam_tips": ["先看老师重点，再看普通课件提炼知识点"],
            "source_refs": ["input/teacher_focus.md"],
            "source_kind": "teacher_focus",
            "priority": 0,
            "reviewed_by_llm": True,
            "quality_status": "approved",
        },
    ]
    if include_knowledge_bank:
        write_json(course / "generated" / "knowledge_bank.json", {"items": knowledge_items})
        _, _, knowledge_hash = renderer.load_validated_knowledge_bank(course)
    else:
        knowledge_hash = "missing"

    types = ["单选题", "判断题", "填空题", "简答题"]
    candidates = []
    audits = []
    bank = []
    for index in range(1, question_count + 1):
        q_type = types[(index - 1) % len(types)]
        candidate_id = f"candidate-{index:03d}"
        audit_id = f"audit-{index:03d}"
        stem = f"第 {index} 题测试题干是什么？"
        answer = "A" if q_type == "单选题" else "对" if q_type == "判断题" else "关键术语" if q_type == "填空题" else "按定义、条件、结论作答。"
        analysis = f"这题核心知识点：测试课程第 {index} 个考点；考试时要写出依据和易混点。"
        options = [{"label": "A", "text": "正确项"}, {"label": "B", "text": "干扰项"}] if q_type == "单选题" else []
        candidates.append({"id": candidate_id, "original_stem": stem})
        if audit_all_candidates or index < question_count:
            audits.append(
                {
                    "audit_id": audit_id,
                    "source_id": candidate_id,
                    "decision": "reuse_full",
                    "original_stem": stem,
                    "final_stem": stem,
                    "type": q_type,
                    "options": options,
                    "final_answer": answer,
                    "final_analysis": analysis,
                    "approved_for_bank": True,
                    "reviewed_by_llm": True,
                    "quality_status": "approved",
                    "confidence": 0.95,
                    "knowledge_ids": ["K001"] if include_question_knowledge_ids else [],
                }
            )
            bank_item = {
                "id": f"Q{index:03d}",
                "audit_id": audit_id,
                "source_kind": "xxt_reused",
                "type": q_type,
                "stem": stem,
                "options": options,
                "answer": answer,
                "analysis": analysis,
                "reviewed_by_llm": True,
                "quality_status": "approved",
            }
            if include_question_knowledge_ids:
                bank_item["knowledge_ids"] = ["K001"]
            bank.append(bank_item)
    if include_candidates:
        write_json(course / "generated" / "question_candidates.json", {"items": candidates})
    write_json(course / "generated" / "question_audit.json", {"items": audits})
    write_json(
        course / "generated" / "question_bank.json",
        {"items": bank, "summary": {"knowledge_bank_hash": "stale" if stale_knowledge_hash else knowledge_hash}},
    )
    tmp.course = course  # type: ignore[attr-defined]
    return tmp


class QuestionAuditWorkflowTest(unittest.TestCase):
    def run_render(self, course: Path, minimum: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--course", str(course), "--min-approved-questions", str(minimum)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
        )

    def test_renders_audited_bank_with_template_pages(self) -> None:
        tmp = make_course(question_count=4)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 4)  # type: ignore[attr-defined]
        self.assertEqual(result.returncode, 0, result.stderr)
        practice = (tmp.course / "output" / "practice.html").read_text(encoding="utf-8")  # type: ignore[attr-defined]
        questions = (tmp.course / "output" / "questions.html").read_text(encoding="utf-8")  # type: ignore[attr-defined]
        self.assertIn("entry-cards", practice)
        self.assertIn("知识点学习", practice)
        self.assertLess(practice.index("知识点学习"), practice.index("精选练习"))
        self.assertLess(practice.index("老师重点"), practice.index("课件提炼"))
        self.assertIn('class="ch"', questions)
        self.assertNotIn("practice.generated.html", practice)
        self.assertNotIn("资料未提供完整解析", questions)

    def test_blocks_missing_knowledge_bank(self) -> None:
        tmp = make_course(question_count=4, include_knowledge_bank=False)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 4)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing generated/knowledge_bank.json", result.stderr + result.stdout)

    def test_blocks_question_without_knowledge_ids(self) -> None:
        tmp = make_course(question_count=4, include_question_knowledge_ids=False)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 4)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing knowledge_ids", result.stderr + result.stdout)

    def test_blocks_stale_knowledge_hash(self) -> None:
        tmp = make_course(question_count=4, stale_knowledge_hash=True)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 4)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("knowledge_bank_hash", result.stderr + result.stdout)

    def test_blocks_below_minimum(self) -> None:
        tmp = make_course(question_count=2)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 3)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("below required minimum", result.stderr + result.stdout)

    def test_blocks_unaudited_candidate(self) -> None:
        tmp = make_course(question_count=4, audit_all_candidates=False)
        self.addCleanup(tmp.cleanup)
        result = self.run_render(tmp.course, 3)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidate not audited", result.stderr + result.stdout)

    def test_blocks_weak_analysis(self) -> None:
        tmp = make_course(question_count=4)
        self.addCleanup(tmp.cleanup)
        bank_path = tmp.course / "generated" / "question_bank.json"  # type: ignore[attr-defined]
        payload = json.loads(bank_path.read_text(encoding="utf-8"))
        payload["items"][0]["analysis"] = "资料未提供完整解析"
        write_json(bank_path, payload)
        result = self.run_render(tmp.course, 4)  # type: ignore[attr-defined]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("weak analysis", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
