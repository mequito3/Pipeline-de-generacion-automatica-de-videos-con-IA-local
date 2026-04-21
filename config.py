"""
config.py — Configuración central del Shorts Factory
Todos los parámetros del sistema. Sin API keys de pago.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
# CHANNEL_ENV_FILE permite al orchestrator apuntar a un .env distinto por canal
BASE_DIR = Path(__file__).parent
_env_file = os.environ.get("CHANNEL_ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(_env_file, override=True)

# ─── Groq (cloud gratuito, se usa antes de Ollama si hay API key) ────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL:   str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ─── OpenAI (fallback de emergencia cuando Groq falla) ───────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ─── Ollama (LLM local) ───────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv(
    "OLLAMA_MODEL", "llama3.2"
)  # 2GB — cabe en VRAM de RTX 500 Ada
# Modelo de fallback si el principal falla o genera JSON inválido 3 veces
# Opciones más capaces: "mistral:7b", "llama3.1:8b", "gemma2:9b"
OLLAMA_FALLBACK_MODEL: str = os.getenv("OLLAMA_FALLBACK_MODEL", "")
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))  # lee del .env

# ─── Pexels (stock videos) ───────────────────────────────────────────────────
# Regístrate gratis en https://www.pexels.com/api/ (200 req/hora, 20k/mes)
PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
USE_PEXELS: bool = True  # Siempre Pexels — Stable Diffusion eliminado

# ─── Video ────────────────────────────────────────────────────────────────────
VIDEO_WIDTH: int = 1080
VIDEO_HEIGHT: int = 1920
VIDEO_DURATION: int = 50  # 50s = sweet spot retención Shorts de confesiones (35s era muy corto para medir watch time)
FPS: int = 30
INTRO_DURATION: float = 0.0   # 0 = sin intro (recomendado Shorts: viewers hacen swipe si no ven historia en <2s)
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
# Veces máximas que se regenera un video nuevo cuando WhatsApp responde "no"
MAX_WA_RETRIES: int = int(os.getenv("MAX_WA_RETRIES", "3"))

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
VIDEOS_PER_DAY: int = int(os.getenv("VIDEOS_PER_DAY", "3"))
# Ventanas de audiencia pico (latinoamérica): almuerzo / tarde / noche
SCHEDULE_WIN1: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN1_MIN", "11")), int(os.getenv("SCHEDULE_WIN1_MAX", "13")))
SCHEDULE_WIN2: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN2_MIN", "16")), int(os.getenv("SCHEDULE_WIN2_MAX", "18")))
SCHEDULE_WIN3: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN3_MIN", "20")), int(os.getenv("SCHEDULE_WIN3_MAX", "22")))
# Retrocompatibilidad
SCHEDULE_MIN_HOUR: int = int(os.getenv("SCHEDULE_MIN_HOUR", "18"))
SCHEDULE_MAX_HOUR: int = int(os.getenv("SCHEDULE_MAX_HOUR", "22"))

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
    "Sígueme — mañana publico la historia MÁS impactante que he narrado",
    "Suscríbete ya, esta semana hay historias que te van a quitar el sueño",
    "Dale follow — tengo una historia que va a hacer estallar los comentarios",
    "Sígueme para no perderte la próxima — es PEOR que esta",
    "Suscríbete, la próxima historia involucra a toda una familia",
    "Dale like y sígueme — lo que viene te va a dejar sin palabras",
]

# ─── Hashtags permanentes del nicho (se añaden a todos los videos) ───────────
# Tienen volumen constante en búsqueda de Shorts en español latino
BASE_HASHTAGS: list[str] = [
    "#confesiones", "#historiareal", "#dramareal", "#infidelidad",
    "#traicion", "#relatosdeamor", "#storytime", "#HistoriasReales",
    "#cortometraje", "#dramático",
]

# ─── Pie de afiliado en descripción (dejar vacío para desactivar) ─────────────
AFFILIATE_FOOTER: str = os.getenv("AFFILIATE_FOOTER", "")

# ─── Subtítulos ───────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE: int = 88
SUBTITLE_POSITION_Y: float = 0.75  # 75% desde arriba (tercio inferior)
SUBTITLE_MARGIN_V: int = 800  # px desde abajo — pone el sub a ~58% desde arriba (justo bajo el centro)
SUBTITLE_FONT_COLOR: str = "white"
SUBTITLE_STROKE_COLOR: str = "black"
SUBTITLE_STROKE_WIDTH: int = 2
SUBTITLE_FONT: str = "Impact"  # estándar de Shorts virales (más impactante que Arial)

# ─── TikTok ──────────────────────────────────────────────────────────────────
TIKTOK_UPLOAD_ENABLED: bool = (
    os.getenv("TIKTOK_UPLOAD_ENABLED", "false").lower() == "true"
)
TIKTOK_USERNAME: str = os.getenv("TIKTOK_USERNAME", "")

# ─── YouTube Data API v3 (opcional) ──────────────────────────────────────────
# Para stats exactas en el Analista (likes, comentarios por video).
# Clave gratuita: console.cloud.google.com → Enable YouTube Data API v3 → API Key
# Sin esta key el analista usa scraping — funciona igual pero con menos detalle.
YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")

# ─── Analista + CEO Report ────────────────────────────────────────────────────
ANALYTICS_HOUR: int = int(os.getenv("ANALYTICS_HOUR", "9"))    # hora del reporte diario
ANALYTICS_ENABLED: bool = (
    os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"
)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
