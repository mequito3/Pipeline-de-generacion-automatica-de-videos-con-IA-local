"""
whatsapp_notifier.py — Aprobación de videos vía WhatsApp (Twilio)

Envía el video comprimido + detalles completos a tu WhatsApp personal y espera
tu respuesta (SI / NO) antes de publicar en YouTube.

Requiere: pip install twilio
"""

import logging
import subprocess
import time
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_POLL_INTERVAL  = 30    # segundos entre consultas a Twilio
_UPLOAD_TIMEOUT = 120   # segundos para subir el video (videos pueden ser 10-25 MB)
_WA_VIDEO_LIMIT = 14    # MB máximos para video inline en WhatsApp


# ─── Compresión del video ─────────────────────────────────────────────────────


def _compress_video(video_path: Path) -> Path:
    """
    Comprime el video a <14MB para poder enviarlo inline por WhatsApp.
    Escala a 540x960 (mantiene calidad visual suficiente para previsualizar).
    Retorna el path del video comprimido (en el mismo directorio).
    """
    out_path = video_path.parent / f"{video_path.stem}_wa.mp4"
    if out_path.exists():
        out_path.unlink()

    # Obtener duración real con ffprobe
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip() or "35")
    except Exception:
        duration = 35.0

    # Calcular bitrate para que el archivo quede en ~12MB
    target_kb     = 12 * 1024
    audio_kbps    = 64
    video_kbps    = max(200, int((target_kb * 8) / duration) - audio_kbps)

    logger.info(f"Comprimiendo video para WhatsApp ({video_kbps}kbps, 540x960)...")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", "scale=540:960",
            "-c:v", "libx264", "-b:v", f"{video_kbps}k", "-preset", "fast",
            "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            str(out_path),
        ],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0 or not out_path.exists():
        logger.warning("ffmpeg compresión falló — usando original")
        return video_path

    size_mb = out_path.stat().st_size / (1024 * 1024)
    logger.info(f"Video comprimido: {size_mb:.1f} MB → {out_path.name}")
    return out_path


# ─── Subida temporal ──────────────────────────────────────────────────────────


def _upload_file(file_path: Path) -> str:
    """
    Sube el archivo a un host temporal gratuito y retorna la URL pública.

    Orden de intentos:
      1. litterbox.catbox.moe — retención 24h, sin cuenta, hasta 1GB
      2. gofile.io            — sin límite de tamaño, sin cuenta
      3. file.io              — hasta 2GB, sin cuenta
    """
    mime = "video/mp4" if file_path.suffix == ".mp4" else "image/jpeg"

    size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info(f"Subiendo {file_path.name} ({size_mb:.1f} MB) a host temporal...")

    # ── 1. litterbox.catbox.moe (preferido: URL directa, retención 24h) ────────
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "24h"},
                files={"fileToUpload": (file_path.name, f, mime)},
                timeout=_UPLOAD_TIMEOUT,
            )
        r.raise_for_status()
        url = r.text.strip()
        if url.startswith("http"):
            logger.info(f"Subido a litterbox: {url}")
            return url
        logger.warning(f"litterbox: respuesta inesperada: {r.text[:100]}")
    except Exception as e:
        logger.warning(f"litterbox falló: {e}")

    # ── 2. file.io (URL directa de descarga — Twilio puede accederla) ──────────
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://file.io/?expires=1d",
                files={"file": (file_path.name, f, mime)},
                timeout=_UPLOAD_TIMEOUT,
            )
        r.raise_for_status()
        data = r.json()
        url = data.get("link", "")
        if url.startswith("http"):
            logger.info(f"Subido a file.io: {url}")
            return url
        logger.warning(f"file.io: respuesta inesperada: {data}")
    except Exception as e:
        logger.warning(f"file.io falló: {e}")

    # ── 3. gofile.io (último recurso — solo da página de descarga, no URL directa) ──
    try:
        srv_resp = requests.get("https://api.gofile.io/getServer", timeout=10).json()
        server = srv_resp.get("data", {}).get("server", "")
        if server:
            with open(file_path, "rb") as f:
                r = requests.post(
                    f"https://{server}.gofile.io/uploadFile",
                    files={"file": (file_path.name, f, mime)},
                    timeout=_UPLOAD_TIMEOUT,
                )
            r.raise_for_status()
            data = r.json().get("data", {})
            url = data.get("downloadPage", "")
            if url.startswith("http"):
                logger.info(f"Subido a gofile (página, no URL directa): {url}")
                return url
    except Exception as e:
        logger.warning(f"gofile falló: {e}")

    logger.warning("No se pudo subir el archivo a ningún host — se enviará solo texto")
    return ""


# ─── Construcción del mensaje ─────────────────────────────────────────────────


def _build_message(
    title: str,
    description: str,
    tags: list,
    duration_s: float,
    video_size_mb: float,
    narrator_gender: str,
    timeout_h: int,
) -> str:
    """Construye el mensaje completo para WhatsApp con toda la info del video."""
    gender_icon  = "👩" if narrator_gender == "female" else ("👨" if narrator_gender == "male" else "🎙")
    gender_label = "Mujer" if narrator_gender == "female" else ("Hombre" if narrator_gender == "male" else "Auto")

    # Descripción completa (YouTube acepta hasta 5000 chars; mostramos todo)
    desc_full = (description or "").strip()

    # Todos los hashtags formateados
    hashtags = " ".join(
        (t if t.startswith("#") else f"#{t}") for t in (tags or [])
    )

    lines = [
        f"🎬 *{config.CHANNEL_NAME}*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📌 *TÍTULO:*",
        title,
        "",
        f"⏱ *Duración:* {duration_s:.0f}s   "
        f"📁 *Tamaño:* {video_size_mb:.1f} MB   "
        f"{gender_icon} *Narrador:* {gender_label}",
    ]

    if desc_full:
        lines += [
            "",
            "📝 *DESCRIPCIÓN (para YouTube):*",
            desc_full,
        ]

    if hashtags:
        lines += [
            "",
            "🏷 *HASHTAGS:*",
            hashtags,
        ]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Responde *SI* para subir a YouTube",
        "Responde *NO* para descartar",
        f"_(Sin respuesta en {timeout_h}h = descartado)_",
    ]

    return "\n".join(lines)


# ─── API pública ──────────────────────────────────────────────────────────────


def send_upload_confirmation(
    title: str,
    youtube_url: str,
    thumbnail_path: Path | None = None,
    duration_s: float = 0.0,
    video_size_mb: float = 0.0,
    word_count: int = 0,
    description: str = "",
    tags: list | None = None,
    hook: str = "",
    pregunta: str = "",
    tiktok_url: str = "",
) -> None:
    """
    Envía un mensaje de WhatsApp completo tras publicar el video en YouTube y TikTok.
    Incluye título, enlace, descripción, hashtags, gancho y pregunta final.
    """
    try:
        from twilio.rest import Client  # type: ignore
    except ImportError:
        logger.warning("twilio no instalado — no se puede enviar confirmacion de upload")
        return

    account_sid = getattr(config, "TWILIO_ACCOUNT_SID", "")
    auth_token  = getattr(config, "TWILIO_AUTH_TOKEN", "")
    from_number = getattr(config, "TWILIO_WHATSAPP_FROM", "")
    to_number   = getattr(config, "WHATSAPP_TO", "")

    if not all([account_sid, auth_token, from_number, to_number]):
        missing = [k for k, v in {
            "TWILIO_ACCOUNT_SID": account_sid,
            "TWILIO_AUTH_TOKEN": auth_token,
            "TWILIO_WHATSAPP_FROM": from_number,
            "WHATSAPP_TO": to_number,
        }.items() if not v]
        logger.warning(f"WhatsApp: faltan variables en .env: {', '.join(missing)} — omitiendo notificacion")
        return

    client  = Client(account_sid, auth_token)
    from_ws = f"whatsapp:{from_number}"
    to_ws   = f"whatsapp:{to_number}"

    # Estadísticas
    stats_parts = []
    if duration_s:   stats_parts.append(f"⏱ {duration_s:.0f}s")
    if video_size_mb: stats_parts.append(f"📁 {video_size_mb:.1f}MB")
    if word_count:   stats_parts.append(f"📝 {word_count} palabras")
    stats_line = "   ".join(stats_parts)

    # Hashtags
    hashtags_str = ""
    if tags:
        hashtags_str = " ".join(t if t.startswith("#") else f"#{t}" for t in tags)

    # Construir mensaje completo
    lines = [
        f"✅ *VIDEO PUBLICADO* — {config.CHANNEL_NAME}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🎬 *TÍTULO:*",
        f"_{title}_",
        "",
        f"🔗 *YOUTUBE:*",
        youtube_url if youtube_url else "_(URL no capturada — revisa YouTube Studio)_",
    ]

    if tiktok_url:
        lines += ["", f"🎵 *TIKTOK:*", tiktok_url]

    if stats_line:
        lines += ["", stats_line]

    if hook:
        lines += ["", f"🪝 *GANCHO:* _{hook}_"]

    if description:
        # Limitar descripción a 800 chars para no saturar el mensaje
        desc_preview = description[:800] + ("..." if len(description) > 800 else "")
        lines += [
            "",
            "📝 *DESCRIPCIÓN (YouTube):*",
            desc_preview,
        ]

    if hashtags_str:
        # Máx 200 chars de hashtags
        tags_preview = hashtags_str[:200] + ("..." if len(hashtags_str) > 200 else "")
        lines += [
            "",
            "🏷 *HASHTAGS:*",
            tags_preview,
        ]

    if pregunta:
        lines += ["", f"❓ *PREGUNTA FINAL:* _{pregunta}_"]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "_(Mensaje automático — Shorts Factory)_",
    ]

    body = "\n".join(lines)

    # Subir thumbnail para adjuntarlo
    media_url_list = None
    if thumbnail_path and thumbnail_path.exists():
        try:
            thumb_url = _upload_file(thumbnail_path)
            if thumb_url:
                media_url_list = [thumb_url]
        except Exception as e:
            logger.warning(f"No se pudo subir thumbnail para confirmacion: {e}")

    try:
        kwargs: dict = {"from_": from_ws, "to": to_ws, "body": body}
        if media_url_list:
            kwargs["media_url"] = media_url_list
        client.messages.create(**kwargs)
        logger.info(f"WhatsApp de confirmacion enviado a {to_number}")
        if youtube_url:
            logger.info(f"Enlace del video: {youtube_url}")
    except Exception as e:
        logger.error(
            f"Error enviando confirmacion WhatsApp: {e}\n"
            "  → Si es error de sandbox: reenvía 'join <palabra>' al número de Twilio\n"
            "  → Diagnóstico completo: python main.py --test-wa"
        )


def send_approval_request(
    video_path: Path,
    thumbnail_path: Path | None,
    title: str,
    duration_s: float,
    description: str = "",
    tags: list | None = None,
    narrator_gender: str = "auto",
) -> bool:
    """
    Comprime el video, lo sube y envía a WhatsApp para aprobación.

    Returns:
        True  → usuario respondió SI  → publicar en YouTube
        False → NO, timeout, o error  → descartar
    """
    try:
        from twilio.rest import Client  # type: ignore
    except ImportError:
        logger.error(
            "twilio no está instalado.\n"
            "  Instala con: pip install twilio\n"
            "  O desactiva: WHATSAPP_APPROVAL_ENABLED=false en .env"
        )
        return False

    account_sid = getattr(config, "TWILIO_ACCOUNT_SID", "")
    auth_token  = getattr(config, "TWILIO_AUTH_TOKEN", "")
    from_number = getattr(config, "TWILIO_WHATSAPP_FROM", "")
    to_number   = getattr(config, "WHATSAPP_TO", "")
    timeout_s   = int(getattr(config, "WHATSAPP_APPROVAL_TIMEOUT", 7200))

    if not all([account_sid, auth_token, from_number, to_number]):
        logger.error(
            "Faltan variables de Twilio/WhatsApp en .env:\n"
            "  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,\n"
            "  TWILIO_WHATSAPP_FROM, WHATSAPP_TO"
        )
        return False

    client  = Client(account_sid, auth_token)
    from_ws = f"whatsapp:{from_number}"
    to_ws   = f"whatsapp:{to_number}"
    timeout_h = timeout_s // 3600

    # ── Preparar video para envío ──────────────────────────────────────────────
    media_url_list = None
    video_size_mb  = video_path.stat().st_size / (1024 * 1024) if video_path.exists() else 0.0
    send_path      = video_path

    if video_path.exists():
        # Comprimir si supera el límite de WhatsApp
        if video_size_mb > _WA_VIDEO_LIMIT:
            send_path = _compress_video(video_path)

        logger.info(f"Subiendo video a host temporal...")
        video_url = _upload_file(send_path)
        if video_url:
            media_url_list = [video_url]
        else:
            # Fallback: intentar con el thumbnail
            if thumbnail_path and thumbnail_path.exists():
                thumb_url = _upload_file(thumbnail_path)
                if thumb_url:
                    media_url_list = [thumb_url]
                    logger.info("Enviando thumbnail en lugar del video (fallo de subida)")

        # Limpiar video comprimido temporal
        if send_path != video_path and send_path.exists():
            send_path.unlink(missing_ok=True)

    if media_url_list:
        logger.info(f"Media adjunta al mensaje: {media_url_list[0]}")
    else:
        logger.warning(
            "No se pudo obtener URL pública del video — "
            "el mensaje de WhatsApp se enviará SIN video adjunto"
        )

    # ── Construir mensaje ──────────────────────────────────────────────────────
    body = _build_message(
        title=title,
        description=description,
        tags=tags or [],
        duration_s=duration_s,
        video_size_mb=video_size_mb,
        narrator_gender=narrator_gender,
        timeout_h=timeout_h,
    )

    # ── Enviar ─────────────────────────────────────────────────────────────────
    sent_at_ts = time.time()
    try:
        kwargs: dict = {"from_": from_ws, "to": to_ws, "body": body}
        if media_url_list:
            kwargs["media_url"] = media_url_list
        client.messages.create(**kwargs)
        logger.info(
            f"WhatsApp enviado a {to_number} — "
            f"esperando SI/NO (timeout {timeout_h}h)..."
        )
    except Exception as e:
        logger.error(f"Error enviando WhatsApp: {e}")
        return False

    # ── Polling: esperar respuesta ─────────────────────────────────────────────
    deadline = time.time() + timeout_s
    logger.info("Puedes responder SI o NO desde tu telefono cuando quieras.")

    poll_count = 0
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        poll_count += 1
        remaining = max(0, int(deadline - time.time()))
        # Loguear solo cada 10 polls (~5 min) para no llenar el log
        if poll_count % 10 == 0:
            logger.info(
                f"WhatsApp: esperando respuesta... ({remaining // 60}min restantes)"
            )
        try:
            messages = client.messages.list(
                from_=to_ws,
                to=from_ws,
                limit=10,
            )
            for m in messages:
                # Ignorar mensajes anteriores al envío.
                # Twilio pone date_sent=None en mensajes ENTRANTES (los que tú
                # envías al sandbox) — usar date_created como fallback.
                msg_date = m.date_sent or m.date_created
                if msg_date:
                    try:
                        msg_ts = msg_date.timestamp()
                    except Exception:
                        msg_ts = sent_at_ts + 1
                    if msg_ts <= sent_at_ts:
                        continue

                reply = (m.body or "").strip().lower()
                logger.info(f"WhatsApp recibido: '{reply}'")

                if reply.startswith("si") or reply in ("s", "yes", "sí", "ok", "dale", "1", "publicar"):
                    logger.info("WhatsApp: APROBADO — publicando en YouTube")
                    return True

                if reply.startswith("no") or reply in ("n", "nope", "rechazar", "2", "descartar"):
                    logger.info("WhatsApp: RECHAZADO — video descartado")
                    return False

                logger.warning(f"Respuesta no reconocida: '{reply}' — esperando SI o NO")

        except Exception as e:
            logger.warning(f"Error consultando Twilio: {e}")

    logger.warning(
        f"WhatsApp: timeout de {timeout_h}h sin respuesta — video descartado"
    )
    return False
