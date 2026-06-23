"""Convert downloaded course materials to Markdown with MinerU."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mineru_runner import copy_markdown_with_assets, run_mineru
from office_convert import convert_presentation_to_pdf
from selectors import safe_filename

SUPPORTED_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
PRESENTATION_EXTENSIONS = {".ppt", ".pptx"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_id(path: Path, course_dir: Path) -> str:
    rel = str(path.resolve().relative_to(course_dir.resolve())).replace("\\", "/")
    return hashlib.sha1((rel + "\n" + sha256_file(path)).encode("utf-8", errors="replace")).hexdigest()[:16]


def iter_materials(course_dir: Path) -> list[Path]:
    raw_dir = course_dir / "raw" / "materials"
    if not raw_dir.exists():
        return []
    return sorted(
        [path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS],
        key=lambda p: str(p).casefold(),
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def markdown_header(source: Path, source_hash: str, note: str) -> str:
    return "\n".join(
        [
            f"# {source.stem}",
            "",
            f"Source file: `{source}`",
            f"Source sha256: `{source_hash}`",
            f"Converted at: `{utc_now()}`",
            f"Conversion note: {note}",
            "",
            "---",
        ]
    )


def process_material(source: Path, course_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    file_id = source_id(source, course_dir)
    source_hash = sha256_file(source)
    safe_stem = safe_filename(source.stem, "material")
    dest_md = course_dir / "materials_md" / f"{safe_stem}-{file_id[:8]}.md"
    asset_dir = course_dir / "assets" / "materials" / f"{safe_stem}-{file_id[:8]}"
    log_path = course_dir / "manifests" / "mineru.log"

    if dest_md.exists() and not args.force:
        return {
            "id": file_id,
            "source": str(source),
            "status": "skipped",
            "markdown": str(dest_md),
            "sha256": source_hash,
            "updated_at": utc_now(),
        }

    mineru_input = source
    note = "direct MinerU conversion"
    converted_pdf = ""

    if source.suffix.lower() in PRESENTATION_EXTENSIONS:
        pdf_path = course_dir / "work" / "converted_pdf" / file_id / f"{safe_stem}.pdf"
        try:
            mineru_input = convert_presentation_to_pdf(source, pdf_path, timeout=args.office_timeout)
            converted_pdf = str(mineru_input)
            note = "PowerPoint/Office converted presentation to PDF, then MinerU OCR"
        except Exception as exc:
            if source.suffix.lower() == ".pptx":
                mineru_input = source
                note = f"presentation-to-PDF failed; fallback to direct MinerU on PPTX: {exc}"
            else:
                raise

    mineru_output = course_dir / "work" / "mineru" / file_id
    source_md = run_mineru(
        mineru_input,
        mineru_output,
        backend=args.backend,
        method=args.method,
        lang=args.lang,
        mineru_bin=args.mineru_bin,
        log_path=log_path,
        formula=args.formula,
        table=args.table,
    )
    copy_markdown_with_assets(source_md, dest_md, asset_dir, header=markdown_header(source, source_hash, note))
    return {
        "id": file_id,
        "source": str(source),
        "status": "done",
        "markdown": str(dest_md),
        "assets": str(asset_dir),
        "sha256": source_hash,
        "mineru_input": str(mineru_input),
        "converted_pdf": converted_pdf,
        "note": note,
        "updated_at": utc_now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert course materials under raw/materials to Markdown with MinerU.")
    parser.add_argument("--course", required=True, help="Course directory, for example data/courses/<course_slug>.")
    parser.add_argument("--backend", default="hybrid-auto-engine", help="MinerU backend.")
    parser.add_argument("--method", default="ocr", choices=["auto", "txt", "ocr"], help="MinerU parse method.")
    parser.add_argument("--lang", default="ch", help="MinerU OCR language.")
    parser.add_argument("--mineru-bin", default="", help="Optional MinerU executable path or conda:<env>, for example conda:data_pipeline.")
    parser.add_argument("--office-timeout", type=int, default=180, help="Seconds before Office conversion is considered stuck.")
    parser.add_argument("--formula", action="store_true", help="Enable MinerU formula parsing. Disabled by default to avoid optional model requirements.")
    parser.add_argument("--table", action="store_true", help="Enable MinerU table parsing. Disabled by default to avoid optional model requirements.")
    parser.add_argument("--force", action="store_true", help="Regenerate Markdown even when output exists.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max files for smoke tests.")
    args = parser.parse_args()

    course_dir = Path(args.course).resolve()
    if not course_dir.exists():
        raise SystemExit(f"Course directory does not exist: {course_dir}")

    materials = iter_materials(course_dir)
    if args.limit > 0:
        materials = materials[: args.limit]

    records: list[dict[str, Any]] = []
    failed_path = course_dir / "manifests" / "failed_items.jsonl"
    for source in materials:
        try:
            record = process_material(source, course_dir, args)
            records.append(record)
            print(f"{record['status'].upper()} {source} -> {record.get('markdown', '')}", flush=True)
        except Exception as exc:
            failure = {
                "source": str(source),
                "status": "failed",
                "error": str(exc),
                "updated_at": utc_now(),
            }
            records.append(failure)
            append_jsonl(failed_path, failure)
            print(f"FAILED {source}: {exc}", flush=True)

    manifest_path = course_dir / "manifests" / "conversion_manifest.json"
    write_json(manifest_path, {"updated_at": utc_now(), "items": records})
    failed = [item for item in records if item.get("status") == "failed"]
    print(f"materials={len(materials)} failed={len(failed)} manifest={manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
