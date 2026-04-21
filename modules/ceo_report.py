"""
ceo_report.py — Reporte ejecutivo diario por WhatsApp

Lee el snapshot más reciente de analytics_log.json, genera un resumen
ejecutivo con Groq y lo envía por WhatsApp via Twilio.

El reporte incluye:
  - Métricas del canal (vistas 28d, watch time, suscriptores) con deltas
  - Top videos de la semana (vistas, CTR, retención)
  - Insight principal: qué tipo de contenido funciona mejor
  - Acción recomendada para los próximos videos
  - Alertas: caídas de CTR, retención baja, o crecimiento estancado

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

import httpx

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from modules.analytics_agent import (
    ChannelSnapshot,
    VideoStats,
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
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        return _build_fallback_report(snap, prev)

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
{errors_text}{expansion_text}

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
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model":       getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  600,
                    "temperature": 0.7,
                },
            )
            r.raise_for_status()
            report = r.json()["choices"][0]["message"]["content"].strip()
            # Limitar a 1500 chars para WhatsApp
            if len(report) > 1500:
                report = report[:1490].rsplit("\n", 1)[0] + "\n_..._"
            return report
    except Exception as e:
        logger.warning(f"  Groq report falló: {e} — usando fallback")
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


# ─── Envío WhatsApp ───────────────────────────────────────────────────────────

def _send_whatsapp(body: str) -> bool:
    """Envía mensaje WhatsApp via Twilio. Retorna True si se envió."""
    sid   = getattr(config, "TWILIO_ACCOUNT_SID", "")
    token = getattr(config, "TWILIO_AUTH_TOKEN", "")
    frm   = getattr(config, "TWILIO_WHATSAPP_FROM", "")
    to    = getattr(config, "WHATSAPP_TO", "")

    if not all([sid, token, frm, to]):
        logger.warning(
            "  WhatsApp no configurado — faltan TWILIO_ACCOUNT_SID / "
            "TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM / WHATSAPP_TO en .env"
        )
        return False

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        msg = client.messages.create(
            from_=f"whatsapp:{frm}",
            to=f"whatsapp:{to}",
            body=body,
        )
        logger.info(f"  WhatsApp enviado — SID: {msg.sid} | Estado: {msg.status}")
        return True
    except ImportError:
        logger.error("  twilio no instalado — ejecuta: pip install twilio")
        return False
    except Exception as e:
        logger.error(f"  Error enviando WhatsApp: {e}")
        return False


# ─── API pública ──────────────────────────────────────────────────────────────

def run_ceo_report(send: bool = True) -> str:
    """
    Genera y opcionalmente envía el reporte ejecutivo del día.

    Args:
        send: Si True, envía por WhatsApp. Si False, solo retorna el texto.

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
        ok = _send_whatsapp(report_text)
        if not ok:
            logger.warning("  El reporte no se pudo enviar por WhatsApp")

    return report_text
