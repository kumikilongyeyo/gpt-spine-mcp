param(
    [switch]$Codex
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
}

$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
uv tool install --force "${RepoDir}[agents]"

if ($Codex) {
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
        throw "Installed GPT Spine, but codex was not found on PATH."
    }
    codex mcp remove gpt-spine 2>$null
    $global:LASTEXITCODE = 0
    codex mcp add gpt-spine -- gpt-spine-mcp
}

gpt-spine doctor
