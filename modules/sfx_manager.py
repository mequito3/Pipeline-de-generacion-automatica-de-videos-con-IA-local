"""
sfx_manager.py — Efectos de sonido CC0 para el pipeline de video

Genera SFX con síntesis pura ffmpeg (sin dependencias externas ni descargas).
Los archivos se cachean en assets/sfx/ para no regenerarlos en cada run.

Efectos disponibles:
  whoosh  — transición entre escenas (barrido de frecuencia descendente, 0.35s)
  impact  — momento dramático CLIMAX/CONFRONTACION (pulso grave + reverb, 0.5s)
  ding    — pregunta final outro (tono suave 880Hz, 0.3s)
  riser   — arranque del video (frecuencia ascendente, 0.8s)

mix_sfx_layer() mezcla los SFX sobre el audio base usando los tiempos de escena.
"""

import logging
import subprocess
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_SFX_DIR = config.ASSETS_DIR / "sfx"


def _sfx_path(name: str) -> Path:
    _SFX_DIR.mkdir(parents=True, exist_ok=True)
    return _SFX_DIR / f"{name}.wav"


def _run_ffmpeg(*args: str, desc: str = "") -> bool:
    cmd    = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"SFX ffmpeg falló [{desc}]: {result.stderr[-200:]}")
        return False
    return True


def get_whoosh() -> Path | None:
    """Barrido de frecuencia descendente 1200→200Hz, 0.35s — para transiciones."""
    p = _sfx_path("whoosh")
    if p.exists():
        return p
    ok = _run_ffmpeg(
        "-f", "lavfi",
        "-i", "aevalsrc=sin(2*PI*t*(1200-1000*t)):s=44100:d=0.35",
        "-af", "volume=0.7,afade=t=out:st=0.20:d=0.15",
        "-ar", "44100", "-ac", "2",
        str(p),
        desc="whoosh",
    )
    return p if ok else None


def get_impact() -> Path | None:
    """Pulso grave 60Hz + armonicos, 0.5s — para CLIMAX/CONFRONTACION."""
    p = _sfx_path("impact")
    if p.exists():
        return p
    # Mezcla tono grave + ruido filtrado (simula golpe dramático)
    ok = _run_ffmpeg(
        "-f", "lavfi",
        "-i", (
            "aevalsrc=0.6*sin(2*PI*60*t)*exp(-8*t)"
            "+0.3*sin(2*PI*120*t)*exp(-10*t)"
            "+0.15*sin(2*PI*240*t)*exp(-14*t)"
            ":s=44100:d=0.5"
        ),
        "-af", "volume=1.2,highpass=f=30,afade=t=out:st=0.30:d=0.20",
        "-ar", "44100", "-ac", "2",
        str(p),
        desc="impact",
    )
    return p if ok else None


def get_ding() -> Path | None:
    """Tono suave 880Hz con decay natural, 0.3s — para outro/pregunta final."""
    p = _sfx_path("ding")
    if p.exists():
        return p
    ok = _run_ffmpeg(
        "-f", "lavfi",
        "-i", "aevalsrc=0.5*sin(2*PI*880*t)*exp(-6*t)+0.2*sin(2*PI*1760*t)*exp(-10*t):s=44100:d=0.3",
        "-af", "volume=0.8",
        "-ar", "44100", "-ac", "2",
        str(p),
        desc="ding",
    )
    return p if ok else None


def get_riser() -> Path | None:
    """Frecuencia ascendente 80→600Hz, 0.8s — para el inicio del video."""
    p = _sfx_path("riser")
    if p.exists():
        return p
    ok = _run_ffmpeg(
        "-f", "lavfi",
        "-i", "aevalsrc=0.4*sin(2*PI*(80+650*t/0.8)*t):s=44100:d=0.8",
        "-af", "volume=0.6,afade=t=in:st=0:d=0.1,afade=t=out:st=0.65:d=0.15",
        "-ar", "44100", "-ac", "2",
        str(p),
        desc="riser",
    )
    return p if ok else None


def mix_sfx_layer(
    base_audio: Path,
    scene_times: list[float],
    act_sequence: list[str],
    out_path: Path,
    sfx_volume: float | None = None,
) -> Path:
    """
    Añade capa de SFX al audio base.

    - scene_times: lista de timestamps (segundos) donde empieza cada escena
    - act_sequence: lista de actos narrativos para cada escena
    - sfx_volume: volumen relativo de los SFX (default: config.SFX_VOLUME)

    Si no hay SFX disponibles o la mezcla falla, devuelve base_audio sin cambios.
    """
    if sfx_volume is None:
        sfx_volume = getattr(config, "SFX_VOLUME", 0.30)

    if not getattr(config, "SFX_ENABLED", True):
        return base_audio

    # Pre-generar (o recuperar del caché) todos los SFX que se usarán
    whoosh = get_whoosh()
    impact = get_impact()
    ding   = get_ding()
    riser  = get_riser()

    if not any([whoosh, impact, ding, riser]):
        logger.warning("SFX: ningún efecto disponible — omitiendo capa SFX")
        return base_audio

    # Construir argumentos ffmpeg: base_audio + SFX con adelay por cada evento
    inputs:    list[str] = ["-i", str(base_audio)]
    delays:    list[str] = ["[0:a]volume=1.0[base]"]
    mix_parts: list[str] = ["[base]"]
    idx = 1

    _DRAMATIC = {"CLIMAX", "CONFRONTACION"}

    for i, (t_start, act) in enumerate(zip(scene_times, act_sequence)):
        act_u = act.upper()
        sfx_path: Path | None = None

        if i == 0 and riser:
            sfx_path = riser
        elif act_u in _DRAMATIC and impact:
            sfx_path = impact
        elif whoosh and i > 0:
            sfx_path = whoosh

        if sfx_path is None:
            continue

        delay_ms = int(t_start * 1000)
        sfx_lbl  = f"sfx{idx}"
        inputs.extend(["-i", str(sfx_path)])
        delays.append(
            f"[{idx}:a]volume={sfx_volume},"
            f"adelay={delay_ms}:all=1[{sfx_lbl}]"
        )
        mix_parts.append(f"[{sfx_lbl}]")
        idx += 1

    # Añadir ding al final (outro) si está disponible
    if ding and scene_times:
        outro_t    = scene_times[-1] + 1.0
        delay_ms   = int(outro_t * 1000)
        sfx_lbl    = f"sfx{idx}"
        inputs.extend(["-i", str(ding)])
        delays.append(
            f"[{idx}:a]volume={sfx_volume * 1.2},"
            f"adelay={delay_ms}:all=1[{sfx_lbl}]"
        )
        mix_parts.append(f"[{sfx_lbl}]")
        idx += 1

    if idx == 1:
        return base_audio

    n_mix = len(mix_parts)
    mix_filter = (
        "".join(delays) + ";" +
        "".join(mix_parts) +
        f"amix=inputs={n_mix}:duration=first:dropout_transition=1[sfx_out]"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", mix_filter,
        "-map", "[sfx_out]",
        "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"SFX mix falló: {result.stderr[-300:]} — usando audio sin SFX")
        return base_audio

    logger.info(f"SFX mezclados: {idx-1} efectos en {n_mix-1} escenas (vol={sfx_volume})")
    return out_path
