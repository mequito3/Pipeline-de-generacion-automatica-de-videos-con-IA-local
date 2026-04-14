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
SYSTEM_PROMPT = """Eres un experto en crear historias de confesiones dramáticas para YouTube Shorts en español latino neutro.
Tu misión: transformar un TEMA en una historia emocional breve, viral y anónima (sin nombres reales, sin copiar fuentes).
CRÍTICO: Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto extra. Empieza con { y termina con }.

ESTRUCTURA OBLIGATORIA DE LA HISTORIA:
- hook (0–3s): golpe inicial impactante, máx 12 palabras. Que duela o sorprenda desde el primer segundo.
- contexto (3–8s): sitúa al espectador rápido. Quién, qué situación. Máx 20 palabras.
- problema (8–20s): el conflicto central, el momento de traición o descubrimiento. 30–45 palabras.
- giro (20–30s): la revelación inesperada que cambia todo. 20–30 palabras.
- final (30–40s): reacción/consecuencia. Corto y poderoso. 15–20 palabras.
- pregunta (engagement): una sola pregunta al espectador tipo "¿Qué harías tú?" o "¿Perdonarías esto?".

REGLAS DE ESCRITURA:
- Frases cortas (máx 12 palabras por frase)
- Lenguaje simple y emocional
- PROHIBIDO: nombres reales, acusaciones directas, violencia explícita, contenido ilegal
- La historia debe ser ficción inspirada en el tema, NO una copia

IMAGE PROMPTS: cinematográficos y emocionales en inglés (para Stable Diffusion).
Ejemplos: "close-up of a woman crying in a dark room, cinematic", "man holding phone in shock, dramatic lighting", "empty bed with wedding ring on pillow, emotional"."""

USER_PROMPT_TEMPLATE = """Tema de confesión: {topic}

Genera una historia dramática anónima para YouTube Shorts con este JSON exacto:

{{
  "title": "título clickbait en español, máx 100 chars, genera urgencia o impacto emocional",
  "description": "descripción SEO 200-400 chars en español con keywords de drama y emociones",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "narrator_gender": "female",
  "character_description": "descripcion fisica consistente: genero, edad aproximada, rasgos, ropa. Ej: 'Hispanic woman, late 20s, dark hair, red blouse'",
  "hook": "frase inicial impactante, máx 12 palabras, que enganche desde el segundo 0",
  "contexto": "situación inicial, 15-20 palabras, establece personajes y escenario",
  "problema": "conflicto central o descubrimiento, 30-45 palabras, el momento más tenso",
  "giro": "revelación inesperada que cambia todo, 20-30 palabras",
  "final": "consecuencia o reacción, 15-20 palabras, poderoso y corto",
  "pregunta": "pregunta de engagement, ej: ¿Qué harías tú en esta situación?",
  "script_text": "NARRACIÓN COMPLETA: hook + contexto + problema + giro + final + pregunta. Entre 80 y 110 palabras. Tono dramático y emocional.",
  "scenes": [
    {{"text": "hook exacto", "image_prompt": "cinematic image description in English, emotional lighting"}},
    {{"text": "contexto exacto", "image_prompt": "cinematic image description in English, emotional"}},
    {{"text": "problema (primera parte, máx 25 palabras)", "image_prompt": "cinematic image description in English, dramatic"}},
    {{"text": "problema (segunda parte) + giro", "image_prompt": "cinematic image description in English, shocking moment"}},
    {{"text": "final + pregunta", "image_prompt": "cinematic image description in English, emotional close-up"}}
  ]
}}

REGLAS CRÍTICAS:
- script_text: entre 80 y 110 palabras (video de 28-35 segundos)
- scenes: exactamente 5 escenas
- image_prompts: en inglés, estilo cinematográfico/emocional, sin texto en imagen
- La historia debe ser ficción anónima, nunca copiar texto real"""

USER_PROMPT_RETRY_TEMPLATE = """Tema de confesión: {topic}

INTENTO ANTERIOR FALLÓ. Responde SOLO con JSON válido (empieza con {{ termina con }}).
Campos obligatorios: title, description, tags (lista 12), narrator_gender, character_description, hook, contexto, problema, giro, final, pregunta, script_text (80-110 PALABRAS), scenes (lista 5 items con text e image_prompt).

{{
  "title": "...",
  "description": "...",
  "tags": ["#confesion","#drama","#historia","#viral","#shorts","#traicion","#secreto","#real","#impactante","#emocional","#relaciones","#verdad"],
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "hook": "Nunca debí revisar su celular...",
  "contexto": "Llevábamos tres años juntos y yo confiaba ciegamente en él.",
  "problema": "Esa noche encontré mensajes que me helaron la sangre. Fotos, conversaciones, planes. Todo con mi mejor amiga. Durante meses.",
  "giro": "Lo peor no fue la traición. Fue que ella estaba embarazada.",
  "final": "Los confronté juntos. Ninguno dijo una sola palabra.",
  "pregunta": "¿Qué harías tú en mi lugar?",
  "script_text": "Nunca debí revisar su celular. Llevábamos tres años juntos y yo confiaba ciegamente en él. Esa noche encontré mensajes que me helaron la sangre. Fotos, conversaciones, planes. Todo con mi mejor amiga. Durante meses. Lo peor no fue la traición. Fue que ella estaba embarazada. Los confronté juntos. Ninguno dijo una sola palabra. ¿Qué harías tú en mi lugar?",
  "scenes": [
    {{"text": "Nunca debí revisar su celular.", "image_prompt": "{character_example}, looking at phone screen in shock, dark room, cinematic lighting"}},
    {{"text": "Llevábamos tres años juntos y yo confiaba ciegamente en él.", "image_prompt": "couple holding hands, warm light, then shadow falls over them"}},
    {{"text": "Esa noche encontré mensajes que me helaron la sangre. Fotos, conversaciones, planes. Todo con mi mejor amiga.", "image_prompt": "close-up of phone screen with messages, trembling hands, dramatic"}},
    {{"text": "Lo peor no fue la traición. Fue que ella estaba embarazada. Los confronté juntos.", "image_prompt": "three people in tense confrontation, cold lighting, emotional"}},
    {{"text": "Ninguno dijo una sola palabra. ¿Qué harías tú en mi lugar?", "image_prompt": "{character_example}, alone in empty room, tears on face, cinematic close-up"}}
  ]
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


def _call_ollama(
    prompt_user: str,
    attempt: int = 1,
    model: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 1200,
) -> str:
    """
    Llama a Ollama con streaming para mostrar progreso en tiempo real.

    Args:
        prompt_user: Prompt del usuario
        attempt: Numero de intento (para logging)
        model: Nombre exacto del modelo. Si es None usa config.OLLAMA_MODEL.
        system_prompt: System prompt a usar. Si es None usa SYSTEM_PROMPT global.
        max_tokens: Tokens maximos a generar (subir para historias largas).

    Returns:
        Texto completo de respuesta del modelo
    """
    model_name   = model or config.OLLAMA_MODEL
    sys_prompt   = system_prompt or SYSTEM_PROMPT
    logger.info(f"Llamando a Ollama [{model_name}] — intento {attempt} — max_tokens={max_tokens}")
    print(f"   Generando con {model_name}... ", end="", flush=True)

    full_response = ""
    char_count = 0

    # Streaming: recibir token por token y mostrar progreso
    stream = ollama.chat(
        model=model_name,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt_user},
        ],
        options={
            "temperature": 0.8,
            "top_p": 0.9,
            "num_predict": max_tokens,
            "stop": ["\n}\n", "\n}}", "}\n\n\n"],  # Parar al cerrar el JSON
        },
        stream=True,
    )

    json_closed = False
    for chunk in stream:
        token = chunk["message"]["content"]
        full_response += token
        char_count += len(token)
        if char_count >= 100:
            print(".", end="", flush=True)
            char_count = 0
        # Detectar cierre del JSON y parar en cuanto las llaves estén balanceadas
        if not json_closed and full_response.count("}") >= full_response.count("{") > 0:
            json_closed = True
            break  # El JSON está completo — no esperar el resto de max_tokens

    print(f" {len(full_response)} chars")  # salto de línea al terminar
    return full_response


def _validate_script(script: dict) -> bool:
    """
    Valida estructura y contenido del script de confesión dramática.
    Detecta y corrige problemas comunes:
    - Campos requeridos faltantes
    - Script demasiado corto (<45 palabras) o largo (>140 palabras)
    - Sin pregunta de engagement en la última escena
    - Hook ausente en la primera escena

    Returns:
        True si es válido (con correcciones aplicadas in-place si es posible)
    """
    # ── Campos requeridos ──────────────────────────────────────────────────────
    required_keys = ["title", "description", "tags", "script_text",
                     "hook", "contexto", "problema", "giro", "final", "pregunta", "scenes"]
    for key in required_keys:
        if key not in script:
            logger.warning(f"Campo faltante en script: {key}")
            return False

    if not isinstance(script["scenes"], list) or len(script["scenes"]) < 4:
        logger.warning(f"Scenes inválidas: {len(script.get('scenes', []))} escenas (se requieren 4+)")
        return False

    for i, scene in enumerate(script["scenes"]):
        if "text" not in scene or "image_prompt" not in scene:
            logger.warning(f"Escena {i} incompleta: {scene.keys()}")
            return False

    # ── Conteo de palabras — target: 80–110 palabras (28–35 segundos) ─────────
    script_text = script.get("script_text", "")
    word_count  = len(script_text.split())

    if word_count < 45:
        # Intentar reconstruir desde las secciones narrativas
        reconstructed = " ".join(filter(None, [
            script.get("hook", ""),
            script.get("contexto", ""),
            script.get("problema", ""),
            script.get("giro", ""),
            script.get("final", ""),
            script.get("pregunta", ""),
        ]))
        reconstructed_words = len(reconstructed.split())
        if reconstructed_words >= 45:
            logger.warning(
                f"script_text corto ({word_count} palabras) — reconstruyendo desde secciones ({reconstructed_words} palabras)"
            )
            script["script_text"] = reconstructed
            word_count = reconstructed_words
        else:
            logger.warning(f"Script muy corto: {word_count} palabras (mín 45). Reintentando.")
            return False

    if word_count < 70:
        logger.warning(f"Script corto: {word_count} palabras (objetivo 80-110)")
    elif word_count > 140:
        logger.warning(f"Script largo: {word_count} palabras (objetivo 80-110, máx 140)")
    else:
        logger.info(f"Longitud OK: {word_count} palabras")

    # ── Pregunta de engagement en la última escena ────────────────────────────
    engagement_keywords = ["harías", "harias", "perdonar", "perdonarías", "culpa",
                           "opinión", "opinion", "comentar", "piensas", "crees",
                           "lugar", "situación", "situacion", "harás", "haras"]
    last_scene_text = script["scenes"][-1].get("text", "").lower()
    if not any(kw in last_scene_text for kw in engagement_keywords):
        pregunta = script.get("pregunta", "¿Qué harías tú en esta situación?")
        logger.warning("Última escena sin pregunta de engagement — añadiendo")
        script["scenes"][-1]["text"] += f" {pregunta}"

    # ── Hook presente en la primera escena ────────────────────────────────────
    hook  = script.get("hook", "").strip()
    first = script["scenes"][0].get("text", "").strip()
    if hook and not any(w in first.lower() for w in hook.lower().split()[:4]):
        logger.warning("Hook no aparece en la primera escena — ajustando")
        script["scenes"][0]["text"] = hook + " " + first

    # ── Multiplicador de escenas (rotación visual cada 1–2s, una imagen por chunk) ──
    # Divide textos largos en chunks de ~6 palabras.
    # Cada chunk recibe un ángulo de cámara distinto para que ComfyUI genere
    # imágenes realmente diferentes (evitar el cache-hit por prompt idéntico).
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
                # Fusionar tail muy corto (<4 palabras) con el chunk anterior
                if len(chunk) < 4 and sub_chunks:
                    prev = sub_chunks[-1]
                    prev["text"] = prev["text"] + " " + " ".join(chunk)
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

STORY_SYSTEM_PROMPT = """Eres un narrador profesional de confesiones reales para YouTube Shorts en espanol latino neutro. Estilo cinematografico, crudo y visceral.

TU MISION: Tomar la historia real y hacer DOS cosas:
A) Narrarla COMPLETA con dramatismo extremo. Sin omitir nada.
B) Disenar un STORYBOARD cinematografico: 4-5 actos con imagenes coherentes.

═══ ESTRUCTURA OBLIGATORIA DE LA NARRACION ═══

El campo script_text DEBE tener esta estructura:
  [INTRO_HOOK] → [HISTORIA COMPLETA] → [OUTRO_CTA]

1. INTRO_HOOK (primeros 5 segundos) — PREGUNTA RETÓRICA al espectador:
   - Debe ser una pregunta que haga al espectador sentir que le hablan A ÉL directamente.
   - MALO: "Hoy les voy a contar..." (no es pregunta, no engancha)
   - BUENO: "¿Alguna vez creíste conocer bien a tu pareja y resultó que todo era mentira?"
   - BUENO: "¿Qué harías si descubrieras que tu mejor amiga te había traicionado por años?"
   - BUENO: "¿Puedes imaginar llegar a casa y que tu vida entera se derrumbe en segundos?"
   - La pregunta DEBE relacionarse con el tema específico de esta historia.

2. HISTORIA COMPLETA (cuerpo de la narración):
   - Primera frase después del hook = revelación devastadora. IMPACTO INMEDIATO.
   - Frases cortas en tensión máxima (máximo 8 palabras).
   - Emociones físicas: "el corazón se me detuvo", "las manos me temblaban".
   - Traducir al español si está en inglés.
   - Cambiar nombres reales por: "él", "ella", "mi pareja", "mi ex", "mi mejor amiga/o", "mi madre".
   - Primera persona siempre. Tono crudo y real.
   - NARRA TODO — no resumir.

3. OUTRO_CTA (últimos 5 segundos) — PREGUNTA ESPECÍFICA + LLAMADA A LA ACCIÓN:
   - Primero: pregunta directamente relacionada con el dilema de ESTA historia.
   - Luego: llamada a la acción breve y natural.
   - MALO: "¿Qué harías tú?" (genérica)
   - BUENO: "¿Lo hubieras perdonado después de cinco años juntos? Déjamelo en los comentarios."
   - BUENO: "¿Tú te quedarías o te irías? Cuéntame abajo y dale like si te sorprendió."

4. GENERO DEL NARRADOR:
   - Detectar si la historia la cuenta un hombre o una mujer.
   - narrator_gender = "female" si la voz narradora es femenina, "male" si es masculina.
   - character_description DEBE coincidir con el género detectado.

═══ REGLAS DEL STORYBOARD ═══
- Define 4-5 ACTOS: INICIO → DESCUBRIMIENTO → CONFRONTACION → CONSECUENCIA → REFLEXION.
- Cada acto = UNA locacion + UN estado emocional del personaje.
- image_prompt en INGLES, formato SD: "cinematic portrait, 35mm film, dramatic lighting, [character], [location], [emotion], shallow depth of field, photorealistic"
- El character_description DEBE ser consistente en TODOS los image_prompt.

CRITICO: Responde UNICAMENTE con JSON valido. Sin markdown. Sin texto antes del JSON."""

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
  "title": "titulo clickbait en espanol, impactante, maximo 100 caracteres",
  "description": "descripcion SEO 200-400 caracteres con palabras clave de drama y emociones",
  "tags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10","#tag11","#tag12"],
  "narrator_gender": "female o male segun quien cuenta la historia",
  "character_description": "descripcion fisica CONSISTENTE del narrador en ingles. Ej: 'Hispanic woman, late 20s, long dark wavy hair, wearing a white blouse and jeans'",
  "intro_hook": "pregunta retorica al espectador sobre el tema de esta historia. Ej: '¿Alguna vez creiste conocer bien a alguien y resultó que todo era mentira?'",
  "hook": "revelacion devastadora de la historia, maxima 12 palabras — primer impacto tras el intro_hook",
  "pregunta": "pregunta ESPECIFICA al dilema de esta historia. Ej: '¿Lo hubieras perdonado despues de cinco anos?'",
  "outro_cta": "pregunta especifica + llamada a accion natural. Ej: '¿Lo hubieras perdonado? Dejamelo en los comentarios y dale like si te sorprendio.'",
  "script_text": "EMPIEZA con intro_hook. Luego narracion COMPLETA. TERMINA con outro_cta. Todo en espanol, primera persona, frases cortas.",
  "scenes": [
    {{"text": "intro_hook exacto aqui", "image_prompt": "cinematic portrait, 35mm film, [character_description], dramatic dark background, questioning silhouette, emotional"}},
    {{"text": "primer fragmento de la historia", "image_prompt": "cinematic portrait, 35mm film, [character_description], [location inicio], calm unaware expression, soft lamp light, photorealistic"}},
    {{"text": "fragmento del descubrimiento", "image_prompt": "cinematic portrait, 35mm film, [character_description], shocked expression, holding phone, tears forming, dramatic side lighting, photorealistic"}},
    {{"text": "fragmento de confrontacion o tension maxima", "image_prompt": "cinematic portrait, 35mm film, [character_description], angry devastated, dark hallway, harsh overhead light, photorealistic"}},
    {{"text": "consecuencia emocional final + outro_cta", "image_prompt": "cinematic portrait, 35mm film, [character_description], sitting alone crying, early morning light, broken expression, photorealistic"}}
  ]
}}

REGLAS:
- scenes: minimo 5, maximo 8. Cada scene = un fragmento de narracion + su imagen.
- image_prompt: reemplaza [character_description] y [location inicio] con los valores reales.
- script_text = intro_hook + historia completa + outro_cta (todo junto, sin omitir nada).
- narrator_gender: detectar si la historia la cuenta un hombre (male) o una mujer (female)."""

STORY_RETRY_PROMPT = """Historia:
TITULO: {titulo}
HISTORIA: {historia}

FALLO ANTERIOR — responde SOLO JSON valido, sin markdown, empieza con {{ termina con }}.
CAMPOS OBLIGATORIOS: title, description, tags, narrator_gender, character_description, intro_hook, hook, pregunta, outro_cta, script_text, scenes (minimo 5).

{{
  "title": "titulo impactante en espanol",
  "description": "descripcion SEO 200 caracteres",
  "tags": ["#confesion","#drama","#historia","#viral","#shorts","#traicion","#secreto","#real","#impactante","#emocional","#relaciones","#verdad"],
  "narrator_gender": "{narrator_gender_example}",
  "character_description": "{character_example}",
  "intro_hook": "¿Alguna vez creiste conocer a alguien y todo era mentira?",
  "hook": "primera frase devastadora de la historia, maxima 12 palabras",
  "pregunta": "pregunta especifica al dilema de esta historia",
  "outro_cta": "¿Lo hubieras perdonado? Dejalo en comentarios y dale like.",
  "script_text": "¿Alguna vez creiste conocer a alguien? Nunca imagine que ese dia destruiria todo. Llegue a casa y lo vi. Mi corazon se paro. [narracion completa de la historia aqui]. ¿Lo hubieras perdonado? Dejalo en comentarios.",
  "scenes": [
    {{"text": "¿Alguna vez creiste conocer a alguien?", "image_prompt": "cinematic portrait, 35mm film, {character_example}, dark dramatic background, questioning look, emotional"}},
    {{"text": "primer fragmento de la historia", "image_prompt": "cinematic portrait, 35mm film, {character_example}, bedroom at night, calm expression, soft lamp light, photorealistic"}},
    {{"text": "descubrimiento impactante", "image_prompt": "cinematic portrait, 35mm film, {character_example}, shocked holding phone, tears forming, dramatic side lighting, photorealistic"}},
    {{"text": "confrontacion o tension maxima", "image_prompt": "cinematic portrait, 35mm film, {character_example}, dark hallway confrontation, harsh overhead light, photorealistic"}},
    {{"text": "consecuencia + outro_cta", "image_prompt": "cinematic portrait, 35mm film, {character_example}, alone crying on floor, early morning light, photorealistic"}}
  ]
}}"""


def _validate_story_script(script: dict) -> bool:
    """
    Valida el script y prepara las scenes para el video.

    Flujo:
    1. Valida campos requeridos
    2. Integra intro_hook y outro_cta en script_text y scenes
    3. Asegura que cada scene tiene image_prompt
    4. Divide textos largos en sub-chunks con variacion de angulo de camara
       — dentro del mismo acto = misma locacion, distinto angulo
    """
    required_keys = ["title", "description", "tags", "script_text", "hook", "pregunta", "scenes"]
    for key in required_keys:
        if key not in script:
            logger.warning(f"Campo faltante: {key}")
            return False

    if not isinstance(script["scenes"], list) or len(script["scenes"]) < 4:
        logger.warning(f"Pocas scenes: {len(script.get('scenes', []))} (minimo 4)")
        return False

    word_count = len(script.get("script_text", "").split())
    if word_count < 30:
        # Intentar reconstruir desde scenes si el script_text es demasiado corto
        reconstructed = " ".join(s.get("text", "") for s in script["scenes"])
        reconstructed_words = len(reconstructed.split())
        if reconstructed_words >= 30:
            script["script_text"] = reconstructed
            word_count = reconstructed_words
            logger.warning(f"script_text reconstruido desde scenes: {word_count} palabras")
        elif word_count > 0:
            # Al menos tiene algo — aceptar con advertencia
            logger.warning(f"Script corto ({word_count} palabras) — aceptando con lo disponible")
        else:
            logger.warning(f"Script demasiado corto: {word_count} palabras")
            return False

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

    titulo  = story["titulo"]
    historia = story["historia"]
    fuente   = story.get("fuente", "Reddit")

    # Calcular tokens: narración en español (~1.5x palabras originales) + JSON overhead (~400 tokens)
    # Schema simplificado (sin storyboard): ~1200-1600 tokens típico
    # Cap en 2200 para historias largas; mínimo 1400 para que el JSON no se trunque
    story_words   = len(historia.split())
    max_tokens    = min(2200, max(1400, int(story_words * 2.5)))
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
