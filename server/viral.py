"""Rule-based viral-take scoring for FrogNews AutoCortes.

No API key required. Scores candidate 15-30 s windows from the
transcript (faster-whisper word timestamps) on the signals that made
the approved FrogNews Shorts work: a hook in the first 2 seconds,
exclamation / question, GTA 6 easter-egg keywords, reaction words,
and dense speech with no dead air.

Optional LLM re-rank: if OPENAI_API_KEY is set, the top rule-based
candidates are sent to the OpenAI chat API for a second opinion.
Any LLM failure falls back silently to the rule-based ranking —
the endpoint never fails because the LLM did.
"""
from __future__ import annotations

import os
import re

from .models import Suggestion, TranscriptSegment, Word

# Keywords that performed in the approved FrogNews Shorts.
KEYWORDS = {
    "alien", "aliens", "easter egg", "easter eggs", "goat", "loucura",
    "maluquice", "tentáculo", "tentaculo", "nave mãe", "nave mae",
    "lúcia", "lucia", "sonho", "ovo", "corpo", "gta 6", "gta",
    "segredo", "código", "codigo", "314", "maravilhoso", "sensacional",
    "incrível", "incrivél", "inacreditável", "impossível", "que isso",
}
REACTION_WORDS = {
    "caraca", "caramba", "mano", "rapaz", "nossa", "meu", "deus",
    "ih", "olha", "pera", "peraí", "pera ai", "galera", "rapaziada",
    "que", "cara", "louco", "loucura",
}
HOOK_OPENERS = {
    "que", "olha", "caraca", "mano", "galera", "ih", "não", "não,",
    "pera", "me", "eu", "realmente", "é", "isso",
}

IDEAL_MIN, IDEAL_MAX = 15.0, 30.0
IDEAL_TARGET = 22.0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _words_in(seg: TranscriptSegment) -> list[Word]:
    return seg.words


def _window_text(words: list[Word], start: float, end: float) -> str:
    return " ".join(w.word for w in words if w.start >= start - 0.05 and w.end <= end + 0.05)


def _score_window(words: list[Word], start: float, end: float) -> tuple[int, list[str], str]:
    """Return (score 0-100, reasons, hook text) for one candidate window."""
    text = _window_text(words, start, end)
    low = _norm(text)
    reasons: list[str] = []
    score = 0.0
    dur = end - start

    # --- length: 15-30 s ideal, peak around 22 s ---
    if IDEAL_MIN <= dur <= IDEAL_MAX:
        score += 20
        reasons.append(f"duração ideal ({dur:.0f}s)")
        score += max(0.0, 5 - abs(dur - IDEAL_TARGET) * 0.5)
    elif dur < 12:
        score += 5
        reasons.append("curto demais — pode não segurar")
    elif dur > 35:
        score += 8
        reasons.append("longo para um Short — considere cortar")
    else:
        score += 12

    # --- hook: first 2 seconds ---
    hook_words = [w.word for w in words if w.start < start + 2.0]
    hook = " ".join(hook_words).strip()
    hook_low = _norm(hook)
    if hook:
        if "!" in hook or "?" in hook:
            score += 15
            reasons.append("gancho com exclamação/pergunta nos 2s iniciais")
        if any(k in hook_low for k in KEYWORDS):
            score += 12
            reasons.append("palavra-chave viral logo no gancho")
        if hook_low.split(" ")[0] in HOOK_OPENERS if hook_low else False:
            score += 5
            reasons.append("abertura direta, sem enrolação")

    # --- keywords anywhere in the window ---
    hits = sorted({k for k in KEYWORDS if k in low})
    if hits:
        score += min(20, 6 * len(hits))
        reasons.append("temas fortes: " + ", ".join(hits[:4]))

    # --- reaction / emotion ---
    if "!" in text:
        score += 8
        reasons.append("reação exclamada")
    if "?" in text:
        score += 6
        reasons.append("pergunta que prende")
    reactions = sum(1 for w in words if _norm(w.word.strip(".,!?")) in REACTION_WORDS)
    if reactions >= 2:
        score += 6
        reasons.append("várias reações do streamer")

    # --- speech density: no dead air ---
    win_words = [w for w in words if w.start >= start and w.end <= end]
    if win_words:
        spoken = sum(max(0.0, w.end - w.start) for w in win_words)
        density = spoken / max(dur, 0.01)
        if density >= 0.55:
            score += 10
            reasons.append("fala densa, sem pausa morta")
        elif density < 0.35:
            score -= 8
            reasons.append("muita pausa — jump cuts vão ajudar, mas o take é fraco")

    return max(0, min(100, round(score))), reasons, hook


def suggest_takes(
    segments: list[TranscriptSegment],
    words: list[Word] | None = None,
    top_n: int = 3,
) -> list[Suggestion]:
    """Slide candidate windows over the transcript and return the top N."""
    all_words: list[Word] = list(words or [])
    if not all_words:
        for seg in segments:
            all_words.extend(seg.words)
    if not all_words:
        # No word timestamps: fall back to segment text windows.
        return [
            Suggestion(
                start=s.start, end=min(s.end, s.start + IDEAL_TARGET),
                score=40, hook=s.text[:60],
                reason=["sem timestamps por palavra — sugestão aproximada"],
                transcript_excerpt=s.text[:200],
            )
            for s in segments[:top_n]
        ]
    all_words.sort(key=lambda w: w.start)

    candidates: list[tuple[int, float, float, list[str], str]] = []
    seen: set[tuple[float, float]] = set()
    for seg in segments:
        # Anchor a window at each segment start, and one centred on it.
        for anchor in (seg.start, max(seg.start, (seg.start + seg.end) / 2 - 11)):
            for length in (20.0, 25.0, 16.0):
                start, end = anchor, anchor + length
                key = (round(start, 1), round(end, 1))
                if key in seen:
                    continue
                seen.add(key)
                score, reasons, hook = _score_window(all_words, start, end)
                candidates.append((score, start, end, reasons, hook))

    # De-duplicate overlapping winners: keep the best, drop windows
    # that overlap it by >60% of the shorter window.
    candidates.sort(key=lambda c: c[0], reverse=True)
    picked: list[Suggestion] = []
    for score, start, end, reasons, hook in candidates:
        overlap = any(
            min(end, p.end) - max(start, p.start)
            > 0.6 * min(end - start, p.end - p.start)
            for p in picked
        )
        if overlap:
            continue
        picked.append(
            Suggestion(
                start=round(start, 2), end=round(end, 2), score=score,
                hook=hook or _window_text(all_words, start, start + 2)[:80],
                reason=reasons,
                transcript_excerpt=_window_text(all_words, start, end)[:240],
            )
        )
        if len(picked) >= top_n:
            break
    return picked


def llm_rerank(
    suggestions: list[Suggestion], transcript_text: str, api_key: str
) -> tuple[list[Suggestion], bool]:
    """Optional OpenAI re-rank of the top candidates. Returns
    (suggestions, llm_used). Never raises — returns input unchanged
    on any failure (no key, no network, bad response)."""
    if not api_key or not suggestions:
        return suggestions, False
    try:
        import httpx

        lines = "\n".join(
            f"{i+1}. {s.start:.0f}-{s.end:.0f}s (score {s.score}): {s.hook} | {s.transcript_excerpt[:160]}"
            for i, s in enumerate(suggestions)
        )
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": (
                        "Você escolhe o melhor trecho de uma live de GTA 6 para virar "
                        "um YouTube Short viral (15-30s). Responda SÓ com os números "
                        "dos candidatos em ordem, ex.: 2,1,3")},
                    {"role": "user", "content": (
                        f"Transcrição (trecho): {transcript_text[:3000]}\n\n"
                        f"Candidatos:\n{lines}")},
                ],
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        order = [int(x) - 1 for x in re.findall(r"\d+", content)]
        order = [i for i in order if 0 <= i < len(suggestions)]
        if not order:
            return suggestions, False
        ranked = [suggestions[i] for i in order]
        ranked += [s for s in suggestions if s not in ranked]
        return ranked, True
    except Exception:
        return suggestions, False
