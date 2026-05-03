"""
publish_next_private.py — Publica el video privado más antiguo del canal

Uso:
    python publish_next_private.py

Toma el video privado subido hace más tiempo (FIFO) y lo cambia a PÚBLICO.
Si no hay videos privados, termina sin error (exit 0).

Configuración en .env:
    TELEGRAM_BOT_TOKEN=xxx
    TELEGRAM_CHAT_ID=xxx

Archivos necesarios (misma carpeta o ruta en YT_SECRETS / YT_TOKEN):
    youtube_client_secrets.json   — credenciales OAuth2
    youtube_publisher_token.json  — token generado con setup_youtube_publisher.py

Cron recomendado en el servidor (19:00 todos los días):
    0 19 * * * /usr/bin/python3 /opt/publisher/publish_next_private.py >> /opt/publisher/publisher.log 2>&1
"""

import io
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
SECRETS_FILE = Path(os.getenv("YT_SECRETS", str(BASE_DIR / "youtube_client_secrets.json")))
TOKEN_FILE   = Path(os.getenv("YT_TOKEN",   str(BASE_DIR / "youtube_publisher_token.json")))
SCOPES       = ["https://www.googleapis.com/auth/youtube"]

load_dotenv(BASE_DIR / ".env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("publisher")


# ── Telegram ──────────────────────────────────────────────────────────────────
def _notify(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram notify failed: {e}")


# ── OAuth ─────────────────────────────────────────────────────────────────────
def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        log.error(f"Token no encontrado: {TOKEN_FILE}")
        log.error("Ejecuta primero: python setup_youtube_publisher.py")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            log.info("Token expirado — renovando...")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            log.info("Token renovado.")
        else:
            log.error("Token inválido y sin refresh_token. Re-ejecuta setup_youtube_publisher.py")
            sys.exit(1)

    return creds


# ── YouTube API ───────────────────────────────────────────────────────────────
def _get_oldest_private_video(yt) -> dict | None:
    """Devuelve el video privado más antiguo (FIFO), o None si no hay ninguno."""
    # 1. Obtener IDs de videos recientes del canal (máx 50)
    search_resp = yt.search().list(
        part="id,snippet",
        forMine=True,
        type="video",
        maxResults=50,
        order="date",
    ).execute()

    items = search_resp.get("items", [])
    if not items:
        return None

    # 2. Consultar estado de privacidad de cada video en batch
    video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
    if not video_ids:
        return None

    status_resp = yt.videos().list(
        part="status,snippet",
        id=",".join(video_ids),
    ).execute()

    # 3. Filtrar solo los privados y ordenar por fecha (FIFO: más antiguo primero)
    private_videos = [
        {
            "video_id":     v["id"],
            "title":        v["snippet"]["title"],
            "published_at": v["snippet"]["publishedAt"],
        }
        for v in status_resp.get("items", [])
        if v.get("status", {}).get("privacyStatus") == "private"
    ]

    if not private_videos:
        return None

    private_videos.sort(key=lambda x: x["published_at"])
    return private_videos[0]


def _publish_video(yt, video_id: str) -> bool:
    """Cambia la visibilidad del video a PUBLIC."""
    try:
        yt.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": False,
                },
            },
        ).execute()
        return True
    except Exception as e:
        log.error(f"Error al publicar video {video_id}: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        log.error("Falta google-api-python-client. Instala con:")
        log.error("  pip install google-auth google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    log.info("Publisher iniciado")

    creds = _get_credentials()
    yt    = build("youtube", "v3", credentials=creds, cache_discovery=False)

    video = _get_oldest_private_video(yt)

    if video is None:
        log.info("No hay videos privados pendientes. Nada que publicar.")
        sys.exit(0)

    video_id = video["video_id"]
    title    = video["title"]
    age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))).days

    log.info(f"Video a publicar: [{video_id}] {title!r} (subido hace {age_days}d)")

    ok = _publish_video(yt, video_id)

    if ok:
        url = f"https://www.youtube.com/watch?v={video_id}"
        log.info(f"✅ Publicado: {url}")
        _notify(
            f"▶️ <b>Video publicado en YouTube</b>\n\n"
            f"🎬 {title}\n"
            f"🔗 {url}\n"
            f"📅 Subido hace {age_days} día{'s' if age_days != 1 else ''}"
        )
    else:
        log.error(f"❌ Falló al publicar [{video_id}] {title!r}")
        _notify(f"❌ Error al publicar video en YouTube:\n{title}\n(ID: {video_id})")
        sys.exit(1)


if __name__ == "__main__":
    main()
