# setup_env.ps1
# One-shot environment setup for td-mcp on the EXACT Python TouchDesigner uses.
# TD reported: 3.11.10 (heads/3.11-Derivative-dirty, Oct 2024) -> stock base 3.11.10.
# Run from the td-mcp folder:  powershell -ExecutionPolicy Bypass -File setup_env.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 1) Ensure the LATEST uv (install if missing, else self-update)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output "== installing uv =="
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:LOCALAPPDATA\uv;$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Write-Output "== updating uv to latest =="
    uv self update
}

# 2) Install the EXACT Python version TD ships (3.11.10)
Write-Output "== installing Python 3.11.10 =="
uv python install 3.11.10

# 3) Create / reset the venv ON that interpreter, and pin it
Write-Output "== creating venv (python 3.11.10) =="
uv venv --python 3.11.10 --clear
uv python pin 3.11.10

# 4) Install td-mcp + MCP server deps.
#    Add ,dense and/or ,scrape to also get all-MiniLM (~80MB) / doc scraper.
Write-Output "== installing td-mcp [mcp] =="
uv pip install -e ".[mcp]"

# 5) Verify the active interpreter is exactly 3.11.10
Write-Output "== verify =="
uv run python --version
uv run python -c "import sys; print(sys.version)"
Write-Output "td-mcp env ready on Python 3.11.10 (matches TouchDesigner)"
