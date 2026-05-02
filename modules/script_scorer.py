"""
script_scorer.py — Evalúa la calidad del guion antes de publicar

Score 1-10 basado en gancho, tensión, giro y pregunta final.
Si score < MIN_SCORE el pipeline regenera automáticamente (hasta 3 veces).
Cadena: Groq → OpenAI → score fijo 8 (no bloquea el pipeline).
"""

import logging
from pathlib import Path
import sys

import config
from modules import llm_service

logger = logging.getLogger(__name__)

MIN_SCORE: int = getattr(config, "SCRIPT_MIN_SCORE", 7)


def _build_prompt(script: dict) -> str:
    hook     = script.get("hook", "")
    text     = script.get("script_text", "")[:600]
    pregunta = script.get("pregunta", "")
    title    = script.get("title", "")
    return (
        f"Evalúa este guion de YouTube Shorts en español del 1 al 10.\n\n"
        f"TÍTULO: {title}\nGANCHO: {hook}\nGUION: {text}\nPREGUNTA: {pregunta}\n\n"
        f"Criterios (peso igual): gancho (¿engancha en 3s?), tensión (¿conflicto fuerte?), "
        f"giro (¿revelación inesperada?), pregunta (¿invita a comentar?).\n\n"
        f"Responde EXACTAMENTE así (sin nada más):\n"
        f"SCORE: [número 1-10]\n"
        f"FEEDBACK: [una línea concreta]"
    )


def _parse_response(text: str) -> tuple[int, str]:
    score    = 5
    feedback = "Sin feedback"
    for line in text.splitlines():
        if line.upper().startswith("SCORE:"):
            try:
                score = max(1, min(10, int(line.split(":", 1)[1].strip())))
            except ValueError:
                pass
        elif line.upper().startswith("FEEDBACK:"):
            feedback = line.split(":", 1)[1].strip()
    return score, feedback


def score_script(script: dict) -> tuple[int, str]:
    """
    Evalúa el guion con Groq → OpenAI como fallback.

    Returns:
        (score 1-10, feedback de una línea)
    """
    prompt = _build_prompt(script)
    text = llm_service.call_llm(prompt, max_tokens=80, temperature=0.2)
    if text:
        return _parse_response(text)
    return 8, "Sin evaluación (APIs no disponibles)"

