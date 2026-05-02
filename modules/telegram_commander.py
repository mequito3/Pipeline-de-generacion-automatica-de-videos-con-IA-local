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
  /generate → genera un video, pide aprobacion y lo encola para el proximo slot
  /next     → cuando se publica el proximo video
  /queue    → muestra el video en cola (si hay alguno)
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

import config
from modules import llm_service

logger = logging.getLogger(__name__)

_API_BASE     = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 5   # Poll corto: el notifier puede tomar control rápido al aprobar
_PARSE_MODE   = "HTML"

_bot_thread: Optional[threading.Thread] = None
_bot_running = False

# ─── Pausa durante aprobación ─────────────────────────────────────────────────
# Cuando telegram_notifier espera un click de aprobación, hace su propio polling.
# Si el commander también está polling al mismo tiempo, hay race condition:
# el commander consume el callback antes que el notifier y la aprobación falla.
# _approval_in_progress.set() pausa el commander para ceder el polling al notifier.
_approval_in_progress: threading.Event = threading.Event()


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

_last_notify_ts: float = 0.0
_MIN_NOTIFY_INTERVAL = 1.2  # Telegram limita a ~1 msg/s por chat; 1.2s da margen


def notify(text: str, parse_mode: str = _PARSE_MODE) -> bool:
    """Envia notificacion al CEO. Silencioso si no hay credenciales.
    Rate-limited a 1 msg/s para evitar 429 de Telegram."""
    global _last_notify_ts
    if not _creds_ok():
        return False
    # Respetar rate limit de Telegram: mínimo 1.2s entre mensajes al mismo chat
    gap = time.time() - _last_notify_ts
    if gap < _MIN_NOTIFY_INTERVAL:
        time.sleep(_MIN_NOTIFY_INTERVAL - gap)
    _last_notify_ts = time.time()
    result = _api("sendMessage", json={
        "chat_id":                  _chat_id(),
        "text":                     text[:4096],
        "parse_mode":               parse_mode,
        "disable_web_page_preview": True,
    })
    if not result.get("ok"):
        # Si Telegram devuelve 429, esperar retry_after y reintentar una vez
        err = str(result)
        if "429" in err or "retry" in err.lower():
            logger.warning("Telegram 429 — esperando 3s y reintentando...")
            time.sleep(3)
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
    "/generate": "Genera un video, espera tu aprobacion y lo encola para el proximo slot",
    "/queue":    "Muestra el video en cola y cuando se publicara",
    "/next":     "Cuando se publicara el proximo video (segun el scheduler)",
    "/music":    "Descarga musica CC0 desde la Biblioteca de audio de YouTube Studio",
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
                _main._safe_run_factory(skip_publish=True)
            except Exception as e:
                notify_error("/generate", str(e))

        threading.Thread(target=_run_pipeline, daemon=True).start()
        return (
            "🎬 Pipeline iniciado.\n"
            "Cuando el video este listo recibiras la preview para aprobar. "
            "Si lo apruebas, quedara en cola y se publicara en el proximo slot programado.\n"
            "Usa <code>python main.py --now</code> para publicar inmediatamente."
        )

    if cmd == "/next":
        try:
            import main as _main
            from datetime import datetime as _dt
            slot = _main._next_slot
            if slot is None:
                return "⏳ Scheduler aun calculando los slots de hoy..."
            now  = _dt.now()
            if slot <= now:
                return "🎬 El slot ya llego — pipeline en proceso ahora mismo."
            diff  = slot - now
            mins  = int(diff.total_seconds() // 60)
            horas = mins // 60
            resto = mins % 60
            tiempo = f"{horas}h {resto}min" if horas else f"{resto} min"
            return (
                f"⏰ Proximo video: <b>{slot.strftime('%d/%m/%Y a las %H:%M')}</b>\n"
                f"   Faltan: {tiempo}"
            )
        except Exception as e:
            return f"❌ Error: {e}"

    if cmd == "/music":
        def _run_music():
            try:
                from modules.youtube_audio_library import download_music_tracks, purge_content_id_files
                removed = purge_content_id_files()
                if removed:
                    notify(f"🗑 Eliminados {len(removed)} archivo(s) con Content ID: {', '.join(removed)}")
                notify("🎵 Descargando música desde la Biblioteca de audio de YouTube Studio...")
                tracks = download_music_tracks(n_tracks=5)
                if tracks:
                    lines = [f"✅ <b>{len(tracks)} tracks descargados:</b>"]
                    for t in tracks:
                        lines.append(f"  • {t}")
                    notify("\n".join(lines))
                else:
                    notify(
                        "⚠️ No se pudo descargar música automáticamente.\n"
                        "Descárgala manualmente desde <b>YouTube Studio → Biblioteca de audio</b> "
                        "y guarda los MP3 en <code>assets/music/</code>."
                    )
            except Exception as e:
                notify_error("/music", str(e))

        threading.Thread(target=_run_music, daemon=True).start()
        return (
            "🎵 Iniciando descarga de música CC0...\n"
            "Abrirá YouTube Studio con tu sesión activa y descargará tracks "
            "<b>Dramatic / Emotional</b> sin atribución requerida.\n"
            "Te aviso cuando termine."
        )

    if cmd == "/queue":
        try:
            import main as _main
            items = _main._queue_load()
            if not items:
                return "📭 Cola vacia — no hay videos pendientes de publicar."
            lines = [f"📋 <b>{len(items)} video(s) en cola:</b>\n"]
            for i, item in enumerate(items, 1):
                title     = item.get("script", {}).get("title", "Sin título")[:60]
                sched_raw = item.get("scheduled_for", "")
                try:
                    from datetime import datetime as _dt
                    sched = _dt.fromisoformat(sched_raw).strftime("%d/%m/%Y a las %H:%M")
                except Exception:
                    sched = sched_raw
                lines.append(f"{i}. <b>{title}</b>\n   ⏰ Publicacion: {sched}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error leyendo cola: {e}"

    return (
        f"❓ Comando no reconocido: <code>{cmd}</code>\n"
        f"Escribe /help para ver los disponibles."
    )


def _ask_groq(user_text: str) -> str:
    """Responde preguntas libres con Groq + contexto del canal."""
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

    channel = config.CHANNEL_NAME
    system  = _SYSTEM_PROMPT_TPL.format(channel=channel) + extra
    result  = llm_service.call_llm(user_text, system=system, max_tokens=500, temperature=0.7)
    return result or "❌ No pude procesar tu pregunta (LLMs no disponibles)."


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
        # Ceder el polling a telegram_notifier mientras hay una aprobación pendiente
        if _approval_in_progress.is_set():
            time.sleep(2)
            continue
        try:
            resp = _api("getUpdates", json={
                "offset":          last_update_id + 1,
                "timeout":         _POLL_TIMEOUT,
                "allowed_updates": ["message"],
            })
            if not resp.get("ok"):
                time.sleep(5)
                continue

            for update in resp.get("result", []):
                last_update_id = max(last_update_id, update.get("update_id", 0))

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


