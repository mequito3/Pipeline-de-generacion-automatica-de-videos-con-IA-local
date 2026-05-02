"""
setup_youtube_analytics.py — Configuración OAuth para YouTube Analytics API

Ejecutar UNA SOLA VEZ para autorizar el acceso al canal:
    python setup_youtube_analytics.py

Qué hace:
  1. Abre el navegador para que inicies sesión con tu cuenta de YouTube
  2. Pides permiso de lectura de analytics (solo lectura, nunca escribe)
  3. Guarda el token en youtube_token.json — se renueva automáticamente para siempre

Prerequisitos en Google Cloud Console:
  1. APIs & Services → Library → habilitar "YouTube Analytics API"
  2. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
     - Application type: Desktop app
     - Name: Shorts Factory
  3. Descargar el JSON → guardar como youtube_client_secrets.json en esta carpeta
"""

import io
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SECRETS_FILE = Path(__file__).parent / "youtube_client_secrets.json"
TOKEN_FILE   = Path(__file__).parent / "youtube_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
    print("\n" + "=" * 60)
    print("  YouTube Analytics — Setup OAuth")
    print("=" * 60)

    # Verificar dependencias
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("\n❌ Faltan dependencias. Ejecuta:")
        print("   pip install google-auth google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    # Verificar client_secrets.json
    if not SECRETS_FILE.exists():
        print(f"\n❌ Falta: {SECRETS_FILE.name}")
        print("\nPasos para crearlo:")
        print("  1. console.cloud.google.com → tu proyecto 'Shorts Factory'")
        print("  2. APIs & Services → Library → busca 'YouTube Analytics API' → Enable")
        print("  3. APIs & Services → Credentials → + Create Credentials")
        print("     → OAuth 2.0 Client ID → Desktop app → Name: Shorts Factory → Create")
        print("  4. Descarga el JSON con el botón ⬇ que aparece")
        print(f"  5. Renómbralo a: youtube_client_secrets.json")
        print(f"  6. Ponlo en: {Path(__file__).parent}")
        print("\nLuego vuelve a ejecutar: python setup_youtube_analytics.py")
        sys.exit(1)

    # Si ya hay token válido, solo verificar
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.valid:
            print("\n✅ Token ya configurado y válido.")
            _print_channel_info()
            return
        if creds.expired and creds.refresh_token:
            print("  Token expirado — renovando...")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print("✅ Token renovado correctamente.")
            _print_channel_info()
            return

    # Flujo OAuth — abre el navegador
    print("\n  Abriendo navegador para autorización...")
    print("  Inicia sesión con la cuenta de YouTube del canal.")
    print("  (Solo lectura — nunca modifica tu canal)\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"\n✅ Token guardado en: {TOKEN_FILE.name}")
    print("   Las próximas ejecuciones se renuevan automáticamente.")

    _print_channel_info()


def _print_channel_info():
    """Muestra info básica del canal para confirmar que funciona."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

        resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
        ch   = resp["items"][0]
        name = ch["snippet"]["title"]
        subs = int(ch["statistics"].get("subscriberCount", 0))
        vids = int(ch["statistics"].get("videoCount", 0))
        views = int(ch["statistics"].get("viewCount", 0))

        print(f"\n  Canal conectado: {name}")
        print(f"  Suscriptores   : {subs:,}")
        print(f"  Videos         : {vids:,}")
        print(f"  Vistas totales : {views:,}")
        print("\n  YouTube Analytics listo para usar.")
    except Exception as e:
        print(f"\n  (No se pudo verificar el canal: {e})")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
