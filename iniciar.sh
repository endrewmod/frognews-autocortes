#!/usr/bin/env bash
# FrogNews AutoCortes — iniciar (Linux / Mac).
# Double-click / run:  bash iniciar.sh
# Cria um terminal para mostrar o endereço, o IP da rede e o token,
# cria um virtualenv na primeira vez, instala as dependências, pergunta
# a pasta dos cortes e inicia o servidor local na porta 8000.
#
# IMPORTANTE: o servidor precisa rodar dentro de um terminal aberto,
# senão a pessoa não vê o endereço nem o token para colar no site.
# Se este script for aberto pelo gerenciador de arquivos (sem terminal),
# ele se reabre sozinho dentro do Konsole (KDE) ou de outro terminal.
if [ -z "$FROGNEWS_IN_TERMINAL" ] && [ ! -t 1 ]; then
  export FROGNEWS_IN_TERMINAL=1
  SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  if command -v konsole >/dev/null 2>&1; then
    exec konsole --hold -e bash "$SCRIPT" "$@"
  elif command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- bash -c "bash \"$SCRIPT\" \"\$@\"; echo; read -p 'Pressione Enter para fechar... '" _ "$@"
  elif command -v xfce4-terminal >/dev/null 2>&1; then
    exec xfce4-terminal --hold -e bash "$SCRIPT" "$@"
  elif command -v xterm >/dev/null 2>&1; then
    exec xterm -hold -e bash "$SCRIPT" "$@"
  fi
  # Nenhum terminal encontrado: segue sem terminal. O servidor sobe
  # mesmo assim, e o endereço aparece na página local do servidor.
fi
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
