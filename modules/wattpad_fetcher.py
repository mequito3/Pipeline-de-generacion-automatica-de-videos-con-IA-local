"""
wattpad_fetcher.py — Extrae historias de Wattpad para el canal de Telegram

Busca confesiones y romance adulto en espanol usando la API no oficial de Wattpad.
Retorna el mismo formato que scraper.get_story().
"""

import json
import logging
import random
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import requests
import config

logger = logging.getLogger(__name__)

# Usa el mismo archivo que scraper.py para no repetir historias entre ambos módulos
_USED_FILE = Path(getattr(config, "BASE_DIR", ".")) / "used_posts.json"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

_LANG_ES = 5  # Language ID de Wattpad para Español

_SEARCH_QUERIES = [
    "confesion adulto",
    "secreto prohibido",
    "infidelidad romance",
    "historia adulta drama",
    "romance prohibido mature",
    "confesiones oscuras drama",
    "traicion amor secreto",
    "tentacion prohibida romance",
    "historia morbosa drama",
    "romance adulto secreto",
]

_MIN_WORDS = 250


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str):
        self.chunks.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.chunks)).strip()


def _load_used() -> set:
    try:
        data = json.loads(_USED_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in data.get("wattpad", []))
    except Exception:
        return set()


def _save_used(used: set) -> None:
    try:
        data = {}
        if _USED_FILE.exists():
            data = json.loads(_USED_FILE.read_text(encoding="utf-8"))
        data["wattpad"] = list(used)[-500:]
        _USED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"wattpad: no se pudo guardar used_posts: {e}")


def _search_stories(query: str, limit: int = 20, mature: bool = False) -> list[dict]:
    """Busca historias en Wattpad en español."""
    try:
        params = {
            "query":    query,
            "limit":    limit,
            "offset":   random.randint(0, 60),
            "language": _LANG_ES,
        }
        r = requests.get(
            "https://www.wattpad.com/api/v3/stories",
            headers=_HEADERS,
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        stories = data.get("stories", [])
        if mature:
            # Priorizar maduras pero incluir todas
            stories.sort(key=lambda s: (1 if s.get("mature") else 0), reverse=True)
        return stories
    except Exception as e:
        logger.warning(f"wattpad search '{query}': {e}")
        return []


def _get_part_text(part_id: int | str, min_words: int = _MIN_WORDS) -> str:
    """
    Descarga el texto de una parte de Wattpad.
    Devuelve texto limpio o '' si no alcanza el minimo.
    """
    try:
        r = requests.get(
            f"https://www.wattpad.com/apiv2/storytext?id={part_id}",
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=20,
        )
        if r.status_code != 200:
            return ""
        stripper = _HTMLStripper()
        stripper.feed(r.text)
        text = stripper.get_text()
        if len(text.split()) >= min_words:
            return text
    except Exception as e:
        logger.warning(f"wattpad part text {part_id}: {e}")
    return ""


def _score_story(story: dict) -> float:
    votes  = story.get("voteCount", 0)
    reads  = story.get("readCount", 0)
    mature = 1.5 if story.get("mature") else 1.0
    return (votes * 3 + reads * 0.01) * mature


def get_story(prefer_mature: bool = False) -> dict | None:
    """
    Retorna una historia de Wattpad lista para publicar.

    Formato de salida:
      {
        "titulo":   str,
        "historia": str,
        "fuente":   "wattpad",
        "url":      str,
        "mature":   bool,
      }
    """
    used = _load_used()

    queries = random.sample(_SEARCH_QUERIES, min(4, len(_SEARCH_QUERIES)))

    candidates: list[dict] = []
    for q in queries:
        results = _search_stories(q, limit=20, mature=prefer_mature)
        candidates.extend(results)
        time.sleep(0.4)

    if not candidates:
        logger.warning("wattpad: sin resultados")
        return None

    # Deduplicar por id
    seen: set = set()
    unique: list[dict] = []
    for s in candidates:
        sid = str(s.get("id", ""))
        if sid and sid not in seen:
            seen.add(sid)
            unique.append(s)
    candidates = unique

    # Filtrar ya usadas y sin partes
    candidates = [
        s for s in candidates
        if str(s.get("id")) not in used and s.get("numParts", 0) > 0 and s.get("parts")
    ]

    if not candidates:
        logger.warning("wattpad: todos los candidatos ya fueron usados")
        return None

    candidates.sort(key=_score_story, reverse=True)

    for story in candidates[:15]:
        story_id = str(story.get("id", ""))
        titulo   = story.get("title", "").strip()
        parts    = story.get("parts", [])

        if not titulo or not parts:
            continue

        # Intentar las primeras 3 partes hasta encontrar una con suficiente texto
        texto = ""
        for part in parts[:3]:
            part_id = part.get("id")
            if not part_id:
                continue
            t = _get_part_text(part_id)
            if t:
                texto = t
                break
            time.sleep(0.3)

        if not texto:
            logger.info(f"wattpad: '{titulo}' sin texto suficiente — skip")
            continue

        words = len(texto.split())
        used.add(story_id)
        _save_used(used)

        logger.info(
            f"wattpad: '{titulo}' ({words} palabras, mature={story.get('mature')}, "
            f"votes={story.get('voteCount')}, reads={story.get('readCount')})"
        )
        return {
            "titulo":   titulo,
            "historia": texto[:3500],
            "fuente":   "wattpad",
            "url":      f"https://www.wattpad.com/story/{story_id}",
            "mature":   bool(story.get("mature")),
        }

    logger.warning("wattpad: no se encontro historia valida")
    return None
