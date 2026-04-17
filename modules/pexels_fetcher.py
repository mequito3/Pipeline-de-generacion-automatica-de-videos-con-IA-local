"""
pexels_fetcher.py — Clips de stock video de Pexels por escena

Características:
- Caché persistente en assets/pexels_cache/ (no re-descarga entre runs)
- Pool de 3 clips por keyword: cada escena recibe un clip diferente
- Anti-repetición por run: nunca repite el mismo clip en el mismo video
  (solo repite como último recurso cuando se agota todo el pool)
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
POOL_SIZE = 3          # Clips a descargar por keyword
CACHE_DIR  = config.BASE_DIR / "assets" / "pexels_cache"
CACHE_FILE = CACHE_DIR / "cache.json"

# Caché en memoria: keyword → [path1, path2, path3]
_mem_cache: dict[str, list[str]] = {}
# Clips usados en el run actual (para anti-repetición)
_run_used: set[str] = set()

# Queries dramáticas por acto — variadas para dar clips distintos
_ACT_QUERIES: dict[str, list[str]] = {
    "INICIO":         ["night city lights", "woman walking alone street", "dark room candle"],
    "DESCUBRIMIENTO": ["shocked woman phone", "dramatic discovery face", "reading message surprised"],
    "CONFRONTACION":  ["couple arguing dramatic", "confrontation face close", "angry woman crying"],
    "CLIMAX":         ["woman crying close portrait", "emotional breakdown dramatic", "tears face close"],
    "CONSECUENCIA":   ["sad person window rain", "lonely street night", "regret person alone"],
    "REFLEXION":      ["thinking woman window", "sunset alone silhouette", "contemplation quiet"],
    "FINAL":          ["walking away alone", "door closing dramatic", "empty room light"],
    "GANCHO":         ["mystery dramatic face", "secret whisper close", "dark cinematic portrait"],
    "CTA":            ["question mark dramatic", "thinking person", "dramatic close face"],
}

_GENERIC_POOL = [
    "dramatic woman portrait", "emotional scene close", "night alone city",
    "crying woman dramatic", "mystery dark portrait", "dramatic face emotion",
    "sad person window", "tense dramatic scene", "close face emotion",
]


# ─── Caché persistente (lista de paths por keyword) ──────────────────────────

def _load_disk_cache() -> dict[str, list[str]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        raw: dict = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        cleaned: dict[str, list[str]] = {}
        for k, v in raw.items():
            # Soporta formato antiguo (str) y nuevo (list)
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
    """Elige un clip del pool que NO se haya usado en este run. Si todos usados, elige al azar."""
    unused = [p for p in pool if p not in _run_used and Path(p).exists()]
    if unused:
        return random.choice(unused)
    # Todos usados → elegir el que menos veces se repite (devuelve None solo si pool vacío)
    valid = [p for p in pool if Path(p).exists()]
    return random.choice(valid) if valid else None


# ─── Extracción de keywords ───────────────────────────────────────────────────

def _queries_for_scene(image_prompt: str, act: str, scene_idx: int) -> list[str]:
    """
    Genera 2-3 queries para buscar en Pexels, asegurando variedad entre escenas.
    Combina keywords del prompt con variantes del acto.
    """
    stopwords = {
        "a","an","the","in","on","at","of","with","and","or","is","are","was",
        "were","to","for","very","that","this","her","his","their","from","into",
        "through","during","she","he","they","it","as","by","be","has","have","had",
        "close","portrait","scene","cinematic","film","shot","view","back",
    }
    words = re.findall(r"[a-zA-Z]+", (image_prompt or "").lower())
    kw = [w for w in words if w not in stopwords and len(w) > 3]

    queries = []

    # Query 1: keywords del prompt (máx 3 palabras)
    if len(kw) >= 2:
        queries.append(" ".join(kw[:3]))
    elif len(kw) == 1:
        queries.append(kw[0])

    # Query 2: basada en el acto narrativo (varía por índice de escena)
    act_upper = (act or "").upper()
    for key, options in _ACT_QUERIES.items():
        if key in act_upper:
            # Usar índice para rotar las opciones (distintas entre escenas del mismo acto)
            queries.append(options[scene_idx % len(options)])
            break

    # Query 3: genérica del pool global (rotada por escena)
    queries.append(_GENERIC_POOL[scene_idx % len(_GENERIC_POOL)])

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
    Descarga hasta `target` clips únicos para `keyword` y los añade al caché.
    Usa páginas distintas de Pexels para máxima variedad.
    """
    existing = _mem_cache.get(keyword, [])
    needed   = target - len(existing)
    if needed <= 0:
        return existing

    logger.info(f"    Expandiendo pool '{keyword}' ({len(existing)}/{target} clips)...")

    all_videos: list[dict] = []
    for page in [1, 2, random.randint(3, 8)]:
        vids = _search_pexels(keyword, api_key, page=page)
        all_videos.extend(vids)
        if len(all_videos) >= target * 4:
            break
        time.sleep(0.2)

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
            time.sleep(0.3)

    return downloaded


# ─── Función principal ────────────────────────────────────────────────────────

def fetch_videos(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Descarga clips portrait de Pexels para cada escena.

    Garantías de variedad:
    - Cada escena usa una query diferente (prompt + acto + genérica)
    - Pool de 3 clips por query: nunca repite el mismo clip en el mismo video
    - Solo repite como último recurso (pool agotado)

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

    global _mem_cache, _run_used
    _mem_cache = _load_disk_cache()
    _run_used  = set()   # reset al inicio de cada run

    cached_count = sum(len(v) for v in _mem_cache.values())
    if cached_count:
        logger.info(f"Pexels: {cached_count} clips en caché local")

    results:   list[str] = []
    last_valid: str | None = None

    for i, scene in enumerate(scenes):
        image_prompt = scene.get("image_prompt", "") or scene.get("text", "")
        act          = scene.get("act", "")
        queries      = _queries_for_scene(image_prompt, act, i)

        picked: str | None = None

        for query in queries:
            # Aseguramos pool mínimo en caché
            pool = _mem_cache.get(query, [])
            if len(pool) < POOL_SIZE:
                pool = _expand_pool(query, api_key, target=POOL_SIZE)

            picked = _pick_unused(pool)
            if picked:
                logger.info(f"  Escena {i+1} [{act or '-'}]: '{query}' → {Path(picked).name}")
                break

        if not picked:
            # Último recurso: cualquier clip del caché global no usado en este run
            all_cached = [p for paths in _mem_cache.values() for p in paths
                          if Path(p).exists() and p not in _run_used]
            if all_cached:
                picked = random.choice(all_cached)
                logger.warning(f"  Escena {i+1}: sin clips nuevos, usando global '{Path(picked).name}'")
            elif last_valid:
                picked = last_valid
                logger.warning(f"  Escena {i+1}: pool agotado, repitiendo '{Path(picked).name}'")
            else:
                logger.error(f"  Escena {i+1}: sin clips disponibles")
                results.append("")
                continue

        _run_used.add(picked)
        results.append(picked)
        last_valid = picked

    used_unique = len({p for p in results if p})
    logger.info(f"Pexels: {len(scenes)} escenas → {used_unique} clips únicos")
    return results
