"""
scraper.py — Extrae historias reales de Reddit para el pipeline de confesiones

Fuentes (Reddit JSON API, sin autenticacion):
  r/confessions, r/TrueOffMyChest, r/relationship_advice,
  r/tifu, r/offmychest, r/survivinginfidelity, r/AITAH

Flujo:
  1. Fetch posts del subreddit via JSON API publica
  2. Filtrar por upvotes, longitud y contenido
  3. Evitar repetir posts ya usados (tracked en used_posts.json)
  4. Retornar historia lista para script_generator
"""

import json
import logging
import random
import time
from pathlib import Path

import requests
import config

logger = logging.getLogger(__name__)

# Headers para la Reddit JSON API (requiere User-Agent valido)
_REDDIT_HEADERS = {
    "User-Agent": "ConfessionsShortsFactory/2.0 (automated narration bot)",
    "Accept": "application/json",
}

# Palabras clave que descartan automaticamente el post
_BLOCKED_KEYWORDS = [
    "suicide", "suicid", "self-harm", "kill myself", "end my life",
    "child abuse", "underage", "minor", "pedophil",
    "rape", "sexual assault",
    "terrorism", "bomb", "shooting",
]


# ─── Gestion de posts ya usados ───────────────────────────────────────────────

def _load_used_ids() -> set:
    """Carga los IDs de posts ya usados desde disco."""
    if config.USED_POSTS_FILE.exists():
        try:
            data = json.loads(config.USED_POSTS_FILE.read_text(encoding="utf-8"))
            return set(data.get("used_ids", []))
        except Exception:
            pass
    return set()


def _mark_as_used(post_id: str) -> None:
    """Marca un post como usado para no repetirlo."""
    used = _load_used_ids()
    used.add(post_id)
    config.USED_POSTS_FILE.write_text(
        json.dumps({"used_ids": list(used)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── Filtros de contenido ─────────────────────────────────────────────────────

def _is_clean(text: str) -> bool:
    """Retorna False si el texto contiene contenido que no podemos publicar."""
    text_lower = text.lower()
    return not any(kw in text_lower for kw in _BLOCKED_KEYWORDS)


def _clean_text(text: str) -> str:
    """Limpia el texto del post: elimina caracteres raros, saltos excesivos."""
    # Colapsar multiples lineas vacias en una sola
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Eliminar markdown de Reddit (**bold**, *italic*, etc.)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}(.*?)_{1,2}", r"\1", text)
    # Eliminar URLs
    text = re.sub(r"https?://\S+", "", text)
    return text.strip()


# ─── Fetch de Reddit ──────────────────────────────────────────────────────────

def _fetch_subreddit(subreddit: str) -> list[dict]:
    """
    Descarga los posts de un subreddit via JSON API publica.
    Retorna lista de dicts con los datos del post.
    """
    sort = config.REDDIT_SORT
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit=100"
    if sort == "top":
        url += f"&t={config.REDDIT_TIME_FILTER}"

    try:
        resp = requests.get(
            url,
            headers=_REDDIT_HEADERS,
            timeout=config.SCRAPER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        posts = [child["data"] for child in data["data"]["children"]]
        logger.info(f"r/{subreddit}: {len(posts)} posts descargados")
        return posts
    except requests.exceptions.ConnectionError:
        logger.warning(f"r/{subreddit}: sin conexion a internet")
        return []
    except Exception as e:
        logger.warning(f"r/{subreddit}: error al descargar — {e}")
        return []


_DRAMA_KEYWORDS = {
    "traición", "traicion", "betrayal", "cheating", "cheated",
    "descubrí", "descubri", "discovered", "found out",
    "nunca", "jamás", "jamas", "never",
    "secreto", "secret",
    "llorando", "lloré", "llore", "crying", "cried",
    "destrozado", "destrozada", "broken", "devastated",
    "mentira", "lie", "lied", "liar",
    "engañó", "engano", "engaño", "cheated",
    "abandonó", "abandono", "left me", "walked out",
    "confesión", "confesion", "confession",
    "destruyó", "destruyo", "destroyed",
    "heartbreak", "heartbroken", "devastated",
    "horrified", "shocked", "shattered",
}


def _score_post(post: dict) -> float:
    """
    Puntúa un post combinando:
    - Engagement base: upvotes + comentarios (señal de interés)
    - Dramatismo: bonus por palabras clave emocionales en título/texto
    - Longitud óptima: bonus por historias completas (800-4000 chars)
    """
    upvotes  = post.get("score", 0)
    comments = post.get("num_comments", 0)
    titulo   = (post.get("title", "") or "").lower()
    texto    = (post.get("selftext", "") or "").lower()
    contenido = titulo + " " + texto[:500]  # revisar solo el inicio del texto

    # Score base de engagement
    score = float(upvotes + comments * 3)

    # Bonus dramatismo: +30 por cada palabra clave dramática encontrada
    for kw in _DRAMA_KEYWORDS:
        if kw in contenido:
            score += 30

    # Bonus por longitud óptima (800-4000 chars = historia completa pero manejable)
    n = len(texto)
    if 800 <= n <= 4000:
        score += 50
    elif 4000 < n <= 6000:
        score += 20

    return score


# ─── Fuentes de historias ────────────────────────────────────────────────────

def _fetch_grouphug() -> list[dict]:
    """
    Obtiene confesiones de grouphug.us — sitio público de confesiones desde 2003.
    Retorna lista de dicts en el mismo formato que los posts de Reddit.
    """
    import re as _re
    results = []
    # Intentar varias páginas de confesiones
    for page in range(1, 4):
        url = f"https://grouphug.us/confessions?page={page}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            }
            resp = requests.get(url, headers=headers, timeout=config.SCRAPER_TIMEOUT)
            if resp.status_code != 200:
                break
            # Extraer confesiones del HTML (divs con clase confession o similar)
            text_blocks = _re.findall(r'<p[^>]*class="[^"]*confession[^"]*"[^>]*>(.*?)</p>', resp.text, _re.DOTALL)
            if not text_blocks:
                # Intentar otro patrón
                text_blocks = _re.findall(r'<div[^>]*class="[^"]*post-text[^"]*"[^>]*>(.*?)</div>', resp.text, _re.DOTALL)

            for i, block in enumerate(text_blocks[:10]):
                # Limpiar HTML
                clean = _re.sub(r'<[^>]+>', '', block).strip()
                clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'").replace('&quot;', '"')
                if len(clean) < config.STORY_MIN_CHARS:
                    continue
                results.append({
                    "id": f"grouphug_{page}_{i}",
                    "title": clean[:80] + "...",
                    "selftext": clean,
                    "score": 200,  # score base para ordenamiento
                    "num_comments": 0,
                    "is_self": True,
                    "_source": "grouphug.us",
                })
        except Exception as e:
            logger.debug(f"grouphug.us página {page}: {e}")
            break

    if results:
        logger.info(f"grouphug.us: {len(results)} confesiones obtenidas")
    return results


def _fetch_postsecret_blog() -> list[dict]:
    """
    Obtiene los textos de PostSecret (blog público de Frank Warren).
    Los posts son breves pero muy emocionales — buenos para historias cortas.
    """
    import re as _re
    results = []
    url = "https://postsecret.com/"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=config.SCRAPER_TIMEOUT)
        if resp.status_code == 200:
            # Extraer fragmentos de texto de los posts (alt text de imágenes + captions)
            alts = _re.findall(r'<img[^>]*alt="([^"]{50,})"', resp.text)
            captions = _re.findall(r'<figcaption[^>]*>(.*?)</figcaption>', resp.text, _re.DOTALL)
            texts = alts + [_re.sub(r'<[^>]+>', '', c).strip() for c in captions]
            for i, t in enumerate(texts):
                if len(t) >= config.STORY_MIN_CHARS:
                    results.append({
                        "id": f"postsecret_{i}",
                        "title": t[:80],
                        "selftext": t,
                        "score": 150,
                        "num_comments": 0,
                        "is_self": True,
                        "_source": "postsecret.com",
                    })
    except Exception as e:
        logger.debug(f"postsecret.com: {e}")
    return results


def _try_post(post: dict, used_ids: set) -> dict | None:
    """Aplica filtros y devuelve story dict si el post es válido, None si no."""
    post_id = post.get("id", "")
    titulo  = post.get("title", "").strip()
    texto   = post.get("selftext", "").strip()
    upvotes = post.get("score", 0)
    is_self = post.get("is_self", False)
    source  = post.get("_source", "reddit")

    if post_id in used_ids:
        return None
    if source == "reddit" and not is_self:
        return None
    if texto in ("[removed]", "[deleted]", ""):
        return None
    if source == "reddit" and upvotes < config.REDDIT_MIN_UPVOTES:
        return None
    if len(texto) < config.STORY_MIN_CHARS:
        return None
    if not _is_clean(titulo + " " + texto):
        return None

    if len(texto) > config.STORY_MAX_CHARS:
        texto = texto[:config.STORY_MAX_CHARS]

    texto_limpio = _clean_text(texto)
    _mark_as_used(post_id)

    return {
        "titulo":   titulo or texto_limpio[:80],
        "historia": texto_limpio,
        "fuente":   source if source != "reddit" else "Reddit",
        "upvotes":  upvotes,
        "post_id":  post_id,
    }


def get_story() -> dict | None:
    """
    Busca y retorna una historia real de Reddit u otras fuentes de confesiones.

    Flujo:
    1. Reddit (principal) — recorre subreddits configurados en orden aleatorio
    2. Fallback: grouphug.us si Reddit no da resultados válidos
    3. Marca el post como usado para evitar repeticiones.

    Returns:
        Dict con keys: titulo, historia, fuente, upvotes, post_id
        None si no encuentra ninguna historia valida.
    """
    used_ids = _load_used_ids()

    # ── FUENTE PRIMARIA: Reddit ────────────────────────────────────────────────
    subreddits = config.REDDIT_SUBREDDITS[:]
    random.shuffle(subreddits)

    for subreddit in subreddits:
        logger.info(f"Buscando historia en r/{subreddit}...")
        posts = _fetch_subreddit(subreddit)

        if not posts:
            time.sleep(1)
            continue

        posts.sort(key=_score_post, reverse=True)
        top_posts = posts[:5]
        random.shuffle(top_posts)
        top_posts += posts[5:30]

        for post in top_posts:
            post["_source"] = f"r/{subreddit}"
            story = _try_post(post, used_ids)
            if story:
                logger.info(
                    f"Historia seleccionada: '{story['titulo'][:60]}' "
                    f"| {len(story['historia'])} chars | {story['upvotes']} upvotes | {story['fuente']}"
                )
                return story

        time.sleep(1.5)

    # ── FUENTE SECUNDARIA: grouphug.us ────────────────────────────────────────
    logger.info("Reddit sin resultados — intentando grouphug.us...")
    gh_posts = _fetch_grouphug()
    random.shuffle(gh_posts)
    for post in gh_posts:
        story = _try_post(post, used_ids)
        if story:
            logger.info(f"Historia de grouphug.us: {len(story['historia'])} chars")
            return story

    logger.warning("No se encontro ninguna historia valida en ninguna fuente")
    return None


def reset_used_posts() -> None:
    """Limpia el historial de posts usados. Util para empezar de cero."""
    if config.USED_POSTS_FILE.exists():
        config.USED_POSTS_FILE.write_text(
            json.dumps({"used_ids": []}, indent=2),
            encoding="utf-8",
        )
        logger.info("Historial de posts usados reiniciado")
