# FrogNews AutoCortes — Render Server

Render server for the FrogNews AutoCortes web app. It turns a
YouTube / Twitch link into a finished Short in the **approved v3
model** — no redesign:

- Vertical **1080×1920, 30 fps**, H.264 + AAC
- Facecam on top **1080×640**, gameplay below **1080×1280**
- Audio **loudnorm I=-16, TP=-1.5**
- Captions **DejaVu Sans Bold 76, white + black outline, 2–3 words
  at a time, keywords in yellow**, synced word-by-word
  (faster-whisper word timestamps — never YouTube's grouped VTT)
- **Jump cuts** on speech pauses; **zoom 1.00 static except a short
  trapezoid punch (0.2 s ramps, peak 1.09 strong / 1.07 light) only
  on impacts**
- Swear words stay in the **audio** but are never written in captions

Model source: `~/workspace/frognews-teste/MODELO_FINAL.md` and
`build_v3.py` (generalized here in `server/pipeline.py`).

## Architecture — local-first

```
Site no GitHub Pages (grátis, só a página)     Servidor LOCAL (esta pasta,
                                                roda no computador de quem usa)
┌──────────────────────────┐         ┌──────────────────────────────────┐
│ AutoCortes web app       │  JSON   │ FastAPI + ffmpeg + yt-dlp      │
│ cola o link -> preview   │ ──────▶ │ faster-whisper (local, free)    │
│ escolhe minutagem / take │  MP4    │ viral scoring (rule-based)      │
│ conecta IP + token       │ ◀────── │ v3 render + legendas            │
│ baixa o Short pronto     │         │ cortes salvos na pasta que o    │
└──────────────────────────┘         │ usuário escolheu               │
└──────────────────────────────────┘
```

O site nunca renderiza vídeo — o GitHub Pages não consegue. Ele
coleta o corte (link, minutagem, impactos, legendas), manda o JSON
para o servidor **local** da pessoa (`POST /jobs/{id}/render`) e
baixa o MP4 pronto. O servidor é o computador dela, enquanto ela
está no site.

## Endpoints

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/` | Service info + local / LAN URLs + output folder |
| `GET` | `/health` | ffmpeg / yt-dlp present, Whisper model, LLM on/off |
| `GET` | `/server-info` | What the site needs to connect: localhost URL, LAN IP/URL, output folder, token required? (never returns the token) |
| `GET` | `/connect?token=...` | Connection check — `{connected: true/false}`; a wrong token returns `connected: false`, not an error page |
| `GET` | `/local` | Landing page served by this server: status, IP, output folder, token, change-folder form |
| `POST` | `/settings/output-dir` | `{path}` → validates, creates and saves the cuts folder (needs the token when one exists) |
| `GET` | `/jobs` | List jobs (needs the token when one exists) |
| `POST` | `/jobs` | `{url}` → metadata only (title, channel, thumbnail, duration). No download yet, so a bad link fails fast. Rejects videos over `MAX_VIDEO_MINUTES`. (Needs token) |
| `POST` | `/jobs/{id}/download` | Background: yt-dlp download (≤1080p, mp4) + extract 16 kHz mono WAV for Whisper |
| `POST` | `/jobs/{id}/transcribe` | Background: faster-whisper (`WHISPER_MODEL`, default `small`), `word_timestamps=True`, VAD on. Saved as `transcript.json` |
| `POST` | `/jobs/{id}/suggest` | Rule-based viral scoring from the transcript → top 3 takes with `start`, `end`, `score` 0–100, `hook`, `reason`. LLM re-rank only if `OPENAI_API_KEY` is set |
| `POST` | `/jobs/{id}/render` | Background: accepts the web-app export JSON directly (bare `{start, end, segments, impacts, captions, words, streamer}` or wrapped under `cut` / `render` / `export`). Runs the v3 render |
| `GET` | `/jobs/{id}` | Status + per-step progress |
| `GET` | `/jobs/{id}/transcript` | Full transcript (segments + words) |
| `GET` | `/jobs/{id}/download` | Finished MP4 (`409` until the render is `done`) |

### Job steps / progress

`created (0%) → metadata (10%) → downloading (25%) → downloaded (40%)
→ transcribing (55%) → transcribed (70%) → scoring (75%) → scored (80%)
→ rendering (90%) → done (100%)`. On failure `status="error"` and
`error` holds the message — check `GET /jobs/{id}`.

### Token (conexão)

On first run the launcher creates a token and stores it in
`jobs/.token` (never committed). All `/jobs/*` endpoints and
`POST /settings/output-dir` require it, as the `X-Local-Token`
header (or `?token=` on plain download links). `GET /connect`
checks it and returns `{connected: true/false}`. Public endpoints
(`/`, `/health`, `/server-info`, `/local`, `/connect`) never need
it — `/server-info` never returns the token value; only the
`/local` page (same machine) and the launcher printout show it.

### Render input (web-app export JSON)

```json
{
  "start": 2230.5,
  "end": 2274.0,
  "streamer": "davy",
  "segments": [
    {"start": 2231.0, "end": 2234.2, "punch_start": 12, "punch_end": 36, "peak": 1.09}
  ],
  "impacts": [
    {"start": 0.40, "end": 1.20, "peak": 1.09, "text": "Que loucura", "timeline": "final"}
  ],
  "captions": [
    {"start": 0.36, "end": 1.36, "text": "Que loucura", "timeline": "final"}
  ],
  "words": [{"word": "Que", "start": 2231.36, "end": 2231.6}]
}
```

- `segments` are **source** seconds; `impacts` / `captions` are
  **final** seconds (after jump cuts) unless `"timeline": "source"`.
- Omit `segments` and the server derives jump cuts from `words`
  (pauses ≥ 0.55 s dropped, never mid-word). Omit `captions` and it
  groups `words` into 2–3-word cues.
- `streamer`: `davy` (facecam top-right), `alanzoka` (facecam right,
  middle/lower — verified, **not** top-right), `default` (= davy).
  Or pass an explicit `face_box` `{x, y, w, h}` as fractions of the
  source frame.

## Como usar — servidor local (recomendado, $0/mês)

O fluxo que o usuário segue, do começo ao fim:

### Passo 1 — Entrar no site e baixar o servidor

1. Abre o site do AutoCortes no GitHub Pages.
2. Lá tem o botão **“Baixar servidor local”** — baixa esta pasta
   como ZIP.
3. Descompacta o ZIP em qualquer lugar do computador (ex.:
   `Documentos/frognews-autocortes-server`).

Pré-requisitos (uma vez só): **Python 3.11+** e **ffmpeg** no PATH.
- Windows: instale o Python em python.org marcando “Add Python
  to PATH”; ffmpeg em gyan.dev/ffmpeg/builds.
- Linux (Ubuntu/Debian): `sudo apt install python3 python3-venv ffmpeg`
- Mac: `brew install python ffmpeg`

### Passo 2 — Rodar o iniciar e escolher a pasta dos cortes

- **Windows:** clique duplo em `iniciar.bat`
- **Linux / Mac:** `bash iniciar.sh` (ou `./iniciar.sh`)
- Ou, direto: `python3 run_local.py`

Na primeira vez ele pergunta onde salvar os cortes prontos
(padrão: `~/Videos/FrogNews-Cortes`). Aperte Enter para aceitar
ou digite outra pasta. A escolha fica salva em
`local_config.json` e pode ser trocada depois no site ou em
`http://localhost:8000/local`.

O terminal mostra, por exemplo:

```
🐸 FrogNews AutoCortes — servidor local rodando
  Neste computador:  http://localhost:8000
  Na mesma Wi-Fi:    http://<IP-da-sua-rede>:8000
  Página local:      http://localhost:8000/local
  Pasta dos cortes:  /home/voce/Videos/FrogNews-Cortes
  Token de conexão:  <seu-token>
```

**Deixe essa janela aberta enquanto usa o site.** Fechou, o
servidor para. Os cortes prontos ficam na pasta escolhida, com
nome `frognews-<id>.mp4` — e uma cópia fica em `jobs/<id>/`.

### Passo 3 — Conectar o IP + token no site

No site, no campo de conexão, cole:

1. **Endereço:** `http://localhost:8000` se o site está aberto no
   mesmo computador; ou o endereço da Wi-Fi
   (`http://<IP-da-sua-rede>:8000`) se está no celular.
2. **Token:** o token que apareceu no terminal (ou em
   `http://localhost:8000/local`).
3. Clique em **Conectar**. O site chama `GET /connect` e mostra
   “conectado” — ou “token errado”, se digitou errado.

### Passo 4 — Usar

Cola o link, escolhe a minutagem ou o melhor take, acompanha o
processo passo a passo e baixa o Short. O MP4 final já está
também na pasta escolhida no passo 2.

### Rodar sem o iniciar (manual)

```bash
cp .env.example .env
pip install -r requirements.txt
python3 run_local.py --output-dir ~/Videos/FrogNews-Cortes
# ou: uvicorn server.main:app --port 8000   (sem pasta/token automáticos)
# abra http://localhost:8000/local
```

Smoke test (ffmpeg, yt-dlp, deps, compile, `/health` + `/server-info`):

```bash
bash scripts/smoke_test.sh
```

Example flow with `curl` (job `abc123`, token `$TOKEN`):

```bash
curl -X POST localhost:8000/jobs -H 'Content-Type: application/json' \
  -H "X-Local-Token: $TOKEN" \
  -d '{"url": "https://www.youtube.com/watch?v=XXXX"}'
curl -X POST localhost:8000/jobs/abc123/download -H "X-Local-Token: $TOKEN"
curl -X POST localhost:8000/jobs/abc123/transcribe -H "X-Local-Token: $TOKEN"
curl -X POST localhost:8000/jobs/abc123/suggest -H "X-Local-Token: $TOKEN"
curl -X POST localhost:8000/jobs/abc123/render -H 'Content-Type: application/json' \
  -H "X-Local-Token: $TOKEN" -d @export.json
curl localhost:8000/jobs/abc123 -H "X-Local-Token: $TOKEN"   # poll until step == "done"
curl -O "localhost:8000/jobs/abc123/download?token=$TOKEN"
```

## HTTPS e conteúdo misto — leia isto

**O problema, em linguagem simples:** o site do GitHub Pages é
HTTPS (cadeado). O navegador não deixa uma página HTTPS falar
com um endereço `http://` comum — isso se chama *conteúdo
misto* e o navegador bloqueia sem perguntar. Então o site do
GitHub **não consegue** falar direto com `http://localhost:8000`
ou `http://<IP-da-sua-rede>:8000` do seu computador.

**Os caminhos confiáveis são:**

**(a) Usar o app servido pelo próprio servidor local (recomendado).**
Abra `http://localhost:8000/local` no computador (ou
`http://<IP-da-sua-rede>:8000/local` no celular, mesmo Wi-Fi). Essa
página é `http://` falando com `http://` — sem bloqueio — e
mostra status, IP, pasta e token. O site completo do GitHub
continua existindo para baixar o servidor; o uso do dia a dia
acontece na página local.

**(b) Túnel HTTPS (opcional, para usar o site do GitHub mesmo).**
Um túnel dá um endereço HTTPS público para o seu servidor local:

```bash
# Cloudflare Tunnel (grátis, sem cadastro de cartão)
cloudflared tunnel --url http://localhost:8000
# → https://alguma-coisa.trycloudflare.com  (cole esse endereço no site)

# ou ngrok (conta grátis)
ngrok http 8000
# → https://xxxx.ngrok-free.app
```

O endereço do túnel muda a cada reinício no plano grátis — cole
o novo no site quando mudar. O token continua valendo: sem ele,
ninguém usa o seu servidor mesmo com o endereço público.

## Endereços: localhost, IP da rede e IP público

| Endereço | O que é | Quando usar |
|---|---|---|
| `localhost` | “este computador” — só funciona na própria máquina | Site aberto no mesmo PC do servidor |
| `<IP-da-sua-rede>` (IP da rede local) | Endereço do computador dentro da sua Wi-Fi/casa | Celular ou outro PC na mesma Wi-Fi |
| IP público | Endereço da sua internet visto de fora | Não use sem túnel + token — expor a porta 8000 direto na internet deixa qualquer um tentar usar o seu servidor |

**Firewall:** na primeira vez o Windows/macOS pode perguntar se
permite o Python na rede — clique em permitir **apenas em rede
privada** (casa/trabalho). Se o celular não conectar no IP
`<IP-da-sua-rede>`, verifique: mesmo Wi-Fi, servidor rodando, e a
porta 8000 liberada no firewall da rede privada. **Não libere a
porta no roteador e não exponha o servidor à internet sem o
token** — o token é a única proteção dos endpoints de job.

## Nuvem (opcional — só se quiser servidor 24h sem o PC ligado)

O modo local cobre o uso normal e custa $0. Se um dia quiser que
o servidor fique ligado sem o computador (para outras pessoas
usarem sem instalar nada), aí sim vale uma VPS ~$5–10/mês:

```bash
# na VPS (Ubuntu/Debian)
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone <seu-repo> frognews-autocortes-server
cd frognews-autocortes-server
cp .env.example .env        # CORS_ORIGINS = URL do GitHub Pages
docker compose up -d --build
curl http://localhost:8000/health
```

Atrás de HTTPS (Caddy pega certificado sozinho):

```
# /etc/caddy/Caddyfile
api.seudominio.com {
  reverse_proxy localhost:8000
}
```

E aponte o site para `https://api.seudominio.com`. Render /
Railway grátis também existem, mas dormem após inatividade, têm
pouca RAM e disco efêmero — servem para testar a API, não para
renderizar Shorts com Whisper + ffmpeg.

## Environment variables

| Var | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Optional. Only enables LLM re-rank in `suggest`. Viral scoring works without it |
| `LLM_MODEL` | `gpt-4o-mini` | Used only when the key above is set |
| `WHISPER_MODEL` | `small` | `tiny`/`base` faster, less accurate; `medium` slower. First run downloads the model (~500 MB for `small`) |
| `MAX_VIDEO_MINUTES` | `240` | Videos longer than this are rejected at `POST /jobs` |
| `JOBS_DIR` | `jobs` | Job storage (downloads, transcripts, outputs) |
| `OUTPUT_DIR` | *(empty = `~/Videos/FrogNews-Cortes`)* | Folder for finished cuts. Set by `run_local.py` / the site (`POST /settings/output-dir`) and saved in `local_config.json` |
| `CORS_ORIGINS` | `*` | Set to the GitHub Pages origin in production; localhost + LAN IP are always allowed |
| `PORT` | `8000` | docker-compose host port / launcher port |
| `RENDER_TIMEOUT_SECONDS` | `1200` | ffmpeg render kill timeout |

The connection token is **not** an env var: it is generated on
first run and stored in `jobs/.token` (never committed).

## Costs (honest)

| Item | Cost |
|---|---|
| GitHub Pages (web app) | $0 |
| **Local server (this machine)** | **$0 — the recommended mode** |
| Cloudflare Tunnel / ngrok (optional, HTTPS for the GitHub site) | $0 on the free tier (tunnel URL changes on restart) |
| Render / Railway free tier | $0 — sleeps, little RAM, ephemeral disk; OK for testing, not for rendering |
| Cheap VPS (optional, 24h server) | ~$5–10/month |
| Whisper transcription (local, faster-whisper) | $0 after the one-time model download |
| LLM re-rank (OpenAI, optional) | Paid per use, only when `OPENAI_API_KEY` is set; scoring works without it |
| YouTube / Twitch download | $0, but see the legal note below |

No other paid service is required. There is no per-Short fee in
this server itself.

## Legal note

Only process videos you have the rights to — your own lives, or
videos whose owners allowed reuse. Downloading YouTube or Twitch
videos can violate those platforms' Terms of Service, and reusing
someone else's stream without permission can be a copyright issue
even if the download itself works. This server does not check
rights for you; the operator is responsible for what they feed it.

## Files

```
iniciar.bat  iniciar.sh  run_local.py  local_config.json (criado na 1ª vez)
Dockerfile  docker-compose.yml  requirements.txt  .env.example  .gitignore
server/
  main.py        FastAPI app + job lifecycle + all endpoints + /local
  local_config.py  output folder, LAN IP, token, server URLs
  models.py      Pydantic models matching the web-app export JSON
  pipeline.py    Generalized v3 render (base + jump cuts + zoom + ASS)
  viral.py       Rule-based viral scoring (+ optional LLM re-rank)
  transcribe.py  faster-whisper, word timestamps
  downloader.py  yt-dlp metadata + download, ffprobe
scripts/smoke_test.sh
```
