"""
youtube_audio_library.py — Descarga música CC0 desde la Biblioteca de audio de YouTube Studio

Usa el perfil de Chrome de YouTube (ya con sesión activa) para:
  1. Navegar a studio.youtube.com → Biblioteca de audio
  2. Filtrar: sin atribución requerida + mood Dramatic/Emotional
  3. Descargar N tracks a assets/music/
  4. Eliminar archivos de Fesliyan/Bensound (disparan Content ID)

Invocación: /music en Telegram o python main.py --music
"""

import asyncio
import json
import logging
import random
import re
import shutil
import sys
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_MUSIC_DIR         = config.ASSETS_DIR / "music"
_CONTENT_ID_NAMES  = ("fesliyan", "bensound", "incompetech")
_DEFAULT_N         = 5


# ─── Helpers de música ───────────────────────────────────────────────────────

def purge_content_id_files() -> list[str]:
    """Elimina tracks registrados en Content ID. Devuelve lista de nombres borrados."""
    removed = []
    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for f in _MUSIC_DIR.glob("*"):
        if f.suffix.lower() in (".mp3", ".wav") and any(m in f.name.lower() for m in _CONTENT_ID_NAMES):
            f.unlink(missing_ok=True)
            removed.append(f.name)
            logger.info(f"Eliminado (Content ID): {f.name}")
    return removed


def count_safe_tracks() -> int:
    """Cuenta archivos de música que NO son de Fesliyan/Bensound/Incompetech."""
    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    return sum(
        1 for f in _MUSIC_DIR.glob("*")
        if f.suffix.lower() in (".mp3", ".wav")
        and not any(m in f.name.lower() for m in _CONTENT_ID_NAMES)
    )


def needs_music_refresh() -> bool:
    """True si no hay música segura — trigger para avisar al usuario."""
    return count_safe_tracks() == 0


# ─── Preparar perfil de Chrome con directorio de descarga ────────────────────

def _set_chrome_download_dir(profile_dir: Path, download_dir: Path) -> None:
    """
    Escribe el directorio de descarga en el archivo de preferencias de Chrome
    ANTES de abrir el navegador. Este método es más fiable que CDP en Windows
    porque el lock de Chrome a veces no responde a setDownloadBehavior.
    """
    prefs_path = profile_dir / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)

    prefs = {}
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        except Exception:
            prefs = {}

    # Insertar o actualizar sección de descarga
    prefs.setdefault("download", {}).update({
        "default_directory": str(download_dir.absolute()),
        "directory_upgrade": True,
        "prompt_for_download": False,
    })
    prefs.setdefault("savefile", {})["default_directory"] = str(download_dir.absolute())

    prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    logger.debug(f"Chrome prefs: download dir → {download_dir}")


# ─── Lógica async de descarga ─────────────────────────────────────────────────

async def _download_async(n_tracks: int) -> list[str]:
    import nodriver as uc
    from modules.youtube_uploader import _inject_stealth, _delay

    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    profile_dir  = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Directorio de descarga temporal
    dl_dir = config.BASE_DIR / "temp_music_dl"
    dl_dir.mkdir(parents=True, exist_ok=True)

    # Pre-configurar Chrome para descargar ahí sin diálogo
    _set_chrome_download_dir(profile_dir, dl_dir)

    browser = None
    downloaded: list[str] = []

    try:
        browser = await uc.start(
            user_data_dir=str(profile_dir),
            browser_args=[
                "--window-size=1920,1080",
                "--window-position=-2000,0",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            headless=False,
        )

        page = await browser.get("about:blank")
        await _inject_stealth(page)

        # ── Verificar sesión de YouTube Studio ───────────────────────────────
        page = await browser.get("https://studio.youtube.com")
        await _delay(5, 9)

        current = page.url or ""
        if "accounts.google.com" in current or "signin" in current:
            raise RuntimeError(
                "YouTube Studio sin sesión activa.\n"
                f"Abre Chrome con el perfil: {profile_dir}\n"
                "Entra a studio.youtube.com, inicia sesión y ciérralo."
            )

        # ── Extraer channel ID ────────────────────────────────────────────────
        channel_id = ""
        m = re.search(r"/channel/(UC[^/?#]+)", current)
        if m:
            channel_id = m.group(1)
        if not channel_id:
            try:
                body = await page.evaluate("document.body.innerHTML")
                m2 = re.search(r'"channelId"\s*:\s*"(UC[^"]+)"', body)
                if m2:
                    channel_id = m2.group(1)
            except Exception:
                pass

        music_url = (
            f"https://studio.youtube.com/channel/{channel_id}/music"
            if channel_id else
            "https://studio.youtube.com/channel//music"
        )
        logger.info(f"Biblioteca de audio: {music_url}")

        page = await browser.get(music_url)
        await _delay(6, 10)

        # ── Filtro: "No Attribution Required" ────────────────────────────────
        # Marca el checkbox/chip para ver solo música CC0 libre de atribución
        await _apply_no_attribution_filter(page)
        await _delay(2, 3)

        # ── Descargar tracks ──────────────────────────────────────────────────
        files_before = _current_dl_files(dl_dir)
        downloaded   = await _click_download_buttons(page, n_tracks, dl_dir, files_before)

    except Exception as e:
        logger.error(f"Error en Biblioteca de audio: {e}", exc_info=True)
        raise
    finally:
        if browser:
            try:
                await browser.stop()
            except Exception:
                pass
        # Mover cualquier archivo que quedó pendiente
        _move_completed_downloads(dl_dir, downloaded)
        # Limpiar directorio temporal
        shutil.rmtree(str(dl_dir), ignore_errors=True)

    return downloaded


async def _apply_no_attribution_filter(page) -> None:
    """Activa el filtro 'No attribution required' en la Biblioteca de audio."""
    try:
        result = await page.evaluate("""
            (function() {
                // Buscar botón/chip de "No attribution required" o similar
                var all = document.querySelectorAll(
                    'button, [role="button"], [role="option"], [role="checkbox"], ' +
                    'tp-yt-paper-button, ytcp-chip, label'
                );
                var keywords = [
                    'no attribution', 'sin atribución', 'free to use',
                    'attribution not required', 'libre de atribución'
                ];
                for (var el of all) {
                    var txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (keywords.some(k => txt.includes(k))) {
                        el.click();
                        return txt;
                    }
                }
                return null;
            })()
        """)
        if result:
            logger.info(f"Filtro aplicado: '{result}'")
    except Exception as e:
        logger.debug(f"Filtro attribution: {e}")


def _current_dl_files(dl_dir: Path) -> set:
    return {f for f in dl_dir.glob("*") if not f.name.endswith(".crdownload")}


async def _click_download_buttons(page, n_tracks: int, dl_dir: Path, files_before: set) -> list[str]:
    """Hace clic en los botones de descarga y espera cada archivo."""
    from modules.youtube_uploader import _delay
    downloaded = []
    scroll_attempts = 0

    while len(downloaded) < n_tracks and scroll_attempts < 20:
        # Obtener todos los botones de descarga visibles
        btn_count = await page.evaluate("""
            Array.from(document.querySelectorAll(
                '[aria-label*="Download"], [aria-label*="Descargar"], ' +
                '[title*="Download"], [title*="Descargar"], ' +
                'button[class*="download"], ytcp-icon-button[class*="download"]'
            )).filter(b => {
                var r = b.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }).length
        """)

        if btn_count == 0 or btn_count <= len(downloaded):
            # Scroll para cargar más tracks
            await page.evaluate("window.scrollBy(0, 500)")
            await _delay(1.5, 2.5)
            scroll_attempts += 1
            continue

        scroll_attempts = 0  # resetear si encontramos botones

        # Clic en el botón índice len(downloaded)
        idx = len(downloaded)
        clicked = await page.evaluate(f"""
            (function() {{
                var btns = Array.from(document.querySelectorAll(
                    '[aria-label*="Download"], [aria-label*="Descargar"], ' +
                    '[title*="Download"], [title*="Descargar"], ' +
                    'button[class*="download"], ytcp-icon-button[class*="download"]'
                )).filter(b => {{
                    var r = b.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }});
                if (btns[{idx}]) {{
                    btns[{idx}].scrollIntoView({{block: 'center'}});
                    btns[{idx}].click();
                    return true;
                }}
                return false;
            }})()
        """)

        if not clicked:
            await _delay(1, 2)
            continue

        # Esperar hasta 30s que aparezca el archivo
        file_found = None
        for _ in range(30):
            await asyncio.sleep(1)
            now = _current_dl_files(dl_dir)
            new = now - files_before
            # Solo contar completados (sin .crdownload)
            completed = {f for f in new if not f.name.endswith(".crdownload")}
            if completed:
                file_found = next(iter(completed))
                files_before = now
                break

        if file_found:
            # Mover a assets/music/
            dest = _MUSIC_DIR / file_found.name
            shutil.move(str(file_found), str(dest))
            downloaded.append(file_found.name)
            logger.info(f"  Track {len(downloaded)}/{n_tracks}: {file_found.name}")
            await _delay(1.5, 3.0)
        else:
            logger.warning(f"  Descarga {idx+1} no completó en 30s — saltando")
            # Avanzar igual para no quedarse atascado en el mismo botón
            files_before = _current_dl_files(dl_dir)

    return downloaded


def _move_completed_downloads(dl_dir: Path, already_moved: list[str]) -> None:
    """Mueve cualquier archivo que quedó sin mover (descarga tardía)."""
    if not dl_dir.exists():
        return
    moved_names = set(already_moved)
    for f in dl_dir.glob("*"):
        if f.name.endswith(".crdownload") or f.name in moved_names:
            continue
        if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac"):
            dest = _MUSIC_DIR / f.name
            try:
                shutil.move(str(f), str(dest))
                logger.info(f"  (tardío) Movido: {f.name}")
            except Exception:
                pass


# ─── Generador de música ambiente CC0 (sin internet, sin API key) ─────────────

# Presets de música ambiente dramática: (nombre, bpm, notas_base_Hz, modo)
_AMBIENT_PRESETS = [
    ("ambient_drama_01", 60,  [130.81, 155.56, 196.00, 246.94],  "minor"),  # C minor
    ("ambient_drama_02", 55,  [146.83, 174.61, 220.00, 261.63],  "minor"),  # D minor
    ("ambient_drama_03", 65,  [164.81, 196.00, 246.94, 293.66],  "minor"),  # E minor
    ("ambient_drama_04", 50,  [110.00, 130.81, 164.81, 220.00],  "minor"),  # A minor
    ("ambient_drama_05", 58,  [123.47, 146.83, 185.00, 220.00],  "minor"),  # B minor
]


def download_freepd_tracks(n: int = 5) -> list[str]:
    """
    Genera N tracks de música ambiente dramática usando síntesis de audio puro.
    100% CC0, no requiere internet ni API key. Produce .wav de 90 segundos.
    """
    import math
    import struct
    import wave as _wave

    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    generated = []
    sample_rate = 44100
    duration    = 90      # segundos
    n_samples   = sample_rate * duration

    for preset in _AMBIENT_PRESETS[:n]:
        name, bpm, base_freqs, mode = preset
        dest = _MUSIC_DIR / f"{name}.wav"
        if dest.exists() and dest.stat().st_size > 100_000:
            logger.debug(f"Ya existe: {dest.name}")
            generated.append(dest.name)
            continue

        logger.info(f"Generando música ambiente: {dest.name}...")
        try:
            samples = []
            for i in range(n_samples):
                t = i / sample_rate

                # Drone base: acorde menor con 4 notas
                chord = sum(
                    math.sin(2 * math.pi * freq * t) * (0.15 / len(base_freqs))
                    for freq in base_freqs
                )
                # Sub-graves para peso dramático
                sub = math.sin(2 * math.pi * base_freqs[0] * 0.5 * t) * 0.12

                # Pulso lento (breathing)
                lfo = 0.5 + 0.5 * math.sin(2 * math.pi * (bpm / 60) * 0.25 * t)
                # Fade in/out para loop limpio
                fade = min(t / 3.0, 1.0) * min((duration - t) / 3.0, 1.0)

                val = (chord + sub) * lfo * fade
                # Clip y convertir a int16
                val = max(-0.95, min(0.95, val))
                samples.append(int(val * 32767))

            # Escribir WAV mono 16-bit
            with _wave.open(str(dest), "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

            generated.append(dest.name)
            logger.info(f"  ✓ {dest.name} ({dest.stat().st_size // 1024}KB, {duration}s)")
        except Exception as e:
            logger.warning(f"  Error generando {name}: {e}")

    if generated:
        logger.info(f"Música ambiente generada: {len(generated)} track(s) → assets/music/")
    else:
        logger.warning("No se pudo generar ningún track de música")
    return generated


# ─── Punto de entrada síncrono ────────────────────────────────────────────────

def download_music_tracks(n_tracks: int = _DEFAULT_N) -> list[str]:
    """
    Descarga N tracks CC0 desde la Biblioteca de audio de YouTube Studio.
    Elimina Fesliyan/Bensound automáticamente antes de descargar.
    Retorna lista de nombres de archivos descargados.
    """
    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    removed = purge_content_id_files()
    if removed:
        logger.info(f"Eliminados {len(removed)} archivo(s) con Content ID")

    logger.info(f"Iniciando descarga de {n_tracks} tracks CC0...")

    try:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_download_async(n_tracks))
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
            result = asyncio.run(_download_async(n_tracks))

        if result:
            logger.info(f"Descarga completada: {len(result)} track(s)")
        else:
            logger.warning("No se descargó ningún track — revisa la sesión de YouTube Studio")

        return result

    except Exception as e:
        logger.error(f"download_music_tracks falló: {e}")
        return []
