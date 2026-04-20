"""
growth_agent.py — Agente de crecimiento de canal

Estrategias implementadas:
  1. Comenta en videos del nicho (confesiones/drama, 10K-500K views, últimos 7 días)
     → Comentarios generados por IA, contextuales al título del video
  2. Responde a comentarios en tus propios videos
     → El algoritmo de YouTube premia el engagement del creador
  3. Deja comentario-pregunta pineado en cada video propio nuevo

Anti-detección: mismo stack que youtube_uploader
  - nodriver (sin WebDriver)
  - Stealth JS pre-carga
  - Bezier mouse + micro-jitter
  - Delays con distribución triangular
  - Tipeo humano con errores reales
  - Ve el video un tiempo antes de comentar

Límites seguros: máx 10 comentarios externos + 5 replies propios por día
"""

import asyncio
import json
import logging
import os
import platform
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import nodriver as uc

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from modules.youtube_uploader import (
    _cleanup_chrome_profile,
    _cursor,
    _delay,
    _human_click,
    _human_type,
    _inject_stealth,
    _random_mouse_wander,
    _scroll,
    _think,
)

logger = logging.getLogger(__name__)

# ─── Límites diarios (conservadores para no activar filtros de spam) ──────────

DAILY_EXTERNAL_LIMIT = 10
DAILY_OWN_LIMIT      = 5
GROWTH_LOG_FILE      = Path(__file__).parent.parent / "growth_log.json"

# ─── Keywords para buscar videos del nicho ────────────────────────────────────

NICHE_SEARCHES = [
    "confesión drama real español",
    "me traicionó historia real",
    "descubrí la verdad relato",
    "infidelidad historia verdadera",
    "me engañó mi pareja storytime",
    "relato real traición amor",
    "me mintió durante años historia",
    "secreto familiar revelado",
    "historia real venganza pareja",
    "confesiones dramáticas latinos",
]

# ─── Plantillas de comentarios (fallback sin Groq) ────────────────────────────
# Variadas para que no se repitan exactas — el bot elige una y la varía

_COMMENT_TEMPLATES = [
    "no puedo creer lo q acabo de ver... qué situación tan fuerte la verdad",
    "algo muy parecido le pasó a alguien q conozco y todavía no lo supera",
    "la parte de {kw} me dejó helado/a, no me lo esperaba para nada",
    "no sé cómo aguantó tanto, yo desde el primer momento me hubiera ido",
    "estas historias me tocan demasiado... hay traiciones q no se superan",
    "me quedé pensando en esto un buen rato, hay cosas q no tienen vuelta atrás",
    "qué historia... ¿y la otra persona nunca dio explicaciones de verdad?",
    "me recuerda algo q yo viví, uno nunca está listo pa ese momento",
    "el final me sorprendió 😮 creo q hizo lo correcto aunque duele",
    "historias así te demuestran q uno nunca conoce del todo a las personas",
    "bro qué fuerte esto, no me esperaba ese giro",
    "me pasó algo igual y aún no lo proceso 😶",
    "lo q más duele es q estas cosas pasan más de lo q creemos",
]

# Templates para comentario pineado en tus propios videos
_PIN_TEMPLATES = [
    "¿tú qué hubieras hecho en su lugar? cuéntame abajo 👇",
    "¿team confrontar o team irse en silencio? comenta",
    "¿crees q tomó la decisión correcta? quiero leer tu opinión",
    "¿a alguien más le pasó algo así? cuéntame tu historia 👇",
    "¿perdonar o alejarse para siempre? vota en los comentarios",
    "¿qué hubieras hecho diferente desde el inicio? 👇",
    "si llegaste hasta acá eres de los míos 🙌 cuéntame qué piensas",
    "comenta lo que sentiste y sígueme pa más historias así",
    "¿perdonarías o te irías sin mirar atrás? 👇 y sígueme si quieres más",
    "dime qué piensas y dale like si te llegó la historia 🙏",
]

# Fallback para respuestas propias (solo si Groq falla)
_REPLY_TEMPLATES = [
    "gracias por compartir eso, de verdad 🙏",
    "entiendo lo q dices, estas historias tocan fibras muy profundas",
    "exacto, por eso quise compartirla. mucha gente pasa por esto sin decirlo",
    "qué buen punto, yo también lo pensé cuando la narré",
    "gracias por comentar, me alegra q la historia haya llegado",
    "así es... y lo más duro es q le pasa a más gente de lo q creemos",
    "me alegra q lo hayas sentido así, esa es la idea 🙌",
    "qué fuerte lo q compartís, ojalá estés bien",
    "gracias por eso 🙏 sígueme pa más historias reales",
]


# ─── Evaluate seguro con reintentos ──────────────────────────────────────────

async def _eval_safe(page, js: str, retries: int = 5) -> any:
    """
    Wrapper de page.evaluate() con reintentos.
    IMPORTANTE: js debe ser expresión directa o IIFE — NO arrow fn suelta.
      ✓ "document.readyState"
      ✓ "(() => { return x; })()"
      ✗ "() => x"  ← esto devuelve el objeto función, no el valor
    """
    # Esperar a que la página cargue (expresión directa, no arrow fn)
    for _ in range(10):
        try:
            ready = await page.evaluate("document.readyState")
            if ready == "complete":
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)

    # Ejecutar con reintentos
    for attempt in range(retries):
        try:
            result = await page.evaluate(js)
            if result is not None:
                return result
        except Exception as e:
            logger.debug(f"  evaluate intento {attempt + 1}/{retries}: {e}")
        await asyncio.sleep(2.0)
    return None


# ─── Growth log ───────────────────────────────────────────────────────────────

def _load_log() -> dict:
    if GROWTH_LOG_FILE.exists():
        try:
            return json.loads(GROWTH_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"commented": {}, "daily": {}}


def _save_log(log: dict) -> None:
    GROWTH_LOG_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _daily_counts(log: dict) -> tuple[int, int]:
    d = log.get("daily", {}).get(_today(), {})
    return d.get("external", 0), d.get("own", 0)


def _inc(log: dict, kind: str) -> None:
    today = _today()
    log.setdefault("daily", {}).setdefault(today, {"external": 0, "own": 0})
    log["daily"][today][kind] += 1


def _already_commented(log: dict, video_id: str) -> bool:
    entry = log.get("commented", {}).get(video_id)
    if not entry:
        return False
    try:
        days_ago = (datetime.now() - datetime.strptime(entry["date"], "%Y-%m-%d")).days
        return days_ago < 30
    except Exception:
        return False


def _mark_commented(log: dict, video_id: str, title: str) -> None:
    log.setdefault("commented", {})[video_id] = {
        "date": _today(), "title": title[:80]
    }


# ─── Generador de comentarios con Groq ────────────────────────────────────────

async def _generate_comment(video_title: str) -> str:
    """Genera comentario orgánico contextual al título usando Groq."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        return _fallback_comment(video_title)

    include_cta = random.random() < 0.25  # 25% de comentarios con CTA suave
    cta_line = (
        "El comentario puede terminar con una frase corta como 'sígueme si te gusta este tipo de contenido' "
        "o 'te sigo' (natural, no forzado)."
        if include_cta else
        "NO incluyas auto-promoción ni links."
    )
    prompt = (
        f'Eres un espectador real de YouTube de Latinoamérica, escribiendo desde el celular. '
        f'Acabas de ver un video titulado: "{video_title}"\n'
        "Escribe UN comentario corto en español latino muy casual (máx 18 palabras, 1 frase).\n"
        "ESTILO OBLIGATORIO: habla de barrio, rápido, como WhatsApp. "
        "Usa 'q' en vez de 'que', 'pa' en vez de 'para', 'xq' para 'porque'. "
        "Omite el punto final. Sin mayúsculas al inicio si da más natural. "
        "Puedes poner algún error menor de tipeo real (letra cambiada, palabra repetida).\n"
        "Opciones: reacción emocional genuina, experiencia personal brevísima, o pregunta al creador.\n"
        f"{cta_line} PROHIBIDO: más de 1 emoji.\n"
        "Responde SOLO con el texto del comentario, sin comillas ni explicación."
    )

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 50,
                    "temperature": 0.95,
                },
            )
            r.raise_for_status()
            comment = (
                r.json()["choices"][0]["message"]["content"]
                .strip().strip('"').strip("'")
            )
            if len(comment) > 180:
                comment = comment[:180].rsplit(".", 1)[0] + "."
            return comment
    except Exception as e:
        logger.debug(f"Groq comentario falló: {e}")
        return _fallback_comment(video_title)


async def _generate_reply(comment_text: str) -> str:
    """Genera respuesta contextual al comentario de un espectador."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key or not comment_text.strip():
        return random.choice(_REPLY_TEMPLATES)

    include_cta = random.random() < 0.30
    cta_line = (
        "Puedes terminar con algo como 'sígueme pa más historias así' o 'gracias por seguirme 🙏' (natural)."
        if include_cta else ""
    )
    prompt = (
        "Eres el creador de un canal de YouTube de confesiones y dramas reales en español latino.\n"
        f'Un espectador comentó en tu video: "{comment_text}"\n'
        "Escribe UNA respuesta corta, cálida y auténtica (máx 20 palabras).\n"
        "REGLAS: responde DIRECTAMENTE a lo que dijo — no ignores su mensaje. "
        "Estilo casual de barrio, como WhatsApp, sin punto final. "
        "Puedes usar 'q', 'xq', 'pa', etc. Sin mayúsculas si suena más natural. "
        f"{cta_line}\n"
        "PROHIBIDO: repetir su comentario textual. PROHIBIDO: más de 1 emoji.\n"
        "Responde SOLO con el texto de la respuesta, sin comillas."
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.95,
                },
            )
            r.raise_for_status()
            reply = (
                r.json()["choices"][0]["message"]["content"]
                .strip().strip('"').strip("'")
            )
            if len(reply) > 200:
                reply = reply[:200].rsplit(" ", 1)[0]
            return reply
    except Exception as e:
        logger.debug(f"Groq reply falló: {e}")
        return random.choice(_REPLY_TEMPLATES)


def _fallback_comment(video_title: str) -> str:
    template = random.choice(_COMMENT_TEMPLATES)
    words = [w for w in video_title.split() if len(w) > 4 and not w.startswith("#")]
    kw = random.choice(words).lower() if words else "esa parte"
    comment = template.replace("{kw}", kw)
    endings = ["", "...", " en serio", " de verdad"]
    return comment + random.choice(endings)


# ─── Buscar videos del nicho ──────────────────────────────────────────────────

async def _search_niche_videos(browser, keyword: str, log: dict) -> list[dict]:
    """Busca en YouTube y retorna videos del nicho no comentados aún."""
    search_url = (
        "https://www.youtube.com/results?search_query="
        + keyword.replace(" ", "+")
    )
    try:
        page = await browser.get(search_url)
        await _delay(3.0, 6.0)
        await _dismiss_consent(page)
        await _scroll(page, random.randint(200, 450))
        await _random_mouse_wander(page)
        await _delay(1.5, 3.0)

        # nodriver no deserializa arrays correctamente — usar JSON.stringify y parsear en Python
        videos_json = await _eval_safe(page, """(function() {
            var results = [];
            var links = document.querySelectorAll(
                'a#video-title[href*="watch"], a#video-title-link[href*="watch"]'
            );
            for (var i = 0; i < links.length; i++) {
                try {
                    var a = links[i];
                    var title = a.getAttribute('title') || '';
                    var href  = a.href || '';
                    var m = href.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
                    if (m && title.length > 3)
                        results.push({id: m[1], title: title, url: 'https://www.youtube.com/watch?v=' + m[1]});
                } catch(e) {}
            }
            return JSON.stringify(results.slice(0, 20));
        })()""")

        try:
            videos_raw = json.loads(videos_json) if videos_json else []
        except Exception:
            videos_raw = []
        logger.info(f"  Videos brutos: {len(videos_raw)}")
        channel_name = getattr(config, "CHANNEL_NAME", "").lower()
        filtered = []
        for v in videos_raw:
            if not isinstance(v, dict):
                continue
            vid_id = v.get("id", "")
            title = v.get("title", "")
            if not vid_id or not title:
                continue
            if channel_name and channel_name in title.lower():
                continue
            if _already_commented(log, vid_id):
                continue
            filtered.append(v)

        return filtered[:6]

    except Exception as e:
        logger.warning(f"  Búsqueda fallida '{keyword}': {e}")
        return []


# ─── Comentar en video ajeno ──────────────────────────────────────────────────

async def _comment_on_video(browser, video: dict) -> bool:
    """Navega al video, lo 've' un rato, y deja un comentario humano."""
    try:
        logger.info(f"  [{video['title'][:55]}]")
        page = await browser.get(video["url"])
        await _delay(4.0, 8.0)

        # Ver el video (comportamiento humano — no comentar de inmediato)
        await _scroll(page, random.randint(100, 250))
        await _random_mouse_wander(page)
        watch_secs = random.uniform(14.0, 30.0)
        logger.debug(f"  Viendo {watch_secs:.0f}s...")
        await asyncio.sleep(watch_secs)

        # Scroll hacia los comentarios
        await _scroll(page, random.randint(300, 500))
        await _delay(2.0, 4.0)
        await _random_mouse_wander(page)

        # Caja de comentario
        comment_box = None
        for sel in [
            "#placeholder-area",
            "[aria-label='Agregar un comentario...']",
            "[aria-label='Add a comment...']",
        ]:
            try:
                comment_box = await page.select(sel, timeout=8)
                if comment_box:
                    break
            except Exception:
                pass

        if not comment_box:
            logger.warning("  Caja de comentarios no encontrada")
            return False

        await _human_click(page, comment_box)
        await _delay(1.5, 3.0)

        # Caja activa (contenteditable)
        active_box = None
        for sel in ["#contenteditable-root", "[contenteditable='true']"]:
            try:
                active_box = await page.select(sel, timeout=5)
                if active_box:
                    break
            except Exception:
                pass

        comment = await _generate_comment(video["title"])
        logger.info(f"  Comentario: {comment[:70]}")

        await _human_type(active_box or comment_box, comment, clear_first=False)
        await _delay(1.5, 3.5)
        await _random_mouse_wander(page)
        await _think()

        # Submit
        submitted = False
        for sel in [
            "#submit-button button",
            "button[aria-label='Comentar']",
            "button[aria-label='Comment']",
            "ytd-comment-simplebox-renderer #submit-button",
        ]:
            try:
                btn = await page.select(sel, timeout=5)
                if btn:
                    await _human_click(page, btn)
                    await _delay(3.0, 6.0)
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            logger.warning("  Botón submit no encontrado")
            return False

        log = _load_log()
        _mark_commented(log, video["id"], video["title"])
        _inc(log, "external")
        _save_log(log)
        logger.info("  ✓ Comentario publicado")
        return True

    except Exception as e:
        logger.warning(f"  Error comentando: {e}")
        return False


# ─── Obtener channel ID ───────────────────────────────────────────────────────

async def _get_channel_id(browser) -> str | None:
    """
    Extrae el channel ID (UCxxx) del canal logueado.
    Navega a studio.youtube.com y prueba 5 métodos distintos.
    """
    try:
        page = await browser.get("https://studio.youtube.com")
        await _delay(5.0, 8.0)
        await _random_mouse_wander(page)

        # Verificar URL actual — expresión directa (no arrow fn)
        current_url = await _eval_safe(page, "window.location.href") or ""
        logger.info(f"  Studio URL: {current_url[:90]}")

        if "accounts.google.com" in current_url or "signin" in current_url.lower():
            logger.warning(
                "  Sesión no activa. FIX: Abre Chrome con este perfil, loguea en\n"
                f"  studio.youtube.com y cierra Chrome:\n"
                f"  \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" "
                f"--user-data-dir=\"{config.CHROME_PROFILE_DIR}\""
            )
            return None

        # IIFE para extraer channel ID — 5 métodos en cascada
        channel_id = await _eval_safe(page, """(function() {
            var UC = /UC[A-Za-z0-9_-]{20,}/;
            try {
                if (typeof ytcfg !== 'undefined') {
                    var v = (ytcfg.data_ && ytcfg.data_.CHANNEL_ID) || (ytcfg.get && ytcfg.get('CHANNEL_ID'));
                    if (v && UC.test(v)) return v;
                }
                var html = document.documentElement.innerHTML;
                var m;
                m = html.match(/"CHANNEL_ID":"(UC[A-Za-z0-9_-]{20,})"/);  if (m) return m[1];
                m = html.match(/"externalId":"(UC[A-Za-z0-9_-]{20,})"/);  if (m) return m[1];
                m = html.match(/"channelId":"(UC[A-Za-z0-9_-]{20,})"/);   if (m) return m[1];
                m = html.match(/channel\/(UC[A-Za-z0-9_-]{20,})/);        if (m) return m[1];
            } catch(e) {}
            return null;
        })()""")

        if channel_id:
            logger.info(f"  Channel ID: {channel_id}")
            return str(channel_id)

        # Si nada funcionó, loguear la URL para debug
        logger.warning(f"  No se pudo extraer channel ID. URL: {current_url[:90]}")
        return None

    except Exception as e:
        logger.warning(f"  Error en _get_channel_id: {e}", exc_info=True)
        return None


# ─── Engagement en canal propio ───────────────────────────────────────────────

async def _engage_own_channel(browser, log: dict) -> int:
    """Pinea pregunta + responde comentarios en el video más reciente del canal. Retorna replies hechos."""
    own_done = 0
    try:
        # Obtener channel ID desde YouTube Studio
        channel_id = await _get_channel_id(browser)
        if not channel_id:
            logger.warning("  No se pudo obtener el channel ID — ¿sesión iniciada?")
            return
        logger.info(f"  Channel ID: {channel_id}")

        # Página PÚBLICA del canal — sin Shadow DOM, selectores estándar funcionan
        page = await browser.get(
            f"https://www.youtube.com/channel/{channel_id}/videos"
        )
        await _delay(5.0, 9.0)
        await _scroll(page, random.randint(100, 300))
        await _delay(2.0, 4.0)

        # Extraer ID del video/short más reciente — maneja Shorts (/shorts/ID) y videos normales
        vid_id = None
        vid_is_short = False
        for attempt in range(4):
            result_json = await page.evaluate("""(function() {
                var el, m;
                el = document.querySelector('a[href*="/shorts/"]');
                if (el && el.href) { m = el.href.match(/\\/shorts\\/([a-zA-Z0-9_-]{11})/); if (m) return JSON.stringify({id: m[1], short: true}); }
                el = document.querySelector('a#video-title-link[href*="watch"], a#video-title[href*="watch"], a[href*="watch?v="]');
                if (el && el.href) { m = el.href.match(/[?&]v=([a-zA-Z0-9_-]{11})/); if (m) return JSON.stringify({id: m[1], short: false}); }
                return null;
            })()""")
            try:
                parsed = json.loads(result_json) if result_json else None
                if parsed:
                    vid_id = parsed["id"]
                    vid_is_short = parsed.get("short", False)
                    break
            except Exception:
                pass
            logger.debug(f"  Intento {attempt + 1}/4 esperando hidratación...")
            await asyncio.sleep(4.0)

        if not vid_id:
            logger.warning("  No se encontró ningún video en el canal público")
            return

        # Siempre usar /watch?v= para comentar — Shorts UI tiene layout distinto
        video_url = f"https://www.youtube.com/watch?v={vid_id}"
        logger.info(f"  Video objetivo: {video_url} (Short: {vid_is_short})")

        # Navegar al video y verificar que esté disponible
        page = await browser.get(video_url)
        await _delay(4.0, 7.0)

        page_title = await page.evaluate("document.title || ''")
        unavailable_signals = ["unavailable", "not available", "eliminado", "no disponible", "private"]
        if any(s in (page_title or "").lower() for s in unavailable_signals):
            logger.warning(f"  Video {vid_id} no disponible ({page_title}) — saltando")
            return

        await _scroll(page, random.randint(150, 300))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.0)

        # 1. Comentario-pregunta para engagement
        pinned = await _leave_pin_comment(page)
        if pinned:
            own_done += 1
            _inc(log, "own")
            _save_log(log)
        await _delay(4.0, 8.0)

        # 2. Responder a comentarios existentes
        _, own_count = _daily_counts(log)
        if own_count < DAILY_OWN_LIMIT:
            own_done += await _reply_to_top_comments(page, log)

    except Exception as e:
        logger.warning(f"  Error en engagement propio: {e}")
    return own_done


async def _leave_pin_comment(page) -> bool:
    """Publica el comentario-pregunta en el video actual. Retorna True si lo publicó."""
    try:
        comment_box = None
        for sel in [
            "#placeholder-area",
            "[aria-label='Agregar un comentario...']",
            "[aria-label='Add a comment...']",
        ]:
            try:
                comment_box = await page.select(sel, timeout=8)
                if comment_box:
                    break
            except Exception:
                pass

        if not comment_box:
            logger.warning("  _leave_pin_comment: caja de comentario no encontrada")
            return False

        await _human_click(page, comment_box)
        await _delay(1.5, 3.0)

        active_box = None
        for sel in ["#contenteditable-root", "[contenteditable='true']"]:
            try:
                active_box = await page.select(sel, timeout=5)
                if active_box:
                    break
            except Exception:
                pass

        pin_text = random.choice(_PIN_TEMPLATES)
        await _human_type(active_box or comment_box, pin_text, clear_first=False)
        await _delay(1.5, 3.0)

        for sel in [
            "#submit-button button",
            "button[aria-label='Comentar']",
            "button[aria-label='Comment']",
        ]:
            try:
                btn = await page.select(sel, timeout=5)
                if btn:
                    await _human_click(page, btn)
                    await _delay(3.0, 5.0)
                    logger.info(f"  ✓ Comentario pineado: {pin_text}")
                    return True
            except Exception:
                pass

        logger.warning("  _leave_pin_comment: botón submit no encontrado")
        return False

    except Exception as e:
        logger.debug(f"  Error pineando comentario: {e}")
    return False


async def _reply_to_top_comments(page, log: dict) -> int:
    """Responde a los primeros comentarios del video actual con replies contextuales."""
    try:
        # Scroll para cargar comentarios
        await _scroll(page, random.randint(500, 800))
        await _delay(3.0, 5.0)

        # Extraer textos de comentarios — necesarios para generar replies contextuales
        comments_json = await page.evaluate("""(function() {
            var texts = [];
            var els = document.querySelectorAll('ytd-comment-thread-renderer #content-text');
            for (var i = 0; i < Math.min(els.length, 5); i++) {
                var t = (els[i].innerText || els[i].textContent || '').trim();
                texts.push(t.slice(0, 250));
            }
            return JSON.stringify(texts);
        })()""")
        try:
            comment_texts = json.loads(comments_json) if comments_json else []
        except Exception:
            comment_texts = []

        reply_btns = []
        for sel in ["[aria-label='Responder']", "[aria-label='Reply']"]:
            try:
                reply_btns = await page.select_all(sel, timeout=8)
                if reply_btns:
                    break
            except Exception:
                pass

        if not reply_btns:
            logger.debug("  No se encontraron botones de respuesta")
            return 0

        replied = 0
        for i, btn in enumerate(reply_btns[:3]):
            _, own_count = _daily_counts(log)
            if own_count >= DAILY_OWN_LIMIT:
                break

            try:
                comment_text = comment_texts[i] if i < len(comment_texts) else ""
                logger.debug(f"  Comentario a responder: {comment_text[:60]}")

                # Generar reply contextual con Groq
                reply_text = await _generate_reply(comment_text)

                await _human_click(page, btn)
                await _delay(1.5, 3.0)

                reply_box = None
                for sel in ["#contenteditable-root", "[contenteditable='true']"]:
                    try:
                        reply_box = await page.select(sel, timeout=5)
                        if reply_box:
                            break
                    except Exception:
                        pass

                if not reply_box:
                    continue

                await _human_type(reply_box, reply_text, clear_first=False)
                await _delay(1.5, 3.0)

                for sel in [
                    "#submit-button button",
                    "button[aria-label='Responder']",
                    "button[aria-label='Reply']",
                ]:
                    try:
                        sub = await page.select(sel, timeout=5)
                        if sub:
                            await _human_click(page, sub)
                            await _delay(3.0, 5.0)
                            _inc(log, "own")
                            _save_log(log)
                            replied += 1
                            logger.info(f"  ✓ Reply {replied}: {reply_text[:50]}")
                            break
                    except Exception:
                        pass

                # Pausa larga entre replies — humanos no responden en ráfaga
                await _delay(20.0, 45.0)

            except Exception as e:
                logger.debug(f"  Error en reply: {e}")

        return replied

    except Exception as e:
        logger.debug(f"  Error buscando comentarios: {e}")
    return 0


# ─── Sesión principal ─────────────────────────────────────────────────────────

async def _dismiss_consent(page) -> None:
    """Descarta el dialog de consentimiento de cookies de Google si aparece."""
    try:
        for text in ["Aceptar todo", "Accept all", "Reject all", "Rechazar todo"]:
            try:
                btn = await page.find(text, timeout=2)
                if btn:
                    await _human_click(page, btn)
                    await asyncio.sleep(1.5)
                    logger.debug("Consent dialog descartado")
                    return
            except Exception:
                pass
    except Exception:
        pass


async def _growth_session_async(do_own: bool = True) -> dict:
    log = _load_log()
    ext_count, own_count = _daily_counts(log)
    results = {"external": 0, "own": 0, "skipped": 0}

    if ext_count >= DAILY_EXTERNAL_LIMIT and own_count >= DAILY_OWN_LIMIT:
        logger.info("Límite diario de crecimiento alcanzado")
        return results

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"

    _cleanup_chrome_profile(profile_dir)

    chrome_bin = getattr(config, "CHROME_BINARY", "")
    if not chrome_bin:
        for candidate in [
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
        ]:
            if Path(candidate).exists():
                chrome_bin = candidate
                break

    # Resetear cursor al centro
    _cursor["x"] = 960.0
    _cursor["y"] = 540.0

    browser = None
    try:
        browser = await uc.start(
            user_data_dir=str(profile_dir),
            browser_executable_path=chrome_bin or None,
            browser_args=[
                "--start-maximized",
                "--window-size=1920,1080",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.get("about:blank")
        await _inject_stealth(page)

        # Warm-up natural — igual que youtube_uploader
        page = await browser.get("https://www.youtube.com")
        await _delay(3.0, 6.0)

        # Descartar consent dialog de Google/YouTube si aparece
        await _dismiss_consent(page)

        await _scroll(page, random.randint(150, 350))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.5)

        # ── Comentar en videos del nicho ──────────────────────────────────────
        if ext_count < DAILY_EXTERNAL_LIMIT:
            keywords = random.sample(NICHE_SEARCHES, k=min(3, len(NICHE_SEARCHES)))
            session_target = random.randint(3, 5)
            session_done = 0
            # `page` no se usa directamente aquí — cada función abre su propia navegación

            for keyword in keywords:
                if session_done >= session_target:
                    break
                log = _load_log()
                ext_count, _ = _daily_counts(log)
                if ext_count >= DAILY_EXTERNAL_LIMIT:
                    break

                logger.info(f"Búsqueda: '{keyword}'")
                videos = await _search_niche_videos(browser, keyword, log)

                for video in videos:
                    if session_done >= session_target:
                        break
                    ok = await _comment_on_video(browser, video)
                    if ok:
                        session_done += 1
                        results["external"] += 1
                    else:
                        results["skipped"] += 1

                    # Pausa larga entre comentarios — no los hace en ráfaga
                    await _delay(18.0, 40.0)

        # ── Engagement en canal propio ─────────────────────────────────────────
        if do_own:
            log = _load_log()
            _, own_count = _daily_counts(log)
            if own_count < DAILY_OWN_LIMIT:
                logger.info("Iniciando engagement en canal propio...")
                results["own"] += await _engage_own_channel(browser, log)

    except Exception as e:
        logger.error(f"Error en sesión de crecimiento: {e}", exc_info=True)
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    logger.info(
        f"Sesión terminada — externos: {results['external']} | "
        f"propios: {results['own']} | omitidos: {results['skipped']}"
    )
    return results


# ─── API pública ──────────────────────────────────────────────────────────────

def run_growth_session(do_own: bool = True) -> dict:
    """
    Ejecuta una sesión de crecimiento.
    Se llama desde main.py después de cada upload y 2x por día extra.
    """
    logger.info("=== GROWTH AGENT — inicio de sesión ===")

    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_growth_session_async(do_own=do_own))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)
    else:
        return asyncio.run(_growth_session_async(do_own=do_own))
