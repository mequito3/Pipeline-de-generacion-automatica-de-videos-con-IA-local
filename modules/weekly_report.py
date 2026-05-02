"""
weekly_report.py — Reporte semanal de tendencias del canal

Analiza los últimos 7 días de analytics_log.json y genera con Groq:
  - Qué tipo de historia funcionó mejor (retención / CTR)
  - Mejor hora de publicación de la semana
  - Tendencia de suscriptores vs semana anterior
  - Recomendación concreta para la próxima semana

Se ejecuta automáticamente cada lunes a la misma hora que el reporte diario.
También disponible con: /weekly en el bot de Telegram.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

import config
from modules import llm_service

logger = logging.getLogger(__name__)

_ANALYTICS_FILE = Path(__file__).parent.parent / "analytics_log.json"

# Palabras clave para clasificar el tipo de historia por título
_CATEGORY_KEYWORDS = {
    "infidelidad":  ["infidelidad", "traición", "engaño", "pareja", "amante", "infiel"],
    "familia":      ["familia", "madre", "padre", "hermano", "familiar", "padres", "hijo"],
    "amistad":      ["amiga", "amigo", "amistad", "mejor amiga", "traicionó"],
    "trabajo":      ["jefe", "trabajo", "oficina", "empresa", "compañero"],
    "secreto":      ["secreto", "verdad", "descubrí", "encontré", "escondía"],
    "drama":        ["drama", "confesión", "revelación", "sorpresa", "impactante"],
}


def _classify_title(title: str) -> str:
    title_lower = title.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in title_lower for k in keywords):
            return cat
    return "otro"


def _load_snapshots_last_n_days(days: int = 7) -> list:
    """Carga snapshots de los últimos N días del analytics_log."""
    if not _ANALYTICS_FILE.exists():
        return []
    try:
        with open(_ANALYTICS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = [data]
        cutoff = datetime.now() - timedelta(days=days)
        recent = [
            s for s in data
            if datetime.fromisoformat(s.get("timestamp", "2000-01-01")[:19]) >= cutoff
        ]
        return recent
    except Exception as e:
        logger.warning(f"Weekly report: error cargando analytics: {e}")
        return []


def _build_weekly_summary() -> str:
    """Construye el texto de contexto para el prompt de Groq."""
    snaps = _load_snapshots_last_n_days(7)
    if not snaps:
        return ""

    # Tomar el más reciente para métricas del canal
    latest = sorted(snaps, key=lambda s: s.get("timestamp", ""))[-1]

    # Agregar videos por categoría
    cat_stats: dict[str, list] = defaultdict(list)
    all_videos = []
    for snap in snaps:
        for v in snap.get("videos", []):
            all_videos.append(v)
            cat = _classify_title(v.get("title", ""))
            cat_stats[cat].append({
                "views":     v.get("views", 0),
                "ctr":       v.get("ctr_pct", 0) or 0,
                "retention": v.get("avg_view_pct", 0) or 0,
            })

    # Promedios por categoría
    cat_summary = ""
    for cat, vids in sorted(cat_stats.items(), key=lambda x: -len(x[1])):
        if not vids:
            continue
        avg_views = sum(v["views"] for v in vids) / len(vids)
        avg_ctr   = sum(v["ctr"] for v in vids) / len(vids)
        avg_ret   = sum(v["retention"] for v in vids) / len(vids)
        cat_summary += (
            f"  • {cat}: {len(vids)} videos | "
            f"{avg_views:.0f} vistas prom | "
            f"CTR {avg_ctr:.1f}% | "
            f"Retención {avg_ret:.0f}%\n"
        )

    # Top 3 videos de la semana
    top3 = sorted(all_videos, key=lambda v: v.get("views", 0), reverse=True)[:3]
    top3_text = ""
    for i, v in enumerate(top3, 1):
        top3_text += f"  {i}. \"{v.get('title','?')[:50]}\" — {v.get('views',0):,} vistas\n"

    return (
        f"Canal: {latest.get('subscribers', 0):,} subs | "
        f"{latest.get('views_28d', 0):,} vistas 28d | "
        f"+{latest.get('subs_gained_28d', 0)} subs ganados\n\n"
        f"RENDIMIENTO POR CATEGORÍA (última semana):\n{cat_summary or '  Sin datos suficientes'}\n"
        f"TOP 3 VIDEOS:\n{top3_text or '  Sin datos'}"
    )


def generate_weekly_report(send: bool = True) -> str:
    """
    Genera el reporte semanal de tendencias.

    Args:
        send: Si True, envía por Telegram además de retornar el texto.

    Returns:
        Texto del reporte.
    """
    logger.info("=== WEEKLY REPORT — generando ===")

    summary = _build_weekly_summary()
    if not summary:
        msg = "Sin datos suficientes para el reporte semanal (ejecuta --analytics primero)."
        logger.warning(msg)
        return msg

    channel = config.CHANNEL_NAME

    prompt = (
        f"Eres el analista del canal \"{channel}\" (YouTube Shorts, confesiones y dramas).\n\n"
        f"DATOS DE LA SEMANA:\n{summary}\n\n"
        f"Genera un reporte semanal para Telegram (máximo 300 palabras, máximo 1400 chars).\n"
        f"Formato: usa *negrita* para títulos, _cursiva_ para énfasis.\n\n"
        f"ESTRUCTURA OBLIGATORIA:\n"
        f"📊 *Semana del [fecha]*\n"
        f"[2 líneas con tendencia general]\n\n"
        f"🏆 *Qué funcionó mejor*\n"
        f"[categoría ganadora + por qué, con dato concreto]\n\n"
        f"📉 *Qué no funcionó*\n"
        f"[categoría peor + razón]\n\n"
        f"⚡ *Acción para la próxima semana*\n"
        f"[1 recomendación muy específica: tipo de historia, cantidad, tono]\n\n"
        f"_Reporte semanal — Shorts Factory_"
    )
    report = llm_service.call_llm(prompt, max_tokens=500, temperature=0.6)
    if not report:
        report = f"📊 *Reporte semanal — {channel}*\n\n{summary}\n\n_Reporte semanal — Shorts Factory_"

    if len(report) > 1400:
        report = report[:1390].rsplit("\n", 1)[0] + "\n_..._"

    if send:
        from modules import telegram_commander
        import re as _re
        html = _re.sub(r"\*([^*]+)\*", r"<b>\1</b>", report)
        html = _re.sub(r"_([^_]+)_",   r"<i>\1</i>", html)
        telegram_commander.notify(html)
        logger.info("Weekly report enviado por Telegram")

    return report


def is_monday() -> bool:
    return datetime.now().weekday() == 0

