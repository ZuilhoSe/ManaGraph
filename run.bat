@echo off
setlocal

set ROOT=%~dp0

echo Starting ManaGraph backend (FastAPI) and frontend (Vite)...

start "ManaGraph API" cmd /k "cd /d "%ROOT%src" && "%ROOT%.venv\Scripts\uvicorn.exe" service.api:app --reload --port 8000"

start "ManaGraph Web" cmd /k "cd /d "%ROOT%web" && npm run dev"

echo.
echo Two windows opened: "ManaGraph API" and "ManaGraph Web".
echo Close this window whenever you like - the other two keep running.
