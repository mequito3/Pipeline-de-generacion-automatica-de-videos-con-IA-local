"""
pexels_fetcher.py — Descarga clips de stock video de Pexels para cada escena

API gratuita: https://www.pexels.com/api/
Límites: 200 req/hora, 20,000/mes — suficiente para pipeline de Shorts.

Uso:
    clips = pexels_fetcher.fetch_videos(script["scenes"], str(images_dir))
    # clips es una lista de paths MP4, igual que image_generator.generate_images()
"""

import logging
import random
import re
import time
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"

# Caché de sesión: keyword → path local  (evita re-descargar el mismo clip)
_session_cache: dict[str, str] = {}

# Términos de búsqueda dramáticos en inglés por acto narrativo
_ACT_FALLBACKS: dict[str, list[str]] = {
    "INICIO":         ["night city street", "people walking alone", "dark room"],
    "DESCUBRIMIENTO": ["shocked woman", "dramatic discovery", "reading phone"],
    "CONFRONTACION":  ["argument couple", "dramatic confrontation", "anger"],
    "CLIMAX":         ["crying woman", "emotional moment", "dramatic face"],
    "CONSECUENCIA":   ["sad person window", "rain city", "lonely person"],
    "REFLEXION":      ["person thinking", "sunset alone", "contemplation"],
    "FINAL":          ["walking away", "door closing", "empty room"],
}

_GENERIC_FALLBACKS = [
    "dramatic scene", "emotional person", "night rain", "city lights",
    "lonely person", "dramatic moment", "dark atmosphere",
]


def _extract_keywords(image_prompt: str, act: str = "") -> str:
    """
    Extrae 2-3 palabras clave de un image_prompt en inglés para buscar en Pexels.
    Ejemplo: "a woman crying alone in a dark room" → "woman crying dark room"
    """
    stopwords = {
        "a", "an", "the", "in", "on", "at", "of", "with", "and", "or",
        "is", "are", "was", "were", "to", "for", "very", "that", "this",
        "her", "his", "their", "from", "into", "through", "during", "she",
        "he", "they", "it", "as", "by", "be", "has", "have", "had",
    }
    words = re.findall(r"[a-zA-Z]+", (image_prompt or "").lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 3][:4]
    if keywords:
        return " ".join(keywords[:3])

    # Si el prompt está vacío, usar fallback por acto
    act_upper = (act or "").upper()
    for key, options in _ACT_FALLBACKS.items():
        if key in act_upper:
            return random.choice(options)

    return random.choice(_GENERIC_FALLBACKS)


def _search_pexels(query: str, api_key: str) -> list[dict]:
    """Busca videos en Pexels con orientación portrait."""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "portrait",
        "size": "medium",
        "per_page": 15,
    }
    try:
        resp = requests.get(PEXELS_VIDEO_API, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        videos = data.get("videos", [])
        logger.debug(f"Pexels '{query}': {len(videos)} resultados")
        return videos
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise RuntimeError(
                "PEXELS_API_KEY inválida o sin permisos.\n"
                "Verifica tu clave en https://www.pexels.com/api/"
            ) from e
        logger.warning(f"Pexels HTTP error para '{query}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Pexels search error para '{query}': {e}")
        return []


def _pick_video_url(video: dict) -> str:
    """Elige la URL del archivo de mejor calidad disponible (HD portrait → SD → cualquiera)."""
    files = video.get("video_files", [])
    # Preferir HD, luego SD
    for quality in ("hd", "sd"):
        for f in files:
            if f.get("quality") == quality and f.get("link"):
                return f["link"]
    # Fallback: primer archivo disponible
    for f in files:
        if f.get("link"):
            return f["link"]
    return ""


def _download_clip(url: str, dest_path: Path) -> bool:
    """Descarga un clip MP4 en streaming. Devuelve True si el archivo es válido."""
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return dest_path.exists() and dest_path.stat().st_size > 50_000
    except Exception as e:
        logger.warning(f"Error descargando {url[:60]}...: {e}")
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        return False


def fetch_videos(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Descarga 1 clip de Pexels por escena con orientación portrait.

    Args:
        scenes:     Lista de dicts del script con claves "image_prompt" y "act"
        output_dir: Directorio donde guardar los clips descargados

    Returns:
        Lista de paths MP4 locales (misma longitud que scenes).
        Escenas fallidas reutilizan el último clip exitoso.

    Raises:
        RuntimeError: Si PEXELS_API_KEY no está configurada.
    """
    api_key = getattr(config, "PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PEXELS_API_KEY no configurada.\n"
            "1. Regístrate gratis en https://www.pexels.com/api/\n"
            "2. Añade al .env: PEXELS_API_KEY=tu_clave_aqui\n"
            "3. O desactiva Pexels: USE_PEXELS=false"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[str] = []
    last_valid: str | None = None

    for i, scene in enumerate(scenes):
        image_prompt = scene.get("image_prompt", "") or scene.get("text", "")
        act = scene.get("act", "")
        keywords = _extract_keywords(image_prompt, act)

        # Caché de sesión: si ya descargamos este keyword, reusar
        if keywords in _session_cache and Path(_session_cache[keywords]).exists():
            logger.info(f"  Escena {i+1}: caché '{keywords}'")
            results.append(_session_cache[keywords])
            last_valid = _session_cache[keywords]
            continue

        logger.info(f"  Escena {i+1}/{len(scenes)}: buscando '{keywords}' en Pexels...")
        videos = _search_pexels(keywords, api_key)

        # Reintento con query más corta si no hay resultados
        if not videos and " " in keywords:
            short_q = keywords.split()[0]
            logger.info(f"  Sin resultados, reintentando con '{short_q}'")
            videos = _search_pexels(short_q, api_key)

        # Último recurso: genérico dramático
        if not videos:
            fallback_q = random.choice(_GENERIC_FALLBACKS)
            logger.info(f"  Reintento final con '{fallback_q}'")
            videos = _search_pexels(fallback_q, api_key)

        downloaded = False
        random.shuffle(videos)
        for video in videos[:6]:
            url = _pick_video_url(video)
            if not url:
                continue
            vid_id = video.get("id", f"v{i}")
            dest = out_dir / f"pexels_{i+1:03d}_{vid_id}.mp4"
            logger.info(f"    Descargando Pexels ID {vid_id}...")
            if _download_clip(url, dest):
                size_kb = dest.stat().st_size // 1024
                logger.info(f"    OK ({size_kb} KB) → {dest.name}")
                _session_cache[keywords] = str(dest)
                results.append(str(dest))
                last_valid = str(dest)
                downloaded = True
                break
            time.sleep(0.5)

        if not downloaded:
            if last_valid:
                logger.warning(f"  Escena {i+1}: fallo, reutilizando '{Path(last_valid).name}'")
                results.append(last_valid)
            else:
                logger.error(f"  Escena {i+1}: sin clips disponibles")
                results.append("")

        time.sleep(0.3)  # Rate limiting básico (~3 req/s máximo)

    ok = sum(1 for p in results if p)
    logger.info(f"Pexels: {ok}/{len(scenes)} clips descargados")
    return results
