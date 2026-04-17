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
        "C:/Windows/Fonts/impact.ttf",    # Impact — estándar viral de Shorts
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
    Intro con branding completo:
      - Fondo: primera escena sin blur (inmersión inmediata)
      - Gradiente cinemático (oscuro arriba/abajo, semi en centro)
      - Vignette en bordes para profundidad
      - Franja roja + barra dorada con nombre del canal (top)
      - Etiqueta dorada "HISTORIA REAL" sobre el hook
      - Hook grande centrado (Impact blanco con stroke)
      - Línea roja de acento bajo el hook
    """
    W, H   = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    WHITE  = (255, 255, 255)
    RED    = (204, 0, 0)
    GOLD   = (255, 210, 40)

    # ── 1. Fondo ──────────────────────────────────────────────────────────────
    if first_image_path:
        bg = _open_as_image(first_image_path, (W, H))
    else:
        bg = Image.new("RGB", (W, H), (10, 10, 20))

    # ── 2. Gradiente cinemático (oscuro arriba, abierto al centro, oscuro abajo)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grad)
    for y in range(H):
        frac = y / H
        if frac < 0.25:
            # Superior: oscuro progresivo de 80% → 40%
            t     = frac / 0.25
            alpha = int(204 - t * 102)
        elif frac > 0.72:
            # Inferior: oscuro progresivo de 40% → 85%
            t     = (frac - 0.72) / 0.28
            alpha = int(102 + t * 115)
        else:
            # Centro: semi-transparente fijo 40%
            alpha = 102
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    # ── 3. Vignette en bordes (esquinas oscuras, efecto cinemático) ───────────
    vig    = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd     = ImageDraw.Draw(vig)
    radius = int(W * 0.55)
    for px in range(0, W, 3):
        for py in range(0, H, 3):
            dx = px - W / 2
            dy = py - H / 2
            dist = (dx * dx / (W / 2) ** 2 + dy * dy / (H / 2) ** 2) ** 0.5
            if dist > 0.75:
                a = int(min(180, (dist - 0.75) / 0.55 * 180))
                vd.point((px, py), fill=(0, 0, 0, a))
    bg = Image.alpha_composite(bg, vig).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path    = _find_font()
    font_channel = _load_font(font_path, 44)
    font_label   = _load_font(font_path, 38)
    font_hook    = _load_font(font_path, 90)

    # ── 4. Franja roja de branding (top) ──────────────────────────────────────
    stripe_h = 80
    draw.rectangle([0, 0, W, stripe_h], fill=(*RED, 230))
    # Barra dorada (5px) bajo la franja
    draw.rectangle([0, stripe_h, W, stripe_h + 5], fill=GOLD)

    channel = getattr(config, "CHANNEL_NAME", "GATA CURIOSA")
    c_bbox  = draw.textbbox((0, 0), channel, font=font_channel)
    c_w     = c_bbox[2] - c_bbox[0]
    c_h     = c_bbox[3] - c_bbox[1]
    draw.text(
        ((W - c_w) // 2, (stripe_h - c_h) // 2),
        channel, font=font_channel, fill=WHITE
    )

    # ── 5. Zona central: etiqueta + hook + línea de acento ────────────────────
    hook_words = hook.split()[:12]
    hook_short = " ".join(hook_words) + ("..." if len(hook.split()) > 12 else "")
    hook_lines = _wrap_text(draw, hook_short, font_hook, W - 120)
    line_h     = 108
    block_h    = len(hook_lines) * line_h

    # Etiqueta dorada sobre el hook
    label      = "HISTORIA REAL"
    lbl_bbox   = draw.textbbox((0, 0), label, font=font_label)
    lbl_w      = lbl_bbox[2] - lbl_bbox[0]
    lbl_h      = lbl_bbox[3] - lbl_bbox[1]
    label_gap  = 18   # espacio entre etiqueta y hook
    total_h    = lbl_h + label_gap + block_h + 16 + 6  # +acento
    y_center   = H // 2 + 60  # ligeramente bajo el centro (visualmente más dramático)
    y_label    = y_center - total_h // 2

    # Fondo semitransparente detrás de la etiqueta
    pad = 12
    draw.rectangle(
        [(W - lbl_w) // 2 - pad, y_label - pad // 2,
         (W + lbl_w) // 2 + pad, y_label + lbl_h + pad // 2],
        fill=(*GOLD, 40)
    )
    draw.text(((W - lbl_w) // 2, y_label), label, font=font_label, fill=GOLD)

    # Hook lines
    y_hook = y_label + lbl_h + label_gap
    for i, line in enumerate(hook_lines):
        bbox = draw.textbbox((0, 0), line, font=font_hook)
        x    = (W - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, y_hook + i * line_h, line, font_hook, WHITE, 5)

    # Línea roja de acento bajo el hook
    accent_y = y_hook + block_h + 16
    accent_w = min(W - 160, 600)
    draw.rectangle(
        [(W - accent_w) // 2, accent_y,
         (W + accent_w) // 2, accent_y + 6],
        fill=RED
    )

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

    # Fondo — soporta tanto imagen como clip de video (Pexels)
    if bg_path:
        bg = _open_as_image(bg_path, (W_T, H_T))
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

    # Fondo: último clip/imagen de escena
    if last_image_path:
        bg = _open_as_image(last_image_path, (W, H))
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

    # CTA: comenta (rotativo)
    cta1     = random.choice(getattr(config, "CTA_COMMENTS", ["Comenta tu respuesta abajo"]))
    c1_bbox  = draw.textbbox((0, 0), cta1, font=font_cta)
    c1_x     = (W - (c1_bbox[2] - c1_bbox[0])) // 2
    cta1_y   = sep_y + 36
    _draw_text_with_stroke(draw, c1_x, cta1_y, cta1, font_cta, WHITE, 2)

    # CTA: sígueme (rotativo)
    cta2    = random.choice(getattr(config, "CTA_FOLLOW", ["Sígueme para más historias reales"]))
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


# ─── Detección de tipo de entrada ────────────────────────────────────────────

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

def _is_video(path: str) -> bool:
    return Path(path).suffix.lower() in _VIDEO_EXTS


def _open_as_image(path: str, size: tuple[int, int]) -> Image.Image:
    """
    Abre un path como imagen PIL, sea PNG/JPG o un clip MP4 (extrae frame al 35%).
    Siempre devuelve una imagen RGB del tamaño pedido.
    """
    p = Path(path)
    if not p.exists():
        return Image.new("RGB", size, (10, 10, 20))
    if _is_video(path):
        try:
            dur = _get_audio_duration(path)
            ts  = round(dur * 0.35, 2)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", str(ts), "-i", path,
                 "-frames:v", "1", "-q:v", "2", tmp_path],
                check=True,
            )
            img = Image.open(tmp_path).convert("RGB").resize(size, Image.LANCZOS)
            Path(tmp_path).unlink(missing_ok=True)
            return img
        except Exception as e:
            logger.warning(f"No se pudo extraer frame de {p.name}: {e}")
            return Image.new("RGB", size, (10, 10, 20))
    return Image.open(path).convert("RGB").resize(size, Image.LANCZOS)


# ─── Clip de escena desde stock video (Pexels) ────────────────────────────────

def _build_scene_clip_from_video(
    video_path: str,
    duration: float,
    fps: int,
    out_path: Path,
    scene_idx: int,
) -> None:
    """
    Prepara un clip de stock video para una escena:
    - Escala y recorta a 1080x1920 (portrait center-crop)
    - Toma un segmento aleatorio dentro del clip (variedad entre runs)
    - Si el clip es más corto que duration, lo loopea
    - Añade fade in/out suave
    Usa libx264 (CPU) porque los clips ya tienen movimiento real — no necesitan GPU.
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    fade_d = round(min(0.30, duration * 0.12), 3)
    fade_out_start = round(max(0.0, duration - fade_d), 3)

    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"fade=t=in:st=0:d={fade_d},"
        f"fade=t=out:st={fade_out_start}:d={fade_d}"
    )

    clip_dur = _get_audio_duration(video_path)
    # Offset aleatorio para variedad cuando el mismo clip se reutiliza
    max_offset = max(0.0, clip_dur - duration - 0.5)
    start_offset = round(random.uniform(0.0, max_offset), 2) if max_offset > 0 else 0.0

    if clip_dur < duration:
        # Clip corto: loopeamos antes de recortar
        _ffmpeg(
            "-stream_loop", "-1",
            "-i", video_path,
            "-t", str(duration),
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an",
            str(out_path),
            desc=f"escena {scene_idx} (stock video, loop)",
        )
    else:
        _ffmpeg(
            "-ss", str(start_offset),
            "-i", video_path,
            "-t", str(duration),
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an",
            str(out_path),
            desc=f"escena {scene_idx} (stock video)",
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
        # El intro muestra el TÍTULO (clickbait de YouTube), no el hook.
        # El hook se narra y aparece como subtítulo en la primera escena —
        # mostrarlo también en el intro causaba que se leyera dos veces.
        intro_img = _render_intro_png(
            hook=script.get("title", script.get("hook", "")),
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
        using_video = any(_is_video(p) for p in valid_images)
        clip_mode = "stock video (Pexels)" if using_video else "zoompan + NVENC (SD)"
        logger.info(f"Generando {len(valid_images)} clips — modo: {clip_mode}...")
        t0 = time.time()

        scene_clip_paths = [tmp_dir / f"clip_{i+1:03d}_scene.mp4"
                            for i in range(len(valid_images))]

        def build_scene(idx):
            t_s  = time.time()
            act  = scenes[idx].get("act", "") if idx < len(scenes) else ""
            src  = valid_images[idx]
            if _is_video(src):
                _build_scene_clip_from_video(
                    video_path=src,
                    duration=scene_duration,
                    fps=config.FPS,
                    out_path=scene_clip_paths[idx],
                    scene_idx=idx,
                )
            else:
                _build_scene_clip(
                    image_path=src,
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
        # Pre-delay audio: añadir silencio al inicio equivalente a la intro.
        # Más confiable que -itsoffset, que falla con NVENC + filter chains.
        intro_ms      = int(config.INTRO_DURATION * 1000)
        audio_delayed = tmp_dir / "audio_delayed.aac"
        _ffmpeg(
            "-i", str(padded_audio),
            "-af", f"adelay={intro_ms}:all=1",
            "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
            str(audio_delayed),
            desc="audio delay intro",
        )
        final_audio = audio_delayed

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
            "-i", str(final_audio),   # audio ya lleva silencio al inicio (adelay)
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
