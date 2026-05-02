"""
server.py — Entry point para Render
  - Flask en puerto $PORT → UptimeRobot hace ping a /health cada 5 min
  - Scheduler del bot corre en hilo background
  - Restaura sesión de Chrome desde CHROME_SESSION_B64 al arrancar
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

import base64
import io
import logging
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("server")

# ── Restaurar sesión de Chrome ─────────────────────────────────────────────────

def _restore_chrome_session() -> None:
    b64 = os.environ.get("CHROME_SESSION_B64", "").strip()
    if not b64:
        log.warning("CHROME_SESSION_B64 no configurada — Chrome abrirá sin sesión de YouTube")
        return
    try:
        profile_dir = Path("chrome_profile/Default")
        profile_dir.mkdir(parents=True, exist_ok=True)
        raw = base64.b64decode(b64)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            tar.extractall(str(profile_dir))
        log.info(f"Sesión Chrome restaurada ({len(raw)//1024} KB)")
    except Exception as e:
        log.error(f"No se pudo restaurar sesión Chrome: {e}")


# ── Iniciar Xvfb (display virtual para Chrome) ────────────────────────────────

def _start_xvfb() -> None:
    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = ":99"
        time.sleep(1.5)
        log.info("Xvfb iniciado en :99")
    except FileNotFoundError:
        log.warning("Xvfb no encontrado — omitido (¿entorno Windows?)")
    except Exception as e:
        log.warning(f"Xvfb no pudo iniciar: {e}")


# ── Flask ──────────────────────────────────────────────────────────────────────

app = Flask(__name__)
_start_time = time.time()
_videos_count = 0


@app.route("/")
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "shorts-factory",
        "uptime_h": round((time.time() - _start_time) / 3600, 1),
        "videos_uploaded": _videos_count,
    })


# ── Scheduler thread ───────────────────────────────────────────────────────────

def _scheduler_thread() -> None:
    global _videos_count
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from main import _run_scheduler, _safe_run_factory
        import config

        log.info("Scheduler iniciado")

        # Primer video inmediatamente al arrancar
        if _safe_run_factory():
            _videos_count += 1

        # Loop continuo con ventanas de audiencia pico
        while True:
            import datetime as _dt
            from main import _daily_schedule, _cleanup_old_runs, _rotate_logs

            now = _dt.datetime.now()
            pending = [t for t in _daily_schedule() if t > now]

            if not pending:
                tomorrow = _dt.date.today() + _dt.timedelta(days=1)
                pending = [
                    t.replace(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day)
                    for t in _daily_schedule()
                ]

            for next_run in pending:
                wait_secs = (next_run - _dt.datetime.now()).total_seconds()
                if wait_secs <= 0:
                    continue
                log.info(
                    f"Próximo video: {next_run.strftime('%d/%m %H:%M')} "
                    f"(en {wait_secs/3600:.1f}h)"
                )
                while _dt.datetime.now() < next_run:
                    time.sleep(60)

                _cleanup_old_runs(days_to_keep=config.CLEANUP_DAYS_TO_KEEP)
                _rotate_logs(max_files=config.LOGS_MAX_FILES)
                if _safe_run_factory():
                    _videos_count += 1

    except Exception as e:
        log.error(f"Scheduler crasheó: {e}", exc_info=True)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _restore_chrome_session()
    _start_xvfb()

    t = threading.Thread(target=_scheduler_thread, daemon=True, name="scheduler")
    t.start()

    port = int(os.environ.get("PORT", 10000))
    log.info(f"Flask escuchando en :{port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
