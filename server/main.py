"""FrogNews AutoCortes render server — FastAPI.

Flow (matches the web-app steps, process by process):
  POST /jobs                  {url}            -> metadata only, no download
  POST /jobs/{id}/download                     -> yt-dlp download + audio
  POST /jobs/{id}/transcribe                   -> faster-whisper, word timestamps
  POST /jobs/{id}/suggest                      -> rule-based viral takes (+LLM re-rank if key set)
  POST /jobs/{id}/render      {export JSON}    -> v3 render (1080x1920)
  GET  /jobs/{id}                              -> status + per-step progress
  GET  /jobs/{id}/download                     -> finished MP4

Run locally:  python3 run_local.py   (or double-click iniciar.bat / bash iniciar.sh)
  — asks for the output folder, prints localhost + LAN URLs and the
  connection token, serves the app at http://localhost:8000/local.
Jobs live under $JOBS_DIR (default ./jobs), one folder per job, with
job.json persisted so a restart does not lose job state. Finished
MP4s are also copied to the user-chosen output folder
($OUTPUT_DIR / local_config.json, default ~/Videos/FrogNews-Cortes).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from pydantic import BaseModel

from . import downloader, local_config, transcribe as transcribe_mod
from .models import (
    ExportPayload, Job, JobCreate, RenderRequest, SuggestResponse,
)
from .pipeline import render_cut
from .viral import llm_rerank, suggest_takes

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = Path(os.environ.get("JOBS_DIR", BASE_DIR / "jobs")).resolve()
JOBS_DIR.mkdir(parents=True, exist_ok=True)
MAX_VIDEO_MINUTES = float(os.environ.get("MAX_VIDEO_MINUTES", "240"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

STEPS = [
    "created", "metadata", "downloading", "downloaded", "transcribing",
    "transcribed", "scoring", "scored", "rendering", "done",
]
STEP_PROGRESS = {
    "created": 0, "metadata": 10, "downloading": 25, "downloaded": 40,
    "transcribing": 55, "transcribed": 70, "scoring": 75, "scored": 80,
    "rendering": 90, "done": 100,
}

app = FastAPI(title="FrogNews AutoCortes", version="1.1.0-local")

# CORS — local-first. The GitHub Pages site is HTTPS, so it cannot call
# a plain http:// local address (mixed content, see README). The site
# therefore either runs from this server itself (/local) or through an
# HTTPS tunnel. Allow: the configured origins, localhost, this
# machine's LAN IP, and any *.github.io origin.
_PORT = int(os.environ.get("PORT", "8000"))
_LAN_IP = local_config.get_lan_ip()
_cors_env = os.environ.get("CORS_ORIGINS", "*").strip()
if _cors_env == "*":
    _cors_origins = ["*"]
    _cors_regex = None
else:
    _cors_origins = [
        o.strip()
        for o in _cors_env.split(",")
        if o.strip()
    ] + [
        "http://localhost:8000",
        "http://localhost:8000",
        f"http://{_LAN_IP}:8000",
        f"http://localhost:{_PORT}",
        f"http://localhost:{_PORT}",
        f"http://{_LAN_IP}:{_PORT}",
    ]
    _cors_regex = r"https://[a-zA-Z0-9-]+\.github\.io"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_jobs: dict[str, Job] = {}


# ---------------------------------------------------------------- local-first
def _output_dir() -> Path:
    """Folder where finished cuts are saved (user-chosen, persisted
    in local_config.json). Falls back to ~/Videos/FrogNews-Cortes."""
    return local_config.get_output_dir()


def _provided_token(request: Request, token: str | None = None) -> str | None:
    """Token from the X-Local-Token header or the ?token= query param
    (the query form is for plain <a> downloads, which cannot set a
    header)."""
    if token:
        return token
    header = request.headers.get("x-local-token")
    if header:
        return header
    return request.query_params.get("token")


def _require_token(request: Request, token: str | None = None) -> None:
    """Job endpoints require the token whenever one exists (created on
    first run by run_local.py / iniciar). Public endpoints
    (/, /health, /server-info, /local, /connect) do not."""
    if not local_config.check_token(_provided_token(request, token)):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing token — copy it from http://localhost:8000/local "
            "and send it as X-Local-Token (or ?token=)",
        )


# ---------------------------------------------------------------- helpers
def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(job: Job) -> None:
    _job_dir(job.id)
    (JOBS_DIR / job.id / "job.json").write_text(
        job.model_dump_json(indent=2), encoding="utf-8"
    )


def _set(job: Job, step: str, error: str = "") -> Job:
    job.step = step
    job.status = "error" if error else ("done" if step == "done" else "processing")
    job.progress = STEP_PROGRESS.get(step, job.progress)
    if error:
        job.error = error
    _save(job)
    return job


def _get(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        path = JOBS_DIR / job_id / "job.json"
        if path.exists():
            job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            _jobs[job_id] = job
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


def _load_transcript(job: Job):
    path = _job_dir(job.id) / "transcript.json"
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail="no transcript yet — call POST /jobs/{id}/transcribe first",
        )
    from .models import Transcript

    return Transcript.model_validate_json(path.read_text(encoding="utf-8"))


def _unwrap_render(body: dict) -> RenderRequest:
    """Accept a bare RenderRequest or the web-app export wrapper."""
    if any(k in body for k in ("cut", "render", "export")):
        payload = ExportPayload.model_validate(body)
        inner = payload.cut or payload.render or payload.export
        if inner is None:
            raise HTTPException(status_code=422, detail="export wrapper has no cut/render/export")
        return inner
    return RenderRequest.model_validate(body)


# ---------------------------------------------------------------- routes
@app.get("/")
def root() -> dict:
    urls = local_config.server_urls(_PORT)
    return {
        "service": "FrogNews AutoCortes render server",
        "version": "1.1.0-local",
        "model": "v3 — 1080x1920, face top / gameplay bottom, word captions, zoom on impacts only",
        "mode": "local-first — this machine is the server",
        "localhost_url": urls["localhost_url"],
        "lan_url": urls["lan_url"],
        "output_dir": str(_output_dir()),
        "token_required": local_config.token_exists(),
        "local_page": "/local",
        "endpoints": ["/jobs", "/health", "/server-info", "/connect", "/local"],
    }


@app.get("/server-info")
def server_info() -> dict:
    """Everything the GitHub Pages site needs to connect: addresses,
    output folder, and whether a token is required. The token value
    itself is NEVER returned here — it lives only on the /local page
    (same machine) and in the launcher printout."""
    import importlib.util
    import shutil

    urls = local_config.server_urls(_PORT)
    return {
        "service": "FrogNews AutoCortes render server",
        "version": "1.1.0-local",
        "mode": "local",
        "localhost_url": urls["localhost_url"],
        "lan_ip": urls["lan_ip"],
        "lan_url": urls["lan_url"],
        "output_dir": str(_output_dir()),
        "token_required": local_config.token_exists(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "yt_dlp": shutil.which("yt-dlp") is not None
        or importlib.util.find_spec("yt_dlp") is not None,
        "whisper_model": WHISPER_MODEL,
        "llm_enabled": bool(OPENAI_API_KEY),
        "jobs_dir": str(JOBS_DIR),
    }


@app.get("/connect")
def connect(request: Request, token: str | None = None) -> dict:
    """Connection check for the site: call with the token the user
    pasted and read `connected`. Never raises — a wrong token just
    returns connected=false so the site can show 'token errado'."""
    ok = local_config.check_token(_provided_token(request, token))
    urls = local_config.server_urls(_PORT)
    return {
        "connected": ok,
        "token_required": local_config.token_exists(),
        "localhost_url": urls["localhost_url"],
        "lan_url": urls["lan_url"],
        "output_dir": str(_output_dir()),
        "detail": (
            "connected"
            if ok
            else "token inválido — copie o token de http://localhost:8000/local"
        ),
    }


@app.get("/local", response_class=HTMLResponse)
def local_page() -> str:
    """Minimal landing page served by the local server itself, so the
    user can use the app without the HTTPS/mixed-content problem:
    just open http://localhost:8000 (or the LAN URL on the phone).
    Shows status, IP, output folder and the token — this page is only
    reachable from this machine / local network, never from the
    internet unless the user forwards the port (don't)."""
    import shutil

    urls = local_config.server_urls(_PORT)
    token = local_config.get_or_create_token() if local_config.token_exists() else ""
    out = _output_dir()
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FrogNews AutoCortes — servidor local</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6}}
code{{background:#eee;padding:2px 6px;border-radius:4px;word-break:break-all}}
.ok{{color:green;font-weight:bold}}.warn{{background:#fff3cd;padding:12px;border-radius:8px}}
input{{width:100%;padding:8px;margin:4px 0}}button{{padding:10px 16px;cursor:pointer}}</style>
</head><body>
<h1>🐸 FrogNews AutoCortes — servidor local</h1>
<p>Este computador <b>é</b> o servidor. Enquanto ele estiver ligado e esta
janela/programa aberto, o site consegue cortar os vídeos aqui.</p>
<p>Status: <span class="ok">● rodando</span> — ffmpeg: {'sim' if shutil.which('ffmpeg') else 'NÃO ENCONTRADO — instale o ffmpeg'}</p>
<h2>Conectar o site</h2>
<p>Na página do site, cole estes dados e clique em Conectar:</p>
<p>Endereço (neste computador): <code>{urls['localhost_url']}</code></p>
<p>Endereço (celular no mesmo Wi-Fi): <code>{urls['lan_url']}</code></p>
<p>Token: <code>{token or '(ainda não gerado — reinicie o iniciar)'}</code>
<button onclick="navigator.clipboard.writeText('{token}')">Copiar token</button></p>
<h2>Pasta dos cortes</h2>
<p>Os Shorts prontos são salvos em: <code>{out}</code></p>
<form onsubmit="event.preventDefault();fetch('/settings/output-dir',{{method:'POST',headers:{{'Content-Type':'application/json','X-Local-Token':'{token}'}},body:JSON.stringify({{path:document.getElementById('p').value}})}}).then(r=>r.json()).then(d=>{{document.getElementById('msg').textContent=d.output_dir?'Pasta alterada para '+d.output_dir:'Erro: '+(d.detail||'');location.reload();}});">
<input id="p" placeholder="Ex.: C:\\Videos\\Cortes ou /home/voce/Videos/Cortes">
<button type="submit">Trocar pasta</button></form>
<p id="msg"></p>
<div class="warn"><b>Por que não usar o site do GitHub direto?</b> O site do GitHub é
HTTPS e o navegador bloqueia conexão com endereço http:// local. Por isso
o caminho confiável é usar <b>esta página</b> (http://localhost:8000/local)
ou o IP da rede local. Veja o README, seção “HTTPS e conteúdo misto”.</div>
<p><a href="/health">/health</a> · <a href="/docs">/docs</a> · <a href="/server-info">/server-info</a></p>
</body></html>"""


@app.get("/health")
def health() -> dict:
    import importlib.util
    import shutil

    return {
        "ok": True,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "yt_dlp": shutil.which("yt-dlp") is not None
        or importlib.util.find_spec("yt_dlp") is not None,
        "whisper_model": WHISPER_MODEL,
        "llm_enabled": bool(OPENAI_API_KEY),
        "jobs_dir": str(JOBS_DIR),
    }


@app.get("/jobs")
def list_jobs(request: Request, token: str | None = None) -> list[Job]:
    _require_token(request, token)
    return list(_jobs.values())


class OutputDirRequest(BaseModel):
    path: str


@app.post("/settings/output-dir")
def set_output_dir_endpoint(
    body: OutputDirRequest, request: Request, token: str | None = None
) -> dict:
    """Change the folder where finished cuts are saved. Validates and
    creates the folder, persists it to local_config.json, and applies
    it immediately — the next render saves its MP4 there."""
    _require_token(request, token)
    previous = str(_output_dir())
    try:
        new_dir = local_config.set_output_dir(body.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"output_dir": str(new_dir), "previous_output_dir": previous}


@app.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request, token: str | None = None) -> Job:
    _require_token(request, token)
    """Step 1 — metadata only. No video is downloaded here, so a bad
    or unsupported link fails fast and cheap."""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url must start with http:// or https://")
    try:
        meta = downloader.get_metadata(url)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta["duration"] > MAX_VIDEO_MINUTES * 60:
        raise HTTPException(
            status_code=422,
            detail=f"video is {meta['duration']/60:.0f} min — limit is {MAX_VIDEO_MINUTES:.0f} min (MAX_VIDEO_MINUTES)",
        )
    job = Job(
        id=uuid.uuid4().hex[:12],
        url=url, title=meta["title"], channel=meta["channel"],
        thumbnail=meta["thumbnail"], duration=meta["duration"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    with _lock:
        _jobs[job.id] = job
    _set(job, "metadata")
    job.status = "created"
    _save(job)
    return job


def _do_download(job_id: str) -> None:
    job = _get(job_id)
    try:
        _set(job, "downloading")
        source = downloader.download(job.url, _job_dir(job_id))
        job.source_path = str(source)
        audio = transcribe_mod.extract_audio(source, _job_dir(job_id) / "audio.wav")
        job.audio_path = str(audio)
        _set(job, "downloaded")
        job.status = "downloaded"
        _save(job)
    except Exception as exc:  # noqa: BLE001 — surfaced on the job
        _set(job, job.step, error=str(exc))


@app.post("/jobs/{job_id}/download")
def start_download(
    job_id: str, tasks: BackgroundTasks, request: Request, token: str | None = None
) -> Job:
    _require_token(request, token)
    job = _get(job_id)
    tasks.add_task(_do_download, job_id)
    return job


def _do_transcribe(job_id: str) -> None:
    job = _get(job_id)
    try:
        _set(job, "transcribing")
        audio = Path(job.audio_path) if job.audio_path else _job_dir(job_id) / "audio.wav"
        if not audio.exists():
            raise RuntimeError("no audio yet — call POST /jobs/{id}/download first")
        transcript = transcribe_mod.transcribe(audio, model_name=WHISPER_MODEL)
        transcribe_mod.save_transcript(transcript, _job_dir(job_id) / "transcript.json")
        _set(job, "transcribed")
        job.status = "transcribed"
        _save(job)
    except Exception as exc:  # noqa: BLE001
        _set(job, job.step, error=str(exc))


@app.post("/jobs/{job_id}/transcribe")
def start_transcribe(
    job_id: str, tasks: BackgroundTasks, request: Request, token: str | None = None
) -> Job:
    _require_token(request, token)
    _get(job_id)
    tasks.add_task(_do_transcribe, job_id)
    return _get(job_id)


@app.post("/jobs/{job_id}/suggest")
def suggest(job_id: str, request: Request, token: str | None = None) -> SuggestResponse:
    _require_token(request, token)
    """Step 3 — rule-based viral scoring. No API key needed.
    Optional LLM re-rank runs only when OPENAI_API_KEY is set."""
    job = _get(job_id)
    transcript = _load_transcript(job)
    _set(job, "scoring")
    suggestions = suggest_takes(transcript.segments, transcript.words, top_n=3)
    llm_used = False
    if OPENAI_API_KEY:
        full_text = " ".join(s.text for s in transcript.segments)
        suggestions, llm_used = llm_rerank(suggestions, full_text, OPENAI_API_KEY)
    _set(job, "scored")
    job.status = "scored"
    _save(job)
    return SuggestResponse(job_id=job_id, suggestions=suggestions, llm_used=llm_used)


def _do_render(job_id: str, cut: RenderRequest) -> None:
    job = _get(job_id)
    try:
        _set(job, "rendering")
        source = Path(job.source_path) if job.source_path else _job_dir(job_id) / "source.mp4"
        if not source.exists():
            raise RuntimeError("no downloaded video yet — call POST /jobs/{id}/download first")
        result = render_cut(source, _job_dir(job_id), cut)
        # Finished cut: keep the copy in the job folder AND save one in
        # the user's chosen output folder (local_config.json /
        # $OUTPUT_DIR), so the MP4 shows up where they expect it.
        rendered = Path(result["output_path"])
        dest = _output_dir() / f"frognews-{job_id}.mp4"
        if rendered.resolve() != dest.resolve():
            import shutil

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rendered, dest)
        job.output_path = str(dest)
        job.output_url = f"/jobs/{job_id}/download"
        _set(job, "done")
        _save(job)
    except Exception as exc:  # noqa: BLE001
        _set(job, job.step, error=str(exc))


@app.post("/jobs/{job_id}/render")
def start_render(
    job_id: str, body: dict, tasks: BackgroundTasks,
    request: Request, token: str | None = None,
) -> Job:
    _require_token(request, token)
    job = _get(job_id)
    try:
        cut = _unwrap_render(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid render JSON: {exc}") from exc
    if cut.end and cut.end > job.duration > 0:
        raise HTTPException(
            status_code=422,
            detail=f"cut ends at {cut.end:.0f}s but the video is only {job.duration:.0f}s",
        )
    tasks.add_task(_do_render, job_id, cut)
    return job


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, token: str | None = None) -> Job:
    _require_token(request, token)
    return _get(job_id)


@app.get("/jobs/{job_id}/transcript")
def get_transcript(job_id: str, request: Request, token: str | None = None):
    _require_token(request, token)
    job = _get(job_id)
    return _load_transcript(job).model_dump()


@app.get("/jobs/{job_id}/download")
def download_output(job_id: str, request: Request, token: str | None = None):
    _require_token(request, token)
    job = _get(job_id)
    output = Path(job.output_path) if job.output_path else _job_dir(job_id) / "output.mp4"
    if job.step != "done" or not output.exists():
        raise HTTPException(
            status_code=409,
            detail=f"render not ready (step: {job.step}). Check GET /jobs/{job_id} first.",
        )
    return FileResponse(
        output, media_type="video/mp4",
        filename=f"frognews-{job_id}.mp4",
    )
