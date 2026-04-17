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

import argparse
import asyncio
import concurrent.futures
import io
import json
import logging
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

import requests

# Añadir directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))

import config
from modules import script_generator, tts_engine, video_assembler, youtube_uploader, scraper, pexels_fetcher

logger = logging.getLogger("main")

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


# ─── Pipeline principal ───────────────────────────────────────────────────────

def run_factory(topic: str | None = None) -> bool | None:
    """
    Ejecuta el pipeline completo de generación y subida de un video.

    Flujo:
    1. Obtener topic → 2. Generar script → 3. TTS → 4. Clips Pexels → 5. Video → 6. Upload

    Args:
        topic: Tema específico. Si es None, usa la lista rotatoria.

    Returns:
        True si todo el pipeline se completó exitosamente
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = setup_logging(timestamp)

    logger.info("=" * 60)
    logger.info("  SHORTS FACTORY — INICIO")
    logger.info("=" * 60)

    # Crear directorio de trabajo para esta corrida
    run_dir = config.OUTPUT_DIR / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    images_dir = run_dir / "images"

    step_times = {}
    total_start = time.time()

    try:
        # ── PASO 1: Buscar historia real en Reddit ─────────────────────────────
        t0 = time.time()
        if topic:
            # --topic manual: usar el sistema anterior de generacion desde tema
            logger.info(f"[1/6] MODO MANUAL — tema: {topic}")
            story = None
        else:
            logger.info("[1/6] Buscando historia real en Reddit...")
            story = scraper.get_story()
            if story:
                logger.info(f"      Historia encontrada:")
                logger.info(f"      Titulo original : {story['titulo'][:70]}")
                logger.info(f"      Fuente          : {story['fuente']} ({story['upvotes']} upvotes)")
                logger.info(f"      Longitud        : {len(story['historia'])} caracteres")
            else:
                logger.warning("      No se encontro historia en Reddit — usando tema rotatorio")
                topic = get_next_topic()
                logger.info(f"      Categoria de respaldo: {topic}")
        step_times["scraping"] = time.time() - t0

        # ── PASO 2: Narrar/generar historia con Ollama ─────────────────────────
        t0 = time.time()
        if story:
            logger.info("[2/6] Narrando historia real con Ollama (sin resumir)...")
            script = script_generator.generate_script_from_story(story)
        else:
            logger.info("[2/6] Generando historia desde tema con Ollama...")
            script = script_generator.generate_script(topic)
        step_times["script"] = time.time() - t0
        _g = script.get("narrator_gender", "auto")
        _g_emoji = "👩" if _g == "female" else ("👨" if _g == "male" else "🔍")
        logger.info(f"      Titulo del video  : {script['title']}")
        logger.info(f"      Narrador          : {_g_emoji} {_g.upper()}")
        logger.info(f"      Gancho de apertura: {script.get('hook', '—')}")
        logger.info(f"      Pregunta final    : {script.get('pregunta', '—')}")
        logger.info(f"      Palabras narradas : {len(script['script_text'].split())} palabras")
        logger.info(f"      Escenas generadas : {len(script['scenes'])} | Tardo: {step_times['script']:.0f}s")

        # ── PASOS 3+4: Voz narrativa (TTS) + Imágenes en PARALELO ────────────────
        narrator_gender = script.get("narrator_gender", "auto")
        if narrator_gender not in ("female", "male"):
            narrator_gender = "auto"
        gender_label = {"female": "Mujer", "male": "Hombre", "auto": "Auto-detectado"}.get(narrator_gender, "Auto")

        logger.info(f"[3+4/6] TTS ({gender_label}) + {len(script['scenes'])} clips [Pexels] en paralelo...")

        t0 = time.time()

        def _run_tts():
            return tts_engine.generate_audio(
                script["script_text"],
                output_path=str(run_dir / "narration.mp3"),
                gender=narrator_gender,
            )

        def _run_images():
            return pexels_fetcher.fetch_videos(
                script["scenes"],
                str(images_dir),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _pool:
            _tts_f    = _pool.submit(_run_tts)
            _images_f = _pool.submit(_run_images)
            audio_path  = _tts_f.result()
            image_paths = _images_f.result()

        parallel_time = time.time() - t0
        step_times["tts"]    = parallel_time
        step_times["images"] = parallel_time
        audio_duration = tts_engine.get_audio_duration(Path(audio_path))
        logger.info(f"      TTS + Imágenes listos en {parallel_time:.0f}s (en paralelo)")
        logger.info(f"      Audio: {audio_duration:.1f}s | Imagenes: {len(image_paths)}")

        # ── PASO 5: Ensamblar video final ──────────────────────────────────────
        t0 = time.time()
        logger.info("[5/6] Ensamblando el video final...")
        video_path = video_assembler.assemble_video(
            script=script,
            audio_path=audio_path,
            images=image_paths,
            output_path=str(run_dir / "final_video.mp4")
        )
        step_times["video"] = time.time() - t0
        video_size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        logger.info(f"      Video guardado    : {video_path}")
        logger.info(f"      Tamaño            : {video_size_mb:.1f} MB | Tardó: {step_times['video']:.0f}s")

        # Generar thumbnail personalizado
        try:
            thumbnail_path = video_assembler.generate_thumbnail(
                script=script,
                images=image_paths,
                output_path=str(run_dir / "thumbnail.jpg"),
            )
            logger.info(f"      Thumbnail         : {thumbnail_path}")
        except Exception as e_thumb:
            logger.warning(f"      Thumbnail no generado: {e_thumb}")

        # ── PASO 6: Aprobación vía WhatsApp (opcional) ────────────────────────
        if config.WHATSAPP_APPROVAL_ENABLED:
            from modules import whatsapp_notifier
            thumbnail_p = Path(run_dir / "thumbnail.jpg")
            logger.info("[6/7] Enviando video a WhatsApp para aprobacion...")
            approved = whatsapp_notifier.send_approval_request(
                video_path=Path(video_path),
                thumbnail_path=thumbnail_p if thumbnail_p.exists() else None,
                title=script["title"],
                duration_s=audio_duration,
                description=script.get("description", ""),
                tags=script.get("tags", []),
                narrator_gender=script.get("narrator_gender", "auto"),
            )
            if not approved:
                logger.info("      Video RECHAZADO vía WhatsApp — se generará un nuevo video")
                _cleanup_temp_files(run_dir, keep_final=False)
                return None  # None = rechazado, distinto de False = error real
            logger.info("      Video APROBADO via WhatsApp — continuando...")
            youtube_step_label = "[7/7]"
        else:
            youtube_step_label = "[6/6]"

        # ── PASO 7 (o 6): Subir a YouTube ─────────────────────────────────────
        youtube_url = ""
        if not config.YOUTUBE_UPLOAD_ENABLED:
            logger.info(f"{youtube_step_label} Subida a YouTube: DESACTIVADA en .env")
            success = True
        else:
            t0 = time.time()
            logger.info(f"{youtube_step_label} Subiendo a YouTube...")
            thumbnail_p = run_dir / "thumbnail.jpg"
            # Fusionar hashtags del script con los hashtags base del nicho
            base_tags = getattr(config, "BASE_HASHTAGS", [])
            all_tags = list(dict.fromkeys(script.get("tags", []) + base_tags))

            # Descripción profesional y consistente para todos los videos:
            # 1. Frase corta del conflicto (del LLM, 80-120 chars)
            # 2. CTA de seguimiento (rotatorio del config)
            # 3. Pie de afiliado si está configurado
            desc_line = script["description"].strip()
            cta_follow = random.choice(config.CTA_FOLLOW)
            affiliate = getattr(config, "AFFILIATE_FOOTER", "")

            description = f"{desc_line}\n\n{cta_follow}"
            if affiliate:
                description = f"{description}\n\n{affiliate}"

            upload_result = youtube_uploader.upload_to_youtube(
                video_path=video_path,
                title=script["title"],
                description=description,
                tags=all_tags,
                thumbnail_path=str(thumbnail_p) if thumbnail_p.exists() else "",
            )
            step_times["upload"] = time.time() - t0
            # upload_result: str (URL, puede ser "") = OK | None = fallo
            success = upload_result is not None
            if success:
                youtube_url = upload_result or ""
                if youtube_url:
                    logger.info(f"      Video publicado: {youtube_url} | Tardó: {step_times['upload']:.0f}s")
                else:
                    logger.info(f"      Video publicado en YouTube (URL no capturada) | Tardó: {step_times['upload']:.0f}s")

                # Notificar por WhatsApp con el enlace del video
                if getattr(config, "WHATSAPP_APPROVAL_ENABLED", False) or getattr(config, "WHATSAPP_TO", ""):
                    try:
                        from modules import whatsapp_notifier
                        thumbnail_p = Path(run_dir / "thumbnail.jpg")
                        whatsapp_notifier.send_upload_confirmation(
                            title=script["title"],
                            youtube_url=youtube_url,
                            thumbnail_path=thumbnail_p if thumbnail_p.exists() else None,
                            duration_s=audio_duration,
                            video_size_mb=video_size_mb,
                            word_count=len(script["script_text"].split()),
                            description=script.get("description", ""),
                            tags=script.get("tags", []),
                            hook=script.get("hook", ""),
                            pregunta=script.get("pregunta", ""),
                        )
                    except Exception as e_wa:
                        logger.warning(f"      Notificacion WhatsApp fallo (no critico): {e_wa}")
            else:
                logger.error(f"      ERROR: No se pudo subir el video | Tardó: {step_times['upload']:.0f}s")

        # ── Resumen final ──────────────────────────────────────────────────────
        total_time = time.time() - total_start
        fuente_str = script.get("_fuente", topic or "generado")
        logger.info("\n" + "="*60)
        logger.info("  VIDEO COMPLETADO")
        logger.info("="*60)
        logger.info(f"  Fuente historia  : {fuente_str}")
        logger.info(f"  Titulo del video : {script['title']}")
        logger.info(f"  Gancho inicial   : {script.get('hook', '—')}")
        logger.info(f"  Pregunta final   : {script.get('pregunta', '—')}")
        logger.info(f"  Palabras narradas: {len(script['script_text'].split())} palabras")
        logger.info(f"  Duracion audio   : {audio_duration:.0f} segundos")
        logger.info(f"  Archivo          : {video_path}")
        logger.info(f"  Tamanio          : {video_size_mb:.1f} MB")
        if youtube_url:
            logger.info(f"  YouTube          : {youtube_url}")
        logger.info("")
        logger.info(f"  Buscar historia  : {step_times.get('scraping', 0):.0f}s")
        logger.info(f"  Narrar (Ollama)  : {step_times.get('script', 0):.0f}s")
        logger.info(f"  TTS + Imagenes   : {step_times.get('images', 0):.0f}s (paralelo)")
        logger.info(f"  Ensamblar video  : {step_times.get('video', 0):.0f}s")
        if step_times.get("upload"):
            logger.info(f"  Subir YouTube    : {step_times.get('upload', 0):.0f}s")
        logger.info(f"  TIEMPO TOTAL     : {total_time:.0f}s ({total_time/60:.1f} minutos)")
        logger.info("="*60 + "\n")

        # ── Limpiar temporales (mantener solo el video final) ──────────────────
        _cleanup_temp_files(run_dir, keep_final=True)

        return success

    except Exception as e:
        logger.error(f"Error fatal en el pipeline: {e}", exc_info=True)
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


def _cleanup_old_runs(days_to_keep: int = 7) -> None:
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


def _rotate_logs(max_files: int = 30) -> None:
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


# ─── Test WhatsApp ────────────────────────────────────────────────────────────

def test_whatsapp() -> None:
    """
    Diagnóstico completo de la conexión WhatsApp/Twilio.
    Envía un mensaje de prueba real y reporta cada paso.
    Uso: python main.py --test-wa
    """
    print("\n" + "="*60)
    print("  TEST WhatsApp / Twilio")
    print("="*60)

    # 1. Verificar credenciales
    sid   = getattr(config, "TWILIO_ACCOUNT_SID", "")
    token = getattr(config, "TWILIO_AUTH_TOKEN", "")
    frm   = getattr(config, "TWILIO_WHATSAPP_FROM", "")
    to    = getattr(config, "WHATSAPP_TO", "")

    checks = {
        "TWILIO_ACCOUNT_SID":  sid,
        "TWILIO_AUTH_TOKEN":   token,
        "TWILIO_WHATSAPP_FROM": frm,
        "WHATSAPP_TO":         to,
    }
    ok = True
    for k, v in checks.items():
        status = "✅" if v else "❌ FALTA"
        print(f"  {status}  {k}: {'***' + v[-4:] if v else 'no configurado'}")
        if not v:
            ok = False

    if not ok:
        print("\n❌ Faltan credenciales. Configura el .env con los valores de Twilio.")
        print("   Guía: https://www.twilio.com/console/messaging/whatsapp/sandbox")
        print("   Luego envía 'join <palabra>' al sandbox desde tu WhatsApp.")
        return

    # 2. Intentar importar twilio
    try:
        from twilio.rest import Client
    except ImportError:
        print("\n❌ twilio no instalado. Ejecuta: pip install twilio")
        return

    print("\n  Credenciales OK. Enviando mensaje de prueba...")

    try:
        client   = Client(sid, token)
        from_ws  = f"whatsapp:{frm}"
        to_ws    = f"whatsapp:{to}"
        msg      = client.messages.create(
            from_=from_ws,
            to=to_ws,
            body=(
                f"✅ *Test de conexión — {config.CHANNEL_NAME}*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Si recibes este mensaje, las notificaciones de YouTube funcionan correctamente.\n\n"
                "_Este es un mensaje automático de prueba._"
            ),
        )
        print(f"\n✅ Mensaje enviado correctamente.")
        print(f"   SID: {msg.sid}")
        print(f"   Estado: {msg.status}")
        print(f"\n   Revisa WhatsApp en {to} — debería llegar en segundos.")
        print("\n   Si NO llega:")
        print("   1. Abre WhatsApp y envía 'join <palabra>' al número del sandbox")
        print(f"      → número sandbox: {frm}")
        print("   2. Las sesiones del sandbox expiran cada 72h sin actividad")
        print("   3. Verifica que WHATSAPP_TO tiene el formato +34612345678")
    except Exception as e:
        print(f"\n❌ Error enviando mensaje: {e}")
        print("\n   Causas frecuentes:")
        print("   • Sesión del sandbox expirada → reenvía 'join <palabra>' al sandbox")
        print("   • TWILIO_WHATSAPP_FROM incorrecto (debe ser el número del sandbox)")
        print("   • WHATSAPP_TO sin código de país (ej: +34612345678 no 612345678)")
        print("   • Credenciales de Twilio incorrectas")

    print("\n" + "="*60)


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


# ─── Wrapper seguro para el scheduler ───────────────────────────────────────

def _safe_run_factory(topic: str | None = None) -> bool:
    """
    Llama a run_factory() sin dejar morir al scheduler si algo falla.

    Maneja tres resultados de run_factory():
      True  → éxito, termina
      False → error real, no reintenta
      None  → rechazado por WhatsApp, regenera un video nuevo (hasta MAX_WA_RETRIES veces)
    """
    max_retries = getattr(config, "MAX_WA_RETRIES", 3)

    for attempt in range(1, max_retries + 1):
        try:
            result = run_factory(topic=topic)

            if result is True:
                return True

            if result is None:
                # Usuario respondió "no" por WhatsApp → generar video nuevo
                if attempt < max_retries:
                    logger.info(
                        f"WhatsApp: video rechazado — generando nuevo video "
                        f"(intento {attempt + 1}/{max_retries})..."
                    )
                    continue
                else:
                    logger.warning(
                        f"WhatsApp: {max_retries} videos rechazados consecutivos — "
                        "no se sube nada en este slot."
                    )
                    return False

            # result is False → error real, no reintentar
            return False

        except KeyboardInterrupt:
            raise
        except SystemExit as e:
            logger.error(f"run_factory terminó con sys.exit({e.code}) — servicio caído.")
            return False
        except Exception as e:
            logger.error(f"run_factory lanzó excepción inesperada: {e}", exc_info=True)
            return False

    return False


# ─── Scheduler multi-video con ventanas de audiencia pico ────────────────────

def _daily_schedule() -> list:
    """
    Calcula VIDEOS_PER_DAY horarios para HOY, distribuidos en ventanas pico.
    Ventanas por defecto (audiencia latinoamericana):
      WIN1: 11-13h (almuerzo) | WIN2: 16-18h (tarde) | WIN3: 20-22h (noche)
    """
    import datetime as _dt
    today = _dt.date.today()
    windows = [
        getattr(config, "SCHEDULE_WIN1", (11, 13)),
        getattr(config, "SCHEDULE_WIN2", (16, 18)),
        getattr(config, "SCHEDULE_WIN3", (20, 22)),
    ]
    n = getattr(config, "VIDEOS_PER_DAY", 3)
    expanded = (windows * ((n // len(windows)) + 1))[:n]
    times = []
    for min_h, max_h in expanded:
        h = random.randint(min_h, max_h)
        m = random.randint(0, 59)
        times.append(_dt.datetime.combine(today, _dt.time(h, m)))
    return sorted(times)


def _run_scheduler(topic: str | None = None) -> None:
    """
    Publica VIDEOS_PER_DAY videos/día en ventanas de audiencia pico.
    Por defecto 3 videos: 11-13h, 16-18h, 20-22h (hora local).
    """
    import datetime as _dt

    n  = getattr(config, "VIDEOS_PER_DAY", 3)
    w1 = getattr(config, "SCHEDULE_WIN1", (11, 13))
    w2 = getattr(config, "SCHEDULE_WIN2", (16, 18))
    w3 = getattr(config, "SCHEDULE_WIN3", (20, 22))
    print(f"⏰ Scheduler: {n} videos/día en ventanas de audiencia pico")
    print(f"   WIN1: {w1[0]:02d}-{w1[1]:02d}h | WIN2: {w2[0]:02d}-{w2[1]:02d}h | WIN3: {w3[0]:02d}-{w3[1]:02d}h")
    print("   (Ctrl+C para detener)\n")

    _cleanup_old_runs(days_to_keep=7)
    _rotate_logs(max_files=30)

    # Primer video: inmediatamente al arrancar
    _safe_run_factory(topic=topic)

    while True:
        import datetime as _dt
        now = _dt.datetime.now()

        # Slots de hoy que aún no han pasado
        pending = [t for t in _daily_schedule() if t > now]

        if not pending:
            # Todos los slots de hoy ya pasaron → programar para mañana
            tomorrow = _dt.date.today() + _dt.timedelta(days=1)
            pending = [
                t.replace(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day)
                for t in _daily_schedule()
            ]

        for next_run in pending:
            wait_secs = (next_run - _dt.datetime.now()).total_seconds()
            if wait_secs <= 0:
                continue

            logger.info(
                f"Próximo video: {next_run.strftime('%d/%m/%Y a las %H:%M')} "
                f"(en {wait_secs / 3600:.1f}h)"
            )
            print(f"\n⏰ Próximo video: {next_run.strftime('%d/%m/%Y a las %H:%M')} "
                  f"(en {wait_secs / 3600:.1f}h)\n")

            while _dt.datetime.now() < next_run:
                time.sleep(60)

            _cleanup_old_runs(days_to_keep=7)
            _rotate_logs(max_files=30)
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
        "--test-wa",
        action="store_true",
        help="Diagnosticar y probar la conexión WhatsApp/Twilio"
    )
    parser.add_argument(
        "--topic",
        type=str,
        help="Tema específico para el video (por defecto: rotatorio automático)"
    )

    args = parser.parse_args()

    print("\n" + "="*60)
    print(f"  {config.CHANNEL_NAME} — Generador de YouTube Shorts")
    print("  Pexels + Groq/Ollama + Edge TTS")
    print("="*60)
    print(f"  Modelo de IA   : {config.OLLAMA_MODEL}")
    print(f"  Imagenes       : Pexels (stock video)")
    print(f"  Resolucion     : {config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT} px @ {config.FPS}fps")
    print(f"  Duracion video : Automatica (basada en narracion)")
    print(f"  Canal          : {config.CHANNEL_NAME}")
    print("="*60 + "\n")

    if args.test:
        run_tests()

    elif getattr(args, "test_wa", False):
        test_whatsapp()

    elif args.now:
        success = run_factory(topic=args.topic)
        sys.exit(0 if success else 1)

    else:
        _run_scheduler(topic=args.topic)


if __name__ == "__main__":
    main()
