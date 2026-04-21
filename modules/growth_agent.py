"""
growth_agent.py — Agente de crecimiento de canal

Estrategias implementadas:
  1. Comenta en videos del nicho (confesiones/drama, 10K-500K views, últimos 7 días)
     → Comentarios generados por IA, contextuales al título del video
  2. Responde a comentarios en tus propios videos
     → El algoritmo de YouTube premia el engagement del creador
  3. Deja comentario-pregunta pineado en cada video propio nuevo

Anti-detección: mismo stack que youtube_uploader
  - nodriver (sin WebDriver)
  - Stealth JS pre-carga
  - Bezier mouse + micro-jitter
  - Delays con distribución triangular
  - Tipeo humano con errores reales
  - Ve el video un tiempo antes de comentar

Límites seguros: máx 10 comentarios externos + 5 replies propios por día
"""

import asyncio
import json
import logging
import os
import platform
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import nodriver as uc

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from modules.youtube_uploader import (
    _cleanup_chrome_profile,
    _cursor,
    _delay,
    _human_click,
    _human_type,
    _inject_stealth,
    _random_mouse_wander,
    _scroll,
    _think,
)

logger = logging.getLogger(__name__)

# ─── Límites diarios (conservadores para no activar filtros de spam) ──────────

DAILY_EXTERNAL_LIMIT = 10
DAILY_OWN_LIMIT      = 5
GROWTH_LOG_FILE      = Path(__file__).parent.parent / "growth_log.json"

# ─── Keywords para buscar videos del nicho ────────────────────────────────────

MIN_VIDEO_VIEWS = 50_000   # filtrar videos con menos de 50K vistas

# Agrupadas por categoría para rotar entre categorías y no repetir nicho
NICHE_SEARCHES = [
    # === TRAICIÓN / INFIDELIDAD — BOMBA ===
    "mi esposo me engañó con mi mejor amiga historia real",
    "me traicionó con mi hermana y me enteré así relato",
    "llevaba años engañándome con la misma persona historia",
    "encontré a mi pareja con otra en mi propia cama relato",
    "mi pareja tenía una familia secreta historia impactante",
    "me puso los cuernos con mi prima historia real",
    "lo pesqué en una mentira y se destapó todo relato",
    "me engañó 5 años y nadie me dijo nada historia",
    "descubrí la infidelidad por un mensaje de voz historia",
    "mi mejor amiga y mi novio una historia que destruyó todo",
    "me enteré que me ponía los cuernos en su funeral relato",
    "lo seguí un día y descubrí la verdad historia real",
    "tenía dos celulares y yo sin saberlo durante años relato",
    "me mandó un mensaje que era para la otra historia real",
    "vivíamos juntos y tenía otra mujer en otra ciudad relato",
    "la app de pasos me confirmó lo que ya sospechaba historia",
    "su ubicación lo delató y cambió todo relato real",
    "me traicionaron los dos al mismo tiempo historia impactante",
    # === FAMILIA TÓXICA / SECRETOS FAMILIARES ===
    "mi suegra destruyó mi matrimonio y no me arrepiento",
    "mis padres me echaron de casa por esto historia real",
    "descubrí que soy adoptado de la peor forma relato",
    "el secreto oscuro de mi familia que cambió todo historia",
    "mi hermano me robó todo y desapareció historia real",
    "mi madre intentó separarnos y casi lo logra relato",
    "la herencia que partió a mi familia en dos historia",
    "mi familia me eligió a él antes que a mí relato real",
    "el secreto que mi abuela se llevó casi a la tumba historia",
    "descubrí que tengo hermanos que nadie me contó relato",
    "mi papá tenía una segunda familia y lo descubrí así historia",
    "mi suegra le contaba todo lo mío a sus amigas relato",
    "me encerraron en casa por este secreto familiar historia",
    "la verdad sobre mi origen que me rompió en dos relato",
    "me desheredaron por casarme con quien yo quería historia",
    "mi cuñada me tendió una trampa y me costó el matrimonio relato",
    "mi familia eligió dinero sobre mí historia real dura",
    # === AMIGOS QUE TRAICIONAN ===
    "mi mejor amigo me traicionó de la peor forma historia",
    "llevábamos 10 años de amistad y lo destruyó todo relato",
    "me robó la pareja y siguió siendo mi amigo historia real",
    "le conté mi secreto y lo usó en mi contra relato",
    "era mi amiga pero me odiaba por dentro historia real",
    "fingió ser mi amiga durante años y yo sin saberlo relato",
    "me dejó sola en el peor momento de mi vida historia",
    "le presté dinero y desapareció de mi vida relato real",
    "me enteré de lo que decía de mí a mis espaldas historia",
    "mi grupo de amigos me hacía bullying y yo sin verlo relato",
    # === DECISIONES POLÉMICAS — GENERAN DEBATE ===
    "lo dejé con todo pagado y me fui sin decir nada relato",
    "lo perdoné y volvió a traicionarme historia real",
    "dejé todo por él y me abandonó al año storytime",
    "me vengué y no me arrepiento historia real relato",
    "corté a toda mi familia y no volvería atrás historia",
    "fui el malo de la historia y tenía razón relato",
    "le dije toda la verdad en su boda y no me arrepiento",
    "le conté a su esposa todo y fue lo correcto historia",
    "lo expuse públicamente y la gente se dividió relato",
    "dejé de hablarle a mi madre y no pienso volver historia",
    "me fui del país sin avisar y empecé de cero relato",
    "vendí todo lo que era nuestro sin pedirle permiso historia",
    "le devolví todo lo que me regaló y corté historia real",
    "bloqueé a toda su familia y no siento culpa relato",
    "me fui de la boda antes de decir el sí historia real",
    # === REVELACIONES IMPACTANTES — GIRO DRAMÁTICO ===
    "descubrí quién era en realidad y no lo podía creer",
    "un mensaje en su celular me cambió la vida relato",
    "la verdad que nadie me dijo durante años historia",
    "se murió y descubrí todo lo que me ocultó relato",
    "el secreto que guardó 10 años salió a la luz historia",
    "me enteré de la verdad por un desconocido en la calle",
    "encontré fotos que destruyeron todo lo que creía historia",
    "un comentario en redes me hizo descubrir la verdad relato",
    "su propia madre me contó lo que él nunca dijo historia",
    "revisé su correo por accidente y vi algo que no debía",
    "un número desconocido me mandó todo con pruebas historia",
    "la niñera me contó lo que pasaba cuando yo no estaba relato",
    "su historial de búsqueda me lo dijo todo historia real",
    "me mandaron un video anónimo que lo mostraba todo relato",
    "su ex me escribió para contarme algo urgente historia",
    # === RELACIONES TÓXICAS / CONTROL / ABUSO ===
    "salí de una relación narcisista historia real relato",
    "me manipuló durante años sin darme cuenta storytime",
    "gaslight relación tóxica historia real lo que viví",
    "mi ex me acosó meses después de dejarlo historia",
    "cómo me di cuenta que era una relación de control relato",
    "me costó años entender que era abuso emocional historia",
    "me aislaba de todos y yo creía que era amor relato",
    "me hacía sentir loca y era todo calculado historia",
    "me controlaba el teléfono los amigos y el dinero relato",
    "tardé en irme porque me había convencido que era mi culpa",
    "el día que entendí que no era amor era posesión historia",
    "me amenazó si lo dejaba y lo dejé igual relato real",
    "cómo salí de una relación donde no era libre historia",
    "me quitó la confianza en mí misma poco a poco relato",
    # === DINERO / ESTAFA / TRABAJO ===
    "mi pareja me robó todos los ahorros y se fue historia",
    "me estafaron y perdí todo lo que tenía relato real",
    "mi jefe me acosó y tuve que irme yo historia impactante",
    "me despidieron por negarme a algo ilegal relato real",
    "mi socio me traicionó y se quedó con todo historia",
    "invertí mis ahorros en algo y lo perdí todo relato",
    "me prometió dinero que nunca existió historia real",
    "trabajé gratis años para alguien que me traicionó relato",
    "me robaron la idea de negocio y la registraron a su nombre",
    "descubrí que mi pareja vaciaba mi cuenta en secreto historia",
    # === EX PAREJA / SEGUNDA OPORTUNIDAD / REGRESO ===
    "mi ex volvió después de años y lo que pasó historia",
    "lo dejé y se fue con otra y quiso volver relato",
    "me pidió una segunda oportunidad y lo que hice historia",
    "mi ex me escribió el día de mi boda relato real",
    "lo dejé pasar y me arrepiento hasta hoy historia",
    "volví con mi ex y fue el peor error de mi vida relato",
    "me di cuenta demasiado tarde que lo amaba historia real",
    "él siguió con su vida y yo no pude hacer lo mismo relato",
    # === SECRETOS DE VIDA DOBLE ===
    "llevaba una doble vida y nadie lo sabía historia real",
    "descubrí que no era quien decía ser en nada relato",
    "trabajaba en algo que me ocultó durante años historia",
    "tenía deudas que le escondió a toda su familia relato",
    "su nombre no era el real y tardé en saberlo historia",
    "mintió sobre su pasado y salió todo a la luz relato",
    "descubrí que estaba casado y tenía hijos historia real",
    "era una persona completamente diferente cuando salía relato",
    # === DRAMA REDES SOCIALES / GENERACIÓN Z ===
    "me cancelaron en redes y perdí todo relato real",
    "alguien filtró mis fotos privadas historia impactante",
    "mi pareja tenía otra cuenta secreta y lo descubrí historia",
    "me hacía ghosting pero veía todas mis historias relato",
    "lo expuse en tiktok y se volvió viral historia real",
    "me bloqueó en todo sin explicación y lo que descubrí relato",
    "encontré su perfil falso que usaba para ligar historia",
    "me stalkeo durante meses y yo sin saberlo relato",
    # === CONFESIONES ÍNTIMAS / ARREPENTIMIENTO ===
    "hice algo imperdonable y necesito contarlo historia",
    "nunca le dije la verdad y me arrepiento cada día relato",
    "guardé ese secreto 20 años y hoy lo cuento historia",
    "tomé una decisión que destruyó a alguien y fue mi culpa",
    "le fallé a la persona que más me quería historia real",
    "mentí una vez y cambió el rumbo de todo relato",
    "debí hablar antes y no lo hice historia que me pesa",
    # === FORMATO SHORTS VIRAL — BÚSQUEDAS CORTAS ===
    "confesión impactante español shorts viral",
    "historia real que te deja sin palabras español",
    "drama real latino narrado historia corta",
    "storytime dramático infidelidad español",
    "relato corto impactante traición real",
    "historia verdadera que nadie te cuenta español",
    "confesiones reales canal latino shorts",
    "historia de vida dura españa latinoamerica relato",
    "drama familiar real narrado español",
    "relato de vida impactante latino corto",
    "storytime real que te rompe el corazón español",
    "historias reales de pareja tóxica shorts",
    # === INGLÉS + ESPAÑOL — AUDIENCIA BILINGÜE ===
    "cheated with best friend true story español reacción",
    "toxic relationship storytime español real",
    "found out the truth story español latino",
    "my partner had a secret life historia real español",
    "she betrayed me worst way story reacción latina",
    "caught him lying for years storytime español",
    "left everything for him true story español",
    "narcissist relationship story español real",
    "family secret revealed true story español reacción",
    "he had another family story reacción español",
]

# ─── Sistema de personas para comentarios (fallback sin Groq) ────────────────
# 6 tipos de espectador distintos → comentarios que suenan a personas reales

_COMMENT_PERSONAS = {
    "impactado": [
        "no no no esto no puede ser real te lo juro",
        "me cayó el veinte con lo de {kw} literalmente",
        "qué traición tan grande dios mío 😶",
        "esto está al nivel de película pero es REAL",
        "bro qué fuerte nunca me lo esperaba así",
        "me dejó sin palabras de verdad",
        "eso es lo más fuerte q vi en mucho tiempo",
        "no puedo con esto q acabo de ver",
    ],
    "identificado": [
        "me pasó algo calcado y todavía duele te lo juro",
        "eso mismo viví yo y nunca lo superé del todo",
        "demasiado real esto me tocó el corazón de verdad",
        "hermano/a yo pasé por algo igual y te entiendo",
        "esto es más común de lo q la gente cree",
        "no sabía q alguien más había vivido algo así",
        "me recuerda tanto a lo q yo viví hace unos años",
    ],
    "escéptico": [
        "algo no me cuadra pero bueno puede ser",
        "¿y por qué aguantó tanto? eso no me cierra",
        "hay cosas q no encajan del todo en la historia",
        "no sé si creerle del todo pero si es real qué fuerte",
        "me cuesta creer q nadie se diera cuenta antes",
        "¿en serio nadie le dijo nada? raro eso",
    ],
    "curioso": [
        "necesito saber q pasó con {kw} después",
        "parte 2 ya esto no puede quedar así",
        "me quedé con ganas de saber el final de verdad",
        "¿volvieron a hablar o fue el final definitivo?",
        "¿y la otra persona qué dijo cuando se supo todo?",
        "quiero saber cómo terminó esto en serio",
    ],
    "opinador": [
        "desde el primer momento esa persona mostraba quien era",
        "no se perdona eso, punto",
        "el error fue perdonarla la primera vez honestamente",
        "hay cosas q no tienen vuelta y esta es una de ellas",
        "yo hubiera tomado la misma decisión sin dudar",
        "lo que hizo no tiene justificación ninguna",
        "uno tiene q saber cuándo retirarse y punto",
    ],
    "solidario": [
        "fuerza para quien vivió algo así de verdad 🙏",
        "hay cosas de las q uno no se recupera fácil",
        "lo importante es q ya salió de ahí",
        "cuánto dolor dios mío q difícil todo eso",
        "uno nunca está listo pa recibir algo así",
        "ojalá esté bien quien vivió esto de verdad",
    ],
}

# Fallback plano — por si falla todo lo anterior
_COMMENT_TEMPLATES = [t for ts in _COMMENT_PERSONAS.values() for t in ts]

# Templates para comentario pineado en tus propios videos
_PIN_TEMPLATES = [
    "¿tú qué hubieras hecho en su lugar? cuéntame abajo 👇",
    "¿team confrontar o team irse en silencio? comenta",
    "¿crees q tomó la decisión correcta? quiero leer tu opinión",
    "¿a alguien más le pasó algo así? cuéntame tu historia 👇",
    "¿perdonar o alejarse para siempre? vota en los comentarios",
    "¿qué hubieras hecho diferente desde el inicio? 👇",
    "si llegaste hasta acá eres de los míos 🙌 cuéntame qué piensas",
    "comenta lo que sentiste y sígueme pa más historias así",
    "¿perdonarías o te irías sin mirar atrás? 👇 y sígueme si quieres más",
    "dime qué piensas y dale like si te llegó la historia 🙏",
]

# Fallback para respuestas propias (solo si Groq falla)
_REPLY_TEMPLATES = [
    "gracias por compartir eso, de verdad 🙏",
    "entiendo lo q dices, estas historias tocan fibras muy profundas",
    "exacto, por eso quise compartirla. mucha gente pasa por esto sin decirlo",
    "qué buen punto, yo también lo pensé cuando la narré",
    "gracias por comentar, me alegra q la historia haya llegado",
    "así es... y lo más duro es q le pasa a más gente de lo q creemos",
    "me alegra q lo hayas sentido así, esa es la idea 🙌",
    "qué fuerte lo q compartís, ojalá estés bien",
    "gracias por eso 🙏 sígueme pa más historias reales",
]


# ─── Evaluate seguro con reintentos ──────────────────────────────────────────

async def _eval_safe(page, js: str, retries: int = 5) -> any:
    """
    Wrapper de page.evaluate() con reintentos.
    IMPORTANTE: js debe ser expresión directa o IIFE — NO arrow fn suelta.
      ✓ "document.readyState"
      ✓ "(() => { return x; })()"
      ✗ "() => x"  ← esto devuelve el objeto función, no el valor
    """
    # Esperar a que la página cargue (expresión directa, no arrow fn)
    for _ in range(10):
        try:
            ready = await page.evaluate("document.readyState")
            if ready == "complete":
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)

    # Ejecutar con reintentos
    for attempt in range(retries):
        try:
            result = await page.evaluate(js)
            if result is not None:
                return result
        except Exception as e:
            logger.debug(f"  evaluate intento {attempt + 1}/{retries}: {e}")
        await asyncio.sleep(2.0)
    return None


# ─── Growth log ───────────────────────────────────────────────────────────────

def _load_log() -> dict:
    if GROWTH_LOG_FILE.exists():
        try:
            return json.loads(GROWTH_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"commented": {}, "daily": {}}


def _save_log(log: dict) -> None:
    GROWTH_LOG_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _daily_counts(log: dict) -> tuple[int, int]:
    d = log.get("daily", {}).get(_today(), {})
    return d.get("external", 0), d.get("own", 0)


def _inc(log: dict, kind: str) -> None:
    today = _today()
    log.setdefault("daily", {}).setdefault(today, {"external": 0, "own": 0})
    log["daily"][today][kind] += 1


def _already_commented(log: dict, video_id: str) -> bool:
    entry = log.get("commented", {}).get(video_id)
    if not entry:
        return False
    try:
        days_ago = (datetime.now() - datetime.strptime(entry["date"], "%Y-%m-%d")).days
        return days_ago < 30
    except Exception:
        return False


def _mark_commented(log: dict, video_id: str, title: str) -> None:
    log.setdefault("commented", {})[video_id] = {
        "date": _today(), "title": title[:80]
    }


# ─── Generador de comentarios con Groq ────────────────────────────────────────

async def _generate_comment(video_title: str) -> str:
    """Genera comentario orgánico contextual al título usando Groq."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        return _fallback_comment(video_title)

    # Persona aleatoria → cada comentario suena a una persona distinta
    persona, descripcion, longitud = random.choice([
        ("IMPACTADO",    "reacciona con shock genuino, no puede creerlo",            "4-8 palabras, muy corto"),
        ("IMPACTADO",    "reacciona con shock genuino, no puede creerlo",            "10-16 palabras"),
        ("IDENTIFICADO", "cuenta brevísimamente que vivió algo similar",             "8-15 palabras"),
        ("IDENTIFICADO", "cuenta brevísimamente que vivió algo similar",             "12-18 palabras"),
        ("ESCÉPTICO",    "duda de algo en la historia o hace notar algo raro",       "8-14 palabras"),
        ("CURIOSO",      "pregunta qué pasó después o pide parte 2",                "5-12 palabras"),
        ("OPINADOR",     "da su opinión directa sin filtro sobre la situación",      "10-18 palabras"),
        ("SOLIDARIO",    "expresa empatía o apoyo emocional hacia el narrador",      "6-12 palabras"),
    ])
    include_cta = random.random() < 0.20
    cta_line = (
        "Puede terminar con 'te sigo' o 'sígueme' si suena natural (no forzado)."
        if include_cta else
        "NO incluyas auto-promoción."
    )
    prompt = (
        f'Eres un espectador real de YouTube latinoamericano escribiendo desde el celular.\n'
        f'Video que acabas de ver: "{video_title}"\n'
        f'TU PERSONALIDAD HOY: {persona} — {descripcion}.\n'
        f'LONGITUD EXACTA: {longitud}.\n'
        "ESTILO: habla de barrio, como WhatsApp. "
        "Usa 'q'/'pa'/'xq'/'bro'/'hermano' según suene natural. "
        "Omite punto final. Sin mayúscula al inicio si da más natural. "
        "Algún error de tipeo menor es ok (no exageres).\n"
        f"{cta_line}\n"
        "PROHIBIDO: más de 1 emoji. PROHIBIDO: mencionar el canal.\n"
        "Responde SOLO con el comentario, sin comillas ni explicación."
    )

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 50,
                    "temperature": 0.95,
                },
            )
            r.raise_for_status()
            comment = (
                r.json()["choices"][0]["message"]["content"]
                .strip().strip('"').strip("'")
            )
            if len(comment) > 180:
                comment = comment[:180].rsplit(".", 1)[0] + "."
            return comment
    except Exception as e:
        logger.debug(f"Groq comentario falló: {e}")
        return _fallback_comment(video_title)


async def _generate_reply(comment_text: str) -> str:
    """Genera respuesta contextual al comentario de un espectador."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key or not comment_text.strip():
        return random.choice(_REPLY_TEMPLATES)

    include_cta = random.random() < 0.30
    cta_line = (
        "Puedes terminar con algo como 'sígueme pa más historias así' o 'gracias por seguirme 🙏' (natural)."
        if include_cta else ""
    )
    prompt = (
        "Eres el creador de un canal de YouTube de confesiones y dramas reales en español latino.\n"
        f'Un espectador comentó en tu video: "{comment_text}"\n'
        "Escribe UNA respuesta corta, cálida y auténtica (máx 20 palabras).\n"
        "REGLAS: responde DIRECTAMENTE a lo que dijo — no ignores su mensaje. "
        "Estilo casual de barrio, como WhatsApp, sin punto final. "
        "Puedes usar 'q', 'xq', 'pa', etc. Sin mayúsculas si suena más natural. "
        f"{cta_line}\n"
        "PROHIBIDO: repetir su comentario textual. PROHIBIDO: más de 1 emoji.\n"
        "Responde SOLO con el texto de la respuesta, sin comillas."
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.95,
                },
            )
            r.raise_for_status()
            reply = (
                r.json()["choices"][0]["message"]["content"]
                .strip().strip('"').strip("'")
            )
            if len(reply) > 200:
                reply = reply[:200].rsplit(" ", 1)[0]
            return reply
    except Exception as e:
        logger.debug(f"Groq reply falló: {e}")
        return random.choice(_REPLY_TEMPLATES)


def _parse_yt_duration(text: str) -> int:
    """Convierte '1:23' o '12:34' o '1:23:45' a segundos."""
    parts = text.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


def _fallback_comment(video_title: str) -> str:
    persona = random.choice(list(_COMMENT_PERSONAS.keys()))
    template = random.choice(_COMMENT_PERSONAS[persona])
    words = [w for w in video_title.split() if len(w) > 4 and not w.startswith("#")]
    kw = random.choice(words).lower() if words else "esa parte"
    comment = template.replace("{kw}", kw)
    endings = ["", "...", " en serio", " de verdad", " pa siempre", " q fuerte"]
    return comment + random.choice(endings)


# ─── Helpers de filtrado ──────────────────────────────────────────────────────

def _parse_views(text: str) -> int:
    """Convierte '1,5 M de vistas' / '345 mil' / '45K' a entero."""
    if not text:
        return 0
    t = text.lower().replace("\xa0", " ").replace(",", ".").strip()
    m = re.search(r"([\d.]+)\s*m(?:ill|de|\b)", t)
    if m:
        try: return int(float(m.group(1)) * 1_000_000)
        except: pass
    m = re.search(r"([\d.]+)\s*(?:mil\b|k\b)", t)
    if m:
        try: return int(float(m.group(1)) * 1_000)
        except: pass
    m = re.search(r"[\d]+", t.replace(".", ""))
    if m:
        try: return int(m.group())
        except: pass
    return 0


# ─── Buscar videos del nicho ──────────────────────────────────────────────────

async def _search_niche_videos(browser, keyword: str, log: dict) -> list[dict]:
    """Busca en YouTube y retorna videos del nicho con buen volumen no comentados aún."""
    # Variar el filtro: semana (viral reciente), mes, o más vistos — no siempre lo mismo
    search_filter = random.choice([
        "&sp=EgIQAQ%3D%3D",  # esta semana (viral reciente)
        "&sp=EgIQAQ%3D%3D",  # esta semana (peso doble — priorizamos frescura)
        "&sp=EgIQAg%3D%3D",  # este mes
        "&sp=CAM%3D",         # más vistos (evergreen)
    ])
    search_url = (
        "https://www.youtube.com/results?search_query="
        + keyword.replace(" ", "+")
        + search_filter
    )
    try:
        page = await browser.get(search_url)
        await _delay(3.0, 6.0)
        await _dismiss_consent(page)
        await _scroll(page, random.randint(200, 500))
        await _random_mouse_wander(page)
        await _delay(1.5, 3.0)

        videos_json = await _eval_safe(page, """(function() {
            var results = [];
            var renderers = document.querySelectorAll('ytd-video-renderer');
            for (var i = 0; i < renderers.length; i++) {
                try {
                    var r   = renderers[i];
                    var a   = r.querySelector('a#video-title');
                    if (!a) continue;
                    var title = (a.getAttribute('title') || a.innerText || '').trim();
                    var href  = a.href || '';
                    var m = href.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
                    if (!m || !title || title.length < 4) continue;
                    var spans    = r.querySelectorAll('#metadata-line span');
                    var viewText = spans.length > 0 ? (spans[0].innerText || '') : '';
                    var dateText = spans.length > 1 ? (spans[1].innerText || '') : '';
                    results.push({
                        id: m[1],
                        title: title,
                        url: 'https://www.youtube.com/watch?v=' + m[1],
                        views: viewText,
                        date: dateText
                    });
                } catch(e) {}
            }
            return JSON.stringify(results.slice(0, 30));
        })()""")

        try:
            videos_raw = json.loads(videos_json) if videos_json else []
        except Exception:
            videos_raw = []

        channel_name = getattr(config, "CHANNEL_NAME", "").lower()
        filtered = []
        for v in videos_raw:
            if not isinstance(v, dict):
                continue
            vid_id = v.get("id", "")
            title  = v.get("title", "")
            if not vid_id or not title:
                continue
            if channel_name and channel_name in title.lower():
                continue
            if _already_commented(log, vid_id):
                continue
            views = _parse_views(v.get("views", ""))
            if views > 0 and views < MIN_VIDEO_VIEWS:
                continue  # ignorar videos con pocas vistas
            filtered.append(v)

        logger.info(
            f"  '{keyword[:45]}': {len(videos_raw)} brutos → "
            f"{len(filtered)} con ≥{MIN_VIDEO_VIEWS//1000}K vistas"
        )
        return filtered[:6]

    except Exception as e:
        logger.warning(f"  Búsqueda fallida '{keyword}': {e}")
        return []


# ─── Comentar en video ajeno ──────────────────────────────────────────────────

async def _comment_on_video(browser, video: dict) -> bool:
    """Navega al video, lo 've' un rato, y deja un comentario humano."""
    try:
        logger.info(f"  [{video['title'][:55]}]")
        page = await browser.get(video["url"])
        await _delay(4.0, 8.0)

        # Ver el video — tiempo proporcional a la duración real
        await _scroll(page, random.randint(100, 250))
        await _random_mouse_wander(page)

        # Leer duración del video del player
        dur_text = await _eval_safe(page, """(function() {
            var t = document.querySelector('.ytp-time-duration');
            return t ? (t.innerText || '') : '';
        })()""") or ""
        duration_s = _parse_yt_duration(dur_text)

        if duration_s > 0:
            if duration_s <= 63:          # Short (hasta ~1min)
                ratio = random.triangular(0.40, 0.80, 0.60)
            elif duration_s <= 300:       # Video corto (1-5 min)
                ratio = random.triangular(0.20, 0.45, 0.30)
            else:                         # Video largo (>5 min)
                ratio = random.triangular(0.08, 0.25, 0.15)
            watch_secs = max(8.0, min(duration_s * ratio, 270.0))
        else:
            watch_secs = random.triangular(14.0, 35.0, 22.0)

        logger.debug(f"  Viendo {watch_secs:.0f}s (duración: {dur_text or '?'})")
        await asyncio.sleep(watch_secs)

        # Scroll hacia los comentarios
        await _scroll(page, random.randint(300, 500))
        await _delay(2.0, 4.0)
        await _random_mouse_wander(page)

        # Caja de comentario
        comment_box = None
        for sel in [
            "#placeholder-area",
            "[aria-label='Agregar un comentario...']",
            "[aria-label='Add a comment...']",
        ]:
            try:
                comment_box = await page.select(sel, timeout=8)
                if comment_box:
                    break
            except Exception:
                pass

        if not comment_box:
            logger.warning("  Caja de comentarios no encontrada")
            return False

        await _human_click(page, comment_box)
        await _delay(1.5, 3.0)

        # Caja activa (contenteditable)
        active_box = None
        for sel in ["#contenteditable-root", "[contenteditable='true']"]:
            try:
                active_box = await page.select(sel, timeout=5)
                if active_box:
                    break
            except Exception:
                pass

        comment = await _generate_comment(video["title"])
        logger.info(f"  Comentario: {comment[:70]}")

        await _human_type(active_box or comment_box, comment, clear_first=False)
        await _delay(1.5, 3.5)
        await _random_mouse_wander(page)
        await _think()

        # Submit
        submitted = False
        for sel in [
            "#submit-button button",
            "button[aria-label='Comentar']",
            "button[aria-label='Comment']",
            "ytd-comment-simplebox-renderer #submit-button",
        ]:
            try:
                btn = await page.select(sel, timeout=5)
                if btn:
                    await _human_click(page, btn)
                    await _delay(3.0, 6.0)
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            logger.warning("  Botón submit no encontrado")
            return False

        log = _load_log()
        _mark_commented(log, video["id"], video["title"])
        _inc(log, "external")
        _save_log(log)
        logger.info("  ✓ Comentario publicado")
        return True

    except Exception as e:
        logger.warning(f"  Error comentando: {e}")
        return False


# ─── Obtener channel ID ───────────────────────────────────────────────────────

async def _get_channel_id(browser) -> str | None:
    """
    Extrae el channel ID (UCxxx) del canal logueado.
    Navega a studio.youtube.com y prueba 5 métodos distintos.
    """
    try:
        page = await browser.get("https://studio.youtube.com")
        await _delay(5.0, 8.0)
        await _random_mouse_wander(page)

        # Verificar URL actual — expresión directa (no arrow fn)
        current_url = await _eval_safe(page, "window.location.href") or ""
        logger.info(f"  Studio URL: {current_url[:90]}")

        if "accounts.google.com" in current_url or "signin" in current_url.lower():
            logger.warning(
                "  Sesión no activa. FIX: Abre Chrome con este perfil, loguea en\n"
                f"  studio.youtube.com y cierra Chrome:\n"
                f"  \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" "
                f"--user-data-dir=\"{config.CHROME_PROFILE_DIR}\""
            )
            return None

        # IIFE para extraer channel ID — 5 métodos en cascada
        channel_id = await _eval_safe(page, """(function() {
            var UC = /UC[A-Za-z0-9_-]{20,}/;
            try {
                if (typeof ytcfg !== 'undefined') {
                    var v = (ytcfg.data_ && ytcfg.data_.CHANNEL_ID) || (ytcfg.get && ytcfg.get('CHANNEL_ID'));
                    if (v && UC.test(v)) return v;
                }
                var html = document.documentElement.innerHTML;
                var m;
                m = html.match(/"CHANNEL_ID":"(UC[A-Za-z0-9_-]{20,})"/);  if (m) return m[1];
                m = html.match(/"externalId":"(UC[A-Za-z0-9_-]{20,})"/);  if (m) return m[1];
                m = html.match(/"channelId":"(UC[A-Za-z0-9_-]{20,})"/);   if (m) return m[1];
                m = html.match(/channel\/(UC[A-Za-z0-9_-]{20,})/);        if (m) return m[1];
            } catch(e) {}
            return null;
        })()""")

        if channel_id:
            logger.info(f"  Channel ID: {channel_id}")
            return str(channel_id)

        # Si nada funcionó, loguear la URL para debug
        logger.warning(f"  No se pudo extraer channel ID. URL: {current_url[:90]}")
        return None

    except Exception as e:
        logger.warning(f"  Error en _get_channel_id: {e}", exc_info=True)
        return None


# ─── Engagement en canal propio ───────────────────────────────────────────────

async def _engage_own_channel(browser, log: dict, own_video_url: str = "") -> int:
    """Pinea pregunta + responde comentarios en el video más reciente del canal. Retorna replies hechos."""
    own_done = 0
    try:
        if own_video_url:
            # URL directa del video recién subido — más fiable que scraping del canal
            import re as _re
            m = _re.search(r"[?&]v=([a-zA-Z0-9_-]{11})|/shorts/([a-zA-Z0-9_-]{11})", own_video_url)
            if m:
                vid_id = m.group(1) or m.group(2)
                video_url = f"https://www.youtube.com/watch?v={vid_id}"
                logger.info(f"  Video objetivo (URL directa): {video_url}")
            else:
                video_url = own_video_url
                logger.info(f"  Video objetivo (URL directa): {video_url}")
        else:
            # Fallback: obtener channel ID y scraping del canal
            channel_id = await _get_channel_id(browser)
            if not channel_id:
                logger.warning("  No se pudo obtener el channel ID — ¿sesión iniciada?")
                return 0
            logger.info(f"  Channel ID: {channel_id}")

            page = await browser.get(
                f"https://www.youtube.com/channel/{channel_id}/videos"
            )
            await _delay(5.0, 9.0)
            await _scroll(page, random.randint(100, 300))
            await _delay(2.0, 4.0)

            vid_id = None
            for attempt in range(4):
                result_json = await page.evaluate("""(function() {
                    var el, m;
                    el = document.querySelector('a[href*="/shorts/"]');
                    if (el && el.href) { m = el.href.match(/\\/shorts\\/([a-zA-Z0-9_-]{11})/); if (m) return JSON.stringify({id: m[1], short: true}); }
                    el = document.querySelector('a#video-title-link[href*="watch"], a#video-title[href*="watch"], a[href*="watch?v="]');
                    if (el && el.href) { m = el.href.match(/[?&]v=([a-zA-Z0-9_-]{11})/); if (m) return JSON.stringify({id: m[1], short: false}); }
                    return null;
                })()""")
                try:
                    parsed = json.loads(result_json) if result_json else None
                    if parsed:
                        vid_id = parsed["id"]
                        break
                except Exception:
                    pass
                logger.debug(f"  Intento {attempt + 1}/4 esperando hidratación...")
                await asyncio.sleep(4.0)

            if not vid_id:
                logger.warning("  No se encontró ningún video en el canal público")
                return 0

            video_url = f"https://www.youtube.com/watch?v={vid_id}"
            logger.info(f"  Video objetivo (canal scraping): {video_url}")

        # Navegar al video y verificar que esté disponible
        page = await browser.get(video_url)
        await _delay(4.0, 7.0)

        page_title = await page.evaluate("document.title || ''")
        unavailable_signals = ["unavailable", "not available", "eliminado", "no disponible", "private"]
        if any(s in (page_title or "").lower() for s in unavailable_signals):
            logger.warning(f"  Video {vid_id} no disponible ({page_title}) — saltando")
            return

        await _scroll(page, random.randint(150, 300))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.0)

        # 1. Comentario-pregunta para engagement
        pinned = await _leave_pin_comment(page)
        if pinned:
            own_done += 1
            _inc(log, "own")
            _save_log(log)
        await _delay(4.0, 8.0)

        # 2. Responder a comentarios existentes
        _, own_count = _daily_counts(log)
        if own_count < DAILY_OWN_LIMIT:
            own_done += await _reply_to_top_comments(page, log)

    except Exception as e:
        logger.warning(f"  Error en engagement propio: {e}")
    return own_done


async def _leave_pin_comment(page) -> bool:
    """Publica el comentario-pregunta en el video actual. Retorna True si lo publicó."""
    try:
        comment_box = None
        for sel in [
            "#placeholder-area",
            "[aria-label='Agregar un comentario...']",
            "[aria-label='Add a comment...']",
        ]:
            try:
                comment_box = await page.select(sel, timeout=8)
                if comment_box:
                    break
            except Exception:
                pass

        if not comment_box:
            logger.warning("  _leave_pin_comment: caja de comentario no encontrada")
            return False

        await _human_click(page, comment_box)
        await _delay(1.5, 3.0)

        active_box = None
        for sel in ["#contenteditable-root", "[contenteditable='true']"]:
            try:
                active_box = await page.select(sel, timeout=5)
                if active_box:
                    break
            except Exception:
                pass

        pin_text = random.choice(_PIN_TEMPLATES)
        await _human_type(active_box or comment_box, pin_text, clear_first=False)
        await _delay(1.5, 3.0)

        for sel in [
            "#submit-button button",
            "button[aria-label='Comentar']",
            "button[aria-label='Comment']",
        ]:
            try:
                btn = await page.select(sel, timeout=5)
                if btn:
                    await _human_click(page, btn)
                    await _delay(3.0, 5.0)
                    logger.info(f"  ✓ Comentario pineado: {pin_text}")
                    return True
            except Exception:
                pass

        logger.warning("  _leave_pin_comment: botón submit no encontrado")
        return False

    except Exception as e:
        logger.debug(f"  Error pineando comentario: {e}")
    return False


async def _reply_to_top_comments(page, log: dict) -> int:
    """Responde a los primeros comentarios del video actual con replies contextuales."""
    try:
        # Scroll para cargar comentarios
        await _scroll(page, random.randint(500, 800))
        await _delay(3.0, 5.0)

        # Extraer textos de comentarios — necesarios para generar replies contextuales
        comments_json = await page.evaluate("""(function() {
            var texts = [];
            var els = document.querySelectorAll('ytd-comment-thread-renderer #content-text');
            for (var i = 0; i < Math.min(els.length, 5); i++) {
                var t = (els[i].innerText || els[i].textContent || '').trim();
                texts.push(t.slice(0, 250));
            }
            return JSON.stringify(texts);
        })()""")
        try:
            comment_texts = json.loads(comments_json) if comments_json else []
        except Exception:
            comment_texts = []

        reply_btns = []
        for sel in ["[aria-label='Responder']", "[aria-label='Reply']"]:
            try:
                reply_btns = await page.select_all(sel, timeout=8)
                if reply_btns:
                    break
            except Exception:
                pass

        if not reply_btns:
            logger.debug("  No se encontraron botones de respuesta")
            return 0

        replied = 0
        for i, btn in enumerate(reply_btns[:3]):
            _, own_count = _daily_counts(log)
            if own_count >= DAILY_OWN_LIMIT:
                break

            try:
                comment_text = comment_texts[i] if i < len(comment_texts) else ""
                logger.debug(f"  Comentario a responder: {comment_text[:60]}")

                # Generar reply contextual con Groq
                reply_text = await _generate_reply(comment_text)

                await _human_click(page, btn)
                await _delay(1.5, 3.0)

                reply_box = None
                for sel in ["#contenteditable-root", "[contenteditable='true']"]:
                    try:
                        reply_box = await page.select(sel, timeout=5)
                        if reply_box:
                            break
                    except Exception:
                        pass

                if not reply_box:
                    continue

                await _human_type(reply_box, reply_text, clear_first=False)
                await _delay(1.5, 3.0)

                for sel in [
                    "#submit-button button",
                    "button[aria-label='Responder']",
                    "button[aria-label='Reply']",
                ]:
                    try:
                        sub = await page.select(sel, timeout=5)
                        if sub:
                            await _human_click(page, sub)
                            await _delay(3.0, 5.0)
                            _inc(log, "own")
                            _save_log(log)
                            replied += 1
                            logger.info(f"  ✓ Reply {replied}: {reply_text[:50]}")
                            break
                    except Exception:
                        pass

                # Pausa larga entre replies — humanos no responden en ráfaga
                await _delay(20.0, 45.0)

            except Exception as e:
                logger.debug(f"  Error en reply: {e}")

        return replied

    except Exception as e:
        logger.debug(f"  Error buscando comentarios: {e}")
    return 0


# ─── Sesión principal ─────────────────────────────────────────────────────────

async def _browse_casually(browser) -> None:
    """
    Simula navegación orgánica entre bloques de comentarios.
    Va al homepage o trending, scrollea y mira videos sin comentar.
    Rompe el patrón lineal comment→comment→comment.
    """
    try:
        destinations = [
            "https://www.youtube.com",
            "https://www.youtube.com/?bp=6gQJRkVleHBsb3Jl",  # trending/explore
        ]
        page = await browser.get(random.choice(destinations))
        await _delay(3.0, 6.0)
        await _dismiss_consent(page)

        # Scroll orgánico por el feed
        for _ in range(random.randint(2, 4)):
            await _scroll(page, random.randint(200, 500))
            await _delay(4.0, 12.0)
            await _random_mouse_wander(page)

        # 40% de probabilidad: clic en un video y observar sin comentar
        if random.random() < 0.40:
            vid_link = None
            for sel in ["a#video-title-link", "a#thumbnail"]:
                try:
                    candidates = await page.select_all(sel, timeout=5)
                    if candidates:
                        vid_link = random.choice(candidates[:8])
                        break
                except Exception:
                    pass

            if vid_link:
                await _human_click(page, vid_link)
                await _delay(2.0, 5.0)
                # Mira 20-60s sin interactuar
                await asyncio.sleep(random.triangular(20.0, 60.0, 35.0))
                await _random_mouse_wander(page)

        break_secs = random.triangular(90.0, 240.0, 150.0)
        logger.debug(f"  Micro-break browsing {break_secs:.0f}s")
        await asyncio.sleep(break_secs)

    except Exception as e:
        logger.debug(f"  _browse_casually: {e}")
        await asyncio.sleep(random.uniform(60.0, 120.0))


async def _dismiss_consent(page) -> None:
    """Descarta el dialog de consentimiento de cookies de Google si aparece."""
    try:
        for text in ["Aceptar todo", "Accept all", "Reject all", "Rechazar todo"]:
            try:
                btn = await page.find(text, timeout=2)
                if btn:
                    await _human_click(page, btn)
                    await asyncio.sleep(1.5)
                    logger.debug("Consent dialog descartado")
                    return
            except Exception:
                pass
    except Exception:
        pass


async def _growth_session_async(do_own: bool = True, own_video_url: str = "") -> dict:
    log = _load_log()
    ext_count, own_count = _daily_counts(log)
    results = {"external": 0, "own": 0, "skipped": 0}

    if ext_count >= DAILY_EXTERNAL_LIMIT and own_count >= DAILY_OWN_LIMIT:
        logger.info("Límite diario de crecimiento alcanzado")
        return results

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"

    _cleanup_chrome_profile(profile_dir)

    chrome_bin = getattr(config, "CHROME_BINARY", "")
    if not chrome_bin:
        for candidate in [
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
        ]:
            if Path(candidate).exists():
                chrome_bin = candidate
                break

    # Resetear cursor al centro
    _cursor["x"] = 960.0
    _cursor["y"] = 540.0

    browser = None
    try:
        browser = await uc.start(
            user_data_dir=str(profile_dir),
            browser_executable_path=chrome_bin or None,
            browser_args=[
                "--start-maximized",
                "--window-size=1920,1080",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.get("about:blank")
        await _inject_stealth(page)

        # Warm-up natural — igual que youtube_uploader
        page = await browser.get("https://www.youtube.com")
        await _delay(3.0, 6.0)

        # Descartar consent dialog de Google/YouTube si aparece
        await _dismiss_consent(page)

        await _scroll(page, random.randint(150, 350))
        await _random_mouse_wander(page)
        await _delay(2.0, 4.5)

        # ── Comentar en videos del nicho ──────────────────────────────────────
        if ext_count < DAILY_EXTERNAL_LIMIT:
            # Rotar keywords con ventana de 48h: cada keyword puede volver a usarse
            # pasadas 48h → nunca se queda sin keywords frescas aunque el pool sea pequeño
            log = _load_log()
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
            used_kws = log.get("used_keywords", {})
            # Solo mantener keywords usadas en últimas 48h (bloqueadas)
            active_used = {k: d for k, d in used_kws.items() if d >= cutoff}
            fresh = [k for k in NICHE_SEARCHES if k not in active_used]
            if len(fresh) < 3:
                active_used = {}  # si se agotaron, resetear todas
                fresh = list(NICHE_SEARCHES)
            keywords = random.sample(fresh, k=min(3, len(fresh)))
            log["used_keywords"] = active_used
            _save_log(log)

            # Batches de 2-3 comentarios con micro-break entre bloques
            BATCH_SIZE = random.randint(2, 3)
            session_done = 0
            batch_done = 0

            for keyword in keywords:
                log = _load_log()
                ext_count, _ = _daily_counts(log)
                if ext_count >= DAILY_EXTERNAL_LIMIT:
                    break

                logger.info(f"Búsqueda: '{keyword}'")
                videos = await _search_niche_videos(browser, keyword, log)

                # Registrar keyword como usada
                log.setdefault("used_keywords", {})[keyword] = _today()
                _save_log(log)

                for video in videos:
                    log = _load_log()
                    ext_count, _ = _daily_counts(log)
                    if ext_count >= DAILY_EXTERNAL_LIMIT:
                        break

                    ok = await _comment_on_video(browser, video)
                    if ok:
                        session_done += 1
                        batch_done += 1
                        results["external"] += 1
                    else:
                        results["skipped"] += 1

                    # Pausa natural entre comentarios individuales
                    await _delay(20.0, 45.0)

                    # Al completar un batch: micro-break de navegación orgánica
                    if batch_done >= BATCH_SIZE:
                        logger.info(f"  Micro-break después de {batch_done} comentarios...")
                        await _browse_casually(browser)
                        batch_done = 0
                        BATCH_SIZE = random.randint(2, 3)  # variar el próximo batch

        # ── Engagement en canal propio ─────────────────────────────────────────
        if do_own:
            log = _load_log()
            _, own_count = _daily_counts(log)
            if own_count < DAILY_OWN_LIMIT:
                logger.info("Iniciando engagement en canal propio...")
                results["own"] += await _engage_own_channel(browser, log, own_video_url=own_video_url)

    except Exception as e:
        logger.error(f"Error en sesión de crecimiento: {e}", exc_info=True)
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    logger.info(
        f"Sesión terminada — externos: {results['external']} | "
        f"propios: {results['own']} | omitidos: {results['skipped']}"
    )
    return results


# ─── API pública ──────────────────────────────────────────────────────────────

def run_growth_session(do_own: bool = True, own_video_url: str = "") -> dict:
    """
    Ejecuta una sesión de crecimiento.
    Se llama desde main.py después de cada upload y 2x por día extra.

    Args:
        do_own: Si True, deja comentario/pin en el canal propio.
        own_video_url: URL del video recién subido para comentar directamente
                       (evita scraping del canal, útil cuando el video aún procesa).
    """
    logger.info("=== GROWTH AGENT — inicio de sesión ===")

    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _growth_session_async(do_own=do_own, own_video_url=own_video_url)
            )
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    for t in pending:
                        t.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)
    else:
        return asyncio.run(_growth_session_async(do_own=do_own, own_video_url=own_video_url))
