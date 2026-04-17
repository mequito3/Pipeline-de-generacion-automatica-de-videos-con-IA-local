"""
config.py — Configuración central del Shorts Factory
Todos los parámetros del sistema. Sin API keys de pago.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# ─── Groq (cloud gratuito, se usa antes de Ollama si hay API key) ────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL:   str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ─── Ollama (LLM local) ───────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv(
    "OLLAMA_MODEL", "llama3.2"
)  # 2GB — cabe en VRAM de RTX 500 Ada
# Modelo de fallback si el principal falla o genera JSON inválido 3 veces
# Opciones más capaces: "mistral:7b", "llama3.1:8b", "gemma2:9b"
OLLAMA_FALLBACK_MODEL: str = os.getenv("OLLAMA_FALLBACK_MODEL", "")
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))  # lee del .env

# ─── Stable Diffusion (imágenes locales) ─────────────────────────────────────
SD_BACKEND: str = os.getenv("SD_BACKEND", "auto")  # "a1111" | "comfyui" | "auto"
SD_A1111_URL: str = os.getenv("SD_A1111_URL", "http://localhost:7860")
SD_COMFYUI_URL: str = os.getenv("SD_COMFYUI_URL", "http://localhost:8000")
SD_STEPS: int = int(os.getenv("SD_STEPS", "20"))  # más pasos = más calidad, más lento
SD_CFG_SCALE: float = float(os.getenv("SD_CFG_SCALE", "7.0"))
SD_SAMPLER: str = "DPM++ 2M Karras"
SD_TIMEOUT: int = int(os.getenv("SD_TIMEOUT", "180"))  # segundos máximos por imagen

# ─── Z-Image Turbo (ComfyUI) — parámetros de rendimiento ─────────────────────
# Resolución nativa de inferencia (se escala a 1080x1920 en el video)
# 832 → sweet spot calidad/velocidad en portrait  |  768 → ~5-7s  |  1024 → ~20-30s
SD_NATIVE_RES: int = int(os.getenv("SD_NATIVE_RES", "768"))
# Pasos de muestreo (4=turbo rápido, 8=calidad)
SD_TURBO_STEPS: int = int(os.getenv("SD_TURBO_STEPS", "4"))
# Máximo de imágenes únicas por video (las escenas sobrantes reciclan imágenes existentes)
SD_MAX_IMAGES: int = int(os.getenv("SD_MAX_IMAGES", "6"))
# Negative prompt universal para todas las imágenes generadas
SD_NEGATIVE_PROMPT: str = os.getenv(
    "SD_NEGATIVE_PROMPT",
    "ugly, deformed, blurry, low quality, watermark, text, logo, nsfw, "
    "cartoon, anime, drawing, painting, illustration, bad anatomy, extra limbs"
)

# ─── Video ────────────────────────────────────────────────────────────────────
VIDEO_WIDTH: int = 1080
VIDEO_HEIGHT: int = 1920
VIDEO_DURATION: int = 35  # segundos objetivo del Short (confesiones: 20–40s ideal)
FPS: int = 30
INTRO_DURATION: float = 2.5   # tiempo en pantalla de la intro (hook legible por el usuario)
OUTRO_DURATION: float = 4.0   # CTA final con pregunta de engagement

# ─── TTS (Text-to-Speech) ─────────────────────────────────────────────────────
# TTS_BACKEND:
#   "edge"    → Microsoft Edge TTS neural (gratis, alta calidad, requiere internet)
#               Voces: es-MX-DaliaNeural (mujer), es-MX-JorgeNeural (hombre)
#   "pyttsx3" → Windows SAPI (100% offline, necesita voz española en Windows)
TTS_BACKEND: str = os.getenv("TTS_BACKEND", "edge")
TTS_EDGE_VOICE: str = os.getenv("TTS_EDGE_VOICE", "es-MX-DaliaNeural")
TTS_VOICE_RATE: int = int(os.getenv("TTS_VOICE_RATE", "175"))
TTS_VOLUME: float = 1.0

# ─── WhatsApp / Aprobación manual ────────────────────────────────────────────
# Si es true, el pipeline pausa y espera tu SI/NO en WhatsApp antes de publicar
WHATSAPP_APPROVAL_ENABLED: bool = (
    os.getenv("WHATSAPP_APPROVAL_ENABLED", "false").lower() == "true"
)
TWILIO_ACCOUNT_SID: str  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str   = os.getenv("TWILIO_AUTH_TOKEN", "")
# Número de Twilio con prefijo whatsapp (sandbox: +14155238886)
TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "")
# Tu número personal con código de país (ej. +521234567890)
WHATSAPP_TO: str = os.getenv("WHATSAPP_TO", "")
# Segundos máximos esperando respuesta (default: 2 horas)
WHATSAPP_APPROVAL_TIMEOUT: int = int(os.getenv("WHATSAPP_APPROVAL_TIMEOUT", "7200"))

# ─── YouTube (Selenium) ───────────────────────────────────────────────────────
YOUTUBE_EMAIL: str = os.getenv("YOUTUBE_EMAIL", "")
YOUTUBE_PASSWORD: str = os.getenv("YOUTUBE_PASSWORD", "")
YOUTUBE_STUDIO_URL: str = "https://studio.youtube.com"
CHROME_PROFILE_DIR: str = str(BASE_DIR / "chrome_profile")
UPLOAD_MAX_RETRIES: int = 3
UPLOAD_RETRY_WAIT: int = 60  # segundos entre reintentos
YOUTUBE_UPLOAD_ENABLED: bool = (
    os.getenv("YOUTUBE_UPLOAD_ENABLED", "false").lower() == "true"
)

# ─── Scheduler ────────────────────────────────────────────────────────────────
SCHEDULE_HOURS: int = int(os.getenv("SCHEDULE_HOURS", "8"))
# Horas pico para español latino (MX/CO): 7am, 7pm, 9pm
# Si está vacío, usa intervalo fijo de SCHEDULE_HOURS
SCHEDULE_PEAK_HOURS: list = [
    int(h) for h in os.getenv("SCHEDULE_PEAK_HOURS", "7,19,21").split(",") if h.strip()
]

# ─── Paths ────────────────────────────────────────────────────────────────────
OUTPUT_DIR: Path = BASE_DIR / "output"
LOGS_DIR: Path = BASE_DIR / "logs"
ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
MUSIC_DIR: Path = ASSETS_DIR / "music"
TOPICS_INDEX_FILE: Path = BASE_DIR / "topics_index.json"
USED_POSTS_FILE: Path = BASE_DIR / "used_posts.json"

# ─── Scraper de historias (Reddit JSON API) ───────────────────────────────────
REDDIT_SUBREDDITS: list[str] = [
    "confessions",          # confesiones personales
    "TrueOffMyChest",       # "tengo que decirlo"
    "relationship_advice",  # drama de relaciones
    "tifu",                 # "hoy la cague"
    "offmychest",           # desahogos personales
    "survivinginfidelity",  # infidelidad
    "AITAH",                # "¿soy el malo aquí?"
    "cheating_stories",     # historias de infidelidad
    "amiwrong",             # ¿soy el malo?
    "entitledparents",      # dramas familiares
    "raisedbynarcissists",  # relaciones toxicas
    "NuclearRevenge",       # confrontaciones dramaticas
]
REDDIT_SORT: str = "hot"  # "hot" | "top" | "new"
REDDIT_TIME_FILTER: str = "week"  # para sort=top: hour|day|week|month|year|all
REDDIT_MIN_UPVOTES: int = 50  # upvotes minimos para considerar el post
STORY_MIN_CHARS: int = 800   # mínimo 800 chars — historias cortas no dan para 40s
STORY_MAX_CHARS: int = 5000  # máximo 5000 — historias muy largas pierden al espectador
SCRAPER_TIMEOUT: int = 15  # segundos de timeout por request

# ─── Categorías rotativas de confesiones ──────────────────────────────────────
# Cada categoría es el "tema emocional" que el LLM convierte en historia dramática
TOPICS: list[str] = [
    "Traición de pareja descubierta por accidente",
    "Secreto familiar guardado durante años",
    "El mejor amigo que me traicionó",
    "La mentira que destruyó mi relación",
    "Lo que encontré en el celular de mi pareja",
    "La confesión que nadie esperaba escuchar",
    "El momento en que descubrí la verdad",
    "Descubrí algo que no debía saber nunca",
    "La doble vida que llevaba mi pareja",
    "El error que arruinó nuestra amistad",
    "La traición del familiar más cercano",
    "Mi jefe tenía un secreto oscuro",
    "Lo que pasó cuando revisé su historial",
    "La persona que fingía ser mi amiga",
    "El vecino que escondía algo terrible",
    "Confesión: hice algo imperdonable",
    "Mi pareja me mintió durante años",
    "El regalo que ocultaba una verdad oscura",
    "Lo que encontré cuando me fui de viaje",
    "La llamada que lo cambió todo",
]

# ─── Branding del canal ───────────────────────────────────────────────────────
CHANNEL_NAME: str = os.getenv("CHANNEL_NAME", "GATA CURIOSA")
MUSIC_VOLUME: float = float(
    os.getenv("MUSIC_VOLUME", "0.13")
)  # 0.0–1.0 (13% = +30% sobre el anterior)

# ─── CTAs del outro (se elige uno al azar en cada video) ──────────────────────
CTA_COMMENTS: list[str] = [
    "Comenta tu respuesta abajo",
    "Cuéntame qué piensas en los comentarios",
    "Deja tu opinión abajo",
    "¿Qué hubieras hecho tú? Comenta",
    "Respóndeme en los comentarios",
]
CTA_FOLLOW: list[str] = [
    "Sígueme para más historias reales",
    "Suscríbete para más confesiones",
    "No te pierdas las próximas historias",
    "Dale like y sígueme para más",
    "Sígueme, hay muchas más historias así",
]

# ─── Ruta del workflow de ComfyUI (configurable por usuario) ──────────────────
COMFYUI_WORKFLOW_PATH: Path = Path(os.getenv(
    "COMFYUI_WORKFLOW_PATH",
    str(Path(__file__).parent / "assets" / "comfyui_workflow.json")
))

# ─── Subtítulos ───────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE: int = 88
SUBTITLE_POSITION_Y: float = 0.75  # 75% desde arriba (tercio inferior)
SUBTITLE_MARGIN_V: int = 280  # margen vertical desde abajo (más espacio visual)
SUBTITLE_FONT_COLOR: str = "white"
SUBTITLE_STROKE_COLOR: str = "black"
SUBTITLE_STROKE_WIDTH: int = 2
SUBTITLE_FONT: str = "Impact"  # estándar de Shorts virales (más impactante que Arial)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
