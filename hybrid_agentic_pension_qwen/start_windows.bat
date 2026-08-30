@echo off
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env
start http://127.0.0.1:8000
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
