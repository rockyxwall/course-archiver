Write-Host "Setting up download-course-auto..." -ForegroundColor Cyan

uv sync
if ($LASTEXITCODE -ne 0) { Write-Error "uv sync failed"; exit 1 }

uv run playwright install chromium
if ($LASTEXITCODE -ne 0) { Write-Error "playwright install failed"; exit 1 }

Write-Host "`nDone! Next steps:" -ForegroundColor Green
Write-Host "  1. Copy .env.example to .env and fill in your credentials"
Write-Host "  2. Run: uv run python main.py --train"
Write-Host "     Navigate the site, then Ctrl+C to save training_report.json"
Write-Host "  3. Fill in selectors.json based on training_report.json"
Write-Host "  4. Run: uv run python main.py"
