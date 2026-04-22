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


def mark_as_used(post_id: str) -> None:
    """Marca un post como usado para no repetirlo."""
    used = _load_used_ids()
    used.add(post_id)
    config.USED_POSTS_FILE.write_text(
        json.dumps({"used_ids": list(used)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# Alias privado para compatibilidad interna
_mark_as_used = mark_as_used


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


def _fetch_confesiones_anonimas() -> list[dict]:
    """
    Obtiene confesiones de confesionesanonimas.org/muro.php — sitio hispanohablante
    de confesiones anonimas con categorias dramaticas.

    Estructura HTML de cada confesion:
      <article class="card" data-category="Amor / Relaciones">
        <h3>Titulo o "Sin titulo"</h3>
        <div class="meta">Categoria • Pais • Fecha</div>
        <button class="btn view" data-full="TEXTO COMPLETO AQUI">Ver mas</button>
      </article>

    El texto completo esta en el atributo data-full del boton "Ver mas".
    Retorna lista de dicts en el mismo formato que los posts de Reddit.
    """
    from bs4 import BeautifulSoup
    import re as _re
    import hashlib

    # Categorias con mayor potencial dramatico para Shorts
    _DRAMATIC_CATEGORIES = {
        "Amor / Relaciones",
        "Secretos Oscuros",
        "Tristeza / Dolor",
        "Familia",
        "Chismecito",
        "Amigos",
    }

    # Palabras clave que descartan confesiones de esta fuente
    _CA_BLOCKED = [
        "suicid", "matarme", "quitarme la vida",
        "abusar", "abuso sexual", "menor",
        "terroris",
    ]

    results = []
    # La pagina no tiene paginacion publica visible — una sola pagina con ~20-30 cards
    pages_to_try = [
        "https://confesionesanonimas.org/muro.php",
        "https://confesionesanonimas.org/muro.php?categoria=Amor+%2F+Relaciones",
        "https://confesionesanonimas.org/muro.php?categoria=Secretos+Oscuros",
        "https://confesionesanonimas.org/muro.php?categoria=Tristeza+%2F+Dolor",
        "https://confesionesanonimas.org/muro.php?categoria=Familia",
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }

    seen_texts: set[str] = set()

    for url in pages_to_try:
        try:
            resp = requests.get(url, headers=headers, timeout=config.SCRAPER_TIMEOUT)
            if resp.status_code != 200:
                logger.debug(f"confesionesanonimas.org: HTTP {resp.status_code} en {url}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("article.card")

            if not cards:
                logger.debug(f"confesionesanonimas.org: 0 cards en {url}")
                continue

            logger.debug(f"confesionesanonimas.org: {len(cards)} cards en {url}")

            for card in cards:
                # Extraer categoria del atributo data-category
                category = card.get("data-category", "").strip()

                # Solo categorias dramaticas (o todas si no hay filtro en URL)
                if category and category not in _DRAMATIC_CATEGORIES:
                    continue

                # Titulo del h3 (puede ser "Sin título")
                h3 = card.find("h3")
                raw_title = h3.get_text(strip=True) if h3 else ""
                titulo = raw_title if raw_title and raw_title.lower() != "sin título" else ""

                # Meta: "Categoria • Pais • Fecha"
                meta_div = card.find("div", class_="meta")
                fecha = ""
                if meta_div:
                    meta_text = meta_div.get_text(separator=" ", strip=True)
                    # Extraer fecha con regex (formato: YYYY-MM-DD o YYYY-MM-DD HH:MM)
                    date_match = _re.search(r"\d{4}-\d{2}-\d{2}", meta_text)
                    if date_match:
                        fecha = date_match.group(0)

                # Texto completo: atributo data-full del boton "Ver mas"
                btn = card.find("button", class_="view")
                if not btn:
                    continue
                texto = btn.get("data-full", "").strip()

                if not texto:
                    continue

                # Evitar duplicados por contenido (el mismo texto puede aparecer en varias URLs)
                text_hash = hashlib.md5(texto[:200].encode("utf-8")).hexdigest()[:12]
                if text_hash in seen_texts:
                    continue
                seen_texts.add(text_hash)

                # Filtro de contenido bloqueado
                texto_lower = texto.lower()
                if any(kw in texto_lower for kw in _CA_BLOCKED):
                    continue

                # Filtro de longitud minima — consistente con STORY_MIN_CHARS global
                word_count = len(texto.split())
                if len(texto) < config.STORY_MIN_CHARS:
                    continue

                # ID unico basado en hash del contenido (no hay ID nativo)
                post_id = f"ca_{text_hash}"

                # Titulo de fallback: primeras palabras del texto
                if not titulo:
                    titulo = texto[:80].rstrip() + "..."

                # Score base: preferir categorias mas dramaticas
                score_map = {
                    "Secretos Oscuros": 350,
                    "Amor / Relaciones": 300,
                    "Tristeza / Dolor": 280,
                    "Familia": 260,
                    "Chismecito": 240,
                    "Amigos": 220,
                }
                score = score_map.get(category, 200)

                # Bonus por longitud: historias mas largas = mejores para narrar
                if word_count >= 200:
                    score += 60
                elif word_count >= 120:
                    score += 30

                results.append({
                    "id": post_id,
                    "title": titulo,
                    "selftext": texto,
                    "score": score,
                    "num_comments": 0,
                    "is_self": True,
                    "_source": "confesionesanonimas.org",
                    "_category": category,
                    "_fecha": fecha,
                })

        except requests.exceptions.ConnectionError:
            logger.warning("confesionesanonimas.org: sin conexion a internet")
            break
        except Exception as e:
            logger.debug(f"confesionesanonimas.org error en {url}: {e}")
            continue

    if results:
        logger.info(
            f"confesionesanonimas.org: {len(results)} confesiones validas obtenidas"
        )
    return results


_WATTPAD_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":         "https://www.wattpad.com/",
}

# Búsquedas dramáticas genéricas
_WATTPAD_QUERIES = [
    "confesion traicion drama",
    "secreto familiar oscuro",
    "descubrí engaño pareja",
    "infidelidad verdad revelacion",
    "historia real drama romance",
    "traicion amor secreto",
    "mentira familia drama",
]

# Búsquedas para historias eróticas/adultas de vida real — canal premium Stars
# Enfocadas en relaciones prohibidas reales: jefe, cuñado, vecino, amigo, etc.
_WATTPAD_ADULT_QUERIES = [
    "infidelidad esposa amante secreto",
    "jefe empleada seduccion prohibida",
    "cuñado prohibido deseo culpa",
    "mejor amigo traicion pasion secreta",
    "vecino casado tension sexual",
    "matrimonio aburrido amante apasionado",
    "esposo infiel noche hotel otra",
    "aventura extramarital confesion real",
    "noche de hotel secreto prohibido",
    "deseo prohibido amigo pareja",
    "relacion prohibida trabajo pasion",
    "encuentro casual pasion descontrolada",
    "ex novio reencuentro prohibido",
    "seduccion cuñada cuñado taboo",
    "amante secreta anos mentira revelacion",
    "primera vez experiencia prohibida adulto",
    "pareja abierta celos confusion",
    "trio accidental noche secreto",
]

# Palabras clave de fanfic/fantasía — descarta estas historias automáticamente
_FANFIC_KEYWORDS = {
    # Harry Potter
    "hogwarts", "harry potter", "voldemort", "hermione", "dumbledore", "draco",
    # Naruto / anime
    "naruto", "sasuke", "kakashi", "genos", "sai", "anime", "manga",
    "marinette", "adrien", "miraculous",
    # Kpop
    "kpop", "bts", "jungkook", "taehyung", "suga", "jimin", "jhope", "namjoon",
    "exo", "stray kids", "txt", "ateez", "seventeen",
    # Bandas pop
    "one direction", "billie eilish", "shawn mendes", "zayn", "niall", "harry styles",
    # Fantasia
    "vampire", "vampiro", "werewolf", "lobo alfa", "alpha", "omega", "lycan",
    "dragon", "dragón", "magia", "magic", "witch", "bruja", "hechizo", "pocion",
    "elfo", "elfos", "duende", "hada", "hadas", "fantasia", "reino",
    "demonio", "angel caido", "supernatural", "sobrenatural",
    # Videojuegos / series
    "hallownest", "hollow knight", "fnaf", "minecraft", "undertale",
    "stranger things", "disney", "marvel", "avenger", "superman", "batman",
    "spiderman", "deadpool", "thor",
    # Novelas chinas / coreanas
    "xianwang", "danmei", "wuxia", "xianxia", "manhwa", "manhwa",
    "cultivation", "cultivacion", "sect", "immortal", "inmortal",
    # Marcadores de fanfic
    "fanfic", "fanfiction", "au ", "universo alterno", "wattpad original",
    "x reader", "x lector",
    # Listas / recopilaciones — no son historias
    "lecturas de wattpad", "recomendaciones", "lista de", "mis lecturas",
    "libros recomendados", "mejores historias", "top wattpad",
}


def _wattpad_part_text(part_id: int) -> str:
    """Descarga un capítulo de Wattpad y extrae el texto plano."""
    import re as _re
    from html.parser import HTMLParser

    class _Strip(HTMLParser):
        def __init__(self):
            super().__init__()
            self.chunks: list[str] = []
        def handle_data(self, data: str):
            self.chunks.append(data)

    try:
        url  = f"https://www.wattpad.com/apiv2/storytext?id={part_id}"
        hdrs = {"User-Agent": _WATTPAD_HEADERS["User-Agent"]}
        resp = requests.get(url, headers=hdrs, timeout=20)
        if resp.status_code != 200:
            return ""
        parser = _Strip()
        parser.feed(resp.text)
        text = _re.sub(r"\s+", " ", " ".join(parser.chunks)).strip()
        return text
    except Exception as e:
        logger.debug(f"Wattpad part {part_id}: {e}")
        return ""


def _fetch_wattpad() -> list[dict]:
    """
    Busca historias dramáticas en español en Wattpad via API no oficial.
    Devuelve el texto del primer capítulo de cada historia encontrada.

    API usada (pública, sin auth):
      GET https://www.wattpad.com/api/v3/stories?query=...&language=3&filter=hot
      language=3 → Español en la codificación de Wattpad
    """
    import re as _re

    results: list[dict] = []
    queries = random.sample(_WATTPAD_QUERIES, min(3, len(_WATTPAD_QUERIES)))
    seen_ids: set[str] = set()

    for query in queries:
        try:
            resp = requests.get(
                "https://www.wattpad.com/api/v3/stories",
                headers=_WATTPAD_HEADERS,
                params={
                    "query":    query,
                    "language": 5,            # 5 = Español
                    "limit":    20,
                    "offset":   random.randint(0, 40),
                },
                timeout=20,
            )
            if resp.status_code != 200:
                logger.debug(f"Wattpad query '{query}': HTTP {resp.status_code}")
                continue

            stories = resp.json().get("stories", [])
            logger.debug(f"Wattpad '{query}': {len(stories)} historias")

            for story in stories:
                story_id = str(story.get("id", ""))
                if not story_id or story_id in seen_ids:
                    continue
                seen_ids.add(story_id)

                title       = story.get("title", "").strip()
                description = story.get("description", "").strip()
                parts       = story.get("parts", [])
                reads       = story.get("readCount", 0)
                votes       = story.get("voteCount", 0)

                if not parts:
                    continue

                # Tomar el primer capítulo
                first_part_id = parts[0].get("id") if isinstance(parts[0], dict) else None
                if not first_part_id:
                    continue

                text = _wattpad_part_text(first_part_id)

                # Si el capítulo está vacío o es muy corto, usar la descripción
                if len(text) < config.STORY_MIN_CHARS:
                    text = description
                if len(text) < config.STORY_MIN_CHARS:
                    continue

                # Limpiar etiquetas HTML residuales
                text = _re.sub(r"<[^>]+>", "", text).strip()
                if len(text) > config.STORY_MAX_CHARS:
                    text = text[:config.STORY_MAX_CHARS]

                # Score proporcional a popularidad en la plataforma
                pop_score = min(votes // 5 + reads // 500, 800)

                results.append({
                    "id":          f"wattpad_{story_id}",
                    "title":       title,
                    "selftext":    text,
                    "score":       200 + pop_score,
                    "num_comments": 0,
                    "is_self":     True,
                    "_source":     "Wattpad",
                })

            time.sleep(1.5)  # respetar rate limit de Wattpad

        except requests.exceptions.ConnectionError:
            logger.warning("Wattpad: sin conexión a internet")
            break
        except Exception as e:
            logger.debug(f"Wattpad query '{query}': {e}")

    if results:
        logger.info(f"Wattpad: {len(results)} historias obtenidas")
    return results


def _fetch_wattpad_adult() -> list[dict]:
    """
    Busca historias eróticas/adultas de vida real en Wattpad.
    Filtra fanfic/fantasía automáticamente.
    Intenta hasta 3 partes por historia si la primera es corta.
    """
    import re as _re

    results:  list[dict] = []
    queries   = random.sample(_WATTPAD_ADULT_QUERIES, min(6, len(_WATTPAD_ADULT_QUERIES)))
    seen_ids: set[str]   = set()
    MIN_CHARS    = 300
    MIN_READS    = 5_000   # solo historias con tracción real

    for query in queries:
        try:
            resp = requests.get(
                "https://www.wattpad.com/api/v3/stories",
                headers=_WATTPAD_HEADERS,
                params={
                    "query":    query,
                    "language": 5,
                    "limit":    40,   # más candidatos para poder filtrar por popularidad
                    "offset":   0,    # siempre desde el top — queremos las más leídas
                },
                timeout=20,
            )
            if resp.status_code != 200:
                logger.debug(f"Wattpad adult '{query}': HTTP {resp.status_code}")
                continue

            stories = resp.json().get("stories", [])
            logger.debug(f"Wattpad adult '{query}': {len(stories)} raw")

            for story in stories:
                story_id = str(story.get("id", ""))
                if not story_id or story_id in seen_ids:
                    continue
                seen_ids.add(story_id)

                title = story.get("title", "").strip()
                parts = story.get("parts", [])
                reads = story.get("readCount", 0)
                votes = story.get("voteCount", 0)
                desc  = story.get("description", "")

                if not parts or not title:
                    continue

                # Descartar historias con pocas lecturas — solo virales
                if reads < MIN_READS:
                    logger.debug(f"Wattpad adult: pocas lecturas ({reads}) '{title[:40]}'")
                    continue

                # Filtrar fanfic/fantasía
                tags_raw   = story.get("tags", [])
                tags_str   = " ".join(t if isinstance(t, str) else t.get("name", "") for t in tags_raw)
                combined   = f"{title} {desc} {tags_str}".lower()
                if any(kw in combined for kw in _FANFIC_KEYWORDS):
                    logger.debug(f"Wattpad adult: fanfic descartado '{title[:40]}'")
                    continue

                # Intentar hasta 3 partes para encontrar texto suficiente
                text = ""
                for part in parts[:3]:
                    if not isinstance(part, dict):
                        continue
                    pid = part.get("id")
                    if not pid:
                        continue
                    t = _wattpad_part_text(pid)
                    if len(t) >= MIN_CHARS:
                        text = t
                        break

                if not text:
                    text = desc.strip()
                if len(text) < MIN_CHARS:
                    continue

                text = _re.sub(r"<[^>]+>", "", text).strip()
                if len(text) > config.STORY_MAX_CHARS:
                    text = text[:config.STORY_MAX_CHARS]

                # Score real: lecturas pesan más que votos
                pop_score = reads // 1000 + votes // 10
                results.append({
                    "id":           f"wattpad_adult_{story_id}",
                    "title":        title,
                    "selftext":     text,
                    "score":        300 + pop_score,
                    "num_comments": 0,
                    "is_self":      True,
                    "_source":      "Wattpad",
                    "_reads":       reads,
                    "_adult":       True,
                })

            time.sleep(0.8)
        except Exception as e:
            logger.warning(f"Wattpad adult '{query}': {e}")

    results.sort(key=lambda r: r.get("score", 0), reverse=True)
    if results:
        top = results[0]
        logger.info(
            f"Wattpad adult: {len(results)} historias validas "
            f"| top: '{top['title'][:50]}' ({top.get('_reads',0):,} lecturas)"
        )
    else:
        logger.info("Wattpad adult: 0 historias validas")
    return results


def _load_used_ids_channel() -> set:
    """Carga los IDs usados por el canal de Telegram (lista separada de YouTube)."""
    channel_file = getattr(config, "USED_POSTS_CHANNEL_FILE", None)
    if channel_file and Path(channel_file).exists():
        try:
            data = json.loads(Path(channel_file).read_text(encoding="utf-8"))
            return set(data.get("used_ids", []))
        except Exception:
            pass
    return set()


def _mark_as_used_channel(post_id: str) -> None:
    """Marca un post como usado para el canal de Telegram."""
    channel_file = getattr(config, "USED_POSTS_CHANNEL_FILE", None)
    if not channel_file:
        mark_as_used(post_id)
        return
    used = _load_used_ids_channel()
    used.add(post_id)
    Path(channel_file).write_text(
        json.dumps({"used_ids": list(used)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_story_for_channel() -> dict | None:
    """
    Versión especial de get_story() para el canal premium de Telegram.
    Prioriza historias de Wattpad con contenido adulto/morboso que el LLM
    reescribirá para hacerlas más intensas antes de publicarlas detrás del paywall.
    Usa su propia lista de usados (used_posts_channel.json) para no desperdiciar
    historias que YouTube aún no ha usado.

    Returns:
        Dict con keys: titulo, historia, fuente, upvotes, post_id, _adult
        None si no encuentra nada.
    """
    used_ids = _load_used_ids_channel()

    # Intentar primero Wattpad adulto, luego fuentes normales como fallback
    sources = [
        ("wattpad_adult", _fetch_wattpad_adult),
        ("wattpad",       _fetch_wattpad),
        ("confesiones",   _fetch_confesiones_anonimas),
    ]

    for source_name, fetch_fn in sources:
        logger.info(f"Canal premium: buscando en {source_name}...")
        try:
            posts = fetch_fn()
        except Exception as e:
            logger.debug(f"{source_name} error: {e}")
            continue

        posts.sort(key=lambda p: p.get("score", 0), reverse=True)
        random.shuffle(posts[:5])  # mezcla el top 5 para variedad

        for post in posts:
            story = _try_post(post, used_ids)
            if story:
                story["_adult"] = post.get("_adult", False)
                _mark_as_used_channel(story["post_id"])
                logger.info(
                    f"Historia para canal ({source_name}): "
                    f"'{story['titulo'][:60]}' | {len(story['historia'])} chars"
                )
                return story

    logger.warning("Canal premium: sin historias disponibles en ninguna fuente")
    return None


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
    1. Elegir fuente primaria al azar: 50% Reddit, 30% confesionesanonimas.org, 20% grouphug.us
    2. Si la fuente primaria falla, intentar las restantes en orden.
    3. Marca el post como usado para evitar repeticiones.

    Returns:
        Dict con keys: titulo, historia, fuente, upvotes, post_id
        None si no encuentra ninguna historia valida.
    """
    used_ids = _load_used_ids()

    # Seleccion de fuente primaria con pesos
    #   40% Reddit (señal social real — upvotes = validación humana)
    #   25% confesionesanonimas.org (hispanohablante, dramático)
    #   25% Wattpad (ficción dramática en español, historias largas y bien escritas)
    #   10% grouphug.us (confesiones cortas, fallback)
    fuente_rand = random.random()
    if fuente_rand < 0.40:
        orden_fuentes = ["reddit", "confesionesanonimas", "wattpad", "grouphug"]
    elif fuente_rand < 0.65:
        orden_fuentes = ["confesionesanonimas", "wattpad", "reddit", "grouphug"]
    elif fuente_rand < 0.90:
        orden_fuentes = ["wattpad", "confesionesanonimas", "reddit", "grouphug"]
    else:
        orden_fuentes = ["grouphug", "reddit", "confesionesanonimas", "wattpad"]

    logger.info(f"Orden de fuentes para esta ejecucion: {orden_fuentes}")

    for fuente in orden_fuentes:

        # ── Reddit ────────────────────────────────────────────────────────────
        if fuente == "reddit":
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
                        mark_as_used(story["post_id"])
                        logger.info(
                            f"Historia seleccionada (Reddit): '{story['titulo'][:60]}' "
                            f"| {len(story['historia'])} chars | {story['upvotes']} upvotes"
                        )
                        return story

                time.sleep(1.5)

            logger.info("Reddit: sin resultados validos")

        # ── confesionesanonimas.org ────────────────────────────────────────────
        elif fuente == "confesionesanonimas":
            logger.info("Intentando confesionesanonimas.org...")
            ca_posts = _fetch_confesiones_anonimas()
            # Ordenar por score descendente y mezclar los top para variedad
            ca_posts.sort(key=lambda p: p.get("score", 0), reverse=True)
            top_ca = ca_posts[:5]
            random.shuffle(top_ca)
            top_ca += ca_posts[5:]

            for post in top_ca:
                story = _try_post(post, used_ids)
                if story:
                    mark_as_used(story["post_id"])
                    logger.info(
                        f"Historia seleccionada (confesionesanonimas.org): "
                        f"'{story['titulo'][:60]}' | {len(story['historia'])} chars"
                    )
                    return story

            logger.info("confesionesanonimas.org: sin resultados validos")

        # ── Wattpad ───────────────────────────────────────────────────────────
        elif fuente == "wattpad":
            logger.info("Intentando Wattpad...")
            wp_posts = _fetch_wattpad()
            wp_posts.sort(key=lambda p: p.get("score", 0), reverse=True)
            top_wp = wp_posts[:5]
            random.shuffle(top_wp)
            top_wp += wp_posts[5:]
            for post in top_wp:
                story = _try_post(post, used_ids)
                if story:
                    mark_as_used(story["post_id"])
                    logger.info(
                        f"Historia seleccionada (Wattpad): "
                        f"'{story['titulo'][:60]}' | {len(story['historia'])} chars"
                    )
                    return story
            logger.info("Wattpad: sin resultados validos")

        # ── grouphug.us ───────────────────────────────────────────────────────
        elif fuente == "grouphug":
            logger.info("Intentando grouphug.us...")
            gh_posts = _fetch_grouphug()
            random.shuffle(gh_posts)
            for post in gh_posts:
                story = _try_post(post, used_ids)
                if story:
                    mark_as_used(story["post_id"])
                    logger.info(
                        f"Historia seleccionada (grouphug.us): "
                        f"{len(story['historia'])} chars"
                    )
                    return story

            logger.info("grouphug.us: sin resultados validos")

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
