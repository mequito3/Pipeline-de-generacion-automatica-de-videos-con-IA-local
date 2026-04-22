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
OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o")

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
PEXELS_POOL_SIZE: int = int(os.getenv("PEXELS_POOL_SIZE", "15"))  # clips por keyword
PEXELS_HISTORY_LIMIT: int = int(os.getenv("PEXELS_HISTORY_LIMIT", "100"))  # clips recientes a evitar

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
# WHISPER_DEVICE: dispositivo para stable-ts / faster-whisper (subtítulos).
# "cpu" por defecto — evita conflicto de VRAM cuando VoiceBox ya carga su modelo en GPU.
# Cambia a "cuda" solo si tienes VRAM de sobra (>= 8 GB libres con VoiceBox activo).
WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
# Boost de volumen para la voz clonada (VoiceBox) antes del filtro de limpieza.
# La voz femenina clonada suena más suave de base → necesita más ganancia.
VOICE_VOLUME_FEMALE: float = float(os.getenv("VOICE_VOLUME_FEMALE", "2.0"))
VOICE_VOLUME_MALE:   float = float(os.getenv("VOICE_VOLUME_MALE",   "1.4"))

# ─── Telegram / Aprobación manual ────────────────────────────────────────────
# Si es true, el pipeline pausa y espera tu ✅/❌ en Telegram antes de publicar
TELEGRAM_APPROVAL_ENABLED: bool = (
    os.getenv("TELEGRAM_APPROVAL_ENABLED", "false").lower() == "true"
)
TELEGRAM_BOT_TOKEN: str  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str    = os.getenv("TELEGRAM_CHAT_ID", "")
# Segundos máximos esperando respuesta (default: 2 horas)
TELEGRAM_APPROVAL_TIMEOUT: int = int(os.getenv("TELEGRAM_APPROVAL_TIMEOUT", "7200"))
# Veces máximas que se regenera un video nuevo cuando se rechaza
MAX_WA_RETRIES: int = int(os.getenv("MAX_WA_RETRIES", "3"))

# ─── Canal de Confesiones (Telegram) ─────────────────────────────────────────
# ID del canal donde el bot publica confesiones (ej: @micanal o -100123456789)
# El bot debe ser admin del canal con permisos de publicación.
TELEGRAM_CHANNEL_ID: str    = os.getenv("TELEGRAM_CHANNEL_ID", "")
# Link público del canal (para incluir en CTAs de YouTube/TikTok)
TELEGRAM_CHANNEL_LINK: str  = os.getenv("TELEGRAM_CHANNEL_LINK", "")
# Precio en Stars para desbloquear contenido premium (mínimo 1, recomendado 50-100)
TELEGRAM_CHANNEL_STARS: int = int(os.getenv("TELEGRAM_CHANNEL_STARS", "50"))
# Cuántas confesiones publicar en el canal por día
TELEGRAM_CHANNEL_DAILY: int = int(os.getenv("TELEGRAM_CHANNEL_DAILY", "4"))

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

# ─── Pool amplio de hashtags del nicho ───────────────────────────────────────
# Cada video elige 10-14 al azar — nunca el mismo bloque dos veces
HASHTAG_POOL: list[str] = [
    "#confesiones", "#historiareal", "#dramareal", "#infidelidad",
    "#traicion", "#relatosdeamor", "#storytime", "#HistoriasReales",
    "#cortometraje", "#dramático", "#secretosfamiliares", "#dramacompleto",
    "#relacionestoxicas", "#engano", "#secreto", "#vidaReal",
    "#dramafamiliar", "#confesionreal", "#testimonio", "#experienciareal",
    "#relatodrama", "#momentodrama", "#laVerdad", "#revelacion",
    "#chisme", "#historiasdrama", "#amorytraicion", "#mentiras",
    "#shorts", "#viral", "#fyp", "#parati", "#tendencia",
    "#shortsespanol", "#videoviral", "#relatosverdaderos", "#nolopodiacreeer",
    "#latinoamerica", "#mexico", "#colombia", "#argentina", "#espanol",
]
# Para compatibilidad: BASE_HASHTAGS apunta al pool completo
BASE_HASHTAGS: list[str] = HASHTAG_POOL

# ─── Pie de afiliado en descripción (dejar vacío para desactivar) ─────────────
AFFILIATE_FOOTER: str = os.getenv("AFFILIATE_FOOTER", "")

# ─── Script Scorer ────────────────────────────────────────────────────────────
# Score mínimo para publicar (1-10). Si el guion queda por debajo se regenera.
SCRIPT_MIN_SCORE: int = int(os.getenv("SCRIPT_MIN_SCORE", "7"))

# ─── Growth Agent ─────────────────────────────────────────────────────────────
# Límites diarios de comentarios (conservadores = bajo riesgo de ban)
# Para canal nuevo (<1K subs) puedes subir a 10/5/4 sin riesgo real.
GROWTH_DAILY_EXTERNAL_LIMIT: int  = int(os.getenv("GROWTH_DAILY_EXTERNAL_LIMIT", "5"))
GROWTH_DAILY_OWN_LIMIT: int       = int(os.getenv("GROWTH_DAILY_OWN_LIMIT", "2"))
GROWTH_SESSION_EXTERNAL_CAP: int  = int(os.getenv("GROWTH_SESSION_EXTERNAL_CAP", "2"))
GROWTH_MIN_VIDEO_VIEWS: int       = int(os.getenv("GROWTH_MIN_VIDEO_VIEWS", "50000"))
GROWTH_ACTIVE_HOUR_START: int     = int(os.getenv("GROWTH_ACTIVE_HOUR_START", "8"))
GROWTH_ACTIVE_HOUR_END: int       = int(os.getenv("GROWTH_ACTIVE_HOUR_END",   "23"))

# ─── Analytics Agent ──────────────────────────────────────────────────────────
ANALYTICS_MAX_SNAPSHOTS: int     = int(os.getenv("ANALYTICS_MAX_SNAPSHOTS", "60"))
ANALYTICS_TOP_VIDEOS_DETAIL: int = int(os.getenv("ANALYTICS_TOP_VIDEOS_DETAIL", "3"))

# ─── Paths adicionales ────────────────────────────────────────────────────────
# Posts usados por el canal de Telegram — separado de YouTube para no desperdiciar historias
USED_POSTS_CHANNEL_FILE: Path = BASE_DIR / "used_posts_channel.json"

# ─── Subtítulos ───────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE: int = int(os.getenv("SUBTITLE_FONT_SIZE", "88"))
SUBTITLE_MARGIN_V: int = int(os.getenv("SUBTITLE_MARGIN_V", "1000"))  # px desde abajo (1000 → ~48% desde arriba en 1920px)
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
