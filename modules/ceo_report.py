"""
ceo_report.py — Reporte ejecutivo diario por Telegram

Lee el snapshot más reciente de analytics_log.json, genera un resumen
ejecutivo con Groq y lo envía por Telegram.

El reporte incluye:
  - Métricas del canal (vistas 28d, watch time, suscriptores) con deltas
  - Top videos de la semana (vistas, CTR, retención)
  - Insight principal: qué tipo de contenido funciona mejor
  - Acción recomendada para los próximos videos
  - Alertas: caídas de CTR, retención baja, o crecimiento estancado
  - Datos reales de YouTube Analytics API (CTR, fuentes de tráfico) si está configurado

Uso:
  python main.py --report          → enviar reporte ahora
  Automático en el scheduler a las 9:00 AM
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import sys
import config
from modules import llm_service

from modules.analytics_agent import (
    ChannelSnapshot,
    get_latest_snapshot,
    get_previous_snapshot,
)

logger = logging.getLogger(__name__)


# ─── Formateo de números para WhatsApp ───────────────────────────────────────

def _fmt(n: float, decimals: int = 0, suffix: str = "") -> str:
    """Formatea un número con separadores de miles y sufijo opcional."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M{suffix}"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K{suffix}"
    if decimals:
        return f"{n:.{decimals}f}{suffix}"
    return f"{int(n)}{suffix}"


def _delta_str(pct: float) -> str:
    """Flecha + porcentaje coloreado con emoji."""
    if pct > 5:
        return f"📈 +{pct:.1f}%"
    if pct < -5:
        return f"📉 {pct:.1f}%"
    return f"➡️ {pct:+.1f}%"


# ─── Generación del reporte con Groq ─────────────────────────────────────────

async def _generate_report_groq(
    snap: ChannelSnapshot,
    prev: Optional[ChannelSnapshot],
) -> str:
    """
    Genera el cuerpo del reporte ejecutivo usando Groq.
    Si Groq no está disponible, usa el template de fallback.
    """
    # Preparar datos para el prompt
    top_videos_text = ""
    for i, v in enumerate(snap.videos[:5], 1):
        delta = f" ({_delta_str(v.views_delta_pct)})" if v.views_delta_pct else ""
        ctr   = f" | CTR {v.ctr_pct}%" if v.ctr_pct else ""
        ret   = f" | Retención {v.avg_view_pct}%" if v.avg_view_pct else ""
        top_videos_text += f"  {i}. \"{v.title[:50]}\": {_fmt(v.views)} vistas{delta}{ctr}{ret}\n"

    prev_subs   = prev.subscribers   if prev else "—"
    prev_views  = _fmt(prev.views_28d) if prev else "—"
    delta_views = f"({_delta_str(snap.views_delta_pct)})" if prev else ""
    delta_subs  = f"({_delta_str(snap.subs_delta_pct)})" if prev else ""

    errors_text = ""
    if snap.errors:
        errors_text = f"\nADVERTENCIAS del sistema: {'; '.join(snap.errors[:3])}"

    # Cargar sugerencias de expansión desde agent_memory
    expansion_text = ""
    try:
        from modules import agent_memory as _am
        exp = _am.get_expansion_suggestions()
        if exp and exp.get("adjacent_niches"):
            niches   = "\n".join(f"  • {n}" for n in exp["adjacent_niches"][:2])
            auto     = "\n".join(f"  • {a}" for a in exp["automation_next_steps"][:2])
            expansion_text = f"\n\nSUGERENCIAS DE EXPANSIÓN (basadas en datos):\n{niches}\n\nSIGUIENTE PASO AUTOMATIZACIÓN:\n{auto}"
    except Exception:
        pass

    # Métricas reales de YouTube Analytics (si está configurado)
    analytics_premium_text = ""
    try:
        from modules import youtube_analytics as _ya
        if _ya.is_configured():
            overview = _ya.get_channel_overview(days=28)
            if overview:
                analytics_premium_text = (
                    f"\nANALYTICS REALES (28d):\n"
                    f"- Retención promedio canal: {overview.get('avg_view_pct', 0):.1f}%\n"
                    f"- Duración promedio vista: {overview.get('avg_view_duration_s', 0):.0f}s\n"
                )
                if overview.get("estimated_revenue_usd", 0) > 0:
                    analytics_premium_text += (
                        f"- Ingresos estimados: ${overview['estimated_revenue_usd']:.2f} USD\n"
                        f"- CPM: ${overview.get('cpm', 0):.2f} | RPM: ${overview.get('rpm', 0):.2f}\n"
                    )
            videos_analy = _ya.get_videos_analytics(max_results=5)
            if videos_analy:
                analytics_premium_text += "\nCTR Y RETENCIÓN POR VIDEO:\n"
                for v in videos_analy[:4]:
                    ctr = v.get("ctr_pct", 0)
                    ret = v.get("avg_view_pct", 0)
                    analytics_premium_text += (
                        f'  • "{v.get("title","?")[:45]}": '
                        f'CTR {ctr:.1f}% | Retención {ret:.0f}%\n'
                    )
            sources = _ya.get_traffic_sources(days=28)
            if sources:
                top_src = sources[:3]
                src_str = " | ".join(f"{s['source']} {s['pct']}%" for s in top_src)
                analytics_premium_text += f"\nFUENTES DE TRÁFICO: {src_str}\n"
            countries = _ya.get_top_countries(days=28, limit=3)
            if countries:
                cty_str = " | ".join(f"{c['country']} {c['views']:,}" for c in countries)
                analytics_premium_text += f"TOP PAÍSES: {cty_str}\n"
    except Exception as _e:
        logger.debug(f"YouTube Analytics premium: {_e}")

    prompt = f"""Eres el analista de un canal de YouTube Shorts en español llamado "{getattr(config, 'CHANNEL_NAME', 'GATA CURIOSA')}" (nicho: confesiones y dramas reales).

Fecha del reporte: {snap.timestamp[:10]}

MÉTRICAS DEL CANAL:
- Suscriptores: {_fmt(snap.subscribers)} {delta_subs} (anterior: {prev_subs})
- Suscriptores ganados (28d): {snap.subs_gained_28d}
- Vistas (28d): {_fmt(snap.views_28d)} {delta_views} (anterior: {prev_views})
- Watch time (28d): {snap.watch_time_h_28d:.1f} horas
- Video más visto: "{snap.top_video_title}" — {_fmt(snap.top_video_views)} vistas

RENDIMIENTO POR VIDEO (recientes):
{top_videos_text if top_videos_text else "  Sin datos de videos"}
{analytics_premium_text}{errors_text}{expansion_text}

INSTRUCCIONES:
Escribe un reporte ejecutivo para WhatsApp (máximo 400 palabras, nunca superar 1800 caracteres totales).
Formato WhatsApp: usa *negrita* para títulos, _cursiva_ para énfasis, emojis estratégicos.
ESTRUCTURA OBLIGATORIA (usa estas secciones con este orden):

📊 *Resumen del canal*
[2-3 líneas con las métricas clave y su tendencia]

🏆 *Top contenido*
[Lista los 3 mejores videos con sus datos más relevantes]

💡 *Insight clave*
[1 observación concreta sobre qué tipo de historia o formato funciona mejor]

⚡ *Acción esta semana*
[1 recomendación específica y accionable para el canal]

🚀 *Expansión sugerida*
[1-2 líneas con el siguiente nicho/plataforma a atacar basándote en las sugerencias de expansión si las hay, o en los datos disponibles]

Si hay caídas importantes (CTR < 3%, retención < 30%, vistas cayendo > 20%), incluye una sección de alerta con emoji 🚨.

Termina siempre con la firma: _Reporte automático — Shorts Factory_

Responde SOLO con el texto del mensaje WhatsApp, sin explicaciones adicionales."""

    try:
        report = await llm_service.call_llm_async(prompt, max_tokens=600, temperature=0.7)
        if not report:
            return _build_fallback_report(snap, prev)
        if len(report) > 1500:
            report = report[:1490].rsplit("\n", 1)[0] + "\n_..._"
        return report
    except Exception as e:
        logger.warning(f"  CEO report LLM falló: {e} — usando fallback")
        return _build_fallback_report(snap, prev)


def _build_fallback_report(
    snap: ChannelSnapshot,
    prev: Optional[ChannelSnapshot],
) -> str:
    """Template de fallback si Groq no está disponible."""
    date_str = snap.timestamp[:10]

    delta_v = f" ({_delta_str(snap.views_delta_pct)})" if prev else ""
    delta_s = f" ({_delta_str(snap.subs_delta_pct)})" if prev else ""

    top_lines = ""
    for i, v in enumerate(snap.videos[:3], 1):
        d = f" {_delta_str(v.views_delta_pct)}" if v.views_delta_pct else ""
        c = f" | CTR {v.ctr_pct}%" if v.ctr_pct else ""
        top_lines += f"  {i}. _{v.title[:45]}_ — {_fmt(v.views)} vistas{d}{c}\n"

    alert = ""
    for v in snap.videos[:3]:
        if v.ctr_pct and v.ctr_pct < 3.0:
            alert = f"\n🚨 *Alerta* CTR bajo en \"{v.title[:35]}\" ({v.ctr_pct}%) — revisar thumbnail."
        if v.avg_view_pct and v.avg_view_pct < 30.0:
            alert += f"\n🚨 *Alerta* Retención baja ({v.avg_view_pct}%) — revisar gancho del video."

    return f"""📊 *{getattr(config, 'CHANNEL_NAME', 'Canal')} — Reporte {date_str}*
━━━━━━━━━━━━━━━━━━━━━━━
👥 Suscriptores: *{_fmt(snap.subscribers)}*{delta_s} (+{snap.subs_gained_28d} este mes)
👁️ Vistas 28d: *{_fmt(snap.views_28d)}*{delta_v}
⏱️ Watch time: *{snap.watch_time_h_28d:.1f}h*

🏆 *Top videos*
{top_lines if top_lines else '  Sin datos de videos'}
💡 *Mejor video:* _{snap.top_video_title[:50] or 'N/A'}_
   {_fmt(snap.top_video_views)} vistas | CTR {snap.top_video_ctr or '—'}%
{alert}
_Reporte automático — Shorts Factory_"""


# ─── Envío Telegram ───────────────────────────────────────────────────────────

def _send_telegram(body: str) -> bool:
    """Envía el reporte por Telegram. Retorna True si se envió."""
    from modules.telegram_notifier import send_message
    token   = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning(
            "  Telegram no configurado — faltan TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_CHAT_ID en .env"
        )
        return False
    import re as _re
    html = _re.sub(r"\*([^*]+)\*", r"<b>\1</b>", body)
    html = _re.sub(r"_([^_]+)_",   r"<i>\1</i>", html)

    ok = send_message(html)
    if ok:
        logger.info("  Reporte enviado por Telegram ✅")
    else:
        logger.warning("  Error enviando reporte por Telegram")
    return ok


# ─── API pública ──────────────────────────────────────────────────────────────

def run_ceo_report(send: bool = True) -> str:
    """
    Genera y opcionalmente envía el reporte ejecutivo del día por Telegram.

    Args:
        send: Si True, envía por Telegram. Si False, solo retorna el texto.

    Returns:
        Texto del reporte generado.
    """
    import asyncio

    logger.info("=== CEO REPORT — generando reporte ===")

    snap = get_latest_snapshot()
    if not snap:
        msg = "No hay datos de analítica aún. Ejecuta primero: python main.py --analytics"
        logger.warning(f"  {msg}")
        return msg

    prev = get_previous_snapshot()

    # Generar reporte con Groq
    if __import__("sys").platform == "win32":
        import asyncio as _asyncio
        loop = _asyncio.ProactorEventLoop()
        _asyncio.set_event_loop(loop)
        try:
            report_text = loop.run_until_complete(_generate_report_groq(snap, prev))
        finally:
            loop.close()
            _asyncio.set_event_loop(None)
    else:
        report_text = asyncio.run(_generate_report_groq(snap, prev))

    logger.info(f"  Reporte generado ({len(report_text)} chars)")
    logger.debug(f"\n{report_text}")

    if send:
        ok = _send_telegram(report_text)
        if not ok:
            logger.warning("  El reporte no se pudo enviar por Telegram")

    return report_text

