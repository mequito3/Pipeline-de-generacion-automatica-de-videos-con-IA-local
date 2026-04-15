"""
image_generator.py — Genera imágenes 1080x1920 para cada escena del script

Rutas según disponibilidad:
  1. Escena visual → ComfyUI Z-Image Turbo (~20-30s, IA local)
  2. Sin ComfyUI   → PIL fallback con gradiente oscuro
"""

import copy
import io
import json
import logging
import random
import re
import shutil
import time
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import config

logger = logging.getLogger(__name__)

# Resolución nativa de Z-Image Turbo — se escala a 1080x1920 después
# Configurable via SD_NATIVE_RES en .env (768=rápido, 1024=calidad)
def _native_res() -> int:
    return getattr(config, "SD_NATIVE_RES", 768)

def _turbo_steps() -> int:
    return getattr(config, "SD_TURBO_STEPS", 4)


# ─── Fuentes ──────────────────────────────────────────────────────────────────

def _find_font(size: int = 60) -> ImageFont.FreeTypeFont:
    """Carga la mejor fuente TTF disponible en el tamaño pedido."""
    for fp in config.FONTS_DIR.glob("*.ttf"):
        try:
            return ImageFont.truetype(str(fp), size)
        except Exception:
            pass
    for fp in [
        "C:/Windows/Fonts/impact.ttf",    # Impact — estándar viral de Shorts
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def detect_sd_backend() -> str:
    """
    Detecta qué backend de SD está disponible y accesible.

    Si SD_BACKEND está forzado ('comfyui' o 'a1111'), verifica conectividad
    antes de confirmar — evita que un servidor caído cause timeouts silenciosos
    durante la generación. En ese caso emite un warning claro y devuelve 'none'.
    """
    if config.SD_BACKEND == "comfyui":
        for endpoint in ["/system_stats", "/queue", "/"]:
            try:
                r = requests.get(f"{config.SD_COMFYUI_URL}{endpoint}", timeout=5)
                if r.status_code in (200, 404):
                    logger.info(f"ComfyUI confirmado en {config.SD_COMFYUI_URL}")
                    return "comfyui"
            except Exception:
                pass
        logger.warning(
            f"SD_BACKEND=comfyui pero {config.SD_COMFYUI_URL} no responde. "
            "Asegúrate de que ComfyUI esté corriendo. Usando fallback PIL."
        )
        return "none"

    if config.SD_BACKEND == "a1111":
        try:
            r = requests.get(f"{config.SD_A1111_URL}/sdapi/v1/sd-models", timeout=5)
            if r.status_code == 200:
                logger.info(f"A1111 confirmado en {config.SD_A1111_URL}")
                return "a1111"
        except Exception:
            pass
        logger.warning(
            f"SD_BACKEND=a1111 pero {config.SD_A1111_URL} no responde. "
            "Asegúrate de que A1111 esté corriendo. Usando fallback PIL."
        )
        return "none"

    # ── Modo auto: sondear ambos ──────────────────────────────────────────────
    for endpoint in ["/system_stats", "/queue", "/"]:
        try:
            r = requests.get(f"{config.SD_COMFYUI_URL}{endpoint}", timeout=5)
            if r.status_code in (200, 404):
                logger.info(f"ComfyUI detectado en {config.SD_COMFYUI_URL}")
                return "comfyui"
        except Exception:
            pass

    try:
        r = requests.get(f"{config.SD_A1111_URL}/sdapi/v1/sd-models", timeout=5)
        if r.status_code == 200:
            logger.info(f"A1111 detectado en {config.SD_A1111_URL}")
            return "a1111"
    except Exception:
        pass

    logger.warning(
        "Ningún backend SD detectado — usando fallback PIL.\n"
        "  Para imágenes IA, inicia uno de estos servidores:\n"
        "  • ComfyUI:  python main.py  (en directorio ComfyUI, puerto 8000)\n"
        "  • A1111:    ./webui.sh / webui.bat  (puerto 7860)\n"
        "  Luego ajusta SD_BACKEND en .env: 'comfyui' | 'a1111' | 'auto'"
    )
    return "none"


# Negative prompt words to replace if wrong gender is detected
_FEMALE_WORDS = {"woman", "girl", "female", "she", "her", "lady", "wife", "girlfriend"}
_MALE_WORDS   = {"man", "boy", "male", "he", "his", "guy", "husband", "boyfriend"}

# Palabras que indican que la escena es un retrato del narrador
# (y por tanto debe llevar character_description al frente)
_PORTRAIT_WORDS = {
    "portrait", "face", "expression", "close-up", "closeup", "silhouette",
    "tears", "crying", "sobbing", "screaming", "laughing", "staring",
    "looking", "smiling", "frowning", "shocked", "devastated", "angry",
}


def _enrich_prompt(raw: str, character_description: str = "", gender: str = "") -> str:
    """
    Construye el prompt final para SD:
    1. Solo antepone character_description si la escena es un retrato del narrador
       (no en escenas de animales, objetos, lugares, u otras personas)
    2. Corrige el género si el prompt menciona el sexo equivocado
    3. Añade estilo dramático/cinematográfico
    """
    prompt = raw.strip()

    # 1. Corregir género si el prompt menciona el sexo contrario
    if gender == "male":
        for fw in _FEMALE_WORDS:
            prompt = re.sub(rf'\b{fw}\b', "man", prompt, flags=re.IGNORECASE)
    elif gender == "female":
        for mw in _MALE_WORDS:
            prompt = re.sub(rf'\b{mw}\b', "woman", prompt, flags=re.IGNORECASE)

    # 2. Anteponer character_description solo si la escena es sobre el narrador.
    #    Si el prompt describe un animal, objeto, lugar u otras personas sin
    #    referencia a un retrato del narrador, no lo forzamos.
    if character_description:
        prompt_lower = prompt.lower()
        char_lower   = character_description.lower()
        is_narrator_scene = any(w in prompt_lower for w in _PORTRAIT_WORDS)
        if is_narrator_scene and char_lower[:20] not in prompt_lower:
            prompt = f"{character_description}, {prompt}"

    # 3. Sufijo de estilo cinematográfico/dramático
    return (
        f"{prompt}, "
        "dramatic lighting, extreme emotional tension, intense shadows, "
        "hyper-realistic, raw and visceral, high contrast, moody atmosphere, "
        "cinematic composition, 4k, sharp focus, no text, no watermark, "
        "photorealistic, ultra detailed"
    )


_WORKFLOW_JSON = config.COMFYUI_WORKFLOW_PATH

def _build_z_image_workflow(prompt: str, seed: int) -> dict:
    """Workflow Z-Image Turbo para ComfyUI API — cargado desde el JSON del usuario."""
    with open(_WORKFLOW_JSON, encoding="utf-8") as f:
        nodes = copy.deepcopy(json.load(f))
    # Inyectar prompt y seed dinámicamente
    nodes["57:27"]["inputs"]["text"] = prompt
    nodes["57:3"]["inputs"]["seed"]  = seed
    return {"client_id": str(uuid.uuid4()), "prompt": nodes}


def _comfyui_clear_queue() -> None:
    """Limpia la cola de ComfyUI antes de empezar."""
    try:
        queue = requests.get(f"{config.SD_COMFYUI_URL}/queue", timeout=5).json()
        ids   = ([item[1] for item in queue.get("queue_pending", [])]
                 + [item[1] for item in queue.get("queue_running", [])])
        if ids:
            requests.post(f"{config.SD_COMFYUI_URL}/queue", json={"delete": ids}, timeout=5)
            logger.info(f"Cola ComfyUI limpiada: {len(ids)} item(s)")
        requests.post(f"{config.SD_COMFYUI_URL}/interrupt", timeout=5)
    except Exception as e:
        logger.debug(f"_comfyui_clear_queue: {e}")



def _comfyui_submit(prompt: str, character_description: str = "", gender: str = "", seed: int | None = None) -> str:
    """Envía un prompt a ComfyUI y retorna el prompt_id (sin esperar)."""
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    workflow = _build_z_image_workflow(_enrich_prompt(prompt, character_description, gender), seed=seed)
    logger.info(f"Z-Image Turbo submit | seed={seed} | '{prompt[:60]}'")
    # timeout=90: el HTTP server de ComfyUI puede bloquearse hasta ~60s
    # mientras la GPU termina el job anterior antes de aceptar el siguiente.
    # 30s era demasiado corto con modelos grandes o GPU lenta.
    resp = requests.post(f"{config.SD_COMFYUI_URL}/prompt", json=workflow, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI HTTP {resp.status_code}: {resp.text[:200]}")
    prompt_id = resp.json().get("prompt_id")
    if not prompt_id:
        raise ValueError(f"Sin prompt_id: {resp.text[:100]}")
    return prompt_id


def _comfyui_download(outputs: dict, output_path: Path) -> str:
    """Descarga la imagen de los outputs de ComfyUI y la guarda como portrait."""
    for node_output in outputs.values():
        imgs = node_output.get("images")
        if not imgs:
            continue
        info  = imgs[0]
        img_r = requests.get(
            f"{config.SD_COMFYUI_URL}/view",
            params={"filename": info["filename"],
                    "subfolder": info.get("subfolder", ""),
                    "type":      info.get("type", "output")},
            timeout=60,
        )
        img_r.raise_for_status()
        img = Image.open(io.BytesIO(img_r.content)).convert("RGB")
        img = _to_portrait(img)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG", optimize=True)
        logger.info(f"Z-Image Turbo → {output_path.name} ({config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT})")
        return str(output_path)
    raise RuntimeError("ComfyUI completó sin imágenes en el output.")


# ─── Resolución portrait ──────────────────────────────────────────────────────

def _to_portrait(img: Image.Image) -> Image.Image:
    """Escala a 1080x1920 preservando ratio. Rellena con fondo borroso si es landscape."""
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    src_r = img.width / img.height
    tgt_r = W / H

    if abs(src_r - tgt_r) < 0.05:
        return img.resize((W, H), Image.LANCZOS)

    if src_r < tgt_r:
        # Más alta → escalar al ancho, recortar verticalmente
        scale  = W / img.width
        new_h  = int(img.height * scale)
        scaled = img.resize((W, new_h), Image.LANCZOS)
        top    = (new_h - H) // 2
        return scaled.crop((0, top, W, top + H))
    else:
        # Más ancha → relleno con fondo borroso oscuro
        scale  = W / img.width
        new_h  = int(img.height * scale)
        scaled = img.resize((W, new_h), Image.LANCZOS)
        bg     = img.resize((W, H), Image.LANCZOS)
        bg     = bg.filter(ImageFilter.GaussianBlur(radius=40))
        dark   = Image.new("RGBA", bg.size, (0, 0, 0, 140))
        bg     = Image.alpha_composite(bg.convert("RGBA"), dark).convert("RGB")
        y_off  = (H - new_h) // 2
        bg.paste(scaled, (0, y_off))
        return bg


# ─── Fallback PIL ─────────────────────────────────────────────────────────────

def _pil_fallback(text: str, output_path: Path, idx: int = 0) -> str:
    """Imagen de fallback: gradiente oscuro con texto de la escena."""
    palettes = [
        ((6, 6, 20),  (16, 16, 50)),
        ((20, 4, 4),  (50, 8,  8)),
        ((4, 20, 4),  (8,  45, 8)),
        ((4, 8, 20),  (8,  20, 55)),
        ((20, 10, 4), (50, 25, 8)),
    ]
    top, bot = palettes[idx % len(palettes)]
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(top[0] + (bot[0]-top[0])*t),
            int(top[1] + (bot[1]-top[1])*t),
            int(top[2] + (bot[2]-top[2])*t),
        ))

    if text:
        font  = _find_font(52)
        words = text.split()
        lines, current = [], []
        for word in words:
            test = " ".join(current + [word])
            if draw.textbbox((0, 0), test, font=font)[2] > W - 80 and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))

        line_h  = 68
        y_start = H // 2 - len(lines) * line_h // 2
        for i, line in enumerate(lines):
            bb = draw.textbbox((0, 0), line, font=font)
            x  = (W - (bb[2]-bb[0])) // 2
            y  = y_start + i * line_h
            draw.text((x+2, y+2), line, fill=(0,  0,  0),   font=font)
            draw.text((x,   y),   line, fill=(255,255,255), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    logger.info(f"PIL fallback → {output_path.name}")
    return str(output_path)


# ─── API pública ──────────────────────────────────────────────────────────────

def generate_images(
    scenes: list[dict],
    output_dir: str | Path,
    character_description: str = "",
    gender: str = "",
    post_seed: int | None = None,
) -> list[str]:
    """
    Genera imágenes para todas las escenas del script.
    character_description y gender se inyectan en cada prompt para
    mantener consistencia visual del personaje y género correcto.

    Optimizaciones:
      - Deduplicación de prompts: escenas con el mismo prompt comparten la imagen generada

    Rutas por escena:
      1. Prompt visual → ComfyUI batch (dedup + poll simultáneo)
      2. Sin ComfyUI   → PIL fallback con gradiente oscuro
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    backend     = detect_sd_backend()
    total       = len(scenes)
    image_paths: list[str | None] = [None] * total

    if backend == "comfyui":
        _comfyui_clear_queue()

    max_unique = getattr(config, "SD_MAX_IMAGES", 12)
    logger.info(
        f"Generando {total} imágenes | backend='{backend}' | "
        f"res={_native_res()}px | steps={_turbo_steps()} | max_unique={max_unique}"
    )
    start = time.time()

    # ── Paso 1: Clasificar escenas → ComfyUI o PIL fallback ───────────────────
    # comfyui_jobs: prompt → [(scene_idx, output_path)] — agrupa duplicados
    comfyui_jobs: dict[str, list[tuple[int, Path]]] = {}
    # unique_prompts_seen: control del cap de SD_MAX_IMAGES
    unique_prompts_seen: list[str] = []

    for i, scene in enumerate(scenes):
        out = output_dir / f"scene_{i:03d}.png"

        if out.exists():
            logger.info(f"Imagen cacheada: {out.name}")
            image_paths[i] = str(out)
            continue

        raw    = scene.get("image_prompt") or scene.get("text") or "dark background"
        prompt = raw.strip()

        # ── Cap de imágenes únicas: reciclar si ya alcanzamos el máximo ───────
        if prompt not in unique_prompts_seen and prompt not in comfyui_jobs:
            if len(unique_prompts_seen) >= max_unique and unique_prompts_seen:
                recycled = unique_prompts_seen[i % len(unique_prompts_seen)]
                comfyui_jobs.setdefault(recycled, []).append((i, out))
                logger.debug(f"Escena {i}: prompt reciclado (límite {max_unique} alcanzado)")
                continue
            unique_prompts_seen.append(prompt)

        # ── Ruta 1: ComfyUI — acumular para batch ─────────────────────────────
        if backend == "comfyui":
            comfyui_jobs.setdefault(prompt, []).append((i, out))
            continue

        # ── Ruta 2: PIL fallback ──────────────────────────────────────────────
        try:
            image_paths[i] = _pil_fallback(scene.get("text", prompt), out, i)
        except Exception as e:
            logger.error(f"Error fallback escena {i}: {e}")
            img = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), (8, 8, 20))
            img.save(str(out))
            image_paths[i] = str(out)

    # ── Paso 2: ComfyUI — submit uno a uno y esperar resultado antes del siguiente
    # (evita timeouts cuando ComfyUI bloquea su HTTP server mientras genera en GPU)
    if comfyui_jobs:
        n_unique = len(comfyui_jobs)
        n_total  = sum(len(v) for v in comfyui_jobs.values())
        if n_unique < n_total:
            logger.info(
                f"Dedup ComfyUI: {n_total} escenas → {n_unique} prompts únicos "
                f"(ahorro: {n_total - n_unique} generaciones)"
            )

        for prompt_idx, (prompt, scene_list) in enumerate(comfyui_jobs.items()):
            # ── Submit ────────────────────────────────────────────────────────
            scene_seed = None
            if post_seed is not None:
                scene_seed = (post_seed + prompt_idx * 137) % (2**31)
            try:
                prompt_id = _comfyui_submit(
                    prompt,
                    character_description=character_description,
                    gender=gender,
                    seed=scene_seed,
                )
                logger.info(f"Encolado {prompt_id[:8]}… → '{prompt[:55]}'")
            except Exception as e:
                logger.error(f"Error enviando '{prompt[:55]}': {e}")
                for idx, out in scene_list:
                    image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)
                continue

            # ── Poll hasta completar (300s por imagen) ────────────────────────
            img_timeout  = 300
            poll_elapsed = 0
            done         = False
            while poll_elapsed < img_timeout:
                time.sleep(2)
                poll_elapsed += 2
                try:
                    history = requests.get(
                        f"{config.SD_COMFYUI_URL}/history/{prompt_id}", timeout=10
                    ).json()
                except Exception as e:
                    logger.warning(f"Polling {prompt_id[:8]}: {e}")
                    continue

                if prompt_id not in history:
                    continue

                entry  = history[prompt_id]
                status = entry.get("status", {})

                if status.get("status_str") == "error":
                    logger.error(f"ComfyUI error {prompt_id[:8]}: {status.get('messages', [])}")
                    for idx, out in scene_list:
                        image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)
                    done = True
                    break

                outputs = entry.get("outputs", {})
                if not outputs:
                    if poll_elapsed % 15 == 0:
                        logger.info(f"Esperando imagen {prompt_idx+1}/{n_unique}… {poll_elapsed}s")
                    continue

                # Imagen lista — descargar y copiar para escenas duplicadas
                primary_idx, primary_out = scene_list[0]
                try:
                    result = _comfyui_download(outputs, primary_out)
                    image_paths[primary_idx] = result
                    for idx, out in scene_list[1:]:
                        shutil.copy2(result, str(out))
                        image_paths[idx] = str(out)
                        logger.info(f"Imagen reutilizada: {out.name}")
                except Exception as e:
                    logger.error(f"Error descargando {prompt_id[:8]}: {e}")
                    for idx, out in scene_list:
                        image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)
                done = True
                break

            if not done:
                logger.error(f"Timeout: {prompt_id[:8]} no completó en {img_timeout}s")
                for idx, out in scene_list:
                    image_paths[idx] = _pil_fallback(scenes[idx].get("text", ""), out, idx)

    # ── Paso 3: Safety net — ningún slot debe quedar None ─────────────────────
    for i, path in enumerate(image_paths):
        if path is None:
            out = output_dir / f"scene_{i:03d}.png"
            logger.warning(f"Slot {i} sin imagen, generando fallback de emergencia")
            try:
                image_paths[i] = _pil_fallback(scenes[i].get("text", ""), out, i)
            except Exception:
                img = Image.new("RGB", (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), (8, 8, 20))
                img.save(str(out))
                image_paths[i] = str(out)

    elapsed = time.time() - start
    ok_count = sum(1 for p in image_paths if p)
    logger.info(f"{ok_count}/{total} imágenes listas en {elapsed:.1f}s ({elapsed/total:.1f}s/img)")
    return image_paths
