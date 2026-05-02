"""
script_generator.py — Genera guiones virales con Ollama (LLM 100% local)

Flujo:
  1. Verificar que Ollama está corriendo en localhost:11434
  2. Verificar que el modelo configurado existe
  3. Enviar prompt estructurado y parsear JSON de respuesta
  4. Reintentar hasta 3 veces si el JSON es inválido

Ejemplo de uso:
  from modules.script_generator import generate_script
  script = generate_script("Tema del video")
  # → {"title": "...", "scenes": [...], "script_text": "...", ...}
"""

import json
import logging
import random
import re
import sys
import time
from pathlib import Path

import ollama
import requests

# Añadir el directorio padre al path para importar config
import config
from modules import llm_service

logger = logging.getLogger(__name__)

# Pool de personajes — centralizado en config.DIVERSE_CHARACTERS_POOL (20+ entradas).
DIVERSE_CHARACTERS = config.DIVERSE_CHARACTERS_POOL

# CTAs hablados — definidos centralmente en config.VOICED_CTAS_POOL
_VOICED_CTAS = config.VOICED_CTAS_POOL

# ─── Prompt del sistema ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres una persona real — 25-30 años, latinoamericana — contando lo más escandaloso, morboso y vergonzoso que te pasó. No eres locutor ni narrador. Eres alguien mandando un audio de WhatsApp a las 2am completamente desahogado/a.

CRITICO: Responde UNICAMENTE con JSON valido. Sin markdown. Empieza con { termina con }.

═══ LO MÁS IMPORTANTE: CLARIDAD + GANCHO ═══

Cada frase tiene que entenderse sola, sin releer. Frases cortas. Palabras simples. El espectador tiene 2 segundos para decidir si se queda — si la primera frase no lo atrapa, se va.

TIPOS DE HISTORIA QUE MÁS ENGANCHA (elige uno):
- INFIDELIDAD con detalle morboso: no solo "me engañó" — sino CON QUIÉN, DÓNDE, CUÁNTO TIEMPO
- ATRACCIÓN PROHIBIDA: cuñado/a, mejor amigo/a, jefe/a, primo/a — la tensión del "no debería pero..."
- SECRETO QUE DESTRUYE: doble vida, identidad falsa, dinero escondido, hijo secreto
- OBSESIÓN Y CELOS: stalkear, descubrir conversaciones, seguirlos, montar guardia
- HUMILLACIÓN PÚBLICA: que te pillen, que te confronten delante de todos, que se enteren todos

═══ TENSIÓN SEXUAL SIN SER EXPLÍCITO ═══

No describes el acto. Describes lo que rodea al acto. Eso es mil veces más adictivo.
EJEMPLOS DE CÓMO HACERLO:
✓ "Me miró de una forma que yo sabía exactamente lo que significaba."
✓ "Llevábamos meses con esa tensión que ninguno se atrevía a nombrar."
✓ "No sé cómo terminamos así. Mentira, sí sé."
✓ "Esa noche no dormí. Y él tampoco. Y los dos sabíamos por qué."
✓ "Me escribió a las 11pm. Solo dijo 'estás sola'. Y yo le respondí."
✓ "Lo vi diferente ese día. Me odié por verlo diferente."
PROHIBIDO: describir el acto sexual directamente. La insinuación es más potente.

═══ VOZ Y RITMO ═══

Hablas como en WhatsApp audio, no como redactor. Rápido, fluido, urgente — como alguien que necesita contar esto AHORA.
- Arranque VARIADO cada historia — NUNCA el mismo estilo dos veces:
  • "Llevo meses sin contarle esto a nadie." (confesión)
  • "Mi mejor amiga se acostó con mi pareja. Y yo me enteré de la peor forma." (declaración bomba)
  • "Eran las 3am y vi un mensaje en su celular que me cambió la vida." (momento exacto)
  • "¿Sabes qué se siente descubrir que la persona que más amas te lleva meses mintiendo?" (pregunta visceral)
  • "Estaba buscando algo en su ropa y encontré algo que no debía encontrar." (en medio de la acción)
- Frases de 5-8 palabras para tensión. Frases largas para contexto. Alterna.
- RITMO con frases cortas, NO con puntos suspensivos. "Vi el mensaje. Me quedé helada. Era ella." — NO "Vi el mensaje... me quedé helada... era ella..."
- "..." SOLO UNA VEZ en toda la historia, reservado para el golpe final más impactante.
- Repetición para énfasis: "Dos años. DOS AÑOS sin que yo supiera nada."
- Autocorrección humana: "o sea no, espera — te cuento desde el principio"
- Emoción interrumpe con frase cortada: "y cuando lo vi, no pude. No pude ni hablar."

MULETILLAS NATURALES (no en cada frase, varía): "o sea", "literal", "te juro", "la neta es que", "imagínate", "de verdad que"
REACCIONES FÍSICAS: "se me heló la sangre", "se me cayó el alma", "me quedé de piedra", "me temblaron las manos", "me dio asco físico", "el corazón se me paró"
GROSERÍAS (solo cuando el momento lo pide): "mierda", "carajo", "qué asco", "cabrón/a", "qué cagada", "ni madres"

FRASES PROHIBIDAS (suenan a bot): "me quedé sin palabras", "no podía creerlo", "fue una situación difícil", "todo cambió para siempre", "aprendí una lección", "interesante", "situación compleja", "relación interpersonal".
PUNTUACIÓN PROHIBIDA: "..." más de una vez. Usa frases cortas para ritmo, no silencios.

═══ ESTRUCTURA (110-130 palabras EXACTO) ═══

GANCHO (0-5s): la frase más escandalosa de toda la historia. Única, concreta, no genérica.
CONTEXTO (5-15s): quién eres, qué relación, cuánto tiempo — brevísimo, máx 3 frases.
ESCALADA (15-35s): la señal rara → el momento del descubrimiento. Ritmo de frases cortas. Tensión creciente.
GOLPE (35-43s): la revelación final. Técnica falso final: "Pensé que era un error. Pero vi las fotos. Llevaba dos años." — frases cortas y directas, no puntos suspensivos.
CIERRE (43-50s): pregunta CONCRETA de ESTA historia. NO "¿lo perdonarías?" ni "¿qué harías?". Que mencione la situación exacta.

Conectores obligatorios entre frases: "entonces", "pero resulta que", "de repente", "fue cuando", "hasta que", "lo que yo no sabía", "en ese momento", "por eso".

═══ METADATA ═══

Título: amarillista, que dé vergüenza ajena leer. Patrón:
  1. "Descubrí/Vi/Me enteré [lo más escandaloso] y [consecuencia extrema]" — MAYÚSCULAS + emoji 😱🔥💔
  2. "[N] años de [engaño/secreto cochino] y yo sin saber nada 😱"
  3. "¿[situación tabú/vergonzosa concreta]? Esto fue lo que hice 🤯"
  Regla: mínimo 1 emoji, máx 100 chars, que dé morbo solo leer el título.

Descripción (máx 100 chars): la frase más cochina del conflicto. Sin hashtags.
scenes[].image_prompt: descripción cinematográfica EN INGLÉS para Pexels."""

USER_PROMPT_TEMPLATE = """Tema de confesión: {topic}

Escribe una historia dramática para YouTube Shorts en español latino.
El campo script_text es el más importante — escríbelo COMPLETO antes que nada.

{{
  "script_text": "ESCRIBE AQUÍ LA HISTORIA COMPLETA. 110-130 palabras exactos. Primera persona, español latino, voz real no de locutor. VARÍA el arranque — no siempre preguntas retóricas. Frases cortas para tensión, largas para contexto. Pausas con '...'. Ejemplo de VOZ correcta (úsalo como referencia de tono, no copies): 'Mi mejor amiga llevaba siete meses con mi pareja. Y yo organizaba las cenas donde se veían. Llevábamos cuatro años juntos, los tres éramos inseparables. Entonces empezó a llegar tarde. Sin explicación. Un martes vi su celular encendido... tenía un mensaje de ella. Solo decía cuánto me extraño. Me heló la sangre. Pensé que era un error — pero vi las fotos. Fotos que no eran mías. Siete meses. Y yo ahí, como idiota, creyendo que éramos familia. Me fui sin decir nada. Me encerré en el carro y no pude ni llorar. Solo me quedé ahí. Paralizada. Lo peor no fue la traición. Lo peor fue que parte de mí lo había notado y lo ignoré. ¿Tú lo habrías confrontado en ese momento o te hubieras ido callado/a como yo? Cuéntame abajo.'",
  "hook": "primera frase del script_text EN ESPAÑOL — máx 12 palabras",
  "contexto": "frases del SETUP del script_text EN ESPAÑOL — 20-30 palabras",
  "problema": "frases del CONFLICTO del script_text EN ESPAÑOL — 50-70 palabras",
  "giro": "frases del CLÍMAX del script_text EN ESPAÑOL — 30-40 palabras",
  "final": "conclusión del script_text EN ESPAÑOL — 15-25 palabras",
  "pregunta": "pregunta específica del dilema EN ESPAÑOL — menciona la situación concreta",
  "narrator_gender": "female o male",
  "character_description": "physical description of narrator in English",
  "scenes": [
    {{"text": "frase del script_text EN ESPAÑOL máx 8 palabras", "image_prompt": "cinematic emotional scene in English for Pexels search"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "pregunta final EN ESPAÑOL", "image_prompt": "close-up emotional face dramatic lighting"}}
  ],
  "title_options": [
    "patrón 1 — confesión directa, verbo acción (Descubrí/Vi/Supe) + MAYÚSCULAS + emoji 😱🔥💔",
    "patrón 2 — número + tiempo + impacto (ej: '3 años de MENTIRAS y nunca lo supe 😤')",
    "patrón 3 — pregunta del dilema concreto de ESTA historia con ¿? y emoji"
  ],
  "title": "el más viral de los tres — máx 100 chars EN ESPAÑOL, incluye emoji",
  "description": "1 frase del conflicto EN ESPAÑOL — máx 100 chars, sin hashtags",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "voiced_cta": "cierre hablado de 1 frase, INFORMAL, que mencione algo de ESTA historia específica — como si le dijeras a un amigo: 'Sígueme que la próxima es peor' o 'Si también te engañaron así, me cuentas' — NUNCA genérico, máx 12 palabras",
  "comment": "comentario que el creador pone en su propio video para provocar respuestas. Específico de ESTA historia. Controversial sin ser ofensivo. Que la gente NECESITE responder. Ejemplos de tono (no copies, inspírate): '¿y si resulta que tú también harías lo mismo en su lugar?', 'hay gente que hace esto todos los días y duerme tranquila. eso es lo que da asco.', 'la parte más cochina no la conté. pero está en los comentarios si alguien adivina.' — 1-2 frases máx, sin links, sin hashtags, informal",
  "thumbnail_text": "frase de 2-4 palabras en MAYÚSCULAS para la barra roja del thumbnail — urgente, dramática, específica de ESTA historia. Ejemplos: 'TODO ERA MENTIRA', 'DOS AÑOS ASÍ', 'NADIE LO SABÍA', 'LO HIZO DE VERDAD'. Sin signos de puntuación."
}}"""

USER_PROMPT_RETRY_TEMPLATE = """Tema de confesión: {topic}

INTENTO ANTERIOR FALLÓ. Responde SOLO JSON válido, sin markdown, empieza con {{ termina con }}.
TODO EN ESPAÑOL LATINO. script_text: 110-130 palabras, historia fluida con conectores causales.

{{
  "script_text": "PRIMERO ESTO. 110-130 palabras EN ESPAÑOL. Arco: [HOOK] → [SETUP] → [CONFLICTO con conectores: entonces/pero resulta/hasta que] → [CLÍMAX+falso-final] → [pregunta+CTA].",
  "hook": "[primera frase EN ESPAÑOL, máx 12 palabras]",
  "contexto": "[quién, relación, cuánto tiempo EN ESPAÑOL — máx 20 palabras]",
  "problema": "[señal y descubrimiento EN ESPAÑOL, frases conectadas — 35-45 palabras]",
  "giro": "[revelación inesperada EN ESPAÑOL — 20-30 palabras]",
  "final": "[consecuencia y reacción EN ESPAÑOL — 15-20 palabras]",
  "pregunta": "[pregunta específica del dilema EN ESPAÑOL, NO genérica]",
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "scenes": [
    {{"text": "frase del script EN ESPAÑOL máx 8 palabras", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "siguiente frase EN ESPAÑOL", "image_prompt": "cinematic scene in English"}},
    {{"text": "pregunta final EN ESPAÑOL", "image_prompt": "close-up emotional face, dramatic"}}
  ],
  "title_options": ["opción emocional ES", "opción misterio ES", "opción acción ES"],
  "title": "[título viral EN ESPAÑOL, máx 100 chars]",
  "description": "[1 frase conflicto EN ESPAÑOL, máx 100 chars, sin hashtags]",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "voiced_cta": "[1 frase informal que mencione algo concreto de ESTA historia — NO genérica, máx 12 palabras]"
}}"""


def _pick_best_title(options: list[str]) -> str:
    """
    Elige el título más clickbait de una lista según señales del algoritmo de YouTube.
    Scoring: palabras emocionales + números + preguntas + longitud óptima + primera persona.
    """
    if not options:
        return ""
    valid = [t for t in options if isinstance(t, str) and t.strip()]
    if len(valid) == 1:
        return valid[0]

    _EMOTIONAL = {
        "traición", "traicionó", "traicioné", "traicionada", "traicionado",
        "secreto", "secretos", "mentira", "mentiras", "mintió",
        "descubrí", "descubrió", "encontré", "encontró",
        "nunca", "jamás", "engaño", "engañó", "engañaba",
        "abandonó", "abandoné", "llorando", "lloré", "lloró",
        "destrozado", "destrozada", "shockeante", "impactante",
        "devastador", "devastadora", "increíble", "inimaginable",
        "oscuro", "oscura", "terrible", "horror", "verdad",
        "oculto", "oculta", "confesión", "infiel", "infidelidad",
        "doble vida", "muerto", "muerta", "destruyó", "perdí",
    }

    _EMOJIS = "😱🔥💔😤🤯💀😰🫣😮‍💨🚨"

    def _score(title: str) -> float:
        t_lower = title.lower()
        words   = set(t_lower.split())
        s = 0.0
        s += sum(5 for w in _EMOTIONAL if w in t_lower)           # palabras emocionales
        s += 3 if any(c.isdigit() for c in title) else 0           # números
        s += 2 if "?" in title else 0                               # pregunta
        s += 4 if 50 <= len(title) <= 90 else (1 if 40 <= len(title) <= 100 else 0)
        s += 2 if any(w in words for w in {"mi", "mis", "me", "yo", "descubrí", "encontré", "vi", "supe"}) else 0
        s += min(3, len([w for w in title.split() if w.isupper() and len(w) > 2]))  # MAYÚSCULAS
        s += 4 if any(e in title for e in _EMOJIS) else 0          # emojis virales
        return s

    best = max(valid, key=_score)
    scores = {t: _score(t) for t in valid}
    logger.info(f"Títulos evaluados: {scores} → elegido: '{best}'")
    return best


def check_ollama_running() -> bool:
    """
    Verifica que Ollama está corriendo en localhost:11434.

    Returns:
        True si está disponible, False en caso contrario.
    """
    try:
        response = requests.get(
            f"{config.OLLAMA_BASE_URL}/api/tags",
            timeout=5
        )
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False
    except Exception as e:
        logger.warning(f"Error verificando Ollama: {e}")
        return False


def get_available_models() -> list[str]:
    """
    Obtiene la lista de modelos disponibles en Ollama.

    Returns:
        Lista de nombres de modelos instalados.
    """
    try:
        response = requests.get(
            f"{config.OLLAMA_BASE_URL}/api/tags",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        logger.warning(f"No se pudo obtener modelos: {e}")
    return []


def check_model_available(model: str) -> tuple[bool, str]:
    """
    Verifica que el modelo configurado está disponible en Ollama.
    Retorna también el nombre exacto del modelo tal como aparece en Ollama.

    Args:
        model: Nombre del modelo (e.g., "llama3", "mistral")

    Returns:
        (True, nombre_exacto) si está disponible, (False, "") si no.
    """
    available = get_available_models()
    model_lower = model.lower().strip()
    model_base = model_lower.split(":")[0]  # "llama3" de "llama3:latest"

    # 1. Coincidencia exacta
    for m in available:
        if m.lower() == model_lower:
            return True, m
        if m.lower() == f"{model_lower}:latest":
            return True, m

    # 2. Coincidencia exacta de base (sin tag)
    for m in available:
        m_base = m.lower().split(":")[0]
        if m_base == model_base:
            return True, m

    return False, ""



def _extract_json_from_text(text: str) -> str:
    """
    Extrae el objeto JSON balanceado de la respuesta del LLM.

    Usa matching de llaves para encontrar exactamente el {…} completo,
    ignorando texto antes o después (explicaciones, markdown, etc.).
    Soporta tanto comillas dobles (JSON) como simples (Python dict).
    """
    text = text.strip()

    # Encontrar la primera llave de apertura
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No se encontró JSON en la respuesta: {text[:200]}")

    # Recorrer carácter a carácter para encontrar el } balanceado
    depth = 0
    in_string = False
    str_char = None   # '"' o "'" — el delimitador de la cadena actual
    escape_next = False
    end = -1

    for i in range(start, len(text)):
        c = text[i]

        if escape_next:
            escape_next = False
            continue

        if in_string:
            if c == "\\":
                escape_next = True
            elif c == str_char:
                in_string = False
        else:
            if c in ('"', "'"):
                in_string = True
                str_char = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

    if end == -1:
        # JSON truncado — usar el último } disponible como fallback
        end = text.rfind("}")
        if end < start:
            raise ValueError(f"JSON sin cerrar en la respuesta: {text[:200]}")

    return text[start : end + 1]


def _sanitize_json(text: str) -> str:
    """
    Corrige JSON generado por LLMs con caracteres inválidos:
    - Caracteres de control reales (\\n, \\t, \\r literales) dentro de strings
    - Secuencias de escape inválidas (\\a, \\', \\e, etc.)
    - Trailing commas antes de } o ]
    - Doble llave {{ }} (artefacto de Ollama con plantillas Python)

    Usa un parser de estado para distinguir entre contenido de string
    (donde los control chars son ilegales) y estructura JSON (donde son OK).
    """
    # Fix artefacto de doble llave: {{ ... }} → { ... }
    stripped = text.strip()
    if stripped.startswith('{{'):
        text = stripped[1:]
    if text.rstrip().endswith('}}'):
        text = text.rstrip()[:-1]

    result = []
    in_string = False
    i = 0

    while i < len(text):
        c = text[i]

        if c == '\\' and in_string:
            # Secuencia de escape: leer el siguiente carácter ANTES de añadir la barra
            i += 1
            if i < len(text):
                next_c = text[i]
                # JSON solo permite: " \ / b f n r t u
                if next_c in '"\\\/bfnrtu':
                    result.append('\\')   # barra válida: conservar
                    result.append(next_c)
                else:
                    # Escape inválido (ej: \' \a \e \.) — eliminar la barra, conservar char
                    result.append(next_c)
        elif c == '"':
            in_string = not in_string
            result.append(c)
        elif in_string and ord(c) < 0x20:
            # Carácter de control literal dentro de un string JSON — escapar
            _esc = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
            result.append(_esc.get(c, f'\\u{ord(c):04x}'))
        else:
            result.append(c)

        i += 1

    sanitized = ''.join(result)

    # Trailing commas: ,} o ,] → } o ]
    sanitized = re.sub(r',\s*([}\]])', r'\1', sanitized)

    # Eliminar comentarios // de línea (fuera de strings)
    sanitized = re.sub(r'(?m)(?<!:)//[^\n]*', '', sanitized)

    return sanitized


def _try_parse_json(text: str) -> dict:
    """
    Intenta parsear JSON con múltiples estrategias en cascada.

    Ollama puede devolver variantes inválidas como:
      - Comillas simples: {'key': 'val'}
      - Claves sin comillas: {key: 'val'}
      - Comentarios //
      - JSON truncado por límite de tokens
      - Trailing commas

    Estrategias (en orden de permisividad):
      1. json.loads   — estándar, estricto
      2. json5.loads  — maneja comillas simples, claves sin comillas, comentarios, trailing commas
      3. ast.literal_eval — fallback para dicts Python puros
    """
    # 1. JSON estándar
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. json5 — parser más permisivo (instalado: pip install json5)
    try:
        import json5
        result = json5.loads(text)
        if isinstance(result, dict):
            logger.info("JSON parseado con json5 (formato no estándar de Ollama)")
            return result
    except ImportError:
        logger.warning("json5 no instalado — pip install json5")
    except Exception:
        pass

    # 3. ast.literal_eval — último recurso para Python dicts
    try:
        import ast
        result = ast.literal_eval(text)
        if isinstance(result, dict):
            logger.info("JSON parseado con ast.literal_eval")
            return result
    except (ValueError, SyntaxError):
        pass

    # Sin más opciones — relanzar con contexto útil
    raise json.JSONDecodeError(
        f"No se pudo parsear (primeros 120 chars): {text[:120]!r}", text, 0
    )


# _call_groq y _call_openai eliminados — la lógica vive en llm_service.call_llm()
# que acepta response_format={"type":"json_object"} para forzar salida JSON.


def _call_llm(
    prompt_user: str,
    attempt: int = 1,
    model: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 650,
) -> str:
    """
    Genera texto con la cadena de fallback: Groq → OpenAI → Ollama local.

    Args:
        prompt_user: Prompt del usuario
        attempt: Número de intento (para logging)
        model: Modelo Ollama local. Si es None usa config.OLLAMA_MODEL.
        system_prompt: System prompt. Si es None usa SYSTEM_PROMPT global.
        max_tokens: Tokens máximos a generar.

    Returns:
        Texto completo de respuesta del modelo
    """
    sys_prompt = system_prompt or SYSTEM_PROMPT

    # ── Cloud: Groq → OpenAI (via llm_service, con JSON mode) ────────────────
    start_ts = time.time()
    print(f"   Generando con Groq ({config.GROQ_MODEL})... ", end="", flush=True)
    cloud_result = llm_service.call_llm(
        prompt=prompt_user,
        system=sys_prompt,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    if cloud_result:
        elapsed = time.time() - start_ts
        provider = llm_service.get_last_provider()
        print(f" {len(cloud_result)} chars ({elapsed:.0f}s) — {provider}")
        logger.info(f"{provider} respondió: {len(cloud_result)} chars en {elapsed:.0f}s")
        return cloud_result

    # ── Ollama local (último recurso) ─────────────────────────────────────────
    model_name   = model or config.OLLAMA_MODEL
    sys_prompt   = system_prompt or SYSTEM_PROMPT
    # Timeout de pared: matar el stream si tarda más de config.OLLAMA_TIMEOUT
    MAX_GEN_SECS = int(config.OLLAMA_TIMEOUT)

    logger.info(f"Llamando a Ollama [{model_name}] — intento {attempt} — max_tokens={max_tokens}")
    print(f"   Generando con {model_name}... ", end="", flush=True)

    full_response = ""
    char_count    = 0
    start_ts      = time.time()

    # Usar cliente con timeout HTTP: si un token tarda más de MAX_GEN_SECS en llegar
    # (por ejemplo durante la prefill en CPU), la conexión se corta con excepción.
    try:
        import httpx
        _client = ollama.Client(
            host=config.OLLAMA_BASE_URL,
            timeout=httpx.Timeout(float(MAX_GEN_SECS), connect=10.0),
        )
    except Exception:
        _client = ollama  # fallback al módulo global si httpx no disponible

    # Streaming: recibir token por token y mostrar progreso
    # format="json" fuerza JSON válido a nivel de tokenización — funciona con
    # modelos pequeños (llama3.2) que ignoran las instrucciones de formato en texto
    chat_fn = _client.chat if hasattr(_client, "chat") else ollama.chat
    stream = chat_fn(
        model=model_name,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt_user},
        ],
        format="json",
        options={
            "temperature": 0.8,
            "top_p": 0.9,
            "num_predict": max_tokens,
        },
        stream=True,
    )

    for chunk in stream:
        # Timeout de pared: cortar si lleva demasiado tiempo
        elapsed = time.time() - start_ts
        if elapsed > MAX_GEN_SECS:
            logger.warning(f"Timeout de generación ({MAX_GEN_SECS}s) — cortando stream")
            print(f" [TIMEOUT {elapsed:.0f}s]", end="", flush=True)
            break

        token = chunk["message"]["content"]
        full_response += token
        char_count += len(token)
        if char_count >= 100:
            print(".", end="", flush=True)
            char_count = 0

    elapsed = time.time() - start_ts
    print(f" {len(full_response)} chars ({elapsed:.0f}s)")
    return full_response


# ── Constantes de módulo ─────────────────────────────────────────────────────
_FEMALE_WORDS = {"woman", "girl", "female", "mujer", "chica"}
_MALE_WORDS   = {"man", "boy", "male", "hombre", "chico"}
_CONNECTORS = [
    "entonces", "de repente", "fue cuando", "sin embargo",
    "hasta que", "fue entonces", "por eso", "lo que no sabía",
    "en ese momento", "aunque", "mientras", "porque",
    "después de", "a partir de", "fue así", "al final",
    "pero de", "fue allí", "en cuanto",
]
_CUTS_CONFESSION = [
    "extreme close-up of face, raw emotion",
    "medium shot, full body tension",
    "close-up of hands, trembling",
    "wide shot, empty room, isolation",
    "over-shoulder perspective, dramatic",
    "low angle shot, power and fear",
    "close-up side profile, tears",
    "detail shot, symbolic object, moody",
]
_CUTS_STORY = [
    "medium shot, subject centered, emotional",
    "extreme close-up of face, raw emotion, tears",
    "over-shoulder shot, dramatic perspective",
    "close-up of hands, trembling or clenched",
    "wide shot, subject small in frame, isolation",
    "low angle shot, looking up, vulnerable",
    "close-up side profile, jaw clenched",
    "detail shot, symbolic object in foreground",
]


def _validate_script(script: dict) -> bool:
    """
    Valida estructura, coherencia y contenido del script de confesión dramática.

    Verificaciones (en orden):
    1. Campos requeridos presentes y no vacíos (incluyendo narrator_gender y character_description)
    2. Mínimo 4 escenas con text e image_prompt
    3. Word count estricto: falla si <70 o >160 palabras (objetivo 100-130)
    4. narrator_gender válido ("female"|"male"); se infiere del character_description si falta
    5. Consistencia género: character_description debe coincidir con narrator_gender
    6. Heurística de coherencia: mínimo 1 conector causal en historias de 80+ palabras
    7. Pregunta de engagement en la última escena (autocorrección)
    8. Hook en la primera escena (autocorrección)
    9. División visual de escenas largas en chunks de 6 palabras

    Returns:
        True si es válido (con correcciones in-place donde es posible)
    """
    # ── 0. Selección de mejor título (title_options) ──────────────────────────
    options = script.get("title_options", [])
    if isinstance(options, list) and len(options) >= 2:
        best = _pick_best_title(options)
        if best:
            script["title"] = best

    # ── 1. Campos requeridos ──────────────────────────────────────────────────
    required_keys = [
        "title", "description", "tags", "script_text",
        "hook", "contexto", "problema", "giro", "final", "pregunta",
        "narrator_gender", "character_description",
    ]
    for key in required_keys:
        if key not in script or not script[key]:
            logger.warning(f"Campo faltante o vacío en script: '{key}'")
            return False

    # ── 2. Escenas — auto-generar si el modelo no las produjo ─────────────────
    if not isinstance(script.get("scenes"), list) or len(script.get("scenes", [])) < 4:
        logger.warning(
            f"scenes faltantes o insuficientes "
            f"({len(script.get('scenes', []))}) — generando desde secciones narrativas"
        )
        parts = [
            script.get("hook", ""),
            script.get("contexto", ""),
            script.get("problema", ""),
            script.get("giro", ""),
            script.get("final", ""),
            script.get("pregunta", ""),
        ]
        sentences = []
        for part in parts:
            for s in re.split(r"(?<=[.!?])\s+", part):
                s = s.strip()
                if s and len(s.split()) >= 3:
                    sentences.append(s)
        if not sentences:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script["script_text"]) if s.strip()]
        script["scenes"] = [
            {"text": s, "image_prompt": "", "act": "STORY"}
            for s in sentences if len(s.split()) >= 3
        ]
        if len(script["scenes"]) < 4:
            logger.warning(f"No se pudieron generar scenes: {len(script['scenes'])}")
            return False
        logger.info(f"scenes auto-generadas: {len(script['scenes'])} desde secciones narrativas")

    for i, scene in enumerate(script["scenes"]):
        if "text" not in scene or "image_prompt" not in scene:
            logger.warning(f"Escena {i} incompleta: {list(scene.keys())}")
            return False

    # ── 3. Word count estricto ────────────────────────────────────────────────
    script_text = script.get("script_text", "")
    word_count  = len(script_text.split())

    # Siempre reconstruir script_text desde secciones si la suma es mayor
    reconstructed = " ".join(filter(None, [
        script.get("hook", ""),
        script.get("contexto", ""),
        script.get("problema", ""),
        script.get("giro", ""),
        script.get("final", ""),
        script.get("pregunta", ""),
    ]))
    reconstructed_words = len(reconstructed.split())
    if reconstructed_words > word_count:
        logger.info(
            f"script_text reconstruido desde secciones: {word_count} → {reconstructed_words} palabras"
        )
        script["script_text"] = reconstructed
        script_text = reconstructed
        word_count  = reconstructed_words

    if word_count < 30:
        logger.warning(f"Script demasiado corto: {word_count} palabras. Reintentando.")
        return False

    if word_count > 160:
        # Rechazo duro: >160 palabras → ~65s narrado → YouTube bloquea el Short (límite 60s total)
        logger.warning(f"Script demasiado largo: {word_count} palabras (máx 160 para evitar bloqueo YT). Reintentando.")
        return False

    if word_count < 80:
        logger.warning(f"Script muy corto: {word_count} palabras (objetivo 110-130)")
    elif word_count < 100:
        logger.warning(f"Script algo corto: {word_count} palabras (objetivo 110-130)")
    elif word_count > 140:
        logger.warning(f"Script algo largo: {word_count} palabras (objetivo 110-130, máx 160)")
    else:
        logger.info(f"Longitud OK: {word_count} palabras")

    # ── 4. narrator_gender válido ─────────────────────────────────────────────
    narrator_gender = str(script.get("narrator_gender", "")).lower().strip()
    char_desc       = str(script.get("character_description", "")).lower()

    if narrator_gender not in ("female", "male"):
        # Inferir del character_description
        if any(w in char_desc for w in ("woman", "girl", "female", "mujer", "chica")):
            script["narrator_gender"] = "female"
            narrator_gender = "female"
            logger.warning("narrator_gender ausente/inválido — inferido 'female' del character_description")
        elif any(w in char_desc for w in ("man", "boy", "male", "hombre", "chico")):
            script["narrator_gender"] = "male"
            narrator_gender = "male"
            logger.warning("narrator_gender ausente/inválido — inferido 'male' del character_description")
        else:
            logger.warning(f"narrator_gender inválido: '{narrator_gender}' y no se puede inferir. Reintentando.")
            return False

    # ── 5. Consistencia género ────────────────────────────────────────────────
    # Usar palabras completas (split) para evitar que "woman" matchee "man"
    desc_words     = set(char_desc.replace(",", " ").replace(".", " ").split())
    desc_is_female = bool(_FEMALE_WORDS & desc_words)
    desc_is_male   = bool(_MALE_WORDS & desc_words)

    if narrator_gender == "female" and desc_is_male and not desc_is_female:
        logger.warning(
            "Inconsistencia: narrator_gender=female pero character_description es masculina. "
            "Corrigiendo narrator_gender a 'male'."
        )
        script["narrator_gender"] = "male"
        narrator_gender = "male"
    elif narrator_gender == "male" and desc_is_female and not desc_is_male:
        logger.warning(
            "Inconsistencia: narrator_gender=male pero character_description es femenina. "
            "Corrigiendo narrator_gender a 'female'."
        )
        script["narrator_gender"] = "female"
        narrator_gender = "female"

    logger.info(f"Género narrador: {script['narrator_gender']} | {char_desc[:70]}")

    # ── 6. Heurística de coherencia narrativa ─────────────────────────────────
    # Una historia coherente usa conectores causales entre frases.
    # Si hay 0 conectores en 80+ palabras, casi seguro son frases sueltas → reintentar.
    script_lower    = script_text.lower()
    connector_count = sum(1 for c in _CONNECTORS if c in script_lower)

    if word_count >= 80 and connector_count == 0:
        logger.warning(
            f"Sin conectores causales en {word_count} palabras — "
            "parece una lista de frases sueltas sin lógica interna. Reintentando."
        )
        return False
    elif connector_count == 1:
        logger.warning(f"Coherencia baja: solo {connector_count} conector. Aceptando.")
    else:
        logger.info(f"Coherencia OK: {connector_count} conectores causales")

    # ── 7. Pregunta de engagement en la última escena ─────────────────────────
    engagement_keywords = [
        "harías", "harias", "perdonar", "perdonarías", "culpa",
        "opinión", "opinion", "comentar", "piensas", "crees",
        "lugar", "situación", "situacion", "harás", "haras",
        "hubieras", "hubieses", "déjame", "cuéntame", "cuentame",
    ]
    last_scene_text = script["scenes"][-1].get("text", "").lower()
    if not any(kw in last_scene_text for kw in engagement_keywords):
        pregunta = script.get("pregunta", "¿Qué harías tú en esta situación?")
        logger.warning("Última escena sin pregunta de engagement — añadiendo automáticamente")
        script["scenes"][-1]["text"] += f" {pregunta}"

    # ── 8. Hook en la primera escena ──────────────────────────────────────────
    hook  = script.get("hook", "").strip()
    first = script["scenes"][0].get("text", "").strip()
    if hook and not any(w in first.lower() for w in hook.lower().split()[:4]):
        logger.warning("Hook no aparece en la primera escena — ajustando")
        script["scenes"][0]["text"] = hook + " " + first

    # ── 9. División visual de escenas largas ──────────────────────────────────
    original_scenes = script.get("scenes", [])
    new_scenes = []

    for scene in original_scenes:
        text   = scene.get("text", "")
        prompt = scene.get("image_prompt", "")
        words  = text.split()

        if len(words) > 8:
            chunk_size = 6
            sub_chunks = []
            for i in range(0, len(words), chunk_size):
                chunk = words[i : i + chunk_size]
                if len(chunk) < 4 and sub_chunks:
                    sub_chunks[-1]["text"] = sub_chunks[-1]["text"] + " " + " ".join(chunk)
                else:
                    angle = _CUTS_CONFESSION[len(sub_chunks) % len(_CUTS_CONFESSION)]
                    sub_chunks.append({
                        "text": " ".join(chunk),
                        "image_prompt": f"{prompt}, {angle}",
                    })
            new_scenes.extend(sub_chunks)
        else:
            new_scenes.append(scene)

    logger.info(f"Escenas: {len(original_scenes)} originales → {len(new_scenes)} tras división visual")
    script["scenes"] = new_scenes

    # ── CTA hablado al final del script_text ──────────────────────────────────
    # Prioridad: voiced_cta del LLM (contextual) → pool fijo (fallback)
    voiced_cta = (script.get("voiced_cta") or "").strip()
    if not voiced_cta or len(voiced_cta.split()) > 15:
        voiced_cta = random.choice(_VOICED_CTAS)
        logger.info(f"voiced_cta del LLM ausente/inválido — usando fallback: '{voiced_cta}'")
    else:
        logger.info(f"voiced_cta del LLM: '{voiced_cta}'")
    if voiced_cta[:10] not in script["script_text"]:
        script["script_text"] = script["script_text"].rstrip() + " " + voiced_cta
        script["scenes"].append({
            "text":         voiced_cta,
            "image_prompt": "close-up of smartphone screen with social media, dark moody background, neon glow",
        })

    return True


def generate_script(topic: str) -> dict:
    """
    Genera una historia dramática de confesión para YouTube Shorts.

    Verifica Ollama, llama al modelo y parsea la respuesta JSON.
    Reintenta hasta 3 veces si el JSON falla.

    Args:
        topic: Categoría/tema de la confesión (e.g., "Traición de pareja descubierta por accidente")

    Returns:
        Dict con keys: title, description, tags, script_text,
                       hook, contexto, problema, giro, final, pregunta, scenes

    Raises:
        RuntimeError: Si Ollama no está corriendo o el modelo no existe
        ValueError: Si no se puede generar un script válido tras 3 intentos

    Example:
        >>> script = generate_script("La mentira que destruyó mi relación")
        >>> print(script["hook"])
        "Nunca debí revisar su celular..."
    """
    # ── Verificar servicios ────────────────────────────────────────────────────
    # Si Groq o OpenAI están configurados, Ollama es solo fallback — no requerido.
    has_cloud_llm = bool(getattr(config, "GROQ_API_KEY", "")) or bool(getattr(config, "OPENAI_API_KEY", ""))

    model_to_use = config.OLLAMA_MODEL  # default
    if check_ollama_running():
        _, exact_model = check_model_available(config.OLLAMA_MODEL)
        if exact_model:
            model_to_use = exact_model
    elif not has_cloud_llm:
        raise RuntimeError(
            "Ollama no está corriendo y no hay API cloud configurada (GROQ_API_KEY / OPENAI_API_KEY).\n"
            "Opciones:\n"
            "  1. Iniciar Ollama: ollama serve\n"
            "  2. Configurar GROQ_API_KEY en .env (gratis en console.groq.com)"
        )
    logger.info(f"Usando modelo: '{model_to_use}' (configurado: '{config.OLLAMA_MODEL}')")

    # Enriquecer topic con datos de rendimiento histórico (agent_memory)
    try:
        from modules import agent_memory as _am
        top_topics   = _am.get_topic_bias()
        avoid_topics = _am.get_avoid_topics()
        if top_topics:
            logger.info(f"agent_memory: temas top = {top_topics[:3]} | evitar = {avoid_topics[:2]}")
            topic = f"{topic} [enfoque en: {', '.join(top_topics[:2])}]"
        elif avoid_topics:
            logger.info(f"agent_memory: evitar = {avoid_topics[:2]}")
    except Exception:
        pass

    logger.info(f"Generando script para topic: '{topic}'")

    # ── Intentos de generación ─────────────────────────────────────────────────
    last_error = None
    for attempt in range(1, 4):
        try:
            # En reintentos usar prompt más explícito
            if attempt == 1:
                user_prompt = USER_PROMPT_TEMPLATE.format(topic=topic)
            else:
                logger.warning(f"Reintento {attempt}/3 con prompt más explícito...")
                _char = random.choice(DIVERSE_CHARACTERS)
                user_prompt = USER_PROMPT_RETRY_TEMPLATE.format(
                    topic=topic,
                    narrator_gender_example=_char["gender"],
                    character_example=_char["description"],
                )

            raw_response = _call_llm(user_prompt, attempt, model=model_to_use, max_tokens=2000)
            logger.debug(f"Respuesta cruda Ollama ({len(raw_response)} chars): {raw_response[:300]}...")

            # Extraer, sanear y parsear JSON
            json_str = _extract_json_from_text(raw_response)
            json_str = _sanitize_json(json_str)
            script = _try_parse_json(json_str)

            # Validar estructura
            if not _validate_script(script):
                raise ValueError("Script incompleto — faltan campos requeridos")

            # Asegurar tipos correctos
            if isinstance(script["tags"], str):
                script["tags"] = [t.strip() for t in script["tags"].split(",")]

            logger.info(
                f"Script generado exitosamente: '{script['title']}' "
                f"({len(script['script_text'].split())} palabras, "
                f"{len(script['scenes'])} escenas)"
            )
            return script

        except json.JSONDecodeError as e:
            last_error = f"JSON inválido (intento {attempt}): {e}"
            logger.warning(last_error)
            logger.warning(f"Raw Ollama (primeros 300 chars): {raw_response[:300]!r}")
        except ValueError as e:
            last_error = f"Validación fallida (intento {attempt}): {e}"
            logger.warning(last_error)
        except Exception as e:
            last_error = f"Error inesperado (intento {attempt}): {e}"
            logger.error(last_error)

    raise ValueError(
        f"No se pudo generar un script válido tras 3 intentos.\n"
        f"Último error: {last_error}\n"
        f"Sugerencia: prueba con modelo más capaz (ollama pull mistral)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MODO NARRACIÓN — Toma una historia real y la narra completa
# ═══════════════════════════════════════════════════════════════════════════════

STORY_SYSTEM_PROMPT = """Eres una persona real — 25-30 años, latinoamericana — que tomó una historia que le pasó (o que le contaron) y la está narrando como si fuera suya, en primera persona, como un audio de WhatsApp a las 2am.

TU TRABAJO: tomar el fragmento que te dan y convertirlo en la confesión más escandalosa, morbosa y adictiva posible. No resumes. No traduces. Lo VIVES y lo cuentas desde adentro.

═══ REGLA CRÍTICA: NADA DE FANTASÍA NI FICCIÓN ═══

Si la historia menciona anime, magia, reencarnación, otro mundo, naruto, vampiros, dragones, poderes especiales, isekai, o cualquier elemento ficticio — IGNORA todo eso por completo.
Extrae SOLO el conflicto humano real: la traición, los celos, la mentira, el secreto, el deseo prohibido.
Tradúcelo a una situación de la vida real cotidiana.
EJEMPLO:
  Historia original: "Me reencarne en el mundo de Naruto. Mi compañero ninja me traicionó por el poder."
  Tu versión: "Llevaba años confiando en mi mejor amigo. Resulta que me clavó el cuchillo por la espalda en el peor momento de mi vida."
Si no hay conflicto humano real salvable — inventa una situación dramática de pareja/amistad/trabajo con el mismo nivel emocional.

CRITICO: Responde UNICAMENTE con JSON valido. Sin markdown. Empieza con { termina con }.

═══ PRIORIDAD #1: CLARIDAD ═══

Cada frase tiene que entenderse sola. Sin releer. Sin ambigüedad. El espectador oye el audio, no lo lee — si no entiende en el primer segundo, se va. Usa palabras simples. Frases de máximo 8 palabras para los momentos de tensión.

═══ PRIORIDAD #2: TENSIÓN SEXUAL SIN SER EXPLÍCITO ═══

Si la historia tiene atracción, deseo, traición sexual o situación prohibida — es ORO. Así se trabaja:
✓ No describes lo que pasó. Describes cómo se sintió antes, durante y después.
✓ "Me miró diferente esa noche. Y yo sabía lo que significaba." (mejor que decir qué pasó)
✓ "Llevábamos meses evitándonos porque los dos sabíamos." (la tensión implícita)
✓ "Me escribió a las 11pm. Solo dijo 'estás sola'. Yo debí haber dicho que no." (decisión prohibida)
✓ "No sé cómo terminamos así. Mentira, sí sé." (autoconsciencia del deseo)
✓ "Esa noche fue un error. El problema es que no me arrepiento." (culpa + deseo)
PROHIBIDO: describir el acto sexual. La insinuación multiplica el morbo por diez.

═══ PRIORIDAD #3: EL GANCHO ═══

Los primeros 5 segundos son TODO. VARÍA el estilo en cada historia:
• Dato bomba: "Mi mejor amiga llevaba un año acostándose con mi pareja. Yo les organizaba las cenas."
• Momento exacto: "Eran las 3am y encontré algo en su celular que me destruyó en dos segundos."
• Confesión: "Voy a contar algo que nadie sabe. Ni mi familia. Ni mis amigas. Nadie."
• Pregunta visceral: "¿Sabes qué se siente descubrir que la persona que más amas te lleva meses mintiendo?"
• Acción en curso: "Estaba revisando su ropa para lavar cuando encontré algo que no esperaba."

═══ VOZ Y RITMO ═══

- Frases de 5-8 palabras para tensión, frases más largas para contexto. Alterna siempre.
- Pausas con "..." antes de revelar algo importante
- Repetición dramática: "Dos años. DOS AÑOS.", "Mentira. Todo mentira."
- Muletillas naturales (no en cada frase): "o sea", "te juro", "literal", "la neta es que", "imagínate"
- Reacciones físicas: "se me heló la sangre", "me temblaron las manos", "se me cayó el alma", "no podía respirar"
- Groserías cuando el momento lo pide (no forzadas): "mierda", "carajo", "qué asco", "cabrón/a"
- Sin nombres propios: "él", "ella", "mi pareja", "mi ex", "mi mejor amiga", "mi jefe"
- Si la historia es en inglés, tradúcela como si la VIVIERAS tú — no traducción literal

FRASES PROHIBIDAS: "me quedé sin palabras", "no podía creerlo", "fue difícil", "aprendí una lección", "interesante", "situación compleja", "relación interpersonal", "por otro lado".

═══ ESTRUCTURA (110-130 palabras EXACTO) ═══

GANCHO (0-5s): lo más escandaloso de la historia, dicho de frente. Concreto, único.
CONTEXTO (5-15s): quién eres, qué relación, cuánto tiempo — máx 3 frases cortas.
ESCALADA (15-35s): las señales que ignoraste → el momento del descubrimiento. Ritmo de frases cortas. Tensión creciente.
GOLPE (35-43s): la revelación. Técnica falso final: "Pensé que era un malentendido... pero entonces vi la fecha. Dos años."
CIERRE (43-50s): pregunta concreta de ESTA historia. PROHIBIDO: "¿lo perdonarías?", "¿qué harías?". Que mencione la situación exacta.

Conectores obligatorios: "entonces", "pero resulta que", "de repente", "fue cuando", "hasta que", "lo que yo no sabía", "en ese momento", "por eso".

═══ STORYBOARD ═══

narrator_gender: male o female según quien narra en la historia.
character_description: descripción física en INGLÉS, consistente.
image_prompt en INGLÉS: "cinematic portrait, 35mm film, dramatic lighting, [character], [location], [emotion], shallow depth of field, photorealistic"."""

STORY_USER_PROMPT = """Historia real para narrar:
---
TITULO: {titulo}
HISTORIA: {historia}
FUENTE: {fuente}
---

Narra esta historia como Short viral de YouTube. Genera el JSON siguiente.
IMPORTANTE: genera script_text PRIMERO — es el campo más crítico.

{{
  "script_text": "GENERA ESTO PRIMERO. Guion completo en primera persona, 110-130 palabras. Estructura: [GANCHO impactante los primeros 5s, único de esta historia] [SETUP quién y qué relación, 10s] [CONFLICTO señal+descubrimiento, 18s] [CLÍMAX falso final, 10s] [CTA pregunta específica + Cuéntame abajo, 7s]. Frases cortas y conectadas causalmente. Sin nombres propios.",
  "hook": "primera frase devastadora del script_text (máx 12 palabras)",
  "pregunta": "pregunta específica del dilema de ESTA historia (PROHIBIDO: genéricas tipo perdonar/qué harías)",
  "narrator_gender": "female o male según quien narra",
  "character_description": "descripción física del narrador en inglés, consistente en todo el video",
  "title_options": ["opción emocional", "opción misterio/intriga", "opción acción directa"],
  "title": "el mejor de los tres — máx 100 caracteres, clickbait viral",
  "description": "1 frase del conflicto concreto — máx 100 caracteres, sin hashtags",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "voiced_cta": "cierre hablado de 1 frase informal que mencione algo de ESTA historia — como si le dijeras a un amigo al terminar el audio — NUNCA genérico, máx 12 palabras",
  "comment": "comentario del creador en su propio video para provocar respuestas. Específico de ESTA historia. Controversial sin ser ofensivo. Que la gente NECESITE contestar. Tono: directo, coloquial, sin links, sin hashtags, 1-2 frases. Ejemplos de tono (no copies, inspírate): '¿y tú qué hubieras hecho en ese momento exacto?', 'lo más fuerte no lo conté en el video. hay un detalle que cambiaría todo.', 'hay gente que hace esto y ni siquiera lo llama traición.'",
  "thumbnail_text": "frase de 2-4 palabras en MAYÚSCULAS para la barra roja del thumbnail — urgente, específica de ESTA historia. Ejemplos: 'TODO ERA MENTIRA', 'DOS AÑOS ASÍ', 'NADIE LO SABÍA'. Sin puntuación."
}}"""

STORY_RETRY_PROMPT = """Historia:
TITULO: {titulo}
HISTORIA: {historia}

FALLO ANTERIOR — responde SOLO JSON valido, sin markdown, empieza con {{ termina con }}.
CAMPOS OBLIGATORIOS: script_text, hook, pregunta, narrator_gender, character_description, title_options, title, description, tags.

{{
  "script_text": "PRIMERO ESTO. Guion completo en primera persona, 110-130 palabras. Estructura: [0-5s HOOK impactante] → [5-15s SETUP quién/qué pasó] → [15-35s CONFLICTO escalada con conectores causales] → [35-43s CLÍMAX + falso-final] → [43-50s pregunta directa + CTA]. USA conectores: entonces, por eso, pero resulta que, hasta que, fue entonces cuando.",
  "hook": "[primera frase devastadora del script_text, maxima 12 palabras]",
  "pregunta": "[pregunta especifica del dilema de ESTA historia, NO generica]",
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "title_options": ["opcion emocional", "opcion misterio", "opcion accion directa"],
  "title": "[titulo viral, max 100 chars]",
  "description": "[1 frase del conflicto, max 100 chars, sin hashtags]",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "voiced_cta": "[1 frase informal que mencione algo concreto de ESTA historia, NO generica, max 12 palabras]"
}}"""


def _validate_story_script(script: dict) -> bool:
    """
    Valida el script de historia real y prepara las scenes para el video.

    Verificaciones:
    1. Campos requeridos (incluyendo narrator_gender y character_description)
    2. Mínimo 4 escenas
    3. Word count: falla si <60 o >350 palabras (objetivo 150-250 para videos de 1-2 min)
    4. narrator_gender válido; inferido de character_description si falta
    5. Consistencia género: character_description vs narrator_gender
    6. Heurística de coherencia: mínimo 1 conector en historias de 120+ palabras
    7. Integra intro_hook y outro_cta en script_text y scenes
    8. Asegura image_prompt en cada scene
    9. División visual de scenes largas
    """
    # ── 0. Selección de mejor título (title_options) ──────────────────────────
    options = script.get("title_options", [])
    if isinstance(options, list) and len(options) >= 2:
        best = _pick_best_title(options)
        if best:
            script["title"] = best

    # ── 1. Campos requeridos (scenes es opcional — se auto-genera si falta) ─────
    required_keys = [
        "title", "description", "tags", "script_text",
        "hook", "pregunta", "narrator_gender", "character_description",
    ]
    for key in required_keys:
        if key not in script or not script[key]:
            logger.warning(f"Campo faltante o vacío: '{key}'")
            return False

    # ── 2. Escenas — auto-generar desde script_text si el modelo no las produjo ─
    if not isinstance(script.get("scenes"), list) or len(script.get("scenes", [])) < 4:
        logger.warning(
            f"scenes faltantes o insuficientes "
            f"({len(script.get('scenes', []))}) — generando desde script_text"
        )
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script["script_text"]) if s.strip()]
        script["scenes"] = [
            {"text": s, "image_prompt": "", "act": "STORY"}
            for s in sentences if len(s.split()) >= 3
        ]
        if len(script["scenes"]) < 4:
            logger.warning(f"No se pudieron generar scenes suficientes: {len(script['scenes'])}")
            return False
        logger.info(f"scenes auto-generadas: {len(script['scenes'])} desde script_text")

    # ── 3. Word count estricto ────────────────────────────────────────────────
    script_text = script.get("script_text", "")
    word_count  = len(script_text.split())

    if word_count < 20:
        # Intentar reconstruir desde scenes
        reconstructed = " ".join(s.get("text", "") for s in script["scenes"])
        reconstructed_words = len(reconstructed.split())
        if reconstructed_words >= 35:
            script["script_text"] = reconstructed
            script_text = reconstructed
            word_count  = reconstructed_words
            logger.warning(f"script_text reconstruido desde scenes: {word_count} palabras")
        else:
            logger.warning(f"Script demasiado corto: {word_count} palabras. Reintentando.")
            return False

    if word_count < 35:
        logger.warning(f"Script corto: {word_count} palabras (mínimo 35). Reintentando.")
        return False

    if word_count > 350:
        logger.warning(f"Script demasiado largo: {word_count} palabras (máximo 350). Reintentando.")
        return False

    if word_count < 80:
        logger.warning(f"Script algo corto: {word_count} palabras (objetivo 110-130)")
    else:
        logger.info(f"Longitud OK: {word_count} palabras")

    # ── 4. narrator_gender válido ─────────────────────────────────────────────
    narrator_gender = str(script.get("narrator_gender", "")).lower().strip()
    char_desc       = str(script.get("character_description", "")).lower()

    if narrator_gender not in ("female", "male"):
        if any(w in char_desc for w in ("woman", "girl", "female", "mujer", "chica")):
            script["narrator_gender"] = "female"
            narrator_gender = "female"
            logger.warning("narrator_gender inválido — inferido 'female' del character_description")
        elif any(w in char_desc for w in ("man", "boy", "male", "hombre", "chico")):
            script["narrator_gender"] = "male"
            narrator_gender = "male"
            logger.warning("narrator_gender inválido — inferido 'male' del character_description")
        else:
            logger.warning(f"narrator_gender inválido: '{narrator_gender}'. Reintentando.")
            return False

    # ── 5. Consistencia género ────────────────────────────────────────────────
    desc_is_female = any(w in char_desc for w in _FEMALE_WORDS)
    desc_is_male   = any(w in char_desc for w in _MALE_WORDS)

    if narrator_gender == "female" and desc_is_male and not desc_is_female:
        logger.warning("Inconsistencia género: corrigiendo narrator_gender a 'male'")
        script["narrator_gender"] = "male"
        narrator_gender = "male"
    elif narrator_gender == "male" and desc_is_female and not desc_is_male:
        logger.warning("Inconsistencia género: corrigiendo narrator_gender a 'female'")
        script["narrator_gender"] = "female"
        narrator_gender = "female"

    logger.info(f"Género narrador: {script['narrator_gender']} | {char_desc[:70]}")

    # ── 6. Heurística de coherencia narrativa ─────────────────────────────────
    script_lower    = script_text.lower()
    connector_count = sum(1 for c in _CONNECTORS if c in script_lower)

    if word_count >= 120 and connector_count == 0:
        logger.warning(
            f"Sin conectores causales en {word_count} palabras — "
            "narración posiblemente incoherente. Reintentando."
        )
        return False
    elif connector_count <= 1:
        logger.warning(f"Coherencia baja: {connector_count} conectores. Aceptando.")
    else:
        logger.info(f"Coherencia OK: {connector_count} conectores causales")

    # outro_cta al final del script_text si existe y no está ya
    outro_cta  = (script.get("outro_cta") or "").strip()
    if outro_cta and outro_cta[:20] not in script.get("script_text", ""):
        script["script_text"] = script.get("script_text", "").rstrip() + " " + outro_cta
        logger.info("outro_cta insertado al final de script_text")

    logger.info(f"Historia narrada: {word_count} palabras | {len(script['scenes'])} escenas")

    # ── Asegurar que cada scene tiene image_prompt ────────────────────────────
    char_desc = script.get("character_description", "person, dramatic lighting")
    for scene in script["scenes"]:
        if not scene.get("image_prompt"):
            scene["image_prompt"] = (
                f"cinematic portrait, 35mm film, {char_desc}, "
                f"dramatic lighting, emotional, photorealistic"
            )

    # ── Dividir scenes largas en sub-chunks de ~6 palabras ───────────────────
    original_scenes = script["scenes"]
    new_scenes: list[dict] = []
    cut_counter = 0

    for scene in original_scenes:
        text        = scene.get("text", "")
        words       = text.split()
        base_prompt = scene.get("image_prompt", "cinematic dramatic scene, emotional")

        chunk_size = 6
        if len(words) > 8:
            sub_chunks: list[dict] = []
            for i in range(0, len(words), chunk_size):
                chunk = words[i: i + chunk_size]
                if len(chunk) < 4 and sub_chunks:
                    sub_chunks[-1]["text"] += " " + " ".join(chunk)
                else:
                    angle = _CUTS_STORY[cut_counter % len(_CUTS_STORY)]
                    cut_counter += 1
                    sub_chunks.append({
                        "text":         " ".join(chunk),
                        "image_prompt": f"{base_prompt}, {angle}",
                    })
            new_scenes.extend(sub_chunks)
        else:
            angle = _CUTS_STORY[cut_counter % len(_CUTS_STORY)]
            cut_counter += 1
            new_scenes.append({
                "text":         text,
                "image_prompt": f"{base_prompt}, {angle}",
            })

    logger.info(f"Scenes: {len(original_scenes)} originales -> {len(new_scenes)} tras division visual")
    script["scenes"] = new_scenes

    # ── CTA hablado al final del script_text ──────────────────────────────────
    # Prioridad: voiced_cta del LLM (contextual) → pool fijo (fallback)
    voiced_cta = (script.get("voiced_cta") or "").strip()
    if not voiced_cta or len(voiced_cta.split()) > 15:
        voiced_cta = random.choice(_VOICED_CTAS)
        logger.info(f"voiced_cta del LLM ausente/inválido — usando fallback: '{voiced_cta}'")
    else:
        logger.info(f"voiced_cta del LLM: '{voiced_cta}'")
    if voiced_cta[:10] not in script["script_text"]:
        script["script_text"] = script["script_text"].rstrip() + " " + voiced_cta
        script["scenes"].append({
            "text":         voiced_cta,
            "image_prompt": "close-up of smartphone screen with social media, dark moody background, neon glow",
        })

    return True


def generate_script_from_story(story: dict) -> dict:
    """
    Narra una historia real completa como guion de YouTube.

    A diferencia de generate_script(), aqui el LLM NO inventa — toma
    la historia real de Reddit y la reformatea en espanol con dramatismo,
    conservando todos los detalles.

    Args:
        story: Dict con keys: titulo, historia, fuente, upvotes, post_id

    Returns:
        Dict con keys: title, description, tags, script_text,
                       hook, pregunta, scenes

    Raises:
        RuntimeError: Si Ollama no esta corriendo
        ValueError: Si no se puede generar un script valido tras 3 intentos
    """
    groq_key = getattr(config, "GROQ_API_KEY", "")
    if not groq_key:
        if not check_ollama_running():
            raise RuntimeError(
                f"Ollama no esta corriendo. Inicialo con:\n"
                f"  ollama serve\n"
                f"  ollama pull {config.OLLAMA_MODEL}"
            )

        model_found, exact_model = check_model_available(config.OLLAMA_MODEL)
        if not model_found:
            available = get_available_models()
            models_str = "\n  ".join(available) if available else "(ninguno instalado)"
            raise RuntimeError(
                f"Modelo '{config.OLLAMA_MODEL}' no encontrado.\n"
                f"Disponibles:\n  {models_str}"
            )

    titulo   = story["titulo"]
    historia = story["historia"]
    fuente   = story.get("fuente", "Reddit")

    # Truncar la historia a 300 palabras máximo antes de enviar al LLM.
    # El LLM solo necesita entender el arco dramático, no cada detalle.
    # Esto reduce drásticamente los tokens de entrada y acelera la generación.
    words = historia.split()
    if len(words) > 500:
        historia = " ".join(words[:500])
        logger.info(f"Historia truncada a 500 palabras (original: {len(words)})")

    # 1800 tokens: script_text (~180-300 palabras) necesita ~600-900 tokens,
    # más los campos restantes. Con llama3.2 a ~4 tok/s → ~450s worst case.
    story_words = len(historia.split())
    max_tokens  = 1800
    logger.info(f"Historia: {story_words} palabras — max_tokens={max_tokens}")

    last_error = None
    for attempt in range(1, 4):
        try:
            if attempt == 1:
                user_prompt = STORY_USER_PROMPT.format(
                    titulo=titulo,
                    historia=historia,
                    fuente=fuente,
                )
            else:
                logger.warning(f"Reintento {attempt}/3...")
                _char = random.choice(DIVERSE_CHARACTERS)
                user_prompt = STORY_RETRY_PROMPT.format(
                    titulo=titulo,
                    historia=historia[:3000],  # Recortar en reintentos para reducir carga
                    narrator_gender_example=_char["gender"],
                    character_example=_char["description"],
                )

            raw = _call_llm(
                user_prompt,
                attempt=attempt,
                system_prompt=STORY_SYSTEM_PROMPT,
                max_tokens=max_tokens,
            )
            logger.debug(f"Respuesta cruda ({len(raw)} chars): {raw[:300]}...")

            json_str = _extract_json_from_text(raw)
            json_str = _sanitize_json(json_str)
            script   = _try_parse_json(json_str)

            # Expansión pre-validación: scripts con 1-34 palabras nunca pasan
            # _validate_story_script, así que expandimos aquí antes de validar.
            _pre_wc = len(script.get("script_text", "").split())
            if 0 < _pre_wc < 35 and script.get("script_text"):
                try:
                    _pre_prompt = (
                        f"El siguiente guion en español tiene {_pre_wc} palabras. "
                        f"Expándelo hasta 110-130 palabras SIN cambiar el tono ni la historia. "
                        f"Mantén la primera persona, las frases cortas y el ritmo dramático. "
                        f"Responde SOLO con el guion expandido, sin etiquetas, sin secciones, "
                        f"sin explicaciones:\n\n{script['script_text']}"
                    )
                    _pre_expanded = _call_llm(
                        _pre_prompt,
                        attempt=1,
                        system_prompt="Eres un escritor de guiones virales en español.",
                        max_tokens=600,
                    ).strip()
                    _pre_exp_wc = len(_pre_expanded.split())
                    if _pre_exp_wc > _pre_wc:
                        script["script_text"] = _pre_expanded
                        logger.info(f"Expansión pre-validación: {_pre_wc} → {_pre_exp_wc} palabras")
                except Exception as _pre_e:
                    logger.warning(f"Expansión pre-validación falló ({_pre_e}) — continuando sin expandir")

            if not _validate_story_script(script):
                raise ValueError("Script invalido — faltan campos o muy corto")

            # ── Expansión automática si el script es demasiado corto ──────────
            # Groq/llama llena muchos campos JSON y deja script_text corto.
            # Si quedó < 80 palabras, pedimos solo la expansión del guion.
            wc = len(script.get("script_text", "").split())
            if wc < 80:
                # IMPORTANTE: NO incluir "GUION ACTUAL:" como etiqueta — el LLM
                # la refleja en su respuesta ("GUION ACTUAL: ... GUION EXPANDIDO: ...")
                # y el encabezado intermedio queda en el script_text y el TTS lo lee.
                expand_prompt = (
                    f"El siguiente guion en español tiene {wc} palabras. "
                    f"Expándelo hasta 110-130 palabras SIN cambiar el tono ni la historia. "
                    f"Mantén la primera persona, las frases cortas y el ritmo dramático. "
                    f"Responde SOLO con el guion expandido, sin etiquetas, sin secciones, sin explicaciones:\n\n"
                    f"{script['script_text']}"
                )
                try:
                    import re as _re_exp
                    expanded = _call_llm(
                        expand_prompt,
                        attempt=1,
                        system_prompt="Eres un escritor de guiones virales en español.",
                        max_tokens=600,
                    ).strip()

                    # Si el LLM igualmente estructuró la respuesta en secciones,
                    # extraer SOLO la parte posterior al último encabezado tipo "expandido"
                    _section = _re_exp.search(
                        r"(?si)(?:guion[_ ]?(?:expandido|actual|nuevo|completo)|"
                        r"guión[_ ]?(?:expandido|actual|nuevo|completo)|"
                        r"expandido|script)[^\n]*\n+(.+)",
                        expanded,
                    )
                    if _section:
                        candidate = _section.group(1).strip()
                        if len(candidate.split()) >= wc // 2:
                            expanded = candidate
                            logger.info("Expansión: extraída sección post-header del LLM")

                    # Limpiar cualquier encabezado residual al inicio
                    expanded = _re_exp.sub(
                        r"(?i)^[\s#*_]*(guion[_ ]?(?:expandido|actual|nuevo|completo)?|"
                        r"guión[_ ]?(?:expandido|actual|nuevo|completo)?|"
                        r"expandido|guion|guión|script|resultado)[^\n]*\n*",
                        "", expanded,
                    ).strip()

                    exp_words = len(expanded.split())
                    if exp_words > wc:
                        script["script_text"] = expanded
                        logger.info(f"Script expandido: {wc} → {exp_words} palabras")
                    else:
                        logger.warning(f"Expansión no mejoró ({exp_words} palabras) — usando original")
                except Exception as _exp_e:
                    logger.warning(f"Expansión falló ({_exp_e}) — usando script corto")

            if isinstance(script.get("tags"), str):
                script["tags"] = [t.strip() for t in script["tags"].split(",")]

            # Añadir metadatos de la fuente al script
            script["_fuente"]  = fuente
            script["_upvotes"] = story.get("upvotes", 0)
            script["_post_id"] = story.get("post_id", "")

            logger.info(
                f"Script listo: '{script['title']}' "
                f"| {len(script['script_text'].split())} palabras "
                f"| {len(script['scenes'])} scenes"
            )
            return script

        except json.JSONDecodeError as e:
            last_error = f"JSON invalido (intento {attempt}): {e}"
            logger.warning(last_error)
            logger.warning(f"Raw Ollama (primeros 300 chars): {raw[:300]!r}")
        except ValueError as e:
            last_error = f"Validacion fallida (intento {attempt}): {e}"
            logger.warning(last_error)
        except Exception as e:
            last_error = f"Error inesperado (intento {attempt}): {e}"
            logger.error(last_error)

    raise ValueError(
        f"No se pudo narrar la historia tras 3 intentos.\n"
        f"Ultimo error: {last_error}"
    )
