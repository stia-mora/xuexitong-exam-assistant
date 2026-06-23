"""Office document conversion helpers for Windows/PowerPoint."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

POWERPOINT_PDF_FORMAT = 32
DEFAULT_POWERPOINT_PATHS = (
    Path(r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"),
    Path(r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE"),
)


def find_powerpoint() -> Path | None:
    for path in DEFAULT_POWERPOINT_PATHS:
        if path.exists():
            return path
    found = shutil.which("POWERPNT.EXE") or shutil.which("powerpnt")
    return Path(found) if found else None


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(path).exists():
            return path
    return None


def convert_ppt_with_powerpoint(input_path: Path, output_pdf: Path, timeout: int = 180) -> Path:
    input_path = input_path.resolve()
    output_pdf = output_pdf.resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    ps_command = r"""
param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$inputPathFull = [System.IO.Path]::GetFullPath($InputPath)
$outputPathFull = [System.IO.Path]::GetFullPath($OutputPath)
$presentation = $null
$powerpoint = $null
try {
    $powerpoint = New-Object -ComObject PowerPoint.Application
    $powerpoint.DisplayAlerts = 1
    $presentation = $powerpoint.Presentations.Open($inputPathFull, $true, $false, $false)
    $presentation.SaveAs($outputPathFull, 32)
}
finally {
    if ($presentation -ne $null) { $presentation.Close() | Out-Null }
    if ($powerpoint -ne $null) { $powerpoint.Quit() | Out-Null }
}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as script_file:
        script_file.write(ps_command)
        script_path = script_file.name
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                str(input_path),
                str(output_pdf),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    finally:
        try:
            Path(script_path).unlink(missing_ok=True)
        except Exception:
            pass
    if result.returncode != 0:
        raise RuntimeError(f"PowerPoint conversion failed: {result.stderr.strip() or result.stdout.strip()}")
    if not output_pdf.exists() or output_pdf.stat().st_size == 0:
        raise RuntimeError("PowerPoint conversion produced no PDF")
    return output_pdf


def convert_with_soffice(input_path: Path, output_dir: Path, timeout: int = 180) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice/soffice not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(input_path)],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr.strip() or result.stdout.strip()}")
    pdf_path = output_dir / (input_path.stem + ".pdf")
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("LibreOffice conversion produced no PDF")
    return pdf_path


def convert_presentation_to_pdf(input_path: Path, output_pdf: Path, timeout: int = 180) -> Path:
    errors: list[str] = []
    if find_powerpoint():
        try:
            return convert_ppt_with_powerpoint(input_path, output_pdf, timeout=timeout)
        except Exception as exc:
            errors.append(str(exc))
    try:
        converted = convert_with_soffice(input_path, output_pdf.parent, timeout=timeout)
        if converted.resolve() != output_pdf.resolve():
            if output_pdf.exists():
                output_pdf.unlink()
            converted.replace(output_pdf)
        return output_pdf
    except Exception as exc:
        errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "No presentation converter available")
