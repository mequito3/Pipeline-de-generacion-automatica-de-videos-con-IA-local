"""
tiktok_uploader.py — Sube videos a TikTok Studio via automatización del navegador

Usa nodriver (mismo approach que youtube_uploader) para subir a:
  https://www.tiktok.com/tiktok-studio/upload

Requiere sesión activa de TikTok en el perfil de Chrome configurado.
Si no hay sesión: el pipeline lo indica y salta TikTok sin abortar.
"""

import asyncio
import logging
import random
import sys
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# TikTok cambia la URL de upload periódicamente. Se prueban todas en orden.
_TIKTOK_UPLOAD_URLS = [
    "https://www.tiktok.com/creator-center/upload",
    "https://www.tiktok.com/upload",
    "https://www.tiktok.com/tiktok-studio/upload",
]
TIKTOK_CHROME_PROFILE = str(Path(config.BASE_DIR) / "chrome_profile_tiktok")

# Reutilizar helpers anti-detección y utilidades de ventana del uploader de YouTube
from modules.youtube_uploader import (
    _delay,
    _human_type,
    _inject_stealth,
    _scroll,
    _random_mouse_wander,
    _win_foreground,
    _cleanup_chrome_profile,
)

# Pool de comentarios de fallback (config.TIKTOK_COMMENTS_POOL tiene 20+ entradas).
_TIKTOK_COMMENTS = config.TIKTOK_COMMENTS_POOL


async def _post_tiktok_comment(browser, username: str, comment_text: str) -> bool:
    """Publica un comentario en el video más reciente del perfil."""
    try:
        profile_url = f"https://www.tiktok.com/@{username}"
        logger.info(f"TikTok comentario: navegando a {profile_url}")
        page = await browser.get(profile_url)
        await _delay(4.0, 6.0)

        # Primer video del perfil (el más reciente)
        first_video = None
        for sel in [
            '[data-e2e="user-post-item"] a',
            '[data-e2e="user-post-item-desc"] a',
            'div[class*="DivItemContainer"] a',
            'a[href*="/video/"]',
        ]:
            try:
                first_video = await page.select(sel, timeout=6)
                if first_video:
                    break
            except Exception:
                pass

        if not first_video:
            logger.warning("TikTok comentario: primer video no encontrado en perfil")
            return False

        await first_video.click()
        await _delay(3.5, 5.0)

        # Input de comentario
        comment_input = None
        for sel in [
            '[data-e2e="comment-input"]',
            'div[contenteditable="true"][placeholder*="coment"]',
            'div[contenteditable="true"][placeholder*="Add comment"]',
            'div[contenteditable="true"][placeholder*="comment"]',
            '[class*="CommentInput"] div[contenteditable]',
        ]:
            try:
                comment_input = await page.select(sel, timeout=6)
                if comment_input:
                    break
            except Exception:
                pass

        if not comment_input:
            # Scroll para revelar el input si está abajo
            await _scroll(page, 300)
            await _delay(1.5, 2.5)
            for sel in ['[data-e2e="comment-input"]', 'div[contenteditable="true"]']:
                try:
                    comment_input = await page.select(sel, timeout=5)
                    if comment_input:
                        break
                except Exception:
                    pass

        if not comment_input:
            logger.warning("TikTok comentario: input no encontrado")
            return False

        await comment_input.click()
        await _delay(0.8, 1.5)
        await _human_type(comment_input, comment_text, clear_first=False)
        await _delay(0.8, 1.5)

        # Submit: Enter key
        submitted = await page.evaluate("""
            (function() {
                var input = document.querySelector(
                    '[data-e2e="comment-input"], div[contenteditable="true"]'
                );
                if (!input) return false;
                var ev = new KeyboardEvent('keydown', {
                    key: 'Enter', code: 'Enter', keyCode: 13,
                    bubbles: true, cancelable: true
                });
                input.dispatchEvent(ev);
                return true;
            })()
        """)

        if submitted:
            await _delay(2.0, 3.0)
            logger.info(f"TikTok: comentario publicado → '{comment_text[:60]}'")
            return True

        logger.warning("TikTok comentario: no se pudo enviar con Enter")
        return False

    except Exception as e:
        logger.warning(f"TikTok comentario falló (no crítico): {e}")
        return False


async def _find_file_input(browser, main_page):
    """
    Busca el <input type=file> de TikTok Studio en:
    1. La página principal
    2. Tabs adicionales (nodriver expone los OOPIFs como tabs separados)

    Retorna (tab, element) o (None, None).
    """
    # ── Estrategia 1: selector directo en la página principal ─────────────────
    for sel in [
        "input[type='file']",
        "input[accept*='video']",
        "input[accept*='mp4']",
        "input[name='file']",
    ]:
        try:
            el = await main_page.select(sel, timeout=8)
            if el:
                logger.info(f"  File input directo: {sel}")
                return main_page, el
        except Exception:
            pass

    # ── Estrategia 2: buscar en otros tabs (OOPIFs = cross-origin iframes) ───
    logger.info("  Buscando input en sub-frames (tabs de nodriver)...")
    for attempt in range(20):
        await asyncio.sleep(0.5)
        try:
            all_tabs = browser.tabs
        except Exception:
            break

        for tab in all_tabs:
            if tab is main_page:
                continue
            tab_url = getattr(tab, "url", "") or ""
            if not tab_url or "about:blank" in tab_url:
                continue
            # Solo tabs de TikTok (no google, not chrome-extension, etc.)
            if "tiktok.com" not in tab_url and "creator" not in tab_url:
                continue
            logger.debug(f"  Sub-tab: {tab_url[:70]}")
            for sel in ["input[type='file']", "input[accept*='video']"]:
                try:
                    el = await asyncio.wait_for(tab.select(sel, timeout=4), timeout=5)
                    if el:
                        logger.info(f"  File input en sub-tab: {tab_url[:60]}")
                        return tab, el
                except Exception:
                    pass

    # ── Estrategia 3: click en el área de upload para que aparezca el input ──
    logger.info("  Intentando activar el input via click en área de upload...")
    try:
        await main_page.evaluate("""
            (function() {
                var targets = [
                    '[class*="upload-btn"]', '[class*="upload-area"]',
                    '[data-e2e*="upload"]', '[class*="UploaderWrapper"]',
                    '.upload-card', 'label[for*="file"]',
                ];
                for (var t of targets) {
                    var el = document.querySelector(t);
                    if (el) { el.click(); return true; }
                }
                return false;
            })()
        """)
        await asyncio.sleep(2)
        for sel in ["input[type='file']", "input[accept*='video']"]:
            try:
                el = await main_page.select(sel, timeout=5)
                if el:
                    logger.info(f"  File input activado via click: {sel}")
                    return main_page, el
            except Exception:
                pass
    except Exception:
        pass

    return None, None


async def _upload_async(
    video_path: Path,
    caption: str,
    thumbnail_path: str = "",
    comment: str = "",
) -> tuple[bool, str]:
    import nodriver as uc

    profile_dir = Path(TIKTOK_CHROME_PROFILE)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Matar instancias previas de Chrome y resetear posición de ventana.
    # Sin esto Chrome restaura la posición guardada en Preferences (puede ser
    # fuera de pantalla) aunque se pase --start-maximized en los args.
    _cleanup_chrome_profile(profile_dir)

    browser = await uc.start(
        user_data_dir=str(profile_dir),
        browser_args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--window-position=0,0",
            "--disable-dev-shm-usage",
        ],
        headless=False,
    )

    try:
        # Inyectar stealth ANTES de cargar cualquier página
        _blank = await browser.get("about:blank")
        await _inject_stealth(_blank)

        # Warm-up: visitar TikTok home antes de ir a Studio
        page = await browser.get("https://www.tiktok.com")
        await page.activate()
        _win_foreground("TikTok")   # traer Chrome al primer plano del OS
        await _delay(3, 6)
        await _scroll(page, random.randint(100, 250))
        await _random_mouse_wander(page)
        await _delay(2, 4)

        # Navegar a la URL de upload (probar todas en orden hasta que una funcione)
        upload_url_used = ""
        login_redirect = False
        for url in _TIKTOK_UPLOAD_URLS:
            page = await browser.get(url)
            await _delay(7, 11)  # TikTok Studio carga lento
            current_url = page.url or ""
            logger.info(f"TikTok: probando {url} → aterrizó en {current_url[:70]}")

            # Redirigió a login → sin sesión, no tiene sentido probar más URLs
            if any(x in current_url for x in ["login", "passport", "signup", "register"]):
                login_redirect = True
                break

            # Redirigió a 404 → esta URL ya no existe, probar la siguiente
            if "/404" in current_url:
                logger.warning(f"TikTok: {url} devolvió 404 — probando siguiente URL...")
                continue

            # URL cargó bien
            upload_url_used = url
            break

        if login_redirect or not upload_url_used:
            raise RuntimeError(
                f"TikTok sin sesión activa o URL de upload no encontrada. "
                f"Loguéate manualmente: chrome.exe --user-data-dir=\"{profile_dir}\" https://www.tiktok.com/login"
            )

        logger.info(f"TikTok Studio cargado ({upload_url_used}): {(page.url or '')[:70]}")

        # ── Buscar el file input ──────────────────────────────────────────────
        logger.info(f"TikTok: buscando input para {video_path.name}...")
        active_tab, file_input = await _find_file_input(browser, page)

        if not file_input:
            page_html = ""
            try:
                page_html = await page.evaluate("document.body.innerHTML")
            except Exception:
                pass
            logger.error(
                "TikTok: no se encontró el input de archivo.\n"
                "  Posibles causas:\n"
                "  - TikTok cambió su UI de upload\n"
                "  - El navegador está bloqueado por CAPTCHA\n"
                f"  HTML snippet: {page_html[:400]}"
            )
            return False, ""

        await file_input.send_file(str(video_path.absolute()))
        logger.info("TikTok: archivo enviado — esperando que termine de cargarse...")

        # ── Esperar que el video termine de subirse (hasta 5 min) ────────────
        upload_done = False
        for _ in range(60):
            await asyncio.sleep(5)
            try:
                body = await active_tab.evaluate("document.body.innerText")
                lower = body.lower()
                if any(k in lower for k in ["cargado", "uploaded", "upload complete", "100%"]):
                    logger.info("TikTok: video cargado")
                    upload_done = True
                    break
                import re as _re
                m = _re.search(r"(\d{1,3})\s*%", body)
                if m and int(m.group(1)) > 0:
                    logger.info(f"TikTok: cargando... {m.group(1)}%")
            except Exception:
                pass

        if not upload_done:
            logger.warning("TikTok: no se confirmó carga — intentando publicar igual")

        await _delay(2, 3)
        _win_foreground("TikTok")   # asegurar primer plano antes de escribir caption

        # ── Caption ──────────────────────────────────────────────────────────
        try:
            # Limpiar el campo via JS (más confiable que selectAll + tipo)
            await active_tab.evaluate("""
                (function() {
                    var selectors = [
                        '[data-e2e="caption-input"]',
                        '.DraftEditor-root',
                        'div[contenteditable="true"]',
                        'textarea',
                    ];
                    for (var s of selectors) {
                        var el = document.querySelector(s);
                        if (!el) continue;
                        el.focus();
                        // Intentar varias formas de limpiar según el tipo de editor
                        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                            el.value = '';
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                        } else {
                            document.execCommand('selectAll', false, null);
                            document.execCommand('delete', false, null);
                        }
                        return true;
                    }
                    return false;
                })()
            """)
            await _delay(0.3, 0.6)

            caption_input = None
            for sel in [
                "[data-e2e='caption-input']",
                ".DraftEditor-root div[contenteditable]",
                "div[contenteditable='true']",
                "textarea",
            ]:
                try:
                    caption_input = await active_tab.select(sel, timeout=5)
                    if caption_input:
                        break
                except Exception:
                    pass

            if caption_input:
                await caption_input.click()
                await _delay(0.4, 0.8)
                caption_trimmed = caption[:2190]
                await _human_type(caption_input, caption_trimmed, clear_first=False)
                logger.info(f"TikTok: caption escrito ({len(caption_trimmed)} chars)")
                await _delay(2.0, 3.0)
            else:
                logger.warning("TikTok: campo de caption no encontrado — publicando sin descripción")
        except Exception as _ce:
            logger.warning(f"TikTok: caption falló (no crítico): {_ce}")

        # ── Thumbnail (portada) ───────────────────────────────────────────────
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                await _delay(1.0, 2.0)
                cover_id = await active_tab.evaluate("""
                    (function() {
                        var inputs = document.querySelectorAll('input[type="file"]');
                        for (var inp of inputs) {
                            var acc = (inp.accept || '').toLowerCase();
                            if (acc.includes('image') || acc.includes('jpeg') || acc.includes('png')) {
                                var uid = '_cov_' + Date.now();
                                inp.setAttribute('id', uid);
                                return uid;
                            }
                        }
                        return null;
                    })()
                """)
                if cover_id:
                    cov = await active_tab.select(f"#{cover_id}", timeout=3)
                    if cov:
                        await cov.send_file(str(Path(thumbnail_path).absolute()))
                        await _delay(2.0, 3.5)
                        logger.info("TikTok: portada subida")
            except Exception as _ec:
                logger.debug(f"TikTok portada (no crítico): {_ec}")

        # ── Visibilidad: forzar "Todos" / "Everyone" (público) ───────────────────
        await _delay(1.5, 2.5)
        try:
            visibility_set = await active_tab.evaluate("""
                (function() {
                    // Buscar selector/radio de visibilidad y elegir "Everyone"/"Todos"/"Público"
                    var keywords = ['everyone', 'todos', 'público', 'public', 'all'];

                    // Caso 1: radio buttons o checkboxes de visibilidad
                    var inputs = document.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                    for (var inp of inputs) {
                        var label = '';
                        if (inp.id) {
                            var lbl = document.querySelector('label[for="' + inp.id + '"]');
                            label = lbl ? lbl.innerText.toLowerCase() : '';
                        }
                        var parent = inp.parentElement ? inp.parentElement.innerText.toLowerCase() : '';
                        var combined = label + ' ' + parent;
                        if (keywords.some(k => combined.includes(k))) {
                            inp.click();
                            return 'radio:' + (label || parent).trim().slice(0, 30);
                        }
                    }

                    // Caso 2: dropdown/select de privacidad
                    var selects = document.querySelectorAll('select');
                    for (var sel of selects) {
                        for (var opt of sel.options) {
                            if (keywords.some(k => opt.text.toLowerCase().includes(k))) {
                                sel.value = opt.value;
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                return 'select:' + opt.text;
                            }
                        }
                    }

                    // Caso 3: div/button clickeable con texto de visibilidad
                    var all = document.querySelectorAll('[class*="privacy"], [class*="visibility"], [class*="audience"]');
                    for (var el of all) {
                        var txt = el.innerText.toLowerCase();
                        if (keywords.some(k => txt.includes(k)) && el.tagName !== 'DIV') {
                            el.click();
                            return 'btn:' + txt.slice(0, 30);
                        }
                    }

                    return null;
                })()
            """)
            if visibility_set:
                logger.info(f"TikTok: visibilidad establecida ({visibility_set})")
                await _delay(0.5, 1.0)
            else:
                logger.debug("TikTok: selector de visibilidad no encontrado — usando valor por defecto")
        except Exception as _ve:
            logger.debug(f"TikTok visibilidad (no crítico): {_ve}")

        # ── Botón Publicar ────────────────────────────────────────────────────
        _win_foreground("TikTok")   # asegurar primer plano antes del click en Publicar
        await _delay(1.0, 2.0)
        clicked = await active_tab.evaluate("""
            (function() {
                var buttons = document.querySelectorAll('button');
                var keywords = ['publicar', 'post', 'subir', 'upload', 'publish'];
                for (var btn of buttons) {
                    var txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (keywords.some(k => txt === k || txt.startsWith(k))) {
                        btn.click();
                        return btn.innerText.trim();
                    }
                }
                // Fallback: buscar por atributo data-e2e
                var postBtn = document.querySelector(
                    '[data-e2e="post-btn"], [class*="submit"], [class*="PostBtn"]'
                );
                if (postBtn) { postBtn.click(); return 'data-e2e:post'; }
                return null;
            })()
        """)

        if clicked:
            logger.info(f"TikTok: botón '{clicked}' — esperando confirmación...")
        else:
            logger.error("TikTok: botón de publicar no encontrado")
            return False, ""

        # ── Diálogo "Publicar ahora vs Programar" ────────────────────────────
        # TikTok Studio muestra este diálogo después de clicar Publicar.
        # Si no lo manejamos, el video queda como borrador.
        await _delay(2.0, 3.5)
        try:
            post_now_clicked = await active_tab.evaluate("""
                (function() {
                    // Palabras clave del botón "Publicar ahora" / "Post now"
                    var now_kws = [
                        'post now', 'publicar ahora', 'publish now',
                        'subir ahora', 'upload now', 'post immediately'
                    ];
                    // Excluir botones que son "Schedule" / "Programar"
                    var schedule_kws = ['schedule', 'programar', 'later', 'más tarde'];

                    var buttons = Array.from(document.querySelectorAll(
                        'button, [role="button"], [class*="modal"] button, ' +
                        '[class*="dialog"] button, [class*="Dialog"] button, ' +
                        '[class*="Modal"] button, [class*="popup"] button'
                    ));

                    for (var btn of buttons) {
                        var txt = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                        if (!txt) continue;
                        // Debe coincidir con "publicar ahora" y NO con "programar"
                        var isNow = now_kws.some(k => txt.includes(k));
                        var isSchedule = schedule_kws.some(k => txt.includes(k));
                        if (isNow && !isSchedule) {
                            var r = btn.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                btn.click();
                                return txt;
                            }
                        }
                    }

                    // Fallback: si hay un modal visible con un solo botón de acción principal
                    var modal = document.querySelector(
                        '[class*="modal"][class*="visible"], [class*="dialog"][style*="display: block"], ' +
                        '[class*="Modal"]:not([style*="display: none"])'
                    );
                    if (modal) {
                        var btns = Array.from(modal.querySelectorAll('button')).filter(b => {
                            var r = b.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                        // Si hay exactamente un botón de acción primaria visible, clicarlo
                        var primary = btns.find(b =>
                            (b.className || '').toLowerCase().includes('primary') ||
                            (b.className || '').toLowerCase().includes('confirm') ||
                            (b.className || '').toLowerCase().includes('submit')
                        );
                        if (primary) {
                            primary.click();
                            return 'modal-primary:' + (primary.innerText || '').trim().slice(0, 30);
                        }
                    }

                    return null;
                })()
            """)
            if post_now_clicked:
                logger.info(f"TikTok: diálogo manejado — '{post_now_clicked}'")
                await _delay(1.5, 2.5)
            else:
                logger.debug("TikTok: no apareció diálogo 'Publicar ahora' — continuando")
        except Exception as _dnow:
            logger.debug(f"TikTok diálogo post-now (no crítico): {_dnow}")

        # ── Confirmar publicación ─────────────────────────────────────────────
        # Señales fiables de éxito (específicas, no aparecen antes de publicar)
        _SUCCESS_KWS = [
            "your video has been posted",
            "video has been uploaded",
            "subido con éxito",
            "tu video ha sido publicado",
            "video posted",
            "successfully posted",
            "successfully uploaded",
        ]
        # Señales de que está procesando (también es éxito — el video ya se subió)
        _PROCESSING_KWS = [
            "your video is being processed",
            "processing your video",
            "video is processing",
            "en proceso",
            "procesando",
        ]

        tiktok_url = ""
        username = getattr(config, "TIKTOK_USERNAME", "").lstrip("@")

        for tick in range(30):  # hasta 150 segundos
            await asyncio.sleep(5)
            try:
                current = active_tab.url or ""
                body    = await active_tab.evaluate("document.body.innerText")
                lower   = body.lower()

                # Éxito confirmado: mensaje de "publicado"
                if any(kw in lower for kw in _SUCCESS_KWS):
                    tiktok_url = f"https://www.tiktok.com/@{username}"
                    logger.info("TikTok: publicación confirmada (mensaje de éxito)")
                    break

                # Procesando: también es éxito
                if any(kw in lower for kw in _PROCESSING_KWS):
                    tiktok_url = f"https://www.tiktok.com/@{username}"
                    logger.info("TikTok: video en procesamiento (subida exitosa)")
                    break

                # La URL cambió fuera de la página de upload → éxito implícito
                if current and "upload" not in current.lower() and "studio" not in current.lower():
                    tiktok_url = f"https://www.tiktok.com/@{username}"
                    logger.info(f"TikTok: URL redirigió a {current[:60]} — publicado")
                    break

                if tick % 3 == 0:
                    logger.debug(f"TikTok: esperando confirmación ({tick*5}s)... URL={current[:50]}")

            except Exception:
                pass

        # Screenshot siempre — para diagnosticar si falló o no
        try:
            import time as _time
            ts = _time.strftime("%Y%m%d_%H%M%S")
            ss_path = config.BASE_DIR / "logs" / f"tiktok_result_{ts}.png"
            await active_tab.save_screenshot(str(ss_path))
            logger.info(f"TikTok: screenshot → logs/tiktok_result_{ts}.png")
        except Exception as _ss:
            logger.debug(f"TikTok screenshot: {_ss}")

        if tiktok_url:
            # ── Comentario controversial post-publicación ─────────────────────
            try:
                await _delay(3.0, 5.0)
                # Usar comentario generado por IA (específico de esta historia)
                # o fallback a lista si el LLM no lo generó
                post_comment = comment.strip() if comment and len(comment) > 10 else random.choice(_TIKTOK_COMMENTS)
                await _post_tiktok_comment(browser, username, post_comment)
            except Exception as _ce:
                logger.debug(f"TikTok comentario (no crítico): {_ce}")
            return True, tiktok_url

        logger.warning("TikTok: sin confirmación en 150s — el video puede haberse publicado igual. Revisa @" + username)
        return False, ""

    except Exception as e:
        logger.error(f"TikTok upload error: {e}", exc_info=True)
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
    thumbnail_path: str = "",
    comment: str = "",
) -> str | None:
    """
    Sube un video a TikTok Studio.

    Returns:
        URL del perfil de TikTok si tuvo éxito, None si falló.
    """
    vp = Path(video_path)
    if not vp.exists():
        logger.error(f"TikTok: archivo no encontrado: {video_path}")
        return None

    hashtag_str = " ".join(t if t.startswith("#") else f"#{t}" for t in tags[:10])
    caption     = f"{title}\n\n{description}\n\n{hashtag_str}"

    logger.info(f"TikTok: iniciando upload de '{vp.name}'")

    try:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                ok, url = loop.run_until_complete(_upload_async(vp, caption, thumbnail_path, comment))
            finally:
                try:
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
            ok, url = asyncio.run(_upload_async(vp, caption, thumbnail_path, comment))

        if ok:
            logger.info(f"TikTok: video publicado → {url}")
            return url

        logger.error("TikTok: upload falló")
        return None

    except Exception as e:
        logger.error(f"TikTok: error inesperado: {e}")
        return None
