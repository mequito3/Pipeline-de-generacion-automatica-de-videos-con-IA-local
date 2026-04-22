"""
telegram_commander.py — CEO Dashboard + Agente conversacional via Telegram

Doble rol:
  1. NOTIFICADOR: envia updates de cada etapa del pipeline al CEO
  2. AGENTE:      escucha mensajes y responde comandos / preguntas libres con Groq

Comandos disponibles:
  /ping     → test de conexion
  /status   → estado del sistema
  /stats    → metricas del canal (ultimo snapshot)
  /report   → CEO report inmediato
  /generate → lanza el pipeline ahora (genera + publica un video)
  /help     → lista de comandos
  Texto libre → responde como asistente del factory con IA
"""

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

_API_BASE     = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30
_PARSE_MODE   = "HTML"

_bot_thread: Optional[threading.Thread] = None
_bot_running = False

# ─── Estado de aprobación (compartido con telegram_notifier) ──────────────────
_approval_lock   = threading.Lock()
_approval_event: Optional[threading.Event] = None
_approval_result: Optional[str]            = None
_approval_msg_id: Optional[int]            = None


def register_approval(msg_id: int) -> threading.Event:
    """Registra una aprobación pendiente. Retorna el Event que se activa cuando el CEO responde."""
    global _approval_event, _approval_result, _approval_msg_id
    with _approval_lock:
        _approval_event  = threading.Event()
        _approval_result = None
        _approval_msg_id = msg_id
    return _approval_event


def get_approval_result() -> Optional[str]:
    """Retorna 'approve', 'reject', o None."""
    return _approval_result


def clear_approval() -> None:
    global _approval_event, _approval_result, _approval_msg_id
    with _approval_lock:
        _approval_event  = None
        _approval_result = None
        _approval_msg_id = None


# ─── API base ──────────────────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False}
    url = _API_BASE.format(token=token, method=method)
    try:
        r = httpx.post(url, timeout=_POLL_TIMEOUT + 15, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"Telegram [{method}]: {e}")
        return {"ok": False}


def _chat_id() -> str:
    return str(getattr(config, "TELEGRAM_CHAT_ID", ""))


def _creds_ok() -> bool:
    return bool(getattr(config, "TELEGRAM_BOT_TOKEN", "")) and bool(_chat_id())


# ─── Notificaciones outbound ───────────────────────────────────────────────────

def notify(text: str, parse_mode: str = _PARSE_MODE) -> bool:
    """Envia notificacion al CEO. Silencioso si no hay credenciales."""
    if not _creds_ok():
        return False
    result = _api("sendMessage", json={
        "chat_id":                  _chat_id(),
        "text":                     text[:4096],
        "parse_mode":               parse_mode,
        "disable_web_page_preview": True,
    })
    return result.get("ok", False)


_STAGE_ICONS = {
    "scraping":  "🔍",
    "story":     "📖",
    "tts":       "🎙",
    "video":     "🎬",
    "upload":    "📤",
    "tiktok":    "🎵",
    "growth":    "📈",
    "analytics": "📊",
    "playlist":  "🗂",
    "endscreen": "🖼",
    "error":     "🚨",
    "scheduler": "⏰",
}


def notify_stage(name: str, detail: str = "", icon: str = "") -> None:
    """Notifica una etapa del pipeline — una sola linea."""
    ico = icon or _STAGE_ICONS.get(name.lower(), "▶️")
    msg = f"{ico} {detail}" if detail else f"{ico} {name.upper()}"
    notify(msg)


def notify_pipeline_start(topic: str, source: str = "") -> None:
    src = f" · <i>{source}</i>" if source else ""
    notify(f"🚀 <b>{topic[:80]}</b>{src}")


def notify_pipeline_done(
    title: str,
    youtube_url: str,
    tiktok_url: str = "",
    duration_s: float = 0,
    total_time_s: float = 0,
) -> None:
    line = f"🏆 <b>{title[:60]}</b>"
    if duration_s:
        line += f" | {duration_s:.0f}s"
    if total_time_s:
        line += f" | {total_time_s/60:.1f}min"
    if youtube_url:
        line += f"\n{youtube_url}"
    if tiktok_url:
        line += f"\n🎵 {tiktok_url}"
    notify(line)


def notify_error(context: str, error: str) -> None:
    notify(f"🚨 <b>{context}:</b> <code>{str(error)[:300]}</code>")


def notify_growth(platform: str, action: str, detail: str = "") -> None:
    msg = f"📈 {platform.upper()} · {action}"
    if detail:
        msg += f" — <i>{detail[:100]}</i>"
    notify(msg)


def notify_scheduler_next(next_run_str: str, wait_h: float) -> None:
    notify(f"⏰ Proximo: <b>{next_run_str}</b> ({wait_h:.1f}h)")


# ─── Agente conversacional ─────────────────────────────────────────────────────

_COMMANDS_HELP = {
    "/ping":     "Test de conexion — verifica que el bot responde",
    "/status":   "Estado del sistema (CPU, RAM, config activa)",
    "/stats":    "Metricas del canal (ultimo snapshot de analytics)",
    "/report":   "Genera y envia CEO Report ahora",
    "/weekly":   "Reporte semanal de tendencias (que funciona mejor)",
    "/generate": "Lanza el pipeline: genera y publica un video",
    "/help":     "Esta lista de comandos",
}

_SYSTEM_PROMPT_TPL = (
    "Eres el asistente ejecutivo de {channel}, un canal de YouTube Shorts "
    "en espanol especializado en confesiones y dramas reales. "
    "Respondes al CEO (dueno del canal) via Telegram de forma concisa y directa. "
    "Usas datos reales cuando los tienes. "
    "Siempre respondes en espanol. Maximo 300 palabras."
)


def _handle_command(text: str) -> str:
    cmd = text.strip().lower().split()[0]

    if cmd == "/ping":
        return "🏓 Pong — bot activo"

    if cmd == "/help":
        lines = ["🤖 <b>Comandos disponibles:</b>", ""]
        for c, d in _COMMANDS_HELP.items():
            lines.append(f"<code>{c}</code> — {d}")
        lines += [
            "",
            "Tambien puedes escribir cualquier pregunta sobre el canal",
            "y te respondo con IA.",
        ]
        return "\n".join(lines)

    if cmd == "/status":
        channel = getattr(config, "CHANNEL_NAME", "?")
        lines = [f"🖥 <b>ESTADO — {channel}</b>"]
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            lines += [
                f"CPU: {cpu:.0f}%",
                f"RAM: {ram.percent:.0f}% "
                f"({ram.used // 1024 // 1024} MB / {ram.total // 1024 // 1024} MB)",
            ]
        except ImportError:
            pass
        lines += [
            f"Bot: ✅ activo",
            f"YouTube upload: {'✅ ON' if getattr(config, 'YOUTUBE_UPLOAD_ENABLED', False) else '❌ OFF'}",
            f"TikTok upload:  {'✅ ON' if getattr(config, 'TIKTOK_UPLOAD_ENABLED', False) else '❌ OFF'}",
            f"Aprobacion TG:  {'✅ ON' if getattr(config, 'TELEGRAM_APPROVAL_ENABLED', False) else '❌ OFF'}",
            f"LLM:            {getattr(config, 'GROQ_MODEL', '?')} (Groq)",
        ]
        return "\n".join(lines)

    if cmd == "/stats":
        try:
            from modules.analytics_agent import get_latest_snapshot
            snap = get_latest_snapshot()
            if not snap:
                return (
                    "📊 Sin datos de analytics aun.\n"
                    "Ejecuta: <code>python main.py --analytics</code>"
                )
            lines = [
                f"📊 <b>METRICAS — {snap.timestamp[:10]}</b>",
                f"👥 Suscriptores: <b>{snap.subscribers:,}</b> (+{snap.subs_gained_28d} en 28d)",
                f"👁 Vistas 28d:   <b>{snap.views_28d:,}</b>",
                f"⏱ Watch time:    <b>{snap.watch_time_h_28d:.1f}h</b>",
                "",
                f"🏆 Top video:",
                f"<i>{snap.top_video_title[:60]}</i>",
                f"{snap.top_video_views:,} vistas",
            ]
            if snap.videos:
                lines += ["", "📋 Recientes:"]
                for i, v in enumerate(snap.videos[:3], 1):
                    ctr = f" | CTR {v.ctr_pct}%" if v.ctr_pct else ""
                    lines.append(f"  {i}. <i>{v.title[:45]}</i> — {v.views:,} vistas{ctr}")
            return "\n".join(lines)
        except Exception as e:
            return f"📊 Error leyendo stats: {e}"

    if cmd == "/report":
        notify("⏳ Generando CEO Report...")
        try:
            from modules.ceo_report import run_ceo_report
            return run_ceo_report(send=False)
        except Exception as e:
            return f"❌ Error generando reporte: {e}"

    if cmd == "/weekly":
        notify("⏳ Generando reporte semanal...")
        try:
            from modules.weekly_report import generate_weekly_report
            return generate_weekly_report(send=False)
        except Exception as e:
            return f"❌ Error generando reporte semanal: {e}"

    if cmd == "/generate":
        def _run_pipeline():
            try:
                import main as _main
                _main._safe_run_factory()
            except Exception as e:
                notify_error("/generate", str(e))

        threading.Thread(target=_run_pipeline, daemon=True).start()
        return "🚀 Pipeline iniciado. Recibiras notificaciones de cada etapa."

    return (
        f"❓ Comando no reconocido: <code>{cmd}</code>\n"
        f"Escribe /help para ver los disponibles."
    )


def _ask_groq(user_text: str) -> str:
    """Responde preguntas libres con Groq + contexto del canal."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        return "❌ Groq no configurado (GROQ_API_KEY vacio en .env)."

    extra = ""
    try:
        from modules.analytics_agent import get_latest_snapshot
        snap = get_latest_snapshot()
        if snap:
            extra = (
                f" Datos actuales ({snap.timestamp[:10]}): "
                f"{snap.subscribers:,} suscriptores, "
                f"{snap.views_28d:,} vistas en 28 dias, "
                f"top video: '{snap.top_video_title}' con {snap.top_video_views:,} vistas."
            )
    except Exception:
        pass

    channel = getattr(config, "CHANNEL_NAME", "GATA CURIOSA")
    system  = _SYSTEM_PROMPT_TPL.format(channel=channel) + extra

    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model":       getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                "messages":    [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_text},
                ],
                "max_tokens":  500,
                "temperature": 0.7,
            },
            timeout=25,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"❌ Error consultando el agente: {e}"


# ─── Polling loop ──────────────────────────────────────────────────────────────

def _reply(text: str) -> None:
    response = _handle_command(text) if text.startswith("/") else _ask_groq(text)
    if response:
        _api("sendMessage", json={
            "chat_id":                  _chat_id(),
            "text":                     response[:4096],
            "parse_mode":               _PARSE_MODE,
            "disable_web_page_preview": True,
        })


def start_bot(on_start_notify: bool = True) -> None:
    """Loop bloqueante de polling. Llamar desde hilo daemon."""
    global _bot_running
    if not _creds_ok():
        logger.warning("Telegram Commander: credenciales no configuradas — bot no iniciado")
        return

    _bot_running = True
    if on_start_notify:
        channel = getattr(config, "CHANNEL_NAME", "Factory")
        notify(f"🤖 <b>{channel}</b> — bot activo. /help para comandos.")

    last_update_id = 0
    logger.info("Telegram Commander: escuchando mensajes del CEO...")

    while _bot_running:
        try:
            resp = _api("getUpdates", json={
                "offset":          last_update_id + 1,
                "timeout":         _POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"],
            })
            if not resp.get("ok"):
                time.sleep(5)
                continue

            for update in resp.get("result", []):
                last_update_id = max(last_update_id, update.get("update_id", 0))

                # ── Callback de botones inline (aprobación de video) ──────────
                cb = update.get("callback_query")
                if cb:
                    cb_msg_id = (cb.get("message") or {}).get("message_id")
                    cb_data   = cb.get("data", "")
                    cb_id     = cb.get("id", "")
                    with _approval_lock:
                        if _approval_event and not _approval_event.is_set():
                            if _approval_msg_id is None or cb_msg_id == _approval_msg_id:
                                _approval_result = cb_data
                                _api("answerCallbackQuery", json={"callback_query_id": cb_id})
                                if _approval_msg_id:
                                    _api("editMessageReplyMarkup", json={
                                        "chat_id":      _chat_id(),
                                        "message_id":   _approval_msg_id,
                                        "reply_markup": '{"inline_keyboard":[]}',
                                    })
                                label = "APROBADO" if cb_data == "approve" else "RECHAZADO"
                                notify(f"{'✅' if cb_data == 'approve' else '❌'} {label} — procesando...")
                                _approval_event.set()
                                logger.info(f"Telegram: aprobación {label}")
                    continue

                # ── Mensaje de texto del CEO ──────────────────────────────────
                msg  = update.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) != _chat_id():
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                logger.info(f"Telegram CEO: '{text[:60]}'")
                threading.Thread(target=_reply, args=(text,), daemon=True).start()

        except Exception as e:
            logger.debug(f"Telegram Commander polling: {e}")
            time.sleep(10)


def start_bot_background() -> None:
    """Inicia el bot en hilo daemon (no bloquea el pipeline)."""
    global _bot_thread, _bot_running
    if _bot_thread and _bot_thread.is_alive():
        return
    _bot_running = True
    _bot_thread  = threading.Thread(
        target=start_bot, daemon=True, name="telegram-commander"
    )
    _bot_thread.start()
    logger.info("Telegram Commander: bot iniciado en background")


def stop_bot() -> None:
    global _bot_running
    _bot_running = False
