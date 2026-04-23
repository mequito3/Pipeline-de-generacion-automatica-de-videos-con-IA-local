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

# ─── Stickers virales (overlay sobre clips de escena) ────────────────────────
# Texto + color de fondo + color de texto — estilo TikTok/Reels
_STICKER_DATA = [
    # ── Shock / reacción ──────────────────────────────────────────────────────
    ("¡NO PUEDE SER!",      (220,  20,  60), (255, 255, 255)),
    ("¡NO LO CREO!",        (220,  20,  60), (255, 255, 255)),
    ("¡QUÉ FUERTE!",        (200,  10,  50), (255, 255, 255)),
    ("¡INCREÍBLE!",         (190,   0,  40), (255, 255, 255)),
    ("¡DIOS MÍO!",          (220,  20,  60), (255, 255, 0  )),
    ("¡QUÉ LOCURA!",        (180,   0,  80), (255, 255, 255)),
    ("¡ME DEJÓ SIN PALABRAS!", (210, 10, 50), (255, 255, 255)),
    ("¡IMPRESIONANTE!",     (200,  20,  60), (255, 255, 255)),
    ("¡ESTO ES REAL!",      (220,  20,  60), (255, 230,   0)),
    ("¡QUÉ BARBARIDAD!",    (200,   0,  50), (255, 255, 255)),
    # ── Traición / drama ─────────────────────────────────────────────────────
    ("¡QUÉ TRAICIÓN!",      (220,  20,  60), (255, 255, 255)),
    ("¡TRAICIONADO/A!",     (190,   0,  40), (255, 255, 255)),
    ("¡DRAMA REAL!",        (170,   0, 160), (255, 255, 255)),
    ("¡QUÉ DRAMA!",         (160,   0, 150), (255, 255, 255)),
    ("¡QUÉ BAJEZA!",        (200,  20,  60), (255, 255, 255)),
    ("¡CERO LEALTAD!",      (220,  20,  60), (255, 255, 255)),
    ("¡QUÉ DECEPCIÓN!",     (180,  10,  50), (255, 255, 255)),
    ("¡TRAICIÓN PURA!",     (210,  10,  40), (255, 255, 255)),
    ("¡LO PEOR!",           (220,  20,  60), (255, 255, 255)),
    ("¡DOBLE CARA!",        (150,   0, 130), (255, 255, 255)),
    # ── Verificación / autenticidad ───────────────────────────────────────────
    ("HISTORIA REAL",       (0,   140, 200), (255, 255, 255)),
    ("100% REAL",           (0,   100, 220), (255, 255, 255)),
    ("SIN FILTROS",         (10,   10,  10), (255,  50,  70)),
    ("SIN CENSURA",         (20,   20,  20), (255,  40,  60)),
    ("CASO REAL",           (0,   130, 190), (255, 255, 255)),
    ("HISTORIA VERDADERA",  (0,   120, 180), (255, 255, 255)),
    ("CONFESIÓN REAL",      (0,   140, 200), (255, 230,   0)),
    ("RELATO REAL",         (0,   110, 170), (255, 255, 255)),
    ("TESTIMONIO REAL",     (0,   130, 190), (255, 255, 255)),
    ("ESTO PASÓ DE VERDAD", (0,   150, 210), (255, 255, 255)),
    # ── Preguntas / debate ────────────────────────────────────────────────────
    ("¿EN SERIO?",          (255, 155,   0), (0,   0,   0)),
    ("¿LO PERDONARÍAS?",    (255, 140,   0), (0,   0,   0)),
    ("¿QUÉ HARÍAS TÚ?",     (255, 160,  10), (0,   0,   0)),
    ("¿CULPABLE O NO?",     (255, 145,   0), (0,   0,   0)),
    ("¿ESTO ES NORMAL?",    (255, 150,   0), (0,   0,   0)),
    ("¿TÚ LO SABÍAS?",      (255, 140,  10), (0,   0,   0)),
    ("¿QUIÉN TIENE RAZÓN?", (255, 130,   0), (0,   0,   0)),
    ("¿SE LO MERECE?",      (255, 155,   0), (0,   0,   0)),
    ("¿LO HUBIERAS HECHO?", (255, 145,   5), (0,   0,   0)),
    ("¿PERDÓN O JAMÁS?",    (255, 140,   0), (0,   0,   0)),
    # ── Giro / revelación ────────────────────────────────────────────────────
    ("¡GIRO IMPACTANTE!",   (0,   170, 100), (255, 255, 255)),
    ("¡SHOCKING!",          (0,   160,  90), (255, 255, 255)),
    ("¡REVELACIÓN!",        (0,   180, 110), (255, 255, 255)),
    ("¡SPOILER ALERT!",     (50,  170,  80), (255, 255, 255)),
    ("¡ESTO CAMBIA TODO!",  (0,   155,  90), (255, 255, 255)),
    ("¡GIRO TOTAL!",        (0,   160,  80), (255, 255, 255)),
    ("¡LO QUE DESCUBRIÓ!",  (0,   175, 105), (255, 255, 255)),
    ("¡IMPACTANTE!",        (255, 155,   0), (0,   0,   0)),
    ("¡ATENCIÓN!",          (255,  60,  20), (255, 255, 255)),
    ("¡ESTO ES MUY SERIO!", (0,   160,  80), (255, 255, 255)),
    # ── Enganche / TikTok viral ───────────────────────────────────────────────
    ("ESPERA EL FINAL",     (80,   0, 200), (255, 255, 255)),
    ("VE HASTA EL FINAL",   (90,  10, 210), (255, 255, 255)),
    ("NO TE LO PIERDAS",    (70,   0, 190), (255, 255, 255)),
    ("SIGUE MIRANDO",       (85,   5, 200), (255, 255, 255)),
    ("LO MEJOR AL FINAL",   (75,   0, 195), (255, 255, 255)),
    ("PARTE 2 YA",          (80,  10, 210), (255, 230,   0)),
    ("¡AGUANTA AHÍ!",       (70,   0, 200), (255, 255, 255)),
    ("EL FINAL TE ROMPE",   (90,   5, 210), (255, 255, 255)),
    ("NO HAGAS SWIPE",      (80,   0, 200), (255, 255,   0)),
    ("SUBE EL VOLUMEN",     (75,  10, 195), (255, 255, 255)),
    # ── Emocional / empatía ───────────────────────────────────────────────────
    ("¡QUÉ DOLOR!",         (100,  20, 180), (255, 255, 255)),
    ("¡CORAZÓN ROTO!",      (220,  20,  60), (255, 255, 255)),
    ("¡QUÉ INJUSTO!",       (110,  10, 170), (255, 255, 255)),
    ("¡CUÁNTO SUFRIÓ!",     (100,  15, 175), (255, 255, 255)),
    ("¡SE LO MERECÍA!",     (190,   0,  60), (255, 255, 255)),
    ("¡FUERZA!",            (100,  20, 180), (255, 255, 255)),
    ("¡QUÉ VALIENTE!",      (0,   140, 200), (255, 255, 255)),
    ("¡ME LLEGÓ AL ALMA!",  (110,  10, 175), (255, 255, 255)),
    ("¡LLORANDO AQUÍ!",     (100,  15, 180), (255, 255, 255)),
    ("¡ESTO DUELE!",        (200,  20,  60), (255, 255, 255)),
    # ── Picante / polémica ───────────────────────────────────────────────────
    ("¡POLÉMICO!",          (255,  60,  20), (255, 255, 255)),
    ("¡CONTENIDO FUERTE!",  (240,  50,  10), (255, 255, 255)),
    ("¡SIN PALABRAS!",      (255,  50,  20), (255, 255, 255)),
    ("¡ESCÁNDALO!",         (230,  40,  10), (255, 255, 255)),
    ("¡LO CONTÓ TODO!",     (255,  55,  15), (255, 255, 255)),
    ("¡PICANTE!",           (255,  70,  10), (255, 255, 0  )),
    ("¡NADIE LO SABE!",     (240,  45,  10), (255, 255, 255)),
    ("¡SECRETO REVELADO!",  (220,  35,  10), (255, 255, 255)),
    ("¡SE ARMÓ!",           (255,  60,  20), (255, 255, 255)),
    ("¡TODO SALIÓ!",        (240,  50,  10), (255, 255, 255)),
    # ── Noir / oscuro / tenso ─────────────────────────────────────────────────
    ("HISTORIA OSCURA",     (10,   10,  10), (200, 200, 200)),
    ("CONTENIDO REAL",      (20,   20,  20), (180, 180, 180)),
    ("ALERTA EMOCIONAL",    (10,   10,  10), (255,  60,  60)),
    ("CASO PERTURBADOR",    (15,   15,  15), (255,  50,  50)),
    ("RELATO PERTURBADOR",  (10,   10,  10), (200, 200, 200)),
    ("HISTORIA DURA",       (20,   20,  20), (255, 200,   0)),
    ("CONFESIÓN OSCURA",    (10,   10,  10), (220, 220, 220)),
    ("VERDAD INCÓMODA",     (15,   15,  15), (255, 230,   0)),
    ("LO QUE NADIE DICE",   (10,   10,  10), (200, 200, 200)),
    ("SIN TAPUJOS",         (20,   20,  20), (255,  50,  70)),
    # ── Redes sociales / Gen Z ───────────────────────────────────────────────
    ("STORYTIME REAL",      (255,  20, 147), (255, 255, 255)),
    ("RELATAME ESO",        (255,  10, 130), (255, 255, 255)),
    ("NO ME CREO NADA",     (255,  30, 147), (255, 255, 255)),
    ("¡QUÉ BUEN DRAMA!",    (255,  20, 147), (255, 255, 255)),
    ("NIVEL SERIE",         (240,  10, 140), (255, 255, 255)),
    ("PEOR QUE NETFLIX",    (255,  20, 147), (255, 255,   0)),
    ("VIRAL EN 3... 2...",  (255,  30, 147), (255, 255, 255)),
    ("¡TRENDING!",          (240,  10, 130), (255, 255, 255)),
    ("COMENTA ABAJO",       (255,  20, 147), (255, 255, 255)),
    ("¡SÍGUEME YA!",        (255,  15, 140), (255, 255, 255)),
    # ── Engagement directo ───────────────────────────────────────────────────
    ("¿TÚ QUÉ OPINAS?",     (0,   170, 170), (255, 255, 255)),
    ("DEJA TU OPINIÓN",     (0,   160, 160), (255, 255, 255)),
    ("COMENTA QUÉ HARÍAS",  (0,   175, 175), (255, 255, 255)),
    ("¿EQUIPO A O B?",      (0,   165, 165), (255, 255, 255)),
    ("VOTA EN COMENTARIOS", (0,   155, 155), (255, 255, 255)),
    ("¿PERDONAR O CORTAR?", (0,   170, 165), (255, 255, 255)),
    ("TU OPINIÓN IMPORTA",  (0,   160, 160), (255, 255, 255)),
    ("DILO EN COMENTARIOS", (0,   175, 170), (255, 255, 255)),
    ("¿QUÉ HARÍAS?",        (0,   160, 155), (255, 255, 255)),
    ("CUÉNTAME TU CASO",    (0,   170, 165), (255, 255, 255)),
    # ── Tiempo / urgencia ────────────────────────────────────────────────────
    ("¡ATENCIÓN!",          (255,  60,  20), (255, 255, 255)),
    ("MOMENTO CLAVE",       (255,  50,  10), (255, 255, 255)),
    ("¡ESTO ES SERIO!",     (240,  40,  10), (255, 255, 255)),
    ("PUNTO DE QUIEBRE",    (255,  55,  15), (255, 255, 255)),
    ("AQUÍ CAMBIA TODO",    (220,  30,  10), (255, 255, 255)),
    ("¡CUIDADO!",           (255,  60,  20), (255, 255,   0)),
    ("EL MOMENTO EXACTO",   (240,  45,  10), (255, 255, 255)),
    ("¡OJO CON ESTO!",      (255,  50,  20), (255, 255, 255)),
    ("¡ESTO LO CAMBIA!",    (230,  35,  10), (255, 255, 255)),
    ("¡LO QUE VIENE!",      (255,  55,  20), (255, 255, 255)),
]


def _make_sticker_png(out_path: Path, scene_idx: int) -> dict | None:
    """
    Genera un sticker PNG con fondo de color, texto bold y rotación ligera.
    Retorna dict con {path, x, y} para el overlay, o None si falla.
    """
    try:
        W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        text, bg_rgb, fg_rgb = random.choice(_STICKER_DATA)
        font_path = _find_font()
        font_size = random.choice([48, 52, 56, 60])
        font      = _load_font(font_path, font_size)

        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox  = dummy.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 28, 18
        sw, sh = tw + 2 * pad_x, th + 2 * pad_y

        box = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        bd  = ImageDraw.Draw(box)
        bd.rectangle([0, 0, sw - 1, sh - 1], fill=(*bg_rgb, 230))
        bd.rectangle([0, 0, sw - 1, sh - 1], outline=(*fg_rgb, 200), width=4)
        bd.text((pad_x, pad_y), text, font=font, fill=(*fg_rgb, 255))

        angle = random.choice([-5, -3, -2, 0, 0, 2, 3, 5])
        if angle:
            box = box.rotate(angle, expand=True, resample=Image.BICUBIC)

        # Glow: sombra borrosa detrás del sticker para que destaque sobre el video
        glow_radius = 14
        glow = box.filter(ImageFilter.GaussianBlur(radius=glow_radius))
        pad = glow_radius * 2
        canvas = Image.new("RGBA", (box.width + pad, box.height + pad), (0, 0, 0, 0))
        canvas.paste(glow, (pad // 2, pad // 2), glow)
        canvas.paste(box, (pad // 2, pad // 2), box)
        box = canvas

        box.save(str(out_path), "PNG")
        fw, fh = box.size

        # 8 zonas seguras: no cubre subtítulos (60% inferior) ni watermark (top-right corner)
        # scene_idx determina la zona base para que escenas consecutivas no colisionen
        zones = [
            (30,            120),              # sup-izq
            (W // 2 - fw // 2, 120),           # sup-centro
            (30,            420),              # mid-izq
            (W - fw - 30,   H // 3),           # mid-der
            (30,            H // 2 - fh // 2), # centro-izq
            (W - fw - 30,   H // 2 - fh // 2), # centro-der
            (30,            H - fh - 460),     # inf-izq (sobre subtítulos)
            (W - fw - 30,   H - fh - 460),     # inf-der (sobre subtítulos)
        ]
        base_zone = zones[scene_idx % len(zones)]
        jitter_x = random.randint(-30, 30)
        jitter_y = random.randint(-20, 20)
        x = base_zone[0] + jitter_x
        y = base_zone[1] + jitter_y
        x = max(10, min(x, W - fw - 10))
        y = max(80, min(y, H - fh - 420))

        return {"path": str(out_path), "x": x, "y": y}
    except Exception as e:
        logger.debug(f"  Sticker fallido escena {scene_idx}: {e}")
        return None


# 6 personalidades visuales para el hook — elegidas al azar cada video
_HOOK_STYLES = [
    {   # Drama negro — barra oscura, texto amarillo impacto
        "id": "drama",
        "bg": lambda: (*random.choice([(0,0,0),(15,0,0),(10,0,25)]), random.randint(185,215)),
        "fg": lambda: random.choice([(255,220,0),(255,200,0),(255,240,60)]),
        "stroke": (0, 0, 0),
        "size": lambda: random.choice([62, 66, 70]),
        "case": "upper",
        "prefix": lambda: random.choice(["", "⚡ ", "🔥 ", "👀 ", ""]),
        "bar": True,
        "y_frac": lambda: random.uniform(0.07, 0.12),
    },
    {   # Alerta roja — fondo rojo, letras blancas tipo breaking news
        "id": "alert",
        "bg": lambda: (*random.choice([(185,0,0),(200,20,20),(160,0,50)]), random.randint(205,235)),
        "fg": lambda: (255, 255, 255),
        "stroke": (70, 0, 0),
        "size": lambda: random.choice([58, 62, 66]),
        "case": "upper",
        "prefix": lambda: random.choice(["🚨 ", "❗ ", "⛔ ", "🔴 "]),
        "bar": True,
        "y_frac": lambda: random.uniform(0.08, 0.13),
    },
    {   # Ghost — sin barra, texto blanco con sombra negra brutal
        "id": "ghost",
        "bg": None,
        "fg": lambda: (255, 255, 255),
        "stroke": (0, 0, 0),
        "size": lambda: random.choice([68, 72, 76]),
        "case": "upper",
        "prefix": lambda: "",
        "bar": False,
        "y_frac": lambda: random.uniform(0.09, 0.15),
    },
    {   # Explosivo — fondo amarillo/naranja, texto negro
        "id": "explosive",
        "bg": lambda: (*random.choice([(255,200,0),(255,155,0),(255,230,50)]), random.randint(220,245)),
        "fg": lambda: (10, 10, 10),
        "stroke": (80, 50, 0),
        "size": lambda: random.choice([56, 60, 64]),
        "case": "upper",
        "prefix": lambda: random.choice(["💥 ", "🔥 ", "😱 ", "⚡ "]),
        "bar": True,
        "y_frac": lambda: random.uniform(0.07, 0.11),
    },
    {   # Misterio — fondo morado oscuro, texto verde neón
        "id": "mystery",
        "bg": lambda: (*random.choice([(20,0,45),(30,0,60),(10,10,35)]), random.randint(200,220)),
        "fg": lambda: random.choice([(180,255,120),(140,255,80),(200,255,150)]),
        "stroke": (0, 30, 0),
        "size": lambda: random.choice([60, 64, 68]),
        "case": "upper",
        "prefix": lambda: random.choice(["👁️ ", "🫣 ", "💀 ", "🕵️ "]),
        "bar": True,
        "y_frac": lambda: random.uniform(0.08, 0.13),
    },
    {   # Chyron TV — azul profundo, texto blanco, estilo noticiero
        "id": "chyron",
        "bg": lambda: (*random.choice([(0,55,180),(0,40,150),(25,0,120)]), random.randint(215,240)),
        "fg": lambda: (255, 255, 255),
        "stroke": (0, 0, 60),
        "size": lambda: random.choice([54, 58, 62]),
        "case": "upper",
        "prefix": lambda: random.choice(["📺 ", "🎙️ ", "📣 ", ""]),
        "bar": True,
        "y_frac": lambda: random.uniform(0.07, 0.10),
    },
]


def _make_hook_png(out_path: Path, hook_text: str) -> dict | None:
    """
    Genera PNG del hook del video con personalidad visual aleatoria (6 estilos).
    Cada video tiene un look completamente distinto — evita detección de contenido
    repetitivo/automático por YouTube.
    """
    try:
        W = config.VIDEO_WIDTH
        style = random.choice(_HOOK_STYLES)
        font_size: int = style["size"]()
        font_path = _find_font()
        font = _load_font(font_path, font_size)
        draw_dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

        prefix: str = style["prefix"]()
        raw = hook_text.upper() if style["case"] == "upper" else hook_text.title()
        full_text = prefix + raw

        max_w = W - 80
        words = full_text.split()
        lines: list[str] = []
        cur: list[str] = []
        for word in words:
            test = " ".join(cur + [word])
            bbox = draw_dummy.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_w and cur:
                lines.append(" ".join(cur))
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(" ".join(cur))

        lh = draw_dummy.textbbox((0, 0), "Ay", font=font)[3] + 14
        pad_y = random.randint(18, 30)
        total_h = lh * len(lines) + pad_y * 2

        if style["bar"] and style["bg"] is not None:
            bg_rgba = style["bg"]()
            canvas = Image.new("RGBA", (W, total_h), bg_rgba)
        else:
            canvas = Image.new("RGBA", (W, total_h), (0, 0, 0, 0))

        draw = ImageDraw.Draw(canvas)
        fg = style["fg"]()
        stroke = style["stroke"]
        y = pad_y
        stroke_off = random.randint(2, 4)

        for line in lines:
            bbox = draw_dummy.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            x = (W - tw) // 2
            # Sombra/stroke: dibuja el texto 8 veces offset para stroke grueso
            for ox, oy in [(-stroke_off,-stroke_off),(0,-stroke_off),(stroke_off,-stroke_off),
                           (-stroke_off,0),(stroke_off,0),
                           (-stroke_off,stroke_off),(0,stroke_off),(stroke_off,stroke_off)]:
                draw.text((x+ox, y+oy), line, font=font, fill=(*stroke, 220))
            draw.text((x, y), line, font=font, fill=(*fg, 255))
            y += lh

        # Rotación sutil para estilos sin barra (más natural, menos bot)
        angle = random.uniform(-1.5, 1.5) if not style["bar"] else 0
        if angle:
            canvas = canvas.rotate(angle, expand=False, resample=Image.BICUBIC)

        canvas.save(str(out_path), "PNG")
        y_pos = int(config.VIDEO_HEIGHT * style["y_frac"]())
        logger.info(f"  Hook estilo '{style['id']}' | prefijo='{prefix}' | y={y_pos}")
        return {"path": str(out_path), "x": 0, "y": y_pos}
    except Exception as e:
        logger.debug(f"Hook PNG fallido: {e}")
        return None


def _make_chat_bubble_png(out_path: Path, text: str, scene_idx: int) -> dict | None:
    """Overlay burbuja de chat estilo WhatsApp para escenas DESCUBRIMIENTO."""
    try:
        W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        font_path = _find_font()
        font = _load_font(font_path, 38)
        draw_dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

        max_w = int(W * 0.72)
        lines = _wrap_text(draw_dummy, text, font, max_w)[:3]
        lh = draw_dummy.textbbox((0, 0), "Ay", font=font)[3] + 10
        pad_x, pad_y = 28, 20
        bw = max(draw_dummy.textbbox((0, 0), l, font=font)[2] for l in lines) + 2 * pad_x
        bh = lh * len(lines) + 2 * pad_y

        bubble = Image.new("RGBA", (bw + 20, bh + 20), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bubble)
        bd.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=18, fill=(37, 211, 102, 230))
        bd.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=18, outline=(20, 180, 80, 200), width=3)
        bd.polygon([(0, bh - 20), (0, bh + 14), (18, bh - 10)], fill=(37, 211, 102, 230))
        y = pad_y
        for line in lines:
            bd.text((pad_x, y), line, font=font, fill=(255, 255, 255, 255))
            y += lh
        bubble.save(str(out_path), "PNG")
        bw_f, bh_f = bubble.size

        x = 30
        y_pos = int(H * 0.28) + (scene_idx % 3) * 60
        y_pos = max(120, min(y_pos, H - bh_f - 450))
        return {"path": str(out_path), "x": x, "y": y_pos}
    except Exception as e:
        logger.debug(f"Chat bubble fallido: {e}")
        return None


def _make_notification_png(out_path: Path, scene_idx: int) -> dict | None:
    """Overlay notificación iOS/Android para escenas INICIO o REFLEXION."""
    try:
        W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        font_path  = _find_font()
        font_title = _load_font(font_path, 32)
        font_body  = _load_font(font_path, 28)

        titles = ["CONFESIÓN REAL", "HISTORIA IMPACTANTE", "ALERTA EMOCIONAL", "CASO REAL"]
        bodies  = [
            "No vas a creer lo que pasó...",
            "Una historia que te dejará sin palabras",
            "Lo que nadie se atrevió a decir",
            "Esto cambió todo para siempre",
        ]
        title_txt = titles[scene_idx % len(titles)]
        body_txt  = bodies[scene_idx % len(bodies)]

        nw, nh = int(W * 0.82), 110
        notif = Image.new("RGBA", (nw, nh), (0, 0, 0, 0))
        nd    = ImageDraw.Draw(notif)
        nd.rounded_rectangle([0, 0, nw - 1, nh - 1], radius=14, fill=(18, 18, 18, 210))
        nd.rounded_rectangle([0, 0, nw - 1, nh - 1], radius=14, outline=(80, 80, 80, 180), width=2)
        nd.ellipse([16, 18, 38, 40], fill=(220, 30, 30, 255))
        nd.text((54, 14), title_txt, font=font_title, fill=(255, 255, 255, 255))
        nd.text((54, 56), body_txt,  font=font_body,  fill=(180, 180, 180, 255))
        nd.text((nw - 72, 14), "ahora", font=font_body, fill=(130, 130, 130, 255))
        notif.save(str(out_path), "PNG")

        x = (W - nw) // 2
        y = 160 + (scene_idx % 2) * 20
        return {"path": str(out_path), "x": x, "y": y}
    except Exception as e:
        logger.debug(f"Notification overlay fallido: {e}")
        return None


def _make_news_ticker_png(out_path: Path, text: str) -> dict | None:
    """Overlay ticker de noticias para escenas CLIMAX/CONFRONTACION."""
    try:
        W, H      = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
        font_path  = _find_font()
        font_label = _load_font(font_path, 32)
        font_news  = _load_font(font_path, 36)

        label     = "ULTIMA HORA"
        ticker_h  = 90
        ticker_w  = W

        ticker = Image.new("RGBA", (ticker_w, ticker_h), (0, 0, 0, 0))
        td     = ImageDraw.Draw(ticker)

        lw = td.textbbox((0, 0), label, font=font_label)[2] + 40
        td.rectangle([0, 0, lw, ticker_h],         fill=(200, 20, 20, 235))
        td.rectangle([lw, 0, ticker_w, ticker_h],   fill=(15, 15, 15, 215))
        td.rectangle([0, 0, ticker_w, 4],           fill=(255, 220, 0, 255))
        td.rectangle([0, ticker_h - 4, ticker_w, ticker_h], fill=(255, 220, 0, 255))

        lh = td.textbbox((0, 0), label, font=font_label)[3]
        td.text((16, (ticker_h - lh) // 2), label, font=font_label, fill=(255, 255, 255, 255))

        news_short = text[:55] + ("..." if len(text) > 55 else "")
        td.text((lw + 18, (ticker_h - 36) // 2), news_short, font=font_news,
                fill=(255, 255, 255, 255))
        ticker.save(str(out_path), "PNG")

        y = H - 560
        return {"path": str(out_path), "x": 0, "y": y}
    except Exception as e:
        logger.debug(f"News ticker fallido: {e}")
        return None


# Grading de color y grano de película — elegidos al azar para que cada video tenga look distinto
_COLOR_GRADES = [
    "",  # neutro (sin cambio)
    "eq=brightness=0.02:contrast=1.08:saturation=1.12,colorchannelmixer=rr=1.06:bb=0.88",   # cálido/dramático
    "eq=brightness=-0.02:contrast=1.10:saturation=1.05,colorchannelmixer=rr=0.92:bb=1.12",  # frío/suspense
    "eq=brightness=-0.04:contrast=1.22:saturation=1.18:gamma=0.92",                          # noir/oscuro
    "eq=brightness=0.04:contrast=0.93:saturation=0.72",                                      # vintage/desaturado
]
_GRAIN_LEVELS = [0, 0, 6, 8, 10, 12]  # 0 aparece 2× = 33% sin grano, resto con intensidad variada

# Duración de cada transición xfade (en segundos) — usada en scene_duration y apad
_XFADE_DUR = 0.4

# ─── Temas visuales rotativos ─────────────────────────────────────────────────
# Cada video elige uno al azar → paleta, posición del watermark y estilo del outro distintos
_VIDEO_THEMES = [
    {   # 1 — Rojo dramático (actual marca GATA CURIOSA)
        "name":    "rojo_drama",
        "primary": (204,   0,   0),
        "accent":  (255, 210,  40),
        "q_color": (255, 220,   0),
        "sep_color": (255, 210, 40),
        "bg_alpha": 191,
        "wm_x": "w-tw-28", "wm_y": "38", "wm_size": 38,
    },
    {   # 2 — Azul misterio / suspense
        "name":    "azul_misterio",
        "primary": (0,   70, 200),
        "accent":  (0,  210, 255),
        "q_color": (0,  230, 255),
        "sep_color": (0, 210, 255),
        "bg_alpha": 200,
        "wm_x": "28",       "wm_y": "38", "wm_size": 38,
    },
    {   # 3 — Negro noir / tenso
        "name":    "negro_noir",
        "primary": (20,  20,  20),
        "accent":  (210, 210, 210),
        "q_color": (255, 255, 255),
        "sep_color": (180, 180, 180),
        "bg_alpha": 215,
        "wm_x": "w-tw-28", "wm_y": "38", "wm_size": 38,
    },
    {   # 4 — Morado telenovela
        "name":    "morado_drama",
        "primary": (110,   0, 170),
        "accent":  (255,  80, 200),
        "q_color": (255,  90, 210),
        "sep_color": (255, 80, 200),
        "bg_alpha": 195,
        "wm_x": "28",       "wm_y": "38", "wm_size": 38,
    },
    {   # 5 — Verde impacto / viral
        "name":    "verde_impacto",
        "primary": (0,  145,  70),
        "accent":  (255, 230,   0),
        "q_color": (255, 235,   0),
        "sep_color": (255, 230, 0),
        "bg_alpha": 185,
        "wm_x": "(w-tw)/2", "wm_y": "38", "wm_size": 38,
    },
]


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


def _pick_best_frame(video_path: str, W: int, H: int) -> Image.Image:
    """
    Extrae 5 fotogramas del video en distintos momentos y elige el más colorido
    (mayor varianza de color = más interesante visualmente, evita frames oscuros).
    """
    import subprocess as _sp
    import tempfile, os as _os
    candidates = []
    offsets = [0.10, 0.25, 0.40, 0.55, 0.70]

    # Obtener duración del video
    try:
        probe = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10
        )
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 10.0

    for frac in offsets:
        ts = max(0.1, duration * frac)
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            _sp.run(
                ["ffmpeg", "-ss", str(ts), "-i", video_path,
                 "-vframes", "1", "-q:v", "2", "-y", tmp_path],
                capture_output=True, timeout=15
            )
            if _os.path.exists(tmp_path) and _os.path.getsize(tmp_path) > 1000:
                img = Image.open(tmp_path).convert("RGB")
                img.load()
                _os.unlink(tmp_path)
                candidates.append(img)
        except Exception:
            pass

    if not candidates:
        return _open_as_image(video_path, (W, H))

    # Elegir el frame con mayor varianza de color (descarta frames oscuros/blancos)
    import numpy as np
    best = max(candidates, key=lambda im: float(np.array(im).std()))
    return best.resize((W, H), Image.LANCZOS)


def generate_thumbnail(script: dict, images: list[str], output_path: str) -> str:
    """
    Genera thumbnail profesional 1280×720 para YouTube.

    - Selecciona la escena más dramática (DESCUBRIMIENTO/CONFRONTACION/CLIMAX)
    - Si es video Pexels, elige el frame más visualmente interesante de 5 candidatos
    - Texto muy grande (100px) en tercio inferior con gradiente fuerte
    - Franja roja con nombre del canal + barra dorada

    Returns:
        Path absoluto del thumbnail generado.
    """
    W_T, H_T  = 1280, 720
    RED       = (204, 0, 0)
    GOLD      = (255, 210, 40)
    WHITE     = (255, 255, 255)
    YELLOW    = (255, 230, 0)

    # Normalizar: images puede ser list[str] o list[list[str]]
    flat_images: list[str] = []
    for item in images:
        if isinstance(item, list):
            valid = [str(p) for p in item if Path(p).exists()]
            if valid:
                flat_images.append(valid[0])
        elif Path(item).exists():
            flat_images.append(str(item))
    images = flat_images

    scenes = script.get("scenes", [])

    # Elegir escena más dramática por acto
    dramatic_acts = {"DESCUBRIMIENTO", "CONFRONTACION", "CONFRONTACIÓN", "CLIMAX", "GIRO", "REVELACION", "REVELACIÓN"}
    best_idx = None
    for i, scene in enumerate(scenes):
        if i < len(images) and (scene.get("act", "").upper() in dramatic_acts):
            best_idx = i
            break
    if best_idx is None:
        best_idx = max(0, int(len(images) * 0.35))

    bg_path = images[best_idx] if best_idx < len(images) else (images[0] if images else None)

    # Fondo: extraer mejor frame si es video, o abrir imagen directamente
    if bg_path and _is_video(bg_path):
        bg = _pick_best_frame(bg_path, W_T, H_T)
    elif bg_path:
        bg = _open_as_image(bg_path, (W_T, H_T))
    else:
        bg = Image.new("RGB", (W_T, H_T), (10, 10, 20))

    # Gradiente: superior levemente oscurecido, inferior 85% negro (texto claro)
    ov = Image.new("RGBA", (W_T, H_T), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)
    for y in range(H_T):
        frac = y / H_T
        if frac < 0.15:
            alpha = 120           # 47% arriba (se ve el canal)
        elif frac > 0.45:
            # Gradiente intenso en parte inferior para texto legible
            t = (frac - 0.45) / 0.55
            alpha = int(120 + t * 117)   # 47% → 92%
        else:
            t = (frac - 0.15) / 0.30
            alpha = int(120 + t * 0)     # zona media sin cambio
        d.line([(0, y), (W_T, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path    = _find_font()
    font_channel = _load_font(font_path, 34)
    font_hook    = _load_font(font_path, 96)   # Grande — impacto máximo
    font_badge   = _load_font(font_path, 30)

    # ── Franja roja superior (62px) + barra dorada (5px) ─────────────────────
    stripe_h = 62
    draw.rectangle([0, 0, W_T, stripe_h], fill=(*RED, 235))
    draw.rectangle([0, stripe_h, W_T, stripe_h + 5], fill=GOLD)

    # Nombre del canal centrado en la franja
    channel = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMATICAS")
    c_bbox  = draw.textbbox((0, 0), channel, font=font_channel)
    c_w, c_h = c_bbox[2] - c_bbox[0], c_bbox[3] - c_bbox[1]
    draw.text(((W_T - c_w) // 2, (stripe_h - c_h) // 2),
              channel, font=font_channel, fill=WHITE)

    # ── Badge "HISTORIA REAL" — esquina superior izquierda ───────────────────
    badge_text = "HISTORIA REAL"
    b_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    bw, bh = b_bbox[2] - b_bbox[0] + 24, b_bbox[3] - b_bbox[1] + 14
    bx, by = 18, stripe_h + 14
    # Fondo rojo oscuro con borde amarillo
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(160, 0, 0))
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, outline=GOLD, width=3)
    draw.text((bx + 12, by + 7), badge_text, font=font_badge, fill=YELLOW)

    # ── Badge narrador (M/F) — esquina superior derecha ──────────────────────
    narrator_g = script.get("narrator_gender", "female")
    narrator_label = "ELLA" if narrator_g == "female" else "EL"
    n_bbox = draw.textbbox((0, 0), narrator_label, font=font_badge)
    nw, nh = n_bbox[2] - n_bbox[0] + 24, n_bbox[3] - n_bbox[1] + 14
    nx = W_T - nw - 18
    ny = stripe_h + 14
    badge_color = (180, 0, 120) if narrator_g == "female" else (0, 80, 180)
    draw.rounded_rectangle([nx, ny, nx + nw, ny + nh], radius=8, fill=badge_color)
    draw.rounded_rectangle([nx, ny, nx + nw, ny + nh], radius=8, outline=WHITE, width=2)
    draw.text((nx + 12, ny + 7), narrator_label, font=font_badge, fill=WHITE)

    # ── Hook text en tercio inferior (2 líneas máx) ───────────────────────────
    hook = script.get("title", script.get("hook", ""))
    hook_short = " ".join(hook.split()[:9])
    hook_lines = _wrap_text(draw, hook_short, font_hook, W_T - 80)[:2]
    line_h  = 108
    block_h = len(hook_lines) * line_h
    y_start = int(H_T * 0.76) - block_h

    for i, line in enumerate(hook_lines):
        bbox = draw.textbbox((0, 0), line, font=font_hook)
        x    = (W_T - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, y_start + i * line_h, line, font_hook, WHITE, 8)

    # ── Barra de urgencia inferior (30px roja con texto MIRA ESTO) ───────────
    urgency_y = H_T - 38
    draw.rectangle([0, urgency_y, W_T, H_T], fill=(*RED, 220))
    urgency_texts = ["MIRA ESTO", "HISTORIA IMPACTANTE", "NO PUEDO CREERLO", "DEBES VERLO"]
    urgency = random.choice(urgency_texts)
    u_font  = _load_font(font_path, 22)
    u_bbox  = draw.textbbox((0, 0), urgency, font=u_font)
    uw      = u_bbox[2] - u_bbox[0]
    draw.text(((W_T - uw) // 2, urgency_y + 8), urgency, font=u_font, fill=YELLOW)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(str(out_path), "JPEG", quality=95)
    logger.info(f"Thumbnail generado: {out_path.name} ({W_T}x{H_T}) | badge={narrator_label} | urgency='{urgency}'")
    return str(out_path)


def _render_outro_png(
    question: str,
    last_image_path: str | None,
    theme: dict | None = None,
) -> Image.Image:
    """
    Renderiza el frame del outro/CTA aplicando el tema visual del video.
    Cada tema tiene paleta propia → los outros se ven distintos entre videos.
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    t    = theme or _VIDEO_THEMES[0]  # fallback al primer tema

    Q_COLOR  = t["q_color"]
    SEP_COL  = t["sep_color"]
    PRIMARY  = t["primary"]
    BG_ALPHA = t["bg_alpha"]
    WHITE    = (255, 255, 255)
    GRAY     = (190, 190, 190)

    # Fondo: último clip/imagen de escena
    if last_image_path:
        bg = _open_as_image(last_image_path, (W, H))
    else:
        bg = Image.new("RGB", (W, H), (10, 10, 20))

    # Overlay oscuro (intensidad según tema)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, BG_ALPHA))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    font_path      = _find_font()
    font_question  = _load_font(font_path, 58)
    font_cta       = _load_font(font_path, 46)
    font_follow    = _load_font(font_path, 40)
    font_channel   = _load_font(font_path, 32)
    font_telegram  = _load_font(font_path, 34)

    center_y = H // 2

    # Franja de color (tema) — parte superior del outro
    stripe_h = 8
    draw.rectangle([0, 0, W, stripe_h], fill=PRIMARY)

    # Pregunta de engagement — color del tema
    q_lines = _wrap_text(draw, question, font_question, W - 100)[:3]
    line_h  = 72
    block_h = len(q_lines) * line_h
    q_y     = center_y - block_h // 2 - 60

    for i, line in enumerate(q_lines):
        bbox = draw.textbbox((0, 0), line, font=font_question)
        x    = (W - (bbox[2] - bbox[0])) // 2
        _draw_text_with_stroke(draw, x, q_y + i * line_h, line, font_question, Q_COLOR, 3)

    # Línea separadora (color del acento del tema)
    sep_y = q_y + block_h + 24
    draw.rectangle([80, sep_y, W - 80, sep_y + 3], fill=SEP_COL)

    # CTA: comenta
    cta1    = random.choice(getattr(config, "CTA_COMMENTS", ["Comenta tu respuesta abajo"]))
    c1_bbox = draw.textbbox((0, 0), cta1, font=font_cta)
    c1_x    = (W - (c1_bbox[2] - c1_bbox[0])) // 2
    cta1_y  = sep_y + 36
    _draw_text_with_stroke(draw, c1_x, cta1_y, cta1, font_cta, WHITE, 2)

    # CTA: sígueme
    cta2    = random.choice(getattr(config, "CTA_FOLLOW", ["Sigue para más historias reales"]))
    c2_bbox = draw.textbbox((0, 0), cta2, font=font_follow)
    c2_x    = (W - (c2_bbox[2] - c2_bbox[0])) // 2
    cta2_y  = cta1_y + 70
    _draw_text_with_stroke(draw, c2_x, cta2_y, cta2, font_follow, GRAY, 2)

    # Link de Telegram (si está configurado)
    channel_link = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
    if channel_link:
        tg_text = ">> MAS HISTORIAS EN TELEGRAM <<"
        tg_bbox = draw.textbbox((0, 0), tg_text, font=font_telegram)
        tg_x    = (W - (tg_bbox[2] - tg_bbox[0])) // 2
        tg_y    = cta2_y + 62
        _draw_text_with_stroke(draw, tg_x, tg_y, tg_text, font_telegram, (0, 200, 255), 2)

        link_bbox = draw.textbbox((0, 0), channel_link, font=font_telegram)
        link_x    = (W - (link_bbox[2] - link_bbox[0])) // 2
        _draw_text_with_stroke(draw, link_x, tg_y + 44, channel_link, font_telegram, (255, 255, 255), 2)
        ch_y_offset = tg_y + 106
    else:
        ch_y_offset = H - 110

    # Nombre del canal
    channel = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMÁTICAS")
    ch_bbox = draw.textbbox((0, 0), channel, font=font_channel)
    ch_x    = (W - (ch_bbox[2] - ch_bbox[0])) // 2
    ch_y    = max(ch_y_offset, H - 110)
    _draw_text_with_stroke(draw, ch_x, ch_y, channel, font_channel, (170, 170, 170), 2)

    # Barra inferior del tema
    draw.rectangle([0, H - stripe_h, W, H], fill=PRIMARY)

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


def _normalize_scene_images(images: list) -> list[list[str]]:
    """
    Normaliza la lista de imágenes/videos de escenas a list[list[str]].
    Acepta tanto list[str] (legado) como list[list[str]] (multi-cut de pexels_fetcher).
    """
    result: list[list[str]] = []
    for item in images:
        if isinstance(item, list):
            valid = [str(Path(p)) for p in item if Path(p).exists()]
            if valid:
                result.append(valid)
        elif Path(item).exists():
            result.append([str(Path(item))])
    return result


def _build_one_subclip(
    video_path: str,
    duration: float,
    fps: int,
    out_path: Path,
    act: str,
    kb_style: str,
) -> None:
    """
    Genera un sub-clip individual con Ken Burns específico y sin overlays.
    kb_style: zoom_in | zoom_out | pan_right | pan_left | pan_up | pan_down | dramatic
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    fade_d         = round(min(0.12, duration * 0.15), 3)
    fade_out_start = round(max(0.0, duration - fade_d), 3)

    _DRAMATIC_ACTS = {"CLIMAX", "CONFRONTACION", "DESCUBRIMIENTO", "GIRO", "REVELACION"}
    if kb_style == "dramatic" or act.upper() in _DRAMATIC_ACTS:
        SW, SH = int(W * 1.18), int(H * 1.18)
        dx, dy = SW - W, SH - H
        spd_x  = round(dx / max(duration, 1.0), 4)
        spd_y  = round(dy / max(duration, 1.0), 4)
        if kb_style in ("zoom_in", "dramatic"):
            crop_expr = (
                f"crop={W}:{H}:"
                f"x='max(0,min({dx},{dx}-t*{spd_x}))':"
                f"y='max(0,min({dy},{dy}-t*{spd_y}))'"
            )
        else:
            crop_expr = (
                f"crop={W}:{H}:"
                f"x='max(0,min({dx},t*{spd_x}))':"
                f"y='max(0,min({dy},t*{spd_y}))'"
            )
    else:
        SW, SH = int(W * 1.10), int(H * 1.10)
        dx, dy = SW - W, SH - H
        spd_x  = round(dx / max(duration, 1.0), 2)
        spd_y  = round(dy / max(duration, 1.0), 2)
        styles = {
            "pan_right": f"crop={W}:{H}:x='min({dx},t*{spd_x})':y={dy//2}",
            "pan_left":  f"crop={W}:{H}:x='max(0,{dx}-t*{spd_x})':y={dy//2}",
            "pan_up":    f"crop={W}:{H}:x={dx//2}:y='min({dy},t*{spd_y})'",
            "pan_down":  f"crop={W}:{H}:x={dx//2}:y='max(0,{dy}-t*{spd_y})'",
            "zoom_in":   (f"crop={W}:{H}:"
                          f"x='max(0,min({dx},{dx}-t*{spd_x}))':"
                          f"y='max(0,min({dy},{dy}-t*{spd_y}))'"),
            "zoom_out":  (f"crop={W}:{H}:"
                          f"x='min({dx},t*{spd_x})':y='min({dy},t*{spd_y})'"),
        }
        crop_expr = styles.get(kb_style, styles["pan_right"])

    vf = (
        f"scale={SW}:{SH}:force_original_aspect_ratio=increase,"
        f"crop={SW}:{SH},"
        f"{crop_expr},"
        f"fade=t=in:st=0:d={fade_d},"
        f"fade=t=out:st={fade_out_start}:d={fade_d}"
    )

    clip_dur   = _get_audio_duration(video_path)
    max_offset = max(0.0, clip_dur - duration - 0.5)
    start_off  = round(random.uniform(0.0, max_offset), 2) if max_offset > 0 else 0.0

    if clip_dur < duration:
        _ffmpeg(
            "-stream_loop", "-1", "-i", video_path,
            "-t", str(duration), "-vf", vf,
            "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(out_path),
            desc=f"subclip loop {Path(video_path).name[:25]}",
        )
    else:
        _ffmpeg(
            "-ss", str(start_off), "-i", video_path,
            "-t", str(duration), "-vf", vf,
            "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(out_path),
            desc=f"subclip {Path(video_path).name[:25]}",
        )


def _fast_concat_subclips(sub_paths: list[Path], out_path: Path, fps: int) -> None:
    """Concatena sub-clips con cortes rápidos (xfade 0.05s) para ritmo de edición activa."""
    if len(sub_paths) == 1:
        shutil.copy(str(sub_paths[0]), str(out_path))
        return

    FAST_CUT = 0.05
    durations = []
    for p in sub_paths:
        try:
            durations.append(_get_audio_duration(str(p)))
        except Exception:
            durations.append(2.0)

    input_args: list[str] = []
    for p in sub_paths:
        input_args.extend(["-i", str(p)])

    filter_parts: list[str] = []
    prev_label  = "[0:v]"
    cumulative  = 0.0
    for i in range(1, len(sub_paths)):
        cumulative += durations[i - 1]
        offset      = max(0.05, cumulative - i * FAST_CUT)
        new_label   = f"[fc{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade"
            f":duration={FAST_CUT}:offset={offset:.3f}{new_label}"
        )
        prev_label = new_label

    try:
        _ffmpeg(
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", prev_label,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(out_path),
            desc="fast concat subclips",
        )
    except RuntimeError:
        concat_txt = out_path.parent / f"_fc_{out_path.stem}.txt"
        concat_txt.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in sub_paths),
            encoding="utf-8",
        )
        _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c", "copy", str(out_path),
            desc="fast concat copy fallback",
        )
        concat_txt.unlink(missing_ok=True)


# ─── Clip de escena desde stock video (Pexels) ────────────────────────────────

def _build_scene_clip_from_video(
    video_paths: list[str],
    duration: float,
    fps: int,
    out_path: Path,
    scene_idx: int,
    act: str = "",
    hook_text: str = "",
) -> None:
    """
    Prepara el clip de escena con stock videos.
    Si video_paths tiene >1 clips: N sub-clips con Ken Burns alternado + cortes rápidos.
    Aplica overlays contextuales según el acto narrativo (chat bubble / ticker / notif / sticker).
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    _DRAMATIC_ACTS = {"CLIMAX", "CONFRONTACION", "DESCUBRIMIENTO", "GIRO", "REVELACION"}
    is_dramatic    = act.upper() in _DRAMATIC_ACTS

    # ── 1. Construir clip base (sin overlays) ─────────────────────────────────
    raw_clip = out_path.parent / f"{out_path.stem}_raw.mp4"

    if len(video_paths) > 1:
        # Multi-corte: N sub-clips con direcciones Ken Burns alternadas
        n        = len(video_paths)
        sub_dur  = duration / n
        sub_outs = [out_path.parent / f"{out_path.stem}_sub{j}.mp4" for j in range(n)]
        kb_dirs  = (
            ["zoom_in", "zoom_out"] * (n // 2 + 1)
            if is_dramatic else
            ["pan_right", "pan_left", "zoom_in", "pan_up"] * (n // 2 + 1)
        )
        for j, vp in enumerate(video_paths):
            _build_one_subclip(vp, sub_dur, fps, sub_outs[j], act, kb_dirs[j])
        _fast_concat_subclips(sub_outs, raw_clip, fps)
        for sp in sub_outs:
            sp.unlink(missing_ok=True)
    else:
        # Un solo clip — Ken Burns completo (comportamiento original)
        video_path     = video_paths[0]
        fade_d         = round(min(random.choice([0.05, 0.15, 0.25, 0.35]), duration * 0.18), 3)
        fade_out_start = round(max(0.0, duration - fade_d), 3)

        if is_dramatic:
            SW, SH     = int(W * 1.18), int(H * 1.18)
            dx, dy     = SW - W, SH - H
            zoom_spd_x = round(dx / max(duration, 1.0), 4)
            zoom_spd_y = round(dy / max(duration, 1.0), 4)
            if act.upper() == "CLIMAX":
                shake_amp  = 6
                shake_hz   = 3
                shake_expr = f"{dx//2}+{shake_amp}*sin(t*{shake_hz}*2*3.14159)"
                crop_expr  = (
                    f"crop={W}:{H}:"
                    f"x='max(0,min({dx},{dx}-t*{zoom_spd_x}+{shake_expr}))':"
                    f"y='max(0,min({dy},{dy//2}-t*{zoom_spd_y}))'"
                )
            else:
                crop_expr = (
                    f"crop={W}:{H}:"
                    f"x='max(0,min({dx},{dx//2}+t*{zoom_spd_x}/2))':"
                    f"y='max(0,min({dy},{dy}-t*{zoom_spd_y}))'"
                )
        else:
            SW, SH = int(W * 1.10), int(H * 1.10)
            dx, dy = SW - W, SH - H
            spd_x  = round(dx / max(duration, 1.0), 2)
            spd_y  = round(dy / max(duration, 1.0), 2)
            drift  = random.choice(["pan_right", "pan_left", "pan_up", "pan_down", "static"])
            if drift == "pan_right":
                crop_expr = f"crop={W}:{H}:x='min({dx},t*{spd_x})':y={dy//2}"
            elif drift == "pan_left":
                crop_expr = f"crop={W}:{H}:x='max(0,{dx}-t*{spd_x})':y={dy//2}"
            elif drift == "pan_up":
                crop_expr = f"crop={W}:{H}:x={dx//2}:y='min({dy},t*{spd_y})'"
            elif drift == "pan_down":
                crop_expr = f"crop={W}:{H}:x={dx//2}:y='max(0,{dy}-t*{spd_y})'"
            else:
                crop_expr = f"crop={W}:{H}:x={dx//2}:y={dy//2}"

        vf = (
            f"scale={SW}:{SH}:force_original_aspect_ratio=increase,"
            f"crop={SW}:{SH},"
            f"{crop_expr},"
            f"fade=t=in:st=0:d={fade_d},"
            f"fade=t=out:st={fade_out_start}:d={fade_d}"
        )

        clip_dur   = _get_audio_duration(video_path)
        max_offset = max(0.0, clip_dur - duration - 0.5)
        start_off  = round(random.uniform(0.0, max_offset), 2) if max_offset > 0 else 0.0

        if clip_dur < duration:
            input_pfx = ["-stream_loop", "-1", "-i", video_path]
        else:
            input_pfx = ["-ss", str(start_off), "-i", video_path]

        _ffmpeg(
            *input_pfx,
            "-t", str(duration), "-vf", vf,
            "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(raw_clip),
            desc=f"escena {scene_idx} raw",
        )

    # ── 2. Overlays según acto narrativo ─────────────────────────────────────
    overlays_data: list[tuple[str, int, int, float, float]] = []

    if scene_idx == 0 and hook_text:
        hook_png_path = out_path.parent / f"hook_{scene_idx:03d}.png"
        hook_png = _make_hook_png(hook_png_path, hook_text)
        if hook_png:
            t_end = round(min(2.5, duration * 0.40), 2)
            overlays_data.append((hook_png["path"], hook_png["x"], hook_png["y"], 0.0, t_end))
            logger.info(f"  Escena 1: hook '{hook_text[:40]}' ({t_end}s)")

    contextual_added = False
    act_u = act.upper()

    if act_u == "DESCUBRIMIENTO" and random.random() < 0.65:
        bubble_path = out_path.parent / f"bubble_{scene_idx:03d}.png"
        bubble_txt  = random.choice([
            "Algo no cuadraba...", "No podía creer lo que veía",
            "Ahí lo descubrí todo", "Mi mundo se detuvo", "No debí buscar eso",
        ])
        bubble = _make_chat_bubble_png(bubble_path, bubble_txt, scene_idx)
        if bubble:
            appear = round(random.uniform(0.5, duration * 0.35), 2)
            vanish = round(min(duration - 0.2, appear + random.uniform(2.5, 3.5)), 2)
            overlays_data.append((bubble["path"], bubble["x"], bubble["y"], appear, vanish))
            logger.info(f"  Escena {scene_idx+1}: chat bubble DESCUBRIMIENTO")
            contextual_added = True

    elif act_u in ("INICIO", "REFLEXION") and scene_idx >= 1 and random.random() < 0.55:
        notif_path = out_path.parent / f"notif_{scene_idx:03d}.png"
        notif = _make_notification_png(notif_path, scene_idx)
        if notif:
            appear = round(random.uniform(0.3, 1.0), 2)
            vanish = round(min(duration - 0.2, appear + 2.2), 2)
            overlays_data.append((notif["path"], notif["x"], notif["y"], appear, vanish))
            logger.info(f"  Escena {scene_idx+1}: notification {act_u}")
            contextual_added = True

    elif act_u in ("CLIMAX", "CONFRONTACION") and random.random() < 0.70:
        ticker_path = out_path.parent / f"ticker_{scene_idx:03d}.png"
        ticker_txt  = random.choice([
            "La verdad salió a la luz de la peor manera",
            "Nadie esperaba lo que pasó a continuación",
            "El momento más oscuro de la historia",
            "Todo explotó sin control",
            "Aquí llegó el punto de quiebre",
        ])
        ticker = _make_news_ticker_png(ticker_path, ticker_txt)
        if ticker:
            appear = round(random.uniform(0.4, duration * 0.30), 2)
            vanish = round(min(duration - 0.2, appear + random.uniform(2.5, 4.0)), 2)
            overlays_data.append((ticker["path"], ticker["x"], ticker["y"], appear, vanish))
            logger.info(f"  Escena {scene_idx+1}: news ticker {act_u}")
            contextual_added = True

    if not contextual_added and random.random() < 0.40:
        sticker_png = out_path.parent / f"sticker_{scene_idx:03d}.png"
        sticker = _make_sticker_png(sticker_png, scene_idx)
        if sticker:
            appear = round(random.uniform(0.4, max(0.4, duration * 0.35)), 2)
            vanish = round(min(duration - 0.2, appear + random.uniform(2.0, 3.2)), 2)
            overlays_data.append((sticker["path"], sticker["x"], sticker["y"], appear, vanish))
            logger.info(f"  Escena {scene_idx+1}: sticker ({sticker['x']},{sticker['y']})")

    # ── 3. Aplicar overlays sobre el raw clip ─────────────────────────────────
    if overlays_data:
        fd           = 0.28
        extra_inputs: list[str] = []
        for ov in overlays_data:
            extra_inputs.extend(["-i", ov[0]])

        fc_parts: list[str] = []
        prev = "0:v"
        for i, (_, ox, oy, ta, tb) in enumerate(overlays_data):
            ov_lbl   = f"ov{i}"
            next_lbl = "out" if i == len(overlays_data) - 1 else f"v{i+1}"
            fo_start = round(max(ta, tb - fd), 3)
            fc_parts.append(
                f"[{i+1}:v]"
                f"fade=t=in:st={ta:.3f}:d={fd}:alpha=1,"
                f"fade=t=out:st={fo_start:.3f}:d={fd}:alpha=1"
                f"[{ov_lbl}]"
            )
            fc_parts.append(
                f"[{prev}][{ov_lbl}]overlay=x={ox}:y={oy}:"
                f"enable='between(t,{max(0,ta-0.05):.3f},{tb+0.05:.3f})'[{next_lbl}]"
            )
            prev = next_lbl

        _ffmpeg(
            "-i", str(raw_clip), *extra_inputs,
            "-filter_complex", ";".join(fc_parts),
            "-map", "[out]",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(out_path),
            desc=f"escena {scene_idx} overlays ({len(overlays_data)})",
        )
        raw_clip.unlink(missing_ok=True)
    else:
        raw_clip.rename(out_path)



# ─── Música de fondo ──────────────────────────────────────────────────────────

_CONTENT_ID_BLACKLIST = ["fesliyan", "bensound", "incompetech"]  # registradas en Content ID de YouTube

def _pick_music() -> Path | None:
    """Elige aleatoriamente un archivo de música de assets/music/."""
    music_dir = config.ASSETS_DIR / "music"
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    if not tracks:
        return None
    for t in tracks:
        name_lower = t.name.lower()
        for blocked in _CONTENT_ID_BLACKLIST:
            if blocked in name_lower:
                logger.warning(
                    f"MÚSICA BLOQUEADA POR CONTENT ID: '{t.name}' es de '{blocked}' "
                    "— registrada en YouTube Content ID. Elimínala de assets/music/ y "
                    "reemplázala con música de Pixabay Music (pixabay.com/music) — CC0, no registrada."
                )
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


# ─── Concat con transiciones xfade ───────────────────────────────────────────

def _concat_with_xfade(clip_paths: list[Path], tmp_dir: Path, fps: int) -> Path:
    """
    Concatena clips aplicando transiciones xfade variadas entre cada corte.
    Offset formula: offset_i = sum(dur[0..i-1]) - i * XFADE_DUR
    Si xfade falla (FFmpeg antiguo), cae a concat copy silenciosamente.
    """
    out = tmp_dir / "concat_xfade.mp4"
    n   = len(clip_paths)

    if n == 1:
        shutil.copy(str(clip_paths[0]), str(out))
        return out

    TRANSITIONS = [
        "fade", "fadeblack",
        "fadewhite", "fadewhite", "fadewhite",  # flash blanco — estilo viral TikTok
        "dissolve",
        "wipeleft", "wiperight",
        "smoothleft", "smoothright",
        "slideup", "slideleft",
    ]
    XFADE_DUR = _XFADE_DUR

    durations = []
    for p in clip_paths:
        try:
            durations.append(_get_audio_duration(str(p)))
        except Exception:
            durations.append(5.0)

    input_args: list[str] = []
    for p in clip_paths:
        input_args.extend(["-i", str(p)])

    filter_parts: list[str] = []
    prev_label   = "[0:v]"
    cumulative   = 0.0

    for i in range(1, n):
        cumulative += durations[i - 1]
        offset      = max(0.1, cumulative - i * XFADE_DUR)
        trans       = random.choice(TRANSITIONS)
        new_label   = f"[v{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition={trans}"
            f":duration={XFADE_DUR}:offset={offset:.3f}{new_label}"
        )
        prev_label = new_label

    used = [p.split("transition=")[1].split(":")[0] for p in filter_parts]
    logger.info(f"  xfade: {n} clips → transiciones: {used}")

    try:
        _ffmpeg(
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", prev_label,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(out),
            desc="concat xfade",
        )
        return out
    except RuntimeError as e:
        logger.warning(f"xfade falló ({type(e).__name__}) — usando concat copy")
        concat_txt = tmp_dir / "concat_fb.txt"
        concat_txt.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in clip_paths),
            encoding="utf-8",
        )
        fallback = tmp_dir / "concat_copy.mp4"
        _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c", "copy", str(fallback),
            desc="concat copy fallback",
        )
        return fallback


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

    # Normalizar: images puede ser list[str] (legado) o list[list[str]] (multi-cut)
    scene_images = _normalize_scene_images(images)
    if not scene_images:
        raise FileNotFoundError("No hay imágenes válidas para ensamblar")
    valid_images = [clips[0] for clips in scene_images]  # primer clip por escena (para intro/outro)

    if output_path is None:
        ts          = time.strftime("%Y%m%d_%H%M%S")
        output_path = config.OUTPUT_DIR / f"video_{ts}.mp4"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_total = time.time()

    # ── 1. Duración del audio ──────────────────────────────────────────────────
    total_duration = _get_audio_duration(str(audio_path))

    # Cap de duración: YouTube bloquea Shorts >60s con música que tenga Content ID.
    # Máximo seguro: 54s narración + 5.5s outro = 59.5s total.
    _outro_dur_ref = getattr(config, "OUTRO_DURATION", 5.5)
    _max_narration = 54.0 - _outro_dur_ref  # deja margen de ~0.5s
    if total_duration > _max_narration:
        logger.warning(
            f"Audio demasiado largo ({total_duration:.1f}s > {_max_narration:.0f}s). "
            f"El Short total superaría 60s — el script debe ser más corto (objetivo: 110-130 palabras). "
            f"Continuando igualmente, pero el video puede ser bloqueado por YouTube."
        )

    # Compensar xfade: cada transición resta XFADE_DUR del timeline total.
    # Sin compensación el outro aparece N*0.4s antes de que acabe la narración.
    # Fórmula: scene_duration = TTS/n_scenes + XFADE_DUR garantiza que el
    # offset del xfade scene→outro caiga exactamente en t=total_duration.
    scene_duration = total_duration / len(scene_images) + _XFADE_DUR

    # Elegir tema visual para este video
    theme = random.choice(_VIDEO_THEMES)
    logger.info(
        f"Audio: {total_duration:.1f}s | "
        f"{len(scene_images)} escenas x {scene_duration:.1f}s | "
        f"tema: {theme['name']}"
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="csf_video_"))
    try:
        clip_paths: list[Path] = []
        scenes = script.get("scenes", [])

        # ── 2. Intro (opcional — 0 = desactivado para Shorts) ────────────────
        if config.INTRO_DURATION > 0:
            logger.info("Renderizando intro...")
            t0 = time.time()
            intro_img = _render_intro_png(
                hook=script.get("title", script.get("hook", "")),
                title=script.get("title", ""),
                first_image_path=valid_images[0] if valid_images else None,
            )
            intro_png  = tmp_dir / "intro.png"
            intro_clip = tmp_dir / "clip_000_intro.mp4"
            intro_img.save(str(intro_png), "PNG")

            intro_frames         = int(config.INTRO_DURATION * config.FPS)
            intro_fade_in        = min(0.5, config.INTRO_DURATION * 0.2)
            intro_fade_out_start = round(max(0.1, config.INTRO_DURATION - 0.4), 3)
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
        else:
            logger.info("Intro desactivada (INTRO_DURATION=0) — video empieza directo con la historia")

        # ── 3. Pre-renderizar subtítulos ──────────────────────────────────────
        # REMOVIDO: Ahora se inyecta directamente vía FFmpeg ASS en el paso 6.

        # ── 4. Generar clips de escena en paralelo ─────────────────────────────
        n_scenes = len(scene_images)
        logger.info(f"Generando {n_scenes} clips (stock video)...")
        t0 = time.time()

        scene_clip_paths = [tmp_dir / f"clip_{i+1:03d}_scene.mp4" for i in range(n_scenes)]

        def build_scene(idx):
            t_s   = time.time()
            act   = scenes[idx].get("act", "") if idx < len(scenes) else ""
            hook  = script.get("hook", "") if idx == 0 else ""
            clips = scene_images[idx]  # list[str] — uno o más clips para esta escena
            if _is_video(clips[0]):
                _build_scene_clip_from_video(
                    video_paths=clips,
                    duration=scene_duration,
                    fps=config.FPS,
                    out_path=scene_clip_paths[idx],
                    scene_idx=idx,
                    act=act,
                    hook_text=hook,
                )
            logger.info(
                f"  Escena {idx+1}/{n_scenes} [{act or '-'}] "
                f"({len(clips)} clip(s)) lista ({time.time()-t_s:.1f}s)"
            )

        # Paralelo — 4 workers máximo (más saturan CPU/IO sin beneficio)
        errors = []
        with ThreadPoolExecutor(max_workers=min(4, n_scenes)) as executor:
            futures = {executor.submit(build_scene, i): i for i in range(n_scenes)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"  ERROR escena {idx+1}: {e}")
                    errors.append(idx)

        if errors:
            raise RuntimeError(
                f"Escenas fallidas: {errors}. Verifica la API key de Pexels y la conexión."
            )

        clip_paths.extend(scene_clip_paths)
        logger.info(f"Clips de escenas listos en {time.time()-t0:.1f}s")

        # ── 4b. Outro/CTA ──────────────────────────────────────────────────────
        outro_dur = getattr(config, "OUTRO_DURATION", 4.0)
        logger.info(f"Renderizando outro ({outro_dur}s)...")
        t0 = time.time()

        question    = script.get("pregunta", "Y tu, que harias en mi lugar?")
        last_img    = valid_images[-1] if valid_images else None
        outro_img   = _render_outro_png(question, last_img, theme=theme)
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

        # ── 5. Concat con transiciones xfade ──────────────────────────────────
        logger.info("Concatenando clips con transiciones xfade...")
        t0         = time.time()
        concat_mp4 = _concat_with_xfade(clip_paths, tmp_dir, config.FPS)
        logger.info(f"Concat con transiciones en {time.time()-t0:.1f}s")

        # ── 6. Mezclar audio (TTS + música) y añadir ASS ───────────────────────
        logger.info("Mezclando audio e inyectando subtítulos dinámicos ASS...")
        t0 = time.time()

        # Mezclar TTS con música de fondo si hay tracks disponibles
        mixed_audio = tmp_dir / "audio_mixed.aac"
        music_vol   = getattr(config, "MUSIC_VOLUME", 0.10)
        final_audio_raw = _mix_with_music(audio_path, mixed_audio, music_vol=music_vol)

        # Padding de audio: outro_dur + XFADE_DUR para que el silencio cubra
        # el overlap del último xfade (scene→outro) sin cortar el CTA.
        padded_audio = tmp_dir / "audio_padded.aac"
        _ffmpeg(
            "-i", str(final_audio_raw),
            "-af", f"apad=pad_dur={outro_dur + _XFADE_DUR}",
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

        # Estilo visual aleatorio para este video (color grade + grain)
        grade  = random.choice(_COLOR_GRADES)
        grain  = random.choice(_GRAIN_LEVELS)
        grade_label = grade[:45] if grade else "neutro"
        logger.info(f"Estilo visual: grade='{grade_label}' | grain={grain}")

        ass_path  = audio_path.with_suffix(".ass")
        filters   = []          # lista de filtros vf que se unirán con coma
        if grade:
            filters.append(grade)
        if grain > 0:
            filters.append(f"noise=c0s={grain}:c0f=t+u")
        filters.append("vignette=angle=PI/5:mode=forward")
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

        # Watermark: posición y tamaño según tema visual
        channel   = getattr(config, "CHANNEL_NAME", "CONFESIONES DRAMÁTICAS")
        wm_x      = theme["wm_x"]
        wm_y      = theme["wm_y"]
        wm_size   = theme["wm_size"]
        wm_alpha  = 0.75
        font_fp   = _find_font()
        if font_fp:
            safe_font = font_fp.replace("\\", "/")
            if len(safe_font) >= 2 and safe_font[1] == ":":
                safe_font = safe_font[0] + "\\:" + safe_font[2:]
            wm = (f"drawtext=text='{channel}':"
                  f"fontfile='{safe_font}':"
                  f"fontsize={wm_size}:fontcolor=white@{wm_alpha}:"
                  f"x={wm_x}:y={wm_y}:"
                  f"shadowcolor=black@0.75:shadowx=2:shadowy=2")
        else:
            wm = (f"drawtext=text='{channel}':"
                  f"fontsize={wm_size}:fontcolor=white@{wm_alpha}:"
                  f"x={wm_x}:y={wm_y}:"
                  f"shadowcolor=black@0.75:shadowx=2:shadowy=2")
        filters.append(wm)

        vf_args = ["-vf", ",".join(filters)] if filters else []

        _final_encode_args = [
            "-i", str(concat_mp4),
            "-i", str(final_audio),
            *vf_args,
            "-map", "0:v", "-map", "1:a",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-movflags", "+faststart",
            "-c:a", "aac", "-ar", "44100",
            "-shortest",
        ]
        try:
            _ffmpeg(
                *_final_encode_args,
                "-c:v", "h264_nvenc", "-preset", "p6", "-cq", "20",
                str(output_path),
                desc="audio mix + subtítulos ASS (NVENC)",
            )
        except RuntimeError:
            logger.warning("h264_nvenc no disponible — usando libx264 (CPU)")
            _ffmpeg(
                *_final_encode_args,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                str(output_path),
                desc="audio mix + subtítulos ASS (CPU fallback)",
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
