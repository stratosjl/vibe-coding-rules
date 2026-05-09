# vc-roe (vibe-coding-rules) one-line installer for Windows PowerShell.
#
# Usage:
#   iwr -useb https://raw.githubusercontent.com/stratosjl/vibe-coding-rules/main/install.ps1 | iex
#
# What it does:
#   1. Verifies the `claude` CLI is on PATH.
#   2. Adds the vibe-coding-rules marketplace via HTTPS clone (no SSH key needed).
#   3. Installs the vc-roe plugin at user scope.
#   4. Prints next-steps (Claude Code restart required to pick up the new hooks).
#
# Idempotent: re-running is safe. The marketplace-add and plugin-install commands
# are no-ops when the marketplace / plugin is already present.

$ErrorActionPreference = 'Stop'

$RepoUrl = 'https://github.com/stratosjl/vibe-coding-rules.git'
$MarketplaceName = 'vibe-coding-rules'
$PluginName = 'vc-roe'

function Write-Info($msg)  { Write-Host "[vc-roe install] $msg" -ForegroundColor Cyan }
function Write-Warn($msg)  { Write-Host "[vc-roe install] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "[vc-roe install] $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Fail "the 'claude' CLI is not on PATH. Install Claude Code first: https://docs.claude.com/en/docs/claude-code/setup"
}

try {
    $version = & claude --version 2>$null
    Write-Info "claude CLI detected: $version"
}
catch {
    Write-Info "claude CLI detected (version probe failed but command is on PATH)"
}

$marketplaceLog = Join-Path $env:TEMP 'vc-roe-install-marketplace.log'
$pluginLog = Join-Path $env:TEMP 'vc-roe-install-plugin.log'

Write-Info "adding marketplace $MarketplaceName from $RepoUrl"
$marketplaceOutput = & claude plugin marketplace add $RepoUrl 2>&1
$marketplaceOutput | Out-File -FilePath $marketplaceLog -Encoding utf8
$marketplaceText = $marketplaceOutput -join "`n"
if ($LASTEXITCODE -ne 0) {
    if ($marketplaceText -match '(?i)already') {
        Write-Warn "marketplace already present; continuing"
    } else {
        Write-Fail "marketplace add failed; see $marketplaceLog"
    }
}

Write-Info "installing plugin $PluginName@$MarketplaceName at user scope"
$pluginOutput = & claude plugin install "$PluginName@$MarketplaceName" --scope user 2>&1
$pluginOutput | Out-File -FilePath $pluginLog -Encoding utf8
$pluginText = $pluginOutput -join "`n"
if ($LASTEXITCODE -ne 0) {
    if ($pluginText -match '(?i)already') {
        Write-Warn "plugin already installed; continuing"
    } else {
        Write-Fail "plugin install failed; see $pluginLog"
    }
}

Write-Info "verifying"
$listOutput = & claude plugin list 2>&1
if ($listOutput -match $PluginName) {
    # ok
}
else {
    Write-Warn "plugin not visible in 'claude plugin list' output; check %USERPROFILE%\.claude\settings.json"
}

Write-Host ""
Write-Host "[vc-roe install] success." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Close any running Claude Code window/process (CLI, desktop app, IDE extension)."
Write-Host "     Claude Code holds an in-memory snapshot of plugin hooks across each process;"
Write-Host "     a restart is required to pick them up."
Write-Host "  2. Reopen Claude Code in a project directory."
Write-Host "  3. The first reply will show: ""Detected tier: T<N> (S<x>/C<y>), <label>."""
Write-Host "  4. Override with /vc-roe:tier <T0..T4> if needed."
Write-Host ""
Write-Host "For local development or troubleshooting, see:"
Write-Host "  https://github.com/stratosjl/vibe-coding-rules#local-development"
Write-Host "  https://github.com/stratosjl/vibe-coding-rules#logs"
