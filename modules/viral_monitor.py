"""
viral_monitor.py — Detecta despegues virales en las primeras 2h post-upload

Monitorea las vistas cada 30 min via YouTube Data API v3.
Si supera el umbral → alerta inmediata a Telegram para aprovechar el momento
con engagement (comentarios, likes) cuando YouTube más lo está empujando.

Requiere: YOUTUBE_API_KEY en .env (gratis en console.cloud.google.com)
          VIRAL_THRESHOLD en .env (default: 300 vistas en 30 min)
"""

import logging
import threading
import time
from pathlib import Path
import sys

import httpx

import config

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_S = 30 * 60   # cada 30 minutos
_MAX_CHECKS       = 4          # durante 2 horas
_DEFAULT_THRESHOLD = 300       # vistas en 30 min = señal de despegue


def _get_views(video_id: str) -> int:
    """Consulta vistas del video via YouTube Data API v3. Retorna -1 si no disponible."""
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    if not api_key:
        return -1
    try:
        r = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": video_id, "key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            return int(items[0]["statistics"].get("viewCount", 0))
    except Exception as e:
        logger.debug(f"Viral monitor get_views: {e}")
    return -1


def _monitor_loop(video_id: str, title: str, youtube_url: str) -> None:
    from modules.telegram_commander import notify

    if not getattr(config, "YOUTUBE_API_KEY", ""):
        logger.info("Viral monitor: YOUTUBE_API_KEY no configurado — omitiendo")
        return

    threshold = int(getattr(config, "VIRAL_THRESHOLD", _DEFAULT_THRESHOLD))
    prev_views = 0
    alerted    = False

    for check in range(1, _MAX_CHECKS + 1):
        time.sleep(_CHECK_INTERVAL_S)
        views = _get_views(video_id)
        if views < 0:
            continue

        delta = views - prev_views
        mins  = check * 30
        logger.info(f"Viral monitor [{video_id}]: {views:,} vistas a los {mins}min (+{delta:,})")

        if not alerted and delta >= threshold:
            notify(
                f"🔥 <b>VIDEO DISPARANDO</b> · {mins}min post-upload\n"
                f"<i>{title[:60]}</i>\n"
                f"👁 +{delta:,} vistas en 30min — entra y responde comentarios ahora!\n"
                f"{youtube_url}"
            )
            alerted = True
            logger.info(f"Viral alert enviada: +{delta} vistas a los {mins}min")

        prev_views = views

    if not alerted:
        logger.info(f"Viral monitor [{video_id}]: sin despegue viral en 2h")


def start_monitor(video_id: str, title: str, youtube_url: str) -> None:
    """Inicia el monitoreo en hilo daemon (no bloquea el pipeline)."""
    if not video_id:
        return
    threading.Thread(
        target=_monitor_loop,
        args=(video_id, title, youtube_url),
        daemon=True,
        name=f"viral-{video_id}",
    ).start()
    logger.info(f"Viral monitor iniciado: {video_id} (cada 30min durante 2h)")

