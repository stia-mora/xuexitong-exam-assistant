"""One-command authorized Xuexitong course collection and review build pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from selectors import slugify

SCRIPT_DIR = Path(__file__).resolve().parent


def resolve_workspace_path(value: str | Path, workspace_root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (workspace_root / path).resolve()


def run_step(
    label: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    result = subprocess.run(command, cwd=str(cwd), env=env)
    if result.returncode != 0:
        raise SystemExit(f"Step failed ({label}) with exit code {result.returncode}")


def course_dir_from_args(args: argparse.Namespace, workspace_root: Path, data_root: Path) -> Path:
    if args.course:
        return resolve_workspace_path(args.course, workspace_root)
    slug = args.course_slug or slugify(args.course_name or "xuexitong-course")
    return (data_root / slug).resolve()


def read_last_course_dir(data_root: Path) -> Path | None:
    marker = data_root.resolve() / ".last_course_dir"
    if not marker.exists():
        return None
    value = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (data_root.parent / path).resolve()
    else:
        path = path.resolve()
    return path if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a course, convert materials to Markdown, build course.db, "
            "prepare LLM-audit candidates, and render final pages only after reviewed knowledge_bank/question_bank validation."
        )
    )
    parser.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root for local data/profile paths. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--course-url",
        default="",
        help="Learning course URL. If omitted, Chrome opens the Xuexitong home page and waits for you to enter a course.",
    )
    parser.add_argument("--course-name", default="Xuexitong Course")
    parser.add_argument("--course-slug", default="")
    parser.add_argument("--course", default="", help="Explicit course directory, relative to --workspace-root when not absolute.")
    parser.add_argument("--data-root", default="data/courses", help="Course data root, relative to --workspace-root when not absolute.")
    parser.add_argument("--profile-dir", default="data/browser-profile", help="Chrome profile dir, relative to --workspace-root when not absolute.")
    parser.add_argument("--chrome-path", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--max-material-mb", type=float, default=200)
    parser.add_argument("--download-timeout-ms", type=int, default=60000)
    parser.add_argument("--login-wait-seconds", type=int, default=480)
    parser.add_argument(
        "--mineru-bin",
        default="",
        help="Optional MinerU executable path or conda:<env>. Empty uses MINERU_CONDA_ENV, PATH, or the current Python environment.",
    )
    parser.add_argument("--backend", default="pipeline")
    parser.add_argument("--method", default="ocr")
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--office-timeout", type=int, default=240)
    parser.add_argument("--material-limit", type=int, default=0)
    parser.add_argument("--assignment-limit", type=int, default=0)
    parser.add_argument("--exam-limit", type=int, default=0)
    parser.add_argument("--pause-each-exam", action="store_true", help="Pause on each visible exam page before capture.")
    parser.add_argument("--skip-exams", action="store_true", help="Do not capture exam/review pages during collection.")
    parser.add_argument("--require-exams", action="store_true", help="Fail collection if no exam questions are captured.")
    parser.add_argument("--teacher-mock", default="", help="Deprecated for final rendering; kept for CLI compatibility.")
    parser.add_argument("--questions-per-chapter", type=int, default=80, help="Deprecated; final rendering uses audited question_bank.json.")
    parser.add_argument("--ai-fill-min-per-chapter", type=int, default=25, help="Deprecated; LLM supplementing happens during question audit.")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--skip-render", action="store_true", help="Deprecated. Legacy fallback rendering is disabled unless --render-legacy-fallback is set.")
    parser.add_argument("--render-legacy-fallback", action="store_true", help="Render output/practice.generated.html for debugging only. It is not a final review page.")
    parser.add_argument("--skip-exam-pages", action="store_true", help="Skip audited question_bank/mock_exam/multi-page HTML rendering.")
    parser.add_argument("--min-approved-questions", type=int, default=150, help="Minimum LLM-audited approved questions required before final HTML rendering.")
    parser.add_argument("--notebooklm", action="store_true", help="Opt in to uploading converted Markdown bundles to NotebookLM and importing generated study artifacts.")
    parser.add_argument("--notebooklm-profile", default="", help="NotebookLM profile name. Empty uses the active/default profile.")
    parser.add_argument("--notebooklm-strict", action="store_true", help="Fail the pipeline if the optional NotebookLM sync fails.")
    parser.add_argument("--notebooklm-dry-run", action="store_true", help="Build NotebookLM bundles and manifest without contacting Google.")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else Path.cwd().resolve()
    data_root = resolve_workspace_path(args.data_root, workspace_root)
    profile_dir = resolve_workspace_path(args.profile_dir, workspace_root)
    course_dir = course_dir_from_args(args, workspace_root, data_root)

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env["QIMOKAISI_WORKSPACE_ROOT"] = str(workspace_root)
    env.setdefault("NOTEBOOKLM_HOME", str((workspace_root / "data" / "notebooklm").resolve()))
    env.setdefault("NOTEBOOKLM_HL", "zh-CN")
    if args.max_material_mb and args.max_material_mb > 0:
        env["QIMOKAISI_MAX_MATERIAL_MB"] = str(args.max_material_mb)

    if not args.skip_collect:
        collect_cmd = [
            sys.executable,
            str(SCRIPT_DIR / "collect_course.py"),
            "--data-root",
            str(data_root),
            "--course-name",
            args.course_name,
            "--profile-dir",
            str(profile_dir),
            "--chrome-path",
            args.chrome_path,
            "--download-timeout-ms",
            str(args.download_timeout_ms),
            "--login-wait-seconds",
            str(args.login_wait_seconds),
            "--max-material-mb",
            str(args.max_material_mb),
            "--yes",
        ]
        if args.course:
            collect_cmd.extend(["--course", str(course_dir)])
        elif args.course_slug:
            collect_cmd.extend(["--course-slug", args.course_slug])
        if args.course_url:
            collect_cmd.extend(["--course-url", args.course_url])
        if args.material_limit:
            collect_cmd.extend(["--material-limit", str(args.material_limit)])
        if args.assignment_limit:
            collect_cmd.extend(["--assignment-limit", str(args.assignment_limit)])
        if args.exam_limit:
            collect_cmd.extend(["--exam-limit", str(args.exam_limit)])
        if args.pause_each_exam:
            collect_cmd.append("--pause-each-exam")
        if args.skip_exams:
            collect_cmd.append("--skip-exams")
        if args.require_exams:
            collect_cmd.append("--require-exams")
        run_step("collect", collect_cmd, cwd=workspace_root, env=env)
        detected_course_dir = read_last_course_dir(data_root)
        if detected_course_dir is not None:
            course_dir = detected_course_dir
            print(f"Using collected course directory: {course_dir}", flush=True)

    if not args.skip_convert:
        convert_cmd = [
            sys.executable,
            str(SCRIPT_DIR / "convert_materials.py"),
            "--course",
            str(course_dir),
            "--backend",
            args.backend,
            "--method",
            args.method,
            "--lang",
            args.lang,
            "--office-timeout",
            str(args.office_timeout),
        ]
        if args.mineru_bin:
            convert_cmd.extend(["--mineru-bin", args.mineru_bin])
        run_step("convert materials", convert_cmd, cwd=workspace_root, env=env)

    run_step("build course.db", [sys.executable, str(SCRIPT_DIR / "build_course_db.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)
    run_step("prepare review context", [sys.executable, str(SCRIPT_DIR / "prepare_review_context.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)

    if args.notebooklm:
        notebooklm_cmd = [sys.executable, str(SCRIPT_DIR / "notebooklm_sync.py"), "--course", str(course_dir)]
        if args.notebooklm_profile:
            notebooklm_cmd.extend(["--profile", args.notebooklm_profile])
        if args.notebooklm_strict:
            notebooklm_cmd.append("--strict")
        if args.notebooklm_dry_run:
            notebooklm_cmd.append("--dry-run")
        run_step("optional NotebookLM enrichment", notebooklm_cmd, cwd=workspace_root, env=env)
        run_step("rebuild course.db after NotebookLM", [sys.executable, str(SCRIPT_DIR / "build_course_db.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)
        run_step("prepare review context after NotebookLM", [sys.executable, str(SCRIPT_DIR / "prepare_review_context.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)

    generated_html = course_dir / "output" / "practice.generated.html"
    run_step("prepare question candidates for LLM audit", [sys.executable, str(SCRIPT_DIR / "prepare_question_candidates.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)

    if args.render_legacy_fallback and not args.skip_render:
        run_step(
            "render legacy fallback practice html",
            [
                sys.executable,
                str(SCRIPT_DIR / "render_practice_html.py"),
                "--course",
                str(course_dir),
                "--output",
                str(generated_html),
            ],
            cwd=workspace_root,
            env=env,
        )
    if not args.skip_exam_pages:
        knowledge_path = course_dir / "generated" / "knowledge_bank.json"
        audit_path = course_dir / "generated" / "question_audit.json"
        bank_path = course_dir / "generated" / "question_bank.json"
        if knowledge_path.exists() and audit_path.exists() and bank_path.exists():
            exam_cmd = [
                sys.executable,
                str(SCRIPT_DIR / "generate_exam_pages.py"),
                "--course",
                str(course_dir),
                "--min-approved-questions",
                str(args.min_approved_questions),
            ]
            if args.teacher_mock:
                exam_cmd.extend(["--teacher-mock", args.teacher_mock])
            run_step("render audited multi-page exam review", exam_cmd, cwd=workspace_root, env=env)
        else:
            missing = [
                str(path.relative_to(course_dir))
                for path in (knowledge_path, audit_path, bank_path)
                if not path.exists()
            ]
            print(
                "\nWAITING FOR LLM REVIEW: generated/question_candidates.json and generated/teacher_knowledge_seeds.json are ready. "
                f"Missing: {', '.join(missing)}. "
                "First extract generated/knowledge_bank.json from course materials/optional input/teacher_focus.md, "
                "then review every candidate, write generated/question_audit.json and generated/question_bank.json, "
                "then rerun generate_exam_pages.py.",
                flush=True,
            )
    run_step("write crawl report", [sys.executable, str(SCRIPT_DIR / "report_course_collection.py"), "--course", str(course_dir)], cwd=workspace_root, env=env)

    print(f"\nDONE course={course_dir}", flush=True)
    print(f"Workspace root: {workspace_root}", flush=True)
    print(f"Question candidates: {course_dir / 'generated' / 'question_candidates.json'}", flush=True)
    print(f"Teacher knowledge seeds: {course_dir / 'generated' / 'teacher_knowledge_seeds.json'}", flush=True)
    print(f"Reviewed knowledge bank: {course_dir / 'generated' / 'knowledge_bank.json'}", flush=True)
    print(f"Required audit file: {course_dir / 'generated' / 'question_audit.json'}", flush=True)
    print(f"Audited question bank: {course_dir / 'generated' / 'question_bank.json'}", flush=True)
    print(f"Final entry HTML after audit: {course_dir / 'output' / 'practice.html'}", flush=True)
    print(f"Question page after audit: {course_dir / 'output' / 'questions.html'}", flush=True)
    print(f"Mock exam page after audit: {course_dir / 'output' / 'mock_exam.html'}", flush=True)
    if args.render_legacy_fallback:
        print(f"Legacy fallback HTML: {generated_html}", flush=True)
    print(f"Review seed: {course_dir / 'output' / 'final_review_seed.md'}", flush=True)
    print(f"Codex context: {course_dir / 'generated' / 'codex_context.md'}", flush=True)
    print(f"Mock exam JSON: {course_dir / 'generated' / 'mock_exam.json'}", flush=True)
    if args.notebooklm:
        print(f"NotebookLM manifest: {course_dir / 'generated' / 'notebooklm' / 'manifest.json'}", flush=True)
    print(f"Crawl report: {course_dir / 'output' / 'crawl_report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
