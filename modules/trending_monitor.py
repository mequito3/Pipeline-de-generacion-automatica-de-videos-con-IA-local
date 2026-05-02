"""
trending_monitor.py — Detecta temas trending en tiempo real para priorizar contenido

Fuentes:
  1. Google Trends (pytrends) — tendencias en MX, CO, AR, US-español
  2. YouTube trending Shorts en español — scraping de títulos
  3. Reddit hot posts — ya se usa en scraper.py, aquí lo ampliamos

Uso: run_trend_analysis() → lista de temas ordenados por viralidad
     Inyectados como topics prioritarios en el próximo video
"""

import asyncio
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

TRENDS_CACHE_FILE = Path(__file__).parent.parent / "trends_cache.json"
CACHE_TTL_HOURS   = 6  # no consultar Google Trends más de 1 vez cada 6h

# Términos semilla relacionados con el nicho — Google Trends los usa para correlacionar
_SEED_TERMS = [
    "infidelidad", "traición pareja", "secretos familiares",
    "relaciones tóxicas", "confesiones reales", "historia real drama",
]

# Países de mayor audiencia latina
_GEO_TARGETS = ["MX", "CO", "AR", "US"]

# Palabras clave de alto engagement que deben estar en el tema
_HIGH_ENGAGEMENT_WORDS = {
    "traición", "engaño", "secreto", "mentira", "descubrí", "viral",
    "impactante", "sorprendente", "increíble", "nunca", "jamás", "terrible",
    "oscuro", "oculto", "familiar", "pareja", "amigo", "amiga",
}


def _load_cache() -> dict:
    if TRENDS_CACHE_FILE.exists():
        try:
            return json.loads(TRENDS_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        TRENDS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cache_is_fresh(cache: dict) -> bool:
    ts = cache.get("timestamp", 0)
    return (time.time() - ts) < CACHE_TTL_HOURS * 3600


def _score_topic(topic: str) -> float:
    """Puntúa un tema según relevancia para el nicho."""
    t_lower = topic.lower()
    score = 0.0
    score += sum(2 for w in _HIGH_ENGAGEMENT_WORDS if w in t_lower)
    score += 3 if "?" in topic else 0
    score += 2 if any(c.isdigit() for c in topic) else 0
    score += 1 if len(topic) > 20 else 0
    return score


# ─── Fuente 1: Google Trends (pytrends) ──────────────────────────────────────

def _fetch_google_trends() -> list[str]:
    """Consulta Google Trends para los términos semilla en países latinos."""
    topics: list[str] = []
    try:
        from pytrends.request import TrendReq  # type: ignore
        pytrends = TrendReq(hl="es-MX", tz=360, timeout=(10, 25))

        for geo in random.sample(_GEO_TARGETS, min(2, len(_GEO_TARGETS))):
            try:
                # Tendencias del día en el país
                daily = pytrends.trending_searches(pn=geo.lower() if geo != "US" else "united_states")
                if daily is not None and not daily.empty:
                    for term in daily[0].head(15).tolist():
                        if isinstance(term, str) and len(term) > 5:
                            topics.append(term)

                # Relacionados con términos semilla
                seed = random.choice(_SEED_TERMS)
                pytrends.build_payload([seed], cat=0, timeframe="now 7-d", geo=geo)
                related = pytrends.related_queries()
                if related and seed in related:
                    rising = related[seed].get("rising")
                    if rising is not None and not rising.empty:
                        for q in rising["query"].head(10).tolist():
                            if isinstance(q, str):
                                topics.append(q)

                time.sleep(random.uniform(1.5, 3.5))  # rate limit Google
            except Exception as e:
                logger.debug(f"  Google Trends {geo}: {e}")
                continue

        logger.info(f"  Google Trends: {len(topics)} temas obtenidos")
    except ImportError:
        logger.info("  pytrends no instalado — saltando Google Trends")
    except Exception as e:
        logger.warning(f"  Google Trends error: {e}")
    return topics


# ─── Fuente 2: YouTube trending Shorts en español ────────────────────────────

async def _fetch_youtube_trending_async() -> list[str]:
    """Scraping de títulos de Shorts trending en español."""
    topics: list[str] = []
    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "es-MX,es;q=0.9",
        }
        # YouTube trending Shorts en español latino (región MX)
        urls = [
            "https://www.youtube.com/shorts",
            "https://www.youtube.com/feed/trending?bp=4gINGgt5dGRfY2hhcnRz",  # trending música
        ]
        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    # Extraer títulos de videos del HTML
                    matches = re.findall(r'"title":\s*\{"runs":\s*\[\{"text":\s*"([^"]{15,90})"', resp.text)
                    for m in matches[:20]:
                        # Solo temas relacionados con drama/confesiones
                        if any(w in m.lower() for w in ["historia", "traición", "secreto", "confesión",
                                                          "drama", "real", "pareja", "familia"]):
                            topics.append(m)
                    await asyncio.sleep(random.uniform(1.0, 2.5))
                except Exception:
                    continue
        logger.info(f"  YouTube trending: {len(topics)} temas")
    except Exception as e:
        logger.debug(f"  _fetch_youtube_trending: {e}")
    return topics


# ─── Fuente 3: Reddit hot posts ───────────────────────────────────────────────

def _fetch_reddit_hot_topics() -> list[str]:
    """Extrae títulos de posts hot de subreddits del nicho."""
    topics: list[str] = []
    subreddits = ["confessions", "TrueOffMyChest", "AITAH", "relationship_advice", "survivinginfidelity"]
    try:
        import httpx
        headers = {"User-Agent": "shorts-factory-trend-bot/1.0"}
        with httpx.Client(headers=headers, timeout=10.0, follow_redirects=True) as client:
            for sub in random.sample(subreddits, 3):
                try:
                    resp = client.get(f"https://www.reddit.com/r/{sub}/hot.json?limit=10")
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    for post in data.get("data", {}).get("children", []):
                        title = post.get("data", {}).get("title", "")
                        score = post.get("data", {}).get("score", 0)
                        if score > 500 and len(title) > 20:
                            topics.append(title)
                    time.sleep(random.uniform(0.8, 2.0))
                except Exception:
                    continue
        logger.info(f"  Reddit hot: {len(topics)} temas")
    except Exception as e:
        logger.debug(f"  _fetch_reddit_hot_topics: {e}")
    return topics


# ─── Combinador y ranker ──────────────────────────────────────────────────────

def _translate_to_spanish_topic(raw: str) -> str:
    """
    Convierte un título de Reddit (inglés) en un tema de confesión en español.
    Simple heurística — el LLM lo refinará después.
    """
    mappings = {
        "cheating": "infidelidad descubierta",
        "affair": "relación secreta",
        "divorce": "divorcio inesperado",
        "toxic": "relación tóxica",
        "abuse": "abuso emocional",
        "family secret": "secreto familiar oscuro",
        "betrayal": "traición devastadora",
        "narcissist": "pareja narcisista",
        "manipulation": "manipulación psicológica",
        "mother-in-law": "suegra tóxica",
        "sister": "hermana que traicionó",
        "brother": "hermano que desapareció",
        "revenge": "venganza planificada",
        "ghosted": "desapareció sin explicación",
        "lied": "mentiras durante años",
        "hidden": "vida secreta descubierta",
    }
    raw_lower = raw.lower()
    for en, es in mappings.items():
        if en in raw_lower:
            return es
    return raw  # si ya está en español o no hay mapeo


def run_trend_analysis() -> list[str]:
    """
    Analiza tendencias de todas las fuentes y devuelve una lista de temas
    ordenados por relevancia y viralidad para el canal.

    Retorna lista de strings para usar como `topic` en generate_script().
    """
    cache = _load_cache()
    if _cache_is_fresh(cache):
        cached_topics = cache.get("topics", [])
        if cached_topics:
            logger.info(f"Trends (caché): {len(cached_topics)} temas disponibles")
            return cached_topics

    logger.info("=== TRENDING MONITOR — analizando fuentes ===")
    all_topics: list[str] = []

    # Fuente 1: Google Trends
    all_topics.extend(_fetch_google_trends())

    # Fuente 2: YouTube trending (async)
    try:
        yt_topics = asyncio.run(_fetch_youtube_trending_async())
        all_topics.extend(yt_topics)
    except Exception:
        pass

    # Fuente 3: Reddit
    all_topics.extend(_fetch_reddit_hot_topics())

    if not all_topics:
        logger.warning("  Sin temas trending — usando config.TOPICS como fallback")
        return list(config.TOPICS)

    # Traducir títulos en inglés
    all_topics = [_translate_to_spanish_topic(t) for t in all_topics]

    # Deduplicar y rankear
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_topics:
        key = t.lower()[:40]
        if key not in seen:
            seen.add(key)
            unique.append(t)

    ranked = sorted(unique, key=_score_topic, reverse=True)

    # Mezclar top 10 con algunos aleatorios para evitar repetición
    top = ranked[:10]
    rest = ranked[10:]
    random.shuffle(rest)
    final = top + rest[:10]

    logger.info(f"  Top temas: {[t[:45] for t in final[:5]]}")

    # Guardar caché
    _save_cache({"timestamp": time.time(), "topics": final, "date": datetime.now().isoformat()})

    return final


def get_trending_topic() -> str:
    """
    Retorna UN tema trending para usar en el próximo video.
    Rota entre los disponibles para no repetir el mismo.
    """
    topics = run_trend_analysis()
    cache = _load_cache()

    used_idx = cache.get("used_topic_idx", -1)
    next_idx = (used_idx + 1) % max(len(topics), 1)

    cache["used_topic_idx"] = next_idx
    _save_cache(cache)

    topic = topics[next_idx] if topics else random.choice(config.TOPICS)
    logger.info(f"  Tema seleccionado: '{topic}'")
    return topic

