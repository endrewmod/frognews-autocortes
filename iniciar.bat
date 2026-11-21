@echo off
REM FrogNews AutoCortes — iniciar (Windows).
REM Double-click iniciar.bat. Creates a virtualenv on first run,
REM installs deps, asks for the output folder, then starts the
REM local server on port 8000.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python nao encontrado. Instale o Python 3.11+ em python.org
  echo e marque "Add Python to PATH" na instalacao.
  pause
  exit /b 1
)
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo AVISO: ffmpeg nao encontrado no PATH. O render vai falhar sem ele.
  echo Baixe em https://www.gyan.dev/ffmpeg/builds/ e adicione ao PATH.
)

if not exist .venv (
  echo Criando ambiente (.venv) — so na primeira vez...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
  echo Instalando dependencias — so na primeira vez...
  python -m pip install --upgrade pip -q
  pip install -r requirements.txt
)

python run_local.py %*
pause
