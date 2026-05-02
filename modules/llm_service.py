"""
llm_service.py — Servicio centralizado de llamadas a LLM

Prioridad: Groq (gratuito) → OpenAI (fallback) → Ollama (local, solo sync)
Todos los módulos que necesiten un LLM deben usar este servicio en lugar de
duplicar la lógica de fallback.
"""

import asyncio
import logging
import time

import httpx
import requests

import config

logger = logging.getLogger(__name__)

# URLs centralizadas — si cambia el endpoint, solo se toca aquí
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Timestamp hasta el que Groq está bloqueado por rate-limit (compartido por todos los módulos)
_groq_rate_limited_until: float = 0.0

# Último proveedor LLM usado — leído por company.py para incluirlo en la notificación
_last_provider: str = "desconocido"


def get_last_provider() -> str:
    """Devuelve el nombre del último proveedor LLM usado (Groq / OpenAI / Ollama)."""
    return _last_provider


def mark_groq_rate_limited(retry_after_s: int) -> None:
    """Marca Groq como bloqueado por rate-limit. Llamar cuando se recibe 429."""
    global _groq_rate_limited_until
    _groq_rate_limited_until = time.time() + retry_after_s
    logger.warning(f"Groq rate limit — bloqueado {retry_after_s}s. Usando fallback.")


def is_groq_rate_limited() -> bool:
    return time.time() < _groq_rate_limited_until


def call_llm(
    prompt: str,
    system: str = "",
    max_tokens: int = 400,
    temperature: float = 0.8,
    response_format: dict | None = None,
) -> str:
    """
    Llama al LLM disponible: Groq → OpenAI → Ollama.
    Retorna '' si todo falla.

    Args:
        response_format: Si se pasa {"type": "json_object"}, fuerza salida JSON en Groq/OpenAI.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    global _last_provider

    # ── 1. Groq (rápido y gratuito) ──────────────────────────────────────────
    groq_key = config.GROQ_API_KEY
    if groq_key and not is_groq_rate_limited():
        try:
            body: dict = {
                "model":       config.GROQ_MODEL,
                "messages":    messages,
                "temperature": temperature,
                "max_tokens":  max_tokens,
            }
            if response_format:
                body["response_format"] = response_format
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "60"))
                mark_groq_rate_limited(retry_after)
            elif resp.status_code == 200:
                _last_provider = f"Groq ({config.GROQ_MODEL})"
                return resp.json()["choices"][0]["message"]["content"].strip()
            else:
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Groq error: {e}")

    # ── 2. OpenAI (fallback de pago) ─────────────────────────────────────────
    openai_key = config.OPENAI_API_KEY
    if openai_key:
        try:
            body = {
                "model":       config.OPENAI_MODEL,
                "messages":    messages,
                "temperature": temperature,
                "max_tokens":  max_tokens,
            }
            if response_format:
                body["response_format"] = response_format
            resp = requests.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json=body,
                timeout=40,
            )
            if resp.status_code == 200:
                logger.info("LLM: usando OpenAI")
                _last_provider = f"OpenAI ({config.OPENAI_MODEL})"
                return resp.json()["choices"][0]["message"]["content"].strip()
            else:
                logger.warning(f"OpenAI {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"OpenAI error: {e}")

    # ── 3. Ollama (local, sin internet) ──────────────────────────────────────
    try:
        import ollama
        resp = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=messages,
            options={"num_predict": max_tokens},
        )
        _last_provider = f"Ollama ({config.OLLAMA_MODEL})"
        return resp.message.content.strip()
    except Exception as e:
        logger.warning(f"Ollama error: {e}")
        return ""


async def call_llm_async(
    prompt: str,
    system: str = "",
    max_tokens: int = 400,
    temperature: float = 0.8,
) -> str:
    """
    Versión async: Groq → OpenAI. Retorna '' si todo falla.
    (sin Ollama — el contexto async es solo para operaciones de red)
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    global _last_provider

    # ── 1. Groq ──────────────────────────────────────────────────────────────
    groq_key = config.GROQ_API_KEY
    if groq_key and not is_groq_rate_limited():
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {groq_key}"},
                        json={
                            "model":       config.GROQ_MODEL,
                            "messages":    messages,
                            "max_tokens":  max_tokens,
                            "temperature": temperature,
                        },
                    )
                    if r.status_code == 429:
                        retry_after = int(r.headers.get("retry-after", "60"))
                        mark_groq_rate_limited(retry_after)
                        break
                    r.raise_for_status()
                    text = r.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        _last_provider = f"Groq ({config.GROQ_MODEL})"
                        return text
            except Exception as e:
                logger.debug(f"Groq async intento {attempt + 1}/3: {e}")
                if attempt < 2:
                    await asyncio.sleep(3)
        logger.warning("LLM async: Groq falló — intentando OpenAI")

    # ── 2. OpenAI ────────────────────────────────────────────────────────────
    openai_key = config.OPENAI_API_KEY
    if openai_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    OPENAI_URL,
                    headers={"Authorization": f"Bearer {openai_key}"},
                    json={
                        "model":       config.OPENAI_MODEL,
                        "messages":    messages,
                        "max_tokens":  max_tokens,
                        "temperature": temperature,
                    },
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip()
                if text:
                    logger.info("LLM async: usando OpenAI")
                    _last_provider = f"OpenAI ({config.OPENAI_MODEL})"
                    return text
        except Exception as e:
            logger.warning(f"OpenAI async error: {e}")

    return ""
