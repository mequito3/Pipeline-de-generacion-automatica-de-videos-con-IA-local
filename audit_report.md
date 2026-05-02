# Auditoría — 2026-04-30

## Resumen ejecutivo
- Archivos analizados: 36 (32 módulos + config.py + main.py + server.py + herramientas)
- Problemas críticos: 8
- Problemas medios: 9
- Problemas menores: 7

---

## Problemas críticos (rompen producción o convenciones core)

### [CRIT-009] [✅ resuelto en commit pendiente] getattr(config, X, fallback) con fallback divergente al valor real de config
**Archivo(s):** `main.py:784,979`
**Tipo:** hardcode + fuente de verdad duplicada
**Descripción:** Múltiples accesos a config usan getattr con fallback numérico distinto al default definido en config.py. Si config falla silenciosamente el sistema corre con parámetros incorrectos.
**Propuesta:** Acceso directo config.X. Si la constante no existe, agregarla a config.py.
**Esfuerzo:** M

---



### [CRIT-001] `OLLAMA_TIMEOUT` en `config.py` es 600s pero `_call_ollama` usa hardcode 180s [✅ resuelto en commit pendiente]
**Archivo(s):** `modules/script_generator.py:628`
**Tipo:** hardcode
**Descripción:** `config.OLLAMA_TIMEOUT` está definido en `config.py` con valor por defecto 600 segundos (y acepta .env). Sin embargo, en `_call_ollama()` el fallback hardcodeado es `180`:
```python
MAX_GEN_SECS = int(getattr(config, "OLLAMA_TIMEOUT", 180))
```
Si por algún motivo `config` no está disponible, el timeout efectivo es 180s, ignorando lo configurado en .env (que puede ser 600 o más). El valor de fallback debería ser el mismo que en `config.py` (600), o simplemente usar `config.OLLAMA_TIMEOUT` directamente sin fallback alternativo.
**Evidencia:**
```python
# config.py:32
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))
# script_generator.py:628
MAX_GEN_SECS = int(getattr(config, "OLLAMA_TIMEOUT", 180))  # ← 180 ≠ 600
```
**Propuesta:** Cambiar el fallback de `getattr` a `600` para que coincida con `config.py`, o usar `config.OLLAMA_TIMEOUT` sin fallback alternativo.
**Esfuerzo:** S

---

### [CRIT-002] resuelto — Listas `CONNECTORS`, `female_words`/`male_words` y `_CUTS` duplicadas dentro de `script_generator.py`
**Archivo(s):** `modules/script_generator.py:818,846,888` y `modules/script_generator.py:1272,1289,1328`
**Tipo:** duplicado
**Descripción:** Las constantes `CONNECTORS` (lista de 19 conectores), `female_words`/`male_words` (conjuntos de palabras de género) y `_CUTS` (lista de 8 ángulos de cámara) están definidas **dos veces** dentro del mismo archivo, una en `_validate_script()` y otra en `_validate_story_script()`. Cualquier corrección en una copia debe hacerse en la otra manualmente — alta probabilidad de divergencia futura.
**Evidencia:**
```python
# líneas 818 y 1272 — idénticas:
female_words = {"woman", "girl", "female", "mujer", "chica"}
# líneas 846 y 1289 — idénticas:
CONNECTORS = ["entonces", "de repente", "fue cuando", ...]
# líneas 888 y 1328 — idénticas:
_CUTS = ["extreme close-up of face...", ...]
```
**Propuesta:** Extraer las tres constantes a nivel de módulo (fuera de las funciones) y referenciarlas desde ambas funciones. Los conjuntos de género podrían ir en `config.py` si se quiere control externo.
**Esfuerzo:** S

**Nota post-análisis:** `_CUTS` no era duplicado real — eran dos sets intencionales por modo. Resuelto promoviendo a constantes con nombres semánticos diferenciados (`_CUTS_CONFESSION` y `_CUTS_STORY`), no unificando.
[✅ resuelto en commit pendiente]

---

### [CRIT-003] ✅ resuelto en commit pendiente — `server.py` no setea `CUDA_VISIBLE_DEVICES=-1` antes de importar módulos ML
**Archivo(s):** `server.py` (completo, sin línea CUDA)
**Tipo:** riesgo
**Descripción:** `server.py` es un punto de entrada alternativo (Render/cloud). Importa `main.py` y `config` en un hilo background sin establecer `CUDA_VISIBLE_DEVICES=-1` primero. En `main.py` este bloque existe y está documentado como crítico para evitar BSOD por conflicto VRAM con VoiceBox. `server.py` lo omite completamente, exponiendo el mismo riesgo que el proyecto documentó explícitamente.
**Evidencia:**
```python
# main.py:36 — protección presente
_os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

# server.py:1-17 — sin ninguna protección CUDA antes de:
from main import _run_scheduler, _safe_run_factory  # línea 88
```
**Propuesta:** Añadir el bloque idéntico de `CUDA_VISIBLE_DEVICES=-1` al inicio de `server.py`, antes de cualquier import de módulos del proyecto.
**Esfuerzo:** S

---

### [CRIT-004] Magic numbers `days_to_keep=7` y `max_files=30` hardcodeados en `main.py` (ignorando `config.py`) [✅ resuelto en commit pendiente]
**Archivo(s):** `main.py:736,737,846,847`
**Tipo:** hardcode
**Descripción:** Las constantes `CLEANUP_DAYS_TO_KEEP` y `LOGS_MAX_FILES` están correctamente definidas en `config.py`. Sin embargo, en el scheduler de `main.py` las llamadas pasan los valores hardcodeados `7` y `30` en lugar de leer de `config`:
```python
_cleanup_old_runs(days_to_keep=7)  # ignora config.CLEANUP_DAYS_TO_KEEP
_rotate_logs(max_files=30)         # ignora config.LOGS_MAX_FILES
```
Esto ocurre en **dos lugares distintos** del scheduler (líneas 736-737 y 846-847). Si el usuario cambia estos valores en `.env`, el scheduler los ignora.
**Evidencia:**
```python
# config.py:505-506
CLEANUP_DAYS_TO_KEEP: int = int(os.getenv("CLEANUP_DAYS_TO_KEEP", "7"))
LOGS_MAX_FILES:       int = int(os.getenv("LOGS_MAX_FILES",       "30"))
# main.py:736-737 y 846-847
_cleanup_old_runs(days_to_keep=7)   # ← hardcode
_rotate_logs(max_files=30)          # ← hardcode
```
También en `server.py:123-124` ocurre lo mismo con valores distintos (`days_to_keep=3`, `max_files=20`), violación adicional.
**Propuesta:** Reemplazar los valores literales por `config.CLEANUP_DAYS_TO_KEEP` y `config.LOGS_MAX_FILES`. En `server.py`, añadir constantes propias o usar las mismas de `config`.
**Esfuerzo:** S

---

### [CRIT-005] ✅ resuelto — `_TIKTOK_COMMENTS` en `tiktok_uploader.py` — lista pequeña hardcodeada de 12 elementos (violación pool 20+)
**Archivo(s):** `modules/tiktok_uploader.py:40-53`
**Tipo:** hardcode / convención
**Descripción:** La convención del proyecto exige pools de 20+ elementos o generación dinámica para evitar repetición detectable. `_TIKTOK_COMMENTS` tiene solo **12 entradas** hardcodeadas dentro del módulo, no en `config.py`, y no usa el LLM para generación dinámica. Dado que `tiktok_uploader.py` también puede postear comentarios propios en el propio video (`post_comment`), este pool pequeño se agotará rápidamente con contenido repetitivo, aumentando el riesgo de flag de spam en TikTok.
**Evidencia:**
```python
_TIKTOK_COMMENTS = [          # 12 elementos, módulo local, no en config
    "si defiendes lo que hizo esta persona...",
    "hay gente que hace esto y luego duerme...",
    ...  # 10 más
]
```
**Propuesta:** Mover a `config.py` como `TIKTOK_COMMENTS_POOL` con mínimo 20 elementos, o delegar siempre al LLM (como ya hace `tiktok_growth_agent.py` con `_generate_tt_comment()`).
**Esfuerzo:** M

---

### [CRIT-006] ✅ resuelto — `CTA_COMMENTS` y `CTA_FOLLOW` en `config.py` — pools de 5 y 6 elementos (violación pool 20+)
**Archivo(s):** `config.py:206-220`
**Tipo:** hardcode / convención
**Descripción:** `CTA_COMMENTS` tiene **5 entradas** y `CTA_FOLLOW` tiene **6 entradas**. Ambas listas se usan en cada video generado (en `video_assembler.py` y `company.py`). La convención exige mínimo 20 opciones para evitar repetición detectada por el algoritmo de YouTube. Con 3 videos/día, la rotación de 5 CTAs se completa en menos de 2 días.
**Evidencia:**
```python
CTA_COMMENTS: list[str] = [   # 5 elementos
    "Comenta tu respuesta abajo",
    "Cuéntame qué piensas en los comentarios",
    ...
]
CTA_FOLLOW: list[str] = [     # 6 elementos
    "Sígueme — mañana publico...",
    ...
]
```
**Propuesta:** Ampliar ambas listas a mínimo 20 elementos cada una, o reemplazar por generación dinámica con LLM (como ya hace el `voiced_cta` en `script_generator.py`).
**Esfuerzo:** S

---

### [CRIT-007] ✅ resuelto — `CHANNEL_FEMALE_NAMES` (15 items) y `CHANNEL_CITIES` (8 items) por debajo del umbral mínimo de 20
**Archivo(s):** `config.py:423-438`
**Tipo:** hardcode / convención
**Descripción:** `CHANNEL_FEMALE_NAMES` tiene 15 entradas, `CHANNEL_MALE_NAMES` tiene 15, y `CHANNEL_CITIES` tiene solo **8** entradas. La convención exige pools de 20+. `CHANNEL_CITIES` es especialmente crítico: con 8 ciudades y el bot publicando 2 posts/día en el canal Telegram, la repetición de ciudad en las confesiones será evidente en menos de una semana, rompiendo la ilusión de que son historias de diferentes personas.
**Evidencia:**
```python
CHANNEL_CITIES: list[str] = [  # 8 elementos — muy por debajo del umbral
    "Buenos Aires", "Bogotá", "Ciudad de México",
    "Madrid", "Lima", "Santiago", "Medellín", "Montevideo",
]
```
**Propuesta:** Ampliar `CHANNEL_CITIES` a mínimo 20 ciudades latinoamericanas/hispanohablantes. Ampliar ambos pools de nombres a 20+.
**Esfuerzo:** S

---

### [CRIT-008] ✅ resuelto — Tres constantes de límites de TikTok hardcodeadas en `tiktok_growth_agent.py` sin pasar por `config.py`
**Archivo(s):** `modules/tiktok_growth_agent.py:48-50`
**Tipo:** hardcode / convención
**Descripción:** `DAILY_COMMENT_LIMIT`, `DAILY_LIKE_LIMIT` y `DAILY_FOLLOW_LIMIT` son magic numbers definidos directamente en el módulo, sin correspondencia en `config.py`. Comparar con `growth_agent.py` (YouTube) donde los equivalentes sí leen de `config`:
```python
# growth_agent.py — correcto
DAILY_EXTERNAL_LIMIT = getattr(config, "GROWTH_DAILY_EXTERNAL_LIMIT", 5)
# tiktok_growth_agent.py — violación
DAILY_COMMENT_LIMIT = 5   # hardcode
DAILY_LIKE_LIMIT    = 20  # hardcode
DAILY_FOLLOW_LIMIT  = 3   # hardcode
```
Ajustar estos límites requiere editar código en lugar de `.env`.
**Propuesta:** Añadir `TIKTOK_DAILY_COMMENT_LIMIT`, `TIKTOK_DAILY_LIKE_LIMIT`, `TIKTOK_DAILY_FOLLOW_LIMIT` a `config.py` y leer desde allí.
**Esfuerzo:** S

---

## Problemas medios

### [MED-001] resuelto — `_call_groq` y `_call_openai` en `script_generator.py` duplican la lógica HTTP de `llm_service.py`
**Archivo(s):** `modules/script_generator.py:473-579`
**Tipo:** duplicado
**Descripción:** `llm_service.py` existe precisamente para centralizar llamadas a Groq/OpenAI. Sin embargo, `script_generator.py` implementa sus propias funciones `_call_groq()` y `_call_openai()` que reproducen la lógica HTTP manualmente (headers, retry, status 429, rate-limit). La diferencia es que `script_generator.py` usa `response_format: json_object` y hace print de progreso — pero esto podría pasarse como parámetros a `llm_service.call_llm()`. La duplicación implica que si cambia la URL de Groq o la gestión del rate-limit, hay que actualizar dos lugares.
**Propuesta:** Extender `llm_service.call_llm()` para aceptar `response_format` y `verbose` opcionales, y eliminar `_call_groq`/`_call_openai` de `script_generator.py`.
**Esfuerzo:** M

---

### [MED-002] resuelto — Rutas de archivos log hardcodeadas en módulos en lugar de usar `config.BASE_DIR`
**Archivo(s):** `modules/growth_agent.py:60`, `modules/tiktok_growth_agent.py:51`, `modules/analytics_agent.py:46`
**Tipo:** hardcode
**Descripción:** Los archivos de log de actividad usan `Path(__file__).parent.parent` en lugar de `config.BASE_DIR`:
```python
GROWTH_LOG_FILE   = Path(__file__).parent.parent / "growth_log.json"
TT_LOG_FILE       = Path(__file__).parent.parent / "tiktok_growth_log.json"
ANALYTICS_LOG_FILE = Path(__file__).parent.parent / "analytics_log.json"
```
Si el proyecto se mueve o se ejecuta desde otro directorio, estas rutas pueden apuntar incorrectamente. Adicionalmente estas rutas deberían estar en `config.py` para permitir sobreescritura vía `.env`.
**Propuesta:** Definir `GROWTH_LOG_FILE`, `TT_LOG_FILE`, `ANALYTICS_LOG_FILE` en `config.py` usando `BASE_DIR`.
**Esfuerzo:** S

---

### [MED-003] resuelto — `_tg_ctas` — lista inline de 8 CTAs de Telegram en `company.py` (no en config, sin pool 20+)
**Archivo(s):** `modules/company.py:302-311`
**Tipo:** hardcode / convención
**Descripción:** Una lista de 8 textos de CTA para la descripción de YouTube con link a Telegram está definida inline dentro de `PublishingAgent.run()`. No está en `config.py` y tiene solo 8 elementos. Se selecciona uno aleatorio en cada video.
**Evidencia:**
```python
_tg_ctas = [
    f"más historias en mi canal de telegram → {channel_link}",
    ...  # 7 más
]
```
**Propuesta:** Mover a `config.py` como `TELEGRAM_CTA_POOL` con 20+ elementos.
**Esfuerzo:** S

---

### [MED-004] resuelto — `sys.path.insert(0, ...)` en 14 módulos — patrón de path hacking innecesario y repetitivo
**Archivo(s):** `modules/analytics_agent.py:30`, `modules/growth_agent.py:36`, `modules/youtube_uploader.py:32`, y 11 módulos más
**Tipo:** duplicado / convención
**Descripción:** 14 de los módulos usan `sys.path.insert(0, str(Path(__file__).parent.parent))` para poder importar `config`. Este patrón es señal de que el proyecto no tiene un `setup.py`/`pyproject.toml` ni se instala como paquete. Si el proyecto se ejecuta siempre desde la raíz (lo que sugiere `main.py`), este `sys.path.insert` es redundante en los módulos — el import de `config` funciona directamente. Si no es redundante, significa que hay módulos ejecutándose como scripts sueltos, lo que viola la arquitectura.
**Propuesta:** Centralizar el `sys.path.insert` en un único lugar de arranque (solo `main.py` y `server.py`) y eliminarlo de los módulos. Alternativamente, añadir un `pyproject.toml` con `packages = ["."]`.
**Esfuerzo:** M

---

### [MED-005] resuelto — Edge TTS llamado con `asyncio.run()` en contexto sincrónico — incompatible con event loop ya activo
**Archivo(s):** `modules/tts_engine.py:1457`
**Tipo:** riesgo
**Descripción:** `_generate_with_edge_tts()` es una función sincrónica que internamente llama a `asyncio.run(_edge_tts_generate(...))`. Si esta función es invocada desde un contexto donde ya hay un event loop activo (por ejemplo, desde `growth_agent.py` que corre todo en `asyncio`), producirá `RuntimeError: This event loop is already running`. El módulo `tts_engine` puede ser invocado desde pipelines async futuros.
**Propuesta:** Reemplazar `asyncio.run(...)` con un wrapper que detecte si hay un loop activo (`asyncio.get_event_loop().is_running()`) y use `loop.run_until_complete()` como fallback, o hacer `_generate_with_edge_tts` async desde el origen.
**Esfuerzo:** M

---

### [MED-006] resuelto — `DIVERSE_CHARACTERS` en `script_generator.py` — 10 entradas hardcodeadas en módulo, sin pasar por config
**Archivo(s):** `modules/script_generator.py:34-45`
**Tipo:** hardcode
**Descripción:** Pool de 10 descripciones de personajes físicos, definido como constante de módulo. Se usa en reintentos de generación de scripts. La convención exige pools de 20+ o generación dinámica. Con 10 opciones y reusos en reintentos, la diversidad de personajes se agota rápido. No está en `config.py`.
**Propuesta:** Ampliar a 20+ entradas en `config.py` como `DIVERSE_CHARACTERS_POOL`, o generar dinámicamente con LLM (combinaciones de demografías).
**Esfuerzo:** S

---

### [MED-007] resuelto — Logs y constantes con nombres en inglés mezclados con español en `tiktok_growth_agent.py`
**Archivo(s):** `modules/tiktok_growth_agent.py:48-51,101-112`
**Tipo:** convención
**Descripción:** La convención del proyecto exige todo en español: logs, comentarios, mensajes. En `tiktok_growth_agent.py` las constantes tienen nombres en inglés (`DAILY_COMMENT_LIMIT`, `DAILY_LIKE_LIMIT`, `DAILY_FOLLOW_LIMIT`, `TT_LOG_FILE`, `TT_SEARCHES`), los templates de comentarios mezclan español/inglés en la lógica, y el log file se llama `tiktok_growth_log.json` en inglés.
```python
DAILY_COMMENT_LIMIT = 5   # nombre en inglés
TT_LOG_FILE = ...          # nombre en inglés
```
Comparar con `growth_agent.py` donde los equivalentes también están en inglés (`DAILY_EXTERNAL_LIMIT`, `GROWTH_LOG_FILE`), por lo que el problema es sistémico en el módulo growth.
**Propuesta:** Según convención, renombrar constantes a español o al menos seguir un estilo consistente. Revisar todos los `logger.info()/warning()` para que estén en español.
**Esfuerzo:** S

---

### [MED-008] ✅ resuelto — `company.py:_disclaimer` hardcodeado — string que debería estar en `config.py`
**Archivo(s):** `modules/company.py:286`
**Tipo:** hardcode
**Descripción:** El texto legal disclaimer insertado en cada descripción de YouTube está hardcodeado como string literal en `PublishingAgent.run()`:
```python
_DISCLAIMER = "⚠️ Historia basada en confesiones reales. Nombres y detalles modificados."
```
Este texto puede necesitar ajuste legal o de branding. No está en `config.py` y no es configurable vía `.env`.
**Propuesta:** Mover a `config.py` como `VIDEO_DISCLAIMER` con valor por defecto igual al actual.
**Esfuerzo:** S

---

### [MED-009] resuelto — `analytics_agent.py` importa helpers privados de `growth_agent.py` (`_dismiss_consent`, `_get_channel_id`)
**Archivo(s):** `modules/analytics_agent.py:42`
**Tipo:** duplicado / convención
**Descripción:** `analytics_agent.py` importa funciones privadas (prefijo `_`) de `growth_agent.py`:
```python
from modules.growth_agent import _dismiss_consent, _get_channel_id
```
Funciones privadas no forman parte del API pública del módulo. Esta dependencia crea acoplamiento frágil: si `growth_agent.py` refactoriza esas funciones, `analytics_agent.py` rompe silenciosamente. Además, viola el principio de que funciones `_privadas` son internas al módulo.
**Propuesta:** Mover `_dismiss_consent` y `_get_channel_id` a un módulo compartido de helpers de Selenium (ej: `modules/selenium_helpers.py`) o hacer que `growth_agent.py` las exporte con nombres públicos.
**Esfuerzo:** M

---

## Problemas menores

### [MIN-001] resuelto — `export_session.py` y `test_growth_comments.py` — archivos raíz que nadie importa (código de utilidad sin aislamiento)
**Archivo(s):** `export_session.py`, `test_growth_comments.py`
**Tipo:** muerto / convención
**Descripción:** Ambos archivos son scripts de utilidad que no importa ningún módulo del proyecto ni están referenciados en `main.py`. Viven en la raíz junto al código de producción. `test_growth_comments.py` tiene comentarios en inglés ("Force UTF-8 output on Windows"). Deberían estar en un directorio `tools/` o `scripts/` separado para no contaminar el namespace de producción.
**Propuesta:** Mover a `tools/` (ya existe `tools/download_video.py`).
**Esfuerzo:** S

---

### [MIN-002] resuelto — `main.py` help text del argumento `--report` menciona WhatsApp en lugar de Telegram
**Archivo(s):** `main.py:910-911`
**Tipo:** convención
**Descripción:** El módulo `whatsapp_notifier.py` fue eliminado (aparece como `D` en git status), pero el help text del argumento `--report` aún dice "por WhatsApp":
```python
help="Generar y enviar el reporte ejecutivo por WhatsApp ahora"
```
El reporte se envía por Telegram. Documentación desactualizada que puede confundir.
**Propuesta:** Actualizar el help text a "por Telegram".
**Esfuerzo:** S

---

### [MIN-003] resuelto — `server.py` usa magic numbers `days_to_keep=3` y `max_files=20` distintos a los de `main.py` y `config.py`
**Archivo(s):** `server.py:123-124`
**Tipo:** hardcode
**Descripción:** `server.py` limpia runs con `days_to_keep=3` y logs con `max_files=20`, valores **distintos** a los de `main.py` (7 y 30) y a los de `config.py` (`CLEANUP_DAYS_TO_KEEP=7`, `LOGS_MAX_FILES=30`). Hay tres conjuntos de valores diferentes para los mismos parámetros. Comportamiento inconsistente según el punto de entrada.
**Propuesta:** Usar siempre `config.CLEANUP_DAYS_TO_KEEP` y `config.LOGS_MAX_FILES`.
**Esfuerzo:** S

---

### [MIN-004] resuelto — Comentario en inglés en `tts_engine.py:1447` mezclado con código en español
**Archivo(s):** `modules/tts_engine.py:1447`
**Tipo:** convención
**Descripción:** El bloque BSOD Protection y el comentario del label de género mezclan inglés y español:
```python
gender_label = "MASCULINO" if gender == "male" else "FEMENINO"
```
Los valores `"male"` y `"female"` son valores internos del sistema (válido), pero en `test_growth_comments.py:18` hay comentarios en inglés directamente: `"# Force UTF-8 output on Windows"`. La convención es todo en español.
**Propuesta:** Traducir comentarios en inglés a español en todos los módulos.
**Esfuerzo:** S

---

### [MIN-005] resuelto — `script_generator.py` doc-string de `_call_ollama()` incorrecto — dice "Intenta Groq primero" pero es la función Ollama-local
**Archivo(s):** `modules/script_generator.py:588-598`
**Tipo:** convención
**Descripción:** La función `_call_ollama()` tiene docstring que dice "Intenta Groq (cloud gratuito) primero; si falla, usa Ollama local." — lo cual describe correctamente el comportamiento de la función, pero el **nombre** `_call_ollama` es engañoso ya que Ollama es el último recurso, no el primero. Un lector nuevo asumirá que la función llama a Ollama directamente.
**Propuesta:** Renombrar a `_call_llm_with_fallback()` o `_generate_with_groq_or_ollama()` para reflejar el flujo real Groq→OpenAI→Ollama.
**Esfuerzo:** S

---

### [MIN-006] resuelto — `_CHANNEL_ID_CACHE` definido como `Path(__file__).parent.parent / "channel_id_cache.txt"` fuera de `config.py`
**Archivo(s):** `modules/analytics_agent.py:47`
**Tipo:** hardcode
**Descripción:** El archivo caché del Channel ID de YouTube está hardcodeado como path relativo en `analytics_agent.py`. No está en `config.py` y no puede configurarse externamente.
```python
_CHANNEL_ID_CACHE = Path(__file__).parent.parent / "channel_id_cache.txt"
```
**Propuesta:** Mover a `config.py` como `CHANNEL_ID_CACHE_FILE = BASE_DIR / "channel_id_cache.txt"`.
**Esfuerzo:** S

---

### [MIN-007] resuelto — `DIVERSE_CHARACTERS` en inglés — las descripciones de personajes deberían ser consistentes con el idioma del proyecto o estar explícitamente en inglés por razón técnica
**Archivo(s):** `modules/script_generator.py:34-45`
**Tipo:** convención
**Descripción:** Las descripciones de personajes están en inglés ("Hispanic woman, late 20s, dark wavy hair...") porque son image prompts para Pexels, lo cual es correcto técnicamente. Sin embargo no hay ningún comentario explicando el "por qué" de esta excepción a la convención de todo-en-español. Un agente futuro podría "corregirlo" a español rompiendo la integración con Pexels.
**Propuesta:** Añadir un comentario que explique que el inglés es intencional porque son prompts de búsqueda para la API de Pexels.
**Esfuerzo:** S

---

## Métricas globales
- LOC totales: ~19,668 (incluyendo config, main, server, módulos y herramientas)
- LOC en módulos (solo `modules/`): ~18,025
- % archivos sin docstring de módulo: 0% (todos los archivos tienen docstring de nivel módulo)
- Imports rotos detectados: 0 (los archivos eliminados `telegram_channel.py`, `wattpad_fetcher.py`, `whatsapp_notifier.py` no son importados por ningún módulo activo)
- Funciones/clases detectadas: ~337
- Módulos con `sys.path.insert` redundante: 14 de 32


