"""
telegram_notifier.py — Notificaciones y aprobación de videos vía Telegram Bot

Reemplaza WhatsApp/Twilio completamente. Sin límites de mensajes, sin costo.

Flujo de aprobación:
  1. Envía thumbnail + detalles del video con botones inline ✅ / ❌
  2. Polling de getUpdates hasta recibir respuesta (o timeout)
  3. Retorna True (publicar) o False (descartar)

Setup — dos variables en .env:
  TELEGRAM_BOT_TOKEN=7845xxxxxx:AAFxxxxxxxxxxx
  TELEGRAM_CHAT_ID=123456789
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

_API_BASE     = "https://api.telegram.org/bot{token}/{method}"
_POLL_INTERVAL = 5     # segundos entre consultas a getUpdates
_FILE_TIMEOUT  = 180   # segundos para subir archivo a Telegram


# ─── Llamada base a la API ────────────────────────────────────────────────────

def _api(method: str, token: str, timeout: int = _FILE_TIMEOUT, **kwargs) -> dict:
    url = _API_BASE.format(token=token, method=method)
    try:
        r = httpx.post(url, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"Telegram API [{method}]: {e}")
        return {"ok": False}


def _get_creds() -> tuple[str, str]:
    token   = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = str(getattr(config, "TELEGRAM_CHAT_ID", ""))
    return token, chat_id


# ─── Primitivos de envío ──────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Envía mensaje de texto simple al chat configurado."""
    token, chat_id = _get_creds()
    if not token or not chat_id:
        logger.warning("Telegram: faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env")
        return False
    result = _api("sendMessage", token, json={
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    })
    if not result.get("ok"):
        logger.warning(f"Telegram sendMessage falló: {result}")
    return result.get("ok", False)


def _compress_for_telegram(video_path: Path) -> Optional[Path]:
    """
    Comprime el video a <45MB para enviarlo por Telegram.
    Escala a 540x960 y ajusta bitrate según duración.
    Retorna ruta del archivo temporal, o None si falló.
    """
    import subprocess, tempfile
    try:
        # Obtener duración con ffprobe
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip() or "0")
        if duration <= 0:
            return None

        # Bitrate objetivo para 44MB
        target_bits   = 44 * 8 * 1024 * 1024
        audio_kbps    = 96
        video_kbps    = max(300, int(target_bits / duration / 1000) - audio_kbps)

        tmp = Path(tempfile.gettempdir()) / f"tg_{video_path.stem}_preview.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", "scale=540:960",
            "-c:v", "libx264", "-b:v", f"{video_kbps}k", "-preset", "fast",
            "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            "-movflags", "+faststart",
            str(tmp),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and tmp.exists():
            compressed_mb = tmp.stat().st_size / (1024 * 1024)
            logger.info(f"  Video comprimido para Telegram: {compressed_mb:.1f}MB")
            return tmp
    except Exception as e:
        logger.warning(f"  Compresión para Telegram falló: {e}")
    return None


def _send_video_with_markup(
    token: str, chat_id: str,
    video_path: Path, caption: str,
    thumbnail_path: Optional[Path] = None,
    reply_markup: Optional[dict] = None,
) -> Optional[dict]:
    """
    Envía el video MP4 con caption e inline keyboard.
    Si el video supera 49MB lo comprime automáticamente antes de enviar.
    Retorna el mensaje resultado o None si falló.
    """
    size_mb = video_path.stat().st_size / (1024 * 1024) if video_path.exists() else 0

    send_path = video_path
    temp_file: Optional[Path] = None

    if size_mb > 49:
        logger.info(f"  Video {size_mb:.1f}MB > 49MB — comprimiendo para Telegram...")
        temp_file = _compress_for_telegram(video_path)
        if temp_file:
            send_path = temp_file
        else:
            logger.warning("  Compresión falló — usando thumbnail como fallback")
            return None

    data = {
        "chat_id":             chat_id,
        "caption":             caption[:1024],
        "parse_mode":          "HTML",
        "supports_streaming":  "true",
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    try:
        files = {"video": (send_path.name, open(send_path, "rb"), "video/mp4")}
        if thumbnail_path and thumbnail_path.exists():
            files["thumbnail"] = (thumbnail_path.name, open(thumbnail_path, "rb"), "image/jpeg")
        result = _api("sendVideo", token, data=data, files=files, timeout=300)
        for f in files.values():
            f[1].close()
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)
        if result.get("ok"):
            return result.get("result")
        logger.warning(f"Telegram sendVideo falló: {result.get('description','')}")
    except Exception as e:
        logger.warning(f"Telegram sendVideo: {e}")
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)
    return None


def _send_photo_with_markup(
    token: str, chat_id: str,
    photo_path: Path, caption: str,
    reply_markup: Optional[dict] = None,
) -> Optional[dict]:
    """Envía una foto con caption e inline keyboard (fallback si el video es muy grande)."""
    data = {
        "chat_id":    chat_id,
        "caption":    caption[:1024],
        "parse_mode": "HTML",
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    try:
        with open(photo_path, "rb") as f:
            result = _api("sendPhoto", token,
                          data=data,
                          files={"photo": (photo_path.name, f, "image/jpeg")})
        if result.get("ok"):
            return result.get("result")
    except Exception as e:
        logger.warning(f"Telegram sendPhoto: {e}")
    return None


def _send_text_with_markup(
    token: str, chat_id: str,
    text: str, reply_markup: Optional[dict] = None,
) -> Optional[dict]:
    """Envía mensaje de texto con inline keyboard opcional."""
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    result = _api("sendMessage", token, json=payload)
    if result.get("ok"):
        return result.get("result")
    return None


# ─── Construcción de mensajes ─────────────────────────────────────────────────

def _approval_caption(
    title: str, description: str, tags: list,
    duration_s: float, video_size_mb: float,
    narrator_gender: str, timeout_h: int,
) -> str:
    gender_icon  = "👩" if narrator_gender == "female" else ("👨" if narrator_gender == "male" else "🎙")
    gender_label = "Mujer" if narrator_gender == "female" else ("Hombre" if narrator_gender == "male" else "Auto")
    hashtags = " ".join((t if t.startswith("#") else f"#{t}") for t in (tags or []))[:280]
    desc_prev = (description or "")[:380] + ("…" if len(description or "") > 380 else "")

    lines = [
        f"🎬 <b>{config.CHANNEL_NAME}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📌 <b>TÍTULO:</b>",
        f"<i>{title}</i>",
        "",
        f"⏱ {duration_s:.0f}s   📁 {video_size_mb:.1f} MB   {gender_icon} {gender_label}",
    ]
    if desc_prev:
        lines += ["", "📝 <b>DESCRIPCIÓN:</b>", desc_prev]
    if hashtags:
        lines += ["", f"🏷 {hashtags}"]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⏳ Timeout: <b>{timeout_h}h</b> sin respuesta = descartado",
    ]
    return "\n".join(lines)


def _confirmation_caption(
    title: str, youtube_url: str, tiktok_url: str,
    duration_s: float, video_size_mb: float, word_count: int,
    description: str, tags: list, hook: str, pregunta: str,
) -> str:
    lines = [
        f"📤 <b>Agente de Publicación</b>",
        f"✅ Publicado en YouTube — {config.CHANNEL_NAME}",
        "",
        f"🎬 <i>{title}</i>",
        "",
        f"🔗 {youtube_url or '<i>(URL no capturada)</i>'}",
    ]
    if tiktok_url:
        lines.append(f"🎵 {tiktok_url}")

    stats = "   ".join(filter(None, [
        f"⏱ {duration_s:.0f}s"     if duration_s    else "",
        f"📁 {video_size_mb:.1f}MB" if video_size_mb else "",
        f"📝 {word_count} pal"      if word_count    else "",
    ]))
    if stats:
        lines += ["", stats]
    if hook:
        lines += ["", f"🪝 <i>{hook[:100]}</i>"]
    if pregunta:
        lines += ["", f"❓ <i>{pregunta[:100]}</i>"]
    return "\n".join(lines)


# ─── API pública ──────────────────────────────────────────────────────────────

def send_approval_request(
    video_path: Path,
    thumbnail_path: Optional[Path],
    title: str,
    duration_s: float,
    description: str = "",
    tags: Optional[list] = None,
    narrator_gender: str = "auto",
) -> bool:
    """
    Envía thumbnail con botones inline ✅ Publicar / ❌ Descartar.
    Espera la respuesta haciendo polling directo de getUpdates para evitar
    problemas de threading (el método anterior con Event fallaba si el hilo
    del bot moría silenciosamente o tenía condiciones de carrera).

    Returns:
        True  → aprobado → publicar en YouTube
        False → rechazado, timeout, o error
    """
    token, chat_id = _get_creds()
    if not token or not chat_id:
        logger.error(
            "Telegram: faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env\n"
            "  Desactiva la aprobación: TELEGRAM_APPROVAL_ENABLED=false"
        )
        return False

    timeout_s = int(getattr(config, "TELEGRAM_APPROVAL_TIMEOUT", 7200))
    timeout_h = timeout_s // 3600
    video_size_mb = (video_path.stat().st_size / (1024 * 1024)
                     if video_path and video_path.exists() else 0.0)

    caption = _approval_caption(
        title=title, description=description, tags=tags or [],
        duration_s=duration_s, video_size_mb=video_size_mb,
        narrator_gender=narrator_gender, timeout_h=timeout_h,
    )

    markup = {"inline_keyboard": [[
        {"text": "✅ Publicar en YouTube", "callback_data": "approve"},
        {"text": "❌ Descartar",           "callback_data": "reject"},
    ]]}

    # Enviar video + botones → fallback thumbnail → fallback texto
    sent = None
    if video_path and video_path.exists():
        logger.info("  Enviando video a Telegram para aprobación...")
        sent = _send_video_with_markup(
            token, chat_id, video_path, caption,
            thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.exists() else None,
            reply_markup=markup,
        )
    if sent is None and thumbnail_path and thumbnail_path.exists():
        logger.info("  Video no enviado — usando thumbnail como fallback...")
        sent = _send_photo_with_markup(token, chat_id, thumbnail_path, caption, markup)
    if sent is None:
        sent = _send_text_with_markup(token, chat_id, caption, markup)
    if sent is None:
        logger.error("Telegram: no se pudo enviar mensaje de aprobación")
        return False

    msg_id = sent.get("message_id")
    logger.info(f"Telegram: esperando respuesta CEO (msg_id={msg_id}, timeout={timeout_h}h)...")
    print(f"\n⏳ Video enviado a Telegram para aprobación. Tienes {timeout_h}h para responder.\n")

    # ── Polling directo de getUpdates — sin depender del hilo del bot ─────────
    # Avanzamos el offset al update_id más reciente para ignorar updates anteriores.
    # Así el usuario solo puede aprobar/rechazar EL VIDEO ACTUAL, no uno viejo.
    deadline   = time.time() + timeout_s
    last_offset = _get_current_update_offset(token)

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        poll_timeout = min(30, remaining)
        if poll_timeout <= 0:
            break

        try:
            resp = _api("getUpdates", token,
                        timeout=poll_timeout + 10,
                        json={
                            "offset":          last_offset + 1,
                            "timeout":         poll_timeout,
                            "allowed_updates": ["callback_query"],
                        })
        except Exception as e:
            logger.debug(f"getUpdates error: {e}")
            time.sleep(3)
            continue

        if not resp.get("ok"):
            time.sleep(3)
            continue

        for update in resp.get("result", []):
            last_offset = max(last_offset, update.get("update_id", 0))
            cb = update.get("callback_query")
            if not cb:
                continue

            cb_msg_id = (cb.get("message") or {}).get("message_id")
            cb_data   = cb.get("data", "")
            cb_id     = cb.get("id", "")

            # Solo aceptar el callback del mensaje correcto
            if cb_msg_id != msg_id:
                # Ignorar callbacks de otros mensajes pero responder para quitar spinner
                _api("answerCallbackQuery", token, json={"callback_query_id": cb_id})
                continue

            # Responder al callback (quita el spinner de carga en el cliente)
            _api("answerCallbackQuery", token, json={"callback_query_id": cb_id})

            # Quitar los botones del mensaje para evitar doble-clic
            _api("editMessageReplyMarkup", token, json={
                "chat_id":      chat_id,
                "message_id":   msg_id,
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })

            if cb_data == "approve":
                logger.info("Telegram: ✅ APROBADO por el CEO — publicando en YouTube")
                send_message("✅ <b>APROBADO</b> — subiendo a YouTube...")
                return True
            else:
                logger.info("Telegram: ❌ RECHAZADO por el CEO — descartando video")
                send_message("❌ <b>RECHAZADO</b> — generando un video nuevo...")
                return False

    logger.warning(f"Telegram: timeout {timeout_h}h sin respuesta — video descartado")
    send_message(f"⏰ <b>Timeout:</b> {timeout_h}h sin respuesta. Video descartado.")
    return False


def _get_current_update_offset(token: str) -> int:
    """
    Obtiene el update_id más reciente para que el polling de aprobación
    solo vea updates NUEVOS (posteriores a este momento).
    Evita que clicks viejos en otros mensajes disparen la aprobación.
    """
    try:
        resp = _api("getUpdates", token, timeout=5, json={"offset": -1, "limit": 1})
        results = resp.get("result", [])
        if results:
            return results[-1].get("update_id", 0)
    except Exception:
        pass
    return 0


def send_upload_confirmation(
    title: str,
    youtube_url: str,
    thumbnail_path: Optional[Path] = None,
    duration_s: float = 0.0,
    video_size_mb: float = 0.0,
    word_count: int = 0,
    description: str = "",
    tags: Optional[list] = None,
    hook: str = "",
    pregunta: str = "",
    tiktok_url: str = "",
) -> None:
    """Envía confirmación de upload con thumbnail y todos los detalles."""
    token, chat_id = _get_creds()
    if not token or not chat_id:
        logger.warning("Telegram: credenciales no configuradas — omitiendo confirmación")
        return

    caption = _confirmation_caption(
        title=title, youtube_url=youtube_url, tiktok_url=tiktok_url,
        duration_s=duration_s, video_size_mb=video_size_mb, word_count=word_count,
        description=description, tags=tags or [], hook=hook, pregunta=pregunta,
    )

    sent = None
    if thumbnail_path and thumbnail_path.exists():
        sent = _send_photo_with_markup(token, chat_id, thumbnail_path, caption)

    if sent is None:
        send_message(caption)

    logger.info("Telegram: confirmación de upload enviada")
