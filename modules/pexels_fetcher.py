"""
pexels_fetcher.py — Clips de stock video de Pexels por escena

Características:
- Caché persistente en assets/pexels_cache/ (no re-descarga entre runs)
- Pool de 15 clips por keyword para máxima variedad
- Anti-repetición por run: nunca repite el mismo clip en el mismo video
- Historial cross-run: evita repetir los últimos 100 clips usados
- Renovación automática de pool agotado
"""

import json
import logging
import random
import re
import time
from collections import deque
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
POOL_SIZE      = 15   # Clips a mantener por keyword
HISTORY_LIMIT  = 100  # Clips recientes a evitar entre runs
CACHE_DIR      = config.BASE_DIR / "assets" / "pexels_cache"
CACHE_FILE     = CACHE_DIR / "cache.json"
HISTORY_FILE   = CACHE_DIR / "history.json"

# Caché en memoria: keyword → [path1, path2, ...]
_mem_cache: dict[str, list[str]] = {}
# Clips usados en el run actual (anti-repetición intra-run)
_run_used: set[str] = set()
# Historial global entre runs (deque de paths, límite HISTORY_LIMIT)
_history: deque[str] = deque(maxlen=HISTORY_LIMIT)


# Queries dramáticas por acto — varias opciones para rotar entre escenas
_ACT_QUERIES: dict[str, list[str]] = {
    "INICIO": [
        "night city lights rain", "woman walking alone dark street", "dark room candle shadow",
        "mysterious silhouette night", "empty corridor dramatic", "door opening dark room",
        "rainy window night city", "lonely road dramatic sky", "dark hallway suspense",
    ],
    "DESCUBRIMIENTO": [
        "shocked woman phone message", "dramatic discovery face close", "reading message surprised",
        "woman staring screen shocked", "hand trembling phone", "open mouth surprise woman",
        "finding secret dramatic", "unexpected news person", "disbelief face portrait close",
    ],
    "CONFRONTACION": [
        "couple arguing dramatic close", "confrontation face intense", "angry woman crying tears",
        "finger pointing accusation", "heated argument indoor", "woman screaming emotional",
        "tense face confrontation", "fighting couple silhouette", "dramatic argument night",
    ],
    "CLIMAX": [
        "woman crying close portrait", "emotional breakdown dramatic", "tears face close intense",
        "sobbing face extreme close", "collapse emotional person", "despair woman alone",
        "face in hands crying", "uncontrollable tears close", "grief dramatic portrait",
    ],
    "CONSECUENCIA": [
        "sad person window rain", "lonely street night walk", "regret person alone sitting",
        "empty apartment sad", "person leaving dramatic", "shadows and regret dark",
        "rain window melancholy", "abandoned place sad light", "solitude night street",
    ],
    "REFLEXION": [
        "thinking woman window light", "sunset alone silhouette field", "contemplation quiet indoor",
        "woman staring distance", "mirror reflection dramatic", "coffee thinking alone",
        "sitting bench park sad", "looking sky dramatic clouds", "self reflection portrait",
    ],
    "FINAL": [
        "walking away alone street", "door closing dramatic slow", "empty room morning light",
        "person leaving forever bags", "hands letting go dramatic", "sunset walking silhouette",
        "closing chapter book metaphor", "end road dramatic", "back turned walking away",
    ],
    "GANCHO": [
        "mystery dramatic face close", "secret whisper dark close", "dark cinematic portrait",
        "eye close dramatic intense", "hidden face shadow dramatic", "suspicious glance sideways",
        "intrigue face close dramatic", "mysterious woman dark portrait", "tense atmospheric face",
    ],
    "CTA": [
        "question mark dramatic", "thinking person dramatic", "dramatic close face intensity",
        "wondering woman dramatic", "suspense cliffhanger face", "shocked expression close",
        "leaning forward curious", "wide eyes surprise dramatic", "compelling gaze camera",
    ],
}

_GENERIC_POOL = [
    "dramatic woman portrait dark",       "emotional scene close indoor",
    "night alone city rain",              "crying woman dramatic portrait",
    "mystery dark cinematic portrait",    "dramatic face emotion intense",
    "sad person window rain melancholy",  "tense dramatic scene interior",
    "close face emotion dramatic",        "silhouette dramatic sunset",
    "woman running night street",         "hands shaking dramatic close",
    "broken heart dramatic",              "secret revealed shocked face",
    "dark room one light dramatic",       "regret alone bedroom night",
    "phone call shocked reaction",        "betrayal dramatic face",
    "tears running face close",           "solitude park bench night",
]


# ─── Historial cross-run ──────────────────────────────────────────────────────

def _load_history() -> deque[str]:
    if not HISTORY_FILE.exists():
        return deque(maxlen=HISTORY_LIMIT)
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return deque(data, maxlen=HISTORY_LIMIT)
    except Exception:
        return deque(maxlen=HISTORY_LIMIT)


def _save_history() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(list(_history), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── Caché persistente ────────────────────────────────────────────────────────

def _load_disk_cache() -> dict[str, list[str]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        raw: dict = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        cleaned: dict[str, list[str]] = {}
        for k, v in raw.items():
            paths = [v] if isinstance(v, str) else list(v)
            valid = [p for p in paths if Path(p).exists()]
            if valid:
                cleaned[k] = valid
        if len(cleaned) < len(raw):
            _save_disk_cache(cleaned)
        return cleaned
    except Exception:
        return {}


def _save_disk_cache(data: dict[str, list[str]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _cache_add(keyword: str, path: str) -> None:
    pool = _mem_cache.get(keyword, [])
    if path not in pool:
        pool.append(path)
    _mem_cache[keyword] = pool
    disk = _load_disk_cache()
    disk_pool = disk.get(keyword, [])
    if path not in disk_pool:
        disk_pool.append(path)
    disk[keyword] = disk_pool
    _save_disk_cache(disk)


def _pick_unused(pool: list[str]) -> str | None:
    """
    Elige un clip del pool priorizando:
    1. No usado en este run Y no en historial reciente
    2. No usado en este run (pero sí en historial)
    3. Cualquier clip válido (último recurso)
    """
    valid = [p for p in pool if Path(p).exists()]
    if not valid:
        return None

    # Prioridad 1: fresco (no en run ni en historial)
    fresh = [p for p in valid if p not in _run_used and p not in _history]
    if fresh:
        return random.choice(fresh)

    # Prioridad 2: no usado en este run
    unused_run = [p for p in valid if p not in _run_used]
    if unused_run:
        return random.choice(unused_run)

    # Último recurso: cualquier clip válido
    return random.choice(valid)


# ─── Extracción de keywords ───────────────────────────────────────────────────

def _queries_for_scene(image_prompt: str, act: str, scene_idx: int) -> list[str]:
    """Genera 3-4 queries para buscar en Pexels con máxima variedad."""
    stopwords = {
        "a","an","the","in","on","at","of","with","and","or","is","are","was",
        "were","to","for","very","that","this","her","his","their","from","into",
        "through","during","she","he","they","it","as","by","be","has","have","had",
        "close","portrait","scene","cinematic","film","shot","view","back","some",
        "then","when","what","which","who","but","not","can","will","just","also",
    }
    words = re.findall(r"[a-zA-Z]+", (image_prompt or "").lower())
    kw = [w for w in words if w not in stopwords and len(w) > 3]

    queries: list[str] = []

    # Query 1: keywords del prompt (primeras 3 palabras clave)
    if len(kw) >= 2:
        queries.append(" ".join(kw[:3]))
    elif len(kw) == 1:
        queries.append(kw[0] + " dramatic")

    # Query 2: segunda combinación de keywords (palabras 2-4 para variedad)
    if len(kw) >= 4:
        queries.append(" ".join(kw[1:4]))

    # Query 3: basada en el acto narrativo (rota por índice para no repetir entre escenas)
    act_upper = (act or "").upper()
    for key, options in _ACT_QUERIES.items():
        if key in act_upper:
            # Offset por escena para que dos escenas del mismo acto usen queries distintas
            offset = (scene_idx * 3) % len(options)
            queries.append(options[offset])
            # Agregar una segunda opción del mismo acto si hay suficientes
            alt_offset = (offset + 1) % len(options)
            if options[alt_offset] not in queries:
                queries.append(options[alt_offset])
            break

    # Query 4: genérica del pool global (rotada por escena)
    generic = _GENERIC_POOL[scene_idx % len(_GENERIC_POOL)]
    if generic not in queries:
        queries.append(generic)

    # Eliminar duplicados manteniendo orden
    seen: set[str] = set()
    return [q for q in queries if not (q in seen or seen.add(q))]  # type: ignore[func-returns-value]


# ─── Pexels API ───────────────────────────────────────────────────────────────

def _search_pexels(query: str, api_key: str, page: int = 1) -> list[dict]:
    headers = {"Authorization": api_key}
    params  = {"query": query, "orientation": "portrait", "size": "medium",
               "per_page": 15, "page": page}
    try:
        resp = requests.get(PEXELS_VIDEO_API, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("videos", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise RuntimeError("PEXELS_API_KEY inválida — verifica en https://www.pexels.com/api/") from e
        logger.warning(f"Pexels HTTP error '{query}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Pexels error '{query}': {e}")
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


def _expand_pool(keyword: str, api_key: str, target: int = POOL_SIZE) -> list[str]:
    """
    Descarga hasta `target` clips únicos para `keyword`.
    Usa páginas aleatorias de Pexels para garantizar variedad real.
    """
    existing = _mem_cache.get(keyword, [])
    needed   = target - len(existing)
    if needed <= 0:
        return existing

    logger.info(f"    Expandiendo pool '{keyword}' ({len(existing)}/{target} clips)...")

    # Estrategia de páginas: siempre mezclar páginas distintas
    base_pages = [1, 2, 3]
    extra_pages = random.sample(range(4, 26), k=min(5, 22))
    pages_to_try = base_pages + extra_pages
    random.shuffle(pages_to_try[1:])  # Siempre empezar por página 1, luego aleatorio

    all_videos: list[dict] = []
    for page in pages_to_try:
        vids = _search_pexels(keyword, api_key, page=page)
        all_videos.extend(vids)
        if len(all_videos) >= target * 5:
            break
        time.sleep(0.15)

    random.shuffle(all_videos)
    downloaded = list(existing)

    for video in all_videos:
        if len(downloaded) >= target:
            break
        url    = _pick_video_url(video)
        vid_id = video.get("id", "x")
        safe_kw = re.sub(r"[^a-z0-9]+", "_", keyword.lower())[:25]
        dest    = CACHE_DIR / f"{safe_kw}_{vid_id}.mp4"

        if dest.exists() and dest.stat().st_size > 50_000:
            path = str(dest)
            if path not in downloaded:
                downloaded.append(path)
                _cache_add(keyword, path)
            continue

        if _download_clip(url, dest):
            path = str(dest)
            downloaded.append(path)
            _cache_add(keyword, path)
            logger.info(f"    + {dest.name} ({dest.stat().st_size // 1024}KB)")
            time.sleep(0.2)

    return downloaded


def _refresh_pool_if_exhausted(keyword: str, api_key: str) -> list[str]:
    """
    Si todos los clips del pool ya están en el historial reciente,
    fuerza descarga de clips nuevos con páginas distintas a las ya usadas.
    """
    pool = _mem_cache.get(keyword, [])
    valid = [p for p in pool if Path(p).exists()]
    fresh = [p for p in valid if p not in _history]

    if not fresh and len(valid) >= POOL_SIZE:
        # Pool totalmente consumido → ampliar con más clips
        logger.info(f"    Pool '{keyword}' agotado en historial, descargando nuevos...")
        return _expand_pool(keyword, api_key, target=len(valid) + 5)

    return valid or _expand_pool(keyword, api_key, target=POOL_SIZE)


# ─── Función principal ────────────────────────────────────────────────────────

def fetch_videos(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Descarga clips portrait de Pexels para cada escena.

    Garantías de variedad:
    - Pool de 15 clips por query (era 3)
    - Historial de 100 clips usados entre runs → evita repetición cross-run
    - 4 queries por escena para mayor cobertura
    - Páginas Pexels aleatorias (1-25) para variedad real en descargas

    Returns:
        Lista de paths MP4 locales, misma longitud que scenes.
    """
    api_key = getattr(config, "PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PEXELS_API_KEY no configurada.\n"
            "1. Regístrate en https://www.pexels.com/api/\n"
            "2. Añade al .env: PEXELS_API_KEY=tu_clave"
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    global _mem_cache, _run_used, _history
    _mem_cache = _load_disk_cache()
    _history   = _load_history()
    _run_used  = set()

    cached_count = sum(len(v) for v in _mem_cache.values())
    logger.info(f"Pexels: {cached_count} clips en caché | {len(_history)} en historial reciente")

    results:    list[str] = []
    last_valid: str | None = None

    for i, scene in enumerate(scenes):
        image_prompt = scene.get("image_prompt", "") or scene.get("text", "")
        act          = scene.get("act", "")
        queries      = _queries_for_scene(image_prompt, act, i)

        picked: str | None = None

        for query in queries:
            pool = _mem_cache.get(query, [])

            # Asegurar pool mínimo
            if len(pool) < POOL_SIZE:
                pool = _expand_pool(query, api_key, target=POOL_SIZE)

            # Renovar si el pool está agotado en historial
            if pool and all(p in _history for p in pool if Path(p).exists()):
                pool = _refresh_pool_if_exhausted(query, api_key)

            picked = _pick_unused(pool)
            if picked:
                logger.info(f"  Escena {i+1} [{act or '-'}]: '{query}' → {Path(picked).name}")
                break

        if not picked:
            # Último recurso: cualquier clip del caché global no usado en este run
            all_cached = [p for paths in _mem_cache.values() for p in paths
                          if Path(p).exists() and p not in _run_used]
            # Priorizar los que tampoco están en historial
            fresh_global = [p for p in all_cached if p not in _history]
            pool_fallback = fresh_global or all_cached

            if pool_fallback:
                picked = random.choice(pool_fallback)
                logger.warning(f"  Escena {i+1}: sin clips nuevos, usando global '{Path(picked).name}'")
            elif last_valid:
                picked = last_valid
                logger.warning(f"  Escena {i+1}: pool agotado, repitiendo '{Path(picked).name}'")
            else:
                logger.error(f"  Escena {i+1}: sin clips disponibles")
                results.append("")
                continue

        _run_used.add(picked)
        _history.append(picked)
        results.append(picked)
        last_valid = picked

    # Persistir historial actualizado
    _save_history()

    used_unique = len({p for p in results if p})
    logger.info(f"Pexels: {len(scenes)} escenas → {used_unique} clips únicos")
    return results
