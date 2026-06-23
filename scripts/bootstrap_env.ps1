param(
    [string]$EnvName = "qimokaisi-exam",
    [string]$PythonVersion = "3.12",
    [switch]$ForceInstall,
    [switch]$ConfigureMineruLocal,
    [string]$MineruSnapshot = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required command not found: $Name"
    }
    return $cmd
}

function Find-MineruSnapshotPath {
    $snapshotRoot = Join-Path $HOME ".cache\huggingface\hub\models--opendatalab--PDF-Extract-Kit-1.0\snapshots"
    if (-not (Test-Path -LiteralPath $snapshotRoot)) {
        return ""
    }
    $candidate = Get-ChildItem -LiteralPath $snapshotRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($candidate) {
        return $candidate.FullName
    }
    return ""
}

Require-Command "conda" | Out-Null

Write-Step "Checking conda environment: $EnvName"
$envJson = conda env list --json | ConvertFrom-Json
$existing = @($envJson.envs | Where-Object { Split-Path $_ -Leaf | Where-Object { $_ -eq $EnvName } })
if (-not $existing) {
    Write-Step "Creating conda environment $EnvName with Python $PythonVersion"
    conda create -y -n $EnvName "python=$PythonVersion" pip
} else {
    Write-Step "Environment already exists: $($existing[0])"
}

$packages = @(
    "mineru[pipeline]==3.1.0",
    "playwright",
    "pypdf",
    "python-pptx",
    "pydantic",
    "typer",
    "rich",
    "jinja2",
    "beautifulsoup4",
    "markdownify"
    "notebooklm-py[browser]==0.5.0"
)

if ($ForceInstall -or -not $existing) {
    Write-Step "Upgrading pip"
    conda run -n $EnvName python -m pip install --upgrade pip
    Write-Step "Installing project Python dependencies"
    conda run -n $EnvName python -m pip install @packages
} else {
    Write-Step "Installing or verifying project Python dependencies"
    conda run -n $EnvName python -m pip install @packages
}

if ($ConfigureMineruLocal) {
    if (-not $MineruSnapshot) {
        $MineruSnapshot = Find-MineruSnapshotPath
    }
    if (-not $MineruSnapshot -or -not (Test-Path -LiteralPath $MineruSnapshot)) {
        throw @"
MinerU local model snapshot was not found.
Download the model first:
  conda run -n $EnvName mineru-models-download -s huggingface -m pipeline
Then rerun this script with -ConfigureMineruLocal, or pass -MineruSnapshot "<snapshot path>".
"@
    }

    Write-Step "Writing MinerU local model config to $HOME\mineru.json"
    $config = [ordered]@{
        "models-dir" = [ordered]@{
            "pipeline" = (Resolve-Path -LiteralPath $MineruSnapshot).Path
        }
    } | ConvertTo-Json -Depth 5
    $mineruConfigPath = Join-Path $HOME "mineru.json"
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($mineruConfigPath, $config, $utf8NoBom)
    [Environment]::SetEnvironmentVariable("MINERU_MODEL_SOURCE", "local", "User")
}

Write-Step "Verifying imports"
conda run -n $EnvName python -c "import playwright, pypdf, pptx, bs4, markdownify, jinja2; print('python imports ok')"

Write-Step "Verifying MinerU CLI"
conda run -n $EnvName mineru --help | Select-Object -First 8

Write-Host ""
Write-Host "Environment ready. Playwright will use the existing Chrome executable; no browser download was performed." -ForegroundColor Green
