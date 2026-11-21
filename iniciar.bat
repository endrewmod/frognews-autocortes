@echo off
title FrogNews AutoCortes - servidor local
REM FrogNews AutoCortes — iniciar (Windows).
REM Double-click iniciar.bat. Abre esta janela do terminal e a mantem
REM aberta mostrando o endereco, o IP da rede e o token para colar no
REM site. Creates a virtualenv on first run, installs deps, asks for
REM the output folder, then starts the local server on port 8000.
REM Nao feche esta janela enquanto usa o site: fechou, o servidor para.
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
