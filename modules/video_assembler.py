"""
video_assembler.py — Ensambla el video MP4 final con FFmpeg directo (sin moviepy frame loop)

El cuello de botella anterior era moviepy make_frame() llamado 1800 veces (30fps x 60s),
cada uno con PIL resize LANCZOS + dibujo de subtítulos → 20-30 minutos.

Nuevo flujo con FFmpeg nativo:
  1. Renderizar intro como PNG (Pillow, una vez)
  2. Renderizar overlay de subtítulos como PNG transparente (Pillow, una vez por escena)
  3. FFmpeg zoompan (Ken Burns nativo en C) + overlay → clip por escena  ~3s/escena
  4. FFmpeg concat todos los clips
  5. FFmpeg mezcla audio con offset de intro
  Total: ~60 segundos vs 20-30 minutos anteriores
"""

import logging
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config

logger = logging.getLogger(__name__)


# ─── Utilidades de fuentes ────────────────────────────────────────────────────

def _find_font() -> str:
    """Retorna el path a la mejor fuente TTF disponible en el sistema."""
    for fp in config.FONTS_DIR.glob("*.ttf"):
        return str(fp)
    for fp in [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]:
        if Path(fp).exists():
            return fp
    return ""


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_text_with_stroke(draw, x, y, text, font, fill=(255, 255, 255), stroke=4):
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


# ─── Render intro PNG ─────────────────────────────────────────────────────────

def _render_intro_png(hook: str, title: str, first_image_path: str | None) -> Image.Image:
    """
    Intro directa: 0.5s fade-in desde negro a la primera imagen de escena.
    Sin badge, sin blur — el narrador empieza a hablar desde el segundo 0.
    Solo hook text superpuesto para anclar al espectador.
    """
    W, H  = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    WHITE = (255, 255, 255)

    # Fondo: primera imagen de escena sin blur (inmersión inmediata)
    if first_image_path and Path(first_image_path).exists():
        bg = Image.open(first_image_path).convert("RGB").resize((W, H), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (W, H), (10, 10, 20))

    # Overlay oscuro suave (40% alpha) para que el texto sea legible
    ov   = Image.new("RGBA", (W, H), (0, 0, 0, 102))  # 102 ≈ 40% de 255
    bg   = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path = _find_font()
    font_hook = _load_font(font_path, 86)

    # Hook centrado verticalmente — máx 12 palabras
    hook_words = hook.split()[:12]
    hook_short = " ".join(hook_words) + ("..." if len(hook.split()) > 12 else "")
    hook_lines = _wrap_text(draw, hook_short, font_hook, W - 100)
    line_h     = 100
    block_h    = len(hook_lines) * line_h
    y_start    = H // 2 - block_h // 2

    for i, line in enumerate(hook_lines):
        bbox = draw.textbbox((0, 0), line, font=font_hook)
        x    = (W - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, y_start + i * line_h, line, font_hook, WHITE, 5)

    return bg


def generate_thumbnail(script: dict, images: list[str], output_path: str) -> str:
    """
    Genera thumbnail profesional 1280×720 para YouTube.

    Selecciona la escena más dramática (DESCUBRIMIENTO/CONFRONTACION/CLIMAX),
    superpone capas de gradiente, franja roja con canal, y hook text.

    Returns:
        Path absoluto del thumbnail generado.
    """
    W_T, H_T = 1280, 720
    RED      = (204, 0, 0)
    GOLD     = (255, 210, 40)
    WHITE    = (255, 255, 255)

    scenes = script.get("scenes", [])

    # Buscar escena más dramática por acto
    dramatic_acts = {"DESCUBRIMIENTO", "CONFRONTACION", "CONFRONTACIÓN", "CLIMAX", "GIRO", "REVELACION", "REVELACIÓN"}
    best_idx = None
    for i, scene in enumerate(scenes):
        if i < len(images) and (scene.get("act", "").upper() in dramatic_acts):
            best_idx = i
            break
    if best_idx is None:
        # Fallback: 35% del total (punto de máxima tensión narrativa)
        best_idx = max(0, int(len(images) * 0.35))

    bg_path = images[best_idx] if best_idx < len(images) else (images[0] if images else None)

    # Fondo
    if bg_path and Path(bg_path).exists():
        bg = Image.open(bg_path).convert("RGB").resize((W_T, H_T), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (W_T, H_T), (10, 10, 20))

    # Gradiente oscuro: superior 20% con 60% alpha, inferior 50% con 80% alpha
    ov = Image.new("RGBA", (W_T, H_T), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)
    for y in range(H_T):
        frac = y / H_T
        if frac < 0.20:
            alpha = int(153)         # 60% en parte superior
        elif frac > 0.50:
            alpha = int(204)         # 80% en parte inferior
        else:
            # Transición lineal entre 60% y 80%
            t     = (frac - 0.20) / 0.30
            alpha = int(153 + t * 51)
        d.line([(0, y), (W_T, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path    = _find_font()
    font_channel = _load_font(font_path, 32)
    font_hook    = _load_font(font_path, 72)

    # Franja roja superior (60px)
    stripe_h = 60
    draw.rectangle([0, 0, W_T, stripe_h], fill=(*RED, 230))

    # Barra dorada (4px) bajo la franja roja
    draw.rectangle([0, stripe_h, W_T, stripe_h + 4], fill=GOLD)

    # Nombre del canal en la franja roja, centrado verticalmente
    channel = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMÁTICAS")
    c_bbox  = draw.textbbox((0, 0), channel, font=font_channel)
    c_w     = c_bbox[2] - c_bbox[0]
    c_h     = c_bbox[3] - c_bbox[1]
    draw.text(((W_T - c_w) // 2, (stripe_h - c_h) // 2), channel, font=font_channel, fill=WHITE)

    # Hook text centrado — máx 2 líneas de 22 chars
    hook = script.get("hook", script.get("title", ""))
    hook_short = " ".join(hook.split()[:10])
    hook_lines = _wrap_text(draw, hook_short, font_hook, W_T - 80)[:2]
    line_h     = 84
    block_h    = len(hook_lines) * line_h
    y_start    = (H_T + stripe_h + 4) // 2 - block_h // 2 + 20

    for i, line in enumerate(hook_lines):
        bbox = draw.textbbox((0, 0), line, font=font_hook)
        x    = (W_T - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, y_start + i * line_h, line, font_hook, WHITE, 5)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(str(out_path), "JPEG", quality=95)
    logger.info(f"Thumbnail generado: {out_path.name} ({W_T}×{H_T})")
    return str(out_path)


def _render_outro_png(question: str, last_image_path: str | None) -> Image.Image:
    """
    Renderiza el frame del outro/CTA como imagen PIL.
    Diseño: última imagen + overlay negro 75%, pregunta en amarillo,
    CTA de comentarios y suscripción.
    """
    W, H   = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    YELLOW = (255, 220, 0)
    WHITE  = (255, 255, 255)
    GRAY   = (200, 200, 200)
    GOLD   = (255, 210, 40)

    # Fondo: última imagen de escena
    if last_image_path and Path(last_image_path).exists():
        bg = Image.open(last_image_path).convert("RGB").resize((W, H), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (W, H), (10, 10, 20))

    # Overlay negro 75%
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 191))  # 191 ≈ 75% de 255
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path      = _find_font()
    font_question  = _load_font(font_path, 58)
    font_cta       = _load_font(font_path, 46)
    font_follow    = _load_font(font_path, 40)
    font_channel   = _load_font(font_path, 32)

    center_y = H // 2

    # Pregunta de engagement en amarillo — centrada horizontalmente
    q_lines = _wrap_text(draw, question, font_question, W - 100)[:3]
    line_h  = 72
    block_h = len(q_lines) * line_h
    q_y     = center_y - block_h // 2 - 60

    for i, line in enumerate(q_lines):
        bbox = draw.textbbox((0, 0), line, font=font_question)
        x    = (W - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, q_y + i * line_h, line, font_question, YELLOW, 3)

    # Línea separadora dorada
    sep_y = q_y + block_h + 24
    draw.rectangle([80, sep_y, W - 80, sep_y + 3], fill=GOLD)

    # CTA: comenta
    cta1     = "Comenta tu respuesta abajo"
    c1_bbox  = draw.textbbox((0, 0), cta1, font=font_cta)
    c1_x     = (W - (c1_bbox[2] - c1_bbox[0])) // 2
    cta1_y   = sep_y + 36
    _draw_text_with_stroke(draw, c1_x, cta1_y, cta1, font_cta, WHITE, 2)

    # CTA: sígueme
    cta2    = "Sigueme para mas historias reales"
    c2_bbox = draw.textbbox((0, 0), cta2, font=font_follow)
    c2_x    = (W - (c2_bbox[2] - c2_bbox[0])) // 2
    cta2_y  = cta1_y + 70
    _draw_text_with_stroke(draw, c2_x, cta2_y, cta2, font_follow, GRAY, 2)

    # Nombre del canal abajo del todo
    channel   = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMÁTICAS")
    ch_bbox   = draw.textbbox((0, 0), channel, font=font_channel)
    ch_x      = (W - (ch_bbox[2] - ch_bbox[0])) // 2
    ch_y      = H - 110
    _draw_text_with_stroke(draw, ch_x, ch_y, channel, font_channel, (180, 180, 180), 2)

    return bg


# ─── FFmpeg helpers ───────────────────────────────────────────────────────────

def _ffmpeg(*args: str, desc: str = "") -> None:
    """Ejecuta FFmpeg, lanza RuntimeError si falla."""
    cmd    = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg falló [{desc}]:\n{result.stderr[-600:]}\n"
            "Verifica que FFmpeg está instalado: winget install ffmpeg"
        )


def _get_audio_duration(audio_path: str) -> float:
    """Obtiene duración del audio con ffprobe (sin cargar en memoria)."""
    result = subprocess.run([
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ], capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"No se pudo leer duración del audio: {audio_path}\n"
            "Verifica que FFmpeg/ffprobe está instalado."
        )


# ─── Construcción de clips de escena con FFmpeg ───────────────────────────────

def _build_scene_clip(
    image_path: str,
    duration: float,
    fps: int,
    out_path: Path,
    scene_idx: int,
    act: str = "",
) -> None:
    """
    Genera un clip de escena con:
    - Ken Burns dinámico por acto dramático (zoompan)
    - Fade in desde negro (0.3s) + fade out a negro (0.3s) → transición suave entre escenas
    Encodea con h264_nvenc (GPU) para velocidad máxima.

    Ken Burns por acto:
      DESCUBRIMIENTO/CONFRONTACION/CLIMAX → zoom in rápido (tensión máxima)
      CONSECUENCIA/FINAL → zoom out lento (aftermath, peso emocional)
      INICIO/REFLEXION/resto → pan lateral (establecimiento o contemplación)
    """
    W, H     = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    n_frames = max(int(duration * fps), 1)
    zoom_inc = round(0.06 / n_frames, 8)

    act_upper = (act or "").upper()

    # Actos de tensión máxima → zoom in agresivo
    if any(k in act_upper for k in ("DESCUBRI", "CONFRONTA", "CLIMAX", "GIRO", "REVELAC")):
        zoom_fast = round(0.10 / n_frames, 8)
        effects = [
            f"zoompan=z='zoom+{zoom_fast}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
            f"zoompan=z='zoom+{zoom_fast}':x='iw/2-(iw/zoom/2)+{zoom_fast}*50':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
        ]
    # Actos de consecuencia/final → zoom out lento (peso emocional)
    elif any(k in act_upper for k in ("CONSECUENCIA", "FINAL", "RESULTADO", "REFLEXION")):
        effects = [
            f"zoompan=z='1.08-{zoom_inc}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
            f"zoompan=z='1.06-{zoom_inc}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
        ]
    # Actos de inicio/contexto → pan lento lateral (establecimiento)
    else:
        effects = [
            f"zoompan=z='1.04':x='iw/2-(iw/zoom/2)+{zoom_inc}*80':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
            f"zoompan=z='1.04':x='iw/2-(iw/zoom/2)-{zoom_inc}*80':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
            f"zoompan=z='zoom+{zoom_inc}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}",
        ]

    zoompan = random.choice(effects)

    # Fade: máx 0.3s o 12% de la escena (protege escenas muy cortas)
    fade_d = round(min(0.30, duration * 0.12), 3)
    fade_out_start = round(max(0.0, duration - fade_d), 3)
    vf = (
        f"{zoompan},"
        f"fade=t=in:st=0:d={fade_d},"
        f"fade=t=out:st={fade_out_start}:d={fade_d}"
    )

    _ffmpeg(
        "-loop", "1", "-framerate", str(fps), "-i", str(image_path),
        "-vf", vf,
        "-frames:v", str(n_frames),
        "-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p",
        str(out_path),
        desc=f"escena {scene_idx} (Ken Burns + fade)",
    )


def _build_scene_clip_cpu(
    image_path: str,
    duration: float,
    fps: int,
    out_path: Path,
    scene_idx: int,
    act: str = "",
) -> None:
    """Fallback de _build_scene_clip usando libx264 (CPU) en vez de h264_nvenc."""
    W, H     = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    n_frames = max(int(duration * fps), 1)
    zoom_inc = round(0.06 / n_frames, 8)
    act_upper = (act or "").upper()
    if any(k in act_upper for k in ("DESCUBRI", "CONFRONTA", "CLIMAX", "GIRO", "REVELAC")):
        zoom_fast = round(0.10 / n_frames, 8)
        zoompan = f"zoompan=z='zoom+{zoom_fast}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}"
    elif any(k in act_upper for k in ("CONSECUENCIA", "FINAL", "RESULTADO", "REFLEXION")):
        zoompan = f"zoompan=z='1.08-{zoom_inc}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}"
    else:
        zoompan = f"zoompan=z='1.04':x='iw/2-(iw/zoom/2)+{zoom_inc}*80':y='ih/2-(ih/zoom/2)':s={W}x{H}:d={n_frames}:fps={fps}"
    fade_d   = round(min(0.30, duration * 0.12), 3)
    fade_out = round(max(0.0, duration - fade_d), 3)
    vf       = f"{zoompan},fade=t=in:st=0:d={fade_d},fade=t=out:st={fade_out}:d={fade_d}"
    _ffmpeg(
        "-loop", "1", "-framerate", str(fps), "-i", str(image_path),
        "-vf", vf,
        "-frames:v", str(n_frames),
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(out_path),
        desc=f"escena {scene_idx} CPU fallback",
    )


# ─── Música de fondo ──────────────────────────────────────────────────────────

def _pick_music() -> Path | None:
    """Elige aleatoriamente un archivo de música de assets/music/."""
    music_dir = config.ASSETS_DIR / "music"
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    if not tracks:
        return None
    return random.choice(tracks)


def _mix_with_music(tts_path: Path, out_path: Path, music_vol: float = 0.10) -> Path:
    """
    Mezcla el audio TTS con música de fondo.
    - TTS: volumen completo (voz principal)
    - Música: music_vol (0.10 = 10% ≈ -20dB, audible pero no tapa la voz)
    - La música se repite en loop hasta cubrir la duración del TTS
    - Si no hay música disponible, devuelve tts_path sin cambios

    Returns:
        Path al audio mezclado (o tts_path si no hay música)
    """
    music = _pick_music()
    if music is None:
        logger.info("Sin música disponible — usando solo TTS")
        return tts_path

    logger.info(f"Mezclando con música: {music.name} (vol={music_vol})")
    _ffmpeg(
        "-i", str(tts_path),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        f"[0:a]volume=1.0[tts];[1:a]volume={music_vol}[bg];"
        "[tts][bg]amix=inputs=2:duration=first:dropout_transition=3[out]",
        "-map", "[out]",
        "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
        str(out_path),
        desc="mezcla TTS + música de fondo",
    )
    return out_path


# ─── ASS Offset Helper ────────────────────────────────────────────────────────

def _shift_ass_file(ass_path: Path, offset_s: float, out_path: Path) -> None:
    if not ass_path.exists():
        return
    content = ass_path.read_text(encoding="utf-8")

    def add_offset(match):
        h = float(match.group(1))
        m = float(match.group(2))
        s = float(match.group(3))
        total = h * 3600 + m * 60 + s + offset_s
        new_h = int(total // 3600)
        new_m = int((total % 3600) // 60)
        new_s = total % 60
        return f"{new_h:d}:{new_m:02d}:{new_s:05.2f}"

    # \d+ para horas (ASS permite h:mm:ss.cc donde h puede tener varios dígitos)
    shifted = re.sub(r"(\d+):(\d{2}):(\d{2}\.\d{2})", add_offset, content)
    out_path.write_text(shifted, encoding="utf-8")

# ─── Función principal ────────────────────────────────────────────────────────

def assemble_video(
    script: dict,
    audio_path: str,
    images: list[str],
    output_path: str | None = None,
) -> str:
    """
    Ensambla el video MP4 final usando FFmpeg directo.

    Flujo optimizado:
    1. ffprobe  → duración real del audio
    2. Pillow   → render intro PNG (una vez)
    3. Pillow   → render subtitle overlay PNG por escena (una vez cada uno)
    4. FFmpeg   → Ken Burns + subtitle overlay por escena (paralelo, ~3s c/u)
    5. FFmpeg   → concat todos los clips
    6. FFmpeg   → mezclar audio con offset de intro

    Tiempo esperado: ~60s vs ~25 min con moviepy frame loop.

    Args:
        script:      Dict con title, hook, scenes
        audio_path:  Path al MP3 generado por TTS
        images:      Lista de paths PNG de escenas
        output_path: Path de salida. Si None, auto-genera en output/

    Returns:
        Path absoluto del MP4 generado
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio no encontrado: {audio_path}")

    valid_images = [str(Path(p)) for p in images if Path(p).exists()]
    if not valid_images:
        raise FileNotFoundError("No hay imágenes válidas para ensamblar")

    if output_path is None:
        ts          = time.strftime("%Y%m%d_%H%M%S")
        output_path = config.OUTPUT_DIR / f"video_{ts}.mp4"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_total = time.time()

    # ── 1. Duración del audio ──────────────────────────────────────────────────
    total_duration = _get_audio_duration(str(audio_path))
    scene_duration = total_duration / len(valid_images)
    logger.info(
        f"Audio: {total_duration:.1f}s | "
        f"{len(valid_images)} escenas x {scene_duration:.1f}s"
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="csf_video_"))
    try:
        clip_paths: list[Path] = []
        scenes = script.get("scenes", [])

        # ── 2. Intro ───────────────────────────────────────────────────────────
        logger.info("Renderizando intro...")
        t0 = time.time()
        intro_img = _render_intro_png(
            hook=script.get("hook", script.get("title", "")),
            title=script.get("title", ""),
            first_image_path=valid_images[0] if valid_images else None,
        )
        intro_png  = tmp_dir / "intro.png"
        intro_clip = tmp_dir / "clip_000_intro.mp4"
        intro_img.save(str(intro_png), "PNG")

        intro_frames  = int(config.INTRO_DURATION * config.FPS)
        intro_fade_in = 0.5
        intro_fade_out_start = round(config.INTRO_DURATION - 0.4, 3)
        _ffmpeg(
            "-loop", "1", "-framerate", str(config.FPS), "-i", str(intro_png),
            "-vf", f"fade=t=in:st=0:d={intro_fade_in},fade=t=out:st={intro_fade_out_start}:d=0.4",
            "-frames:v", str(intro_frames),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(intro_clip),
            desc="intro",
        )
        clip_paths.append(intro_clip)
        logger.info(f"Intro lista en {time.time()-t0:.1f}s")

        # ── 3. Pre-renderizar subtítulos ──────────────────────────────────────
        # REMOVIDO: Ahora se inyecta directamente vía FFmpeg ASS en el paso 6.

        # ── 4. Generar clips de escena en paralelo ─────────────────────────────
        logger.info(f"Generando {len(valid_images)} clips con FFmpeg zoompan dinámico (NVENC)...")
        t0 = time.time()

        scene_clip_paths = [tmp_dir / f"clip_{i+1:03d}_scene.mp4"
                            for i in range(len(valid_images))]

        def build_scene(idx):
            t_s  = time.time()
            act  = scenes[idx].get("act", "") if idx < len(scenes) else ""
            _build_scene_clip(
                image_path=valid_images[idx],
                duration=scene_duration,
                fps=config.FPS,
                out_path=scene_clip_paths[idx],
                scene_idx=idx,
                act=act,
            )
            logger.info(
                f"  Escena {idx+1}/{len(valid_images)} [{act or '-'}] lista "
                f"({time.time()-t_s:.1f}s)"
            )

        # Paralelo — 4 workers máximo (más saturan CPU/IO sin beneficio)
        errors = []
        with ThreadPoolExecutor(max_workers=min(4, len(valid_images))) as executor:
            futures = {executor.submit(build_scene, i): i
                       for i in range(len(valid_images))}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"  ERROR escena {idx+1}: {e}")
                    errors.append(idx)

        if errors:
            # Intentar con PIL fallback las escenas que fallaron
            logger.warning(f"Regenerando {len(errors)} escena(s) fallidas con libx264...")
            for idx in errors:
                try:
                    act = scenes[idx].get("act", "") if idx < len(scenes) else ""
                    _build_scene_clip_cpu(
                        image_path=valid_images[idx],
                        duration=scene_duration,
                        fps=config.FPS,
                        out_path=scene_clip_paths[idx],
                        scene_idx=idx,
                        act=act,
                    )
                    logger.info(f"  Escena {idx+1} recuperada con libx264")
                except Exception as e2:
                    raise RuntimeError(
                        f"Escena {idx+1} falló incluso con fallback: {e2}"
                    ) from e2

        clip_paths.extend(scene_clip_paths)
        logger.info(f"Clips de escenas listos en {time.time()-t0:.1f}s")

        # ── 4b. Outro/CTA ──────────────────────────────────────────────────────
        outro_dur = getattr(config, "OUTRO_DURATION", 4.0)
        logger.info(f"Renderizando outro ({outro_dur}s)...")
        t0 = time.time()

        question    = script.get("pregunta", "Y tu, que harias en mi lugar?")
        last_img    = valid_images[-1] if valid_images else None
        outro_img   = _render_outro_png(question, last_img)
        outro_png   = tmp_dir / "outro.png"
        outro_clip  = tmp_dir / "clip_outro.mp4"
        outro_img.save(str(outro_png), "PNG")

        outro_frames = int(outro_dur * config.FPS)
        fade_in_d    = 0.4
        fade_out_d   = 0.3
        fade_out_st  = round(outro_dur - fade_out_d, 3)
        _ffmpeg(
            "-loop", "1", "-framerate", str(config.FPS), "-i", str(outro_png),
            "-vf", (
                f"fade=t=in:st=0:d={fade_in_d},"
                f"fade=t=out:st={fade_out_st}:d={fade_out_d}"
            ),
            "-frames:v", str(outro_frames),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(outro_clip),
            desc="outro CTA",
        )
        clip_paths.append(outro_clip)
        logger.info(f"Outro listo en {time.time()-t0:.1f}s")

        # ── 5. Concat ──────────────────────────────────────────────────────────
        logger.info("Concatenando clips...")
        t0         = time.time()
        concat_txt = tmp_dir / "concat.txt"
        concat_txt.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in clip_paths),
            encoding="utf-8"
        )
        concat_mp4 = tmp_dir / "concat.mp4"
        _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c", "copy",
            str(concat_mp4),
            desc="concat",
        )
        logger.info(f"Concat en {time.time()-t0:.1f}s")

        # ── 6. Mezclar audio (TTS + música) y añadir ASS ───────────────────────
        logger.info("Mezclando audio e inyectando subtítulos dinámicos ASS...")
        t0 = time.time()

        # Mezclar TTS con música de fondo si hay tracks disponibles
        mixed_audio = tmp_dir / "audio_mixed.aac"
        music_vol   = getattr(config, "MUSIC_VOLUME", 0.10)
        final_audio_raw = _mix_with_music(audio_path, mixed_audio, music_vol=music_vol)

        # Añadir silencio al final para cubrir el outro (sin cortar el video)
        padded_audio = tmp_dir / "audio_padded.aac"
        _ffmpeg(
            "-i", str(final_audio_raw),
            "-af", f"apad=pad_dur={outro_dur}",
            "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
            str(padded_audio),
            desc="audio padding outro",
        )
        final_audio = padded_audio

        ass_path  = audio_path.with_suffix(".ass")
        filters   = []          # lista de filtros vf que se unirán con coma
        temp_subs = tmp_dir / "subtitles.ass"

        if ass_path.exists():
            _shift_ass_file(ass_path, config.INTRO_DURATION, temp_subs)

        if temp_subs.exists() and temp_subs.stat().st_size > 0:
            safe_path = str(temp_subs.resolve()).replace("\\", "/")
            if len(safe_path) >= 2 and safe_path[1] == ":":
                safe_path = safe_path[0] + "\\:" + safe_path[2:]
            filters.append(f"ass='{safe_path}'")
            logger.info(f"Subtítulos: {temp_subs.name} → {safe_path[:55]}")
        else:
            logger.warning("Sin archivo ASS — video sin subtítulos")

        # Watermark: nombre del canal esquina superior derecha
        channel = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMÁTICAS")
        font_fp  = _find_font()
        if font_fp:
            safe_font = font_fp.replace("\\", "/")
            if len(safe_font) >= 2 and safe_font[1] == ":":
                safe_font = safe_font[0] + "\\:" + safe_font[2:]
            wm = (f"drawtext=text='{channel}':"
                  f"fontfile='{safe_font}':"
                  f"fontsize=36:fontcolor=white@0.55:"
                  f"x=w-tw-28:y=28:"
                  f"shadowcolor=black@0.75:shadowx=2:shadowy=2")
        else:
            wm = (f"drawtext=text='{channel}':"
                  f"fontsize=36:fontcolor=white@0.55:"
                  f"x=w-tw-28:y=28:"
                  f"shadowcolor=black@0.75:shadowx=2:shadowy=2")
        filters.append(wm)

        vf_args = ["-vf", ",".join(filters)] if filters else []

        _ffmpeg(
            "-i", str(concat_mp4),
            "-itsoffset", str(config.INTRO_DURATION), "-i", str(final_audio),
            *vf_args,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "h264_nvenc", "-preset", "p6", "-cq", "20",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-movflags", "+faststart",
            "-c:a", "aac", "-ar", "44100",
            "-shortest",
            str(output_path),
            desc="audio mix + subtítulos ASS",
        )
        logger.info(f"Audio mezclado en {time.time()-t0:.1f}s")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)  # incluye subtitles.ass

    elapsed      = time.time() - start_total
    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    final_dur    = total_duration + config.INTRO_DURATION + outro_dur

    logger.info(
        f"Video listo: {output_path.name} "
        f"({final_dur:.1f}s, {file_size_mb:.1f}MB) — "
        f"ensamblado en {elapsed:.1f}s"
    )
    return str(output_path)
