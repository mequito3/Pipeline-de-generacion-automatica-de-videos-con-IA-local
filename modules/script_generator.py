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

logger = logging.getLogger(__name__)

# ─── Pool de personajes diversos (para reintentos de Ollama) ──────────────────
DIVERSE_CHARACTERS = [
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
]

# ─── Prompt del sistema ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres un narrador experto en confesiones dramáticas para YouTube Shorts en español latino.
Tu misión: crear historias COHERENTES, CREÍBLES y EMOCIONALMENTE PODEROSAS a partir de un tema.
CRÍTICO: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra. Empieza con { y termina con }.

═══ REGLA #1 — COHERENCIA NARRATIVA (la más importante) ═══

Cada frase DEBE ser consecuencia lógica de la anterior. La historia debe tener sentido completo.
OBLIGATORIO: conecta las frases con: entonces, pero, de repente, fue cuando, sin embargo, hasta que, lo que no sabía, en ese momento, por eso, fue así como.
PROHIBIDO: saltar de tema sin conectar. PROHIBIDO: frases sueltas que no explican qué pasó.

HISTORIA MAL ESCRITA — NUNCA HAGAS ESTO:
"Nunca debí revisar su celular. Tres años. Mi amigo. No pude dormir. Traición. ¿Qué harías?"
→ No hay conexión entre frases. No se entiende nada.

HISTORIA BIEN ESCRITA — ASÍ DEBE SER:
"Nunca debí revisar su celular. Llevábamos tres años juntos cuando empezó a llegar tarde cada noche sin explicación. Un martes, mientras cargaba su teléfono, vi un mensaje. Era de mi mejor amigo. Se veían desde hacía seis meses. Lo que me destrozó no fue la traición... sino que ese amigo estuvo en nuestra boda. ¿Habrías podido seguir viviendo en el mismo vecindario que ellos dos?"
→ Cada frase responde por qué ocurrió la siguiente: situación → señal de alerta → descubrimiento → quién → revelación → emoción final.

═══ ESTRUCTURA CAUSAL OBLIGATORIA ═══

Cada parte conecta causalmente con la siguiente:
- hook (0–3s): golpe inicial. Máx 12 palabras. Que duela o sorprenda.
- contexto (3–8s): quién, qué relación, cuánto tiempo llevan. Máx 20 palabras.
- problema (8–20s): la primera señal de que algo no estaba bien → el momento del descubrimiento. 35–45 palabras.
- giro (20–30s): la revelación que cambia todo. Lo más inesperado. 20–30 palabras.
- final (30–40s): cómo reaccionó el narrador y qué consecuencia hubo. 15–20 palabras.
- pregunta: una sola pregunta MUY ESPECIFICA al dilema concreto de ESTA historia.
  PROHIBIDO usar estas frases genericas que se repiten en todos los videos:
  "¿Lo perdonarías?", "¿La perdonarías?", "¿Qué harías tú?", "¿Tú qué harías?",
  "¿Lo hubieras perdonado?", "¿Qué habrías hecho?", "¿Harías lo mismo?".
  La pregunta DEBE mencionar la acción o relación concreta de ESTA historia.
  Ejemplo traición laboral: "¿Habrías contado el secreto a toda la empresa?"
  Ejemplo secreto familiar: "¿Habrías confrontado a tu madre delante de todos?"
  Ejemplo celular revisado: "¿Habrías seguido revisando o cerrado el teléfono?"

REGLAS DE ESCRITURA:
- Frases cortas (máx 12 palabras) pero SIEMPRE conectadas causalmente con la anterior
- Primera persona: quien narra lo vivió
- Detalles específicos y creíbles: mencionar objetos, lugares, momentos concretos
- PROHIBIDO: nombres reales, violencia explícita, contenido ilegal

═══ TÍTULO, DESCRIPCIÓN Y TAGS ═══

Título (máx 100 chars): clickbait viral en español latino. Sé creativo — usa el gancho emocional más fuerte de ESTA historia concreta.
Descripción (200-400 chars): cuenta de qué va el video con las palabras clave naturales de esta historia. Termina invitando a comentar.
Tags (12): los más relevantes para el algoritmo de YouTube en esta historia específica — combina tema, emoción y alcance.

IMAGE PROMPTS: cinematográficos y emocionales en inglés para Stable Diffusion.
Ejemplos: "close-up of a woman staring at phone in shock, dark room, cinematic 35mm", "man sitting alone at kitchen table, head in hands, dramatic side lighting", "empty apartment at night, single lamp, emotional"."""

USER_PROMPT_TEMPLATE = """Tema de confesión: {topic}

Genera una historia dramática anónima para YouTube Shorts con este JSON exacto:

{{
  "narrator_gender": "female o male",
  "character_description": "descripcion fisica del narrador en ingles",
  "hook": "primera frase de impacto, máx 12 palabras, específica del conflicto de {topic}",
  "contexto": "quién narra, con quién, cuánto tiempo llevan — 15-20 palabras",
  "problema": "señal de alerta → descubrimiento — 35-45 palabras conectadas",
  "giro": "revelación que cambia todo — 20-30 palabras",
  "final": "reacción y consecuencia real — 15-20 palabras",
  "pregunta": "pregunta especifica del dilema de {topic} (PROHIBIDO: perdonar/que harias en forma generica)",
  "script_text": "historia completa narrada en primera persona, frases cortas conectadas causalmente, 100-130 palabras, empieza con hook, termina con pregunta",
  "title": "titulo viral que describe EXACTAMENTE lo que pasa en el script_text, máx 100 chars",
  "description": "descripción SEO 200-400 chars que resume el conflicto especifico narrado",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"]
}}

REGLAS CRÍTICAS:
- script_text: 100-130 palabras. Cada frase conecta causalmente con la anterior. NO son frases sueltas.
- La historia es ficción creíble con detalles concretos (objetos, lugares, tiempos)"""

USER_PROMPT_RETRY_TEMPLATE = """Tema de confesión: {topic}

INTENTO ANTERIOR FALLÓ. Responde SOLO con JSON válido (empieza con {{ termina con }}).
REGLA MAS IMPORTANTE: script_text debe ser una historia fluida con lógica interna, NO una lista de frases sueltas. Cada frase conecta con la siguiente usando: entonces, pero, de repente, fue cuando, lo que no sabía, hasta que.
Campos: title, description, tags (lista 12), narrator_gender, character_description, hook, contexto, problema, giro, final, pregunta, script_text (100-130 PALABRAS CONECTADAS).

{{
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "hook": "[primera frase impactante, máx 12 palabras]",
  "contexto": "[quién, relación, cuánto tiempo — máx 20 palabras]",
  "problema": "[señal de alerta → descubrimiento, frases conectadas — 35-45 palabras]",
  "giro": "[revelación inesperada — 20-30 palabras]",
  "final": "[consecuencia y reacción — 15-20 palabras]",
  "pregunta": "[pregunta especifica del dilema de {topic}, NO generica]",
  "script_text": "[narracion fluida, 100-130 palabras, empieza con hook, frases conectadas, termina con pregunta]",
  "title": "[titulo viral que describe EXACTAMENTE lo narrado en script_text]",
  "description": "[descripcion SEO 200-400 chars del conflicto especifico narrado]",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"]
}}"""


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


def _call_groq(
    prompt_user: str,
    attempt: int,
    system_prompt: str,
    max_tokens: int,
) -> str:
    """
    Llama a Groq (cloud gratuito) via API OpenAI-compatible.

    Tier gratuito: 500k tokens/día, 6000 tokens/min, 30 req/min.
    Lanza RuntimeError si la cuota se agota (status 429) o hay error de red,
    para que el caller pueda caer a Ollama local.
    """
    api_key    = getattr(config, "GROQ_API_KEY", "")
    groq_model = getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile")

    logger.info(f"Llamando a Groq [{groq_model}] — intento {attempt} — max_tokens={max_tokens}")
    print(f"   Generando con Groq ({groq_model})... ", end="", flush=True)

    start_ts = time.time()
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": prompt_user},
            ],
            "temperature": 0.8,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )

    elapsed = time.time() - start_ts

    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after", "?")
        raise RuntimeError(
            f"Groq: límite de tasa o cuota agotada (retry-after: {retry_after}s) — usando Ollama local"
        )

    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    print(f" {len(content)} chars ({elapsed:.0f}s) — Groq")
    logger.info(f"Groq respondió: {len(content)} chars en {elapsed:.0f}s")
    return content


def _call_ollama(
    prompt_user: str,
    attempt: int = 1,
    model: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 650,
) -> str:
    """
    Intenta Groq (cloud gratuito) primero; si falla, usa Ollama local.

    Args:
        prompt_user: Prompt del usuario
        attempt: Numero de intento (para logging)
        model: Modelo Ollama. Si es None usa config.OLLAMA_MODEL.
        system_prompt: System prompt. Si es None usa SYSTEM_PROMPT global.
        max_tokens: Tokens maximos a generar.

    Returns:
        Texto completo de respuesta del modelo
    """
    sys_prompt = system_prompt or SYSTEM_PROMPT

    # ── Groq primero (cloud gratuito, más rápido y capaz) ──────────────────────
    groq_key = getattr(config, "GROQ_API_KEY", "")
    if groq_key:
        try:
            return _call_groq(prompt_user, attempt, sys_prompt, max_tokens)
        except RuntimeError as e:
            logger.warning(str(e))
        except Exception as e:
            logger.warning(f"Groq error inesperado: {e} — usando Ollama local")

    # ── Ollama local (fallback) ────────────────────────────────────────────────
    model_name   = model or config.OLLAMA_MODEL
    sys_prompt   = system_prompt or SYSTEM_PROMPT
    # Timeout de pared: matar el stream si tarda más de este tiempo
    # 180s = 3 min (suficiente incluso a 5 tokens/s para 900 tokens)
    MAX_GEN_SECS = int(getattr(config, "OLLAMA_TIMEOUT", 180))

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
    # ── 1. Campos requeridos ──────────────────────────────────────────────────
    required_keys = [
        "title", "description", "tags", "script_text",
        "hook", "contexto", "problema", "giro", "final", "pregunta",
        "narrator_gender", "character_description", "scenes",
    ]
    for key in required_keys:
        if key not in script or not script[key]:
            logger.warning(f"Campo faltante o vacío en script: '{key}'")
            return False

    # ── 2. Escenas ────────────────────────────────────────────────────────────
    if not isinstance(script["scenes"], list) or len(script["scenes"]) < 4:
        logger.warning(f"Scenes inválidas: {len(script.get('scenes', []))} (se requieren 4+)")
        return False

    for i, scene in enumerate(script["scenes"]):
        if "text" not in scene or "image_prompt" not in scene:
            logger.warning(f"Escena {i} incompleta: {list(scene.keys())}")
            return False

    # ── 3. Word count estricto ────────────────────────────────────────────────
    script_text = script.get("script_text", "")
    word_count  = len(script_text.split())

    if word_count < 45:
        # Intentar reconstruir desde las secciones narrativas antes de fallar
        reconstructed = " ".join(filter(None, [
            script.get("hook", ""),
            script.get("contexto", ""),
            script.get("problema", ""),
            script.get("giro", ""),
            script.get("final", ""),
            script.get("pregunta", ""),
        ]))
        reconstructed_words = len(reconstructed.split())
        if reconstructed_words >= 70:
            logger.warning(
                f"script_text corto ({word_count} palabras) — "
                f"reconstruyendo desde secciones ({reconstructed_words} palabras)"
            )
            script["script_text"] = reconstructed
            script_text = reconstructed
            word_count  = reconstructed_words
        else:
            logger.warning(
                f"Script muy corto: {word_count} palabras. "
                f"Reconstrucción también corta: {reconstructed_words} palabras. Reintentando."
            )
            return False

    if word_count < 70:
        logger.warning(f"Script demasiado corto: {word_count} palabras (mínimo 70). Reintentando.")
        return False

    if word_count > 160:
        logger.warning(f"Script demasiado largo: {word_count} palabras (máximo 160). Reintentando.")
        return False

    if 70 <= word_count < 100:
        logger.warning(f"Script algo corto: {word_count} palabras (objetivo 100-130)")
    elif 131 <= word_count <= 160:
        logger.warning(f"Script algo largo: {word_count} palabras (objetivo 100-130)")
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
    female_words = {"woman", "girl", "female", "mujer", "chica"}
    male_words   = {"man", "boy", "male", "hombre", "chico"}

    # Usar palabras completas (split) para evitar que "woman" matchee "man"
    desc_words     = set(char_desc.replace(",", " ").replace(".", " ").split())
    desc_is_female = bool(female_words & desc_words)
    desc_is_male   = bool(male_words & desc_words)

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
    CONNECTORS = [
        "entonces", "de repente", "fue cuando", "sin embargo",
        "hasta que", "fue entonces", "por eso", "lo que no sabía",
        "en ese momento", "aunque", "mientras", "porque",
        "después de", "a partir de", "fue así", "al final",
        "pero de", "fue allí", "en cuanto",
    ]
    script_lower    = script_text.lower()
    connector_count = sum(1 for c in CONNECTORS if c in script_lower)

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
    _CUTS = [
        "extreme close-up of face, raw emotion",
        "medium shot, full body tension",
        "close-up of hands, trembling",
        "wide shot, empty room, isolation",
        "over-shoulder perspective, dramatic",
        "low angle shot, power and fear",
        "close-up side profile, tears",
        "detail shot, symbolic object, moody",
    ]
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
                    angle = _CUTS[len(sub_chunks) % len(_CUTS)]
                    sub_chunks.append({
                        "text": " ".join(chunk),
                        "image_prompt": f"{prompt}, {angle}",
                    })
            new_scenes.extend(sub_chunks)
        else:
            new_scenes.append(scene)

    logger.info(f"Escenas: {len(original_scenes)} originales → {len(new_scenes)} tras división visual")
    script["scenes"] = new_scenes

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
    if not check_ollama_running():
        raise RuntimeError(
            "Ollama no está corriendo. Inicialo con:\n"
            "  ollama serve\n"
            f"  (en otra terminal): ollama pull {config.OLLAMA_MODEL}"
        )

    model_found, exact_model = check_model_available(config.OLLAMA_MODEL)
    if not model_found:
        available = get_available_models()
        models_str = "\n  ".join(available) if available else "(ninguno instalado)"
        raise RuntimeError(
            f"Modelo '{config.OLLAMA_MODEL}' no encontrado en Ollama.\n"
            f"Modelos disponibles:\n  {models_str}\n"
            f"Instalar con: ollama pull {config.OLLAMA_MODEL}"
        )

    # Usar el nombre exacto que Ollama reconoce
    model_to_use = exact_model
    logger.info(f"Usando modelo: '{model_to_use}' (configurado: '{config.OLLAMA_MODEL}')")
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

            raw_response = _call_ollama(user_prompt, attempt, model=model_to_use)
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
            logger.warning(f"Raw Ollama (primeros 300 chars): {raw[:300]!r}")
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

STORY_SYSTEM_PROMPT = """Eres el narrador mas amarillista y sensacionalista de YouTube Shorts en espanol latino. Tu estilo es el de la prensa roja, las revistas del corazon y las telenovelas mas explosivas. Cada frase debe golpear al espectador directo en el pecho.

TU MISION: Tomar la historia real y hacer DOS cosas:
A) Narrarla COMPLETA con dramatismo extremo, lenguaje visceral y detalles impactantes. Sin resumir. Sin omitir. Sin suavizar.
B) Disenar un STORYBOARD cinematografico: 4-5 actos con imagenes coherentes.

═══ REGLA #1 — COHERENCIA NARRATIVA (mas importante que todo) ═══

La historia DEBE tener sentido completo de principio a fin.
OBLIGATORIO: cada frase es consecuencia logica de la anterior. Usa conectores: entonces, pero, de repente, fue cuando, lo que no sabia, sin embargo, hasta que, en ese momento.
PROHIBIDO: frases sueltas sin conexion. PROHIBIDO: saltar de tema sin explicar por que.

HISTORIA MAL NARRADA — NUNCA HAGAS ESTO:
"Me traiciono. Lloraba. No pude dormir. Mi amiga. Tres anos. ¿Que harias?"
→ Frases sin conexion. No se entiende que paso ni por que.

HISTORIA BIEN NARRADA — ASI DEBE SER:
"¿Alguna vez descubriste que tu mejor amiga te estaba mintiendo desde hace anos? Llevabamos cuatro anos de amistad cuando note que empezaba a evitar mis mensajes. Un dia, buscando una foto en mi telefono, encontre una conversacion que no debia ver. Era ella, hablando con mi novio. Llevaban seis meses detras de mis espaldas. Lo que mas me dano no fue la traicion... sino que yo le conte todo sobre nuestra relacion. ¿Perdonarias a alguien asi?"
→ Cada frase explica la siguiente: situacion → señal → descubrimiento → identidad → revelacion → consecuencia emocional.

═══ TONO OBLIGATORIO ═══

Escribe como si le contaras este escandalo a tu mejor amiga en voz baja, con los ojos abiertos de par en par.
USA estas palabras: traicion, mentira, humillacion, dolor, secreto, venganza, llorar, destruir, engano, descubrir, jamas, nunca, impactante, devastador, increible.
EVITA estas palabras: "interesante", "situacion", "aspecto", "contexto", "relacion interpersonal", "ademas", "por otro lado". Son palabras de periodico aburrido.
FRASES CORTAS. Maxima tension. Cada frase = un golpe emocional. Pero CONECTADAS entre si.
Si la historia es en ingles, traducela al espanol mas natural y dramatico posible — nada de traduccion robotica.

═══ ESTRUCTURA OBLIGATORIA DE LA NARRACION ═══

El campo script_text DEBE tener esta estructura exacta:
  [INTRO_HOOK] → [HISTORIA COMPLETA con todos los detalles] → [OUTRO_CTA]

1. INTRO_HOOK (primeros 5 segundos) — PREGUNTA que paraliza al espectador:
   - Debe mencionar el OBJETO, LUGAR o ACCION CONCRETA de ESTA historia. No puede servir para otra historia.
   - MALO (genérico, sirve para cualquier video): "¿Alguna vez llegaste a casa y descubriste que tu vida era una mentira?"
   - BUENO (historia de celular): "¿Alguna vez encontraste algo en el celular de tu pareja que jamás debías ver?"
   - BUENO (historia de mejor amigo): "¿Sabías que tu mejor amigo de diez años podía hacer algo así a tus espaldas?"
   - BUENO (historia de trabajo): "¿Alguna vez descubriste que tu jefe usaba el dinero de la empresa para otra vida?"
   - BUENO (historia de familia): "¿Qué harías si descubrieras que tu propio hermano te estuvo mintiendo durante años?"
   - REGLA: si el hook puede usarse en otro video de otra historia, NO sirve. Escribe uno que solo funcione AQUI.

2. HISTORIA COMPLETA (cuerpo de la narracion):
   - La PRIMERA frase despues del hook = el momento mas impactante de la historia. GOLPE DIRECTO.
   - Frases cortas. Maximo 10 palabras cada una. Ritmo rapido, tension constante.
   - Emociones fisicas concretas: "el corazon se me paralizo", "me temblaban las piernas", "no podia respirar", "las lagrimas no paraban".
   - Vocabulario de impacto: TRAICIONO, MENTIA, DESCUBRI, JAMAS IMAGINE, ME DESTROZARON, LLORE, SECRETO, VENGANZA, HUMILLACION.
   - Cambiar nombres reales por: "el", "ella", "mi pareja", "mi ex", "mi mejor amiga", "mi madre", "mi jefe". NUNCA nombres propios.
   - Primera persona siempre. Como si lo estuvieras viviendo ahora mismo.
   - NARRA TODO — cada detalle importa. No resumir. No saltar partes.
   - Crear suspenso antes de cada revelacion: "Y entonces... lo vi." / "Fue en ese momento cuando todo se derrumbo."
   - Puntos de quiebre emocional: marca los momentos clave con frases de impacto antes de revelarlos.

3. OUTRO_CTA (ultimos 5 segundos) — PREGUNTA ESPECIFICA + LLAMADA A LA ACCION:
   - La pregunta DEBE ser sobre el dilema concreto de ESTA historia, no una pregunta generica.
   - Luego: llamada a la accion corta y natural, como hablandole a un amigo.

   PREGUNTAS ABSOLUTAMENTE PROHIBIDAS (estas son genericas y aparecen en todos los videos):
   - "¿Lo perdonarias?" / "¿La perdonarias?" / "¿Le perdonarias?"
   - "¿Lo hubieras perdonado?" / "¿La hubieras perdonado?"
   - "¿Que harias tu?" / "¿Tu que harias?" / "¿Que hubieras hecho tu?"
   - "¿Lo hubieras hecho?" / "¿Harias lo mismo?"
   - Cualquier variacion de PERDONAR o HACER en pregunta generica sin detalles especificos

   PREGUNTAS BUENAS — especificas del conflicto de ESTA historia:
   - Si la historia es de descubrir mensajes: "¿Hubiera revisado el telefono de tu pareja despues de tres años de relacion?"
   - Si la historia es de una traicion en el trabajo: "¿Le habrias dicho a tu jefe lo que descubriste o te habrias quedado callado?"
   - Si la historia es de un secreto familiar: "¿Habrias confrontado a tu madre delante de toda la familia?"
   - Si la historia es de infidelidad con un amigo: "¿Podrias seguir viviendo en la misma ciudad que esa persona?"
   - REGLA: la pregunta debe mencionar ALGO especifico de ESTA historia (la accion concreta, la relacion concreta, la decision concreta).
   - Cierra siempre con: "Dejamelo en los comentarios", "Cuentame abajo", "Dale like si te paso algo asi".

4. TITULO, DESCRIPCION Y TAGS:
   - Título (máx 100 chars): el gancho más fuerte y viral de ESTA historia concreta.
   - Descripción (200-400 chars): las palabras clave naturales de esta historia. Invita a comentar.
   - Tags (12): los más relevantes para el algoritmo de YouTube en esta historia específica.

5. GENERO DEL NARRADOR:
   - Detectar si la historia la cuenta un hombre o una mujer por el contexto.
   - narrator_gender = "female" si la voz narradora es femenina, "male" si es masculina.
   - character_description DEBE coincidir con el genero detectado.

═══ REGLAS DEL STORYBOARD ═══
- Define 4-5 ACTOS con progresion emocional: INICIO → DESCUBRIMIENTO → CONFRONTACION → CONSECUENCIA → REFLEXION.
- Cada acto = UNA locacion concreta + UN estado emocional especifico del personaje.
- image_prompt en INGLES, formato SD: "cinematic portrait, 35mm film, dramatic lighting, [character], [location], [emotion], shallow depth of field, photorealistic"
- El character_description DEBE ser identico y consistente en TODOS los image_prompt.

CRITICO: Responde UNICAMENTE con JSON valido. Sin markdown. Sin texto antes o despues del JSON. Sin explicaciones."""

STORY_USER_PROMPT = """Historia real para narrar:
---
TITULO: {titulo}
HISTORIA: {historia}
FUENTE: {fuente}
---

Narra esta historia COMPLETA con estructura profesional de YouTube Shorts.

ESTRUCTURA OBLIGATORIA de script_text:
  1. intro_hook (pregunta retórica al espectador, máx 15 palabras)
  2. Historia narrada completa en español, primera persona, frases cortas
  3. outro_cta (pregunta específica + llamada a acción, máx 20 palabras)

{{
  "narrator_gender": "female o male segun quien cuenta la historia",
  "character_description": "descripcion fisica CONSISTENTE del narrador en ingles",
  "intro_hook": "pregunta especifica de ESTA historia — menciona el objeto/lugar/accion concreto. NO sirve para otra historia.",
  "hook": "primera frase de impacto de la narracion, maxima 12 palabras",
  "script_text": "[EMPIEZA con intro_hook. Luego narra TODA la historia en primera persona con todos los detalles, frases cortas conectadas causalmente. Entre 150 y 250 palabras. Termina con outro_cta.]",
  "pregunta": "pregunta especifica del dilema de ESTA historia (PROHIBIDO: perdonar/que harias en forma generica)",
  "outro_cta": "pregunta especifica de ESTE conflicto (no generica) + llamada a accion corta",
  "title": "titulo viral que describe EXACTAMENTE lo que pasa en el script_text, maximo 100 caracteres",
  "description": "descripcion SEO 200-400 caracteres que resume el conflicto especifico narrado en script_text",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"]
}}

REGLAS:
- script_text = intro_hook + historia completa + outro_cta (todo junto, sin omitir nada).
- narrator_gender: detectar si la historia la cuenta un hombre (male) o una mujer (female)."""

STORY_RETRY_PROMPT = """Historia:
TITULO: {titulo}
HISTORIA: {historia}

FALLO ANTERIOR — responde SOLO JSON valido, sin markdown, empieza con {{ termina con }}.
CAMPOS OBLIGATORIOS: title, description, tags, narrator_gender, character_description, intro_hook, hook, pregunta, outro_cta, script_text, scenes (minimo 5).

{{
  "script_text": "[INTRO_HOOK]. [HISTORIA_COMPLETA_CON_TODOS_LOS_DETALLES — 150 a 250 palabras]. [PREGUNTA_ESPECIFICA_SIN_PERDONAR_NI_QUE_HARIAS]? [LLAMADA_A_ACCION].",
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "intro_hook": "[pregunta especifica de ESTA historia — menciona el objeto/lugar/accion concreto]",
  "hook": "[primera frase devastadora, maxima 12 palabras]",
  "pregunta": "[pregunta especifica del dilema de ESTA historia, NO generica]",
  "outro_cta": "[pregunta especifica de ESTE conflicto + llamada a accion]",
  "script_text": "[narracion completa 150-250 palabras, empieza con intro_hook, termina con outro_cta]",
  "title": "[titulo viral que describe EXACTAMENTE lo narrado en script_text]",
  "description": "[descripcion SEO 200-400 chars del conflicto narrado]",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"]
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

    if word_count < 30:
        # Intentar reconstruir desde scenes
        reconstructed = " ".join(s.get("text", "") for s in script["scenes"])
        reconstructed_words = len(reconstructed.split())
        if reconstructed_words >= 60:
            script["script_text"] = reconstructed
            script_text = reconstructed
            word_count  = reconstructed_words
            logger.warning(f"script_text reconstruido desde scenes: {word_count} palabras")
        else:
            logger.warning(f"Script demasiado corto: {word_count} palabras. Reintentando.")
            return False

    if word_count < 60:
        logger.warning(f"Script corto: {word_count} palabras (mínimo 60). Reintentando.")
        return False

    if word_count > 350:
        logger.warning(f"Script demasiado largo: {word_count} palabras (máximo 350). Reintentando.")
        return False

    if 60 <= word_count < 120:
        logger.warning(f"Script algo corto: {word_count} palabras (objetivo 150-250)")
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
    female_words = {"woman", "girl", "female", "mujer", "chica"}
    male_words   = {"man", "boy", "male", "hombre", "chico"}
    desc_is_female = any(w in char_desc for w in female_words)
    desc_is_male   = any(w in char_desc for w in male_words)

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
    CONNECTORS = [
        "entonces", "de repente", "fue cuando", "sin embargo",
        "hasta que", "fue entonces", "por eso", "lo que no sabía",
        "en ese momento", "aunque", "mientras", "porque",
        "después de", "a partir de", "fue así", "al final",
        "pero de", "fue allí", "en cuanto",
    ]
    script_lower    = script_text.lower()
    connector_count = sum(1 for c in CONNECTORS if c in script_lower)

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

    # ── Integrar intro_hook y outro_cta en script_text si existen ─────────────
    intro_hook = (script.get("intro_hook") or "").strip()
    outro_cta  = (script.get("outro_cta") or "").strip()
    script_text = script.get("script_text", "").strip()

    # Asegurarse de que el intro_hook esté al inicio del script_text
    if intro_hook and not script_text.startswith(intro_hook[:20]):
        script["script_text"] = intro_hook + " " + script_text
        logger.info("intro_hook insertado al inicio de script_text")

    # Asegurarse de que el outro_cta esté al final del script_text
    if outro_cta and outro_cta[:20] not in script.get("script_text", ""):
        script["script_text"] = script.get("script_text", "").rstrip() + " " + outro_cta
        logger.info("outro_cta insertado al final de script_text")

    # Si hay intro_hook, añadirlo como primera scene (si no está ya)
    if intro_hook:
        first_text = script["scenes"][0].get("text", "") if script["scenes"] else ""
        if intro_hook[:15] not in first_text:
            script["scenes"].insert(0, {
                "text": intro_hook,
                "image_prompt": "cinematic close-up of mysterious dark background, dramatic lighting, question mark silhouette, emotional atmosphere",
                "act": "INTRO",
            })
            logger.info("intro_hook añadido como primera scene")

    # Asegurarse de que el outro_cta esté en la última scene
    if outro_cta:
        last_text = script["scenes"][-1].get("text", "")
        if outro_cta[:15] not in last_text:
            script["scenes"][-1]["text"] = last_text.rstrip() + " " + outro_cta
            logger.info("outro_cta añadido a la última scene")

    logger.info(f"Historia narrada: {word_count} palabras | {len(script['scenes'])} escenas")

    # ── Asegurar que cada scene tiene image_prompt ────────────────────────────
    char_desc = script.get("character_description", "person, dramatic lighting")
    for scene in script["scenes"]:
        if not scene.get("image_prompt"):
            scene["image_prompt"] = (
                f"cinematic portrait, 35mm film, {char_desc}, "
                f"dramatic lighting, emotional, photorealistic"
            )

    # ── Angulos de camara para variedad visual ────────────────────────────────
    _CUTS = [
        "medium shot, subject centered, emotional",
        "extreme close-up of face, raw emotion, tears",
        "over-shoulder shot, dramatic perspective",
        "close-up of hands, trembling or clenched",
        "wide shot, subject small in frame, isolation",
        "low angle shot, looking up, vulnerable",
        "close-up side profile, jaw clenched",
        "detail shot, symbolic object in foreground",
    ]

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
                    angle = _CUTS[cut_counter % len(_CUTS)]
                    cut_counter += 1
                    sub_chunks.append({
                        "text":         " ".join(chunk),
                        "image_prompt": f"{base_prompt}, {angle}",
                    })
            new_scenes.extend(sub_chunks)
        else:
            angle = _CUTS[cut_counter % len(_CUTS)]
            cut_counter += 1
            new_scenes.append({
                "text":         text,
                "image_prompt": f"{base_prompt}, {angle}",
            })

    logger.info(f"Scenes: {len(original_scenes)} originales -> {len(new_scenes)} tras division visual")
    script["scenes"] = new_scenes
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
    if len(words) > 300:
        historia = " ".join(words[:300])
        logger.info(f"Historia truncada a 300 palabras (original: {len(words)})")

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

            raw = _call_ollama(
                user_prompt,
                attempt=attempt,
                system_prompt=STORY_SYSTEM_PROMPT,
                max_tokens=max_tokens,
            )
            logger.debug(f"Respuesta cruda ({len(raw)} chars): {raw[:300]}...")

            json_str = _extract_json_from_text(raw)
            json_str = _sanitize_json(json_str)
            script   = _try_parse_json(json_str)

            if not _validate_story_script(script):
                raise ValueError("Script invalido — faltan campos o muy corto")

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
