"""
tts_engine.py — Text-to-Speech con soporte dual: edge-tts (neural) o pyttsx3 (local)

Backend seleccionable via TTS_BACKEND en .env:
  - "edge"    → Microsoft Edge TTS neural (gratis, alta calidad, requiere internet)
                 Voces: es-MX-DaliaNeural, es-MX-JorgeNeural, es-CO-GonzaloNeural, etc.
  - "pyttsx3" → Windows SAPI (100% offline, requiere voz española instalada en Windows)

Ejemplo de uso:
  from modules.tts_engine import generate_audio
  path = generate_audio("Nunca debí revisar su celular...", "output/narration.mp3")
"""

import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Voces neurales de Edge TTS — separadas por género
EDGE_VOICES_FEMALE = [
    "es-MX-DaliaNeural",  # Mujer, México — natural y dramática
    "es-AR-ElenaNeural",  # Mujer, Argentina
    "es-US-PalomaNeural",  # Mujer, español neutro
    "es-ES-ElviraNeural",  # Mujer, España (fallback)
]

EDGE_VOICES_MALE = [
    "es-MX-JorgeNeural",  # Hombre, México — voz grave y dramática
    "es-CO-GonzaloNeural",  # Hombre, Colombia
    "es-US-AlonsoNeural",  # Hombre, español neutro
    "es-ES-AlvaroNeural",  # Hombre, España (fallback)
]

# Lista completa como fallback
EDGE_VOICES_ES_LATAM = EDGE_VOICES_FEMALE + EDGE_VOICES_MALE


# ─── Detección automática de género del narrador ──────────────────────────────

# Adjetivos/participios en primera persona que revelan el género del narrador
_FEMALE_MARKERS = {
    "traicionada",
    "engañada",
    "abandonada",
    "enamorada",
    "confundida",
    "devastada",
    "herida",
    "humillada",
    "desesperada",
    "decepcionada",
    "avergonzada",
    "embarazada",
    "casada",
    "divorciada",
    "asustada",
    "sorprendida",
    "equivocada",
    "cansada",
    "perdida",
    "destrozada",
    "rota",
    "atrapada",
    "ilusionada",
    "enamoradísima",
    "celosa",
    "sola",  # "me quedé sola" — alta señal femenina en narrativa
}

_MALE_MARKERS = {
    "traicionado",
    "engañado",
    "abandonado",
    "enamorado",
    "confundido",
    "devastado",
    "herido",
    "humillado",
    "desesperado",
    "decepcionado",
    "avergonzado",
    "casado",
    "divorciado",
    "asustado",
    "sorprendido",
    "equivocado",
    "cansado",
    "perdido",
    "destrozado",
    "roto",
    "atrapado",
    "ilusionado",
    "celoso",
    "solo",  # "me quedé solo" — alta señal masculina
}


def detect_narrator_gender(text: str) -> str:
    """
    Detecta el género del narrador analizando adjetivos en primera persona.

    En español los adjetivos y participios concuerdan en género con el sujeto.
    'Me sentí traicionadA' → narrador femenino.
    'Me sentí traicionadO' → narrador masculino.

    Returns:
        "female" | "male"  (default "female" si no hay señal clara)
    """
    words = set(text.lower().split())
    female_score = len(words & _FEMALE_MARKERS)
    male_score = len(words & _MALE_MARKERS)

    gender = "male" if male_score > female_score else "female"
    logger.info(
        f"Género narrador detectado: {gender} "
        f"(señales fem={female_score}, masc={male_score})"
    )
    return gender


# ─── Formato ASS (Subtítulos dinámicos — formato viral 2025) ─────────────────


def _group_words_for_display(timed_words: list, max_group: int = 3) -> list:
    """
    Agrupa palabras en chunks de 2-3 para mostrar juntas en pantalla.

    Formato viral 2025: 2-3 palabras simultáneas se procesan más rápido
    que una sola — el espectador no espera, la narración fluye.

    Rompe el grupo si:
    1. El grupo tiene max_group palabras
    2. La última palabra termina en puntuación fuerte (. ? !)
    3. El gap hasta la siguiente palabra es > 0.25s (pausa hablada real)

    Returns:
        Lista de chunks: cada chunk = [(word, start, dur), ...]
    """
    if not timed_words:
        return []

    chunks = []
    current: list = []

    for i, (word, start, dur) in enumerate(timed_words):
        current.append((word, start, dur))

        # Condición 1: grupo lleno
        if len(current) >= max_group:
            chunks.append(current)
            current = []
            continue

        # Condición 2: puntuación fuerte → corte natural de frase
        word_stripped = word.rstrip()
        if word_stripped and word_stripped[-1] in ".?!":
            chunks.append(current)
            current = []
            continue

        # Condición 3: pausa hablada larga antes de la siguiente palabra
        if i + 1 < len(timed_words):
            next_start = timed_words[i + 1][1]
            current_end = start + dur
            if next_start - current_end > 0.25:
                chunks.append(current)
                current = []

    if current:
        chunks.append(current)

    return chunks


def _fix_word_timings(words_data: list, min_dur: float = 0.15) -> list:
    """
    Post-procesa timestamps de palabras para subtítulos sincronizados.

    DISEÑO CRÍTICO — no cascadear drift:
    - El start time de cada palabra viene de Whisper/stable-ts y es preciso.
      NO se modifica el start excepto para resolver solapamientos reales.
    - La duración de DISPLAY se extiende hasta el inicio de la siguiente palabra
      (con un pequeño gap), sin afectar el start de la siguiente.
    - Solo se aplica min_dur cuando hay espacio disponible.

    Esto evita el bug anterior donde forzar min_dur=0.25s en palabras cortas
    ("a", "y", "de") empujaba todos los subtítulos siguientes adelante del audio.
    """
    if not words_data:
        return words_data

    n = len(words_data)
    fixed = []

    for i, (word, start, duration) in enumerate(words_data):
        # Resolver solapamiento con palabra anterior
        if fixed:
            prev_start, prev_dur = fixed[-1][1], fixed[-1][2]
            prev_end = prev_start + prev_dur
            if start < prev_end - 0.01:  # solapamiento real (>10ms)
                start = prev_end + 0.02  # empujar 20ms después

        # Calcular duración de display:
        # — Si hay siguiente palabra: extender hasta su start (con 30ms de gap)
        # — Último word: usar duración real o min_dur, lo que sea mayor
        if i + 1 < n:
            next_start = words_data[i + 1][1]
            available = next_start - start
            if available > min_dur:
                # Extender para llenar el espacio, dejando 30ms de separación
                display_dur = available - 0.03
            else:
                # Poco espacio: usar la duración real sin estirar
                display_dur = max(duration, 0.08)
        else:
            display_dur = max(duration, min_dur)

        fixed.append((word, start, display_dur))

    return fixed


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def _write_ass_file(words_data: list, output_path: Path, audio_duration: float = 0.0):
    """
    Genera ASS con efecto KARAOKE + POP (estilo CapCut viral 2025).

    Muestra grupos de 2-3 palabras simultáneamente:
    - Palabra activa (la que se está diciendo): amarillo brillante + bounce
    - Palabras de contexto del mismo grupo: blanco
    - Palabras de tensión dramática (traición, mentira...): rojo + tamaño mayor

    Esto permite al espectador leer con contexto mientras sigue la palabra exacta,
    mejorando la retención vs el typewriter donde solo hay una palabra visible.

    Compatible con libass/FFmpeg sin dependencias adicionales.
    """
    if not words_data:
        return

    font_size     = getattr(config, "SUBTITLE_FONT_SIZE", 88)
    margin_v      = getattr(config, "SUBTITLE_MARGIN_V", 280)
    subtitle_font = getattr(config, "SUBTITLE_FONT", "Impact")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.VIDEO_WIDTH}
PlayResY: {config.VIDEO_HEIGHT}
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{subtitle_font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,10,4,2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Palabras de tensión dramática → rojo brillante cuando son la palabra activa
    TENSION_WORDS = {
        "nunca", "jamás", "traición", "traicionó", "traicionada", "traicionado",
        "llorando", "lloré", "lloró", "destrozado", "destrozada", "destrozó",
        "mentira", "mentiras", "mintió", "mintieron", "secreto", "secretos",
        "descubrí", "descubrió", "engañó", "engañaba", "engañé",
        "rompió", "abandonó", "abandoné", "muerto", "muerta", "morir",
        "dolor", "miedo", "terror", "horror", "odio", "odié", "odió",
        "destruyó", "destruí", "perdí", "perdió",
    }

    # Colores ASS: &H00BBGGRR& (AA=00 = completamente opaco)
    COL_YELLOW = r"\c&H0000FFFF&"  # amarillo — palabra activa
    COL_RED    = r"\c&H000000FF&"  # rojo     — palabras de tensión dramática
    COL_WHITE  = r"\c&H00FFFFFF&"  # blanco   — palabras de contexto en el mismo grupo

    # Bounce ligero cuando la palabra activa cambia (110% → 100% en 80ms)
    BOUNCE = r"\fscx110\fscy110\t(0,80,\fscx100\fscy100)"

    # Borde negro grueso + sombra: legibles sobre cualquier imagen
    BASE = r"\3c&H000000&\4c&H80000000&\b1\bord10\shad5"

    # Agrupar palabras en chunks de 2-3 usando pausas y puntuación naturales
    chunks = _group_words_for_display(words_data, max_group=3)
    lines: list[str] = []

    for ci, chunk in enumerate(chunks):
        chunk_upper = [w.upper().strip() for w, _s, _d in chunk]
        chunk_end   = chunk[-1][1] + chunk[-1][2]

        # El último subtitle debe cubrir hasta el final real del audio,
        # porque Whisper/stable-ts subestima la duración de la última palabra
        if ci == len(chunks) - 1 and audio_duration > 0:
            chunk_end = max(chunk_end, audio_duration - 0.05)

        for k, (word, start, _dur) in enumerate(chunk):
            word_stripped = word.upper().strip()
            if not word_stripped:
                continue

            # Este slot dura hasta que empieza la siguiente palabra (o fin del chunk)
            end = chunk[k + 1][1] if k + 1 < len(chunk) else chunk_end

            # Detectar si la palabra activa es de tensión dramática
            word_lower = word.lower().strip(".,!?;:")
            is_tension = word_lower in TENSION_WORDS
            active_col = COL_RED if is_tension else COL_YELLOW
            active_fs  = font_size + 8 if is_tension else font_size

            # Construir el texto con colores por palabra:
            # activa = amarillo/rojo + bounce, contexto = blanco sin bounce
            parts = []
            for j, cw in enumerate(chunk_upper):
                if j == k:
                    parts.append(f"{{\\fs{active_fs}{active_col}}}{cw}")
                else:
                    parts.append(f"{{\\fs{font_size}{COL_WHITE}}}{cw}")

            # Tag de apertura: alineación inferior centro + borde + sombra + bounce
            open_tag = "{\\an2" + BASE + BOUNCE + "}"

            lines.append(
                f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},"
                f"Default,,0,0,0,,{open_tag}{' '.join(parts)}"
            )

    output_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


# ─── stable-ts: timestamps exactos con forced alignment ─────────────────────


_stable_ts_model = None  # caché: no recargar en cada llamada


def _get_stable_ts_word_timestamps(audio_path: Path, language: str = "es") -> list:
    """
    Extrae timestamps exactos por palabra usando stable-ts (transcripción).

    Usa model.transcribe() — NO model.align() — porque align() requiere el texto
    original como segundo argumento obligatorio. transcribe() funciona directo
    sobre el audio y devuelve timestamps por palabra igual de precisos.

    Returns:
        Lista de (word, start_sec, duration_sec) — vacía si falla o no instalado.
    """
    global _stable_ts_model
    try:
        import stable_whisper

        if _stable_ts_model is None:
            logger.info("stable-ts: cargando modelo base (primera vez)...")
            _stable_ts_model = stable_whisper.load_model("base")

        logger.info("stable-ts: transcribiendo para timestamps exactos...")
        result = _stable_ts_model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            verbose=False,
            ignore_compatibility=True,  # suprime warning de versión de openai-whisper
        )

        timed_words = []
        for seg in result.segments:
            for w in seg.words:
                clean = w.word.strip()
                if clean:
                    timed_words.append((clean, float(w.start), float(w.end - w.start)))

        if timed_words:
            logger.info(f"stable-ts: {len(timed_words)} palabras sincronizadas")
            for i, (word, start, dur) in enumerate(timed_words[:5]):
                logger.debug(f"  [{i}] '{word}' @ {start:.2f}s ({dur:.2f}s)")
        else:
            logger.warning("stable-ts: sin palabras detectadas")

        return timed_words
    except ImportError:
        logger.warning("stable-ts no instalado — pip install stable-ts")
        return []
    except Exception as e:
        logger.warning(f"stable-ts falló ({type(e).__name__}: {e}) — usando fallback")
        return []


# ─── faster-whisper: timestamps exactos por palabra ──────────────────────────

_faster_whisper_model = None  # caché: no recargar en cada llamada


def _get_whisper_word_timestamps(audio_path: Path, language: str = "es") -> list:
    """
    Transcribe el audio con faster-whisper y extrae timestamps exactos por palabra.

    IMPORTANTE — vad_filter=False para audio TTS:
    El VAD (Voice Activity Detection) está diseñado para audio con ruido/silencios
    naturales. El TTS es voz sintetizada limpia sin silencios largos. Con VAD activo,
    el modelo a veces corta el inicio/final de frases y provoca drift en subtítulos.

    Returns:
        Lista de (word, start_sec, duration_sec) — vacía si falla o no está instalado.
    """
    global _faster_whisper_model
    try:
        from faster_whisper import WhisperModel

        if _faster_whisper_model is None:
            model_size = "base"  # 142MB — mejor para español que 'tiny'
            device = "cuda" if _cuda_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(
                f"faster-whisper: cargando modelo '{model_size}' en {device} (primera vez)..."
            )
            _faster_whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)

        logger.info("faster-whisper: transcribiendo para timestamps exactos...")
        segments, _ = _faster_whisper_model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            beam_size=5,
            vad_filter=False,   # TTS es audio limpio — VAD causa drift en subtítulos
            condition_on_previous_text=False,  # cada segmento independiente
        )
        timed_words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    clean = w.word.strip()
                    if clean:
                        timed_words.append((clean, w.start, w.end - w.start))

        if timed_words:
            logger.info(f"faster-whisper: {len(timed_words)} palabras sincronizadas")
            for i, (word, start, dur) in enumerate(timed_words[:5]):
                logger.debug(f"  [{i}] '{word}' @ {start:.2f}s ({dur:.2f}s)")
        else:
            logger.warning("faster-whisper: sin palabras detectadas")

        return timed_words
    except ImportError:
        logger.warning("faster-whisper no instalado — pip install faster-whisper")
        return []
    except Exception as e:
        logger.warning(
            f"faster-whisper falló ({type(e).__name__}: {e}) — usando fallback"
        )
        return []


def _cuda_available() -> bool:
    """Detecta si CUDA está disponible para aceleración GPU."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False
    except Exception as e:
        logger.warning(f"faster-whisper falló ({e}) — usando fallback")
        return []


# ─── Backend edge-tts ─────────────────────────────────────────────────────────


def _add_dramatic_pauses(text: str) -> str:
    """
    Refuerza pausas naturales en el texto para que edge-tts las interprete mejor.

    edge-tts ya pausa en puntuación, pero los scripts de Ollama a veces
    no tienen suficientes puntos. Este paso asegura pausas dramáticas en
    los lugares correctos sin usar SSML (que edge-tts escapa como texto literal).
    """
    import re

    # Normalizar espacios múltiples
    text = re.sub(r'  +', ' ', text.strip())

    # Asegurar que las elipsis tengan exactamente 3 puntos (edge-tts pausa en ellas)
    text = re.sub(r'\.{4,}', '...', text)

    # Si una frase termina sin puntuación antes de mayúscula, añadir punto
    text = re.sub(r'([a-záéíóúüñ])\s+([A-ZÁÉÍÓÚÜÑ])', r'\1. \2', text)

    return text


async def _edge_tts_generate(text: str, output_mp3: Path, voice: str) -> None:
    """
    Genera audio con edge-tts y construye el archivo ASS de subtítulos.

    Las voces en español (DaliaNeural, etc.) no emiten WordBoundary — solo
    SentenceBoundary. Por eso recogemos ambos eventos y usamos el que esté
    disponible para calcular los tiempos de cada palabra.

    Nota: edge-tts v7+ escapa todo el texto con html.escape() internamente,
    así que NO se puede pasar SSML — hay que usar los parámetros rate/pitch
    del constructor para ajustar la prosodia.
    """
    import edge_tts

    prepared_text = _add_dramatic_pauses(text)
    # rate="+3%"  → ligeramente más rápido que natural: urgente y tenso (Shorts virales)
    # pitch="-4Hz" → voz más grave para compensar energía dramática
    # boundary="WordBoundary" → solicitar eventos por palabra (v7+)
    communicate = edge_tts.Communicate(
        prepared_text,
        voice,
        rate="+3%",
        pitch="-4Hz",
        boundary="WordBoundary",
    )

    audio_data = bytearray()
    word_events = []  # WordBoundary — timing exacto por palabra
    sentence_events = []  # SentenceBoundary — fallback para voces ES

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            # 100-nanosecond units → segundos
            offset_s = chunk["offset"] / 10_000_000.0
            duration_s = chunk["duration"] / 10_000_000.0
            word_events.append((chunk["text"], offset_s, duration_s))
        elif chunk["type"] == "SentenceBoundary":
            offset_s = chunk["offset"] / 10_000_000.0
            duration_s = chunk["duration"] / 10_000_000.0
            sentence_events.append((chunk["text"], offset_s, duration_s))

    with open(output_mp3, "wb") as f:
        f.write(audio_data)

    # ── Timestamps exactos con stable-ts (fuente primaria — forced alignment) ──
    timed_words = _get_stable_ts_word_timestamps(output_mp3)

    # ── Fallback 1: faster-whisper ────────────────────────────────────────────
    if not timed_words:
        timed_words = _get_whisper_word_timestamps(output_mp3)

    # ── Fallback 2: WordBoundary de edge-tts (voces EN) ───────────────────────
    if not timed_words and word_events:
        timed_words = word_events
        logger.info("Usando WordBoundary de edge-tts")

    # ── Fallback 3: SentenceBoundary proporcional por caracteres (voces ES) ───
    if not timed_words and sentence_events:
        for sent_text, sent_start, sent_dur in sentence_events:
            sent_words = sent_text.split()
            if not sent_words:
                continue
            total_chars = sum(len(w) for w in sent_words) or 1
            offset = 0.0
            for word in sent_words:
                w_dur = sent_dur * (len(word) / total_chars)
                timed_words.append((word, sent_start + offset, w_dur))
                offset += w_dur
        logger.info("Usando SentenceBoundary proporcional")

    # ── Fallback 4: distribución uniforme por duración real del audio ─────────
    if not timed_words:
        try:
            from pydub import AudioSegment

            total_dur = len(AudioSegment.from_mp3(str(output_mp3))) / 1000.0
        except Exception:
            total_dur = 40.0
        words = text.split()
        if words:
            start_offset = 0.3
            effective_dur = max(total_dur - start_offset - 0.2, len(words) * 0.3)
            w_dur = effective_dur / len(words)
            timed_words = [
                (w, start_offset + i * w_dur, w_dur) for i, w in enumerate(words)
            ]
        logger.warning(
            "Usando distribución uniforme (instala faster-whisper para sync exacto)"
        )

    # ── Post-procesar: eliminar solapamientos y garantizar duración mínima ─────
    timed_words = _fix_word_timings(timed_words)

    # Duración real del audio para que el último subtítulo no se corte
    try:
        from pydub import AudioSegment as _AS
        audio_dur = len(_AS.from_mp3(str(output_mp3))) / 1000.0
    except Exception:
        audio_dur = 0.0

    ass_path = output_mp3.with_suffix(".ass")
    _write_ass_file(timed_words, ass_path, audio_duration=audio_dur)
    if timed_words:
        logger.info(f"ASS generado: {len(timed_words)} palabras sincronizadas")


def _generate_with_edge_tts(text: str, output_path: Path, gender: str = "auto") -> str:
    """
    Genera audio usando Microsoft Edge TTS (neural, gratis).
    Selecciona la voz según el género del narrador.

    Args:
        text:        Texto en español a convertir
        output_path: Path de salida (.mp3)
        gender:      "female" | "male" | "auto" (auto = detectar del texto)

    Returns:
        Path del archivo MP3 generado
    """
    import edge_tts

    # Resolver género si es "auto"
    if gender == "auto":
        gender = detect_narrator_gender(text)

    # Construir lista de voces: la configurada en .env primero (si coincide el género),
    # luego las del género detectado, luego las del otro género como fallback
    configured_voice = getattr(config, "TTS_EDGE_VOICE", None)
    if gender == "male":
        ordered = EDGE_VOICES_MALE + EDGE_VOICES_FEMALE
    else:
        ordered = EDGE_VOICES_FEMALE + EDGE_VOICES_MALE

    # Si hay voz configurada manualmente y NO está ya en la lista, meterla primero
    if configured_voice and configured_voice not in ordered:
        ordered = [configured_voice] + ordered

    gender_label = "MASCULINO" if gender == "male" else "FEMENINO"
    logger.info(
        f"Narrador detectado: {gender_label} → voz principal: '{ordered[0]}' "
        f"(fallbacks: {ordered[1:3]})"
    )

    last_error = None
    for voice in ordered:
        try:
            logger.info(f"edge-tts: probando '{voice}'...")
            asyncio.run(_edge_tts_generate(text, output_path, voice))
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"edge-tts: audio OK con voz '{voice}'")
                return str(output_path)
        except Exception as e:
            last_error = e
            logger.warning(f"edge-tts: '{voice}' falló: {e}")
            continue

    raise RuntimeError(
        f"edge-tts falló con todas las voces.\n"
        f"Último error: {last_error}\n"
        "Verifica conexión a internet o instala: python -m pip install edge-tts"
    )


# ─── Backend pyttsx3 ──────────────────────────────────────────────────────────


def _get_spanish_voice(engine) -> str | None:
    """
    Busca la mejor voz en español en Windows SAPI.
    Prioridad: ES-MX > ES genérico > inglés como último recurso.

    Returns:
        ID de la voz, o None si no hay ninguna.
    """
    voices = engine.getProperty("voices")
    if not voices:
        return None

    logger.debug(f"Voces SAPI disponibles ({len(voices)}):")
    for v in voices:
        logger.debug(f"  [{v.id}] {v.name}")

    # 1. Español México por nombre
    for v in voices:
        if any(n in v.name.lower() for n in ["sabina", "raul", "paloma", "jorge"]):
            logger.info(f"Voz ES-MX: {v.name}")
            return v.id

    # 2. Español México / LATAM por ID
    for v in voices:
        if any(c in v.id.lower() for c in ["es-mx", "es_mx", "es-us", "es_us"]):
            logger.info(f"Voz ES-MX (por ID): {v.name}")
            return v.id

    # 3. Cualquier voz española (España incluida)
    for v in voices:
        id_l = v.id.lower()
        name_l = v.name.lower()
        langs = " ".join(str(l).lower() for l in v.languages)
        if (
            "spanish" in name_l
            or "español" in name_l
            or "es-" in id_l
            or "es_" in id_l
            or "spanish" in langs
            or "0xc0a" in id_l
        ):
            logger.info(f"Voz ES genérico: {v.name}")
            return v.id

    # 4. Sin español — advertir y usar inglés
    logger.warning(
        "No hay voces en español instaladas en Windows.\n"
        "  → Opciones:\n"
        "    A) Instalar voz española: Configuración → Hora e idioma → Voz → Agregar voces\n"
        "       Buscar 'Español (México)' e instalar\n"
        "    B) Usar edge-tts (recomendado): cambiar TTS_BACKEND=edge en .env\n"
        "       Requiere: python -m pip install edge-tts"
    )
    return voices[0].id if voices else None


def _generate_with_pyttsx3(text: str, output_path: Path) -> str:
    """
    Genera audio con pyttsx3 (Windows SAPI, 100% offline).
    Requiere voz española instalada en Windows para audio en español.

    Args:
        text: Texto a convertir
        output_path: Path de salida (.mp3 o .wav)

    Returns:
        Path del archivo de audio generado
    """
    import pyttsx3
    from pydub import AudioSegment

    try:
        engine = pyttsx3.init()
    except Exception as e:
        raise RuntimeError(
            f"No se pudo inicializar pyttsx3: {e}\n"
            "Alternativa: cambiar TTS_BACKEND=edge en .env"
        )

    voice_id = _get_spanish_voice(engine)
    if voice_id:
        engine.setProperty("voice", voice_id)
    engine.setProperty("rate", config.TTS_VOICE_RATE)
    engine.setProperty("volume", config.TTS_VOLUME)

    # Guardar WAV temporal
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
        engine.stop()

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError("pyttsx3 generó un WAV vacío — sin voces instaladas.")

        # Convertir WAV → MP3
        try:
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(output_path), format="mp3", bitrate="192k")
        except Exception as e:
            logger.warning(f"pydub falló ({e}), guardando como WAV")
            output_path = output_path.with_suffix(".wav")
            wav_path.rename(output_path)

    finally:
        if wav_path.exists():
            wav_path.unlink(missing_ok=True)

    return str(output_path)


# ─── API pública ──────────────────────────────────────────────────────────────


def get_audio_duration(audio_path: Path) -> float:
    """
    Retorna la duración en segundos de un archivo de audio MP3 o WAV.
    """
    from pydub import AudioSegment

    suffix = audio_path.suffix.lower()
    if suffix == ".mp3":
        seg = AudioSegment.from_mp3(str(audio_path))
    elif suffix == ".wav":
        seg = AudioSegment.from_wav(str(audio_path))
    else:
        seg = AudioSegment.from_file(str(audio_path))
    return len(seg) / 1000.0


def list_voices() -> list[dict]:
    """Lista voces SAPI disponibles (útil para diagnóstico)."""
    import pyttsx3

    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    engine.stop()
    return [
        {"id": v.id, "name": v.name, "languages": v.languages} for v in (voices or [])
    ]


def generate_audio(
    text: str, output_path: str | None = None, gender: str = "auto"
) -> str:
    """
    Convierte texto en español a audio MP3 con voz acorde al género del narrador.

    Elige el backend según TTS_BACKEND en .env:
      - "edge"    → edge-tts neural (recomendado, gratis, necesita internet)
      - "pyttsx3" → Windows SAPI (offline, necesita voz española en Windows)

    Args:
        text:        Texto en español a narrar
        output_path: Ruta de salida .mp3. Si es None, genera en output/
        gender:      "female" | "male" | "auto" (auto detecta del texto)
                     Viene del campo narrator_gender del script generado por Ollama.

    Returns:
        Path absoluto del archivo MP3 generado

    Example:
        >>> path = generate_audio("Me quedé sola...", "output/narration.mp3", gender="female")
        >>> path = generate_audio("Me quedé solo...", "output/narration.mp3", gender="male")
    """
    if not text or not text.strip():
        raise ValueError("El texto TTS no puede estar vacío")

    if output_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = config.OUTPUT_DIR / f"audio_{ts}.mp3"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backend = getattr(config, "TTS_BACKEND", "pyttsx3").lower()
    word_count = len(text.split())
    logger.info(
        f"Generando audio TTS ({word_count} palabras) | backend='{backend}' | gender='{gender}'"
    )

    if backend == "edge":
        result = _generate_with_edge_tts(text, output_path, gender=gender)
    else:
        result = _generate_with_pyttsx3(text, output_path)
        # Mock ASS para pyttsx3
        ass_path = output_path.with_suffix(".ass")
        if output_path.exists():
            dur = get_audio_duration(output_path)
            words = text.split()
            w_dur = dur / max(1, len(words))
            mock_words = _fix_word_timings([(w, i * w_dur, w_dur) for i, w in enumerate(words)])
            _write_ass_file(mock_words, ass_path, audio_duration=dur)

    # Log duración
    try:
        dur = get_audio_duration(Path(result))
        size_kb = Path(result).stat().st_size // 1024
        logger.info(f"Audio generado: {Path(result).name} ({dur:.1f}s, {size_kb}KB)")
        if dur < 30:
            logger.warning(f"Audio corto ({dur:.1f}s) — el script tiene pocas palabras")
        elif dur > 65:
            logger.warning(f"Audio largo ({dur:.1f}s) — reducir script para Shorts")
    except Exception as e:
        logger.warning(f"No se pudo calcular duración: {e}")

    return result
