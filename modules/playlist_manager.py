import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)

_TOKEN_FILE = Path(__file__).parent.parent / "youtube_publisher_token.json"
_SCOPES = ["https://www.googleapis.com/auth/youtube"]

# ─── Categorías y sus palabras clave ─────────────────────────────────────────

PLAYLIST_CATEGORIES = [
    {
        "name": "Traiciones de Pareja 💔",
        "keywords": [
            "engañó", "engaño", "infidelidad", "infiel", "traición", "traicionó",
            "cuernos", "pareja", "novio", "novia", "esposo", "esposa", "amante",
            "celular", "mensaje", "mentiras", "mintió", "doble vida",
        ],
        "description": "Las historias de traición más impactantes del canal. ¿Tú lo hubieras perdonado?",
    },
    {
        "name": "Secretos Familiares 🤫",
        "keywords": [
            "familia", "familiar", "madre", "padre", "hermano", "hermana",
            "secreto", "adoptado", "herencia", "padres", "abuela", "abuelo",
            "suegra", "cuñado", "cuñada", "parentesco", "origen",
        ],
        "description": "Los secretos que las familias esconden durante años. Historias que te dejarán sin palabras.",
    },
    {
        "name": "Amigos que Traicionan 😤",
        "keywords": [
            "amigo", "amiga", "amistad", "mejor amiga", "mejor amigo",
            "traicionó", "robó", "usó", "aprovechó", "fingió", "fingía",
            "grupo", "falsas", "falsos",
        ],
        "description": "Cuando la persona en quien más confiabas te falla. Historias reales de traición entre amigos.",
    },
    {
        "name": "Relaciones Tóxicas ⚠️",
        "keywords": [
            "tóxica", "tóxico", "narcisista", "control", "manipuló", "manipulación",
            "abuso", "gaslight", "celoso", "celosa", "acoso", "amenazó",
            "dependencia", "aisló", "manipulaba",
        ],
        "description": "Historias de relaciones donde el amor se convirtió en control. ¿Lo reconocerías a tiempo?",
    },
    {
        "name": "Dinero y Estafas 💸",
        "keywords": [
            "dinero", "robó", "estafa", "ahorros", "deuda", "préstamo",
            "negocio", "socio", "trabajo", "jefe", "despidieron", "ilegal",
            "perdí todo", "dinero",
        ],
        "description": "Cuando el dinero destruye relaciones. Estafas, robos y traiciones económicas reales.",
    },
    {
        "name": "Confesiones Impactantes 😱",
        "keywords": [],  # Categoría por defecto — recoge todo lo que no encaja arriba
        "description": "Las confesiones más dramáticas e impactantes que recibirás en tu feed. Activa las notificaciones 🔔",
    },
]


def _classify_video(title: str) -> dict:
    """Determina a qué playlist pertenece un video según su título."""
    title_lower = title.lower()
    scores = []
    for cat in PLAYLIST_CATEGORIES[:-1]:  # excluir la categoría por defecto
        score = sum(1 for kw in cat["keywords"] if kw in title_lower)
        scores.append((score, cat))

    scores.sort(key=lambda x: x[0], reverse=True)
    if scores and scores[0][0] > 0:
        return scores[0][1]
    return PLAYLIST_CATEGORIES[-1]  # por defecto: Confesiones Impactantes


# ─── Cliente YouTube API ──────────────────────────────────────────────────────

def _get_yt_client():
    creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _list_playlists(yt) -> dict[str, str]:
    """Retorna {nombre_playlist: playlist_id} para todas las playlists del canal."""
    playlists = {}
    request = yt.playlists().list(part="snippet", mine=True, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            name = item["snippet"]["title"]
            pid = item["id"]
            playlists[name] = pid
        next_page = response.get("nextPageToken")
        if next_page:
            request = yt.playlists().list(
                part="snippet", mine=True, maxResults=50, pageToken=next_page
            )
        else:
            request = None
    return playlists


def _create_playlist(yt, name: str, description: str) -> str | None:
    """Crea una nueva playlist pública. Retorna el ID o None."""
    body = {
        "snippet": {"title": name, "description": description},
        "status": {"privacyStatus": "public"},
    }
    response = yt.playlists().insert(part="snippet,status", body=body).execute()
    pid = response.get("id")
    if pid:
        logger.info(f"  Playlist creada: '{name}' → {pid}")
    return pid


def _add_to_playlist(yt, video_id: str, playlist_id: str) -> bool:
    """Añade un video a una playlist. Retorna True si tuvo éxito."""
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    yt.playlistItems().insert(part="snippet", body=body).execute()
    return True


# ─── API pública ──────────────────────────────────────────────────────────────

def add_video_to_playlist(video_id: str, video_title: str) -> bool:
    """Clasifica el video y lo añade a la playlist correspondiente."""
    category = _classify_video(video_title)
    logger.info(f"=== PLAYLIST MANAGER ===")
    logger.info(f"  Video: '{video_title}' → categoría: '{category['name']}'")

    try:
        yt = _get_yt_client()

        existing = _list_playlists(yt)
        logger.info(f"  Playlists existentes: {list(existing.keys())}")

        if category["name"] not in existing:
            logger.info(f"  Creando nueva playlist: '{category['name']}'")
            pid = _create_playlist(yt, category["name"], category["description"])
            if not pid:
                logger.error("  No se pudo crear la playlist")
                return False
            existing[category["name"]] = pid
        else:
            logger.info(f"  Playlist ya existe: '{category['name']}'")

        playlist_id = existing[category["name"]]
        ok = _add_to_playlist(yt, video_id, playlist_id)
        if ok:
            logger.info(f"  Video {video_id} añadido a '{category['name']}'")
        return ok

    except Exception as e:
        logger.error(f"  Playlist manager error: {e}", exc_info=True)
        return False
