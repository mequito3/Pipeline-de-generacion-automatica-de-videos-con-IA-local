"""
youtube_uploader.py -- Sube videos a YouTube Studio (nodriver, sin WebDriver)

nodriver usa Chrome DevTools Protocol directamente -- sin protocolo WebDriver,
invisible para los sistemas de bot-detection de Google/YouTube.

Requiere: pip install nodriver
"""

import asyncio
import json as _json
import logging
import random
import sys
import time
from pathlib import Path

import nodriver as uc

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)


# --- Helpers de comportamiento humano (async) ---------------------------------


async def _delay(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa aleatoria con distribucion triangular (mas realista que uniform)."""
    await asyncio.sleep(random.triangular(min_s, max_s, (min_s + max_s) / 2))


async def _human_type(element, text: str, clear_first: bool = True) -> None:
    """
    Escribe texto caracter a caracter usando document.execCommand('insertText').

    Esto garantiza que cada caracter va al elemento correcto (titulo o descripcion)
    incluso si YouTube Studio intenta mover el foco durante la escritura.
    Evita el problema de send_keys() que envia al elemento activo global del tab.
    """
    # Limpiar y dar foco inicial al campo
    # Usamos selectAll+delete para contenteditable (mas confiable que textContent='')
    if clear_first:
        await element.apply(
            "(el) => {"
            "  el.focus();"
            "  document.execCommand('selectAll', false, null);"
            "  document.execCommand('delete', false, null);"
            "}"
        )
    else:
        await element.apply("(el) => el.focus()")
    await asyncio.sleep(random.uniform(0.4, 0.8))

    chars_since_pause = 0
    next_pause_at = random.randint(10, 25)

    for char in text:
        # Serializar el caracter correctamente para JS (maneja \n, comillas, etc.)
        char_json = _json.dumps(char)
        # Cada caracter: re-foco el elemento y lo inserta via execCommand.
        # El re-foco evita que YouTube Studio robe el foco entre caracteres.
        await element.apply(
            "(el) => { el.focus(); document.execCommand('insertText', false, "
            + char_json
            + "); }"
        )

        # Velocidad variable segun tipo de caracter (~60 WPM promedio)
        if char == " ":
            await asyncio.sleep(random.uniform(0.08, 0.22))
        elif char in ".,;:!?\n":
            await asyncio.sleep(random.uniform(0.14, 0.42))
        else:
            await asyncio.sleep(random.uniform(0.05, 0.18))

        # Pausa de "pensar" periodica para simular ritmo humano
        chars_since_pause += 1
        if chars_since_pause >= next_pause_at:
            await asyncio.sleep(random.uniform(0.4, 1.5))
            chars_since_pause = 0
            next_pause_at = random.randint(10, 25)


async def _scroll(page, amount: int = 200) -> None:
    """Scroll suave simulando lectura de pagina."""
    try:
        await page.evaluate(
            f"window.scrollBy({{top: {amount}, behavior: 'smooth'}})"
        )
        await asyncio.sleep(random.uniform(0.5, 1.2))
    except Exception:
        pass


# --- Logica principal de upload (async) --------------------------------------


async def _wait_upload_complete(page, timeout: int = 300) -> None:
    """
    Espera a que el video termine de subirse.
    Detecta cuando el boton 'Guardar' (done-button) aparece habilitado.
    """
    logger.info("Esperando que el video termine de subirse...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            done = await page.select("ytcp-button#done-button", timeout=3)
            if done:
                logger.info("Upload completo -- boton Guardar disponible")
                return
        except Exception:
            pass
        await asyncio.sleep(5)
        elapsed = int(time.time() - start)
        if elapsed % 30 == 0:
            logger.info(f"Esperando upload... ({elapsed}s)")
    logger.warning(f"Timeout esperando upload ({timeout}s)")


async def _upload_async(
    video_path: Path,
    title: str,
    description: str,
    tags: list,
) -> tuple[bool, str]:
    """
    Logica asincrona completa de upload con nodriver (CDP).
    Returns (success, youtube_url)  — url es "" si no se pudo capturar.
    """
    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    browser = await uc.start(
        user_data_dir=str(profile_dir),
        browser_args=[
            "--start-maximized",
            "--window-size=1920,1080",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    page = None
    try:
        page = await browser.get(config.YOUTUBE_STUDIO_URL)
        await _delay(5.0, 10.0)

        # Verificar sesion activa
        current_url = page.url or ""
        if "accounts.google.com" in current_url:
            logger.error(
                "No hay sesion activa en YouTube Studio.\n"
                "  Abre Chrome manualmente, entra a studio.youtube.com, "
                "inicia sesion y cierra Chrome. El perfil guardara las cookies."
            )
            return False, ""

        logger.info(f"YouTube Studio cargado: {current_url[:60]}")

        # Comportamiento humano: leer el dashboard antes de actuar
        await _scroll(page, 150)
        await _delay(1.5, 3.0)
        await _scroll(page, -80)
        await _delay(2.0, 5.0)

        # -- Click en "Crear" --------------------------------------------------
        logger.info("Buscando boton 'Crear'...")
        create_btn = None
        for selector in [
            "ytcp-button#create-icon",
            "[aria-label*='Crear']",
            "[aria-label*='Create']",
        ]:
            try:
                create_btn = await page.select(selector, timeout=8)
                if create_btn:
                    break
            except Exception:
                pass

        if not create_btn:
            try:
                create_btn = await page.find("Crear", timeout=10)
            except Exception:
                pass

        if not create_btn:
            logger.error("No se encontro el boton 'Crear'")
            return False, ""

        await _delay(1.5, 3.5)
        await create_btn.click()
        await _delay(1.5, 3.0)

        # -- Click en "Subir videos" -------------------------------------------
        logger.info("Buscando opcion 'Subir videos'...")
        upload_opt = None
        for text in ["Subir v\u00eddeos", "Subir videos", "Upload videos", "Upload"]:
            try:
                upload_opt = await page.find(text, timeout=6)
                if upload_opt:
                    break
            except Exception:
                pass

        if not upload_opt:
            logger.error("No se encontro la opcion 'Subir videos'")
            return False, ""

        await _delay(0.8, 1.8)
        await upload_opt.click()
        await _delay(2.0, 4.0)

        # -- Seleccionar archivo -----------------------------------------------
        logger.info(f"Cargando archivo: {video_path.name}")
        file_input = await page.select("input[type='file']", timeout=20)
        await file_input.send_file(str(video_path.absolute()))
        logger.info("Archivo enviado -- esperando modal de detalles...")
        await _delay(4.0, 8.0)

        # -- Titulo ------------------------------------------------------------
        logger.info("Escribiendo titulo...")
        title_input = await page.select(
            "#title-textarea #textbox", timeout=30
        )
        await _delay(1.5, 3.0)
        await _human_type(title_input, title)
        await _delay(2.0, 4.5)

        # -- Descripcion + hashtags --------------------------------------------
        logger.info("Escribiendo descripcion...")
        # Re-query para evitar referencia vieja tras typing del titulo
        desc_input = await page.select(
            "#description-textarea #textbox", timeout=15
        )
        hashtags_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in (tags or [])
        )
        full_desc = f"{description}\n\n{hashtags_str}" if hashtags_str else description
        await _delay(1.5, 3.0)
        await _human_type(desc_input, full_desc)
        await _delay(2.5, 5.5)
        await _scroll(page, 200)

        # -- Audiencia: No es para ninos --------------------------------------
        logger.info("Seleccionando audiencia...")
        try:
            not_kids = await page.select(
                "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
                timeout=10,
            )
            if not_kids:
                await _delay(1.0, 2.5)
                await not_kids.click()
        except Exception:
            logger.warning("No se encontro opcion de audiencia, continuando...")
        await _delay(2.0, 4.0)

        # -- Siguiente x3 (pasos 1->2->3->4) ----------------------------------
        for step in range(1, 4):
            logger.info(f"Avanzando al paso {step + 1}...")
            try:
                next_btn = await page.select(
                    "ytcp-button#next-button", timeout=20
                )
                if next_btn is None:
                    logger.warning(f"Siguiente no encontrado en paso {step}")
                    await _delay(3.0, 6.0)
                    continue
                await _delay(2.0, 4.5)
                await next_btn.click()
                # Paso 3 (Comprobaciones->Visibilidad) necesita mas espera
                if step == 3:
                    await _delay(7.0, 14.0)
                else:
                    await _delay(3.5, 6.5)
                await _scroll(page, random.randint(-80, 150))
            except Exception as e:
                logger.warning(f"Paso {step}: {e}")
                await _delay(3.0, 5.0)

        # -- Visibilidad: Publico ---------------------------------------------
        logger.info("Estableciendo visibilidad: Publico...")
        public_radio = await page.select(
            "tp-yt-paper-radio-button[name='PUBLIC']", timeout=25
        )

        # Si no aparecio, reintentar avance (transicion lenta)
        if public_radio is None:
            logger.warning("Visibilidad no visible aun -- reintentando avance...")
            extra_next = await page.select("ytcp-button#next-button", timeout=10)
            if extra_next:
                await extra_next.click()
                await _delay(7.0, 14.0)
            public_radio = await page.select(
                "tp-yt-paper-radio-button[name='PUBLIC']", timeout=20
            )

        # Fallback: buscar por texto visible en la pagina
        if public_radio is None:
            for text in ["P\u00fablica", "Public", "P\u00fablico"]:
                try:
                    public_radio = await page.find(text, timeout=6)
                    if public_radio:
                        break
                except Exception:
                    pass

        if public_radio is None:
            logger.error("No se encontro el boton de visibilidad 'Publico'")
            return False, ""

        await _delay(2.0, 4.5)
        await public_radio.click()
        await _delay(4.0, 9.0)

        # -- Esperar upload completo ------------------------------------------
        await _wait_upload_complete(page)

        # -- Guardar ----------------------------------------------------------
        logger.info("Guardando video...")
        save_btn = await page.select("ytcp-button#done-button", timeout=30)
        if save_btn is None:
            logger.error("No se encontro el boton Guardar")
            return False, ""
        await _delay(2.5, 5.0)
        await save_btn.click()
        await _delay(5.0, 10.0)

        # -- Capturar URL del video publicado ----------------------------------
        import re as _re
        youtube_url = ""

        # 1. Buscar enlace directo en el dialogo de confirmacion
        for selector in [
            "a[href*='/shorts/']",
            "a[href*='watch?v=']",
            "a[href*='youtu.be/']",
        ]:
            try:
                link_el = await page.select(selector, timeout=5)
                if link_el:
                    href = await link_el.get_attribute("href")
                    if href and ("shorts" in href or "watch" in href or "youtu.be" in href):
                        youtube_url = href.strip()
                        logger.info(f"URL capturada del dialogo: {youtube_url}")
                        break
            except Exception:
                pass

        # 2. Si no hubo dialogo, extraer video ID de la URL del Studio
        if not youtube_url:
            current_url_after = page.url or ""
            m = _re.search(r'/video/([a-zA-Z0-9_-]{8,12})(?:/|$)', current_url_after)
            if m:
                vid_id = m.group(1)
                youtube_url = f"https://www.youtube.com/shorts/{vid_id}"
                logger.info(f"URL construida desde Studio URL: {youtube_url}")

        # 3. Buscar texto con ID en el cuerpo de la pagina
        if not youtube_url:
            try:
                body_text = await page.evaluate("document.body.innerText")
                m = _re.search(r'youtu\.be/([a-zA-Z0-9_-]{8,12})', body_text or "")
                if not m:
                    m = _re.search(r'watch\?v=([a-zA-Z0-9_-]{8,12})', body_text or "")
                if not m:
                    m = _re.search(r'/shorts/([a-zA-Z0-9_-]{8,12})', body_text or "")
                if m:
                    youtube_url = f"https://www.youtube.com/shorts/{m.group(1)}"
                    logger.info(f"URL extraida del DOM: {youtube_url}")
            except Exception:
                pass

        if not youtube_url:
            logger.warning("No se pudo capturar la URL del video — el video SI fue publicado")

        # -- Screenshot de confirmacion ----------------------------------------
        ts = time.strftime("%Y%m%d_%H%M%S")
        screenshot_path = config.LOGS_DIR / f"upload_confirm_{ts}.png"
        await page.save_screenshot(str(screenshot_path))
        logger.info(f"Screenshot guardado: {screenshot_path.name}")

        logger.info(f"Video subido exitosamente: '{title}'")
        return True, youtube_url

    except Exception as e:
        logger.error(f"Error durante upload: {e}")
        if page:
            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                err_path = config.LOGS_DIR / f"upload_error_{ts}.png"
                await page.save_screenshot(str(err_path))
                logger.info(f"Screenshot de error: {err_path.name}")
            except Exception:
                pass
        return False, ""

    finally:
        try:
            browser.stop()
        except Exception:
            pass


# --- API publica (sincrona) ---------------------------------------------------


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
) -> str | None:
    """
    Sube un video a YouTube Studio usando nodriver (CDP, sin WebDriver).

    Args:
        video_path: Ruta al archivo MP4
        title: Titulo del video (max 100 chars)
        description: Descripcion del video
        tags: Lista de hashtags (con o sin #)

    Returns:
        URL del video en YouTube si se subio correctamente (puede ser "" si
        no se capturo la URL pero el upload si se completo).
        None si el upload fallo.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video no encontrado: {video_path}")

    logger.info(f"Iniciando upload: {video_path.name}")

    for attempt in range(1, config.UPLOAD_MAX_RETRIES + 1):
        try:
            ok, youtube_url = asyncio.run(
                _upload_async(video_path, title, description, tags)
            )
            if ok:
                return youtube_url  # "" si no se capturo URL, pero upload OK
            logger.warning(f"Intento {attempt} fallo sin excepcion")
        except Exception as e:
            logger.error(f"Excepcion en intento {attempt}/{config.UPLOAD_MAX_RETRIES}: {e}")

        if attempt < config.UPLOAD_MAX_RETRIES:
            logger.info(f"Reintentando en {config.UPLOAD_RETRY_WAIT}s...")
            time.sleep(config.UPLOAD_RETRY_WAIT)

    logger.error(f"Upload fallo tras {config.UPLOAD_MAX_RETRIES} intentos")
    return None
