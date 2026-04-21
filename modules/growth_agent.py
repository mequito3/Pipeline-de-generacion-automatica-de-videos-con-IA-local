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

DAILY_EXTERNAL_LIMIT  = 5   # máx comentarios externos por día (distribuidos en sesiones)
DAILY_OWN_LIMIT       = 2   # máx replies en canal propio por día
SESSION_EXTERNAL_CAP  = 2   # máx por sesión — un humano no comenta en ráfaga
GROWTH_LOG_FILE      = Path(__file__).parent.parent / "growth_log.json"

# ─── Keywords para buscar videos del nicho ────────────────────────────────────

MIN_VIDEO_VIEWS = 50_000   # filtrar videos con menos de 50K vistas

# Términos que indican contenido off-topic (anime, gaming, reacciones a ficción, etc.)
_TITLE_BLACKLIST = [
    "spy x family", "familia forger", "anya", "loid", "yor",
    "one piece", "naruto", "dragon ball", "my hero academia", "boku no hero",
    "dabi", "gacha", "genshin", "minecraft", "roblox", "fortnite",
    "anime", "manga", "temporada", "episodio", "capitulo", "capítulo",
    "película completa", "movie", "trailer", "tráiler",
    "unboxing", "gameplay", "gaming", "videojuego",
    "reaccionando a", "reacción de", "react to", "reaction to",
    "reacciono a", "veo por primera vez",
]

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
    # === NICHO ESPECÍFICO — TÉRMINOS QUE NO CONFUNDEN AL ALGORITMO ===
    "infidelidad descubierta historia real narrada",
    "traición de pareja historia verdadera corta",
    "secreto familiar revelado historia real española",
    "relación tóxica que viví historia real contada",
    "mentira de pareja descubierta así relato",
    "lo descubrí todo en un momento historia real",
    "me engañó años historia contada por ella",
    "doble vida descubierta relato real breve",
    "confesión real infidelidad español narrado",
    "drama familiar real contado en primera persona",
]

# ─── Generadores dinámicos de texto vía Groq ─────────────────────────────────

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Mantenemos esto solo como referencia de personalidades para el prompt — no se usa como fallback
_COMMENT_PERSONAS = {
    "impactado": [
        # — shock corto —
        "no no no esto no puede ser real te lo juro",
        "dios mío qué fuerte",
        "bro no",
        "me dejó helada de verdad",
        "no puedo creerlo",
        "qué locura hermano",
        "esto no debería existir pero existe",
        "me quedé sin palabras literal",
        "no manches",
        "ay dios no",
        "qué asco de persona",
        "me abrió la boca",
        "esto es demasiado",
        "no puede ser",
        "me traumó",
        # — shock medio —
        "me cayó el veinte con lo de {kw} literalmente",
        "qué traición tan grande dios mío 😶",
        "esto está al nivel de película pero es REAL",
        "bro qué fuerte nunca me lo esperaba así",
        "me dejó sin palabras de verdad",
        "eso es lo más fuerte q vi en mucho tiempo",
        "no puedo con esto q acabo de ver",
        "lo de {kw} me partió el alma",
        "qué clase de persona hace eso dios mío",
        "esto me revolvió el estómago",
        "me cae q no lo hubiera creído si no lo veo",
        "maldita sea qué historia tan fuerte",
        "no esperaba eso pa nada",
        "me cayó como balde de agua fría",
        "literalmente me pusieron los pelos de punta",
        "esto me sacudió de verdad",
        "vi el título y dije no puede ser y era peor",
        "qué golpe tan bajo",
        "eso duele leerlo imagínate vivirlo",
        "se me fue el aliento con esa parte",
        "no me entra en la cabeza cómo alguien puede hacer eso",
        "qué historia tan heavy de verdad",
        "me movió algo por dentro que no sé explicar",
        "estaba tranquila y me mandaron esto",
        "fui a ver un video y me arruinaron el día jaja pero en serio qué fuerte",
        "hay personas q no merecen lo q tienen y esta es una",
        "lo de {kw} no lo procesa ni el cerebro más frío",
        "no sé cómo se sigue después de algo así",
        "esto me quitó las ganas de confiar en alguien",
        "acabé de ver esto y todavía estoy procesando",
        "me cayó el piso con esa parte final",
        "¿cómo se sobrevive a algo así? de verdad",
        "me dejó la historia pegada en el cerebro",
        "sentí que me lo estaba contando a mí directamente",
        "qué traición tan calculada eso es lo q más duele",
        "no hay palabras pa describir lo q acabo de ver",
        "me puse en el lugar de esa persona y se me aguaron los ojos",
        "hay historias q te recuerdan pa qué sirve el bloqueo",
        "esto es de esas cosas q no te dejan dormir tranquilo",
        "no entiendo cómo la gente puede ser tan cruel en serio",
        "leí el título y pensé exageran, nope no exageran para nada",
        "me acabo de enterar q el mundo puede ser muy muy feo",
        # — shock largo —
        "juro que en algún momento pensé que era ficción y no",
        "hay historias que te recuerdan por qué hay que tener cuidado con quien confías",
        "esto me lo mandaron y no sé si darle las gracias o el reclamo",
        "me puse a ver esto pensando que era corto y me dejó así",
        "vi el thumbnail y pensé 'otro drama exagerado' y no era para nada exagerado",
        "no sé q es peor si lo que hizo o el tiempo q tardó en salir la verdad",
        "me quedo con la parte de {kw} q eso sí no me lo esperaba",
        "esto me lo recomendó alguien y ahora tengo muchas preguntas sobre mi vida",
        "necesito una pausa después de esto en serio",
        "me costó terminar de ver porque se me iba la sangre",
    ],
    "identificado": [
        # — muy corto —
        "ay esto me tocó",
        "demasiado cercano esto",
        "me vi reflejada",
        "esto lo viví",
        "ay hermana/o",
        "me llegó profundo",
        "demasiado real",
        "esto lo conozco bien",
        "exactamente lo mismo",
        "me dolió porque lo entiendo",
        # — medio —
        "me pasó algo calcado y todavía duele te lo juro",
        "eso mismo viví yo y nunca lo superé del todo",
        "demasiado real esto me tocó el corazón de verdad",
        "esto es más común de lo q la gente cree",
        "no sabía q alguien más había vivido algo así",
        "me recuerda tanto a lo q yo viví hace unos años",
        "yo pasé por algo muy parecido y sé lo q se siente",
        "esto lo viví y te juro q el dolor no se describe",
        "hermano/a no estás solo/a con esto, yo también lo viví",
        "me paralicé en esa parte porque me pasó igual",
        "palabra por palabra me estaba describiendo mi historia",
        "esto que muestran es más normal de lo q parece y eso da miedo",
        "me tocó algo muy adentro q hacía tiempo no sentía",
        "yo viví algo así y lo que más duele es lo q nadie ve por fuera",
        "hay cosas q uno carga solo y ver esto hace sentir que no era el único",
        "me fui a los comentarios a ver si alguien más lo había vivido",
        "no sé si reír o llorar porque me identifico demasiado",
        "tardé años en hablar de eso y verlo acá me sacudió",
        "oye este video lo tendría q ver alguien q conozco",
        "me recuerda una época q prefería no recordar pero es verdad q pasa",
        "exactamente esto es lo q pasé hace 3 años y todavía lo proceso",
        "no esperaba que un video me fuera a pegar tan fuerte hoy",
        "me quedé pensando en alguien específico mientras veía esto",
        "qué incómodo se siente reconocerse en una historia así",
        "esto le debería llegar a más personas porque es más real de lo q parece",
        "yo también guardé ese secreto durante años y entiendo perfectamente",
        "hay algo en {kw} q me dolió de una forma muy específica",
        "me pasó algo calcado pero nunca lo hubiera contado así de bien",
        "uno pensaba q era el único y resulta q no",
        "me vino un recuerdo muy específico con esta historia",
        # — largo —
        "pasé algo parecido y te juro q lo q más duele no es el hecho sino enterarte de cómo te veían mientras tanto",
        "lo q más me llegó es esa sensación de sentirte tonto/a cuando descubres todo",
        "viví algo muy similar y lo más raro es q uno termina sintiéndose culpable aunque no lo sea",
        "esto lo conté en privado a muy pocas personas porque da vergüenza admitirlo pero sí, pasa",
        "me alegra q alguien lo cuente porque mucha gente vive esto en silencio y se siente sola",
        "lo q más me resonó es que uno sigue adelante y hace su vida pero eso no se olvida nunca del todo",
        "hay momentos en esta historia q reconozco sin necesidad de que me los expliquen",
        "yo no lo hubiera dicho con estas palabras pero es exactamente lo q sentí",
        "me sorprende cuánta gente ha pasado por algo parecido y ninguno lo habla",
        "lo de {kw} me hizo acordar de una situación q creía superada y evidentemente no",
    ],
    "escéptico": [
        # — muy corto —
        "no sé no sé",
        "algo no cierra ahí",
        "mmm no del todo",
        "qué raro eso",
        "hay algo raro",
        "no me convence",
        "algo falta",
        "eso no suena bien",
        # — medio —
        "algo no me cuadra pero bueno puede ser",
        "¿y por qué aguantó tanto? eso no me cierra",
        "hay cosas q no encajan del todo en la historia",
        "no sé si creerle del todo pero si es real qué fuerte",
        "me cuesta creer q nadie se diera cuenta antes",
        "¿en serio nadie le dijo nada? raro eso",
        "no digo q mienta pero hay partes q no me cuadran",
        "a ver... ¿nadie más vio señales antes? eso me parece difícil",
        "la historia tiene lógica pero hay puntos q no terminan de cerrar",
        "puede ser verdad pero falta contexto para juzgar bien",
        "hay algo en el tono que me hace pensar que falta parte de la historia",
        "no sé, me genera dudas la parte de {kw}",
        "¿y por qué justo ahí? eso no lo entiendo bien",
        "puede ser pero también pudo haberlo resuelto antes",
        "me llama la atención que nadie más sepa nada de esto",
        "algo en esta historia no encaja y no sé bien qué es",
        "lo creo pero me parece q hay más detrás q no se dice",
        "¿en serio así fue exactamente? parece mucho pero bueno",
        "la gente no suele actuar así de limpio... hay algo más",
        "no dudo q haya pasado algo pero no todo como se cuenta",
        "¿nadie preguntó nada antes? eso me parece raro la verdad",
        "entiendo la historia pero hay cosas q no suenan del todo honestas",
        "mhm puede ser. igual hay detalles q me parecen convenientes",
        "me quedo con la duda de qué contaría la otra parte",
        "no es que no lo crea, es q hay piezas q no encajan perfectamente",
        # — largo —
        "a ver sin juzgar pero hay algo en cómo está contado q me genera preguntas",
        "puede q todo sea verdad pero me parece q hay contexto q no se da y q cambia la lectura",
        "lo que más me llama la atención es por qué esperar tanto para contarlo",
        "no digo q sea mentira, digo q cuando hay una sola versión siempre falta algo",
        "hay historias q te cuentan de una manera y cuando escuchas la otra parte resulta diferente",
        "me quedé pensando en la parte de {kw} porque eso no cierra del todo con lo anterior",
        "puede ser que todo pasara exactamente así pero hay algo q no termina de convencerme",
        "sin ánim de ofender pero hay detalles que solo se recuerdan cuando convienen",
        "no pongo en duda lo q vivió pero tampoco toda historia tiene un solo culpable claro",
        "esto levanta preguntas que la historia no responde y eso me hace pensar",
    ],
    "curioso": [
        # — muy corto —
        "y después qué",
        "parte 2 porfavor",
        "¿cómo terminó?",
        "necesito más",
        "¿y luego?",
        "espera y qué pasó",
        "¿hablaron después?",
        "¿cómo quedaron?",
        # — medio —
        "necesito saber q pasó con {kw} después",
        "parte 2 ya esto no puede quedar así",
        "me quedé con ganas de saber el final de verdad",
        "¿volvieron a hablar o fue el final definitivo?",
        "¿y la otra persona qué dijo cuando se supo todo?",
        "quiero saber cómo terminó esto en serio",
        "¿y después de eso qué pasó con {kw}?",
        "¿hay continuación? necesito saber el final",
        "me quedé colgado/a con esa última parte",
        "¿y cómo está ahora esa persona?",
        "espera ¿y eso cómo terminó?",
        "¿alguien más preguntó algo o todos se hicieron los que no sabían?",
        "¿y la familia qué dijo cuando se enteró?",
        "¿volvieron a verse? porque eso me quedó pendiente",
        "¿y {kw} nunca dio explicaciones o quedó todo en el aire?",
        "qué pasó después no me queda claro",
        "me dejaron con el cliffhanger más innecesario de mi vida",
        "¿y cómo sigue viviendo con eso? de verdad necesito saber",
        "no puedo quedarme sin saber el final de esto",
        "¿hay segunda parte o la dejaron ahí?",
        "¿alguien tiene más info sobre cómo terminó?",
        "me quedé con la duda de {kw} alguien sabe?",
        "¿eso fue lo último que se supo o hubo más cosas después?",
        "oye ¿se reconciliaron o fue definitivo?",
        "¿y los demás? ¿nadie dijo nada más después?",
        # — largo —
        "lo que más quiero saber es qué pasó con {kw} porque la historia queda incompleta sin eso",
        "me gustaría saber cómo está esa persona hoy porque lo que vivió no es poca cosa",
        "¿hay algún update? porque eso no puede quedar así y ya",
        "la parte q más me dejó con duda es q pasó después de {kw} eso no quedó claro",
        "me gustaría escuchar la historia completa porque siento q falta bastante",
        "¿alguien que vio esto sabe si hay más contexto en algún lado?",
        "necesito saber si al final hubo algún tipo de justicia o todo quedó igual",
        "lo que más me genera curiosidad es si esa persona supo alguna vez lo que causó",
        "¿y los que estaban alrededor? ¿cómo reaccionaron cuando salió todo a la luz?",
        "quiero saber si con el tiempo las cosas se resolvieron o todavía están igual de rotas",
    ],
    "opinador": [
        # — muy corto —
        "eso no se perdona",
        "error garrafal",
        "se lo buscó",
        "no hay excusa",
        "clarísimo desde el principio",
        "debió irse antes",
        "no hay vuelta",
        "eso tiene nombre",
        # — medio —
        "desde el primer momento esa persona mostraba quien era",
        "no se perdona eso, punto",
        "el error fue perdonarla la primera vez honestamente",
        "hay cosas q no tienen vuelta y esta es una de ellas",
        "yo hubiera tomado la misma decisión sin dudar",
        "lo que hizo no tiene justificación ninguna",
        "uno tiene q saber cuándo retirarse y punto",
        "eso se llama falta de carácter y ya",
        "la persona que hace eso no merece ni explicación",
        "lo perdonaron una vez y ahí fue el error",
        "hay señales desde el inicio pero uno no las ve hasta después",
        "eso no fue un error fue una decisión",
        "quien hace eso sabe exactamente lo que está haciendo",
        "no hay forma de justificar eso ni intentándolo mucho",
        "en mi opinión lo correcto era irse mucho antes",
        "eso se llama egoísmo puro y duro",
        "uno a veces se aferra a lo q quiere ver y no a lo q es",
        "eso tiene consecuencias y está bien q las tenga",
        "a veces el problema no es el otro sino que uno lo permite",
        "si ya lo hizo una vez lo iba a volver a hacer, así funciona",
        "hay gente q solo aprende cuando ya no te tiene",
        "no es sadismo es justicia que se sepa la verdad",
        "la gente así siempre encuentra a quien culpar menos a sí misma",
        "desde q dijo lo de {kw} ya se sabía cómo iba a terminar esto",
        "lo q hizo no tiene nombre bonito llámenlo por lo que es",
        # — largo —
        "mi opinión es q hubo muchas señales que se ignoraron y eso no es casualidad",
        "creo que el problema de fondo es que se confundió amor con necesidad",
        "esto es un ejemplo claro de que algunas personas solo cambian cuando les conviene",
        "lo que más me molesta es que ese tipo de personas siempre encuentra cómo quedar bien con todos",
        "no juzgo las decisiones pero hay una parte donde claramente se pudo actuar antes",
        "hay gente que hace daño sin remordimiento y lo peor es que les funciona durante mucho tiempo",
        "eso que hizo no fue un error de juicio fue una elección consciente y punto",
        "lo más triste es q probablemente esa persona ni siquiera entiende el daño q causó",
        "cuando alguien muestra su verdadero carácter hay q creerle aunque duela",
        "mi consejo siempre es el mismo: la primera vez que alguien te falla así es la última",
    ],
    "solidario": [
        # — muy corto —
        "fuerza 🙏",
        "qué difícil",
        "mucho ánimo",
        "ay qué dolor",
        "lo siento mucho",
        "cuánto dolor",
        "uno no merece eso",
        "ojalá esté bien",
        "ánimo de verdad",
        "te mando buena energía",
        # — medio —
        "fuerza para quien vivió algo así de verdad 🙏",
        "hay cosas de las q uno no se recupera fácil",
        "lo importante es q ya salió de ahí",
        "cuánto dolor dios mío q difícil todo eso",
        "uno nunca está listo pa recibir algo así",
        "ojalá esté bien quien vivió esto de verdad",
        "nadie debería pasar por algo así",
        "ojalá hoy esté en un lugar mejor q antes",
        "esa persona es más fuerte de lo que cree",
        "es de admirar q puedan contarlo, no es fácil",
        "eso deja marca, ojalá encuentre paz con el tiempo",
        "te mando toda la fuerza desde acá de verdad",
        "no me imagino cómo se habrá sentido en ese momento",
        "lo que más me mueve es el nivel de traición que vivió",
        "que alguien lo cuente ya es un paso enorme",
        "hay cosas q uno procesa de a poco y está bien así",
        "ojalá quien lo vivió tenga personas cerca que lo apoyen",
        "no es fácil salir de algo así pero se puede",
        "qué difícil tomar esa decisión en ese momento",
        "a veces uno carga solo lo q debería cargar acompañado",
        "uno no merece ese tipo de cosas viniendo de quien confía",
        "el dolor de esa traición no se explica, se siente",
        "que pueda contarlo ya muestra q va sanando aunque duela",
        "la vida después de algo así no es la misma pero sigue",
        "ojalá hoy ese dolor sea un poco más chico q ayer",
        # — largo —
        "me duele que haya gente que pase por esto sola sin poder contarlo",
        "hay una valentía enorme en poder hablar de algo así aunque haya pasado tiempo",
        "ojalá quien vivió esto sepa que hay gente que lo escucha y lo entiende desde acá",
        "lo que más me llega es pensar en cómo se habrá sentido en ese momento sin nadie que supiera",
        "hay heridas que no se ven pero que cargan más peso que las que sí se ven",
        "eso que vivió no era normal aunque en su momento lo pareciera, ojalá lo sepa hoy",
        "la gente que pasa por cosas así merece mucho más reconocimiento del que recibe",
        "me alegra q cuenten estas historias porque mucha gente se siente sola con lo mismo",
        "hay procesos que llevan tiempo y está bien, lo importante es seguir",
        "uno aprende a vivir con esas cicatrices aunque nunca desaparezcan del todo",
    ],
}



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


# ─── Generadores 100% dinámicos vía Groq ─────────────────────────────────────

async def _groq_call(prompt: str, max_tokens: int) -> str | None:
    """Llama a Groq y retorna el texto generado. None si falla tras 3 intentos."""
    api_key = getattr(config, "GROQ_API_KEY", "")
    if not api_key:
        return None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    _GROQ_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.95,
                    },
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                if text:
                    return text
        except Exception as e:
            logger.debug(f"Groq intento {attempt + 1}/3: {e}")
            if attempt < 2:
                await asyncio.sleep(3)
    return None


async def _generate_comment(video_title: str) -> str | None:
    """Genera comentario 100% dinámico contextual al video. None si Groq no responde."""
    persona, descripcion, longitud = random.choice([
        ("IMPACTADO",    "reacciona con shock genuino, no puede creerlo",           "4-7 palabras, muy corto"),
        ("IMPACTADO",    "reacciona con shock genuino, no puede creerlo",           "10-16 palabras"),
        ("IDENTIFICADO", "cuenta brevísimamente que vivió algo similar",            "8-15 palabras"),
        ("IDENTIFICADO", "cuenta brevísimamente que vivió algo similar",            "12-18 palabras"),
        ("ESCÉPTICO",    "duda de algo concreto en la historia",                    "8-14 palabras"),
        ("CURIOSO",      "pregunta qué pasó después o pide parte 2",               "5-12 palabras"),
        ("OPINADOR",     "da su opinión directa sin filtro sobre la situación",     "10-18 palabras"),
        ("SOLIDARIO",    "expresa empatía o apoyo emocional hacia el narrador",     "6-12 palabras"),
    ])
    prompt = (
        f'Eres un espectador real latinoamericano escribiendo desde el celular.\n'
        f'Acabas de ver este video de YouTube: "{video_title}"\n'
        f'Personalidad hoy: {persona} — {descripcion}.\n'
        f'Longitud exacta: {longitud}.\n'
        "Escribe UN comentario en español latino, estilo WhatsApp casual. "
        "Usa 'q'/'pa'/'xq'/'bro'/'hermano'/'o sea' según suene natural. "
        "Sin mayúscula al inicio si da más natural. Sin punto final. "
        "1 error de tipeo mínimo está ok pero no exageres.\n"
        "PROHIBIDO ABSOLUTO: 'sígueme', 'te sigo', 'suscríbete', mencionar canales, "
        "cualquier auto-promoción. Los humanos reales NUNCA hacen eso en videos ajenos.\n"
        "PROHIBIDO: más de 1 emoji. Sin comillas. Sin explicaciones.\n"
        "Responde SOLO con el comentario."
    )
    result = await _groq_call(prompt, max_tokens=55)
    if result and len(result) > 180:
        result = result[:180].rsplit(" ", 1)[0]
    return result


async def _generate_reply(comment_text: str, video_title: str = "") -> str | None:
    """Genera respuesta dinámica al comentario de un espectador. None si Groq no responde."""
    if not comment_text.strip():
        return None
    include_cta = random.random() < 0.25
    cta_hint = (
        "Si suena natural puedes cerrar con algo como 'gracias por estar acá' o similar (nunca 'sígueme')."
        if include_cta else ""
    )
    prompt = (
        "Eres el creador de un canal de YouTube de confesiones y dramas reales en español latino.\n"
        + (f'Tu video se llama: "{video_title}"\n' if video_title else "")
        + f'Un espectador comentó: "{comment_text}"\n'
        "Escribe UNA respuesta corta, auténtica y cálida (máx 18 palabras).\n"
        "REGLAS: responde DIRECTAMENTE a lo que dijo. Estilo casual de barrio, sin punto final. "
        "Usa 'q', 'xq', 'pa' si suena natural. "
        f"{cta_hint}\n"
        "PROHIBIDO: repetir su comentario. PROHIBIDO: 'sígueme' o auto-promoción directa. "
        "PROHIBIDO: más de 1 emoji.\n"
        "Responde SOLO con el texto, sin comillas."
    )
    result = await _groq_call(prompt, max_tokens=65)
    if result and len(result) > 200:
        result = result[:200].rsplit(" ", 1)[0]
    return result


async def _generate_pin(video_title: str, pregunta: str = "") -> str | None:
    """Genera el comentario pineado dinámico basado en el video real. None si Groq falla."""
    formato = random.choice([
        "pregunta que genere debate (¿team X o team Y?)",
        "pregunta abierta sobre qué haría el espectador en esa situación",
        "pregunta sobre si alguien vivió algo similar",
        "reflexión sobre el tema del video convertida en pregunta",
        "CTA invitando a compartir su opinión sobre la historia",
    ])
    prompt = (
        "Eres el creador de un canal de YouTube de confesiones y dramas reales en español latino.\n"
        f'Acabas de publicar un video titulado: "{video_title}"\n'
        + (f'La pregunta final del video es: "{pregunta}"\n' if pregunta else "")
        + f"Escribe UN comentario para pinear (máx 15 palabras). Formato: {formato}.\n"
        "Objetivo: que la gente comente, debata o cuente su experiencia.\n"
        "Estilo: creador real hablando con su comunidad, casual, sin formalidades. "
        "Puede incluir 1 emoji (👇 o 🙌 o 🙏) si mejora el texto.\n"
        "PROHIBIDO: más de 1 emoji. Sin hashtags. Sin 'sígueme'.\n"
        "Responde SOLO con el texto del comentario."
    )
    result = await _groq_call(prompt, max_tokens=50)
    return result


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
                continue
            title_lower = title.lower()
            if any(bad in title_lower for bad in _TITLE_BLACKLIST):
                logger.debug(f"  Filtrado off-topic: {title[:60]}")
                continue
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
    """
    Simula el comportamiento completo de un humano viendo y comentando un video.
    Cada visita es diferente: varía cuánto ve, si lee comentarios ajenos,
    si da like, si scrollea la descripción, y cómo llega al campo de comentario.
    """
    try:
        logger.info(f"  [{video['title'][:55]}]")
        page = await browser.get(video["url"])
        await _delay(3.0, 7.0)

        # ── 1. Orientación inicial (como cuando acabas de llegar al video) ───────
        # A veces el humano scrollea un poco arriba/abajo antes de enfocarse
        if random.random() < 0.5:
            await _scroll(page, random.randint(60, 180))
            await _delay(1.0, 2.5)
            await _scroll(page, random.randint(-60, -20))
        await _random_mouse_wander(page)

        # ── 2. Ver el video (tiempo proporcional a la duración real) ─────────────
        dur_text = await _eval_safe(page, """(function() {
            var t = document.querySelector('.ytp-time-duration');
            return t ? (t.innerText || '') : '';
        })()""") or ""
        duration_s = _parse_yt_duration(dur_text)

        if duration_s > 0:
            if duration_s <= 63:
                ratio = random.triangular(0.45, 0.90, 0.65)   # Shorts: casi completo
            elif duration_s <= 300:
                ratio = random.triangular(0.20, 0.50, 0.32)
            else:
                ratio = random.triangular(0.08, 0.22, 0.13)
            watch_secs = max(10.0, min(duration_s * ratio, 240.0))
        else:
            watch_secs = random.triangular(12.0, 38.0, 22.0)

        logger.debug(f"  Viendo {watch_secs:.0f}s (duración: {dur_text or '?'})")

        # Durante la visualización: movimientos ocasionales como humano real
        elapsed = 0.0
        while elapsed < watch_secs:
            chunk = random.triangular(4.0, 14.0, 8.0)
            await asyncio.sleep(min(chunk, watch_secs - elapsed))
            elapsed += chunk
            if elapsed < watch_secs and random.random() < 0.3:
                await _random_mouse_wander(page)

        # ── 3. A veces lee la descripción antes de bajar a comentarios ───────────
        if random.random() < 0.35:
            await _scroll(page, random.randint(150, 280))
            await _delay(3.0, 7.0)   # "lee" la descripción
            await _random_mouse_wander(page)

        # ── 4. A veces da like al video (si le gustó lo suficiente) ─────────────
        if random.random() < 0.40:
            try:
                like_btn = await page.select(
                    "ytd-toggle-button-renderer:first-of-type button, "
                    "button[aria-label*='like' i], button[aria-label*='Me gusta']",
                    timeout=4
                )
                if like_btn:
                    await _delay(0.8, 2.0)
                    await _human_click(page, like_btn)
                    await _delay(0.5, 1.5)
                    logger.debug("  👍 Like dado antes de comentar")
            except Exception:
                pass

        # ── 5. Scroll hacia la sección de comentarios ────────────────────────────
        await _scroll(page, random.randint(350, 600))
        await _delay(2.5, 5.0)
        await _random_mouse_wander(page)

        # ── 6. LEER comentarios ajenos antes de escribir (humano real hace esto) ──
        n_reads = random.randint(2, 5)
        for _ in range(n_reads):
            await _scroll(page, random.randint(80, 200))
            await _delay(random.triangular(2.0, 8.0, 4.0), random.triangular(4.0, 12.0, 7.0))
        await _random_mouse_wander(page)

        # A veces scrollea de vuelta un poco (como cuando buscas el cuadro de comentario)
        if random.random() < 0.4:
            await _scroll(page, random.randint(-150, -60))
            await _delay(1.0, 2.5)

        # ── 7. Encontrar y hacer clic en la caja de comentario ───────────────────
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
        await _delay(1.2, 2.8)

        # ── 8. Generar y escribir el comentario ───────────────────────────────────
        active_box = None
        for sel in ["#contenteditable-root", "[contenteditable='true']"]:
            try:
                active_box = await page.select(sel, timeout=5)
                if active_box:
                    break
            except Exception:
                pass

        comment = await _generate_comment(video["title"])
        if not comment:
            logger.warning("  Groq no generó comentario — omitiendo este video")
            return False
        logger.info(f"  Comentario: {comment[:70]}")

        # A veces pausa antes de empezar a escribir (como pensando qué poner)
        await _delay(random.triangular(1.5, 6.0, 3.0), random.triangular(3.0, 9.0, 5.0))
        await _human_type(active_box or comment_box, comment, clear_first=False)

        # Pausa post-escritura (releer antes de enviar)
        await _delay(1.5, 4.0)
        await _random_mouse_wander(page)
        await _think()

        # ── 9. Enviar ─────────────────────────────────────────────────────────────
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
                    await _delay(2.5, 5.5)
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            logger.warning("  Botón submit no encontrado")
            return False

        # ── 10. Comportamiento post-comentario (humano sigue unos segundos) ───────
        await _delay(3.0, 8.0)
        if random.random() < 0.5:
            await _scroll(page, random.randint(100, 300))
            await _delay(2.0, 5.0)

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

        page_title = await page.evaluate("document.title || ''")
        clean_title = re.sub(r"\s*[-–|].*$", "", page_title).strip()
        pin_text = await _generate_pin(clean_title)
        if not pin_text:
            logger.warning("  _leave_pin_comment: Groq no generó texto — omitiendo pin")
            return False
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
                page_title = await page.evaluate("document.title || ''")
                clean_title = re.sub(r"\s*[-–|].*$", "", page_title).strip()
                reply_text = await _generate_reply(comment_text, video_title=clean_title)
                if not reply_text:
                    logger.debug("  Groq no generó reply — omitiendo este comentario")
                    continue

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
            keywords = random.sample(fresh, k=min(2, len(fresh)))
            log["used_keywords"] = active_used
            _save_log(log)

            session_done = 0

            for keyword in keywords:
                log = _load_log()
                ext_count, _ = _daily_counts(log)
                if ext_count >= DAILY_EXTERNAL_LIMIT:
                    logger.info("Límite diario de comentarios externos alcanzado")
                    break
                if session_done >= SESSION_EXTERNAL_CAP:
                    logger.info(f"  Cap de sesión alcanzado ({SESSION_EXTERNAL_CAP}) — continuará en la próxima sesión")
                    break

                logger.info(f"Búsqueda: '{keyword}'")
                videos = await _search_niche_videos(browser, keyword, log)

                # Registrar keyword como usada
                log.setdefault("used_keywords", {})[keyword] = _today()
                _save_log(log)

                for video in videos:
                    log = _load_log()
                    ext_count, _ = _daily_counts(log)
                    if ext_count >= DAILY_EXTERNAL_LIMIT or session_done >= SESSION_EXTERNAL_CAP:
                        break

                    ok = await _comment_on_video(browser, video)
                    if ok:
                        session_done += 1
                        results["external"] += 1
                        # Después de comentar: browsing orgánico antes del siguiente
                        if session_done < SESSION_EXTERNAL_CAP:
                            await _browse_casually(browser)
                    else:
                        results["skipped"] += 1

                    # Pausa variable entre intentos (comentado o no)
                    await _delay(15.0, 40.0)

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
