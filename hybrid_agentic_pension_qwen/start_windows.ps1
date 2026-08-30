Set-Location $PSScriptRoot
if (-not (Test-Path '.venv')) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path '.env')) { Copy-Item .env.example .env }
Start-Process 'http://127.0.0.1:8000'
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
