"""
tiktok_uploader.py — Sube videos a TikTok via automatización del navegador

Usa nodriver (mismo approach que youtube_uploader) para subir a:
  https://www.tiktok.com/tiktok-studio/upload

Requiere sesión activa de TikTok en el perfil de Chrome configurado.
"""

import asyncio
import logging
import random
import sys
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/upload"
TIKTOK_CHROME_PROFILE = str(Path(config.BASE_DIR) / "chrome_profile_tiktok")


async def _delay(min_s: float, max_s: float) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_type(element, text: str) -> None:
    for char in text:
        await element.send_keys(char)
        await asyncio.sleep(random.uniform(0.04, 0.12))


async def _upload_async(
    video_path: Path,
    caption: str,
) -> tuple[bool, str]:
    import nodriver as uc

    profile_dir = Path(TIKTOK_CHROME_PROFILE)
    profile_dir.mkdir(parents=True, exist_ok=True)

    browser = await uc.start(
        user_data_dir=str(profile_dir),
        browser_args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        headless=False,
    )

    try:
        # Warm-up breve antes de ir a la página de subida
        page = await browser.get("https://www.tiktok.com")
        await _delay(3, 5)

        page = await browser.get(TIKTOK_UPLOAD_URL)
        await _delay(5, 9)

        # ── Verificar que estamos logueados ──────────────────────────────────
        current_url = page.url or ""
        if any(x in current_url for x in ["login", "passport", "signup", "register"]):
            logger.error(
                "TikTok: no hay sesion activa.\n"
                f"  1. Abre Chrome manualmente con: chrome.exe --user-data-dir=\"{profile_dir}\"\n"
                "  2. Ve a tiktok.com e inicia sesion\n"
                "  3. Cierra Chrome y vuelve a ejecutar el pipeline."
            )
            return False, ""

        logger.info(f"TikTok Studio cargado: {current_url[:70]}")
        await _delay(2, 4)

        # ── Subir archivo ─────────────────────────────────────────────────────
        logger.info(f"TikTok: buscando input de archivo para {video_path.name}...")
        file_input = None
        # TikTok Studio puede tener el input dentro de un iframe o en el DOM principal
        for selector in [
            "input[type='file']",
            "input[accept*='video']",
            "input[accept*='mp4']",
            "input[name='file']",
        ]:
            try:
                file_input = await page.select(selector, timeout=12)
                if file_input:
                    logger.info(f"  Input encontrado con selector: {selector}")
                    break
            except Exception:
                pass

        # Si no encontró input visible, buscar en iframes
        if not file_input:
            try:
                frames = await page.query_selector_all("iframe")
                for frame in frames[:3]:
                    try:
                        frame_content = await frame.content_frame()
                        if frame_content:
                            file_input = await frame_content.query_selector("input[type='file']")
                            if file_input:
                                logger.info("  Input encontrado dentro de iframe")
                                break
                    except Exception:
                        pass
            except Exception:
                pass

        if not file_input:
            logger.error("TikTok: no se encontro el input de archivo — puede que la UI haya cambiado")
            return False, ""

        await file_input.send_file(str(video_path.absolute()))
        logger.info("TikTok: archivo enviado — esperando que termine de subirse...")

        # ── Esperar que el video termine de subirse (hasta 5 min) ────────────
        upload_done = False
        for _ in range(60):
            await asyncio.sleep(5)
            try:
                page_text = await page.evaluate("document.body.innerText")
                if "Cargado" in page_text or "Uploaded" in page_text:
                    logger.info("TikTok: video completamente subido")
                    upload_done = True
                    break
                # Mostrar progreso si hay porcentaje
                import re as _re
                m = _re.search(r'(\d+(?:\.\d+)?)\s*%', page_text)
                if m:
                    logger.info(f"TikTok: subiendo... {m.group(1)}%")
            except Exception:
                pass
        if not upload_done:
            logger.warning("TikTok: no se confirmó upload completo — continuando igual")

        await _delay(2, 3)

        # ── Caption / descripción ─────────────────────────────────────────────
        caption_input = None
        for selector in [
            "[data-e2e='caption-input']",
            ".caption-input",
            "div[contenteditable='true']",
            "textarea",
        ]:
            try:
                caption_input = await page.select(selector, timeout=8)
                if caption_input:
                    break
            except Exception:
                pass

        if caption_input:
            await caption_input.click()
            await _delay(0.5, 1.0)
            # Seleccionar todo el texto existente (TikTok pre-rellena con el nombre del archivo)
            # El primer carácter que escribimos reemplaza la selección automáticamente
            await page.evaluate("""(function() {
                var el = document.querySelector("div[contenteditable='true']") ||
                         document.querySelector("textarea");
                if (el) { el.focus(); document.execCommand('selectAll', false, null); }
            })()""")
            await _delay(0.2, 0.4)
            caption_trimmed = caption[:2190]
            await _human_type(caption_input, caption_trimmed)
            logger.info(f"TikTok: caption escrito ({len(caption_trimmed)} chars)")
            await _delay(2.0, 3.0)
        else:
            logger.warning("TikTok: no se encontró el campo de caption")

        # ── Botón Publicar — click via JavaScript por texto exacto ───────────
        await _delay(1.5, 2.5)
        clicked = await page.evaluate("""(function() {
            var buttons = document.querySelectorAll('button');
            for (var btn of buttons) {
                var txt = btn.innerText.trim();
                if (txt === 'Publicar' || txt === 'Post' || txt === 'Subir' || txt === 'Upload') {
                    btn.click();
                    return txt;
                }
            }
            return null;
        })()""")

        if clicked:
            logger.info(f"TikTok: botón '{clicked}' clickeado — esperando confirmación...")
        else:
            logger.error("TikTok: no se encontró el botón de publicar")
            return False, ""

        # ── Detectar éxito: esperar que la URL cambie o aparezca confirmación ─
        tiktok_url = ""
        for _ in range(24):  # hasta 2 min
            await asyncio.sleep(5)
            try:
                current = page.url or ""
                body = await page.evaluate("document.body.innerText")
                # TikTok redirige a /creator-center o cambia de URL al publicar
                if "upload" not in current.lower():
                    tiktok_url = "https://www.tiktok.com/@" + getattr(config, "TIKTOK_USERNAME", "")
                    logger.info(f"TikTok: publicado — URL cambió a {current[:60]}")
                    break
                # O detectar mensaje de éxito en el DOM
                for kw in ["publicado", "posted", "subido con éxito", "video uploaded"]:
                    if kw.lower() in body.lower():
                        tiktok_url = "https://www.tiktok.com/@" + getattr(config, "TIKTOK_USERNAME", "")
                        logger.info(f"TikTok: publicación confirmada ('{kw}' detectado)")
                        break
                if tiktok_url:
                    break
            except Exception:
                pass

        if tiktok_url:
            return True, tiktok_url

        logger.warning("TikTok: no se pudo confirmar la publicación")
        return False, ""

    except Exception as e:
        logger.error(f"TikTok upload error: {e}")
        return False, ""
    finally:
        try:
            await browser.stop()
        except Exception:
            pass


def upload_to_tiktok(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
) -> str | None:
    """
    Sube un video a TikTok Studio.

    Args:
        video_path:  Ruta al archivo MP4
        title:       Título del video
        description: Descripción breve
        tags:        Lista de hashtags

    Returns:
        URL del perfil de TikTok si tuvo éxito, None si falló.
    """
    vp = Path(video_path)
    if not vp.exists():
        logger.error(f"TikTok: archivo no encontrado: {video_path}")
        return None

    # Caption: título + descripción + hashtags (límite 2200 chars)
    hashtag_str = " ".join(t if t.startswith("#") else f"#{t}" for t in tags[:10])
    caption = f"{title}\n\n{description}\n\n{hashtag_str}"

    logger.info(f"TikTok: iniciando upload de '{vp.name}'")

    try:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                ok, url = loop.run_until_complete(_upload_async(vp, caption))
            finally:
                try:
                    loop.run_until_complete(asyncio.sleep(0))
                except Exception:
                    pass
                loop.close()
                asyncio.set_event_loop(None)
        else:
            ok, url = asyncio.run(_upload_async(vp, caption))

        if ok:
            logger.info(f"TikTok: video publicado → {url}")
            _notify_whatsapp(title, url)
            return url
        else:
            logger.error("TikTok: upload falló")
            return None

    except Exception as e:
        logger.error(f"TikTok: error inesperado: {e}")
        return None


def _notify_whatsapp(title: str, tiktok_url: str) -> None:
    try:
        from twilio.rest import Client  # type: ignore
        account_sid = getattr(config, "TWILIO_ACCOUNT_SID", "")
        auth_token  = getattr(config, "TWILIO_AUTH_TOKEN", "")
        from_number = getattr(config, "TWILIO_WHATSAPP_FROM", "")
        to_number   = getattr(config, "WHATSAPP_TO", "")
        if not all([account_sid, auth_token, from_number, to_number]):
            return
        client = Client(account_sid, auth_token)
        body = (
            f"🎵 *TIKTOK PUBLICADO* — {getattr(config, 'CHANNEL_NAME', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"_{title}_\n\n"
            f"🔗 {tiktok_url}\n\n"
            f"_(Mensaje automático — Shorts Factory)_"
        )
        client.messages.create(
            from_=f"whatsapp:{from_number}",
            to=f"whatsapp:{to_number}",
            body=body,
        )
        logger.info("TikTok: notificacion WhatsApp enviada")
    except Exception as e:
        logger.warning(f"TikTok: notificacion WhatsApp fallo (no critico): {e}")
