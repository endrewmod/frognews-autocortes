#!/usr/bin/env bash
# FrogNews AutoCortes — iniciar (Linux / Mac).
# Double-click / run:  bash iniciar.sh
# Creates a virtualenv on first run, installs deps, asks for the
# output folder, then starts the local server on 0.0.0.0:8000.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERRO: python3 não encontrado. Instale o Python 3.11+ e rode de novo."
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "AVISO: ffmpeg não encontrado no PATH. O render vai falhar sem ele."
  echo "  Ubuntu/Debian: sudo apt install ffmpeg"
  echo "  Mac (Homebrew): brew install ffmpeg"
fi

if [ ! -d .venv ]; then
  echo "Criando ambiente (.venv) — só na primeira vez..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "Instalando dependências — só na primeira vez..."
  pip install --upgrade pip -q
  pip install -r requirements.txt
fi

exec python3 run_local.py "$@"
