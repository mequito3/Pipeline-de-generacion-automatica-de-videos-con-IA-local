"""
playlist_manager.py — Gestión automática de playlists en YouTube Studio

Estrategia:
  - Categoriza cada video según su título/tema
  - Crea la playlist si no existe, añade el video si ya existe
  - YouTube promociona canales con playlists activas (mayor watch time)

Categorías automáticas:
  Traiciones de Pareja 💔 | Secretos Familiares 🤫 | Amigos que Traicionan 😤
  Relaciones Tóxicas ⚠️  | Confesiones Impactantes 😱 | Dinero y Estafas 💸
"""

import asyncio
import logging
import platform
import random
import re
import sys
from pathlib import Path

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

# ─── Categorías y sus palabras clave ─────────────────────────────────────────

PLAYLIST_CATEGORIES = [
    {
        "name": "Traiciones de Pareja 💔",
        "keywords": [
            "engañó", "engaño", "infidelidad", "infiel", "traición", "traicionó",
            "cuernos", "pareja", "novio", "novia", "esposo", "esposa", "amante",
            "celular", "mensaje", "mentiras", "mintió", "doble vida",
        ],
        "description": "Las historias de traición más impactantes del canal. ¿Tú lo hubieras perdonado?",
    },
    {
        "name": "Secretos Familiares 🤫",
        "keywords": [
            "familia", "familiar", "madre", "padre", "hermano", "hermana",
            "secreto", "adoptado", "herencia", "padres", "abuela", "abuelo",
            "suegra", "cuñado", "cuñada", "parentesco", "origen",
        ],
        "description": "Los secretos que las familias esconden durante años. Historias que te dejarán sin palabras.",
    },
    {
        "name": "Amigos que Traicionan 😤",
        "keywords": [
            "amigo", "amiga", "amistad", "mejor amiga", "mejor amigo",
            "traicionó", "robó", "usó", "aprovechó", "fingió", "fingía",
            "grupo", "falsas", "falsos",
        ],
        "description": "Cuando la persona en quien más confiabas te falla. Historias reales de traición entre amigos.",
    },
    {
        "name": "Relaciones Tóxicas ⚠️",
        "keywords": [
            "tóxica", "tóxico", "narcisista", "control", "manipuló", "manipulación",
            "abuso", "gaslight", "celoso", "celosa", "acoso", "amenazó",
            "dependencia", "aisló", "manipulaba",
        ],
        "description": "Historias de relaciones donde el amor se convirtió en control. ¿Lo reconocerías a tiempo?",
    },
    {
        "name": "Dinero y Estafas 💸",
        "keywords": [
            "dinero", "robó", "estafa", "ahorros", "deuda", "préstamo",
            "negocio", "socio", "trabajo", "jefe", "despidieron", "ilegal",
            "perdí todo", "dinero",
        ],
        "description": "Cuando el dinero destruye relaciones. Estafas, robos y traiciones económicas reales.",
    },
    {
        "name": "Confesiones Impactantes 😱",
        "keywords": [],  # Categoría por defecto — recoge todo lo que no encaja arriba
        "description": "Las confesiones más dramáticas e impactantes que recibirás en tu feed. Activa las notificaciones 🔔",
    },
]


def _classify_video(title: str) -> dict:
    """Determina a qué playlist pertenece un video según su título."""
    title_lower = title.lower()
    scores = []
    for cat in PLAYLIST_CATEGORIES[:-1]:  # excluir la categoría por defecto
        score = sum(1 for kw in cat["keywords"] if kw in title_lower)
        scores.append((score, cat))

    scores.sort(key=lambda x: x[0], reverse=True)
    if scores and scores[0][0] > 0:
        return scores[0][1]
    return PLAYLIST_CATEGORIES[-1]  # por defecto: Confesiones Impactantes


# ─── Automatización YouTube Studio ───────────────────────────────────────────

async def _get_existing_playlists(page) -> dict[str, str]:
    """
    Devuelve dict {nombre_playlist: playlist_id} de las playlists existentes.
    Scraping de la página de playlists en Studio.
    """
    playlists = {}
    try:
        items = await page.evaluate("""(function() {
            var result = {};
            var rows = document.querySelectorAll('ytcp-ve[entity-type="PLAYLIST"]');
            rows.forEach(function(row) {
                var title = row.querySelector('#playlist-title');
                var link  = row.querySelector('a[href*="playlist"]');
                if (title && link) {
                    var m = link.href.match(/list=([A-Za-z0-9_-]+)/);
                    if (m) result[title.innerText.trim()] = m[1];
                }
            });
            // Fallback: buscar por otro selector
            if (Object.keys(result).length === 0) {
                var links = document.querySelectorAll('a[href*="list="]');
                links.forEach(function(a) {
                    var m = a.href.match(/list=([A-Za-z0-9_-]+)/);
                    var t = a.innerText.trim() || a.title || '';
                    if (m && t) result[t] = m[1];
                });
            }
            return JSON.stringify(result);
        })()""")
        if items:
            playlists = __import__('json').loads(items)
    except Exception as e:
        logger.debug(f"  _get_existing_playlists: {e}")
    return playlists


async def _create_playlist(page, name: str, description: str) -> str | None:
    """Crea una nueva playlist en YouTube Studio. Retorna el ID o None."""
    try:
        # Buscar botón "Nueva playlist"
        for btn_text in ["Nueva playlist", "New playlist", "Crear playlist"]:
            try:
                btn = await page.find(btn_text, timeout=6)
                if btn:
                    await _human_click(page, btn)
                    await _delay(1.5, 2.5)
                    break
            except Exception:
                continue

        # Rellenar nombre
        name_field = None
        for sel in ["input[placeholder*='título']", "input[placeholder*='itle']", "#playlist-title-input"]:
            try:
                name_field = await page.select(sel, timeout=6)
                if name_field:
                    break
            except Exception:
                continue

        if not name_field:
            logger.warning("  Playlist: no se encontró campo de nombre")
            return None

        await _human_click(page, name_field)
        await _human_type(name_field, name)
        await _delay(0.8, 1.5)

        # Descripción (si hay campo)
        try:
            desc_field = await page.select("textarea[placeholder*='scripción']", timeout=3)
            if desc_field:
                await _human_click(page, desc_field)
                await _human_type(desc_field, description)
                await _delay(0.5, 1.0)
        except Exception:
            pass

        # Privacidad: Pública
        try:
            priv_btn = await page.find("Pública", timeout=3)
            if not priv_btn:
                priv_btn = await page.find("Public", timeout=3)
            if priv_btn:
                await _human_click(page, priv_btn)
                await _delay(0.5, 1.0)
        except Exception:
            pass

        # Crear
        for btn_text in ["Crear", "Create", "Guardar", "Save"]:
            try:
                create_btn = await page.find(btn_text, timeout=5)
                if create_btn:
                    await _human_click(page, create_btn)
                    await _delay(3.0, 5.0)
                    break
            except Exception:
                continue

        # Extraer ID de la URL actual
        url = await page.evaluate("window.location.href")
        m = re.search(r"list=([A-Za-z0-9_-]+)", url or "")
        if m:
            logger.info(f"  ✅ Playlist creada: '{name}' → {m.group(1)}")
            return m.group(1)

        logger.warning(f"  Playlist '{name}' creada pero no se obtuvo ID")
        return None

    except Exception as e:
        logger.warning(f"  _create_playlist error: {e}")
        return None


async def _add_video_to_playlist(browser, video_id: str, playlist_id: str, playlist_name: str) -> bool:
    """Añade un video a una playlist desde su página en YouTube Studio."""
    try:
        edit_url = f"https://studio.youtube.com/video/{video_id}/edit"
        page = await browser.get(edit_url)
        await _delay(5.0, 8.0)

        # Buscar sección de playlists
        playlist_section = None
        for sel in [
            "ytcp-video-metadata-playlists",
            "[test-id='playlists']",
            "div[aria-label*='Playlist']",
            "div[aria-label*='Lista']",
        ]:
            try:
                playlist_section = await page.select(sel, timeout=6)
                if playlist_section:
                    break
            except Exception:
                continue

        if playlist_section:
            await _human_click(page, playlist_section)
            await _delay(1.0, 2.0)

        # Buscar el checkbox de la playlist
        found = await page.evaluate(f"""(function() {{
            var labels = document.querySelectorAll('label, [role="checkbox"]');
            for (var el of labels) {{
                if ((el.innerText || '').includes('{playlist_name.split(" ")[0]}')) {{
                    el.click();
                    return true;
                }}
            }}
            return false;
        }})()""")

        if found:
            await _delay(0.8, 1.5)
            # Guardar
            for btn_text in ["Guardar", "Save", "Hecho", "Done"]:
                try:
                    btn = await page.find(btn_text, timeout=4)
                    if btn:
                        await _human_click(page, btn)
                        await _delay(2.0, 4.0)
                        break
                except Exception:
                    continue
            logger.info(f"  ✅ Video {video_id} añadido a '{playlist_name}'")
            return True

        logger.warning(f"  Playlist '{playlist_name}' no encontrada en el editor del video")
        return False

    except Exception as e:
        logger.warning(f"  _add_video_to_playlist error: {e}")
        return False


async def _manage_playlists_async(video_id: str, video_title: str) -> bool:
    """Sesión completa: clasificar video y añadirlo a la playlist correcta."""
    category = _classify_video(video_title)
    logger.info(f"=== PLAYLIST MANAGER ===")
    logger.info(f"  Video: '{video_title}' → categoría: '{category['name']}'")

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Linux" and not __import__('os').environ.get("DISPLAY"):
        __import__('os').environ["DISPLAY"] = ":99"

    _cleanup_chrome_profile(profile_dir)
    _cursor["x"] = 960.0
    _cursor["y"] = 540.0

    browser = None
    try:
        import nodriver as uc
        browser = await uc.start(
            user_data_dir=str(profile_dir),
            browser_args=[
                "--window-size=1920,1080",
                "--window-position=-2000,0",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.get("about:blank")
        await _inject_stealth(page)
        page = await browser.get("https://studio.youtube.com")
        await _delay(4.0, 7.0)

        # Listar playlists existentes
        playlists_url = "https://studio.youtube.com/channel/mine/playlists"
        page = await browser.get(playlists_url)
        await _delay(4.0, 7.0)
        await _scroll(page, random.randint(100, 300))
        await _delay(2.0, 3.0)

        existing = await _get_existing_playlists(page)
        logger.info(f"  Playlists existentes: {list(existing.keys())}")

        if category["name"] not in existing:
            logger.info(f"  Creando nueva playlist: '{category['name']}'")
            pid = await _create_playlist(page, category["name"], category["description"])
            if pid:
                existing[category["name"]] = pid
        else:
            logger.info(f"  Playlist ya existe: '{category['name']}'")

        # Añadir video a la playlist
        ok = await _add_video_to_playlist(browser, video_id, existing.get(category["name"], ""), category["name"])
        return ok

    except Exception as e:
        logger.error(f"  Playlist manager error: {e}", exc_info=True)
        return False
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass


def add_video_to_playlist(video_id: str, video_title: str) -> bool:
    """API pública — llamar desde main.py después del upload."""
    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_manage_playlists_async(video_id, video_title))
        finally:
            try:
                loop.close()
            except Exception:
                pass
    else:
        return asyncio.run(_manage_playlists_async(video_id, video_title))

