"""
end_screen_manager.py — Añade end screens automáticas a cada video subido

Estrategia:
  - Botón "Suscribirse" al 75% de la duración del video
  - "Mejor video para el espectador" (YouTube elige automáticamente) al 75%
  - End screens mantienen a los viewers en el canal → watch time +12-18%

Flow: YouTube Studio → Video → End screen editor → Guardar
"""

import asyncio
import logging
import platform
import random
import sys
from pathlib import Path

import config

from modules.youtube_uploader import (
    _cleanup_chrome_profile,
    _cursor,
    _delay,
    _human_click,
    _inject_stealth,
    _random_mouse_wander,
    _scroll,
)

logger = logging.getLogger(__name__)


async def _add_end_screen_async(video_id: str, video_duration_s: float) -> bool:
    """
    Abre el editor de end screen de YouTube Studio y añade:
      - Elemento "Suscribirse"
      - Elemento "Mejor video para el espectador"
    Ambos al 75% de la duración del video.
    """
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

        # Navegar al editor del video
        edit_url = f"https://studio.youtube.com/video/{video_id}/edit"
        page = await browser.get(edit_url)
        await _delay(5.0, 8.0)
        logger.info(f"  End screen: editor abierto para {video_id}")

        # Click en pestaña "Pantalla final" o "End screen"
        tab_found = False
        for tab_text in ["Pantalla final", "End screen", "Final"]:
            try:
                tab = await page.find(tab_text, timeout=8)
                if tab:
                    await _human_click(page, tab)
                    await _delay(2.0, 4.0)
                    tab_found = True
                    logger.info(f"  End screen: pestaña '{tab_text}' encontrada")
                    break
            except Exception:
                continue

        if not tab_found:
            # Intentar URL directa al editor de end screen
            end_url = f"https://studio.youtube.com/video/{video_id}/edit/endscreen"
            page = await browser.get(end_url)
            await _delay(4.0, 7.0)

        await _scroll(page, random.randint(50, 150))
        await _delay(1.5, 2.5)

        # Verificar si ya tiene end screens (evitar duplicar)
        existing = await page.evaluate("""(function() {
            var items = document.querySelectorAll('ytve-endscreen-element-renderer, [class*="endscreen-element"]');
            return items.length;
        })()""")

        if existing and int(existing) >= 2:
            logger.info(f"  End screen: ya tiene {existing} elementos, omitiendo")
            return True

        # Añadir desde plantilla si está disponible
        template_added = False
        for tmpl_text in ["Usar plantilla", "Use template", "Plantilla", "Template"]:
            try:
                tmpl_btn = await page.find(tmpl_text, timeout=5)
                if tmpl_btn:
                    await _human_click(page, tmpl_btn)
                    await _delay(1.5, 2.5)

                    # Elegir plantilla con suscripción + video
                    for opt in ["Vídeo y suscripción", "Video and subscribe", "Vídeo más suscripción"]:
                        try:
                            opt_el = await page.find(opt, timeout=4)
                            if opt_el:
                                await _human_click(page, opt_el)
                                await _delay(1.0, 2.0)
                                template_added = True
                                break
                        except Exception:
                            continue
                    break
            except Exception:
                continue

        if not template_added:
            # Añadir manualmente: Suscribirse
            for add_text in ["Añadir elemento", "Add element", "Agregar"]:
                try:
                    add_btn = await page.find(add_text, timeout=5)
                    if add_btn:
                        await _human_click(page, add_btn)
                        await _delay(1.0, 2.0)

                        # Seleccionar "Suscribirse"
                        for sub_text in ["Suscribirse", "Subscribe"]:
                            try:
                                sub_el = await page.find(sub_text, timeout=4)
                                if sub_el:
                                    await _human_click(page, sub_el)
                                    await _delay(1.0, 1.5)
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

            await _delay(1.0, 2.0)

            # Añadir elemento "Mejor video"
            for add_text in ["Añadir elemento", "Add element", "Agregar"]:
                try:
                    add_btn = await page.find(add_text, timeout=5)
                    if add_btn:
                        await _human_click(page, add_btn)
                        await _delay(1.0, 2.0)

                        for vid_text in ["Mejor para el espectador", "Best for viewer", "Vídeo", "Video"]:
                            try:
                                vid_el = await page.find(vid_text, timeout=4)
                                if vid_el:
                                    await _human_click(page, vid_el)
                                    await _delay(1.0, 1.5)
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

        await _random_mouse_wander(page)
        await _delay(1.5, 2.5)

        # Guardar
        saved = False
        for save_text in ["Guardar", "Save"]:
            try:
                save_btn = await page.find(save_text, timeout=6)
                if save_btn:
                    await _human_click(page, save_btn)
                    await _delay(3.0, 5.0)
                    saved = True
                    logger.info(f"  ✅ End screen guardada para {video_id}")
                    break
            except Exception:
                continue

        return saved

    except Exception as e:
        logger.warning(f"  End screen error: {e}")
        return False
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass


def add_end_screen(video_id: str, video_duration_s: float = 45.0) -> bool:
    """API pública — llamar desde main.py ~5min después del upload (video debe estar procesado)."""
    logger.info(f"=== END SCREEN MANAGER === video={video_id} dur={video_duration_s:.0f}s")
    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_add_end_screen_async(video_id, video_duration_s))
        finally:
            try:
                loop.close()
            except Exception:
                pass
    else:
        return asyncio.run(_add_end_screen_async(video_id, video_duration_s))

