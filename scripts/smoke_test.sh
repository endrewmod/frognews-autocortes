#!/usr/bin/env bash
# Smoke test for the FrogNews AutoCortes render server.
# Checks the three things every endpoint depends on: ffmpeg, yt-dlp
# and the Python deps. Run from the repo root:  bash scripts/smoke_test.sh
set -u
fail=0

check() {
  if "$@"; then echo "  OK  $*"; else echo "  FAIL  $*"; fail=1; fi
}

echo "== binaries =="
check command -v ffmpeg
check command -v ffprobe
check command -v yt-dlp
check ffmpeg -version

echo "== python deps =="
check python3 -c "import fastapi, uvicorn, pydantic, httpx, dotenv; print('fastapi ok')"
check python3 -c "import faster_whisper; print('faster-whisper ok')"

echo "== server code compiles =="
check python3 -m py_compile server/main.py server/models.py server/pipeline.py server/viral.py server/transcribe.py server/downloader.py server/local_config.py run_local.py

echo "== server imports + health endpoint =="
check python3 -c "from server.main import app; print('app imports ok')"
if command -v curl >/dev/null 2>&1; then
  (python3 -m uvicorn server.main:app --port 8123 >/tmp/autocortes-smoke.log 2>&1 & echo $! > /tmp/autocortes-smoke.pid)
  sleep 4
  check curl -fsS http://127.0.0.1:8123/health
  check curl -fsS http://127.0.0.1:8123/server-info
  check curl -fsS "http://127.0.0.1:8123/connect"
  kill "$(cat /tmp/autocortes-smoke.pid)" 2>/dev/null || true
else
  echo "  SKIP  curl not installed — start the server manually to check /health"
fi

if [ "$fail" -eq 0 ]; then echo "SMOKE TEST PASSED"; else echo "SMOKE TEST FAILED — see FAIL lines above"; exit 1; fi
