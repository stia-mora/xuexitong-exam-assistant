from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract_exams  # noqa: E402
import prepare_question_candidates  # noqa: E402
import report_course_collection  # noqa: E402
from collect_course import exam_detail_url_from_onclick, existing_exam_question_count  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


ACCESSIBLE_EXAM_HTML = """
<!doctype html><html><head><title>期末试卷B-24级软工</title></head><body>
<h1 class="paper-title">期末试卷B-24级软工</h1>
<div class="questionLi" typename="单选题" id="question1" data="qid-1">
  <div>1. (单选题, 2.0分)</div>
  <div class="mark_name">交互图中，____最符合“alt片段”的含义。</div>
  <div class="stem_answer">
    <div class="answerBg"><span class="num_option">A</span><span>表示并行部署节点</span></div>
    <div class="answerBg"><span class="num_option">B</span><span>表示文件扩展名</span></div>
    <div class="answerBg"><span class="num_option">C</span><span>表示多分支选择的组合片段</span></div>
    <div class="answerBg"><span class="num_option">D</span><span>表示类的属性</span></div>
  </div>
  <div class="result">我的答案:C 正确答案:C 答案解析:alt片段通常指表示多分支选择的组合片段。</div>
</div>
<div class="questionLi" typename="判断题" id="question2" data="qid-2">
  <div>2. (判断题, 2.0分)</div>
  <div class="mark_name">用例图主要用于描述系统内部算法流程。</div>
  <div>我的答案:错 正确答案:错 答案解析:用例图强调参与者与系统服务边界，不描述内部算法。</div>
</div>
<div class="questionLi" typename="简答题" id="question3" data="qid-3">
  <div>3. (简答题, 4.0分)</div>
  <div class="mark_name">简述活动图中泳道的作用。</div>
  <div>正确答案:泳道用于划分责任主体。 答案解析:答题时应写出责任边界、活动归属和协作关系。</div>
</div>
<div class="questionLi" typename="设计题" id="question4" data="qid-4">
  <div>4. (设计题, 10.0分)</div>
  <div class="mark_name">为图书借阅场景设计核心用例。</div>
  <div>正确答案:借书、还书、续借、查询库存。 答案解析:要覆盖参与者、系统边界和主要业务目标。</div>
</div>
</body></html>
"""


class ExamExtractionTest(unittest.TestCase):
    def test_extracts_accessible_exam_questions(self) -> None:
        result = extract_exams.extract_exam(ACCESSIBLE_EXAM_HTML, title="期末试卷", source_url="https://example.test/exam")

        self.assertEqual(result["title"], "期末试卷B-24级软工")
        self.assertEqual(result["question_count"], 4)
        first = result["questions"][0]
        self.assertEqual(first["type"], "单选题")
        self.assertEqual(first["points"], "2.0")
        self.assertIn("alt片段", first["question"])
        self.assertEqual(first["answer"], "C")
        self.assertIn("多分支选择", first["explanation"])
        self.assertEqual([option["label"] for option in first["options"]], ["A", "B", "C", "D"])
        self.assertEqual(result["questions"][1]["answer"], "错")
        self.assertEqual(result["questions"][3]["type"], "设计题")

    def test_unavailable_exam_is_nonfatal_signal(self) -> None:
        html = "<html><body><div>老师未开放，您没有查看权限</div></body></html>"
        with self.assertRaises(extract_exams.ExamUnavailableError) as ctx:
            extract_exams.extract_exam(html, title="未开放试卷", source_url="https://example.test/closed")
        self.assertIn("未开放", str(ctx.exception))

    def test_exam_candidates_are_loaded_before_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            course = Path(tmp) / "course"
            exam_json = course / "exams_md" / "exam.questions.json"
            assignment_json = course / "assignments_md" / "assignment.questions.json"
            write_json(
                exam_json,
                {
                    "title": "期末试卷",
                    "source_url": "https://example.test/exam",
                    "questions": [
                        {
                            "number": 1,
                            "type": "单选题",
                            "question": "考试原题题干是什么？",
                            "options": [{"label": "A", "text": "正确项"}],
                            "answer": "A",
                            "explanation": "考试页给出的解析。",
                        }
                    ],
                },
            )
            write_json(
                assignment_json,
                {
                    "title": "平时作业",
                    "questions": [
                        {
                            "type": "简答题",
                            "question": "作业题题干是什么？",
                            "answer": "参考答案",
                            "explanation": "作业解析。",
                        }
                    ],
                },
            )

            summary = prepare_question_candidates.prepare(course)
            payload = json.loads((course / "generated" / "question_candidates.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["exam_candidate_count"], 1)
        self.assertEqual(summary["assignment_candidate_count"], 1)
        self.assertEqual(payload["items"][0]["source_kind"], "xxt_exam")
        self.assertEqual(payload["items"][1]["source_kind"], "xxt_assignment")

    def test_report_records_exam_unavailable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            course = Path(tmp) / "course"
            write_json(
                course / "manifests" / "course_manifest.json",
                {
                    "items": [
                        {
                            "kind": "exam",
                            "title": "未开放期末试卷",
                            "url": "https://example.test/closed",
                            "status": "unavailable",
                            "error": "老师未开放",
                        }
                    ]
                },
            )
            report = report_course_collection.build_report(course, max_items=5, max_questions=2)

        self.assertIn("考试采集状态", report)
        self.assertIn("unavailable", report)
        self.assertIn("老师未开放", report)
        self.assertIn("不是生成复习包的硬依赖", report)

    def test_builds_exam_detail_url_from_view_answer_onclick(self) -> None:
        item = {
            "onclick": "viewExamAnswer('10488893','170473423')",
            "source_frame_url": (
                "https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list?"
                "courseid=261312387&clazzid=141426521&cpi=406057681&ut=s"
            ),
        }
        url = exam_detail_url_from_onclick(item)

        self.assertIn("reVersionPaperMarkContentNew", url)
        self.assertIn("courseId=261312387", url)
        self.assertIn("classId=141426521", url)
        self.assertIn("id=170473423", url)
        self.assertIn("qbankbackurl=", url)

    def test_require_exams_can_use_zero_count_as_failure_condition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            course = Path(tmp) / "course"
            self.assertEqual(existing_exam_question_count(course), 0)
            write_json(course / "exams_md" / "exam.questions.json", {"questions": [{"question": "Q"}]})
            self.assertEqual(existing_exam_question_count(course), 1)


if __name__ == "__main__":
    unittest.main()
