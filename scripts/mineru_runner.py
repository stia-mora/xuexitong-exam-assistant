"""MinerU CLI wrapper and Markdown asset copier."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

IMAGE_MD_RE = re.compile(r"(!\[[^\]]*\]\()([^\)]+)(\))")
HTML_SRC_RE = re.compile(r"(\bsrc=[\"'])([^\"']+)([\"'])", re.IGNORECASE)
URL_SCHEMES = ("http://", "https://", "data:", "mailto:", "#")


def resolve_mineru_command(explicit: str = "") -> list[str]:
    if explicit:
        if explicit.startswith("conda:"):
            env_name = explicit.split(":", 1)[1].strip()
            if not env_name:
                raise ValueError("mineru conda environment name is empty")
            return ["conda", "run", "-n", env_name, "mineru"]
        return [str(Path(explicit).expanduser().resolve())]

    env_name = os.environ.get("MINERU_CONDA_ENV", "").strip()
    if env_name:
        return ["conda", "run", "-n", env_name, "mineru"]

    data_pipeline_mineru = Path(r"D:\ProgramData\anaconda3\envs\data_pipeline\Scripts\mineru.exe")
    if data_pipeline_mineru.exists() and shutil.which("conda"):
        return ["conda", "run", "-n", "data_pipeline", "mineru"]

    found = shutil.which("mineru")
    if found:
        return [found]
    env_dir = Path(sys.executable).resolve().parent
    for candidate_dir in (env_dir, env_dir / "Scripts"):
        for name in ("mineru.exe", "mineru"):
            candidate = candidate_dir / name
            if candidate.exists():
                return [str(candidate)]
    return ["mineru"]

def run_mineru(
    input_path: Path,
    output_root: Path,
    backend: str = "hybrid-auto-engine",
    method: str = "ocr",
    lang: str = "ch",
    mineru_bin: str = "",
    log_path: Path | None = None,
    formula: bool = False,
    table: bool = False,
) -> Path:
    input_path = input_path.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    command = [
        *resolve_mineru_command(mineru_bin),
        "-p",
        str(input_path),
        "-o",
        str(output_root),
        "-b",
        backend,
        "-m",
        method,
        "-l",
        lang,
        "-f",
        "true" if formula else "false",
        "-t",
        "true" if table else "false",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("MINERU_LOG_LEVEL", "INFO")
    result = subprocess.run(
        command,
        cwd=str(Path.cwd()),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write("COMMAND: " + " ".join(command) + "\n")
            f.write("--- stdout ---\n" + result.stdout.rstrip() + "\n")
            f.write("--- stderr ---\n" + result.stderr.rstrip() + "\n")
            f.write(f"--- exit {result.returncode} ---\n")
    if result.returncode != 0:
        raise RuntimeError(f"MinerU failed with exit code {result.returncode}: {result.stderr.strip()[:1000]}")
    return find_markdown(output_root, input_path.stem)


def find_markdown(output_root: Path, stem_hint: str = "") -> Path:
    md_files = sorted(output_root.rglob("*.md"), key=lambda p: (len(str(p)), str(p).casefold()))
    if not md_files:
        raise FileNotFoundError(f"MinerU produced no Markdown under {output_root}")
    if stem_hint:
        exact = [p for p in md_files if p.stem == stem_hint]
        if len(exact) == 1:
            return exact[0]
    plain = [p for p in md_files if not p.stem.endswith(("_content_list", "_middle", "_model"))]
    if len(plain) == 1:
        return plain[0]
    return md_files[0]


def is_external_link(link: str) -> bool:
    stripped = link.strip()
    return stripped.startswith(URL_SCHEMES) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", stripped) is not None


def split_link(link: str) -> tuple[str, str]:
    positions = [pos for pos in (link.find("#"), link.find("?")) if pos >= 0]
    if not positions:
        return link, ""
    pos = min(positions)
    return link[:pos], link[pos:]


def copy_linked_asset(link: str, source_md: Path, dest_md: Path, asset_dir: Path) -> str:
    if is_external_link(link):
        return link
    raw_path, suffix = split_link(link.strip())
    if not raw_path:
        return link
    decoded = urllib.parse.unquote(raw_path).replace("/", os.sep)
    source = (source_md.parent / decoded).resolve()
    if not source.exists() or not source.is_file():
        return link
    relative_source = Path(decoded)
    if relative_source.is_absolute() or ".." in relative_source.parts:
        relative_source = Path(source.name)
    destination = asset_dir / relative_source
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return os.path.relpath(destination, start=dest_md.parent).replace(os.sep, "/") + suffix


def rewrite_markdown_assets(markdown: str, source_md: Path, dest_md: Path, asset_dir: Path) -> str:
    def replace_md(match: re.Match[str]) -> str:
        return match.group(1) + copy_linked_asset(match.group(2), source_md, dest_md, asset_dir) + match.group(3)

    def replace_src(match: re.Match[str]) -> str:
        return match.group(1) + copy_linked_asset(match.group(2), source_md, dest_md, asset_dir) + match.group(3)

    return HTML_SRC_RE.sub(replace_src, IMAGE_MD_RE.sub(replace_md, markdown))


def copy_sibling_assets(source_md: Path, asset_dir: Path) -> None:
    for child in source_md.parent.iterdir():
        if child == source_md:
            continue
        if child.is_dir() and child.name.lower() in {"images", "image", "assets"}:
            target = asset_dir / child.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)


def copy_markdown_with_assets(source_md: Path, dest_md: Path, asset_dir: Path, header: str = "") -> Path:
    dest_md.parent.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    copy_sibling_assets(source_md, asset_dir)
    markdown = source_md.read_text(encoding="utf-8", errors="replace")
    markdown = rewrite_markdown_assets(markdown, source_md, dest_md, asset_dir)
    if header:
        markdown = header.rstrip() + "\n\n" + markdown.lstrip()
    dest_md.write_text(markdown, encoding="utf-8", newline="\n")
    return dest_md
