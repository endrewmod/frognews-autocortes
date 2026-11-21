#!/usr/bin/env python3
"""Local launcher for FrogNews AutoCortes — one command, no cloud.

Starts the render server on this machine (porta 8000) so the web
app — on GitHub Pages or served from this server at /local — can
connect to it while it is running.

Usage:
    python3 run_local.py
    python3 run_local.py --output-dir "C:\\Videos\\Cortes"
    python3 run_local.py --port 8000

On first run it asks which folder finished cuts should be saved to
(default ~/Videos/FrogNews-Cortes) and saves the answer to
local_config.json. It also creates the connection token
(jobs/.token) and prints it — the site needs the address + token
to connect. Keep the token private: anyone on your Wi-Fi with it
can start renders on your machine.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from server import local_config  # noqa: E402


def _ask_output_dir(cli_value: str | None) -> Path:
    """Priority: --output-dir flag > $OUTPUT_DIR > local_config.json >
    interactive prompt (default ~/Videos/FrogNews-Cortes)."""
    if cli_value:
        return local_config.set_output_dir(cli_value)
    if os.environ.get("OUTPUT_DIR", "").strip():
        return local_config.get_output_dir()
    saved = local_config.load_config().get("output_dir", "")
    if saved:
        path = Path(saved).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    default = local_config.default_output_dir()
    if sys.stdin.isatty():
        print("Onde salvar os cortes prontos?")
        print(f"  Pasta padrão: {default}")
        answer = input("  Digite outra pasta ou aperte Enter para usar a padrão: ").strip()
        chosen = answer or str(default)
        return local_config.set_output_dir(chosen)
    return local_config.set_output_dir(str(default))


def _port_is_free(port: int) -> bool:
    """True when nothing is listening on this port yet."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _frognews_running(port: int) -> bool:
    """True when a FrogNews server is already answering on this port
    (its /health answers {"ok": true}). Any other program occupying
    the port does not answer this."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=1.5
        ) as resp:
            return json.loads(resp.read()).get("ok") is True
    except Exception:
        return False


def _pick_port(requested: int) -> tuple[int, str]:
    """Port to start on. Returns (port, status):
    - "free": the requested port is free.
    - "already_running": a FrogNews server is already on this port —
      start nothing, just show its address.
    - "moved": the port was busy with something else, so the next
      free port was chosen (and the banner says so)."""
    if _port_is_free(requested):
        return requested, "free"
    if _frognews_running(requested):
        return requested, "already_running"
    for port in range(requested + 1, requested + 11):
        if _port_is_free(port):
            return port, "moved"
    raise OSError(
        f"portas {requested} a {requested + 10} ocupadas — "
        "feche o outro programa e rode o iniciar de novo"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="FrogNews AutoCortes — servidor local")
    parser.add_argument("--host", default="0.0.0.0",
                        help="endereco de escuta (padrao: esta maquina + rede local)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--output-dir", default=None,
                        help="folder where finished cuts are saved")
    args = parser.parse_args()

    try:
        output_dir = _ask_output_dir(args.output_dir)
    except (ValueError, OSError) as exc:
        print(f"ERRO na pasta de saída: {exc}", file=sys.stderr)
        return 1

    token = local_config.get_or_create_token()
    try:
        port, port_status = _pick_port(args.port)
    except OSError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    urls = local_config.server_urls(port)

    print()
    print("=" * 64)
    if port_status == "already_running":
        print("  🐸 FrogNews AutoCortes — JÁ ESTÁ RODANDO")
        print("=" * 64)
        print("  Você já tem um servidor rodando — não precisa abrir outro.")
        print("  Use o endereço e o token abaixo no site.")
    else:
        print("  🐸 FrogNews AutoCortes — servidor local rodando")
        print("=" * 64)
        if port_status == "moved":
            print(f"  A porta {args.port} estava ocupada — usando a porta {port}.")
    print("  COPIE O ENDEREÇO E O TOKEN ABAIXO NO SITE E CLIQUE EM CONECTAR.")
    print()
    print(f"  Endereço neste PC:   {urls['localhost_url']}")
    print(f"  Endereço na Wi-Fi:  {urls['lan_url']}   (celular, por exemplo)")
    print(f"  Página local:        {urls['localhost_url']}/local")
    print(f"  Pasta dos cortes:    {output_dir}")
    print(f"  Token de conexão:    {token}")
    print()
    print("  No celular: abra o navegador e digite o endereço da Wi-Fi acima.")
    print("  Não compartilhe o token fora da sua rede.")
    print("  Não feche esta janela enquanto usa o site: fechou, o servidor para.")
    if port_status == "already_running":
        print("=" * 64)
        print()
        sys.stdout.flush()
        return 0
    if shutil.which("ffmpeg") is None:
        print("  AVISO: ffmpeg não encontrado no PATH — o render vai falhar.")
        print("         Instale o ffmpeg e rode o iniciar de novo.")
    print("  Para parar: Ctrl+C")
    print("=" * 64)
    print()
    sys.stdout.flush()
    # So /server-info and /local report the same port we are about to bind.
    os.environ["PORT"] = str(port)

    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
