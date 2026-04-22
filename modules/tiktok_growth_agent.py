"""
tiktok_growth_agent.py — Agente de crecimiento en TikTok

Estrategias:
  1. Buscar videos del nicho (confesiones, drama, historias reales)
  2. Ver cada video 20-40s (TikTok registra watch time)
  3. Dar like a videos relevantes (60% probabilidad)
  4. Comentar en los más virales (30% probabilidad, max 5/día)
  5. Seguir a creadores del nicho pequeños (2-3/día, rotativo)
  6. Responder comentarios en propios TikToks

Anti-detección:
  - Mismo perfil Chrome que el uploader (sesión real activa)
  - Delays triangulares + scroll orgánico
  - Límites conservadores (TikTok baneará rápido si abusas)
"""

import asyncio
import json
import logging
import platform
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from modules.youtube_uploader import (
    _cursor,
    _delay,
    _human_click,
    _human_type,
    _inject_stealth,
    _organic_pause,
    _random_mouse_wander,
    _scroll,
    _simulate_reading,
)

logger = logging.getLogger(__name__)

# ─── Límites diarios (TikTok es más estricto que YouTube) ────────────────────
DAILY_COMMENT_LIMIT = 5
DAILY_LIKE_LIMIT    = 20
DAILY_FOLLOW_LIMIT  = 3
TT_LOG_FILE         = Path(__file__).parent.parent / "tiktok_growth_log.json"

# ─── Búsquedas del nicho en TikTok ───────────────────────────────────────────
TT_SEARCHES = [
    "historia real traición",
    "me engañó y no lo sabía",
    "secreto familiar impactante",
    "mi pareja tenía una doble vida",
    "confesión real storytime",
    "relación tóxica historia real",
    "descubrí la verdad de mi pareja",
    "infidelidad historia real",
    "amiga que me traicionó",
    "secreto que guardé años",
    "drama familiar real",
    "me mintió durante años",
]

# ─── Mapa de topics ganadores → términos en TT_SEARCHES ─────────────────────
_TT_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "traicion":   ["traición", "engañó", "infiel", "doble vida"],
    "secreto":    ["secreto", "verdad", "descubrí"],
    "familia":    ["familia", "madre", "padre", "suegra"],
    "narci":      ["tóxic", "narcis", "manipul", "abuso"],
    "doble_vida": ["doble vida", "segunda familia"],
}


def _tt_memory_boosted_searches() -> list[str]:
    """Devuelve TT_SEARCHES con los topics ganadores de analytics al frente."""
    try:
        from modules import agent_memory as _am
        top_topics = _am.get_topic_bias()
        if not top_topics:
            return list(TT_SEARCHES)
        priority, rest = [], []
        for s in TT_SEARCHES:
            sl = s.lower()
            boosted = any(
                kw in sl
                for topic in top_topics[:2]
                for kw in _TT_TOPIC_KEYWORDS.get(topic, [])
            )
            (priority if boosted else rest).append(s)
        return priority + rest
    except Exception:
        return list(TT_SEARCHES)


# ─── Templates de comentarios TikTok (más cortos y directos que YouTube) ─────
TT_COMMENT_TEMPLATES = [
    "Esto me pasó igual 😶 no lo podía creer",
    "Dios mío... yo hubiera hecho lo mismo 😤",
    "Esto me dejó sin palabras fr 😱",
    "¿Cómo se puede llegar a eso? 💀",
    "Me quedé helado/a con esto 🫣",
    "El final no me lo esperaba para nada 😮",
    "Esto merece más views de los que tiene 🔥",
    "Literal me pasó algo parecido, cuéntame más",
    "No entiendo cómo lo aguantaste tanto tiempo 😤",
    "Se me cayó el alma escuchando esto 💔",
]


# ─── Log helpers ──────────────────────────────────────────────────────────────

def _tt_load_log() -> dict:
    if TT_LOG_FILE.exists():
        try:
            return json.loads(TT_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _tt_save_log(log: dict) -> None:
    try:
        TT_LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _tt_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _tt_daily(log: dict, key: str) -> int:
    return log.get("daily", {}).get(_tt_today(), {}).get(key, 0)


def _tt_inc(log: dict, key: str) -> None:
    log.setdefault("daily", {}).setdefault(_tt_today(), {})[key] = _tt_daily(log, key) + 1
    _tt_save_log(log)


# ─── LLM para comentarios contextuales ───────────────────────────────────────

async def _generate_tt_comment(video_desc: str) -> str:
    """Genera un comentario para TikTok — longitud variable para parecer humano."""
    try:
        import httpx
        groq_key = getattr(config, "GROQ_API_KEY", "")
        if not groq_key:
            return random.choice(TT_COMMENT_TEMPLATES)

        # Variar el tipo de comentario: reacción corta, media o larga
        style = random.choices(
            ["corto", "medio", "largo"],
            weights=[40, 40, 20], k=1
        )[0]
        length_hint = {
            "corto": "máx 5 palabras (ej: 'no lo puedo creer 😱')",
            "medio": "entre 8 y 15 palabras, natural y emotivo",
            "largo": "entre 15 y 25 palabras, como si contaras tu propia experiencia",
        }[style]

        prompt = (
            f"Eres alguien que acaba de ver este TikTok: '{video_desc[:100]}'\n"
            f"Escribe UN comentario en español latino, {length_hint}. "
            f"Sin hashtags. Solo el comentario, sin comillas."
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 40,
                    "temperature": 0.9,
                },
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
                return text[:100] if len(text) > 5 else random.choice(TT_COMMENT_TEMPLATES)
    except Exception:
        pass
    return random.choice(TT_COMMENT_TEMPLATES)


# ─── Acciones en TikTok ───────────────────────────────────────────────────────

async def _search_tiktok_niche(browser, keyword: str) -> list[dict]:
    """Busca videos del nicho en TikTok y retorna lista con url, desc, likes."""
    videos: list[dict] = []
    try:
        search_url = f"https://www.tiktok.com/search?q={keyword.replace(' ', '%20')}&t=video"
        page = await browser.get(search_url)
        await _delay(4.0, 7.0)

        # Scroll para cargar resultados
        for _ in range(random.randint(2, 4)):
            await _scroll(page, random.randint(300, 600))
            await _delay(1.5, 3.0)

        results = await page.evaluate("""(function() {
            var items = document.querySelectorAll('div[class*="DivItemContainer"], div[data-e2e="search_video-item"]');
            var videos = [];
            items.forEach(function(item) {
                var link = item.querySelector('a[href*="/video/"]');
                var desc = item.querySelector('[class*="SpanText"], [data-e2e="video-desc"]');
                var likes = item.querySelector('[data-e2e="like-count"], [class*="like"]');
                if (link) {
                    videos.push({
                        url: link.href,
                        desc: desc ? desc.innerText.slice(0, 120) : '',
                        likes_text: likes ? likes.innerText : '0'
                    });
                }
            });
            return JSON.stringify(videos.slice(0, 12));
        })()""")

        if results:
            videos = json.loads(results)
            logger.info(f"  TikTok búsqueda '{keyword}': {len(videos)} videos")
    except Exception as e:
        logger.debug(f"  _search_tiktok_niche: {e}")
    return videos


async def _engage_tiktok_video(browser, video: dict, log: dict) -> dict:
    """
    Ve un video de TikTok, lo likea y/o comenta según probabilidades.
    Retorna dict con acciones realizadas.
    """
    actions = {"liked": False, "commented": False, "url": video.get("url", "")}
    url = video.get("url", "")
    if not url:
        return actions

    try:
        page = await browser.get(url)
        await _delay(3.0, 6.0)

        # Salida temprana "no me enganchó" — 20% de videos (patrón humano real)
        if random.random() < 0.20:
            bail_s = random.uniform(3.0, 8.0)
            await asyncio.sleep(bail_s)
            logger.debug(f"  TikTok: salida temprana ({bail_s:.0f}s) — no interesó")
            return actions

        # Ver el video 20-45 segundos (registra watch time)
        watch_s = random.triangular(20.0, 45.0, 30.0)
        logger.info(f"  👁️ Viendo TikTok {watch_s:.0f}s: {url[-30:]}")
        await asyncio.sleep(watch_s)
        await _random_mouse_wander(page)

        # Like (60% probabilidad si no se alcanzó el límite)
        if random.random() < 0.60 and _tt_daily(log, "likes") < DAILY_LIKE_LIMIT:
            try:
                like_btn = None
                for sel in ['button[data-e2e="like-icon"]', 'span[data-e2e="like-count"]',
                            'button[aria-label*="like"]', 'button[aria-label*="Me gusta"]']:
                    try:
                        like_btn = await page.select(sel, timeout=4)
                        if like_btn:
                            break
                    except Exception:
                        continue

                if like_btn:
                    await _human_click(page, like_btn)
                    await _delay(0.8, 1.5)
                    actions["liked"] = True
                    _tt_inc(log, "likes")
                    logger.info(f"  ❤️ Like dado")
            except Exception:
                pass

        await _delay(1.0, 2.5)

        # Comentar (30% probabilidad si no se alcanzó el límite)
        if random.random() < 0.30 and _tt_daily(log, "comments") < DAILY_COMMENT_LIMIT:
            try:
                comment_text = await _generate_tt_comment(video.get("desc", ""))
                await _delay(0.5, 1.5)

                comment_input = None
                for sel in ['div[data-e2e="comment-input"]', 'div[contenteditable="true"]',
                            'input[placeholder*="omenta"]', 'input[placeholder*="omment"]']:
                    try:
                        comment_input = await page.select(sel, timeout=5)
                        if comment_input:
                            break
                    except Exception:
                        continue

                if comment_input:
                    await _human_click(page, comment_input)
                    await _delay(0.5, 1.0)
                    await _human_type(comment_input, comment_text)
                    await _delay(1.0, 2.0)

                    # Enviar comentario
                    sent = await page.evaluate("""(function() {
                        var btns = document.querySelectorAll('button');
                        for (var btn of btns) {
                            var t = (btn.innerText || '').trim().toLowerCase();
                            if (t === 'publicar' || t === 'post' || t === 'send' || t === 'enviar') {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    })()""")

                    if not sent:
                        # Fallback: Enter
                        await page.keyboard.send("Enter")
                        sent = True

                    if sent:
                        await _delay(1.5, 3.0)
                        actions["commented"] = True
                        _tt_inc(log, "comments")
                        logger.info(f"  💬 Comentado: '{comment_text[:40]}'")

            except Exception as e:
                logger.debug(f"  Comentar TikTok: {e}")

    except Exception as e:
        logger.debug(f"  _engage_tiktok_video: {e}")

    return actions


async def _reply_own_tiktok_comments(browser, log: dict) -> int:
    """
    Va al perfil propio de TikTok y responde comentarios sin respuesta.
    """
    replied = 0
    username = getattr(config, "TIKTOK_USERNAME", "")
    if not username:
        return 0

    try:
        profile_url = f"https://www.tiktok.com/@{username}"
        page = await browser.get(profile_url)
        await _delay(4.0, 7.0)

        # Abrir el video más reciente
        video_link = None
        for sel in ['a[href*="/video/"]', 'div[data-e2e="user-post-item"] a']:
            try:
                links = await page.select_all(sel, timeout=6)
                if links:
                    video_link = links[0]
                    break
            except Exception:
                continue

        if not video_link:
            return 0

        await _human_click(page, video_link)
        await _delay(3.0, 6.0)

        # Buscar comentarios sin respuesta
        unreplied = await page.evaluate("""(function() {
            var comments = document.querySelectorAll('div[data-e2e="comment-item"]');
            var result = [];
            comments.forEach(function(c) {
                var text = c.querySelector('[data-e2e="comment-text-content"]');
                var replies = c.querySelectorAll('[data-e2e="comment-reply-item"]');
                if (text && replies.length === 0) {
                    result.push(text.innerText.slice(0, 100));
                }
            });
            return JSON.stringify(result.slice(0, 3));
        })()""")

        if not unreplied:
            return 0

        comments_list = json.loads(unreplied)
        for comment_text in comments_list:
            if _tt_daily(log, "own_replies") >= 3:
                break
            try:
                reply = await _generate_tt_comment(comment_text)

                # Click en "Responder" del comentario
                replied_ok = await page.evaluate(f"""(function() {{
                    var comments = document.querySelectorAll('div[data-e2e="comment-item"]');
                    for (var c of comments) {{
                        var text = c.querySelector('[data-e2e="comment-text-content"]');
                        if (text && text.innerText.slice(0, 50) === '{comment_text[:50]}') {{
                            var replyBtn = c.querySelector('[data-e2e="comment-reply-btn"]');
                            if (replyBtn) {{ replyBtn.click(); return true; }}
                        }}
                    }}
                    return false;
                }})()""")

                if replied_ok:
                    await _delay(1.0, 2.0)
                    input_el = await page.select('div[contenteditable="true"]', timeout=4)
                    if input_el:
                        await _human_type(input_el, reply)
                        await _delay(0.8, 1.5)
                        await page.keyboard.send("Enter")
                        await _delay(1.5, 3.0)
                        replied += 1
                        _tt_inc(log, "own_replies")
                        logger.info(f"  TikTok reply: '{reply[:40]}'")
                        await _delay(5.0, 12.0)
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"  _reply_own_tiktok_comments: {e}")

    return replied


def _tt_is_active_hour() -> bool:
    """Solo actúa entre GROWTH_ACTIVE_HOUR_START y GROWTH_ACTIVE_HOUR_END (defecto 8-23h)."""
    h = datetime.now().hour
    start = getattr(config, "GROWTH_ACTIVE_HOUR_START", 8)
    end   = getattr(config, "GROWTH_ACTIVE_HOUR_END",   23)
    return start <= h < end


def _tt_pick_session_mode() -> str:
    """Varía el tipo de sesión para romper el patrón mecánico."""
    return random.choices(
        ["engage", "engage", "engage", "fyp_only", "reply_focus"],
        weights=[40, 30, 20, 7, 3],
        k=1
    )[0]


async def _tiktok_growth_session_async() -> dict:
    """Sesión completa de crecimiento en TikTok."""
    if not _tt_is_active_hour():
        h = datetime.now().hour
        logger.info(f"TikTok growth: hora {h:02d}h fuera de ventana activa — omitiendo")
        return {"likes": 0, "comments": 0, "own_replies": 0, "videos_watched": 0}

    log = _tt_load_log()
    results = {"likes": 0, "comments": 0, "own_replies": 0, "videos_watched": 0}

    session_mode = _tt_pick_session_mode()
    if session_mode != "engage":
        logger.info(f"TikTok growth: modo '{session_mode}' (variación aleatoria)")

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    # TikTok usa el mismo perfil Chrome que el uploader
    tiktok_profile = Path(str(config.CHROME_PROFILE_DIR).replace("chrome_profile", "chrome_profile_tiktok"))
    if tiktok_profile.exists():
        profile_dir = tiktok_profile

    if platform.system() == "Linux" and not __import__('os').environ.get("DISPLAY"):
        __import__('os').environ["DISPLAY"] = ":99"

    _cursor["x"] = 960.0
    _cursor["y"] = 540.0

    browser = None
    try:
        import nodriver as uc
        browser = await uc.start(
            user_data_dir=str(profile_dir),
            browser_args=[
                "--window-size=1920,1080",
                "--window-position=-2000,0",   # off-screen — no visible para el usuario
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = await browser.get("about:blank")
        await _inject_stealth(page)

        # Warm-up orgánico: for-you page durante 60-120s como usuario normal
        page = await browser.get("https://www.tiktok.com")
        await _delay(4.0, 7.0)
        # Simular lectura del for-you feed antes de buscar nada
        await _simulate_reading(page, duration_s=random.triangular(55.0, 120.0, 80.0))
        await _organic_pause(page, 5.0, 18.0)
        await _random_mouse_wander(page)

        # ── fyp_only: solo navegar el For You Page sin buscar (patrón casual) ────
        if session_mode == "fyp_only":
            logger.info("  TikTok: sesión FYP-only — solo navegando sin buscar")
            await _simulate_reading(page, duration_s=random.triangular(120.0, 240.0, 170.0))
            await _organic_pause(page, 10.0, 30.0)
            # Dar algunos likes desde el FYP (JS click en botón like visible)
            for _ in range(random.randint(2, 5)):
                try:
                    liked = await page.evaluate("""(function() {
                        var btns = document.querySelectorAll('button[data-e2e="like-icon"]');
                        if (btns.length) { btns[0].click(); return true; }
                        return false;
                    })()""")
                    if liked:
                        await _delay(1.5, 4.0)
                        await _scroll(page, random.randint(200, 500))
                        await _delay(2.0, 5.0)
                except Exception:
                    pass
            return results  # sesión terminada

        # ── Buscar y engajar en videos del nicho ──────────────────────────────
        # reply_focus: todavía hace warm-up pero luego solo responde canal propio
        if session_mode != "reply_focus":
            boosted = _tt_memory_boosted_searches()
            keywords = boosted[:2] if len(boosted) >= 2 else boosted
        else:
            keywords = []  # skip búsqueda externa

        for keyword in keywords:
            if (_tt_daily(log, "likes") >= DAILY_LIKE_LIMIT and
                    _tt_daily(log, "comments") >= DAILY_COMMENT_LIMIT):
                break

            videos = await _search_tiktok_niche(browser, keyword)
            random.shuffle(videos)

            for video in videos[:4]:
                actions = await _engage_tiktok_video(browser, video, log)
                results["videos_watched"] += 1
                if actions["liked"]:
                    results["likes"] += 1
                if actions["commented"]:
                    results["comments"] += 1

                # Delay variable entre videos + pausa orgánica ocasional
                await _delay(random.triangular(20.0, 60.0, 35.0))
                if random.random() < 0.40:
                    # Volver al for-you a ver 1-2 videos antes del siguiente
                    await _organic_pause(None, 10.0, 30.0)

                if results["videos_watched"] >= 6:
                    break

        # ── Responder comentarios en propios TikToks ──────────────────────────
        username = getattr(config, "TIKTOK_USERNAME", "")
        if username:
            own_replies = await _reply_own_tiktok_comments(browser, log)
            results["own_replies"] += own_replies

    except Exception as e:
        logger.error(f"TikTok growth error: {e}", exc_info=True)
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    logger.info(
        f"TikTok sesión — 👁️ vistos: {results['videos_watched']} | "
        f"❤️ likes: {results['likes']} | 💬 comentarios: {results['comments']} | "
        f"↩️ replies propios: {results['own_replies']}"
    )
    try:
        from modules import agent_memory as _am
        _am.update_from_growth(results, "tiktok")
    except Exception:
        pass
    return results


def run_tiktok_growth() -> dict:
    """API pública — llamar desde main.py en la sesión de crecimiento."""
    logger.info("=== TIKTOK GROWTH AGENT — inicio ===")
    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_tiktok_growth_session_async())
        finally:
            try:
                loop.close()
            except Exception:
                pass
    else:
        return asyncio.run(_tiktok_growth_session_async())
