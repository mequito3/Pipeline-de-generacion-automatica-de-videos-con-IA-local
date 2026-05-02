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
import os
import random
import tempfile
import time
from pathlib import Path

import config

# ══════════════════════════════════════════════════════════════════════════════
# BSOD PROTECTION — LEER ANTES DE MODIFICAR ESTE BLOQUE
# ══════════════════════════════════════════════════════════════════════════════
# VoiceBox (servidor TTS en http://127.0.0.1:17493) corre en la GPU y mantiene
# su modelo Qwen/XTTS cargado en VRAM permanentemente mientras está activo.
#
# PROBLEMA: Cuando Python importa stable_whisper o faster_whisper, PyTorch
# inicializa el runtime CUDA *aunque device="cpu"*, reservando 500MB–1GB de VRAM
# adicional. En laptops con GPU modesta, la suma agota la VRAM disponible →
# el driver NVIDIA crashea → BSOD con "your device ran into a problem".
#
# SOLUCIÓN: Ocultar todos los dispositivos CUDA a este proceso Python antes de
# que cualquier biblioteca ML sea importada. VoiceBox NO se ve afectado porque
# es un proceso separado que ya estaba corriendo con su propio entorno.
#
# REGLA: Solo omitir si WHISPER_DEVICE=cuda está explícitamente configurado en
# .env (cuando tienes VRAM de sobra, >=8GB libres con VoiceBox activo).
#
# NOTA PARA FUTUROS AGENTES: NO eliminar ni mover este bloque.
# Documentado en: memory/project_voicebox.md
# ══════════════════════════════════════════════════════════════════════════════
_whisper_device_cfg = os.getenv("WHISPER_DEVICE", "cpu").lower()
if _whisper_device_cfg == "cpu":
    # Siempre forzar — no verificar si ya existe. NVIDIA tools u otras libs pueden
    # haber seteado CUDA_VISIBLE_DEVICES=0 antes de llegar aquí, lo que anulaba
    # la protección. Override incondicional garantiza que torch/CTranslate2 no toque la GPU.
    # IMPORTANTE: usar "-1" (NO "") — en Windows, string vacío no deshabilita CUDA;
    # "-1" es el valor documentado para ocultar todos los dispositivos en Linux y Windows.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # fuerza evaluación consistente
    # Limitar threads de CPU para Whisper/PyTorch — evita CLOCK_WATCHDOG_TIMEOUT.
    # Sin este límite, faster-whisper satura todos los núcleos al 100%
    # durante la alineación, lo que activa el watchdog timer del SO y produce BSOD.
    # Override incondicional — si el sistema tenía OMP_NUM_THREADS=8, el límite
    # no se aplicaba antes (condición "not in os.environ" fallaba silenciosamente).
    # WHISPER_CPU_THREADS=1 por defecto — seguro en laptops. Sube a 2 solo si tienes buena refrigeración.
    _wt = os.getenv("WHISPER_CPU_THREADS", "1")
    for _tenv in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_tenv] = _wt

logger = logging.getLogger(__name__)

# Último backend TTS realmente usado — leído por company.py para la notificación
_last_tts_backend: str = "desconocido"


def get_last_tts_backend() -> str:
    """Devuelve el backend TTS realmente usado en la última llamada."""
    return _last_tts_backend


# Voces neurales de Edge TTS — separadas por género
EDGE_VOICES_FEMALE = [
    "es-MX-DaliaNeural",    # Mujer, México — natural y dramática (la mejor)
    "es-MX-NuriaNeural",    # Mujer, México — conversacional, joven
    "es-MX-CarlotaNeural",  # Mujer, México — expresiva
    "es-MX-MarinaNeural",   # Mujer, México — cálida
    "es-AR-ElenaNeural",    # Mujer, Argentina
    "es-US-PalomaNeural",   # Fallback neutro
]

EDGE_VOICES_MALE = [
    "es-MX-JorgeNeural",    # Hombre, México — grave y dramático (el mejor)
    "es-MX-GerardoNeural",  # Hombre, México — conversacional
    "es-MX-LucianoNeural",  # Hombre, México — natural
    "es-CO-GonzaloNeural",  # Hombre, Colombia
    "es-US-AlonsoNeural",   # Fallback neutro
]

# ─── Paletas de subtítulos por emoción ───────────────────────────────────────
# Formato ASS: &H00BBGGRR& (Alpha=00 = completamente opaco)
# Cada emoción tiene 3 colores:
#   active  → la palabra que se está diciendo en este momento
#   context → las otras palabras del mismo grupo (en pantalla al mismo tiempo)
#   tension → palabras de alta carga dramática (traición, mentira, jamás…)

SUBTITLE_SCHEMES: dict[str, dict] = {
    # ── TRAICIÓN / INFIDELIDAD ── 5 variantes ────────────────────────────────
    "traicion": {
        "name":    "traicion_rojo_vivo",
        "active":  r"\c&H000000FF&",   # rojo puro
        "context": r"\c&H006688FF&",   # salmón suave
        "tension": r"\c&H000000CC&",   # carmesí oscuro
    },
    "traicion_2": {
        "name":    "traicion_rojo_sangre",
        "active":  r"\c&H000000CC&",   # rojo oscuro
        "context": r"\c&H00AAAAAA&",   # gris medio
        "tension": r"\c&H000000FF&",   # rojo brillante
    },
    "traicion_3": {
        "name":    "traicion_naranja_rabia",
        "active":  r"\c&H000066FF&",   # naranja intenso
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
    "traicion_4": {
        "name":    "traicion_coral",
        "active":  r"\c&H00507FFF&",   # coral
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H000000FF&",   # rojo
    },
    "traicion_5": {
        "name":    "traicion_rosa_herida",
        "active":  r"\c&H00CC44FF&",   # rosa intenso
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000CC&",   # rojo oscuro
    },

    # ── SHOCK / DESCUBRIMIENTO ── 5 variantes ────────────────────────────────
    "shock": {
        "name":    "shock_amarillo_viral",
        "active":  r"\c&H0000FFFF&",   # amarillo brillante
        "context": r"\c&H00FFFFFF&",   # blanco puro
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "shock_2": {
        "name":    "shock_amarillo_neon",
        "active":  r"\c&H0000EEFF&",   # amarillo neón
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H000000FF&",   # rojo
    },
    "shock_3": {
        "name":    "shock_blanco_pop",
        "active":  r"\c&H00FFFFFF&",   # blanco puro
        "context": r"\c&H00AAAAAA&",   # gris
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "shock_4": {
        "name":    "shock_dorado_revelacion",
        "active":  r"\c&H0000D7FF&",   # dorado
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
    "shock_5": {
        "name":    "shock_cyan_frio",
        "active":  r"\c&H00FFFF00&",   # cyan helado
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H000000FF&",   # rojo para tensión
    },

    # ── TRISTEZA / ABANDONO ── 5 variantes ───────────────────────────────────
    "tristeza": {
        "name":    "tristeza_azul_cielo",
        "active":  r"\c&H00FF8C00&",   # azul cielo
        "context": r"\c&H00CCCCCC&",   # gris claro
        "tension": r"\c&H00FF0000&",   # azul profundo
    },
    "tristeza_2": {
        "name":    "tristeza_azul_marino",
        "active":  r"\c&H00FF4400&",   # azul marino
        "context": r"\c&H00BBBBBB&",   # gris
        "tension": r"\c&H00FF0000&",   # azul profundo
    },
    "tristeza_3": {
        "name":    "tristeza_lila",
        "active":  r"\c&H00EE88CC&",   # lila suave
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H00CC0088&",   # violeta
    },
    "tristeza_4": {
        "name":    "tristeza_plata",
        "active":  r"\c&H00EEEEEE&",   # plata
        "context": r"\c&H00999999&",   # gris oscuro
        "tension": r"\c&H00FF8C00&",   # azul cielo
    },
    "tristeza_5": {
        "name":    "tristeza_turquesa",
        "active":  r"\c&H00D4A000&",   # turquesa
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H00FF0000&",   # azul profundo
    },

    # ── VENGANZA / CONFRONTACIÓN ── 5 variantes ──────────────────────────────
    "venganza": {
        "name":    "venganza_naranja_fuego",
        "active":  r"\c&H000066FF&",   # naranja fuego
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
    "venganza_2": {
        "name":    "venganza_rojo_naranja",
        "active":  r"\c&H000033FF&",   # rojo-naranja
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H000000FF&",   # rojo puro
    },
    "venganza_3": {
        "name":    "venganza_dorado_triunfo",
        "active":  r"\c&H0000D7FF&",   # dorado
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000066FF&",   # naranja
    },
    "venganza_4": {
        "name":    "venganza_verde_victoria",
        "active":  r"\c&H0000FF66&",   # verde lima
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "venganza_5": {
        "name":    "venganza_amarillo_fuego",
        "active":  r"\c&H0000FFFF&",   # amarillo
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },

    # ── MIEDO / TERROR / OSCURO ── 5 variantes ───────────────────────────────
    "miedo": {
        "name":    "miedo_violeta_electrico",
        "active":  r"\c&H00CC0088&",   # violeta eléctrico
        "context": r"\c&H00BBBBBB&",   # gris medio
        "tension": r"\c&H00FF00FF&",   # magenta
    },
    "miedo_2": {
        "name":    "miedo_magenta_oscuro",
        "active":  r"\c&H00FF00FF&",   # magenta puro
        "context": r"\c&H00AAAAAA&",   # gris
        "tension": r"\c&H000000FF&",   # rojo
    },
    "miedo_3": {
        "name":    "miedo_morado_profundo",
        "active":  r"\c&H00880080&",   # morado
        "context": r"\c&H00CCCCCC&",   # gris claro
        "tension": r"\c&H00FF00FF&",   # magenta
    },
    "miedo_4": {
        "name":    "miedo_rojo_tenue",
        "active":  r"\c&H003333CC&",   # rojo tenue / granada
        "context": r"\c&H00999999&",   # gris oscuro
        "tension": r"\c&H000000FF&",   # rojo puro
    },
    "miedo_5": {
        "name":    "miedo_cyan_frio",
        "active":  r"\c&H00FFEE00&",   # cyan frío
        "context": r"\c&H00888888&",   # gris oscuro
        "tension": r"\c&H00CC0088&",   # violeta
    },

    # ── HUMILLACIÓN / VERGÜENZA ── 5 variantes ───────────────────────────────
    "humillacion": {
        "name":    "humillacion_rosa_intenso",
        "active":  r"\c&H00AA00FF&",   # rosa fuerte
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
    "humillacion_2": {
        "name":    "humillacion_fucsia",
        "active":  r"\c&H00CC00DD&",   # fucsia
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H000000FF&",   # rojo
    },
    "humillacion_3": {
        "name":    "humillacion_lila_plata",
        "active":  r"\c&H00EE88AA&",   # lila rosado
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H00AA00FF&",   # rosa
    },
    "humillacion_4": {
        "name":    "humillacion_naranja_vergüenza",
        "active":  r"\c&H000088FF&",   # naranja suave
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
    "humillacion_5": {
        "name":    "humillacion_blanco_frio",
        "active":  r"\c&H00FFFFFF&",   # blanco puro
        "context": r"\c&H00AAAAAA&",   # gris
        "tension": r"\c&H00AA00FF&",   # rosa
    },

    # ── SECRETO / MISTERIO ── 5 variantes ────────────────────────────────────
    "secreto": {
        "name":    "secreto_cyan_frio",
        "active":  r"\c&H00FFFF00&",   # cyan
        "context": r"\c&H00999999&",   # gris oscuro
        "tension": r"\c&H00880080&",   # morado
    },
    "secreto_2": {
        "name":    "secreto_verde_matrix",
        "active":  r"\c&H0000FF00&",   # verde neón
        "context": r"\c&H00666666&",   # gris oscuro
        "tension": r"\c&H0000CC00&",   # verde oscuro
    },
    "secreto_3": {
        "name":    "secreto_plata_hielo",
        "active":  r"\c&H00F0F0F0&",   # plata casi blanco
        "context": r"\c&H00888888&",   # gris medio
        "tension": r"\c&H00FFFF00&",   # cyan
    },
    "secreto_4": {
        "name":    "secreto_turquesa_oscuro",
        "active":  r"\c&H00AA8800&",   # turquesa oscuro
        "context": r"\c&H00AAAAAA&",   # gris
        "tension": r"\c&H00CC0088&",   # violeta
    },
    "secreto_5": {
        "name":    "secreto_indigo",
        "active":  r"\c&H00FF2200&",   # índigo
        "context": r"\c&H00BBBBBB&",   # gris
        "tension": r"\c&H00FF00FF&",   # magenta
    },

    # ── FAMILIA / DRAMA FAMILIAR ── 5 variantes ──────────────────────────────
    "familia": {
        "name":    "familia_dorado_calido",
        "active":  r"\c&H0000D7FF&",   # dorado
        "context": r"\c&H00DDDDFF&",   # crema
        "tension": r"\c&H000040FF&",   # rojo-naranja
    },
    "familia_2": {
        "name":    "familia_ambar",
        "active":  r"\c&H0000AAFF&",   # ámbar
        "context": r"\c&H00EEEEEE&",   # blanco cálido
        "tension": r"\c&H000000FF&",   # rojo
    },
    "familia_3": {
        "name":    "familia_terracota",
        "active":  r"\c&H003366CC&",   # terracota
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H000000FF&",   # rojo
    },
    "familia_4": {
        "name":    "familia_verde_oliva",
        "active":  r"\c&H002288AA&",   # verde oliva
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H000040FF&",   # naranja-rojo
    },
    "familia_5": {
        "name":    "familia_marron_calido",
        "active":  r"\c&H004488CC&",   # marrón cálido
        "context": r"\c&H00DDDDDD&",   # gris
        "tension": r"\c&H000066FF&",   # naranja
    },

    # ── TRABAJO / JEFE / LABORAL ── 5 variantes ──────────────────────────────
    "trabajo": {
        "name":    "trabajo_verde_neon",
        "active":  r"\c&H0000FF00&",   # verde neón
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "trabajo_2": {
        "name":    "trabajo_azul_corporativo",
        "active":  r"\c&H00FF6600&",   # azul corporativo
        "context": r"\c&H00DDDDDD&",   # gris
        "tension": r"\c&H000000FF&",   # rojo
    },
    "trabajo_3": {
        "name":    "trabajo_blanco_frio",
        "active":  r"\c&H00FFFFFF&",   # blanco
        "context": r"\c&H00999999&",   # gris oscuro
        "tension": r"\c&H0000FF00&",   # verde
    },
    "trabajo_4": {
        "name":    "trabajo_cyan_tech",
        "active":  r"\c&H00FFDD00&",   # cyan tech
        "context": r"\c&H00AAAAAA&",   # gris
        "tension": r"\c&H000000FF&",   # rojo
    },
    "trabajo_5": {
        "name":    "trabajo_amarillo_alerta",
        "active":  r"\c&H0000FFFF&",   # amarillo
        "context": r"\c&H00CCCCCC&",   # gris
        "tension": r"\c&H000066FF&",   # naranja
    },

    # ── NEUTRO / REFLEXIÓN ── 5 variantes ────────────────────────────────────
    "neutro": {
        "name":    "neutro_blanco_limpio",
        "active":  r"\c&H00FFFFFF&",   # blanco puro
        "context": r"\c&H00CCCCCC&",   # gris claro
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "neutro_2": {
        "name":    "neutro_plata",
        "active":  r"\c&H00EEEEEE&",   # plata
        "context": r"\c&H00AAAAAA&",   # gris medio
        "tension": r"\c&H0000FFFF&",   # amarillo
    },
    "neutro_3": {
        "name":    "neutro_cyan_suave",
        "active":  r"\c&H00EEBB00&",   # cyan suave
        "context": r"\c&H00DDDDDD&",   # gris claro
        "tension": r"\c&H000066FF&",   # naranja
    },
    "neutro_4": {
        "name":    "neutro_verde_suave",
        "active":  r"\c&H0088EE00&",   # verde suave
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H0000A5FF&",   # naranja
    },
    "neutro_5": {
        "name":    "neutro_limon",
        "active":  r"\c&H0000FFCC&",   # limón
        "context": r"\c&H00FFFFFF&",   # blanco
        "tension": r"\c&H000000FF&",   # rojo
    },
}

# Lista plana para random.choice() cuando no hay texto disponible
_SCHEMES_LIST: list[dict] = list(SUBTITLE_SCHEMES.values())

# ─── Palabras clave por emoción (para detección automática) ──────────────────
_EMOTION_KEYWORDS: dict[str, list[str]] = {
    "traicion": [
        "traición", "traicionó", "traicioné", "traicionada", "traicionado",
        "traicion", "traiciono", "traicione",  # versiones sin acento (LLMs a veces omiten)
        "infiel", "infidelidad", "engaño", "engañó", "engañaba", "engañé",
        "engano", "engano", "mentira", "mintió", "mintio", "amante", "cuernos",
        "celular", "mensajes", "fotos", "la otra", "el otro", "otra mujer", "otro hombre",
        "se veían", "los vi juntos", "llevaban meses", "llevaban tiempo",
    ],
    "tristeza": [
        "llorando", "lloré", "lloró", "lloraba", "lágrimas", "llanto",
        "soledad", "solo", "sola", "perdí", "perdió", "abandono",
        "abandonó", "abandoné", "se fue", "partió", "ya no estaba",
        "vacío", "vacía", "dolor", "duele", "destrozada", "destrozado",
        "me rompió", "extraño", "echo de menos", "ya no regresó",
        "se acabó todo", "me quedé sola", "me quedé solo",
    ],
    "miedo": [
        "miedo", "terror", "aterrada", "aterrado", "asustada", "asustado",
        "escondía", "ocultaba", "amenazó", "amenaza", "peligro",
        "horrible", "pesadilla", "tenía miedo", "no podía dormir",
        "algo oscuro", "lo que guardaba", "nunca me dijo",
        "sin que yo supiera", "a mis espaldas",
    ],
    "venganza": [
        "venganza", "me vengué", "confronté", "lo expuse", "la expuse",
        "revancha", "justicia", "lo conté todo", "se lo dije en su cara",
        "no se lo esperaba", "les conté a todos", "lo publiqué",
        "le dije la verdad", "se acabó", "lo pagó caro",
        "le hice lo mismo", "se enteraron todos",
    ],
    "humillacion": [
        "humillación", "humillada", "humillado", "vergüenza", "ridícula",
        "ridículo", "burla", "se burlaron", "risa", "se rieron",
        "delante de todos", "en público", "me avergonzó", "pasé vergüenza",
        "quedé como", "me dejaron en ridículo", "todos lo vieron",
        "frente a todos",
    ],
    "secreto": [
        "secreto", "doble vida", "nadie sabía", "a mis espaldas",
        "todo ese tiempo", "todo ese tiempo lo estuvo",
        "nunca imaginé que", "identidad falsa", "nombre falso",
        "mentía sobre quién era", "ocultó su verdadera", "vivía una mentira",
        "llevaba años ocultando", "llevaba meses ocultando",
    ],
    "familia": [
        "madre", "padre", "mamá", "papá", "hermano", "hermana",
        "familia", "familiar", "tío", "tía", "abuela", "abuelo",
        "suegra", "suegro", "cuñado", "cuñada", "padrastro", "madrastra",
        "mis padres", "mi familia", "de mi propia sangre",
        "los de mi casa",
    ],
    "trabajo": [
        "jefe", "jefa", "trabajo", "empresa", "oficina", "compañero",
        "compañera", "colega", "ascenso", "despido", "despedido",
        "despedida", "me corrieron", "me robaron la idea",
        "acoso laboral", "recursos humanos", "me reportaron",
    ],
}


def _detect_emotion(text: str) -> str:
    """
    Detecta la emoción dominante de un script para seleccionar la paleta visual.

    Puntúa cada categoría base contando coincidencias de palabras clave.
    En caso de empate usa el orden de impacto visual.

    Returns:
        Categoría base de emoción (sin sufijo numérico).
    """
    text_lower = text.lower()
    scores: dict[str, int] = {e: 0 for e in _EMOTION_KEYWORDS}

    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[emotion] += 1

    # Orden de desempate por impacto visual (primero = prioridad mayor)
    PRIORITY = ["traicion", "shock", "miedo", "venganza",
                 "tristeza", "humillacion", "secreto", "familia", "trabajo"]

    best_score = max(scores.values())
    if best_score == 0:
        return "shock"

    for emotion in PRIORITY:
        if scores.get(emotion, 0) == best_score:
            logger.debug(f"Emoción detectada: '{emotion}' (scores={scores})")
            return emotion

    return "shock"


def get_subtitle_scheme(text: str) -> dict:
    """
    Detecta la emoción dominante y elige aleatoriamente entre las 5 variantes
    de esa emoción para dar variedad visual entre videos del mismo tipo.
    """
    emotion = _detect_emotion(text)
    # Recoger todas las variantes de esa emoción (base + _2 … _5)
    variants = [
        s for key, s in SUBTITLE_SCHEMES.items()
        if key == emotion or key.startswith(f"{emotion}_")
    ]
    scheme = random.choice(variants) if variants else SUBTITLE_SCHEMES.get(emotion, _SCHEMES_LIST[0])
    logger.info(f"Paleta subtítulos: '{scheme['name']}' (emoción: '{emotion}', variante aleatoria de {len(variants)})")
    return scheme


# ─── Detección automática de género del narrador ──────────────────────────────

# Adjetivos/participios en primera persona que revelan el género del narrador
_FEMALE_MARKERS = {
    # ── Participios de estado emocional ──────────────────────────────────────
    "traicionada", "engañada", "abandonada", "enamorada", "confundida",
    "devastada", "herida", "humillada", "desesperada", "decepcionada",
    "avergonzada", "embarazada", "casada", "divorciada", "asustada",
    "sorprendida", "equivocada", "cansada", "perdida", "destrozada",
    "rota", "atrapada", "ilusionada", "celosa", "lastimada",
    "usada", "manipulada", "controlada", "ignorada", "rechazada",
    "querida", "amada", "valorada", "preocupada", "angustiada",
    "desilusionada", "resignada", "enamoradísima", "obsesionada",
    "enamorada", "seducida", "acosada", "maltratada", "abusada",
    "dominada", "sometida", "silenciada", "invisibilizada", "ninguneada",
    "olvidada", "menospreciada", "subestimada", "minimizada", "culpada",
    "juzgada", "señalada", "criticada", "burlada", "ridiculizada",
    "insultada", "amenazada", "golpeada", "agredida", "violentada",
    "presionada", "forzada", "obligada", "chantajeada", "extorsionada",
    "herida", "lastimada", "dañada", "marcada", "afectada",
    "impactada", "afectada", "conmocionada", "perturbada", "traumatizada",
    "bloqueada", "paralizada", "desbordada", "agotada", "quemada",
    "destruida", "aniquilada", "aplastada", "hundida", "derrumbada",
    # ── Adjetivos de estado/situación ─────────────────────────────────────────
    "sola", "triste", "feliz", "furiosa", "enojada", "molesta",
    "harta", "desesperada", "nerviosa", "ansiosa", "deprimida",
    "vulnerable", "frágil", "fuerte", "valiente", "cobarde",
    "ingenua", "tonta", "inteligente", "lista", "astuta",
    "inocente", "culpable", "responsable", "irresponsable",
    "libre", "presa", "encadenada", "liberada", "independiente",
    "dependiente", "insegura", "segura", "confiada", "desconfiada",
    "celosa", "envidiosa", "orgullosa", "avergonzada", "humilde",
    "arrogante", "soberbia", "miedosa", "valiente", "resignada",
    # ── Sustantivos relacionales (solo femenino) ───────────────────────────────
    "novia", "esposa", "mamá", "madre", "hija", "mujer", "chica",
    "amiga", "abuela", "cuñada", "suegra", "nuera", "hermana",
    "tía", "sobrina", "prima", "madrina", "tutora", "jefa",
    "compañera", "colega", "vecina", "conocida", "enemiga",
    "rival", "amante", "querida", "concubina", "nana", "niñera",
    # ── Primera persona femenina ───────────────────────────────────────────────
    "embarazada", "viuda", "soltera", "separada", "divorciada",
    "prometida", "comprometida", "recién", "recasada",
    # ── Verbos reflexivos que revelan género ──────────────────────────────────
    "quedé", "estaba", "estuve", "sentí", "me sentía",
}

_MALE_MARKERS = {
    # ── Participios de estado emocional ──────────────────────────────────────
    "traicionado", "engañado", "abandonado", "enamorado", "confundido",
    "devastado", "herido", "humillado", "desesperado", "decepcionado",
    "avergonzado", "casado", "divorciado", "asustado", "sorprendido",
    "equivocado", "cansado", "perdido", "destrozado", "roto",
    "atrapado", "ilusionado", "celoso", "lastimado",
    "usado", "manipulado", "controlado", "ignorado", "rechazado",
    "querido", "amado", "valorado", "preocupado", "angustiado",
    "desilusionado", "resignado", "obsesionado",
    "seducido", "acosado", "maltratado", "abusado",
    "dominado", "sometido", "silenciado", "ninguneado",
    "olvidado", "menospreciado", "subestimado", "minimizado", "culpado",
    "juzgado", "señalado", "criticado", "burlado", "ridiculizado",
    "insultado", "amenazado", "golpeado", "agredido",
    "presionado", "forzado", "obligado", "chantajeado", "extorsionado",
    "dañado", "marcado", "afectado",
    "impactado", "conmocionado", "perturbado", "traumatizado",
    "bloqueado", "paralizado", "desbordado", "agotado", "quemado",
    "destruido", "aniquilado", "aplastado", "hundido", "derrumbado",
    # ── Adjetivos de estado/situación ─────────────────────────────────────────
    "solo", "triste", "feliz", "furioso", "enojado", "molesto",
    "harto", "desesperado", "nervioso", "ansioso", "deprimido",
    "vulnerable", "frágil", "fuerte", "valiente", "cobarde",
    "ingenuo", "tonto", "inteligente", "listo", "astuto",
    "inocente", "culpable", "responsable", "irresponsable",
    "libre", "preso", "encadenado", "liberado", "independiente",
    "dependiente", "inseguro", "seguro", "confiado", "desconfiado",
    "celoso", "envidioso", "orgulloso", "avergonzado", "humilde",
    "arrogante", "soberbio", "miedoso", "valiente", "resignado",
    # ── Sustantivos relacionales (solo masculino) ──────────────────────────────
    "novio", "esposo", "papá", "padre", "hijo", "hombre", "chico",
    "amigo", "abuelo", "cuñado", "suegro", "yerno", "hermano",
    "tío", "sobrino", "primo", "padrino", "tutor", "jefe",
    "compañero", "colega", "vecino", "conocido", "enemigo",
    "rival", "amante", "concubino", "marido",
    # ── Primera persona masculina ──────────────────────────────────────────────
    "viudo", "soltero", "separado", "divorciado",
    "prometido", "comprometido", "recasado",
}


def detect_narrator_gender(text: str) -> str:
    """
    Detecta el género del narrador en español.

    Estrategia de tres capas (de más a menos específica):

    1. Frases explícitas en primera persona: "soy una mujer", "mi esposo", etc.
       → Señal fuerte, peso doble.
    2. Adjetivos/participios concordados: "traicionada" vs "traicionado".
       → Cuenta frecuencia con regex (no set) para no perder "sola," "sola." etc.
    3. Desempate: femenino por defecto (la mayoría del contenido del canal
       es narrado por mujeres, alineado con la audiencia de GATA CURIOSA).

    Returns:
        "female" | "male"
    """
    import re

    text_lower = text.lower()
    # Tokenizar con regex: captura palabras sin puntuación pegada
    tokens = re.findall(r"[a-záéíóúüñ]+", text_lower)

    # ── Capa 1: frases explícitas (peso x2) ──────────────────────────────────
    _FEMALE_PHRASES = [
        "soy una mujer", "como mujer", "siendo mujer", "mi esposo", "mi marido",
        "mi novio", "quedé embarazada", "me quedé sola", "soy madre",
        "mi ex esposo", "mi ex novio", "mi ex marido",
    ]
    _MALE_PHRASES = [
        "soy un hombre", "como hombre", "siendo hombre", "mi esposa", "mi novia",
        "me quedé solo", "soy padre", "mi ex esposa", "mi ex novia",
        "mi ex mujer",
    ]
    female_score = sum(2 for p in _FEMALE_PHRASES if p in text_lower)
    male_score   = sum(2 for p in _MALE_PHRASES   if p in text_lower)

    # ── Capa 2: adjetivos/participios — frecuencia, no solo presencia ─────────
    for t in tokens:
        if t in _FEMALE_MARKERS:
            female_score += 1
        if t in _MALE_MARKERS:
            male_score += 1

    gender = "male" if male_score > female_score else "female"
    logger.info(
        f"Género narrador: {gender.upper()} "
        f"(fem={female_score}, masc={male_score})"
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


def _filter_transcript_artifacts(words: list) -> list:
    """
    Elimina artefactos de transcripción al inicio del audio.

    Whisper/stable-ts a veces transcribe el silencio o ruido inicial como
    palabras cortas ("hm", "uh", "m") o letras sueltas. Esto causa que aparezca
    una sola palabra en los subtítulos antes de que el narrador empiece a hablar.
    """
    _NOISE_WORDS = {"hmm", "mm", "hm", "uh", "ah", "eh", "oh", "um", "mhm", "m", "h"}
    filtered = []
    for word, start, dur in words:
        clean = word.strip().lower().strip(".,!?;:-\"'")
        if start < 0.35 and (len(clean) <= 1 or clean in _NOISE_WORDS):
            logger.debug(f"Artefacto filtrado: '{word}' @ {start:.2f}s")
            continue
        filtered.append((word, start, dur))
    return filtered or words  # devolver original si filtrar dejó lista vacía


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def _write_ass_file(words_data: list, output_path: Path, audio_duration: float = 0.0, scheme: dict | None = None):
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

    _scheme       = scheme or random.choice(_SCHEMES_LIST)
    base_size     = getattr(config, "SUBTITLE_FONT_SIZE", 88)
    font_size     = base_size + random.choice([-4, -2, 0, 0, 2, 4])
    margin_v_base = getattr(config, "SUBTITLE_MARGIN_V", 1000)
    margin_v      = margin_v_base + random.choice([-50, 0, 0, 0, 50, 100])
    subtitle_font = getattr(config, "SUBTITLE_FONT", "Impact")
    logger.debug(f"Subtítulos: esquema '{_scheme['name']}' | font_size={font_size} | margin_v={margin_v}")

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

    # Colores ASS: &H00BBGGRR& (AA=00 = completamente opaco) — elegidos del esquema del video
    COL_YELLOW = _scheme["active"]   # palabra activa
    COL_RED    = _scheme["tension"]  # palabras de tensión dramática
    COL_WHITE  = _scheme["context"]  # palabras de contexto del mismo grupo

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


# stable-ts / torch ELIMINADOS: importaban torch que causaba BSOD en Windows
# (driver NVIDIA crash al inicializar CUDA mientras VoiceBox usa la GPU).
# Reemplazado por faster-whisper (CTranslate2, sin PyTorch) en ambos flujos.


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
            # Forzar CPU por defecto: VoiceBox ya usa VRAM y cargar aquí en CUDA agota la memoria.
            # Para usar GPU explícitamente: WHISPER_DEVICE=cuda en .env
            device = getattr(config, "WHISPER_DEVICE", "cpu")
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(
                f"faster-whisper: cargando modelo '{model_size}' en {device} (primera vez)..."
            )
            cpu_threads = int(os.getenv("WHISPER_CPU_THREADS", "1"))
            _faster_whisper_model = WhisperModel(
                model_size, device=device, compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=1,
            )

        logger.info("faster-whisper: transcribiendo para timestamps exactos...")
        segments, _ = _faster_whisper_model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            beam_size=1,        # reducido de 5 → 1 para bajar carga de CPU (evita BSOD)
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



# ─── Backend edge-tts ─────────────────────────────────────────────────────────


def _add_dramatic_pauses(text: str) -> str:
    """
    Preprocesa el texto antes de enviarlo a edge-tts.

    Filosofía: mínima intervención. El LLM ya estructura el texto con su
    propia puntuación. Solo corregimos los "..." que generan silencio excesivo
    y aplicamos énfasis en pocas palabras clave.
    """
    import re

    text = re.sub(r'  +', ' ', text.strip())

    # ── Convertir "..." a coma (pausa corta 150ms) ───────────────────────────
    # "..." → 500ms de silencio. El narrador de drama no se calla medio segundo
    # entre frase y frase — eso quiebra el ritmo. Todo "..." se convierte en ","
    # (150ms) para mantener la urgencia sin pausas incoherentes.
    text = re.sub(r'\.{2,}\s*', ', ', text)  # cualquier "..." o ".." → coma

    # ── Énfasis en palabras clave de alto impacto (pocas = énfasis real) ─────
    CAPS_WORDS = {
        r'\bnunca\b':    'NUNCA',
        r'\bjamás\b':    'JAMÁS',
        r'\bmentira\b':  'MENTIRA',
        r'\btraición\b': 'TRAICIÓN',
    }
    for pattern, replacement in CAPS_WORDS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # ── Limpiar puntuación duplicada que pueda haber quedado ─────────────────
    text = re.sub(r',\s*,+', ',', text)
    text = re.sub(r'\.\s*\.+', '.', text)
    text = re.sub(r'  +', ' ', text)

    return text.strip()


async def _edge_tts_generate(text: str, output_mp3: Path, voice: str, rate: str = "+12%", pitch: str = "-2Hz") -> None:
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
        rate=rate,
        pitch=pitch,
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

    # Fade-in suave para evitar inicio abrupto (edge-tts no aplica ningún procesado de audio)
    import subprocess as _sp
    _fade_tmp = output_mp3.with_suffix(".fadein.mp3")
    try:
        _fr = _sp.run(
            ["ffmpeg", "-y", "-i", str(output_mp3),
             "-af", "afade=t=in:ss=0:d=0.12",
             "-c:a", "libmp3lame", "-qscale:a", "2",
             str(_fade_tmp)],
            capture_output=True, timeout=30,
        )
        if _fr.returncode == 0 and _fade_tmp.exists() and _fade_tmp.stat().st_size > 0:
            output_mp3.unlink(missing_ok=True)
            _fade_tmp.rename(output_mp3)
    except Exception as _fe:
        logger.debug(f"edge-tts fade-in omitido: {_fe}")
    finally:
        if _fade_tmp.exists():
            _fade_tmp.unlink(missing_ok=True)

    # ── Timestamps exactos con faster-whisper (CTranslate2, sin PyTorch) ────────
    # stable-ts eliminado: importaba torch que causaba BSOD en Windows.
    timed_words = _get_whisper_word_timestamps(output_mp3)

    # ── Fallback 1: WordBoundary de edge-tts (voces EN) ───────────────────────
    if not timed_words and word_events:
        timed_words = word_events
        logger.info("Usando WordBoundary de edge-tts")

    # ── Fallback 2: SentenceBoundary proporcional por caracteres (voces ES) ───
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

    # ── Fallback 3: distribución uniforme por duración real del audio ─────────
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

    # Filtrar artefactos de transcripción al inicio (ruidos transcritos como palabras sueltas)
    timed_words = _filter_transcript_artifacts(timed_words)

    # ── Post-procesar: eliminar solapamientos y garantizar duración mínima ─────
    timed_words = _fix_word_timings(timed_words)

    # Duración real del audio para que el último subtítulo no se corte
    try:
        from pydub import AudioSegment as _AS
        audio_dur = len(_AS.from_mp3(str(output_mp3))) / 1000.0
    except Exception:
        audio_dur = 0.0

    ass_path = output_mp3.with_suffix(".ass")
    _write_ass_file(timed_words, ass_path, audio_duration=audio_dur, scheme=get_subtitle_scheme(text))
    if timed_words:
        logger.info(f"ASS generado: {len(timed_words)} palabras sincronizadas")


# ─── Preprocesador para VoiceBox ─────────────────────────────────────────────

def _prepare_text_for_voicebox(text: str) -> str:
    """
    Limpia y optimiza el texto antes de enviarlo a VoiceBox.

    VoiceBox (XTTS) es sensible a:
    - Símbolos raros / emojis → generan artefactos o silencios
    - Números en dígitos → los lee como "uno dos tres" en vez de "doce"
    - Puntuación excesiva → pausas demasiado largas
    - Texto muy junto sin comas → narración sin respiración

    A diferencia de edge-tts, VoiceBox NO necesita CAPS para énfasis
    (el modelo clona la prosodia natural).
    """
    import re

    # ── 1. Eliminar emojis y símbolos no verbalizables ────────────────────────
    # \w incluye guión bajo (_) — usar clase explícita sin _ para que VoiceBox no lo lea
    text = re.sub(r"[^a-zA-Z0-9\s.,!?;:\-'\"áéíóúüñÁÉÍÓÚÜÑ]", " ", text)

    # ── 2. Normalizar números frecuentes a palabras ───────────────────────────
    _NUM_MAP = {
        r"\b1\b": "uno",   r"\b2\b": "dos",    r"\b3\b": "tres",
        r"\b4\b": "cuatro", r"\b5\b": "cinco",  r"\b6\b": "seis",
        r"\b7\b": "siete", r"\b8\b": "ocho",   r"\b9\b": "nueve",
        r"\b10\b": "diez", r"\b11\b": "once",  r"\b12\b": "doce",
        r"\b13\b": "trece", r"\b14\b": "catorce", r"\b15\b": "quince",
        r"\b20\b": "veinte", r"\b30\b": "treinta", r"\b40\b": "cuarenta",
        r"\b50\b": "cincuenta", r"\b100\b": "cien",
    }
    for pattern, word in _NUM_MAP.items():
        text = re.sub(pattern, word, text)

    # ── 3. Normalizar puntuación ──────────────────────────────────────────────
    # VoiceBox clona prosodia natural — no necesita comas forzadas extra.
    # Solo convertimos "..." a "," para evitar los 800ms de silencio que genera.
    text = re.sub(r"\.{2,}", ",", text)   # "..." / ".." → coma
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    text = re.sub(r",\s*,+", ",", text)   # doble coma → una
    text = re.sub(r"\s{2,}", " ", text)

    return text.strip()


# ─── Backend VoiceBox (voz clonada local) ────────────────────────────────────

_VOICEBOX_URL            = "http://127.0.0.1:17493"
_VOICEBOX_PROFILE_MALE   = "082af0fc-ac34-4510-af56-9fd7f6266c32"  # hombre americo
_VOICEBOX_PROFILE_FEMALE = "a4b1d4a5-2074-451b-9c71-9d95100a3c94"  # voz mujer deanira

# Filtros de limpieza por género (el hombre tiene más ruido de ventilador):
#   volume=N       — boost de ganancia antes de procesar (mujer necesita más que hombre)
#   highpass=f=80  — elimina zumbido bajo el fondo (<80Hz)
#   afftdn         — reducción de ruido FFT adaptativa
#     nf = noise floor en dB (más negativo = más agresivo)
#     nr = noise reduction 0-97 (97=máximo)
#   loudnorm I=-10 — target loudness para Shorts/TikTok (YouTube normaliza a -14 LUFS,
#                    así que -10 suena presente y con punch sin clipear)
# Si suena "metálico" o "robótico" después del filtro → subir nf (ej: nf=-20)
# Si la voz sigue suave → subir VOICE_VOLUME_FEMALE / VOICE_VOLUME_MALE en .env
_VOICEBOX_DENOISE_FEMALE = (
    "atrim=start=0.9,asetpts=PTS-STARTPTS,"  # corta 900ms — warmup '... ' + arranque de Qwen (ver warmup_text)
    "afade=t=in:ss=0:d=0.15,"               # fade-in suave para entrada limpia
    "volume={vol_f},"
    "highpass=f=80,"
    "afftdn=nf=-25:nr=70,"
    "loudnorm=I=-10:TP=-1.5:LRA=11"
)
_VOICEBOX_DENOISE_MALE = (
    "atrim=start=0.9,asetpts=PTS-STARTPTS,"  # corta 900ms — warmup '... ' + arranque de Qwen (ver warmup_text)
    "afade=t=in:ss=0:d=0.15,"
    "volume={vol_m},"
    "highpass=f=80,"
    "afftdn=nf=-30:nr=80,"
    "loudnorm=I=-10:TP=-1.5:LRA=11"
)


def _voicebox_purge_stuck_jobs(req_module) -> int:
    """Elimina jobs en estado 'generating' que quedaron del run anterior. Devuelve cantidad borrada."""
    try:
        r = req_module.get(f"{_VOICEBOX_URL}/history", timeout=10)
        if r.status_code != 200:
            return 0
        items = r.json().get("items", [])
        deleted = 0
        for item in items:
            if item.get("status") == "generating":
                job_id = item.get("id", "")
                dr = req_module.delete(f"{_VOICEBOX_URL}/history/{job_id}", timeout=10)
                if dr.status_code == 200:
                    logger.info(f"VoiceBox: job stuck eliminado ({job_id[:8]})")
                    deleted += 1
        return deleted
    except Exception as e:
        logger.debug(f"VoiceBox purge error (ignorado): {e}")
        return 0


def _generate_with_voicebox(text: str, output_path: Path, gender: str = "auto") -> str:
    """
    Genera audio con la voz clonada via VoiceBox (http://127.0.0.1:17493).
    Flujo: purge stuck → POST /generate → poll /history/{id} → GET /audio/{id} → WAV → MP3 → ASS
    """
    import requests as _req
    import subprocess
    import time as _time

    if gender == "auto":
        gender = detect_narrator_gender(text)

    profile_id   = _VOICEBOX_PROFILE_MALE if gender == "male" else _VOICEBOX_PROFILE_FEMALE
    gender_label = "MASCULINO" if gender == "male" else "FEMENINO"
    logger.info(f"VoiceBox: narrador {gender_label} | perfil {profile_id[:8]}...")

    # 0. Limpiar jobs stuck de runs anteriores para no bloquear la cola
    purged = _voicebox_purge_stuck_jobs(_req)
    if purged:
        logger.warning(f"VoiceBox: se purgaron {purged} jobs stuck — cola limpia")

    # Preprocesar texto: limpia emojis, normaliza números, agrega pausas naturales
    clean_text = _prepare_text_for_voicebox(text)
    # Prefijo de calentamiento: "... " genera una pausa silenciosa (~700-900ms) antes del
    # contenido real. El modelo Qwen no vocaliza los 3 puntos como palabras (a diferencia
    # del "." simple que a veces pronunciaba "punto" en español, dejando artefactos).
    # El atrim=0.9s corta esa pausa completa antes de llegar al texto real.
    # NO reducir a menos de 3 puntos ni bajar el trim — ver project_voicebox.md en memoria.
    warmup_text = "... " + clean_text
    logger.debug(f"VoiceBox texto preprocesado: {len(warmup_text)} chars (con warmup prefix)")

    # Velocidad: 0.92 — ligeramente más lento que defecto para narración dramática clara
    vb_speed = float(getattr(config, "VOICEBOX_SPEED", 0.92))

    # 1. Lanzar generación asíncrona
    try:
        r = _req.post(
            f"{_VOICEBOX_URL}/generate",
            json={"profile_id": profile_id, "text": warmup_text, "language": "es", "speed": vb_speed},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"VoiceBox no disponible: {e}. ¿Está corriendo en {_VOICEBOX_URL}?")

    gen_id = r.json()["id"]
    logger.info(f"VoiceBox: generando (id={gen_id[:8]}…)")

    # 2. Polling hasta completar — timeout configurable, reintentos ante reconexiones
    timeout_secs  = int(getattr(config, "VOICEBOX_TIMEOUT_SECS", 1500))
    deadline      = _time.time() + timeout_secs
    poll_errors   = 0
    poll_count    = 0
    last_status   = ""
    start_time    = _time.time()
    while _time.time() < deadline:
        _time.sleep(5)
        poll_count += 1
        elapsed = int(_time.time() - start_time)
        try:
            poll = _req.get(f"{_VOICEBOX_URL}/history/{gen_id}", timeout=20)
            poll_errors = 0
            if poll.status_code != 200:
                logger.warning(f"VoiceBox poll HTTP {poll.status_code} (t={elapsed}s) — reintentando...")
                continue
            d = poll.json()
            status = d.get("status", "")
            if status != last_status:
                logger.info(f"VoiceBox estado: '{status}' (t={elapsed}s)")
                last_status = status
            elif poll_count % 12 == 0:
                logger.info(f"VoiceBox generando... estado='{status}' t={elapsed}s — en cola, espera normal")
            if status == "completed":
                break
            if status == "error":
                raise RuntimeError(f"VoiceBox error en generación: {d.get('error', '?')}")
        except RuntimeError:
            raise
        except Exception as _pe:
            poll_errors += 1
            logger.warning(f"VoiceBox poll error #{poll_errors} (t={elapsed}s): {_pe}")
            if poll_errors >= 6:
                raise RuntimeError(
                    "VoiceBox dejó de responder. Reinicia VoiceBox y vuelve a intentar."
                )
            continue
    else:
        raise TimeoutError(
            f"VoiceBox: timeout de {timeout_secs // 60} min — servidor atascado. "
            "Reinicia VoiceBox y vuelve a intentar."
        )

    # 3. Descargar WAV
    audio_r = _req.get(f"{_VOICEBOX_URL}/audio/{gen_id}", timeout=60)
    audio_r.raise_for_status()
    wav_path = output_path.with_suffix(".wav")
    wav_path.write_bytes(audio_r.content)
    logger.info(f"VoiceBox: WAV descargado ({len(audio_r.content) // 1024} KB)")

    # 4. Limpieza de ruido de fondo (ventilador/hiss del micrófono) + WAV → MP3
    vol_f = float(getattr(config, "VOICE_VOLUME_FEMALE", 2.0))
    vol_m = float(getattr(config, "VOICE_VOLUME_MALE",   1.4))
    _filter_female = _VOICEBOX_DENOISE_FEMALE.format(vol_f=vol_f)
    _filter_male   = _VOICEBOX_DENOISE_MALE.format(vol_m=vol_m)
    denoise_filter = _filter_male if gender == "male" else _filter_female
    ffmpeg_r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-af", denoise_filter,
            "-codec:a", "libmp3lame", "-qscale:a", "2",
            str(output_path),
        ],
        capture_output=True, timeout=120,
    )
    wav_path.unlink(missing_ok=True)
    if ffmpeg_r.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"ffmpeg limpieza+MP3 falló: {ffmpeg_r.stderr.decode()[:300]}")

    # 5. Subtítulos ASS — solo faster-whisper (CTranslate2, sin PyTorch → sin BSOD).
    # stable_whisper fue eliminado de este flujo: usaba torch que competía con VoiceBox
    # por VRAM y causaba CLOCK_WATCHDOG_TIMEOUT en laptops.
    timed_words = _get_whisper_word_timestamps(output_path)
    if not timed_words:
        total_dur = get_audio_duration(output_path)
        words     = text.split()
        if words:
            w_dur = max(0.3, (total_dur - 0.5) / len(words))
            timed_words = [(w, 0.3 + i * w_dur, w_dur) for i, w in enumerate(words)]
        logger.warning("VoiceBox: usando distribución uniforme para subtítulos")

    timed_words = _filter_transcript_artifacts(timed_words)
    timed_words = _fix_word_timings(timed_words)
    audio_dur   = get_audio_duration(output_path)
    ass_path    = output_path.with_suffix(".ass")
    _write_ass_file(timed_words, ass_path, audio_duration=audio_dur, scheme=get_subtitle_scheme(text))
    logger.info(f"VoiceBox: ASS generado ({len(timed_words)} palabras sincronizadas)")

    return str(output_path)


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

    # Mezclar orden del pool para variar la voz entre videos
    pool_end = len(EDGE_VOICES_FEMALE) if gender != "male" else len(EDGE_VOICES_MALE)
    front = ordered[:pool_end]
    random.shuffle(front)
    ordered = front + ordered[pool_end:]

    # Rate positivo = más rápido = suena a persona contando un chisme, no a locutor
    # Pitch natural = voz más cercana, más real
    # Configurable via EDGE_TTS_RATE_OPTIONS y EDGE_TTS_PITCH_OPTIONS en .env
    # Default: 0-8% más rápido que neutral → ritmo natural de conversación
    # sin sonar apresurado. Ajustable desde .env: EDGE_TTS_RATE_OPTIONS=2,4,6,8,10
    _rate_opts  = [int(x) for x in os.getenv("EDGE_TTS_RATE_OPTIONS",  "0,2,4,6,8").split(",")]
    _pitch_opts = [int(x) for x in os.getenv("EDGE_TTS_PITCH_OPTIONS", "-2,0,0,0,2").split(",")]
    rate_pct = random.choice(_rate_opts)
    pitch_hz = random.choice(_pitch_opts)
    rate  = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"
    pitch = f"+{pitch_hz}Hz" if pitch_hz >= 0 else f"{pitch_hz}Hz"

    gender_label = "MASCULINO" if gender == "male" else "FEMENINO"
    logger.info(
        f"Narrador detectado: {gender_label} → voz principal: '{ordered[0]}' "
        f"| rate={rate} pitch={pitch} (fallbacks: {ordered[1:3]})"
    )

    last_error = None
    for voice in ordered:
        try:
            logger.info(f"edge-tts: probando '{voice}'...")
            # Si ya hay un event loop activo (p.ej. llamado desde growth_agent async),
            # asyncio.run() lanzaría RuntimeError. En ese caso corremos en hilo propio.
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                    _pool.submit(
                        lambda: asyncio.run(
                            _edge_tts_generate(text, output_path, voice, rate=rate, pitch=pitch)
                        )
                    ).result(timeout=120)
            except RuntimeError:
                asyncio.run(_edge_tts_generate(text, output_path, voice, rate=rate, pitch=pitch))
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


def _smooth_tts_rhythm(text: str) -> str:
    """
    Convierte puntos entre fragmentos cortos en comas para ritmo TTS fluido.

    El LLM genera frases cortas separadas por puntos siguiendo el prompt:
      "Vi el mensaje. Me quedé helada. Era ella. Dos años."
    En TTS cada '.' = 300ms de pausa → ritmo entrecortado e incoherente.

    Fragmentos de ≤4 palabras seguidos de otro fragmento se unen con ','
    (150ms) en lugar de '.' (300ms). Fragmentos más largos conservan su punto.

    "Vi el mensaje. Me quedé helada. Era ella." → "Vi el mensaje, me quedé helada, era ella."
    "Llevábamos cuatro años juntos. Fue entonces cuando lo descubrí todo." → sin cambio
    """
    import re

    # Separar SOLO en '. ' (punto + espacio) — preserva ! ? y el punto final
    parts = re.split(r'\.\s+', text)
    if len(parts) <= 1:
        return text

    ends_with_period = text.rstrip().endswith('.')
    out = []

    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue

        is_last = (i == len(parts) - 1)
        wc = len(part.split())

        if is_last:
            out.append(part)
            if ends_with_period and not part[-1:] in '.!?':
                out.append('.')
        elif wc <= 4:
            # Fragmento corto → coma, siguiente en minúscula (ya es mid-sentence)
            out.append(part)
            out.append(', ')
            nxt = parts[i + 1].strip()
            if nxt and nxt[0].isupper():
                parts[i + 1] = nxt[0].lower() + nxt[1:]
        else:
            out.append(part)
            out.append('. ')

    return ''.join(out).strip()


def _strip_llm_header(text: str) -> str:
    """
    Elimina artefactos de encabezado que el LLM inserta en el texto del guion.

    Patrones que el TTS vocaliza como ruido:
      inicio: "_ expandido"  → "guión bajo expandido"
      inicio: "Guion Expandido:" → "guion expandido"
      MEDIO:  "GUION EXPANDIDO:" entre el texto original y el expandido
              (ocurre cuando el LLM refleja la etiqueta "GUION ACTUAL:" del prompt)

    Estrategia en 4 pasos:
      1. Quitar _*# al inicio
      2. Quitar header LLM al inicio (sin exigir \\n)
      3. Quitar líneas que son SOLO un header LLM en cualquier parte del texto
         (usa MULTILINE para que ^ y $ sean límites de línea)
      4. Limpiar _*# residuales y líneas en blanco extra
    """
    import re

    # Paso 1: quitar markdown/underscore al inicio
    text = re.sub(r"^[\s*#_]+", "", text).strip()

    # Patrón compartido de palabras de encabezado LLM
    _HDR = (
        r"guion[_ ]?(?:expandido|actualizado|actual|nuevo|revisado|completo|final)?|"
        r"guión[_ ]?(?:expandido|actualizado|actual|nuevo|revisado|completo|final)?|"
        r"script[_ ]?(?:expandido|completo)?|"
        r"expandido"
    )

    # Paso 2: quitar header al inicio (sin exigir newline al final)
    text = re.sub(
        rf"(?i)^[#*_\s]*({_HDR})[\s:#*_]*",
        "", text,
    ).strip()

    # Paso 3: quitar líneas que son SOLO un header (en medio del texto)
    # (?im) → case-insensitive + MULTILINE (^ y $ por línea)
    # $ al final garantiza que no borramos líneas con contenido real
    text = re.sub(
        rf"(?im)^[#*_\s]*({_HDR})[\s:#*_]*$",
        "", text,
    )

    # Paso 4: colapsar líneas en blanco múltiples y limpiar residuos
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[\s*#_]+", "", text).strip()

    return text


def generate_audio(
    text: str, output_path: str | None = None, gender: str = "auto",
) -> str:
    """
    Convierte texto en español a audio MP3 con voz acorde al género del narrador.

    TTS_BACKEND en .env:
      - "edge"     → Microsoft Edge TTS neural (gratis, necesita internet)
      - "pyttsx3"  → Windows SAPI (offline)

    Args:
        text:          Texto en español a narrar
        output_path:   Ruta de salida .mp3. Si es None, genera en output/
        gender:        "female" | "male" | "auto"
    """
    if not text or not text.strip():
        raise ValueError("El texto TTS no puede estar vacío")

    # Eliminar encabezados LLM antes de cualquier backend (VoiceBox, edge-tts, pyttsx3)
    original_len = len(text.split())
    text = _strip_llm_header(text)
    stripped_len = len(text.split())
    if stripped_len < original_len:
        logger.info(f"Header LLM eliminado: {original_len} → {stripped_len} palabras")

    # Suavizar ritmo: fragmentos cortos (≤4 palabras) usan ',' en vez de '.'
    # Convierte "Vi el mensaje. Me quedé helada. Era ella." en flujo continuo
    text = _smooth_tts_rhythm(text)

    if output_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = config.OUTPUT_DIR / f"audio_{ts}.mp3"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    global _last_tts_backend
    backend = getattr(config, "TTS_BACKEND", "voicebox").lower()
    word_count = len(text.split())
    logger.info(
        f"Generando audio TTS ({word_count} palabras) | backend='{backend}' | gender='{gender}'"
    )

    if backend == "voicebox":
        try:
            result = _generate_with_voicebox(text, output_path, gender=gender)
            _last_tts_backend = "VoiceBox"
        except (TimeoutError, RuntimeError) as vb_err:
            logger.warning(
                f"VoiceBox falló ({vb_err.__class__.__name__}: {vb_err}) — "
                "usando edge-tts como fallback automático"
            )
            result = _generate_with_edge_tts(text, output_path, gender=gender)
            _last_tts_backend = "edge-tts (fallback)"
    elif backend == "edge":
        result = _generate_with_edge_tts(text, output_path, gender=gender)
        _last_tts_backend = "edge-tts"
    else:
        result = _generate_with_pyttsx3(text, output_path)
        _last_tts_backend = "pyttsx3"
        ass_path = output_path.with_suffix(".ass")
        if output_path.exists():
            dur = get_audio_duration(output_path)
            words = text.split()
            w_dur = dur / max(1, len(words))
            mock_words = _fix_word_timings([(w, i * w_dur, w_dur) for i, w in enumerate(words)])
            _write_ass_file(mock_words, ass_path, audio_duration=dur, scheme=get_subtitle_scheme(text))

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
