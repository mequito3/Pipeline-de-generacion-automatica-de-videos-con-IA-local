"""
pixabay_fetcher.py — Clips de stock video de Pixabay (fuente secundaria)

Pixabay ofrece videos bajo licencia Pixabay Content License:
- Libre para uso comercial
- No requiere atribución
- NO indexada en YouTube Content ID

Se usa como fallback cuando Pexels no tiene clips frescos,
reduciendo el riesgo de "reused content" al diversificar la fuente.

API gratuita: pixabay.com/api/docs/
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

PIXABAY_API_URL = "https://pixabay.com/api/videos/"
CACHE_DIR = config.BASE_DIR / "assets" / "pixabay_cache"
CACHE_FILE = CACHE_DIR / "cache.json"

_mem_cache: dict[str, list[str]] = {}

# Queries equivalentes a las de Pexels pero optimizadas para el índice de Pixabay
_PIXABAY_QUERIES: list[str] = [
    "woman crying emotional", "sad person alone", "dramatic portrait woman",
    "couple arguing", "woman phone shocked", "person thinking alone",
    "sad woman window rain", "emotional face close", "person walking alone night",
    "woman regret alone", "dramatic face tears", "mysterious portrait dark",
    "person leaving dramatic", "lonely woman city", "regret sad emotion",
    "betrayal face shocked", "broken heart drama", "woman crying tears",
    "conflict drama indoor", "emotional breakdown person",
]


def _load_cache() -> dict[str, list[str]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return {k: [p for p in v if Path(p).exists()] for k, v in raw.items()}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _search(query: str, api_key: str, page: int = 1) -> list[dict]:
    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "per_page": 15,
        "page": page,
        "safesearch": "true",
        "order": "popular",
    }
    try:
        resp = requests.get(PIXABAY_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.debug("Pixabay rate limit — esperando 5s")
            time.sleep(5)
        else:
            logger.debug(f"Pixabay HTTP error '{query}': {e}")
        return []
    except Exception as e:
        logger.debug(f"Pixabay error '{query}': {e}")
        return []


def _best_url(hit: dict) -> str:
    videos = hit.get("videos", {})
    for q in ("medium", "large", "small", "tiny"):
        url = videos.get(q, {}).get("url", "")
        if url:
            return url
    return ""


def _download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return dest.exists() and dest.stat().st_size > 50_000
    except Exception as e:
        logger.debug(f"Pixabay download: {e}")
        dest.unlink(missing_ok=True)
        return False


def fetch_clip(query: str, exclude_paths: set[str] | None = None) -> str | None:
    """
    Descarga un clip de Pixabay para la query dada.
    Primero revisa caché local, luego busca en la API.

    Returns:
        Path local al MP4, o None si no hay API key o falla todo.
    """
    api_key = getattr(config, "PIXABAY_API_KEY", "")
    if not api_key:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    global _mem_cache
    if not _mem_cache:
        _mem_cache = _load_cache()

    exclude = exclude_paths or set()

    # Revisar caché primero
    cached = [p for p in _mem_cache.get(query, []) if Path(p).exists() and p not in exclude]
    if cached:
        return random.choice(cached)

    # Buscar en API
    page_num = random.randint(1, 4)
    hits = _search(query, api_key, page=page_num)
    if not hits and page_num > 1:
        hits = _search(query, api_key, page=1)

    random.shuffle(hits)
    for hit in hits:
        url = _best_url(hit)
        if not url:
            continue
        hit_id = hit.get("id", "x")
        safe_q = re.sub(r"[^a-z0-9]+", "_", query.lower())[:20]
        dest = CACHE_DIR / f"{safe_q}_{hit_id}.mp4"

        if dest.exists() and dest.stat().st_size > 50_000:
            path = str(dest)
            if path not in exclude:
                _mem_cache.setdefault(query, [])
                if path not in _mem_cache[query]:
                    _mem_cache[query].append(path)
                _save_cache(_mem_cache)
                return path
            continue

        if _download(url, dest):
            path = str(dest)
            _mem_cache.setdefault(query, [])
            if path not in _mem_cache[query]:
                _mem_cache[query].append(path)
            _save_cache(_mem_cache)
            logger.info(f"Pixabay: descargado '{query}' → {dest.name}")
            return path
        time.sleep(0.25)

    return None


def fetch_clip_generic(exclude_paths: set[str] | None = None) -> str | None:
    """Descarga un clip genérico del pool predefinido (útil como último recurso)."""
    query = random.choice(_PIXABAY_QUERIES)
    return fetch_clip(query, exclude_paths)
