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

# ─── Fingerprint dinámico por sesión ─────────────────────────────────────────
# Pantalla, hardware, AudioContext, WebRTC, batería — varía en cada sesión
# para que el canal no tenga el mismo "perfil de bot" siempre.

_DYNAMIC_STEALTH_TEMPLATE = r"""(function() {
  try { Object.defineProperty(navigator, 'hardwareConcurrency', {get: function() { return __HW__; }}); } catch(e) {}
  try { Object.defineProperty(navigator, 'deviceMemory',        {get: function() { return __MEM__; }}); } catch(e) {}
  try { Object.defineProperty(navigator, 'platform',            {get: function() { return '__PLAT__'; }}); } catch(e) {}
  try { Object.defineProperty(navigator, 'vendor',              {get: function() { return 'Google Inc.'; }}); } catch(e) {}
  try {
    var _s = {width: __SW__, height: __SH__, availWidth: __SAW__, availHeight: __SAH__, colorDepth: 24, pixelDepth: 24};
    for (var _k in _s) {
      (function(key, val) {
        try { Object.defineProperty(screen, key, {get: function() { return val; }}); } catch(_) {}
      })(_k, _s[_k]);
    }
  } catch(e) {}
  try {
    var _aN = __AUDIO__;
    var _oGCD = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(ch) {
      var d = _oGCD.call(this, ch);
      for (var i = 0; i < Math.min(10, d.length); i++) d[i] += (Math.random() - 0.5) * _aN;
      return d;
    };
  } catch(e) {}
  try {
    var _oRTC = window.RTCPeerConnection;
    if (_oRTC) {
      var _sRTC = function(c, x) { if (c && c.iceServers) c.iceServers = []; return new _oRTC(c, x); };
      _sRTC.prototype = _oRTC.prototype;
      Object.defineProperty(window, 'RTCPeerConnection', {value: _sRTC});
    }
  } catch(e) {}
  try {
    var _ci = {effectiveType: '4g', downlink: __DL__, rtt: __RTT__, saveData: false};
    if (!navigator.connection) Object.defineProperty(navigator, 'connection', {get: function() { return _ci; }});
  } catch(e) {}
  try {
    if (!navigator.getBattery) {
      navigator.getBattery = function() {
        return Promise.resolve({
          charging: true, chargingTime: 0, dischargingTime: Infinity,
          level: __BATT__, addEventListener: function() {}
        });
      };
    }
  } catch(e) {}
  try {
    delete window.domAutomation; delete window.domAutomationController;
    delete window._Selenium_IDE_Recorder; delete window.__webdriverFunc;
    delete window.__lastWatirAlert; delete window.__lastWatirConfirm;
  } catch(e) {}
})();"""

_COMMON_SCREEN_PROFILES = [
    (1920, 1080, 1920, 1040),
    (1366,  768, 1366,  728),
    (1440,  900, 1440,  860),
    (1536,  864, 1536,  824),
    (1280,  800, 1280,  760),
    (2560, 1440, 2560, 1400),
]


def _dynamic_stealth_js() -> str:
    """Genera JS de fingerprint aleatorio — distinto en cada sesión del navegador."""
    sw, sh, saw, sah = random.choice(_COMMON_SCREEN_PROFILES)
    return (
        _DYNAMIC_STEALTH_TEMPLATE
        .replace("__HW__",    str(random.choice([4, 6, 8, 8, 8, 10, 12])))
        .replace("__MEM__",   str(random.choice([4, 4, 8, 8, 16])))
        .replace("__PLAT__",  random.choice(["Win32", "Win32", "Win32", "MacIntel"]))
        .replace("__SW__",    str(sw))
        .replace("__SH__",    str(sh))
        .replace("__SAW__",   str(saw))
        .replace("__SAH__",   str(sah))
        .replace("__AUDIO__", str(round(random.uniform(0.000018, 0.000085), 7)))
        .replace("__DL__",    str(round(random.uniform(8.2, 48.5), 1)))
        .replace("__RTT__",   str(random.randint(18, 85)))
        .replace("__BATT__",  str(round(random.uniform(0.55, 1.0), 2)))
    )


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


async def _organic_pause(page=None, min_s: float = 3.0, max_s: float = 12.0) -> None:
    """
    Pausa larga de 'distracción': el usuario se distrajo con otra cosa.
    A veces mueve apenas el mouse (como alguien que tiene la mano sobre él),
    a veces no hace nada — ambas son conductas humanas normales.
    page puede ser None (en ese caso sólo hace sleep).
    """
    pause_total = random.triangular(min_s, max_s, min_s + (max_s - min_s) * 0.35)
    if page is not None and random.random() < 0.45:
        moves = random.randint(1, 3)
        per_move = pause_total / (moves + 1)
        for _ in range(moves):
            nx = max(80.0, min(1840.0, _cursor["x"] + random.uniform(-140, 140)))
            ny = max(60.0, min(1000.0, _cursor["y"] + random.uniform(-100, 100)))
            await _bezier_move(page, _cursor["x"], _cursor["y"], nx, ny)
            await asyncio.sleep(per_move)
    else:
        await asyncio.sleep(pause_total)


async def _simulate_reading(page, duration_s: float = 7.0) -> None:
    """
    Simula leer la página: scroll lento e irregular, pausas de reflexión variables,
    movimiento de cursor como si señalaras texto con el dedo.
    """
    import time as _time
    end_at = _time.time() + duration_s
    while _time.time() < end_at:
        remaining = end_at - _time.time()
        if remaining <= 0.5:
            break
        await _scroll(page, random.randint(50, 220))
        pause = min(random.triangular(0.7, 4.0, 1.6), remaining)
        if random.random() < 0.22:
            await _random_mouse_wander(page)
        await asyncio.sleep(pause)


# ─── Tipeo humano ─────────────────────────────────────────────────────────────

# Teclas vecinas en teclado QWERTY — para simular errores de tipeo realistas
_KEYBOARD_NEIGHBORS: dict[str, str] = {
    "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsrd",
    "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb", "i": "uojk", "j": "huikmnb",
    "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
    "u": "yhij", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}


async def _type_char(element, char: str) -> None:
    """Inserta un único carácter vía execCommand."""
    char_json = _json.dumps(char)
    await element.apply(
        "(el) => { el.focus(); document.execCommand('insertText', false, "
        + char_json + "); }"
    )


async def _backspace(element) -> None:
    """Borra el último carácter (simula Backspace)."""
    await element.apply(
        "(el) => { el.focus(); document.execCommand('delete', false, null); }"
    )


async def _human_type(element, text: str, clear_first: bool = True) -> None:
    """
    Escribe carácter a carácter vía execCommand('insertText').
    Velocidad variable (~60 WPM), pausas de 'pensar' periódicas,
    y errores reales de tipeo corregidos (~1% por carácter):
      - Error de tecla vecina: escribe letra adyacente, pausa, borra, reescribe
      - Doble tipeo: repite el mismo carácter, pausa breve, borra el extra
    """
    if clear_first:
        # Range API — más confiable que execCommand('selectAll') en contenteditable
        # de shadow DOM como los de YouTube Studio
        await element.apply(
            "(el) => {"
            "  el.focus();"
            "  const range = document.createRange();"
            "  range.selectNodeContents(el);"
            "  const sel = window.getSelection();"
            "  sel.removeAllRanges();"
            "  sel.addRange(range);"
            "  document.execCommand('delete', false, null);"
            "}"
        )
        await asyncio.sleep(random.uniform(0.25, 0.5))
        # Segunda pasada: si quedó algo, fuerza borrado total
        await element.apply(
            "(el) => {"
            "  if (el.innerText && el.innerText.trim().length > 0) {"
            "    el.focus();"
            "    document.execCommand('selectAll', false, null);"
            "    document.execCommand('delete', false, null);"
            "  }"
            "}"
        )
    else:
        await element.apply("(el) => el.focus()")
    await asyncio.sleep(random.uniform(0.3, 0.7))

    chars_since_pause = 0
    next_pause_at = random.randint(8, 22)
    wpm_factor = random.uniform(0.8, 1.3)

    for char in text:
        # ── Errores de tipeo (~1 en 100 caracteres) ──────────────────────────
        roll = random.random()
        char_lower = char.lower()

        if roll < 0.007 and char_lower in _KEYBOARD_NEIGHBORS:
            # Error de tecla vecina: escribe la tecla incorrecta, nota el error, corrige
            wrong = random.choice(_KEYBOARD_NEIGHBORS[char_lower])
            if char.isupper():
                wrong = wrong.upper()
            await _type_char(element, wrong)
            await asyncio.sleep(random.uniform(0.18, 0.52))  # "nota el error"
            await _backspace(element)
            await asyncio.sleep(random.uniform(0.09, 0.25))  # breve antes de corregir
            await _type_char(element, char)

        elif roll < 0.010 and char not in " \n":
            # Doble tipeo: presiona la misma tecla dos veces, borra el extra
            await _type_char(element, char)
            await asyncio.sleep(random.uniform(0.06, 0.18))
            await _type_char(element, char)
            await asyncio.sleep(random.uniform(0.22, 0.55))  # "nota el duplicado"
            await _backspace(element)
            await asyncio.sleep(random.uniform(0.08, 0.20))

        else:
            await _type_char(element, char)

        # ── Velocidad según tipo de carácter ─────────────────────────────────
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
    Inyecta stealth JS ANTES de que cargue cualquier página.
    Combina el JS base (siempre igual) + fingerprint dinámico (aleatorio por sesión)
    para que cada ejecución tenga hardware, pantalla y audio distintos.
    """
    combined = _STEALTH_JS + "\n" + _dynamic_stealth_js()
    try:
        import nodriver.cdp.page as cdp_page
        await page.send(
            cdp_page.add_script_to_evaluate_on_new_document(source=combined)
        )
        logger.info("Stealth JS inyectado via CDP (fingerprint randomizado por sesión)")
    except Exception as e:
        try:
            await page.evaluate(combined)
            logger.debug(f"Stealth JS via evaluate (fallback): {e}")
        except Exception as e2:
            logger.debug(f"Stealth injection fallback falló: {e2}")


# ─── Warm-up de sesión ────────────────────────────────────────────────────────

async def _session_warmup(browser) -> None:
    """
    Un humano no abre Chrome y va directo a Studio.
    Navega YouTube, ve 1-2 videos por 35-85s cada uno (crítico para account health),
    y opcionalmente busca algo antes — todo antes de ir a Studio.
    """
    try:
        logger.info("Warm-up: visitando YouTube home...")
        page = await browser.get("https://www.youtube.com")
        await _delay(4.0, 8.0)
        await _scroll(page, random.randint(200, 500))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.5)

        # 30% de las veces: buscar algo primero (como haría un usuario real)
        if random.random() < 0.30:
            try:
                _search_terms = [
                    "historia real pareja", "confesiones amor", "relaciones toxicas",
                    "drama real", "storytime español", "historia impactante shorts",
                ]
                search_box = await page.select("input#search", timeout=5)
                if search_box:
                    await _human_click(page, search_box)
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await _human_type(search_box, random.choice(_search_terms), clear_first=False)
                    await asyncio.sleep(random.uniform(0.8, 1.8))
                    await page.keyboard.send("Enter")
                    await _delay(3.0, 6.0)
                    await _scroll(page, random.randint(200, 500))
                    await _delay(1.5, 3.0)
            except Exception:
                pass

        # Ver 1-2 videos de forma realista — ver <15s es señal de bot
        n_videos = random.choices([1, 2], weights=[0.45, 0.55])[0]
        for video_num in range(n_videos):
            try:
                video_links = await page.select_all("a#video-title", timeout=6)
                if not video_links:
                    break
                pick = random.choice(video_links[:10])
                await _human_click(page, pick)
                await _delay(2.0, 4.0)

                # Ver el video 35-85s (equivalente a ver 1-2 Shorts completos)
                watch_secs = random.triangular(35.0, 85.0, 52.0)
                logger.info(f"Warm-up: viendo video {video_num+1}/{n_videos} ~{watch_secs:.0f}s")

                end_at = time.time() + watch_secs
                while time.time() < end_at:
                    remaining = end_at - time.time()
                    if remaining <= 1.5:
                        break
                    if random.random() < 0.20:
                        await _random_mouse_wander(page)
                    # Scroll a comentarios ocasionalmente (comportamiento humano real)
                    if random.random() < 0.15 and remaining > 15:
                        await _scroll(page, random.randint(250, 500))
                        await asyncio.sleep(random.uniform(4.0, 9.0))
                        await _scroll(page, -random.randint(150, 300))
                    await asyncio.sleep(random.uniform(4.0, 12.0))

                # Dar like ocasionalmente (12%) — como un usuario normal
                if random.random() < 0.12:
                    try:
                        for _like_sel in [
                            "button[title*='Me gusta' i]",
                            "button[aria-label*='like this video' i]",
                            "#like-button button",
                        ]:
                            _like = await page.select(_like_sel, timeout=3)
                            if _like:
                                await _human_click(page, _like)
                                await asyncio.sleep(random.uniform(0.5, 1.5))
                                break
                    except Exception:
                        pass

                if video_num < n_videos - 1:
                    page = await browser.get("https://www.youtube.com")
                    await _delay(2.5, 5.0)
                    await _scroll(page, random.randint(150, 350))
                    await _delay(1.0, 2.5)

            except Exception as e:
                logger.debug(f"Warm-up video {video_num+1}: {e}")
                try:
                    page = await browser.get("https://www.youtube.com")
                    await _delay(2.0, 4.0)
                except Exception:
                    pass
                break

        # Volver a home si quedamos en un video
        try:
            if "watch" in (page.url or "") or "shorts" in (page.url or ""):
                page = await browser.get("https://www.youtube.com")
                await _delay(1.5, 3.5)
        except Exception:
            pass

        await _scroll(page, random.randint(-80, -30))
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

    try:
        await page.evaluate("document.activeElement && document.activeElement.blur()")
        await page.keyboard.send("Escape")
        await _delay(0.8, 1.5)

        await _scroll(page, 300)
        await _delay(1.0, 2.0)

        ts = time.strftime("%Y%m%d_%H%M%S")
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        await page.save_screenshot(str(logs_dir / f"thumbnail_diag_{ts}.png"))

        # Canal no verificado → no hay subida de thumbnail en desktop
        try:
            mobile_msg = await page.find("miniatura en la aplicaci", timeout=2)
            if mobile_msg:
                logger.warning(
                    "Thumbnail: canal no verificado — ve a Studio → Configuración → Canal → Verificar"
                )
                return
        except Exception:
            pass

        # Intentar clic en el botón "Subir miniatura" (puede revelar el input)
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
                    await _delay(1.0, 2.0)
                    break
            except Exception:
                pass

        # Intentar selectores CSS directos
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
                    logger.info(f"Thumbnail input encontrado: {selector}")
                    break
            except Exception:
                pass

        # Traversar shadow DOM via JavaScript (YouTube Studio usa web components)
        if thumb_input is None:
            try:
                unique_id = await page.evaluate("""(function() {
                    function findImageInput(root) {
                        var inputs = root.querySelectorAll('input[type="file"]');
                        for (var i = 0; i < inputs.length; i++) {
                            var acc = (inputs[i].accept || '').toLowerCase();
                            if (acc.includes('image') || acc.includes('jpeg') || acc.includes('png')) {
                                var uid = '_thumb_' + Date.now() + '_' + i;
                                inputs[i].setAttribute('id', uid);
                                inputs[i].style.cssText = 'display:block!important;opacity:0.01!important;position:fixed;top:0;left:0;width:1px;height:1px;z-index:99999';
                                return uid;
                            }
                        }
                        var all = root.querySelectorAll('*');
                        for (var j = 0; j < all.length; j++) {
                            if (all[j].shadowRoot) {
                                var r = findImageInput(all[j].shadowRoot);
                                if (r) return r;
                            }
                        }
                        return null;
                    }
                    return findImageInput(document);
                })()""")
                if unique_id:
                    thumb_input = await page.select(f"#{unique_id}", timeout=3)
                    logger.info("Thumbnail input encontrado via shadow DOM")
            except Exception as _je:
                logger.debug(f"Shadow DOM traversal: {_je}")

        # Último recurso: segundo file input del DOM (el primero es el video)
        if thumb_input is None:
            try:
                all_inputs = await page.select_all("input[type='file']")
                if len(all_inputs) >= 2:
                    thumb_input = all_inputs[1]  # índice 1 = thumbnail (no el video)
                    logger.info("Thumbnail: usando segundo input file del DOM")
            except Exception:
                pass

        if thumb_input:
            await thumb_input.send_file(str(thumb.absolute()))
            await _delay(2.5, 4.5)
            logger.info("Thumbnail subido correctamente")
        else:
            logger.warning(
                "Thumbnail input no encontrado — verifica que el canal esté verificado "
                "en YouTube Studio → Configuración → Canal → Elegibilidad de funciones"
            )

        await _scroll(page, -300)
        await asyncio.sleep(random.uniform(0.6, 1.2))

    except Exception as e:
        logger.warning(f"Thumbnail (no crítico): {e}")


# ─── Esperar upload completo ──────────────────────────────────────────────────

async def _wait_upload_complete(page, timeout: int = 360) -> None:
    """
    Espera hasta que el upload termine y el botón Publicar esté habilitado.

    YouTube Studio siempre muestra #done-button en la pantalla de visibilidad,
    pero lo tiene deshabilitado mientras el video aún se está subiendo.
    Esta función espera hasta que el botón NO esté disabled.
    """
    logger.info("Esperando que el video termine de subirse...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            done = await page.select("ytcp-button#done-button", timeout=3)
            if done:
                # Verificar que el botón está habilitado (no deshabilitado por upload en curso)
                disabled = await done.apply(
                    "(el) => {"
                    "  if (el.hasAttribute('disabled')) return true;"
                    "  if (el.getAttribute('aria-disabled') === 'true') return true;"
                    "  const inner = el.querySelector('button');"
                    "  if (inner && inner.hasAttribute('disabled')) return true;"
                    "  return false;"
                    "}"
                )
                if not disabled:
                    btn_text = ""
                    try:
                        btn_text = await done.apply("(el) => (el.innerText || '').trim()")
                    except Exception:
                        pass
                    logger.info(f"Upload completo — botón habilitado: '{btn_text}'")
                    return
                else:
                    elapsed = int(time.time() - start)
                    if elapsed % 20 == 0:
                        logger.info(f"Botón presente pero deshabilitado (upload en curso)... {elapsed}s")
        except Exception:
            pass
        # Mouse wander mientras espera (comportamiento humano idle)
        if random.random() < 0.3:
            await _random_mouse_wander(page)
        await asyncio.sleep(5)
        elapsed = int(time.time() - start)
        if elapsed % 30 == 0:
            logger.info(f"Esperando upload... ({elapsed}s)")
    logger.warning(f"Timeout esperando upload ({timeout}s) — intentando publicar de todas formas")


# ─── Comentario fijado con link de Telegram ──────────────────────────────────

async def _post_pinned_comment(browser, youtube_url: str, comment_text: str) -> bool:
    """
    Navega al video recién publicado y posta + fija un comentario con el link de Telegram.
    Reutiliza la sesión del navegador ya autenticada — no abre Chrome de nuevo.
    El pin es best-effort: si falla el UI, el comentario queda publicado de todas formas.
    """
    if not youtube_url or not comment_text:
        return False
    try:
        # Shorts URL (/shorts/ID) → watch URL (/watch?v=ID)
        # La página de Shorts no muestra la caja de comentarios estándar.
        # La página /watch?v= sí la tiene con los selectores normales.
        import re as _re
        m = _re.search(r"/shorts/([A-Za-z0-9_-]{8,12})", youtube_url)
        watch_url = f"https://www.youtube.com/watch?v={m.group(1)}" if m else youtube_url

        logger.info(f"Abriendo video para comentario de Telegram: {watch_url}")
        vpage = await browser.get(watch_url)
        await _delay(6.0, 10.0)
        await _scroll(vpage, 600)
        await _delay(2.0, 4.0)

        # Activar la caja de comentarios (placeholder visible antes del textarea real)
        comment_box = None
        for sel in [
            "#simplebox-placeholder",
            "yt-formatted-string#simplebox-placeholder",
            "[placeholder*='Añad']",
            "[placeholder*='Add a comment']",
        ]:
            try:
                comment_box = await vpage.select(sel, timeout=6)
                if comment_box:
                    break
            except Exception:
                pass

        if comment_box is None:
            await _scroll(vpage, 400)
            await _delay(2.0, 3.5)
            for sel in ["#simplebox-placeholder", "[placeholder*='coment']"]:
                try:
                    comment_box = await vpage.select(sel, timeout=5)
                    if comment_box:
                        break
                except Exception:
                    pass

        if comment_box is None:
            logger.warning("Comentario Telegram: caja de comentarios no encontrada")
            return False

        await _human_click(vpage, comment_box)
        await _delay(1.5, 3.0)

        # Textarea real (aparece después del click en el placeholder)
        text_input = None
        for sel in [
            "#contenteditable-root",
            "div[contenteditable='true']#contenteditable-root",
            "ytd-comment-simplebox-renderer div[contenteditable]",
        ]:
            try:
                text_input = await vpage.select(sel, timeout=6)
                if text_input:
                    break
            except Exception:
                pass

        if text_input is None:
            logger.warning("Comentario Telegram: textarea no encontrado")
            return False

        await _human_type(text_input, comment_text, clear_first=False)
        await _delay(1.5, 2.5)

        # Enviar comentario — 3 estrategias en orden
        submitted = False

        # Estrategia 1: selector CSS
        submit_btn = None
        for sel in [
            "#submit-button",
            "ytd-button-renderer#submit-button",
            "yt-button-shape#submit-button",
            "[aria-label*='Comentar']",
            "[aria-label*='Comment']",
            "ytd-comment-simplebox-renderer #submit-button",
            "#submit-button button",
        ]:
            try:
                submit_btn = await vpage.select(sel, timeout=4)
                if submit_btn:
                    break
            except Exception:
                pass

        if submit_btn:
            await _human_click(vpage, submit_btn)
            submitted = True

        # Estrategia 2: JavaScript — busca el botón por texto/aria en el DOM
        if not submitted:
            clicked_js = await vpage.evaluate("""
                (function() {
                    // Botón de submit dentro del simplebox
                    var box = document.querySelector('ytd-comment-simplebox-renderer');
                    if (box) {
                        var btn = box.querySelector('#submit-button, [aria-label*="Comentar"], [aria-label*="Comment"]');
                        if (btn) { btn.click(); return 'simplebox'; }
                    }
                    // Fallback: cualquier botón de submit activo (no disabled)
                    var all = document.querySelectorAll('#submit-button');
                    for (var b of all) {
                        if (!b.disabled && b.offsetParent !== null) {
                            b.click(); return 'fallback';
                        }
                    }
                    return null;
                })()
            """)
            if clicked_js:
                logger.info(f"Comentario submit via JS ({clicked_js})")
                submitted = True

        # Estrategia 3: Ctrl+Enter (atajo de teclado de YouTube)
        if not submitted:
            try:
                await text_input.key_down("ctrl")
                await text_input.key_press("Enter")
                await text_input.key_up("ctrl")
                submitted = True
                logger.info("Comentario submit via Ctrl+Enter")
            except Exception:
                pass

        if not submitted:
            logger.warning("Comentario Telegram: botón submit no encontrado — omitiendo")
            return False

        await _delay(5.0, 8.0)
        logger.info("Comentario de Telegram publicado")

        # ── Fijar el comentario (best-effort) ─────────────────────────────────
        try:
            # Esperar a que aparezca al menos un comentario (el nuestro)
            first_thread = None
            for attempt in range(6):
                await _delay(2.0, 3.0)
                try:
                    first_thread = await vpage.select("ytd-comment-thread-renderer", timeout=5)
                    if first_thread:
                        break
                except Exception:
                    pass
                # Scroll suave para que YouTube cargue los comentarios
                await _scroll(vpage, 200)

            if first_thread is None:
                logger.debug("Pin: comentario aún no visible después de esperar — omitiendo pin")
                return True

            # Hover sobre el primer comentario para revelar el menú "..."
            await _random_mouse_wander(vpage)
            await _delay(0.5, 1.0)

            # Buscar el botón "..." del primer comentario con varios selectores
            menu_btn = None
            for sel in [
                "ytd-comment-thread-renderer #more",
                "ytd-comment-thread-renderer yt-icon-button#more",
                "ytd-comment-thread-renderer [aria-label*='opciones del comentario']",
                "ytd-comment-thread-renderer [aria-label*='comment actions']",
                "ytd-comment-thread-renderer [aria-label*='More']",
                "ytd-comment-thread-renderer ytd-menu-renderer yt-icon-button",
            ]:
                try:
                    menu_btn = await vpage.select(sel, timeout=4)
                    if menu_btn:
                        break
                except Exception:
                    pass

            if not menu_btn:
                # Intentar hover sobre el thread para revelar el botón
                try:
                    await first_thread.mouse_move()
                    await _delay(0.5, 1.0)
                    for sel in ["#more", "yt-icon-button#more", "[aria-label*='opciones']", "[aria-label*='More']"]:
                        try:
                            menu_btn = await first_thread.select(sel, timeout=3)
                            if menu_btn:
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

            if not menu_btn:
                logger.debug("Pin: botón de menú no encontrado — comentario publicado sin fijar")
                return True

            await _human_click(vpage, menu_btn)
            await _delay(1.0, 2.0)

            # Buscar opción "Fijar comentario" / "Pin comment" en el menú desplegable
            pinned = False
            for pin_text in ["Fijar comentario", "Pin comment", "Fijar", "Pin"]:
                try:
                    pin_opt = await vpage.find(pin_text, timeout=4)
                    if pin_opt:
                        await _human_click(vpage, pin_opt)
                        await _delay(1.5, 2.5)
                        # Confirmar diálogo si aparece ("Fijar" / "Pin" de nuevo)
                        for cfm_text in ["Fijar", "Pin", "Confirm", "Confirmar"]:
                            try:
                                cfm = await vpage.find(cfm_text, timeout=3)
                                if cfm:
                                    await _human_click(vpage, cfm)
                                    await _delay(1.0, 2.0)
                                    break
                            except Exception:
                                pass
                        logger.info("Comentario de Telegram FIJADO")
                        pinned = True
                        break
                except Exception:
                    pass

            if not pinned:
                logger.debug("Pin: opción 'Fijar' no encontrada en el menú")

        except Exception as e_pin:
            logger.debug(f"Pin del comentario falló (no crítico): {e_pin}")

        return True

    except Exception as e:
        logger.warning(f"Comentario fijado Telegram falló (no crítico): {e}")
        return False


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
    comment: str = "",
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
                "--window-size=1920,1080",
                "--window-position=0,0",
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

        # Micro-pausa y hover sobre el botón antes de click (más humano)
        await _random_mouse_wander(page)
        await _think()
        await asyncio.sleep(random.uniform(0.4, 1.1))
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

        # Mover el mouse sobre la opción, leer un momento, luego click
        await asyncio.sleep(random.uniform(0.6, 1.4))
        await _human_click(page, upload_opt)
        await _delay(2.5, 5.0)

        # ── Seleccionar archivo ───────────────────────────────────────────────
        logger.info(f"Cargando archivo: {video_path.name}")
        file_input = await page.select("input[type='file']", timeout=20)
        await file_input.send_file(str(video_path.absolute()))
        logger.info("Archivo enviado — esperando modal de detalles...")
        await _random_mouse_wander(page)
        # Espera extra: YouTube pre-rellena el título con el nombre del archivo
        # y lo hace de forma asíncrona; hay que darle tiempo antes de borrar
        await _delay(8.0, 12.0)

        # ── Título ───────────────────────────────────────────────────────────
        logger.info("Escribiendo título...")
        title_input = await page.select("#title-textarea #textbox", timeout=30)
        await _think()
        await _human_click(page, title_input)
        await _delay(2.0, 4.0)
        # Verificar que el campo esté visible y tenga el texto pre-rellenado
        prefilled = await title_input.apply("(el) => el.innerText || ''")
        if prefilled and prefilled.strip():
            logger.info(f"Campo título pre-rellenado con: '{prefilled.strip()[:40]}' — borrando...")
        await _human_type(title_input, title)
        await _random_mouse_wander(page)
        await _delay(1.5, 3.5)

        # ── Descripción + hashtags ────────────────────────────────────────────
        logger.info("Escribiendo descripción...")
        desc_input = await page.select("#description-textarea #textbox", timeout=15)
        hashtags_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in (tags or [])
        )
        # Descripción corta + hashtags al final (estándar de Shorts)
        full_desc = f"{description}\n\n{hashtags_str}" if hashtags_str else description
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
        await _delay(3.0, 5.0)

        # ── Verificar que Público quedó realmente seleccionado ────────────────
        public_confirmed = False
        for verify_attempt in range(3):
            try:
                checked = await public_radio.apply(
                    "(el) => {"
                    "  if (el.getAttribute('aria-checked') === 'true') return true;"
                    "  if (el.hasAttribute('checked')) return true;"
                    "  const inner = el.querySelector('[aria-checked=\"true\"]');"
                    "  return inner !== null;"
                    "}"
                )
                if checked:
                    public_confirmed = True
                    logger.info("Verificado: visibilidad Público confirmada")
                    break
                else:
                    logger.warning(f"Público no confirmado (intento {verify_attempt+1}/3) — reintentando click")
                    await _delay(1.5, 3.0)
                    await _human_click(page, public_radio)
                    await _delay(2.0, 3.5)
            except Exception as e:
                logger.debug(f"Verificación Público intento {verify_attempt+1}: {e}")
                await _delay(1.5, 2.5)

        if not public_confirmed:
            # Último recurso: buscar el radio por texto visible
            try:
                pub_label = await page.find("Pública", timeout=5)
                if pub_label is None:
                    pub_label = await page.find("Public", timeout=3)
                if pub_label:
                    await _human_click(page, pub_label)
                    await _delay(2.0, 3.5)
                    logger.info("Visibilidad: click por texto 'Pública'")
            except Exception:
                pass

        # ── Esperar upload + guardar ──────────────────────────────────────────
        await _wait_upload_complete(page)

        await _scroll(page, random.randint(-80, 80))
        await _random_mouse_wander(page)
        await _think()

        # Screenshot diagnóstico antes de guardar — ver visibilidad seleccionada
        ts_pre = time.strftime("%Y%m%d_%H%M%S")
        try:
            await page.save_screenshot(str(config.LOGS_DIR / f"pre_save_{ts_pre}.png"))
        except Exception:
            pass

        logger.info("Guardando video...")
        save_btn = await page.select("ytcp-button#done-button", timeout=30)
        if save_btn is None:
            logger.error("No se encontró el botón Guardar")
            return False, ""

        # Verificar que el botón dice "Publicar" (confirma que Público está activo)
        try:
            btn_text = await save_btn.apply("(el) => (el.innerText || '').trim()")
            if btn_text:
                logger.info(f"Botón final: '{btn_text}'")
                if "priv" in btn_text.lower() or "draft" in btn_text.lower() or "borrador" in btn_text.lower():
                    logger.warning(f"ALERTA: el botón dice '{btn_text}' — visibilidad podría ser Privado. Reintentando PUBLIC...")
                    if public_radio:
                        await _human_click(page, public_radio)
                        await _delay(2.5, 4.0)
        except Exception:
            pass

        await _human_click(page, save_btn)
        await _delay(3.0, 5.0)

        # ── Diálogo "Aún estamos comprobando tu contenido" ────────────────────
        # YouTube muestra este popup en canales nuevos/sin historial. Hay que
        # hacer clic en "Publicar de todas formas" para que el video se publique.
        try:
            confirm_clicked = await page.evaluate("""
                (function() {
                    var btns = Array.from(document.querySelectorAll('button, yt-button-renderer, tp-yt-paper-button'));
                    var keywords = [
                        'publicar de todas formas', 'publish anyway',
                        'post anyway', 'publish regardless',
                    ];
                    for (var btn of btns) {
                        var txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                        if (keywords.some(k => txt.includes(k))) {
                            btn.click();
                            return txt;
                        }
                    }
                    return null;
                })()
            """)
            if confirm_clicked:
                logger.info(f"Diálogo de revisión detectado — clic en '{confirm_clicked}'")
                await _delay(3.0, 5.0)
        except Exception as _dlg_err:
            logger.debug(f"Check diálogo revisión: {_dlg_err}")

        await _delay(2.0, 4.0)

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

        # ── Comentario fijado con link de Telegram ────────────────────────────
        if youtube_url:
            import random as _rnd
            # Usar comentario generado por IA (único por historia) o fallback
            _yt_comment_fallback = [
                "guardo las historias que youtube me borra en mi canal de telegram 👉 link en mi perfil",
                "hay cosas que no puedo contar aquí... las subo en telegram. link en mi bio 👆",
                "tengo historias guardadas que aquí no me dejan subir — están en mi canal de telegram. bio 👆",
                "si esta te enganchó espérate a las de telegram 😶 link en mi perfil",
                "las más cochinas las guardo para telegram porque aquí me las borran — link en mi bio",
                "en telegram subo las que no pasan por aquí. son peores. link en mi bio 👆",
                "las confesiones que dan más asco están en mi canal de telegram — link en el perfil 👆",
            ]
            comment_text = (
                comment.strip()
                if comment and len(comment.strip()) > 10
                else _rnd.choice(_yt_comment_fallback)
            )
            await _post_pinned_comment(browser, youtube_url, comment_text)

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
    comment: str = "",
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
            # En Windows, asyncio.run() puede dejar el event loop en SelectorEventLoop
            # después de que edge-tts u otras corrutinas corran. nodriver necesita
            # ProactorEventLoop para lanzar subprocesos (Chrome). Lo creamos explícitamente.
            if platform.system() == "Windows":
                loop = asyncio.ProactorEventLoop()
                asyncio.set_event_loop(loop)
                try:
                    ok, youtube_url = loop.run_until_complete(
                        _upload_async(video_path, title, description, tags, thumbnail_path, comment)
                    )
                finally:
                    try:
                        # Cancelar tareas pendientes de nodriver/websockets antes de cerrar
                        pending = asyncio.all_tasks(loop)
                        if pending:
                            for t in pending:
                                t.cancel()
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:
                        pass
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                ok, youtube_url = asyncio.run(
                    _upload_async(video_path, title, description, tags, thumbnail_path, comment)
                )

            if ok:
                return youtube_url
            logger.warning(f"Intento {attempt} falló sin excepción")
        except Exception as e:
            logger.error(
                f"Excepción en intento {attempt}/{config.UPLOAD_MAX_RETRIES}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )

        if attempt < config.UPLOAD_MAX_RETRIES:
            logger.info(f"Reintentando en {config.UPLOAD_RETRY_WAIT}s...")
            time.sleep(config.UPLOAD_RETRY_WAIT)

    logger.error(f"Upload falló tras {config.UPLOAD_MAX_RETRIES} intentos")
    return None

