"""
setup_youtube_publisher.py — Configuración OAuth para publicar videos privados

Ejecutar UNA SOLA VEZ (en tu PC, no en el servidor):
    python setup_youtube_publisher.py

Qué hace:
  1. Abre el navegador para que inicies sesión con tu cuenta de YouTube
  2. Pide permiso de escritura (para cambiar visibilidad de tus videos)
  3. Guarda el token en youtube_publisher_token.json — se renueva automáticamente

Luego copia youtube_publisher_token.json al servidor:
    scp youtube_publisher_token.json usuario@tu-servidor:/opt/publisher/

NOTA: No necesitas tocar nada en Google Cloud Console — usa el mismo
      youtube_client_secrets.json que ya tienes configurado.
"""

import io
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SECRETS_FILE = Path(__file__).parent / "youtube_client_secrets.json"
TOKEN_FILE   = Path(__file__).parent / "youtube_publisher_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def main():
    print("\n" + "=" * 60)
    print("  YouTube Publisher — Setup OAuth")
    print("=" * 60)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("\n❌ Faltan dependencias. Ejecuta:")
        print("   pip install google-auth google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    if not SECRETS_FILE.exists():
        print(f"\n❌ Falta: {SECRETS_FILE.name}")
        print(f"   Asegúrate de tenerlo en: {Path(__file__).parent}")
        sys.exit(1)

    # Si ya hay token válido, solo verificar
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.valid:
            print("\n✅ Token ya configurado y válido.")
            _list_private_videos(creds)
            return
        if creds.expired and creds.refresh_token:
            print("  Token expirado — renovando...")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print("✅ Token renovado.")
            _list_private_videos(creds)
            return

    # Flujo OAuth — abre el navegador
    print("\n  Abriendo navegador para autorización...")
    print("  Inicia sesión con la cuenta de YouTube del canal.")
    print("  Pedirá permiso para 'administrar tu cuenta de YouTube'.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"\n✅ Token guardado en: {TOKEN_FILE.name}")
    print("   Cópialo al servidor con:")
    print(f"   scp {TOKEN_FILE.name} usuario@tu-servidor:/opt/publisher/")

    _list_private_videos(creds)


def _list_private_videos(creds):
    """Muestra los videos privados pendientes de publicar."""
    try:
        from googleapiclient.discovery import build

        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

        # Obtener videos recientes y filtrar privados
        search_resp = yt.search().list(
            part="id,snippet",
            forMine=True,
            type="video",
            maxResults=50,
            order="date",
        ).execute()

        video_ids = [
            item["id"]["videoId"]
            for item in search_resp.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

        if not video_ids:
            print("\n  No se encontraron videos en el canal.")
            return

        status_resp = yt.videos().list(
            part="status,snippet",
            id=",".join(video_ids),
        ).execute()

        private = [
            v for v in status_resp.get("items", [])
            if v.get("status", {}).get("privacyStatus") == "private"
        ]
        private.sort(key=lambda v: v["snippet"]["publishedAt"])

        print(f"\n  Videos privados pendientes de publicar: {len(private)}")
        for i, v in enumerate(private, 1):
            title = v["snippet"]["title"]
            date  = v["snippet"]["publishedAt"][:10]
            print(f"  {i}. [{date}] {title}")

        if private:
            print("\n  El bot publicará el más antiguo primero (FIFO).")
        else:
            print("\n  No hay videos privados ahora — el bot esperará hasta que los haya.")

    except Exception as e:
        print(f"\n  (No se pudo listar videos: {e})")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
