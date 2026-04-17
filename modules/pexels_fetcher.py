"""
pexels_fetcher.py — Descarga clips de stock video de Pexels para cada escena

API gratuita: https://www.pexels.com/api/
Límites: 200 req/hora, 20,000/mes — suficiente para pipeline de Shorts.

Los clips descargados se guardan en assets/pexels_cache/ y se reutilizan
automáticamente en ejecuciones futuras si el keyword coincide.
"""

import json
import logging
import random
import re
import time
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"

# Directorio persistente de caché (survives entre runs)
CACHE_DIR  = config.BASE_DIR / "assets" / "pexels_cache"
CACHE_FILE = CACHE_DIR / "cache.json"

# Caché en memoria para el run actual (evita releer el JSON en cada escena)
_mem_cache: dict[str, str] = {}

# Términos dramáticos por acto (usados cuando el image_prompt está vacío)
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


# ─── Caché persistente ────────────────────────────────────────────────────────

def _load_disk_cache() -> dict[str, str]:
    """Lee el JSON de caché. Elimina entradas cuyo archivo ya no existe."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data: dict[str, str] = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        # Limpiar entradas de archivos borrados
        clean = {k: v for k, v in data.items() if Path(v).exists()}
        if len(clean) < len(data):
            _save_disk_cache(clean)
        return clean
    except Exception:
        return {}


def _save_disk_cache(data: dict[str, str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _cache_lookup(keyword: str) -> str | None:
    """Devuelve el path local si el keyword (o uno similar) está en caché."""
    # Coincidencia exacta
    if keyword in _mem_cache:
        path = _mem_cache[keyword]
        if Path(path).exists():
            return path

    # Coincidencia parcial: busca si alguna palabra del keyword aparece en alguna clave
    kw_words = set(keyword.lower().split())
    best: str | None = None
    best_score = 0
    for cached_kw, cached_path in _mem_cache.items():
        if not Path(cached_path).exists():
            continue
        cached_words = set(cached_kw.lower().split())
        score = len(kw_words & cached_words)
        if score > best_score and score >= 2:
            best_score = score
            best = cached_path
    return best


def _cache_put(keyword: str, path: str) -> None:
    """Guarda en memoria y en disco."""
    _mem_cache[keyword] = path
    disk = _load_disk_cache()
    disk[keyword] = path
    _save_disk_cache(disk)
    logger.debug(f"Caché guardado: '{keyword}' → {Path(path).name}")


# ─── Extracción de keywords ───────────────────────────────────────────────────

def _extract_keywords(image_prompt: str, act: str = "") -> str:
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

    act_upper = (act or "").upper()
    for key, options in _ACT_FALLBACKS.items():
        if key in act_upper:
            return random.choice(options)

    return random.choice(_GENERIC_FALLBACKS)


# ─── Pexels API ───────────────────────────────────────────────────────────────

def _search_pexels(query: str, api_key: str) -> list[dict]:
    headers = {"Authorization": api_key}
    params  = {"query": query, "orientation": "portrait", "size": "medium", "per_page": 15}
    try:
        resp = requests.get(PEXELS_VIDEO_API, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        logger.debug(f"Pexels '{query}': {len(videos)} resultados")
        return videos
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise RuntimeError(
                "PEXELS_API_KEY inválida.\nVerifica tu clave en https://www.pexels.com/api/"
            ) from e
        logger.warning(f"Pexels HTTP error '{query}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Pexels search error '{query}': {e}")
        return []


def _pick_video_url(video: dict) -> str:
    files = video.get("video_files", [])
    for quality in ("hd", "sd"):
        for f in files:
            if f.get("quality") == quality and f.get("link"):
                return f["link"]
    return next((f["link"] for f in files if f.get("link")), "")


def _download_clip(url: str, dest_path: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return dest_path.exists() and dest_path.stat().st_size > 50_000
    except Exception as e:
        logger.warning(f"Error descargando clip: {e}")
        dest_path.unlink(missing_ok=True)
        return False


# ─── Función principal ────────────────────────────────────────────────────────

def fetch_videos(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Descarga 1 clip portrait de Pexels por escena.

    Los clips se guardan en assets/pexels_cache/ y se reutilizan en runs
    futuros cuando el keyword coincide (exacto o parcial con ≥2 palabras).

    Args:
        scenes:     Lista de dicts del script con "image_prompt" y "act"
        output_dir: (ignorado, los clips van a pexels_cache/)

    Returns:
        Lista de paths MP4 locales (misma longitud que scenes).

    Raises:
        RuntimeError: Si PEXELS_API_KEY no está configurada.
    """
    api_key = getattr(config, "PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PEXELS_API_KEY no configurada.\n"
            "1. Regístrate gratis en https://www.pexels.com/api/\n"
            "2. Añade al .env: PEXELS_API_KEY=tu_clave_aqui"
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Cargar caché persistente en memoria al inicio del run
    global _mem_cache
    _mem_cache = _load_disk_cache()
    cached_count = len(_mem_cache)
    if cached_count:
        logger.info(f"Pexels: {cached_count} clips en caché local (assets/pexels_cache/)")

    results: list[str] = []
    last_valid: str | None = None

    for i, scene in enumerate(scenes):
        image_prompt = scene.get("image_prompt", "") or scene.get("text", "")
        act          = scene.get("act", "")
        keywords     = _extract_keywords(image_prompt, act)

        # 1. Buscar en caché (exacto o parcial)
        cached = _cache_lookup(keywords)
        if cached:
            logger.info(f"  Escena {i+1}: caché local '{keywords}' → {Path(cached).name}")
            results.append(cached)
            last_valid = cached
            continue

        # 2. Buscar en Pexels
        logger.info(f"  Escena {i+1}/{len(scenes)}: buscando '{keywords}' en Pexels...")
        videos = _search_pexels(keywords, api_key)

        if not videos and " " in keywords:
            short_q = keywords.split()[0]
            logger.info(f"  Sin resultados, reintentando con '{short_q}'")
            videos = _search_pexels(short_q, api_key)

        if not videos:
            fallback_q = random.choice(_GENERIC_FALLBACKS)
            logger.info(f"  Reintento final con '{fallback_q}'")
            videos = _search_pexels(fallback_q, api_key)

        # 3. Descargar en CACHE_DIR (persistente entre runs)
        downloaded = False
        random.shuffle(videos)
        for video in videos[:6]:
            url    = _pick_video_url(video)
            vid_id = video.get("id", f"v{i}")
            # Nombre basado en keyword para fácil identificación
            safe_kw = re.sub(r"[^a-z0-9]+", "_", keywords.lower())[:30]
            dest    = CACHE_DIR / f"{safe_kw}_{vid_id}.mp4"

            if dest.exists() and dest.stat().st_size > 50_000:
                # Ya descargado en otro run, registrar en caché
                logger.info(f"  Escena {i+1}: archivo ya existe ({dest.name})")
                _cache_put(keywords, str(dest))
                results.append(str(dest))
                last_valid = str(dest)
                downloaded = True
                break

            logger.info(f"    Descargando Pexels ID {vid_id}...")
            if _download_clip(url, dest):
                size_kb = dest.stat().st_size // 1024
                logger.info(f"    OK ({size_kb} KB) → {dest.name}")
                _cache_put(keywords, str(dest))
                results.append(str(dest))
                last_valid = str(dest)
                downloaded = True
                break
            time.sleep(0.5)

        if not downloaded:
            if last_valid:
                logger.warning(f"  Escena {i+1}: fallo, reutilizando clip anterior")
                results.append(last_valid)
            else:
                logger.error(f"  Escena {i+1}: sin clips disponibles")
                results.append("")

        time.sleep(0.3)

    ok = sum(1 for p in results if p)
    logger.info(f"Pexels: {ok}/{len(scenes)} clips listos ({cached_count} ya estaban en caché)")
    return results
