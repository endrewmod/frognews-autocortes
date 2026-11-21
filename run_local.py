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
import os
import shutil
import sys
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
    urls = local_config.server_urls(args.port)

    print()
    print("=" * 64)
    print("  🐸 FrogNews AutoCortes — servidor local rodando")
    print("=" * 64)
    print(f"  Neste computador:  {urls['localhost_url']}")
    print(f"  Na mesma Wi-Fi:   {urls['lan_url']}   (celular, por exemplo)")
    print(f"  Página local:     {urls['localhost_url']}/local")
    print(f"  Pasta dos cortes: {output_dir}")
    print(f"  Token de conexão: {token}")
    print()
    print("  No site, cole o endereço + o token e clique em Conectar.")
    print("  Não compartilhe o token fora da sua rede.")
    if shutil.which("ffmpeg") is None:
        print("  AVISO: ffmpeg não encontrado no PATH — o render vai falhar.")
        print("         Instale o ffmpeg e rode o iniciar de novo.")
    print("  Para parar: Ctrl+C")
    print("=" * 64)
    print()
    sys.stdout.flush()
    # So /server-info and /local report the same port we are about to bind.
    os.environ["PORT"] = str(args.port)

    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
