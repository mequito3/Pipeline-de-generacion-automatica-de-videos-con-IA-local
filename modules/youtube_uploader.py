"""
youtube_uploader.py -- Sube videos a YouTube Studio (nodriver, sin WebDriver)

Anti-detección multicapa:
  1. nodriver: usa Chrome DevTools Protocol directo, sin protocolo WebDriver
  2. Stealth JS inyectado via CDP ANTES de que cargue cualquier página:
     - navigator.webdriver parcheado a undefined
     - navigator.plugins, languages, hardwareConcurrency realistas
     - window.chrome con propiedades completas
     - Canvas fingerprint con ruido sutil
     - Permissions API normalizada
  3. Movimiento de mouse real via CDP Input.dispatchMouseEvent con curvas Bezier
  4. Warm-up de sesión: visita YouTube home antes de ir a Studio
  5. Delays con distribución triangular + pausas de "pensar" variables
  6. Tipeo carácter a carácter con velocidad variable (~60 WPM)
"""

import asyncio
import json as _json
import logging
import math
import os
import platform
import random
import re as _re
import sys
import time
from pathlib import Path

import nodriver as uc

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# ─── Stealth JS ───────────────────────────────────────────────────────────────
# Se inyecta via Page.addScriptToEvaluateOnNewDocument ANTES de que cargue
# cualquier página. Parchea todas las propiedades que delatan automatización.

_STEALTH_JS = r"""
(function() {
    // 1. Eliminar navigator.webdriver (la señal más obvia de bot)
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Plugins realistas (Chrome vacío es señal de bot)
    const _makePlugin = (name, filename, desc) => {
        const p = { name, filename, description: desc, length: 0 };
        Object.setPrototypeOf(p, Plugin.prototype);
        return p;
    };
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                _makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format'),
                _makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
                _makePlugin('Native Client', 'internal-nacl-plugin', ''),
            ];
            Object.setPrototypeOf(arr, PluginArray.prototype);
            return arr;
        }
    });

    // 3. Idiomas (español latinoamericano + inglés, como usuario real)
    Object.defineProperty(navigator, 'languages', {
        get: () => ['es-419', 'es', 'en-US', 'en']
    });

    // 4. Hardware realista (8 cores, 8GB — PC normal de 2024)
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

    // 5. window.chrome completo (ausente en bots headless)
    if (!window.chrome) {
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
            },
            runtime: {
                connect: function(){},
                sendMessage: function(){},
                OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', UPDATE: 'update' },
                PlatformOs: { LINUX: 'linux', MAC: 'mac', WIN: 'win' }
            },
            csi: function() {},
            loadTimes: function() { return { requestTime: Date.now() / 1000 - Math.random() * 2 }; }
        };
    }

    // 6. Permissions API normalizada (los bots suelen romperla)
    try {
        const _origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (params) => {
            if (params.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return _origQuery(params);
        };
    } catch(e) {}

    // 7. Canvas fingerprint: ruido de 1-2 bits por cada 50 pixels
    // Imperceptible visualmente, rompe el hash de tracking
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
        try {
            const ctx = this.getContext('2d');
            if (ctx && this.width > 0 && this.height > 0) {
                const img = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < img.data.length; i += 48) {
                    img.data[i] ^= (Math.random() * 3) | 0;
                }
                ctx.putImageData(img, 0, 0);
            }
        } catch(e) {}
        return _origToDataURL.call(this, type, quality);
    };

    // 8. Screen realista (1920x1080, taskbar de 40px)
    const _screen = {
        width: 1920, height: 1080,
        availWidth: 1920, availHeight: 1040,
        colorDepth: 24, pixelDepth: 24
    };
    for (const [k, v] of Object.entries(_screen)) {
        try { Object.defineProperty(screen, k, { get: () => v }); } catch(e) {}
    }

    // 9. WebGL: ocultar proveedor real (evita fingerprinting por GPU)
    try {
        const _getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';        // UNMASKED_VENDOR_WEBGL
            if (param === 37446) return 'Intel Iris OpenGL'; // UNMASKED_RENDERER_WEBGL
            return _getParam.call(this, param);
        };
        const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';
            if (param === 37446) return 'Intel Iris OpenGL';
            return _getParam2.call(this, param);
        };
    } catch(e) {}

    // 10. Eliminar traces de automation en el objeto window
    delete window.__nightmare;
    delete window._phantom;
    delete window.callPhantom;
    delete window.__selenium_evaluate;
    delete window.__webdriver_evaluate;
    delete window.__driver_evaluate;
})();
"""

# ─── Delays y timing ──────────────────────────────────────────────────────────

_THINK_PAUSES = [1.1, 1.4, 1.8, 2.2, 2.6, 3.0, 1.6, 2.9]

# Posición actual del cursor — se actualiza en cada movimiento para que
# el siguiente click parta desde donde realmente terminó el anterior.
_cursor: dict[str, float] = {"x": 960.0, "y": 540.0}


async def _delay(min_s: float = 2.0, max_s: float = 5.0) -> None:
    """Pausa con distribución triangular (pico en el centro, como pausas humanas)."""
    await asyncio.sleep(random.triangular(min_s, max_s, (min_s + max_s) / 2))


async def _think() -> None:
    """Pausa corta de 'pensar' entre acciones."""
    await asyncio.sleep(random.choice(_THINK_PAUSES) * random.uniform(0.7, 1.3))


# ─── Mouse con curvas Bezier (CDP real) ───────────────────────────────────────

async def _bezier_move(page, x1: float, y1: float, x2: float, y2: float) -> None:
    """
    Mueve el cursor de (x1,y1) a (x2,y2) siguiendo una curva Bezier cuadrática.
    Usa CDP Input.dispatchMouseEvent — mueve el cursor real, no solo dispara eventos JS.
    Actualiza _cursor al terminar para que el próximo movimiento parta desde aquí.
    """
    global _cursor
    try:
        import nodriver.cdp.input_ as cdp_input

        # Punto de control aleatorio fuera de la línea recta (crea la curva)
        cx = (x1 + x2) / 2 + random.uniform(-130, 130)
        cy = (y1 + y2) / 2 + random.uniform(-90, 90)

        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(10, min(45, int(dist / 18)))

        for i in range(steps + 1):
            t = i / steps
            bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
            by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
            # Micro-jitter: la mano humana tiembla ligeramente
            bx += random.uniform(-0.8, 0.8)
            by += random.uniform(-0.8, 0.8)

            await page.send(
                cdp_input.dispatch_mouse_event(
                    type_="mouseMoved",
                    x=round(bx, 1),
                    y=round(by, 1),
                    modifiers=0,
                    buttons=0,
                    button=cdp_input.MouseButton.NONE,
                    click_count=0,
                )
            )
            # Aceleración humana: lento al inicio, rápido en el medio, lento al llegar
            speed_factor = 1.0 - 0.55 * math.sin(math.pi * t)
            await asyncio.sleep(random.uniform(0.005, 0.016) * speed_factor)

        _cursor["x"] = x2
        _cursor["y"] = y2

    except Exception as e:
        logger.debug(f"Bezier move fallback: {e}")
        _cursor["x"] = x2
        _cursor["y"] = y2


async def _get_element_center(element) -> tuple[int, int]:
    """Obtiene las coordenadas del centro de un elemento en la página."""
    try:
        box = await element.apply(
            "(el) => { const r = el.getBoundingClientRect(); "
            "return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)}; }"
        )
        if box and isinstance(box, dict):
            return int(box.get("x", 500)), int(box.get("y", 400))
    except Exception:
        pass
    return random.randint(400, 800), random.randint(300, 600)


async def _human_click(page, element) -> None:
    """
    Click realista: mueve cursor en Bezier desde la posición actual rastreada,
    pausa de 'apuntar', click, y actualiza la posición del cursor.
    """
    tx, ty = await _get_element_center(element)
    # Partir desde donde el cursor quedó del último movimiento (no desde un punto aleatorio)
    await _bezier_move(page, _cursor["x"], _cursor["y"], float(tx), float(ty))
    await asyncio.sleep(random.uniform(0.07, 0.20))  # pausa de "apuntar"
    await element.click()
    await asyncio.sleep(random.uniform(0.1, 0.32))   # pausa post-click


# ─── Scroll con lectura simulada ─────────────────────────────────────────────

async def _scroll(page, amount: int = 200) -> None:
    """Scroll suave en pasos pequeños, como un humano que lee."""
    try:
        steps = random.randint(3, 6)
        step_size = amount // steps
        for _ in range(steps):
            await page.evaluate(
                f"window.scrollBy({{top: {step_size + random.randint(-10, 10)}, behavior: 'smooth'}})"
            )
            await asyncio.sleep(random.uniform(0.15, 0.45))
        # Pausa de "leer" tras scroll
        await asyncio.sleep(random.uniform(0.4, 1.1))
    except Exception:
        pass


async def _random_mouse_wander(page) -> None:
    """
    Mueve el mouse por 2-4 puntos aleatorios vía Bezier.
    No teleporta — parte desde _cursor y actualiza su posición en cada paso.
    Simula el movimiento idle de un humano mientras lee o espera.
    """
    try:
        for _ in range(random.randint(2, 4)):
            nx = float(random.randint(250, 1650))
            ny = float(random.randint(120, 880))
            await _bezier_move(page, _cursor["x"], _cursor["y"], nx, ny)
            await asyncio.sleep(random.uniform(0.25, 0.7))
    except Exception:
        pass


# ─── Tipeo humano ─────────────────────────────────────────────────────────────

async def _human_type(element, text: str, clear_first: bool = True) -> None:
    """
    Escribe carácter a carácter vía execCommand('insertText').
    Velocidad variable (~60 WPM), pausas de 'pensar' periódicas,
    y ocasionales errores de tipeo corregidos.
    """
    if clear_first:
        await element.apply(
            "(el) => { el.focus(); document.execCommand('selectAll', false, null); "
            "document.execCommand('delete', false, null); }"
        )
    else:
        await element.apply("(el) => el.focus()")
    await asyncio.sleep(random.uniform(0.3, 0.7))

    chars_since_pause = 0
    next_pause_at = random.randint(8, 22)
    # Simular velocidad de escritura variable (burst rápido luego lento)
    wpm_factor = random.uniform(0.8, 1.3)

    for i, char in enumerate(text):
        char_json = _json.dumps(char)
        await element.apply(
            "(el) => { el.focus(); document.execCommand('insertText', false, "
            + char_json + "); }"
        )

        # Velocidad según tipo de carácter
        if char == " ":
            base = random.uniform(0.07, 0.20)
        elif char in ".,;:!?\n":
            base = random.uniform(0.12, 0.38)
        elif char in "0123456789":
            base = random.uniform(0.09, 0.20)
        else:
            base = random.uniform(0.04, 0.16)

        await asyncio.sleep(base / wpm_factor)

        # Cambiar velocidad ocasionalmente (burst de escritura)
        if random.random() < 0.05:
            wpm_factor = random.uniform(0.7, 1.5)

        # Pausas de "pensar" periódicas
        chars_since_pause += 1
        if chars_since_pause >= next_pause_at:
            await asyncio.sleep(random.uniform(0.35, 1.4))
            chars_since_pause = 0
            next_pause_at = random.randint(8, 22)


# ─── Stealth setup vía CDP ────────────────────────────────────────────────────

async def _inject_stealth(page) -> None:
    """
    Inyecta el JS de stealth ANTES de que cargue cualquier contenido de página.
    Usa Page.addScriptToEvaluateOnNewDocument — se ejecuta en cada nueva página,
    antes de que cualquier script del sitio pueda detectar las propiedades de bot.
    """
    try:
        import nodriver.cdp.page as cdp_page
        await page.send(
            cdp_page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS)
        )
        logger.info("Stealth JS inyectado via CDP (pre-page-load)")
    except Exception as e:
        # Fallback: ejecutar después de cargar (menos efectivo pero algo)
        try:
            await page.evaluate(_STEALTH_JS)
            logger.debug(f"Stealth JS via evaluate (fallback): {e}")
        except Exception as e2:
            logger.debug(f"Stealth injection fallback falló: {e2}")


# ─── Warm-up de sesión ────────────────────────────────────────────────────────

async def _session_warmup(browser) -> None:
    """
    Un humano no abre Chrome y va directo a Studio.
    Visita YouTube home brevemente, lee un poco, luego navega a Studio.
    Esto establece un historial de navegación normal antes de hacer el upload.
    """
    try:
        logger.info("Warm-up: visitando YouTube home...")
        page = await browser.get("https://www.youtube.com")
        await _delay(3.0, 6.0)
        await _scroll(page, random.randint(150, 350))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.5)
        await _scroll(page, random.randint(-100, -50))
        await asyncio.sleep(random.uniform(1.0, 2.5))
        logger.info("Warm-up completado")
    except Exception as e:
        logger.debug(f"Warm-up omitido: {e}")


# ─── Thumbnail ────────────────────────────────────────────────────────────────

async def _upload_thumbnail(page, thumbnail_path: str) -> None:
    thumb = Path(thumbnail_path)
    if not thumb.exists():
        logger.warning(f"Thumbnail no encontrado: {thumbnail_path}")
        return

    logger.info(f"Subiendo thumbnail: {thumb.name}")
    await _scroll(page, 250)
    await _delay(0.8, 1.8)

    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        await page.save_screenshot(str(logs_dir / f"thumbnail_diag_{ts}.png"))

        thumb_btn_clicked = False
        for selector in [
            "ytcp-thumbnails-compact-editor-tabs ytcp-button",
            "[aria-label*='miniatura' i]",
            "[aria-label*='thumbnail' i]",
            "[aria-label*='Upload thumbnail' i]",
            "ytcp-thumbnail-uploader ytcp-button",
        ]:
            try:
                btn = await page.select(selector, timeout=3)
                if btn:
                    await _human_click(page, btn)
                    thumb_btn_clicked = True
                    await _delay(1.0, 2.0)
                    break
            except Exception:
                pass

        thumb_input = None
        for selector in [
            "input[type='file'][accept*='image']",
            "input[type='file'][accept*='jpeg']",
            "input[type='file'][accept*='png']",
        ]:
            try:
                el = await page.select(selector, timeout=5)
                if el:
                    thumb_input = el
                    break
            except Exception:
                pass

        if thumb_input is None:
            try:
                all_inputs = await page.select_all("input[type='file']")
                if len(all_inputs) > 1:
                    thumb_input = all_inputs[-1]
            except Exception:
                pass

        try:
            mobile_msg = await page.find("miniatura en la aplicaci", timeout=3)
            if mobile_msg:
                logger.warning(
                    "YouTube requiere verificación del canal para subir thumbnails en desktop.\n"
                    "  Ve a studio.youtube.com → Configuración → Canal → Verificar canal."
                )
                return
        except Exception:
            pass

        if thumb_input:
            await thumb_input.send_file(str(thumb.absolute()))
            await _delay(2.5, 4.5)
            logger.info("Thumbnail subido")
        else:
            logger.warning("Input de thumbnail no encontrado")

        await _scroll(page, -250)
        await asyncio.sleep(random.uniform(0.6, 1.2))

    except Exception as e:
        logger.warning(f"Thumbnail (no crítico): {e}")


# ─── Esperar upload completo ──────────────────────────────────────────────────

async def _wait_upload_complete(page, timeout: int = 360) -> None:
    logger.info("Esperando que el video termine de subirse...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            done = await page.select("ytcp-button#done-button", timeout=3)
            if done:
                logger.info("Upload completo — botón Guardar disponible")
                return
        except Exception:
            pass
        # Mouse wander mientras espera (comportamiento humano idle)
        if random.random() < 0.3:
            await _random_mouse_wander(page)
        await asyncio.sleep(5)
        elapsed = int(time.time() - start)
        if elapsed % 30 == 0:
            logger.info(f"Esperando upload... ({elapsed}s)")
    logger.warning(f"Timeout esperando upload ({timeout}s)")


# ─── Pipeline principal ───────────────────────────────────────────────────────

def _cleanup_chrome_profile(profile_dir: Path) -> None:
    """
    Mata Chrome y limpia locks del perfil para evitar
    'Failed to connect to browser' cuando quedó una instancia colgada.
    """
    import subprocess

    # Siempre matar Chrome antes de empezar — evita conflictos de perfil
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["taskkill", "//F", "//IM", "chrome.exe"],
                capture_output=True, timeout=8
            )
            if result.returncode == 0:
                logger.info("Chrome anterior terminado para liberar el perfil")
                time.sleep(2.0)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True, timeout=5)
            time.sleep(1.5)
        except Exception:
            pass

    # Eliminar archivos de bloqueo que Chrome deja si no cierra bien
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        try:
            (profile_dir / lock).unlink(missing_ok=True)
        except Exception:
            pass


async def _upload_async(
    video_path: Path,
    title: str,
    description: str,
    tags: list,
    thumbnail_path: str = "",
) -> tuple[bool, str]:
    """Pipeline completo de upload con anti-detección multicapa."""

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # En Linux sin display físico (Raspberry Pi) → usar Xvfb como pantalla virtual
    if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"
        logger.info("Linux sin display físico — usando DISPLAY=:99 (Xvfb)")

    # Limpiar bloqueos del perfil antes de iniciar Chrome
    _cleanup_chrome_profile(profile_dir)

    # Detectar binario de Chrome/Chromium según SO
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

    # Resetear posición del cursor al centro de pantalla para esta sesión
    global _cursor
    _cursor = {"x": 960.0, "y": 540.0}

    browser = None
    page = None
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

        # Aplicar stealth ANTES de navegar a ninguna URL real
        page = await browser.get("about:blank")
        await _inject_stealth(page)

        # Warm-up de sesión (visitar YouTube home como haría un humano)
        await _session_warmup(browser)

        # Ahora ir a Studio
        page = await browser.get(config.YOUTUBE_STUDIO_URL)
        await _delay(5.0, 10.0)

        current_url = page.url or ""
        if "accounts.google.com" in current_url:
            logger.error(
                "No hay sesión activa en YouTube Studio.\n"
                "  Abre Chrome manualmente, entra a studio.youtube.com,\n"
                "  inicia sesión y cierra Chrome. El perfil guardará las cookies."
            )
            return False, ""

        logger.info(f"YouTube Studio cargado: {current_url[:60]}")

        # Lectura del dashboard como humano
        await _scroll(page, random.randint(100, 220))
        await _random_mouse_wander(page)
        await _delay(2.5, 5.0)
        await _scroll(page, random.randint(-120, -60))
        await _think()

        # ── Botón "Crear" ────────────────────────────────────────────────────
        logger.info("Buscando botón 'Crear'...")
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
            logger.error("No se encontró el botón 'Crear'")
            return False, ""

        await _think()
        await _human_click(page, create_btn)
        await _delay(1.5, 3.0)

        # ── "Subir vídeos" ───────────────────────────────────────────────────
        logger.info("Buscando opción 'Subir vídeos'...")
        upload_opt = None
        for text in ["Subir v\u00eddeos", "Subir videos", "Upload videos", "Upload"]:
            try:
                upload_opt = await page.find(text, timeout=6)
                if upload_opt:
                    break
            except Exception:
                pass

        if not upload_opt:
            logger.error("No se encontró la opción 'Subir vídeos'")
            return False, ""

        await asyncio.sleep(random.uniform(0.5, 1.2))
        await _human_click(page, upload_opt)
        await _delay(2.0, 4.5)

        # ── Seleccionar archivo ───────────────────────────────────────────────
        logger.info(f"Cargando archivo: {video_path.name}")
        file_input = await page.select("input[type='file']", timeout=20)
        await file_input.send_file(str(video_path.absolute()))
        logger.info("Archivo enviado — esperando modal de detalles...")
        await _random_mouse_wander(page)
        await _delay(5.0, 9.0)

        # ── Título ───────────────────────────────────────────────────────────
        logger.info("Escribiendo título...")
        title_input = await page.select("#title-textarea #textbox", timeout=30)
        await _think()
        await _human_click(page, title_input)
        await _delay(1.5, 3.5)
        await _human_type(title_input, title)
        await _random_mouse_wander(page)
        await _delay(1.5, 3.5)

        # ── Descripción + hashtags ────────────────────────────────────────────
        logger.info("Escribiendo descripción...")
        desc_input = await page.select("#description-textarea #textbox", timeout=15)
        hashtags_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in (tags or [])
        )
        full_desc = (
            f"{hashtags_str}\n\n{description}\n\n{hashtags_str}"
            if hashtags_str
            else description
        )
        await _human_click(page, desc_input)
        await _delay(1.2, 2.8)
        await _human_type(desc_input, full_desc, clear_first=False)
        await asyncio.sleep(0.5)
        await page.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"
        )
        await _delay(1.5, 3.5)

        # ── Scroll + thumbnail ────────────────────────────────────────────────
        await _scroll(page, 180)
        await _delay(1.0, 2.5)

        if thumbnail_path:
            await _upload_thumbnail(page, thumbnail_path)
            await _delay(1.5, 3.0)

        # ── Audiencia: No es para niños ───────────────────────────────────────
        logger.info("Seleccionando audiencia...")
        await _scroll(page, 200)
        await asyncio.sleep(random.uniform(0.8, 1.8))
        try:
            not_kids = await page.select(
                "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
                timeout=10,
            )
            if not_kids:
                await _human_click(page, not_kids)
        except Exception:
            logger.warning("Opción de audiencia no encontrada, continuando...")
        await _delay(2.0, 4.0)

        # ── Siguiente x3 ─────────────────────────────────────────────────────
        for step in range(1, 4):
            logger.info(f"Avanzando al paso {step + 1}...")
            try:
                next_btn = await page.select("ytcp-button#next-button", timeout=20)
                if next_btn is None:
                    await _delay(3.0, 6.0)
                    continue
                await _scroll(page, random.randint(40, 160))
                await _random_mouse_wander(page)
                await _delay(1.5, 3.5)
                await _scroll(page, random.randint(-60, -20))
                await _think()
                await _human_click(page, next_btn)
                await _delay(8.0, 15.0) if step == 3 else await _delay(3.5, 7.0)
            except Exception as e:
                logger.warning(f"Paso {step}: {e}")
                await _delay(3.0, 5.0)

        # ── Visibilidad: Público ──────────────────────────────────────────────
        logger.info("Estableciendo visibilidad: Público...")
        public_radio = await page.select(
            "tp-yt-paper-radio-button[name='PUBLIC']", timeout=25
        )

        if public_radio is None:
            extra_next = await page.select("ytcp-button#next-button", timeout=10)
            if extra_next:
                await _human_click(page, extra_next)
                await _delay(7.0, 14.0)
            public_radio = await page.select(
                "tp-yt-paper-radio-button[name='PUBLIC']", timeout=20
            )

        if public_radio is None:
            for text in ["P\u00fablica", "Public", "P\u00fablico"]:
                try:
                    public_radio = await page.find(text, timeout=6)
                    if public_radio:
                        break
                except Exception:
                    pass

        if public_radio is None:
            logger.error("No se encontró el botón de visibilidad 'Público'")
            return False, ""

        await _think()
        await _random_mouse_wander(page)
        await _delay(2.0, 4.5)
        await _human_click(page, public_radio)
        logger.info("Visibilidad: Público seleccionado")
        await _delay(4.0, 9.0)

        # ── Esperar upload + guardar ──────────────────────────────────────────
        await _wait_upload_complete(page)

        await _scroll(page, random.randint(-80, 80))
        await _random_mouse_wander(page)
        await _think()

        logger.info("Guardando video...")
        save_btn = await page.select("ytcp-button#done-button", timeout=30)
        if save_btn is None:
            logger.error("No se encontró el botón Guardar")
            return False, ""

        await _human_click(page, save_btn)
        await _delay(5.0, 10.0)

        # ── Capturar URL ──────────────────────────────────────────────────────
        youtube_url = ""
        for selector in ["a[href*='/shorts/']", "a[href*='watch?v=']", "a[href*='youtu.be/']"]:
            try:
                link_el = await page.select(selector, timeout=5)
                if link_el:
                    href = await link_el.get_attribute("href")
                    if href and ("shorts" in href or "watch" in href):
                        youtube_url = href.strip()
                        break
            except Exception:
                pass

        if not youtube_url:
            current_url_after = page.url or ""
            m = _re.search(r"/video/([a-zA-Z0-9_-]{8,12})(?:/|$)", current_url_after)
            if m:
                youtube_url = f"https://www.youtube.com/shorts/{m.group(1)}"

        if not youtube_url:
            try:
                body_text = await page.evaluate("document.body.innerText")
                for pattern in [r"youtu\.be/([a-zA-Z0-9_-]{8,12})",
                                 r"watch\?v=([a-zA-Z0-9_-]{8,12})",
                                 r"/shorts/([a-zA-Z0-9_-]{8,12})"]:
                    m = _re.search(pattern, body_text or "")
                    if m:
                        youtube_url = f"https://www.youtube.com/shorts/{m.group(1)}"
                        break
            except Exception:
                pass

        ts = time.strftime("%Y%m%d_%H%M%S")
        await page.save_screenshot(str(config.LOGS_DIR / f"upload_confirm_{ts}.png"))
        logger.info(f"Video subido: '{title}' — {youtube_url or 'URL no capturada'}")
        return True, youtube_url

    except Exception as e:
        logger.error(f"Error durante upload: {type(e).__name__}: {e}", exc_info=True)
        if page:
            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                await page.save_screenshot(str(config.LOGS_DIR / f"upload_error_{ts}.png"))
            except Exception:
                pass
        return False, ""

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass


# ─── API pública (síncrona) ───────────────────────────────────────────────────

def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    thumbnail_path: str = "",
) -> str | None:
    """
    Sube un video a YouTube Studio con anti-detección multicapa.

    Returns:
        URL del video si se subió (puede ser "" si no se capturó la URL pero sí se subió).
        None si el upload falló.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video no encontrado: {video_path}")

    logger.info(f"Iniciando upload: {video_path.name}")

    for attempt in range(1, config.UPLOAD_MAX_RETRIES + 1):
        try:
            ok, youtube_url = asyncio.run(
                _upload_async(video_path, title, description, tags, thumbnail_path)
            )
            if ok:
                return youtube_url
            logger.warning(f"Intento {attempt} falló sin excepción")
        except Exception as e:
            logger.error(f"Excepción en intento {attempt}/{config.UPLOAD_MAX_RETRIES}: {type(e).__name__}: {e}", exc_info=True)

        if attempt < config.UPLOAD_MAX_RETRIES:
            logger.info(f"Reintentando en {config.UPLOAD_RETRY_WAIT}s...")
            time.sleep(config.UPLOAD_RETRY_WAIT)

    logger.error(f"Upload falló tras {config.UPLOAD_MAX_RETRIES} intentos")
    return None
