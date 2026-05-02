"""
main.py — Orquestador principal del Shorts Factory

Uso:
  python main.py           → Scheduler automático (3 videos/día)
  python main.py --now     → Generar y subir un video ahora
  python main.py --test    → Probar cada módulo individualmente

Flujo completo:
  1. Verificar servicios (Ollama + ffmpeg)
  2. Buscar historia real en Reddit (o elegir topic rotatorio)
  3. Generar guión con LLM (Groq/Ollama)
  4. Generar audio con TTS (Edge TTS)
  5. Descargar clips de stock video (Pexels)
  6. Ensamblar video MP4 1080x1920
  7. Subir a YouTube con nodriver
  8. Log completo + limpieza de temporales
"""

# ══════════════════════════════════════════════════════════════════════════════
# BSOD PROTECTION — DEBE SER LO PRIMERO ANTES DE CUALQUIER IMPORT
# ══════════════════════════════════════════════════════════════════════════════
# VoiceBox corre en GPU (VRAM). Si cualquier librería ML (torch, CTranslate2,
# numpy+CUDA, etc.) inicializa CUDA en este proceso, la VRAM se agota y el
# driver NVIDIA crashea → CLOCK_WATCHDOG_TIMEOUT BSOD en Windows.
#
# Solución: deshabilitar CUDA a nivel de OS para este proceso ANTES de que
# cualquier import pueda tocarlo. Las variables se setean en tts_engine.py
# también, pero ese módulo se importa después — este bloque garantiza que
# NADA que se importe antes (config, ollama, requests, etc.) pueda habilitar CUDA.
#
# REGLA: NO mover ni eliminar este bloque. No cambiar "-1" por "".
# Documentado en: memory/project_voicebox.md
# ══════════════════════════════════════════════════════════════════════════════
import os as _os
_os.environ["CUDA_VISIBLE_DEVICES"]   = "-1"   # "-1" = ocultar toda GPU (NO "" en Windows)
_os.environ["CUDA_DEVICE_ORDER"]      = "PCI_BUS_ID"
_os.environ["NUMBA_DISABLE_JIT"]      = "1"    # deshabilita compilación JIT de Numba (usa CUDA)
_os.environ["TF_CPP_MIN_LOG_LEVEL"]   = "3"    # TensorFlow silencioso si está instalado
_os.environ.setdefault("OMP_NUM_THREADS",       "1")  # limitar threads OpenMP
_os.environ.setdefault("MKL_NUM_THREADS",       "1")  # limitar threads MKL
_os.environ.setdefault("OPENBLAS_NUM_THREADS",  "1")  # limitar threads OpenBLAS
_os.environ.setdefault("NUMEXPR_NUM_THREADS",   "1")  # limitar threads NumExpr
# ══════════════════════════════════════════════════════════════════════════════

import argparse
import asyncio
import atexit
import io
import json
import logging
import os
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en stdout/stderr para que los emojis no rompan en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Fix asyncio Windows: el ProactorEventLoop de Python 3.10 no limpia bien
# los subprocesos y lanza "RuntimeError: Event loop is closed" en __del__.
# WindowsSelectorEventLoopPolicy elimina ese ruido sin afectar la funcionalidad.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Añadir directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))

# ─── Singleton: solo una instancia puede correr a la vez ─────────────────────
# Si hay dos instancias de main.py corriendo al mismo tiempo, ambos commanders
# hacen getUpdates → Telegram responde 409 Conflict → el notifier no puede
# recibir el click de ✅ Publicar durante la aprobación. El lock file evita esto.
_LOCK_FILE = Path(__file__).parent / ".main_running.lock"

def _acquire_lock() -> None:
    if _LOCK_FILE.exists():
        try:
            existing_pid = int(_LOCK_FILE.read_text().strip())
            import psutil
            if psutil.pid_exists(existing_pid):
                print(f"⚠️  Instancia anterior detectada (PID {existing_pid}) — cerrándola...")
                try:
                    proc = psutil.Process(existing_pid)
                    proc.terminate()          # SIGTERM — cierre limpio
                    proc.wait(timeout=config.PROCESS_TERM_TIMEOUT)
                    print(f"   Instancia anterior cerrada.")
                except psutil.TimeoutExpired:
                    proc.kill()               # forzar si no respondió en 10s
                    print(f"   Instancia anterior forzada a cerrar.")
                except Exception:
                    pass
        except Exception:
            pass  # PID inválido o psutil no disponible → continuar
    _LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)

def _release_lock() -> None:
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass

import config
from modules import script_generator, tts_engine, video_assembler, pexels_fetcher
from modules import growth_agent
from modules import analytics_agent, ceo_report
from modules.tiktok_growth_agent import run_tiktok_growth
from modules import telegram_commander
from modules.company import CEOOrchestrator

logger = logging.getLogger("main")

# ─── Instancia global del CEO (se crea una vez al arrancar) ──────────────────
ceo = CEOOrchestrator()

# ─── Cola de publicación programada ──────────────────────────────────────────
PENDING_QUEUE_FILE = config.OUTPUT_DIR / "pending_queue.json"

# Próximo slot programado — actualizado por el scheduler, leído por /next
_next_slot: "datetime | None" = None


def _queue_load() -> list:
    """Lee la cola de videos pendientes de publicar."""
    if not PENDING_QUEUE_FILE.exists():
        return []
    try:
        with open(PENDING_QUEUE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _queue_dump(items: list) -> None:
    PENDING_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _next_scheduled_slot() -> "datetime":
    """Devuelve el siguiente slot programado (hoy o mañana)."""
    import datetime as _dt
    now = _dt.datetime.now()
    slots = _daily_schedule()
    future = [t for t in slots if t > now]
    if future:
        return future[0]
    # Todos los slots de hoy ya pasaron → primer slot de mañana
    tomorrow = now.date() + _dt.timedelta(days=1)
    windows = [getattr(config, "SCHEDULE_WIN1", (11, 13))]
    min_h, max_h = windows[0]
    return _dt.datetime.combine(
        tomorrow, _dt.time(random.randint(min_h, max_h), random.randint(0, 59))
    )


def _queue_save(result: dict, run_dir: Path) -> str:
    """
    Encola un video aprobado para publicar en el próximo slot.
    Devuelve el horario programado como string legible.
    """
    production = result.get("production", {})
    script     = result.get("script", {})
    scheduled  = _next_scheduled_slot()

    item = {
        "run_dir":       str(run_dir),
        "video_path":    production.get("video_path", ""),
        "thumbnail_path": production.get("thumbnail_path", ""),
        "audio_duration": production.get("audio_duration", 0),
        "script": {
            "title":           script.get("title", ""),
            "description":     script.get("description", ""),
            "tags":            script.get("tags", []),
            "narrator_gender": script.get("narrator_gender", "auto"),
            "script_text":     script.get("script_text", ""),
        },
        "queued_at":    datetime.now().isoformat(),
        "scheduled_for": scheduled.isoformat(),
    }

    items = _queue_load()
    items.append(item)
    _queue_dump(items)
    logger.info(f"Cola: video encolado para {scheduled.strftime('%d/%m/%Y %H:%M')}")
    return scheduled.strftime("%d/%m/%Y a las %H:%M")


def _queue_pop() -> "dict | None":
    """Extrae y devuelve el primer video de la cola (o None si está vacía)."""
    items = _queue_load()
    if not items:
        return None
    item  = items.pop(0)
    _queue_dump(items)
    return item


def _publish_queued_video(item: dict) -> bool:
    """Publica un video previamente aprobado y guardado en la cola."""
    video_path = item.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        logger.error(f"Cola: video no encontrado en disco: {video_path}")
        telegram_commander.notify("❌ Cola: video no encontrado en disco — generando uno nuevo...")
        return False

    script = item.get("script", {})
    try:
        publish_result: dict = {"youtube_url": "", "tiktok_url": "", "video_id": ""}
        if getattr(config, "YOUTUBE_UPLOAD_ENABLED", False):
            publish_result = ceo.publishing.run(
                video_path=video_path,
                script=script,
                thumbnail_path=item.get("thumbnail_path", ""),
                audio_duration=item.get("audio_duration", 0),
            )

        if getattr(config, "TELEGRAM_BOT_TOKEN", ""):
            from modules import telegram_notifier
            thumb_p = Path(item["thumbnail_path"]) if item.get("thumbnail_path") else None
            telegram_notifier.send_upload_confirmation(
                title=script.get("title", ""),
                youtube_url=publish_result.get("youtube_url", ""),
                thumbnail_path=thumb_p if thumb_p and thumb_p.exists() else None,
                duration_s=item.get("audio_duration", 0),
                video_size_mb=Path(video_path).stat().st_size / (1024 * 1024),
                word_count=len(script.get("script_text", "").split()),
                description=script.get("description", ""),
                tags=script.get("tags", []),
            )

        logger.info(f"Cola: video publicado — {publish_result.get('youtube_url', 'sin URL')}")
        return True

    except Exception as e:
        logger.error(f"Cola: error publicando video encolado: {e}", exc_info=True)
        telegram_commander.notify_error("publicar_cola", str(e))
        return False

# ─── Configurar logging ───────────────────────────────────────────────────────

def setup_logging(run_timestamp: str) -> logging.Logger:
    """Configura logging a consola y archivo."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOGS_DIR / f"run_{run_timestamp}.log"

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=config.LOG_FORMAT,
        datefmt=config.LOG_DATE_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ]
    )
    return logging.getLogger("main")


# ─── Gestión de topics ────────────────────────────────────────────────────────

def get_next_topic() -> str:
    """
    Retorna el siguiente topic de la lista rotatoria.
    Guarda el índice en topics_index.json para persistencia entre ejecuciones.

    Returns:
        String con el tema del próximo video
    """
    index = 0

    if config.TOPICS_INDEX_FILE.exists():
        try:
            with open(config.TOPICS_INDEX_FILE) as f:
                data = json.load(f)
                index = data.get("index", 0)
        except Exception:
            index = 0

    topic = config.TOPICS[index % len(config.TOPICS)]
    next_index = (index + 1) % len(config.TOPICS)

    with open(config.TOPICS_INDEX_FILE, "w") as f:
        json.dump({"index": next_index, "last_topic": topic}, f, indent=2)

    return topic


# ─── Verificación de servicios ────────────────────────────────────────────────

def check_services() -> dict:
    """
    Verifica que todos los servicios locales estén corriendo antes de ejecutar.

    Chequea:
    - Ollama en localhost:11434
    - ffmpeg instalado

    Returns:
        Dict con estado de cada servicio y modelo/backend detectado

    Raises:
        SystemExit: Si un servicio crítico no está disponible
    """
    logger = logging.getLogger("main.check_services")
    results = {}

    print("\n" + "="*60)
    print("  VERIFICANDO SERVICIOS LOCALES")
    print("="*60)

    # ── Verificar Ollama ───────────────────────────────────────────────────────
    print(f"\n📡 Ollama ({config.OLLAMA_BASE_URL})...")
    ollama_ok = script_generator.check_ollama_running()

    if ollama_ok:
        models = script_generator.get_available_models()
        model_ok, exact_name = script_generator.check_model_available(config.OLLAMA_MODEL)

        if model_ok:
            display = f"'{exact_name}'" if exact_name != config.OLLAMA_MODEL else f"'{config.OLLAMA_MODEL}'"
            print(f"   ✅ Ollama corriendo — modelo {display} disponible")
        else:
            print(f"   ❌ Ollama corriendo pero modelo '{config.OLLAMA_MODEL}' NO encontrado")
            if models:
                print(f"   Modelos instalados:")
                for m in models:
                    print(f"     • {m}")
                print(f"\n   Opciones:")
                print(f"     1. Cambiar OLLAMA_MODEL={models[0]} en tu .env")
                print(f"     2. Descargar el modelo: ollama pull {config.OLLAMA_MODEL}")
            else:
                print(f"   No hay ningún modelo instalado.")
                print(f"   → Instalar con: ollama pull mistral")
            sys.exit(1)

        results["ollama"] = {"ok": True, "model": exact_name or config.OLLAMA_MODEL, "all_models": models}
    else:
        print(f"   ❌ Ollama NO está corriendo")
        print(f"   → Iniciar con: ollama serve")
        print(f"   → Instalar modelo: ollama pull {config.OLLAMA_MODEL}")
        sys.exit(1)

    # ── Verificar dependencias ─────────────────────────────────────────────────
    print(f"\n🔧 Dependencias del sistema...")
    ffmpeg_ok = _check_ffmpeg()
    if ffmpeg_ok:
        print(f"   ✅ ffmpeg encontrado")
    else:
        print(f"   ❌ ffmpeg NO encontrado — requerido para video/audio")
        print(f"   → Instalar con: winget install ffmpeg")
        print(f"   → O descargar de: https://ffmpeg.org/download.html")
        sys.exit(1)

    print("\n" + "="*60 + "\n")

    return results


def _check_ffmpeg() -> bool:
    """Verifica que ffmpeg está instalado y accesible."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False



def _cleanup_temp_files(run_dir: Path, keep_final: bool = True) -> None:
    """Elimina imágenes y audio temporal de la corrida actual."""
    logger = logging.getLogger("main.cleanup")
    try:
        images_dir = run_dir / "images"
        if images_dir.exists():
            shutil.rmtree(images_dir)
            logger.debug("Imágenes temporales eliminadas")

        audio_file = run_dir / "narration.mp3"
        if audio_file.exists():
            audio_file.unlink()
            logger.debug("Audio temporal eliminado")

    except Exception as e:
        logger.warning(f"Error limpiando temporales: {e}")


def _cleanup_old_runs(days_to_keep: int = config.CLEANUP_DAYS_TO_KEEP) -> None:
    """Elimina carpetas run_* en output/ con más de N días de antigüedad."""
    log = logging.getLogger("main.cleanup")
    cutoff = time.time() - days_to_keep * 86400
    output_dir = config.OUTPUT_DIR
    if not output_dir.exists():
        return
    removed = 0
    freed_mb = 0.0
    for folder in output_dir.iterdir():
        if not folder.is_dir() or not folder.name.startswith("run_"):
            continue
        if folder.stat().st_mtime < cutoff:
            try:
                size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                shutil.rmtree(folder)
                freed_mb += size / 1_048_576
                removed += 1
            except Exception as e:
                log.warning(f"No se pudo eliminar {folder.name}: {e}")
    if removed:
        log.info(f"Limpieza: {removed} carpeta(s) eliminada(s), {freed_mb:.1f} MB liberados")


def _rotate_logs(max_files: int = config.LOGS_MAX_FILES) -> None:
    """Mantiene solo los últimos N archivos de log en logs/."""
    log = logging.getLogger("main.cleanup")
    logs_dir = config.LOGS_DIR
    if not logs_dir.exists():
        return
    log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    to_delete = log_files[: max(0, len(log_files) - max_files)]
    for f in to_delete:
        try:
            f.unlink()
        except Exception as e:
            log.warning(f"No se pudo eliminar log {f.name}: {e}")
    if to_delete:
        log.info(f"Logs rotados: {len(to_delete)} archivo(s) eliminado(s)")


# ─── Modo test ────────────────────────────────────────────────────────────────

def run_tests() -> None:
    """
    Prueba cada módulo individualmente con datos mínimos.
    Útil para verificar la instalación antes de la primera corrida real.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = setup_logging(f"test_{timestamp}")

    print("\n" + "="*60)
    print("  MODO TEST — Verificando que todo funciona")
    print("="*60 + "\n")

    test_dir = config.OUTPUT_DIR / f"test_{timestamp}"
    test_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # ── Test 1: Servicios ──────────────────────────────────────────────────────
    print("[TEST 1/5] Verificando servicios (Ollama, ffmpeg)...")
    try:
        services = check_services()
        results["Servicios"] = "OK — todo corriendo"
    except SystemExit:
        results["Servicios"] = "FALLO — ver mensajes arriba"
        print("  Algunos servicios no estan disponibles.")

    # ── Test 2: Generar historia ───────────────────────────────────────────────
    categoria = config.TOPICS[0]
    print(f"\n[TEST 2/5] Generando historia con Ollama...")
    print(f"   Categoria: {categoria}")
    try:
        script = script_generator.generate_script(categoria)
        print(f"   Titulo del video   : {script['title']}")
        print(f"   Gancho de apertura : {script.get('hook', '?')}")
        print(f"   Giro de la historia: {script.get('giro', '?')}")
        print(f"   Pregunta al final  : {script.get('pregunta', '?')}")
        print(f"   Palabras narradas  : {len(script['script_text'].split())} palabras (~{len(script['script_text'].split())//3}s de video)")
        results["Historia (Ollama)"] = "OK"
    except Exception as e:
        print(f"   ERROR: {e}")
        results["Historia (Ollama)"] = f"FALLO: {e}"
        script = {
            "title": "Test de confesion dramatica",
            "description": "Test description",
            "tags": ["#test"],
            "script_text": "Nunca debi revisar su celular. Llevabamos tres anos juntos. Encontre mensajes que me helaron la sangre. Era con mi mejor amiga. No dije nada. Me fui. Que harias tu en mi lugar?",
            "hook": "Nunca debi revisar su celular.",
            "contexto": "Llevabamos tres anos juntos.",
            "problema": "Encontre mensajes que me helaron la sangre.",
            "giro": "Era con mi mejor amiga.",
            "final": "No dije nada. Me fui.",
            "pregunta": "Que harias tu en mi lugar?",
            "scenes": [
                {"text": "Nunca debi revisar su celular.", "image_prompt": "woman looking at phone in shock, dark room, dramatic lighting"},
                {"text": "Que harias tu en mi lugar?", "image_prompt": "woman alone crying, cinematic close-up, emotional"},
            ]
        }

    # ── Test 3: Voz narradora ──────────────────────────────────────────────────
    print("\n[TEST 3/5] Generando la voz narradora...")
    tts_text = script.get("script_text", "Nunca debi revisar su celular.")
    try:
        audio_path = tts_engine.generate_audio(
            tts_text,
            str(test_dir / "test_audio.mp3")
        )
        duration = tts_engine.get_audio_duration(Path(audio_path))
        print(f"   Archivo de audio   : {Path(audio_path).name}")
        print(f"   Duracion del audio : {duration:.1f} segundos")
        results["Voz narradora"] = f"OK — {duration:.1f}s de audio"
    except Exception as e:
        print(f"   ERROR: {e}")
        results["Voz narradora"] = f"FALLO: {e}"
        audio_path = None

    # ── Test 4: Clips de Pexels ──────────────────────────────────────────────────
    print("\n[TEST 4/5] Descargando clips de stock video (Pexels)...")
    try:
        test_scenes = script.get("scenes", [])[:2]
        for i, s in enumerate(test_scenes, 1):
            print(f"   Escena {i}: {s.get('image_prompt', '')[:70]}")
        image_paths = pexels_fetcher.fetch_videos(
            test_scenes,
            str(test_dir / "images")
        )
        print(f"   Clips descargados  : {len(image_paths)}")
        results["Clips (Pexels)"] = f"OK — {len(image_paths)} clips"
    except Exception as e:
        print(f"   ERROR: {e}")
        results["Clips (Pexels)"] = f"FALLO: {e}"
        image_paths = []

    # ── Test 5: Video final ────────────────────────────────────────────────────
    print("\n[TEST 5/5] Ensamblando el video final...")
    if audio_path and image_paths:
        try:
            video_path = video_assembler.assemble_video(
                script=script,
                audio_path=audio_path,
                images=image_paths,
                output_path=str(test_dir / "test_video.mp4")
            )
            size_mb = Path(video_path).stat().st_size / (1024 * 1024)
            print(f"   Video guardado en  : {video_path}")
            print(f"   Tamanio del video  : {size_mb:.1f} MB")
            results["Video final"] = f"OK — {size_mb:.1f} MB"
        except Exception as e:
            print(f"   ERROR: {e}")
            results["Video final"] = f"FALLO: {e}"
    else:
        print("   Saltando — falta audio o imagenes del paso anterior")
        results["Video final"] = "SALTADO"

    # ── Resumen de tests ───────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  RESULTADO DE LA PRUEBA")
    print("="*60)
    all_ok = True
    for modulo, resultado in results.items():
        if "FALLO" in resultado:
            all_ok = False
        print(f"  {modulo:25} {resultado}")
    print("")
    if all_ok:
        print("  Todo funciona correctamente. Listo para generar videos.")
    else:
        print("  Hay errores. Revisa los mensajes de arriba para corregirlos.")
    print(f"\n  Archivos de prueba en: {test_dir}")
    print("="*60 + "\n")


# ─── Orquestador: lee la memoria y decide la estrategia del día ──────────────

def _orchestrator_briefing() -> dict:
    """
    Lee agent_memory y devuelve decisiones para el scheduler:
      trend       → "rising" | "stable" | "falling"
      top_topics  → lista de temas ganadores
      avoid_topics → temas a evitar
      action      → mensaje de acción recomendada

    Si la memoria tiene más de 72h o está vacía, devuelve defaults neutros.
    """
    try:
        from modules import agent_memory as _am
        mem = _am.load()
        ci = mem.get("content_insights", {})
        trend        = ci.get("trend", "stable")
        top_topics   = mem.get("top_topics", [])
        avoid_topics = mem.get("avoid_topics", [])
        avg_views    = ci.get("avg_views_per_video", 0)
        top_title    = ci.get("top_video_title", "")

        if trend == "rising":
            action = (
                f"Canal en ALZA (+views). Enfocando contenido en: {', '.join(top_topics[:2])}. "
                f"Referencia: '{top_title[:50]}'"
            )
        elif trend == "falling":
            action = (
                f"Canal CAYENDO. Evitando: {', '.join(avoid_topics[:2])}. "
                f"Rotando hacia temas frescos."
            )
        else:
            action = f"Canal ESTABLE. Promedio: {avg_views:,} vistas/video."

        return {
            "trend": trend,
            "top_topics": top_topics,
            "avoid_topics": avoid_topics,
            "action": action,
        }
    except Exception:
        return {"trend": "stable", "top_topics": [], "avoid_topics": [], "action": "Sin datos de memoria."}


# ─── Wrapper seguro para el scheduler ───────────────────────────────────────

def _safe_run_factory(topic: str | None = None, skip_publish: bool = False) -> bool:
    """
    Ejecuta el pipeline completo vía CEO Orchestrator (agentes expertos).
      True  → éxito (publicado o encolado)
      False → error real o max rechazos consecutivos

    skip_publish=True: genera y aprueba el video pero NO publica — lo encola
    para el próximo slot programado. Usado por /generate en Telegram.
    """
    max_retries = getattr(config, "MAX_WA_RETRIES", 3)

    for attempt in range(1, max_retries + 1):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        setup_logging(timestamp)
        run_dir = config.OUTPUT_DIR / f"run_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = ceo.run_pipeline(run_dir=run_dir, topic=topic, skip_publish=skip_publish)

            if result.get("approved") is False:
                _cleanup_temp_files(run_dir, keep_final=False)
                if attempt < max_retries:
                    logger.info(f"Video rechazado — generando nuevo (intento {attempt + 1}/{max_retries})...")
                    continue
                logger.warning(f"{max_retries} videos rechazados consecutivos — no se sube nada en este slot.")
                return False

            if result.get("queued"):
                scheduled_time = _queue_save(result, run_dir)
                telegram_commander.notify(
                    f"📥 Video aprobado y guardado en cola.\n"
                    f"⏰ Se publicará el {scheduled_time}"
                )

            _cleanup_temp_files(run_dir, keep_final=True)
            return True

        except KeyboardInterrupt:
            raise
        except SystemExit as e:
            logger.error(f"pipeline terminó con sys.exit({e.code}) — servicio caído.")
            return False
        except Exception as e:
            logger.error(f"pipeline lanzó excepción inesperada: {e}", exc_info=True)
            telegram_commander.notify_error("pipeline", str(e))
            return False

    return False


# ─── Scheduler multi-video con ventanas de audiencia pico ────────────────────

def _daily_schedule() -> list:
    """
    Calcula N horarios para HOY distribuidos en ventanas de audiencia pico.
    N está limitado por la rampa de publicación según antigüedad del canal:
      Semana 1-2 → 1 video/día  (canal nuevo: no generar alertas de spam)
      Semana 3-4 → 2 videos/día
      Semana 5+  → VIDEOS_PER_DAY completo
    """
    import datetime as _dt
    today = _dt.date.today()
    windows = [
        getattr(config, "SCHEDULE_WIN1", (11, 13)),
        getattr(config, "SCHEDULE_WIN2", (16, 18)),
        getattr(config, "SCHEDULE_WIN3", (20, 22)),
    ]
    n = getattr(config, "VIDEOS_PER_DAY", 3)

    # Rampa de publicación — canal nuevo empieza despacio
    age_weeks = getattr(config, "CHANNEL_AGE_WEEKS", 1)
    if age_weeks <= 2:
        max_daily = 1
    elif age_weeks <= 4:
        max_daily = 2
    else:
        max_daily = n
    n = min(n, max_daily)

    if age_weeks <= 4:
        logger.info(f"Rampa de publicación: canal semana {age_weeks} → {n} video(s)/día (máx={max_daily})")

    expanded = (windows * ((n // len(windows)) + 1))[:n]
    times = []
    for min_h, max_h in expanded:
        h = random.randint(min_h, max_h)
        m = random.randint(0, 59)
        times.append(_dt.datetime.combine(today, _dt.time(h, m)))
    return sorted(times)


def _run_analytics_and_report() -> None:
    """Delega el análisis diario al Analytics Agent vía CEO."""
    if not getattr(config, "ANALYTICS_ENABLED", True):
        return
    ceo.run_analytics()


def _run_scheduler(topic: str | None = None) -> None:
    """
    Publica VIDEOS_PER_DAY videos/día en ventanas de audiencia pico.
    Por defecto 3 videos: 11-13h, 16-18h, 20-22h (hora local).
    Analytics + CEO Report: una vez al día a las ANALYTICS_HOUR (default: 9h).
    """
    import datetime as _dt

    n    = getattr(config, "VIDEOS_PER_DAY", 3)
    a_h  = getattr(config, "ANALYTICS_HOUR", 9)
    w1   = getattr(config, "SCHEDULE_WIN1", (11, 13))
    w2   = getattr(config, "SCHEDULE_WIN2", (16, 18))
    w3   = getattr(config, "SCHEDULE_WIN3", (20, 22))
    ceo.brief_team()   # presenta el equipo activo al arrancar
    print(f"⏰ Scheduler: {n} videos/día en ventanas de audiencia pico")
    print(f"   WIN1: {w1[0]:02d}-{w1[1]:02d}h | WIN2: {w2[0]:02d}-{w2[1]:02d}h | WIN3: {w3[0]:02d}-{w3[1]:02d}h")
    print(f"   Analytics + CEO Report: todos los días a las {a_h:02d}:00h")
    print("   (Ctrl+C para detener)\n")

    _cleanup_old_runs(days_to_keep=config.CLEANUP_DAYS_TO_KEEP)
    _rotate_logs(max_files=config.LOGS_MAX_FILES)

    import threading as _thr

    analytics_done_today: str = ""
    channel_done_today: str   = ""
    growth_done_today: str    = ""
    _schedule_day: str        = ""
    today_slots: list         = []

    while True:
        now       = _dt.datetime.now()
        today_str = now.date().isoformat()

        # Recalcular slots solo cuando cambia el día
        if _schedule_day != today_str:
            _schedule_day = today_str
            today_slots   = _daily_schedule()
            logger.info(
                f"Slots para hoy ({today_str}): "
                f"{[t.strftime('%H:%M') for t in today_slots]}"
            )

        # ── Analítica diaria (una vez al día a las ANALYTICS_HOUR) ───────────
        analytics_target = _dt.datetime.combine(
            now.date(), _dt.time(a_h, random.randint(0, 29))
        )
        if (
            getattr(config, "ANALYTICS_ENABLED", True)
            and analytics_done_today != today_str
            and now >= analytics_target
        ):
            analytics_done_today = today_str
            _run_analytics_and_report()
            briefing = _orchestrator_briefing()
            logger.info(f"[ORQUESTADOR] {briefing['action']}")
            telegram_commander.notify(f"🧠 Orquestador: {briefing['action']}")

        # ── Canal Telegram: confesiones diarias ───────────────────────────────
        channel_hour   = a_h + 1
        channel_target = _dt.datetime.combine(now.date(), _dt.time(channel_hour, 0))
        if (
            getattr(config, "TELEGRAM_CHANNEL_ID", "")
            and channel_done_today != today_str
            and now >= channel_target
        ):
            channel_done_today = today_str
            n_daily = config.TELEGRAM_CHANNEL_DAILY
            logger.info(f"Canal Telegram: publicando {n_daily} confesiones del dia...")
            _thr.Thread(
                target=ceo.channel.run_daily_strategy,
                kwargs={"slots": n_daily},
                daemon=True,
                name="channel_daily",
            ).start()

        # ── Próximo slot ──────────────────────────────────────────────────────
        pending = [t for t in today_slots if t > now]
        if not pending:
            time.sleep(300)
            continue

        next_run  = pending[0]
        wait_secs = (next_run - now).total_seconds()

        global _next_slot
        _next_slot = next_run

        logger.info(
            f"Próximo video: {next_run.strftime('%d/%m/%Y a las %H:%M')} "
            f"(en {wait_secs / 3600:.1f}h)"
        )
        print(f"\n⏰ Próximo video: {next_run.strftime('%d/%m/%Y a las %H:%M')} "
              f"(en {wait_secs / 3600:.1f}h)\n")
        telegram_commander.notify_scheduler_next(
            next_run.strftime("%d/%m/%Y a las %H:%M"),
            wait_secs / 3600,
        )

        # ── Growth en background: UNA sola vez al día, solo si hay tiempo ─────
        # Condiciones: habilitado en config + aún no corrió hoy + faltan >30 min al slot
        if (
            getattr(config, "GROWTH_ENABLED", False)
            and growth_done_today != today_str
            and wait_secs > 1800  # mínimo 30 min antes del slot
        ):
            growth_done_today = today_str
            logger.info("Growth: iniciando sesión de engagement en background...")
            _thr.Thread(
                target=ceo.run_growth_session,
                kwargs={"do_own": False},
                daemon=True,
                name="growth_bg",
            ).start()

        # ── Esperar hasta el slot ─────────────────────────────────────────────
        while _dt.datetime.now() < next_run:
            time.sleep(60)
            now_inner   = _dt.datetime.now()
            inner_str   = now_inner.date().isoformat()
            inner_tgt   = _dt.datetime.combine(now_inner.date(), _dt.time(a_h, 0))
            if (
                getattr(config, "ANALYTICS_ENABLED", True)
                and analytics_done_today != inner_str
                and now_inner >= inner_tgt
            ):
                analytics_done_today = inner_str
                _run_analytics_and_report()

        _cleanup_old_runs(days_to_keep=config.CLEANUP_DAYS_TO_KEEP)
        _rotate_logs(max_files=config.LOGS_MAX_FILES)

        slot_str = next_run.strftime("%H:%M")
        logger.info(f"Slot {slot_str} alcanzado — iniciando publicación...")
        telegram_commander.notify(f"🎬 Slot {slot_str} — iniciando pipeline ahora...")

        queued = _queue_pop()
        if queued:
            logger.info("Cola: publicando video previamente aprobado...")
            telegram_commander.notify("📥 Publicando video guardado en cola...")
            ok = _publish_queued_video(queued)
            if not ok:
                logger.warning("Cola: falló — generando video nuevo...")
                _safe_run_factory()
        else:
            _safe_run_factory()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Shorts Factory — Generador automático de YouTube Shorts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --now      Generar y subir un video ahora mismo
  python main.py            Scheduler automático (cada 8h)
  python main.py --test     Probar cada módulo individualmente

Prerequisitos:
  - Ollama corriendo: ollama serve && ollama pull llama3.2
  - ffmpeg instalado: winget install ffmpeg
  - .env configurado con PEXELS_API_KEY, YOUTUBE_EMAIL, YOUTUBE_PASSWORD
        """
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="Ejecutar el pipeline completo ahora mismo"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Probar cada módulo individualmente"
    )
    parser.add_argument(
        "--topic",
        type=str,
        help="Tema específico para el video (por defecto: rotatorio automático)"
    )
    parser.add_argument(
        "--grow",
        action="store_true",
        help="Ejecutar solo el agente de crecimiento ahora (sin generar video)"
    )
    parser.add_argument(
        "--analytics",
        action="store_true",
        help="Ejecutar el agente analista ahora (stats del canal y videos)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generar y enviar el reporte ejecutivo por Telegram ahora"
    )
    parser.add_argument(
        "--channel",
        action="store_true",
        help="Publicar confesiones en el canal de Telegram ahora (sin esperar el scheduler)"
    )
    args = parser.parse_args()

    print("\n" + "="*60)
    _acquire_lock()  # ← evita múltiples instancias simultáneas (causa 409 en aprobación)

    print(f"  {config.CHANNEL_NAME} — Generador de YouTube Shorts")
    print("  Pexels + Groq/Ollama + Edge TTS")
    print("="*60)
    print(f"  Modelo de IA   : {config.OLLAMA_MODEL}")
    print(f"  Imagenes       : Pexels (stock video)")
    print(f"  Resolucion     : {config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT} px @ {config.FPS}fps")
    print(f"  Duracion video : Automatica (basada en narracion)")
    print(f"  Canal          : {config.CHANNEL_NAME}")
    print("="*60 + "\n")

    # Bot de Telegram: escucha comandos del CEO en background
    telegram_commander.start_bot_background()

    if args.test:
        run_tests()

    elif args.now:
        success = _safe_run_factory(topic=args.topic)
        sys.exit(0 if success else 1)

    elif getattr(args, "grow", False):
        setup_logging(datetime.now().strftime("%Y%m%d_%H%M%S"))
        result = growth_agent.run_growth_session(do_own=True)
        print(f"\n  Comentarios externos : {result['external']}")
        print(f"  Replies propios      : {result['own']}")
        print(f"  Omitidos             : {result['skipped']}")
        try:
            print("  Ejecutando TikTok growth...")
            run_tiktok_growth()
            print("  TikTok growth completado")
        except Exception as e_ttg:
            print(f"  TikTok growth falló (no crítico): {e_ttg}")
        print()

    elif getattr(args, "analytics", False):
        setup_logging(datetime.now().strftime("%Y%m%d_%H%M%S"))
        snap = analytics_agent.run_analytics_session()
        print(f"\n  Canal         : {snap.channel_id}")
        print(f"  Suscriptores  : {snap.subscribers}")
        print(f"  Vistas 28d    : {snap.views_28d}")
        print(f"  Watch time    : {snap.watch_time_h_28d}h")
        print(f"  Videos        : {len(snap.videos)}")
        print(f"  Top video     : {snap.top_video_title[:60]}")
        if snap.errors:
            print(f"  Errores       : {snap.errors}")
        print()

    elif getattr(args, "report", False):
        setup_logging(datetime.now().strftime("%Y%m%d_%H%M%S"))
        report_text = ceo_report.run_ceo_report(send=True)
        print(f"\n{'='*60}")
        print(report_text)
        print(f"{'='*60}\n")

    elif getattr(args, "channel", False):
        setup_logging(datetime.now().strftime("%Y%m%d_%H%M%S"))
        n_daily = config.TELEGRAM_CHANNEL_DAILY
        channel_id = getattr(config, "TELEGRAM_CHANNEL_ID", "")
        if not channel_id:
            print("\n❌ TELEGRAM_CHANNEL_ID no configurado en .env")
            print("   Ejemplo: TELEGRAM_CHANNEL_ID=@GataCuriosaS")
            sys.exit(1)
        print(f"\n📢 Publicando {n_daily} posts en {channel_id}...")
        published = ceo.channel.run_daily_strategy(slots=n_daily)
        print(f"\n✅ {published}/{n_daily} posts publicados en {channel_id}")

    else:
        _run_scheduler(topic=args.topic)


if __name__ == "__main__":
    main()
