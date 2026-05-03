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

# ─── Pexels (stock videos — fuente primaria) ─────────────────────────────────
# Regístrate gratis en https://www.pexels.com/api/ (200 req/hora, 20k/mes)
PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
PEXELS_POOL_SIZE: int = int(os.getenv("PEXELS_POOL_SIZE", "15"))  # clips por keyword
PEXELS_HISTORY_LIMIT: int = int(os.getenv("PEXELS_HISTORY_LIMIT", "100"))  # clips recientes a evitar

# ─── Pixabay (stock videos — fuente secundaria, CC0, sin Content ID) ─────────
# Registrate gratis en pixabay.com/api/docs/ — hasta 100 req/min
# Se usa como fallback cuando Pexels no tiene clips frescos para una escena.
# Diversificar fuentes reduce el riesgo de flag "reused content" de YouTube.
PIXABAY_API_KEY: str = os.getenv("PIXABAY_API_KEY", "")

# ─── Video ────────────────────────────────────────────────────────────────────
VIDEO_WIDTH: int = 1080
VIDEO_HEIGHT: int = 1920
VIDEO_DURATION: int = 50  # 50s = sweet spot retención Shorts de confesiones (35s era muy corto para medir watch time)
FPS: int = 30
INTRO_DURATION: float = 0.0   # 0 = sin intro (recomendado Shorts: viewers hacen swipe si no ven historia en <2s)
OUTRO_DURATION: float = 5.5   # CTA final con pregunta de engagement

# ─── Edición avanzada ────────────────────────────────────────────────────────
# Cortes por escena: 1=un clip por escena (estilo plantilla), 2=dos clips distintos
# por escena con corte rápido entre ellos (ritmo TikTok viral). Default: 2.
CUTS_PER_SCENE: int = int(os.getenv("CUTS_PER_SCENE", "2"))
# Efectos de sonido en transiciones y momentos dramáticos.
# Los SFX se generan con ffmpeg (sin dependencias externas) y se cachean en assets/sfx/.
SFX_ENABLED: bool = os.getenv("SFX_ENABLED", "true").lower() == "true"
SFX_VOLUME: float = float(os.getenv("SFX_VOLUME", "0.30"))

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
# Timeout para VoiceBox en segundos. Si el servidor tarda más que esto, el pipeline
# hace fallback automático a edge-tts en vez de esperar 2 horas colgado.
# 1500 s = 25 min — suficiente para scripts normales en GPU; en CPU tardará más y hará fallback.
VOICEBOX_TIMEOUT_SECS: int = int(os.getenv("VOICEBOX_TIMEOUT_SECS", "1500"))
# Velocidad de narración VoiceBox (0.85=lento, 1.0=normal, 1.15=rápido).
# 0.92 da narración dramática clara sin alargar demasiado el audio.
VOICEBOX_SPEED: float = float(os.getenv("VOICEBOX_SPEED", "0.92"))
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
TELEGRAM_CHANNEL_DAILY: int = int(os.getenv("TELEGRAM_CHANNEL_DAILY", "2"))
# Horas que se conservan los mensajes del canal antes de borrarse automáticamente.
# 0 = nunca borrar. 48h = los suscriptores que revisan cada 2 días siempre ven contenido.
TELEGRAM_CHANNEL_TTL_HOURS: int = int(os.getenv("TELEGRAM_CHANNEL_TTL_HOURS", "48"))

# ─── YouTube (Selenium) ───────────────────────────────────────────────────────
YOUTUBE_EMAIL: str = os.getenv("YOUTUBE_EMAIL", "")
YOUTUBE_PASSWORD: str = os.getenv("YOUTUBE_PASSWORD", "")
YOUTUBE_STUDIO_URL: str = "https://studio.youtube.com"
CHROME_PROFILE_DIR: str        = str(BASE_DIR / "chrome_profile")         # YouTube upload / Studio
CHROME_PROFILE_GROWTH_DIR: str = str(BASE_DIR / "chrome_profile_growth")   # Growth agent (perfil separado para no bloquear upload)
UPLOAD_MAX_RETRIES: int = 3
UPLOAD_RETRY_WAIT: int = 60  # segundos entre reintentos
YOUTUBE_UPLOAD_ENABLED: bool = (
    os.getenv("YOUTUBE_UPLOAD_ENABLED", "false").lower() == "true"
)
# "public" | "private" | "unlisted"
# Con "private" el video queda oculto hasta que publisher_bot.py lo publique.
YOUTUBE_PRIVACY_STATUS: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")

# ─── Scheduler ────────────────────────────────────────────────────────────────
VIDEOS_PER_DAY: int = int(os.getenv("VIDEOS_PER_DAY", "3"))
GROWTH_ENABLED: bool = os.getenv("GROWTH_ENABLED", "false").lower() == "true"
# ↑ false = el growth agent NO abre Chrome (default). Para activarlo: GROWTH_ENABLED=true en .env
#   Requiere login manual en chrome_profile_growth antes de activar.

# Semanas de vida del canal — controla la rampa de publicación automática:
#   1-2 semanas → 1 video/día (canal nuevo, no generar alertas de spam)
#   3-4 semanas → 2 videos/día
#   5+ semanas  → VIDEOS_PER_DAY completo
# Incrementa este valor cada semana para subir gradualmente.
CHANNEL_AGE_WEEKS: int = int(os.getenv("CHANNEL_AGE_WEEKS", "1"))
# Ventanas de audiencia pico (latinoamérica): almuerzo / tarde / noche
SCHEDULE_WIN1: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN1_MIN", "11")), int(os.getenv("SCHEDULE_WIN1_MAX", "13")))
SCHEDULE_WIN2: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN2_MIN", "16")), int(os.getenv("SCHEDULE_WIN2_MAX", "18")))
SCHEDULE_WIN3: tuple[int, int] = (int(os.getenv("SCHEDULE_WIN3_MIN", "20")), int(os.getenv("SCHEDULE_WIN3_MAX", "22")))

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
    "¿Tiene razón o está equivocado? Dímelo abajo",
    "¿A ti te ha pasado algo parecido? Cuéntame",
    "¿Lo perdonarías? Debate en los comentarios",
    "Deja tu veredicto abajo 👇",
    "Dime qué opinas — quiero leer todos los comentarios",
    "¿Tú qué harías en esa situación? Abajo",
    "¿Quien tiene la culpa aquí? Vota en comentarios",
    "La respuesta que más me llega la leo en el próximo video",
    "¿Alguien más vivió algo así? Cuéntame",
    "Necesito saber tu opinión antes de contar lo que pasó después",
    "¿Justificado o no? Solo di sí o no abajo",
    "¿Se lo merecia o fue demasiado? Dímelo",
    "Comenta la parte que más te sorprendió",
    "¿Qué hubieras hecho diferente? Te leo",
    "Di en los comentarios si crees que hay más que no se contó",
]
CTA_FOLLOW: list[str] = [
    "Sígueme — mañana publico la historia MÁS impactante que he narrado",
    "Suscríbete ya, esta semana hay historias que te van a quitar el sueño",
    "Dale follow — tengo una historia que va a hacer estallar los comentarios",
    "Sígueme para no perderte la próxima — es PEOR que esta",
    "Suscríbete, la próxima historia involucra a toda una familia",
    "Dale like y sígueme — lo que viene te va a dejar sin palabras",
    "Sígueme si quieres escuchar lo que pasó después",
    "Suscríbete — la siguiente ocurrió en un lugar donde menos lo esperarías",
    "Dale follow porque lo que me mandaron ayer me dejó sin palabras",
    "Sígueme, tengo historias que llevan semanas esperando ser contadas",
    "Suscríbete — esta semana publico la que más miedo me da subir",
    "Dale follow y activa la campanita. La próxima no la puedes perder",
    "Sígueme para ver si al final se hizo justicia",
    "Suscríbete — hay una que pasó en una empresa y arruinó diez vidas",
    "Dale like y follow — lo que viene tiene un final que no te vas a esperar",
    "Sígueme. El final de la próxima historia te va a romper el alma",
    "Suscríbete ya — la semana que viene publico algo que nunca había contado",
    "Dale follow porque después de esto lo que sigue es mucho peor",
    "Sígueme — tengo una grabación de voz que me enviaron y la voy a contar",
    "Suscríbete. Lo próximo que subo cambió la vida de alguien para siempre",
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

# ─── CTAs hablados (narrados con la voz, no son texto de pantalla) ───────────
# Cortos, informales, en primera persona — suenan humanos, no de locutor.
# Actualizar aquí afecta automáticamente a script_generator y tiktok_growth_agent.
VOICED_CTAS_POOL: list[str] = [
    "Sígueme. La próxima te va a dar vergüenza ajena solo de escucharla.",
    "Dale follow porque lo que viene después de esto es peor. Mucho peor.",
    "Sígueme que tengo historias guardadas que me da miedo subir.",
    "Si esto te pareció fuerte, espérate a la próxima. Sígueme.",
    "Hay cada historia en los mensajes que me mandan... Sígueme y te cuento.",
    "Sígueme porque lo que me pasó después de esto no te lo vas a creer.",
    "Dale follow. La próxima historia involucra a alguien que no debería.",
    "Sígueme que hay una historia que llevo semanas sin atreverme a subir.",
    "Si también viviste algo así, me cuentas abajo. Y dale follow pa' la próxima.",
    "Sígueme porque tengo más y algunas dan más asco que esta.",
    "La que sigue me cuesta contarla. Sígueme si quieres escucharla.",
    "Dale follow. Lo que viene tiene de todo — traición, deseo y una decisión que arruinó todo.",
    "Sígueme que tengo una que pasó en familia. Y es lo peor que vas a escuchar hoy.",
    "Si esto te sacó de onda, espérate. Dale follow.",
    "Sígueme pa' más. Hay cada cosa que la gente hace cuando nadie los ve.",
]

# ─── Canal de Confesiones — pools de contenido ────────────────────────────────
# Todos los textos que aparecen en el canal Telegram.
# Agregar/editar aquí sin tocar el código.

CHANNEL_STORY_INTROS: list[str] = [
    "Me llegó esto a las 2am. No pude dormir después de leerlo.\n\n",
    "Alguien me mandó esto con número desconocido y me pidió que lo publicara.\n\n",
    "Llevo días decidiendo si subir esto o no. Al final, necesita ser contado.\n\n",
    "Una suscriptora me mandó esto hace una semana. Tardé en aceptar que era real.\n\n",
    "Esto me lo confesó alguien que conozco de años. Me pidió que no dijera quién.\n\n",
    "Me llegó esto sin que yo lo esperara. No pude no publicarlo.\n\n",
    "Alguien me escribió esto y me dijo: «sé que no me vas a creer». Yo sí le creí.\n\n",
    "No sé si lo que voy a publicar está bien. Pero prometí que lo haría.\n\n",
    "Recibí esto hace días. Cada vez que iba a publicarlo, me arrepentía. Hoy no.\n\n",
    "Me pidieron publicar esto de forma anónima. Les juro que es real.\n\n",
    "Esto llegó de una cuenta que ya no existe. Solo esto quedó.\n\n",
    "Tardé tres días en decidir si subir esto. Me pesaba demasiado guardarlo sola.\n\n",
    "Una persona que conozco me mandó esto con una condición: que no dijera cuándo pasó.\n\n",
    "Lo leí de un tirón y no pude publicarlo de inmediato. Necesitaba procesarlo.\n\n",
    "No me queda claro si esto es una confesión o un desahogo. Pero lo subo igual.\n\n",
    "Alguien me mandó esto y borró el mensaje antes de que pudiera responder. Ya lo tenía guardado.\n\n",
    "Esto me lo enviaron con «publicalo cuando quieras». Tardé más de lo que debería.\n\n",
    "Me llegó esto hace semanas. Estuve a punto de no publicarlo. Hoy cambié de opinión.\n\n",
    "Una suscriptora nueva me mandó esto sin presentarse. Sin nombre, sin contexto. Solo esto.\n\n",
    "Lo recibí a las 3am. La persona que lo mandó ya no está disponible.\n\n",
]

CHANNEL_STORY_CLIFF: list[str] = [
    "\n…\n\nY lo que hizo después es lo que me dejó sin palabras.\n↓ Historia completa — {stars} ⭐",
    "\n…\n\nAcá es donde todo cambia. El final está justo abajo.\n↓ {stars} ⭐ para leerlo.",
    "\n…\n\nLo que descubrió en ese momento lo cambió todo. Está guardado abajo.\n{stars} ⭐ → historia completa.",
    "\n…\n\nY entonces tomó una decisión que nadie esperaba.\n↓ Continuación — {stars} ⭐",
    "\n…\n\nEl final de esto no lo vi venir. Y dudo que vos tampoco.\n↓ {stars} ⭐ para leer el cierre.",
    "\n…\n\nHasta acá puedo contar. El resto está guardado.\n{stars} ⭐ y lo leés completo.",
    "\n…\n\nAcá paré de leer la primera vez. No pude seguir.\n↓ {stars} ⭐ si querés saber qué pasó después.",
    "\n…\n\nEsto tiene más. Mucho más. Y es peor.\n↓ {stars} ⭐ para el cierre completo.",
    "\n…\n\nEl giro que viene nadie lo esperó. Ni yo cuando lo leí.\n↓ Historia completa — {stars} ⭐",
    "\n…\n\nEl final cambia todo lo que leíste hasta acá.\n↓ {stars} ⭐ → final sin cortes.",
    "\n…\n\nAhí estaba la parte que no podía publicar antes.\n↓ {stars} ⭐ para leer el cierre.",
    "\n…\n\nPara acá llego sin Stars. Lo que sigue es otra cosa.\n↓ {stars} ⭐ y lo leés hoy.",
    "\n…\n\nEl momento en que todo explotó está justo después.\n↓ {stars} ⭐",
    "\n…\n\nY fue ahí cuando entendí que nada iba a ser igual.\n↓ Historia sin cortes — {stars} ⭐",
    "\n…\n\nHasta acá es gratis. Lo que importa de verdad está abajo.\n{stars} ⭐",
    "\n…\n\nLo que viene no lo vas a poder dejar de leer.\n↓ {stars} ⭐ → final completo.",
    "\n…\n\nTodavía no llegamos al peor momento. Ese está abajo.\n↓ {stars} ⭐",
    "\n…\n\nEsto tiene un giro que cambia la historia entera.\n↓ {stars} ⭐ para verlo.",
    "\n…\n\nY lo que pasó después fue lo último que esperaba.\n↓ {stars} ⭐",
    "\n…\n\nAcá termina la parte que puedo contar gratis.\n↓ {stars} ⭐ y leés el final.",
]

CHANNEL_ENGAGEMENT_POSTS: list[str] = [
    "¿Existe el «fue solo una vez» o eso no existe?\n\nSerio. Quiero saber qué piensan. 👇",
    "Si tu pareja te miente en algo «pequeño»... ¿en qué más te puede estar mintiendo?\n\nPregunta genuina. 💭",
    "Las personas que más te quieren son las que más te pueden destruir.\n\n¿Lo vivieron así? Cuéntenme. 👇",
    "¿Por qué cuesta más confiar en alguien nuevo que perdonar al que te lastimó?\n\nAlguien me hizo esta pregunta y no supe qué responder.",
    "Hay personas que te tratan bien cuando quieren algo y mal cuando ya lo tienen.\n\nEs lo más común y lo menos hablado. ¿Les pasó? 👇",
    "La peor traición no siempre es la infidelidad.\nA veces es que alguien que conocías de toda la vida era una persona completamente distinta.\n\n¿A ustedes cuál les dolió más? 💬",
    "¿Alguna vez se quedaron con alguien que los lastimaba porque era más fácil que empezar de cero?\n\nNo juzgo. Solo quiero saber si soy la única. 👇",
    "El que te quiere de verdad no te hace dudar.\n\n¿Cuánto tardaron en aprender eso? ¿O todavía están aprendiendo? 💭",
    "Una mentira «sin importancia» al principio de una relación. ¿La perdonás o cambia todo?\n\n👇",
    "¿Qué es peor: que te engañe con alguien que conocés o con alguien que nunca vas a conocer?\n\nMe lo preguntaron hoy y no supe qué responder.",
    "¿Cuántas veces perdonaron algo que sabían que no debían perdonar?\n\nNo es trampa. Solo curiosidad. 👇",
    "El silencio de alguien que antes te escribía todo el día. ¿Lo dejaron pasar o preguntaron? 💭",
    "¿Alguna vez se dieron cuenta de algo y prefirieron no decir nada para no escuchar la respuesta?\n\n👇",
    "La persona que más te conoce es la que más puede lastimarte.\n\n¿Tuvieron que aprender eso de la peor manera? 💬",
    "¿Confiarían en alguien que ya les mintió una vez?\n\nSin juzgar. Quiero respuestas honestas. 👇",
    "Hay relaciones que no terminan porque sean buenas. Terminan porque el miedo a estar solos es más grande.\n\n¿Les pasó? 💭",
    "¿Cuándo fue la última vez que alguien les sorprendió — para bien?\n\nQuiero saber si pasa. 👇",
    "¿Cuánto tiempo tarda una persona en mostrar quién es realmente?\n\n¿Semanas? ¿Meses? ¿Años? 💭",
    "El problema no siempre es que te engañaron. A veces el problema es que vos también lo sabías y te lo negabas.\n\n¿Me equivoco? 👇",
    "¿Alguna vez terminaron algo antes de que terminara solo para no tener que vivir el final?\n\n💬",
]

CHANNEL_VALUE_POSTS: list[str] = [
    "Hay una diferencia entre perdonar y seguir aguantando.\n\nPerdonar es para vos. Seguir aguantando es para él.\n\nNo las confundas.",
    "No es que no viste las señales.\nEs que las viste y elegiste creer que las cosas iban a cambiar.\n\nEso no te hace tonta. Te hace humana.",
    "El que te quiere de verdad no te hace dudar cada dos días.\n\nSi estás constantemente adivinando si le importás — ya tenés la respuesta.",
    "Cuando alguien te demuestra quién es, créele.\n\nLa primera vez. No la tercera.",
    "No fue que te engañó.\nFue que después de que lo hizo, seguiste dudando de vos en lugar de dudar de él.",
    "Alejarse de alguien que amás es difícil.\nQuedarte con alguien que te hace daño es más difícil todavía.\n\nElegís tu tipo de dolor.",
    "Lo que llaman «amor» a veces es solo costumbre, miedo, y no conocer otra cosa.\n\nPero no es amor.",
    "La persona correcta no te va a hacer sentir que tenés que ganarte su atención cada día.",
    "Tardé años en entender que el silencio de alguien también es una respuesta.\n\nY suele ser la más honesta.",
    "No todo lo que duele es malo. Pero no todo lo malo vale el dolor.",
    "A veces lo que más duele no es lo que te hicieron.\nEs darte cuenta de que vos hubieras hecho cualquier cosa para que no pasara.",
    "El problema con acostumbrarte a poco es que después no sabés pedir más.\n\nY empezás a creer que ese poco es lo que merecés.",
    "Hay personas que te quieren pero no saben cómo hacerlo sin hacerte daño.\n\nQuerer no siempre alcanza.",
    "Seguir con alguien «por lo que fue» es una de las formas más suaves de hacerte daño a vos misma.",
    "No siempre es que no te quisieron.\nA veces simplemente no podían darte lo que vos necesitabas.\n\nLa diferencia importa.",
    "El que te hace sentir que estás pidiendo demasiado, generalmente es el que está dando muy poco.",
    "Confiar en alguien no significa que nunca van a fallarte.\nSignifica saber que si lo hacen, lo van a decir.",
    "Hay una versión tuya que existe solo en las relaciones que te hacen bien.\nBuscala.",
    "Cuando una relación te cansa más de lo que te da, ya te está dando una respuesta.",
    "No todo se puede salvar. Y saber cuándo soltar también es una forma de quererse.",
]

CHANNEL_URGENCY_POSTS: list[str] = [
    "Las historias que no subo a YouTube están todas acá.\n\nDemasiado reales para pasar los filtros.\n\n{stars} ⭐ y las leés sin cortes.",
    "Hay contenido que nunca va a aparecer en el canal.\nDemasiado crudo. Demasiado real.\n\nEso está acá, solo para los que están en este canal. {stars} ⭐",
    "Si estás leyendo esto es porque decidiste quedarte.\n\nLas historias de abajo son las que más me piden.\n{stars} ⭐ para acceder.",
    "La diferencia entre seguir el canal y estar en el canal es esto.\n\n{stars} ⭐ y leés lo que YouTube no deja.",
    "Hay historias que escribo y no puedo publicar en ningún otro lado.\nEsta es una de esas.\n\n{stars} ⭐ → completa, sin censura.",
    "Lo que está acá no lo vas a encontrar en ningún otro lado.\n\n{stars} ⭐ y accedés a todo.",
    "YouTube corta. Telegram no.\n\nLo que YouTube no deja pasar, acá está completo.\n{stars} ⭐",
    "Esto es para los que están. No para los que pasan.\n\nSi llegaste hasta acá, sabés de qué hablo.\n{stars} ⭐",
    "No todo cabe en un Short de 60 segundos.\n\nEl resto está acá.\n{stars} ⭐ para leer lo que falta.",
    "Las historias más densas no las subo a YouTube.\nLas guardo para los que están en este canal.\n\n{stars} ⭐",
    "Hay una versión de esta historia que YouTube nunca va a ver.\n\nEsta es esa versión.\n{stars} ⭐",
    "El canal de YouTube es el trailer.\nEsto es la película completa.\n\n{stars} ⭐ → acceso total.",
    "Acá publico lo que no puedo publicar en ningún otro lado.\nSin filtros. Sin cortes.\n\n{stars} ⭐",
    "No todo lo que recibo se puede publicar en YouTube.\n\nEsto es lo que llega acá.\n{stars} ⭐",
    "Las personas que están en este canal saben que hay contenido que no llega a YouTube.\n\nEste es uno.\n{stars} ⭐",
    "Este canal existe porque YouTube tiene límites.\n\nAcá no hay límites.\n{stars} ⭐ y lo comprobás.",
    "Lo que publicaría si no hubiera restricciones.\n\nAcá lo hay.\n{stars} ⭐",
    "Para los que quieren la historia completa, sin lo que YouTube no deja.\n\n{stars} ⭐",
    "Hay historias que solo llegan acá. Esta es una.\n\n{stars} ⭐ → leerla completa.",
    "Si querés saber qué pasa cuando se saca el filtro, estás en el lugar correcto.\n\n{stars} ⭐",
]

CHANNEL_PAID_CAPTIONS: list[str] = [
    "Continuación de la historia de arriba. {stars} ⭐",
    "El resto está acá. {stars} ⭐ para leer el final.",
    "Historia completa, desde el principio hasta el cierre. {stars} ⭐",
    "Lo que no publiqué arriba. {stars} ⭐",
    "La parte que no cabe en YouTube. {stars} ⭐",
    "El final sin cortes. {stars} ⭐",
    "Todo lo que faltaba arriba. {stars} ⭐",
    "Historia sin censura. {stars} ⭐ para leerla.",
    "Acá está el cierre. {stars} ⭐",
    "El resto que no puse arriba. {stars} ⭐",
    "Versión completa. {stars} ⭐",
    "Lo que YouTube no dejaría publicar. {stars} ⭐",
    "Historia entera, sin cortes. {stars} ⭐",
    "El final que no podía contar gratis. {stars} ⭐",
    "Todo lo que la historia tenía para dar. {stars} ⭐",
    "El cierre que cambia todo. {stars} ⭐",
    "La parte que más pidieron. {stars} ⭐",
    "Completa y sin filtros. {stars} ⭐",
    "Acá está lo que faltaba. {stars} ⭐",
    "Historia sin cortes desde el principio. {stars} ⭐",
]

CHANNEL_HOOK_FALLBACKS: list[str] = [
    "Lo que hice esa noche nadie lo sabe",
    "Llevo años cargando con esto sola",
    "El nunca supo que yo también lo deseaba",
    "Esa noche tomé una decisión que cambió todo",
    "Nadie en mi vida sabe lo que voy a contar",
    "Sigo pensando en lo que pasó y no me arrepiento",
    "Fui la amante durante dos años y no me arrepiento",
    "Ese secreto me cambió para siempre",
    "Nunca debió pasar pero lo dejé pasar igual",
    "El creía que yo no sabía nada y sabía todo",
    "Lo que pasó esa tarde no se lo conté a nadie",
    "Sigo viendo su cara cada vez que cierro los ojos",
    "Duramos tres horas. Nunca fue solo eso.",
    "Ella era mi mejor amiga. El era su novio.",
    "Le mentí a todos menos a él esa noche",
    "No fui la víctima de esta historia. Fui la culpable.",
    "Diez años guardando un secreto que me pesa cada día",
    "El día que lo descubrí no lloré. Eso me asustó más.",
    "Prometí que nunca lo iba a hacer. Y lo hice igual.",
    "Me enamoré de alguien que no debía. Y no lo lamento.",
]

CHANNEL_FEMALE_NAMES: list[str] = [
    "Sofía", "Valeria", "Camila", "Andrea", "Lucía",
    "Daniela", "Renata", "Valentina", "Isabella", "Natalia",
    "Gabriela", "Fernanda", "Mariana", "Alejandra", "Catalina",
    "Paola", "Elena", "Jimena", "Verónica", "Florencia",
    "Rebeca", "Ximena", "Patricia", "Silvana", "Mónica",
]

CHANNEL_MALE_NAMES: list[str] = [
    "Diego", "Mateo", "Sebastián", "Andrés", "Carlos",
    "Tomás", "Emilio", "Nicolás", "Rodrigo", "Facundo",
    "Javier", "Miguel", "Alejandro", "Iván", "Maximiliano",
    "Fernando", "Pablo", "Cristian", "Gustavo", "Ramiro",
    "Luis", "Eduardo", "Sergio", "Mario", "Daniel",
]

CHANNEL_CITIES: list[str] = [
    "Buenos Aires", "Bogotá", "Ciudad de México",
    "Madrid", "Lima", "Santiago", "Medellín", "Montevideo",
    "Caracas", "Quito", "La Paz", "Asunción", "San José",
    "Guadalajara", "Monterrey", "Rosario", "Córdoba", "Cali",
    "Barranquilla", "Valencia", "Sevilla", "Bilbao", "Málaga",
]

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
# Logs de actividad de los agentes de crecimiento y analítica
GROWTH_LOG_FILE:       Path = BASE_DIR / "growth_log.json"
TIKTOK_GROWTH_LOG_FILE: Path = BASE_DIR / "tiktok_growth_log.json"
ANALYTICS_LOG_FILE:    Path = BASE_DIR / "analytics_log.json"
# Caché del Channel ID de YouTube (evita un request extra en cada análisis)
CHANNEL_ID_CACHE_FILE: Path = BASE_DIR / "channel_id_cache.txt"

# ─── Subtítulos ───────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE: int = int(os.getenv("SUBTITLE_FONT_SIZE", "88"))
SUBTITLE_MARGIN_V: int = int(os.getenv("SUBTITLE_MARGIN_V", "500"))
# ↑ DISTANCIA DESDE ABAJO en píxeles (video = 1920px alto):
#   MAYOR = más ARRIBA  |  MENOR = más ABAJO
#   150  → casi en el borde inferior ❌
#   350  → encima de los botones de TikTok/YT (tercio inferior)
#   500  → posición recomendada ✅  (~74% desde arriba)
#   800  → centro del video
#   1200 → tercio superior
SUBTITLE_FONT_COLOR: str = "white"
SUBTITLE_STROKE_COLOR: str = "black"
SUBTITLE_STROKE_WIDTH: int = 2
SUBTITLE_FONT: str = "Impact"  # estándar de Shorts virales (más impactante que Arial)

# ─── TikTok ──────────────────────────────────────────────────────────────────
TIKTOK_UPLOAD_ENABLED: bool = (
    os.getenv("TIKTOK_UPLOAD_ENABLED", "false").lower() == "true"
)
TIKTOK_USERNAME: str = os.getenv("TIKTOK_USERNAME", "")
TIKTOK_DAILY_COMMENT_LIMIT: int = int(os.getenv("TIKTOK_DAILY_COMMENT_LIMIT", "5"))
TIKTOK_DAILY_LIKE_LIMIT:    int = int(os.getenv("TIKTOK_DAILY_LIKE_LIMIT",    "20"))
TIKTOK_DAILY_FOLLOW_LIMIT:  int = int(os.getenv("TIKTOK_DAILY_FOLLOW_LIMIT",  "3"))

# Pool de comentarios de fallback para TikTok (se usa si el LLM no genera uno).
# Controversiales pero no ofensivos — generan respuesta sin riesgo de ban.
TIKTOK_COMMENTS_POOL: list[str] = [
    "si defiendes lo que hizo esta persona, eres parte del problema 😶",
    "hay gente que hace esto y luego duerme tranquila. eso es lo que da asco.",
    "¿nadie va a decir lo que TODOS están pensando?",
    "yo lo hubiera hecho diferente. mucho diferente. ustedes no?",
    "a mí me pasó algo PEOR que esto y nunca lo he contado",
    "comenten lo peor que les han hecho y les digo si es comparable 👇",
    "¿y si resulta que tú también eres el malo de esta historia sin saberlo?",
    "el que me mandó esto dice que todavía le habla. 💀",
    "esto pasa MÁS de lo que creen. mucho más.",
    "la parte que no conté es peor. pero esa me la guardo.",
    "digan la verdad: ¿lo hubieran perdonado o se hubieran ido sin decir nada?",
    "hay cosas que no se perdonan. punto. ¿cuál es la tuya?",
    "¿quién tiene la culpa aquí realmente? sean honestos.",
    "lo más perturbador no es lo que pasó. es lo que pasó DESPUÉS.",
    "si te parece normal lo que hizo, hay que hablar.",
    "la reacción de la gente en comentarios siempre me sorprende más que la historia.",
    "¿alguien más siente que esta historia tiene mucho más que no se dijo?",
    "esto no es raro. el problema es que nadie lo habla.",
    "yo conozco a alguien que hizo exactamente esto. exactamente.",
    "¿por qué nos cuesta tanto decir la verdad cuando duele?",
]

# Disclaimer legal que se inserta en la descripción de cada video.
VIDEO_DISCLAIMER: str = os.getenv(
    "VIDEO_DISCLAIMER",
    "⚠️ Historia basada en confesiones reales. Nombres y detalles modificados.",
)

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

# ─── Gestión del proceso ──────────────────────────────────────────────────────
PROCESS_TERM_TIMEOUT: int = int(os.getenv("PROCESS_TERM_TIMEOUT", "10"))  # segundos para kill limpio
CLEANUP_DAYS_TO_KEEP: int = int(os.getenv("CLEANUP_DAYS_TO_KEEP", "7"))   # días de runs antiguas
LOGS_MAX_FILES:       int = int(os.getenv("LOGS_MAX_FILES",       "30"))  # máximo archivos de log

# ─── Pool de personajes para reintentos del generador de guiones ──────────────
# EN INGLÉS intencionalmente — son prompts de búsqueda para la API de Pexels.
# NO traducir a español; Pexels devuelve peores resultados con prompts en español.
DIVERSE_CHARACTERS_POOL: list[dict] = [
    {"gender": "female", "description": "Hispanic woman, late 20s, dark wavy hair, white blouse"},
    {"gender": "male",   "description": "Latino man, early 30s, short dark hair, grey t-shirt"},
    {"gender": "female", "description": "Black woman, mid 30s, natural curly hair, navy dress"},
    {"gender": "male",   "description": "White man, late 20s, light brown hair, blue hoodie"},
    {"gender": "female", "description": "Asian woman, late 20s, straight black hair, red jacket"},
    {"gender": "male",   "description": "Middle Eastern man, early 30s, dark beard, white shirt"},
    {"gender": "female", "description": "White woman, early 30s, blonde straight hair, beige sweater"},
    {"gender": "male",   "description": "Black man, late 20s, short natural hair, dark jacket"},
    {"gender": "female", "description": "Asian woman, mid 30s, shoulder-length black hair, floral blouse"},
    {"gender": "male",   "description": "Hispanic man, early 30s, curly dark hair, olive green shirt"},
    {"gender": "female", "description": "Latina woman, early 20s, long black hair, denim jacket"},
    {"gender": "male",   "description": "Indian man, mid 30s, black hair, light blue button-down shirt"},
    {"gender": "female", "description": "White woman, late 30s, short auburn hair, grey blazer"},
    {"gender": "male",   "description": "Latino man, late 20s, buzz cut, checkered flannel shirt"},
    {"gender": "female", "description": "Asian woman, early 30s, black bob, professional white shirt"},
    {"gender": "male",   "description": "Middle Eastern man, late 20s, clean-shaven, navy polo shirt"},
    {"gender": "female", "description": "Black woman, early 30s, braided hair, orange hoodie"},
    {"gender": "male",   "description": "White man, mid 30s, beard, dark green jacket"},
    {"gender": "female", "description": "Hispanic woman, mid 30s, shoulder-length brown hair, floral dress"},
    {"gender": "male",   "description": "Asian man, early 30s, glasses, casual grey sweater"},
]

# ─── Pool de CTAs para la descripción de YouTube con link al canal Telegram ───
# Usar como: random.choice(TELEGRAM_CTA_POOL).format(link=channel_link)
TELEGRAM_CTA_POOL: list[str] = [
    "más historias en mi canal de telegram → {link}",
    "las que youtube me borra van a telegram → {link}",
    "historias exclusivas en telegram → {link}",
    "link al canal privado → {link}",
    "lo que no puedo subir aquí está en telegram → {link}",
    "canal de telegram con historias sin censura → {link}",
    "📲 telegram → {link}",
    "el resto está en telegram → {link}",
    "sígueme en telegram para más → {link}",
    "las más fuertes solo en telegram → {link}",
    "entra al canal de telegram → {link}",
    "historias que no caben aquí → telegram → {link}",
    "más drama en telegram → {link}",
    "únete al canal → {link}",
    "las que me dan miedo subir aquí están en telegram → {link}",
    "el canal privado → {link}",
    "todo lo que youtube censura, en telegram → {link}",
    "mis mejores historias en telegram → {link}",
    "la comunidad está en telegram → {link}",
    "sin filtros, en telegram → {link}",
]
