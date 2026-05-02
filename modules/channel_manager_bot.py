"""
channel_manager_bot.py — Estratega del Canal @GataCuriosaS

Agente LLM especializado en crecimiento orgánico y monetización del canal.
Piensa como un director de contenido con foco en ingresos por Telegram Stars.

Diferencias con el gestor básico:
  ✦ Copy escrito con fórmulas PAS / AIDA / curiosity-gap
  ✦ Mix diario variado: confesiones + engagement + valor + urgencia
  ✦ CTAs de Stars diseñados para maximizar conversión
  ✦ Voz de marca consistente ("GATA CURIOSA" — íntimo, dramático, sin filtros)
  ✦ Historial de publicaciones para no repetir

Mix diario recomendado (4 slots):
  Slot 1 — Post de valor gratis (reflexión/tip)    → capta nuevos subs
  Slot 2 — Confesión gratis + media Stars          → conversión directa
  Slot 3 — Post de engagement (debate/pregunta)    → retención orgánica
  Slot 4 — Confesión gratis + media Stars          → segundo pico nocturno
"""

import json
import logging
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests

import config
from modules.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

# Tipos de contenido del mix diario
CONTENT_CONFESSION  = "confession"
CONTENT_ENGAGEMENT  = "engagement"
CONTENT_VALUE       = "value"
CONTENT_URGENCY     = "urgency"


class ChannelManagerBot(BaseAgent):
    name       = "Estratega del Canal"
    role       = "Director de Contenido y Monetización"
    department = "Dpto. Distribución"

    def __init__(self):
        super().__init__()
        self._stats_file = Path(config.BASE_DIR) / "channel_stats.json"

    # ── API helpers ────────────────────────────────────────────────────────────

    def _token(self) -> str:
        return getattr(config, "TELEGRAM_BOT_TOKEN", "")

    def _channel(self) -> str:
        return str(getattr(config, "TELEGRAM_CHANNEL_ID", ""))

    def _stars(self) -> int:
        return int(getattr(config, "TELEGRAM_CHANNEL_STARS", 50))

    def _ok(self) -> bool:
        return bool(self._token()) and bool(self._channel())

    def _api(self, method: str, **kwargs) -> dict:
        if not self._token():
            return {"ok": False}
        url = _API.format(token=self._token(), method=method)
        try:
            r = requests.post(url, timeout=60, **kwargs)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            self.log(f"API [{method}]: {e}")
            return {"ok": False}

    # ── Historial de publicaciones ─────────────────────────────────────────────

    def _load_stats(self) -> dict:
        if self._stats_file.exists():
            try:
                return json.loads(self._stats_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"posts": [], "hooks_used": []}

    def _save_stats(self, stats: dict) -> None:
        try:
            self._stats_file.write_text(
                json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.log(f"No se pudo guardar stats: {e}")

    def _record_post(self, content_type: str, hook: str,
                     message_ids: list[int] | None = None) -> None:
        stats = self._load_stats()
        stats["posts"].append({
            "type":        content_type,
            "hook":        hook[:100],
            "ts":          int(time.time()),
            "message_ids": message_ids or [],
        })
        stats["hooks_used"].append(hook[:80])
        # Mantener solo los últimos 200
        stats["posts"]      = stats["posts"][-200:]
        stats["hooks_used"] = stats["hooks_used"][-200:]
        self._save_stats(stats)

    def cleanup_old_channel_posts(self, max_age_hours: int | None = None) -> int:
        """
        Borra del canal los mensajes con más de max_age_hours horas.
        Retorna el número de mensajes eliminados.
        """
        if max_age_hours is None:
            max_age_hours = int(getattr(config, "TELEGRAM_CHANNEL_TTL_HOURS", 48))
        if not self._ok() or max_age_hours <= 0:
            return 0

        stats   = self._load_stats()
        cutoff  = time.time() - max_age_hours * 3600
        deleted = 0
        keep    = []

        for post in stats.get("posts", []):
            if post.get("ts", 0) < cutoff:
                for mid in post.get("message_ids", []):
                    if not mid:
                        continue
                    try:
                        r = self._api("deleteMessage", json={
                            "chat_id":    self._channel(),
                            "message_id": mid,
                        })
                        if r.get("ok"):
                            deleted += 1
                        time.sleep(0.3)
                    except Exception:
                        pass
            else:
                keep.append(post)

        stats["posts"] = keep
        self._save_stats(stats)
        if deleted:
            self.log(f"limpieza: {deleted} mensaje(s) eliminado(s) del canal")
        return deleted

    # ── Mix de contenido ───────────────────────────────────────────────────────

    def _plan_content_mix(self, slots: int) -> list[str]:
        """
        Planifica el mix de tipos de contenido para el día.
        Siempre al menos 1 confesión con Stars; el resto varía para no saturar.
        """
        if slots <= 1:
            return [CONTENT_CONFESSION]
        if slots == 2:
            return [CONTENT_VALUE, CONTENT_CONFESSION]
        if slots == 3:
            return [CONTENT_VALUE, CONTENT_CONFESSION, CONTENT_ENGAGEMENT]
        # slots >= 4
        base = [CONTENT_VALUE, CONTENT_CONFESSION, CONTENT_ENGAGEMENT, CONTENT_CONFESSION]
        extras = [CONTENT_URGENCY, CONTENT_ENGAGEMENT, CONTENT_VALUE]
        return (base + extras)[:slots]

    # ── Reescritor de historias morbosas/eróticas ──────────────────────────────

    def _rewrite_story_morbid(self, titulo: str, texto: str) -> tuple[str, str]:
        """
        Reescribe la historia como confesion adulta de vida real.

        Reglas clave:
        - Si es fanfic/fantasia (Hogwarts, vampiros, etc): lo convierte a mundo real
          (escuela→universidad, magia→situacion real, dragones→personas reales)
        - Cambia todos los nombres por nombres latinos
        - Primera persona, tono de confesion intima y explicita
        - Sube la tension sexual y el morbo al maximo
        - Resultado siempre en entornos reales: departamento, oficina, hotel, bar, casa
        """
        import re

        def _is_fictional(text: str) -> bool:
            """Pregunta al LLM si el texto tiene personajes/mundos de ficción."""
            try:
                resp = self._llm_call(
                    "El siguiente texto, ¿contiene personajes de ficcion (anime, series, "
                    "videojuegos, libros, peliculas), mundos imaginarios, magia, poderes "
                    "sobrenaturales, reencarnacion en otro mundo, o cualquier elemento "
                    "que no pueda ocurrir en la vida real cotidiana?\n"
                    "Responde UNICAMENTE con SI o NO.\n\n"
                    f"TEXTO:\n{text[:600]}",
                    max_tokens=5,
                ).strip().upper()
                return resp.startswith("SI") or resp.startswith("SÍ")
            except Exception:
                return False  # si falla la validacion, dejar pasar

        _cities   = ", ".join(config.CHANNEL_CITIES[:4])
        _f_names  = ", ".join(config.CHANNEL_FEMALE_NAMES[:5])
        _m_names  = ", ".join(config.CHANNEL_MALE_NAMES[:5])
        _FALLBACK_PROMPT = (
            "Inventa UNA confesion erotica y adulta de vida real en español latino. "
            "600-900 palabras. Primera persona. "
            "Sin ficcion, sin fantasia, sin anime, sin magia, sin poderes. "
            "Solo situaciones que pueden ocurrir en la vida real: "
            "infidelidad, deseo prohibido, secreto entre personas reales. "
            f"Ciudad: una de {_cities}. "
            f"Nombres femeninos: {_f_names}. Nombres masculinos: {_m_names}. "
            "Formato:\nTITULO: <max 80 chars, sin emojis>\nHISTORIA: <historia completa>"
        )

        prompt = (
            "Sos una escritora adulta especializada en confesiones prohibidas y literatura erotica.\n"
            "Tu voz es la de una mujer latina que confiesa en secreto algo que nunca le dijo "
            "a nadie. Cruda. Real. Sin filtro. Sin censura.\n\n"
            f"HISTORIA BASE:\nTitulo: {titulo}\n{texto[:2000]}\n\n"
            "REGLAS OBLIGATORIAS:\n\n"
            "0. CRITICO: el resultado final tiene que poder ocurrir en la vida real.\n"
            "   Si la historia base tiene fantasia, ficcion, magia, personajes de anime,\n"
            "   videojuegos, series, poderes sobrenaturales, reencarnacion, o cualquier\n"
            "   elemento imaginario: IGNORALOS POR COMPLETO.\n"
            "   Toma solo el conflicto emocional humano (traicion, deseo, secreto)\n"
            "   y narralo como si ocurriera en un departamento / oficina / hotel real.\n\n"
            "1. PRIMERA LINEA DE IMPACTO — arranca con algo que deje sin aliento:\n"
            "   'Tenia 24 anos cuando me acosté por primera vez con el marido de mi hermana.'\n"
            "   'Lo hicimos tres veces esa noche. El era el novio de mi mejor amiga.'\n"
            "   'Nunca pense que iba a desear tanto a alguien que no me pertenecia.'\n\n"
            "2. MUNDO REAL SIEMPRE:\n"
            f"   Cualquier personaje ficticio → persona real con nombre latino\n"
            f"   Cualquier lugar ficticio → {', '.join(config.CHANNEL_CITIES)}\n"
            "   Cualquier poder/elemento fantástico → emocion, alcohol, celos, secretos\n\n"
            "3. NOMBRES LATINOS siempre:\n"
            f"   Mujeres: {', '.join(config.CHANNEL_FEMALE_NAMES[:7])}\n"
            f"   Hombres: {', '.join(config.CHANNEL_MALE_NAMES[:7])}\n\n"
            "4. TENSION SEXUAL MAXIMA:\n"
            "   - Describí el deseo fisico con detalle: miradas, manos, calor, respiracion\n"
            "   - Las escenas de intimidad tienen que ser explicitas, no sugeridas\n"
            "   - El morbo tiene que estar en cada parrafo: traicion, secreto, lo prohibido\n\n"
            "5. ESTRUCTURA:\n"
            "   Parrafo 1: situacion + tension antes de 400 chars\n"
            "   Parrafo 2-3: escena se intensifica\n"
            "   Parrafo 4-5: acto prohibido con detalle sensorial\n"
            "   Final: revelacion impactante — CORTAR sin resolver\n\n"
            "6. Longitud: 600-900 palabras. Sin titulos internos. Solo prosa.\n\n"
            "Responde SOLO:\n"
            "TITULO: <frase que enganche, max 80 chars, SIN emojis, SIN ficcion>\n"
            "HISTORIA: <historia completa>"
        )
        try:
            raw = self._llm_call(prompt, max_tokens=2000)
            t_match = re.search(r"TITULO:\s*(.+)",   raw)
            h_match = re.search(r"HISTORIA:\s*(.+)", raw, re.DOTALL)
            nuevo_titulo   = t_match.group(1).strip()[:100] if t_match else titulo[:100]
            nueva_historia = h_match.group(1).strip()       if h_match else ""
            if len(nueva_historia) < 300:
                raise ValueError("muy corta")
            # Validacion LLM: si el resultado sigue teniendo ficcion, generar desde cero
            if _is_fictional(nuevo_titulo + ". " + nueva_historia[:500]):
                self.log("rewrite: resultado tiene ficcion — generando confesion nueva")
                raw2 = self._llm_call(_FALLBACK_PROMPT, max_tokens=2000)
                t2 = re.search(r"TITULO:\s*(.+)",   raw2)
                h2 = re.search(r"HISTORIA:\s*(.+)", raw2, re.DOTALL)
                nuevo_titulo   = t2.group(1).strip()[:100] if t2 else nuevo_titulo
                nueva_historia = h2.group(1).strip()       if h2 else nueva_historia
            self.log(f"historia reescrita: {len(nueva_historia)} chars")
            return nuevo_titulo, nueva_historia
        except Exception as e:
            self.log(f"rewrite fallo ({e}) — generando confesion nueva")
            # Si falla todo: siempre generar algo nuevo, nunca usar el original con ficcion
            try:
                raw3 = self._llm_call(_FALLBACK_PROMPT, max_tokens=2000)
                t3 = re.search(r"TITULO:\s*(.+)",   raw3)
                h3 = re.search(r"HISTORIA:\s*(.+)", raw3, re.DOTALL)
                if t3 and h3 and len(h3.group(1)) > 200:
                    return t3.group(1).strip()[:100], h3.group(1).strip()
            except Exception:
                pass
            return titulo, texto

    # ── Copy: hook de la historia (solo UNA linea, el gancho) ────────────────

    @property
    def _HOOK_FALLBACKS(self): return config.CHANNEL_HOOK_FALLBACKS

    def _craft_hook(self, titulo: str, texto: str) -> str:
        """
        Genera UNA sola frase gancho en primera persona.
        Si el LLM la produce con ficción, usa pool de fallbacks reales.
        """
        prompt = (
            "Eres el admin de un canal de confesiones de vida real en Telegram. "
            "Escribe UNA sola frase gancho en primera persona (maximo 90 caracteres). "
            "Solo drama real: infidelidad, secretos, deseo prohibido entre personas reales. "
            "Tiene que crear intriga sin revelar nada. Sin emojis. Sin punto al final.\n"
            "Ejemplos:\n"
            "  - Mi marido no sabe que yo se todo\n"
            "  - Esa noche descubri quien era realmente\n"
            "  - Lleve anos mintiendole a todos\n\n"
            f"Historia base: {titulo} — {texto[:300]}\n\n"
            "Responde SOLO con la frase gancho. "
            "Si la historia base tiene ficcion, inventa un gancho de vida real."
        )
        try:
            raw  = self._llm_call(prompt, max_tokens=60).strip()
            hook = raw.split("\n")[0].strip()[:120]
            if len(hook) > 15:
                # Validar: si el LLM dice que es ficcion, usar fallback
                check = self._llm_call(
                    f"¿Esta frase suena como algo que puede ocurrir en la vida real "
                    f"(no ficcion, no fantasia)?\n\"{hook}\"\nResponde solo SI o NO.",
                    max_tokens=5,
                ).strip().upper()
                if check.startswith("SI") or check.startswith("SÍ"):
                    return hook
        except Exception:
            pass
        return random.choice(self._HOOK_FALLBACKS)

    # ── Templates de copy — voz de GATA CURIOSA (personal, no de bot) ─────────

    # Pools de contenido — definidos en config.py para fácil edición sin tocar código
    @property
    def _STORY_INTROS(self):   return config.CHANNEL_STORY_INTROS
    @property
    def _STORY_CLIFF(self):    return config.CHANNEL_STORY_CLIFF
    @property
    def _ENGAGEMENT_POSTS(self): return config.CHANNEL_ENGAGEMENT_POSTS
    @property
    def _VALUE_POSTS(self):    return config.CHANNEL_VALUE_POSTS
    @property
    def _URGENCY_POSTS(self):  return config.CHANNEL_URGENCY_POSTS

    def _craft_engagement_post(self) -> str:
        return random.choice(self._ENGAGEMENT_POSTS)

    def _craft_value_post(self) -> str:
        return random.choice(self._VALUE_POSTS)

    # ── Imágenes de Pexels ─────────────────────────────────────────────────────

    def _fetch_images(self, query: str, n: int = 3) -> list[str]:
        api_key = getattr(config, "PEXELS_API_KEY", "")
        if not api_key:
            return []
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={"query": query[:60], "per_page": n, "orientation": "portrait"},
                timeout=15,
            )
            photos = r.json().get("photos", [])
            tmp    = Path(tempfile.mkdtemp(prefix="chbot_"))
            paths  = []
            for i, photo in enumerate(photos[:n]):
                url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large", "")
                if not url:
                    continue
                img = requests.get(url, timeout=20)
                if img.status_code == 200:
                    p = tmp / f"img_{i}.jpg"
                    p.write_bytes(img.content)
                    paths.append(str(p))
            return paths
        except Exception as e:
            self.log(f"fetch images: {e}")
            return []

    # ── Render tarjeta de historia (texto como imagen) ─────────────────────────

    def _render_story_card(self, hook: str, full_story: str, out_dir: Path) -> Optional[str]:
        """
        Renderiza la historia completa como imagen oscura — va en el paid media.
        Aparece completamente borrosa hasta que el usuario paga Stars.
        SIN emojis: Impact no los soporta y rompe el render.
        """
        try:
            import textwrap
            from PIL import Image, ImageDraw, ImageFont

            W, H = 1080, 1350
            img  = Image.new("RGB", (W, H), (6, 6, 14))
            draw = ImageDraw.Draw(img)

            # Franja roja dramática arriba
            for yi in range(200):
                t = 1 - yi / 200
                r = int(160 * t)
                draw.line([(0, yi), (W, yi)], fill=(r, 0, 28))
            draw.rectangle([0, 0, W - 1, H - 1], outline=(160, 0, 28), width=5)

            font_paths = [
                str(config.FONTS_DIR / "Impact.ttf"),
                "C:/Windows/Fonts/impact.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ]
            def _font(size: int):
                for fp in font_paths:
                    try:
                        return ImageFont.truetype(fp, size)
                    except Exception:
                        pass
                return ImageFont.load_default()

            # Header limpio — sin mayúsculas genéricas
            draw.text((W // 2, 50), "Historia completa", font=_font(46),
                      fill=(200, 200, 215), anchor="mm")
            draw.line([(60, 98), (W - 60, 98)], fill=(100, 0, 20), width=2)

            # Hook en rojo suave (no grita)
            hook_clean = hook.replace('"', '').replace("'", "")[:110]
            hook_lines = textwrap.wrap(f"“{hook_clean}”", width=38)
            y = 128
            for line in hook_lines[:3]:
                draw.text((W // 2, y), line, font=_font(38),
                          fill=(230, 80, 80), anchor="mm")
                y += 48

            y += 18

            # Texto de la historia — mayor y más espaciado
            font_story = _font(34)
            story_clean = full_story.replace("\n\n", "\n").strip()

            story_lines: list[str] = []
            for paragraph in story_clean.split("\n"):
                wrapped = textwrap.wrap(paragraph.strip(), width=42) or [""]
                story_lines.extend(wrapped)
                story_lines.append("")  # espacio entre párrafos

            for line in story_lines:
                if y > H - 120:
                    draw.text((60, y), "— continúa —", font=_font(30),
                              fill=(90, 90, 110))
                    break
                draw.text((60, y), line, font=font_story, fill=(210, 210, 228))
                y += 46 if line else 14

            # Footer minimalista
            draw.line([(60, H - 80), (W - 60, H - 80)], fill=(40, 40, 55), width=1)
            draw.text((W // 2, H - 46), "GATA CURIOSA",
                      font=_font(26), fill=(90, 90, 110), anchor="mm")

            out_path = out_dir / "story_card.jpg"
            img.save(str(out_path), "JPEG", quality=93)
            self.log(f"story card renderizado: {out_path}")
            return str(out_path)
        except Exception as e:
            self.log(f"render story card ERROR: {e}")
            return None

    # ── Posts individuales ─────────────────────────────────────────────────────

    def _split_story_for_preview(self, story: str, target: int = 380) -> tuple[str, str]:
        """
        Corta la historia en el ultimo punto antes de 'target' chars.
        Retorna (preview, resto). Si la historia es corta, preview = toda la historia.
        """
        if len(story) <= target:
            return story, ""
        # Buscar ultimo punto seguido de espacio antes del limite
        cut = story.rfind(". ", 0, target)
        if cut == -1:
            cut = story.rfind(" ", 0, target)
        if cut == -1:
            cut = target
        else:
            cut += 1  # incluir el punto
        return story[:cut].strip(), story[cut:].strip()

    def _post_confession_pair(self, story: dict) -> bool:
        """
        Flujo completo de una confesion:
          1. Post GRATIS: comienzo de la historia hasta el punto mas tenso, luego corte
          2. Post PAGADO: historia COMPLETA como imagen borrosa — se desbloquea con Stars

        Retorna True solo si AMBOS posts se publican con exito.
        """
        titulo = story.get("titulo", "")
        texto  = story.get("historia", story.get("texto", ""))

        titulo, texto = self._rewrite_story_morbid(titulo, texto)
        hook          = self._craft_hook(titulo, texto)
        stars         = self._stars()

        # ── POST 1: GRATIS — intro personal + comienzo de historia + corte cinematográfico
        preview, _ = self._split_story_for_preview(texto, target=480)

        intro  = random.choice(self._STORY_INTROS)
        cliff  = random.choice(self._STORY_CLIFF).format(stars=stars)

        free_text = f"{intro}{preview}{cliff}"

        r1 = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     free_text[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        })
        if not r1.get("ok"):
            self.log(f"post gratis fallo: {r1.get('description', '?')}")
            return False

        free_msg_id = (r1.get("result") or {}).get("message_id")
        time.sleep(5)

        # ── POST 2: PAGADO — historia COMPLETA borrosa hasta pagar Stars ─────
        tmp_dir = Path(tempfile.mkdtemp(prefix="chbot_paid_"))
        paid_ok = False
        try:
            card_path = self._render_story_card(hook, texto, tmp_dir)
            imgs      = self._fetch_images(hook, n=2)

            all_media: list[str] = []
            if card_path and Path(card_path).exists():
                all_media.append(card_path)
            all_media.extend(p for p in imgs if Path(p).exists())
            all_media = all_media[:10]

            if not all_media:
                self.log("sin media para paid post — abortando")
                return False

            caption = random.choice(config.CHANNEL_PAID_CAPTIONS).format(stars=stars)
            media_json = [{"type": "photo", "media": f"attach://m{i}"}
                          for i in range(len(all_media))]
            files = {f"m{i}": open(p, "rb") for i, p in enumerate(all_media)}
            try:
                r2 = self._api("sendPaidMedia", data={
                    "chat_id":    self._channel(),
                    "star_count": str(stars),
                    "caption":    caption[:1024],
                    "parse_mode": "HTML",
                    "media":      json.dumps(media_json),
                }, files=files)
                if r2.get("ok"):
                    paid_ok  = True
                    paid_id  = (r2.get("result") or {}).get("message_id")
                    msg_ids  = [i for i in [free_msg_id, paid_id] if i]
                    self._record_post(CONTENT_CONFESSION, hook, msg_ids)
                    self.log(f"confesion publicada: '{hook[:60]}'")
                else:
                    self.log(f"paid media fallo: {r2.get('description', '?')}")
            finally:
                for f in files.values():
                    try:
                        f.close()
                    except Exception:
                        pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return paid_ok

    def _post_engagement(self) -> bool:
        post   = self._craft_engagement_post()
        result = self._api("sendMessage", json={
            "chat_id":    self._channel(),
            "text":       post[:4096],
            "parse_mode": "HTML",
        })
        ok = result.get("ok", False)
        if ok:
            mid = (result.get("result") or {}).get("message_id")
            self._record_post(CONTENT_ENGAGEMENT, post[:80], [mid] if mid else [])
            self.log("post de engagement publicado")
        return ok

    def _post_value(self) -> bool:
        post   = self._craft_value_post()
        result = self._api("sendMessage", json={
            "chat_id":    self._channel(),
            "text":       post[:4096],
            "parse_mode": "HTML",
        })
        ok = result.get("ok", False)
        if ok:
            mid = (result.get("result") or {}).get("message_id")
            self._record_post(CONTENT_VALUE, post[:80], [mid] if mid else [])
            self.log("post de valor publicado")
        return ok

    def _post_urgency(self) -> bool:
        stars    = self._stars()
        template = random.choice(self._URGENCY_POSTS)
        post     = template.format(stars=stars)
        result   = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     post[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        })
        ok = result.get("ok", False)
        if ok:
            mid = (result.get("result") or {}).get("message_id")
            self._record_post(CONTENT_URGENCY, post[:80], [mid] if mid else [])
            self.log("post de urgencia publicado")
        return ok

    # ── Teaser de YouTube con copy profesional ─────────────────────────────────

    def post_youtube_teaser(self, youtube_url: str, title: str, hook: str) -> bool:
        if not self._ok():
            return False
        channel_link = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
        prompt = (
            f"Sos la admin de un canal de confesiones en Telegram. Subiste un nuevo video de YouTube.\n"
            f"Título: '{title}'\nHook: '{hook[:100]}'\n\n"
            "Escribí un mensaje corto para tu canal de Telegram anunciando el video. "
            "Tono: como si le avisaras a una amiga. Sin emojis de catálogo (🔥✅❤️). "
            "Sin 'NUEVO VIDEO' en mayúsculas. Máximo 180 caracteres antes del link. "
            "Tiene que crear intriga sin hacer spoiler. "
            "Respondé solo con el texto (sin el link, lo agrego yo después)."
        )
        try:
            copy = self._llm_call(prompt, max_tokens=160).strip()
            # Limpiar markdown del LLM si aparece
            copy = copy.strip('"').strip("'")
            copy = f"{copy}\n\n▶️ {youtube_url}"
            if channel_link:
                copy += f"\n\nHistoria completa con imágenes → {channel_link}"
        except Exception:
            copy = (
                f"Subí algo nuevo. Miralo antes de que lo eliminen.\n\n"
                f"▶️ {youtube_url}"
                + (f"\n\nHistoria completa → {channel_link}" if channel_link else "")
            )

        result = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     copy[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": False,
        })
        if result.get("ok"):
            self.log(f"teaser YouTube: '{title[:55]}'")
        return result.get("ok", False)

    # ── Historia extendida explícita con paywall de Stars (post-YouTube) ────────

    def _generate_extended_story(
        self, title: str, hook: str, script_text: str, scenes: list[dict]
    ) -> str:
        base = script_text or " ".join(s.get("text", "") for s in scenes)
        prompt = (
            "Sos una escritora adulta especializada en confesiones prohibidas y literatura erótica.\n"
            "Tu voz es la de una mujer latina que confiesa algo que nunca le dijo a nadie.\n"
            "Cruda. Real. Sin filtro. Sin censura.\n\n"
            f"HISTORIA BASE:\nTítulo: {title}\nHook: {hook}\n\n{base[:2000]}\n\n"
            "REGLAS:\n"
            "1. Escribe la versión EXTENDIDA — el doble de larga. Arranca directamente con la acción.\n"
            "2. Las escenas íntimas son EXPLÍCITAS: detalla actos físicos, sensaciones, deseo, "
            "el morbo de lo prohibido. Vocabulario adulto directo. No sugerido, EXPLÍCITO.\n"
            "3. Agrega lo que el video no mostró: antes, durante, después. Múltiples escenas.\n"
            "4. Primera persona. Mundo real: departamento, hotel, oficina, auto, bar.\n"
            "5. Nombres latinos: Sofía, Valeria, Camila, Diego, Sebastián, Mateo, Emilio.\n"
            "6. Longitud: 600 a 1000 palabras. Solo prosa continua, sin títulos ni encabezados.\n\n"
            "Responde SOLO con la historia (sin título, sin prólogo)."
        )
        try:
            raw = self._llm_call(prompt, max_tokens=2000).strip()
            if len(raw) < 400:
                raise ValueError("demasiado corta")
            self.log(f"extended story: {len(raw)} chars")
            return raw
        except Exception as e:
            self.log(f"extended story LLM falló ({e}) — usando texto base")
            return base

    def _pexels_queries_for_script(self, scenes: list[dict]) -> list[str]:
        """Elige las 2 mejores queries de Pexels desde los image_prompts de las escenas."""
        priority_acts = {"CLIMAX", "CONFRONTACION", "DESCUBRIMIENTO", "FINAL", "REVELATION"}
        priority = [
            s.get("image_prompt", "").strip() for s in scenes
            if s.get("act", "").upper() in priority_acts and s.get("image_prompt", "").strip()
        ]
        fallback = [s.get("image_prompt", "").strip() for s in scenes if s.get("image_prompt", "").strip()]
        pool = priority or fallback
        seen: set[str] = set()
        queries: list[str] = []
        for q in pool:
            if q not in seen:
                seen.add(q)
                queries.append(q[:100])
            if len(queries) >= 2:
                break
        return queries or ["passionate couple dramatic intimate dark cinema", "woman alone dramatic portrait night"]

    def _render_story_cards(self, hook: str, full_story: str, out_dir: Path) -> list[str]:
        """
        Renderiza la historia completa como N imágenes paginadas.
        Cada imagen es una página de la historia — sin corte ni 'continúa'.
        Retorna lista de paths (vacía si PIL no está disponible).
        """
        try:
            import textwrap
            from PIL import Image, ImageDraw, ImageFont

            W, H = 1080, 1350
            HEADER_H   = 220   # espacio reservado para gradiente + hook + separador
            FOOTER_H   = 100   # espacio reservado para footer
            TEXT_Y0    = HEADER_H + 10
            TEXT_YMAX  = H - FOOTER_H
            LINE_H     = 46    # altura por línea de texto
            BLANK_H    = 14    # altura de línea vacía (párrafo)
            FONT_SIZE  = 34
            WRAP_WIDTH = 42

            font_paths = [
                str(config.FONTS_DIR / "Impact.ttf"),
                "C:/Windows/Fonts/impact.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ]
            def _font(size: int):
                for fp in font_paths:
                    try:
                        return ImageFont.truetype(fp, size)
                    except Exception:
                        pass
                return ImageFont.load_default()

            # Pre-procesar todas las líneas
            story_clean = full_story.replace("\n\n", "\n").strip()
            all_lines: list[str] = []
            for paragraph in story_clean.split("\n"):
                wrapped = textwrap.wrap(paragraph.strip(), width=WRAP_WIDTH) or [""]
                all_lines.extend(wrapped)
                all_lines.append("")  # espacio entre párrafos

            # Paginar: cuántas líneas caben por página
            def _lines_per_page() -> int:
                available = TEXT_YMAX - TEXT_Y0
                count = 0
                used = 0
                # Simular llenado para calcular máximo
                for _ in range(200):
                    h = LINE_H  # asumir línea de texto (peor caso)
                    if used + h > available:
                        break
                    used += h
                    count += 1
                return max(count, 8)

            MAX_LINES = _lines_per_page()
            pages: list[list[str]] = []
            for start in range(0, max(len(all_lines), 1), MAX_LINES):
                chunk = all_lines[start:start + MAX_LINES]
                if any(l.strip() for l in chunk):
                    pages.append(chunk)

            if not pages:
                pages = [all_lines[:MAX_LINES] or [full_story[:200]]]

            paths: list[str] = []
            hook_clean = hook.replace('"', '').replace("'", "")[:110]

            for page_idx, page_lines in enumerate(pages):
                img  = Image.new("RGB", (W, H), (6, 6, 14))
                draw = ImageDraw.Draw(img)

                # Franja roja
                for yi in range(200):
                    t = 1 - yi / 200
                    r = int(160 * t)
                    draw.line([(0, yi), (W, yi)], fill=(r, 0, 28))
                draw.rectangle([0, 0, W - 1, H - 1], outline=(160, 0, 28), width=5)

                # Header: página 1 muestra el hook; el resto solo el número
                if page_idx == 0:
                    draw.text((W // 2, 40), "Historia completa",
                              font=_font(44), fill=(200, 200, 215), anchor="mm")
                    draw.line([(60, 88), (W - 60, 88)], fill=(100, 0, 20), width=2)
                    y = 100
                    for line in textwrap.wrap(f'"{hook_clean}"', width=38)[:3]:
                        draw.text((W // 2, y), line, font=_font(34),
                                  fill=(230, 80, 80), anchor="mm")
                        y += 44
                else:
                    total = len(pages)
                    draw.text((W // 2, 55),
                              f"Página {page_idx + 1} de {total}",
                              font=_font(38), fill=(160, 160, 180), anchor="mm")
                    draw.line([(60, 100), (W - 60, 100)], fill=(100, 0, 20), width=2)

                # Cuerpo
                y = TEXT_Y0
                font_story = _font(FONT_SIZE)
                for line in page_lines:
                    if y >= TEXT_YMAX:
                        break
                    draw.text((60, y), line, font=font_story, fill=(210, 210, 228))
                    y += LINE_H if line.strip() else BLANK_H

                # Footer
                draw.line([(60, H - 80), (W - 60, H - 80)], fill=(40, 40, 55), width=1)
                draw.text((W // 2, H - 46), "GATA CURIOSA",
                          font=_font(26), fill=(90, 90, 110), anchor="mm")

                out_path = out_dir / f"story_card_{page_idx + 1}.jpg"
                img.save(str(out_path), "JPEG", quality=93)
                paths.append(str(out_path))

            self.log(f"story cards: {len(paths)} página(s) renderizada(s)")
            return paths

        except Exception as e:
            self.log(f"render story cards ERROR: {e}")
            return []

    def post_extended_story_paid(self, script: dict, stars: int | None = None) -> bool:
        """
        Envía historia extendida explícita bloqueada con Stars, justo después del teaser YouTube.

        Contenido desbloqueado: historia completa paginada en N imágenes + 1-2 fotos de Pexels.
        """
        if not self._ok():
            return False

        stars       = stars if stars is not None else self._stars()
        title       = script.get("title", "")
        hook        = script.get("hook", title)
        script_text = script.get("script_text", "")
        scenes      = script.get("scenes", [])

        story_text = self._generate_extended_story(title, hook, script_text, scenes)
        queries    = self._pexels_queries_for_script(scenes)

        tmp_dir = Path(tempfile.mkdtemp(prefix="chbot_ext_"))
        try:
            # Páginas de la historia (todas las que hagan falta, sin corte)
            card_paths = self._render_story_cards(hook, story_text, tmp_dir)

            # 1-2 fotos de Pexels que coincidan con la historia
            imgs: list[str] = []
            for q in queries:
                found = self._fetch_images(q, n=1)
                imgs.extend(p for p in found if Path(p).exists())
                if len(imgs) >= 2:
                    break

            # Orden: fotos de Pexels primero (visual impacto), luego páginas de historia
            all_media: list[str] = []
            all_media.extend(imgs[:2])
            all_media.extend(p for p in card_paths if Path(p).exists())
            all_media = all_media[:10]  # límite Telegram

            if not all_media:
                self.log("extended story paid: sin media — abortando")
                return False

            caption    = f"{hook}\n\n📖 Historia completa ({len(card_paths)} páginas) — {stars} ⭐"
            media_json = [{"type": "photo", "media": f"attach://m{i}"}
                          for i in range(len(all_media))]
            files = {f"m{i}": open(p, "rb") for i, p in enumerate(all_media)}
            try:
                r = self._api("sendPaidMedia", data={
                    "chat_id":    self._channel(),
                    "star_count": str(stars),
                    "caption":    caption[:1024],
                    "parse_mode": "HTML",
                    "media":      json.dumps(media_json),
                }, files=files)
                ok = r.get("ok", False)
                if ok:
                    mid = (r.get("result") or {}).get("message_id")
                    self._record_post("extended_story", hook[:80], [mid] if mid else [])
                    self.log(f"extended story paid OK: '{title[:55]}' ({len(card_paths)} páginas)")
                else:
                    self.log(f"extended story paid fallo: {r.get('description', '?')}")
                return ok
            finally:
                for f in files.values():
                    try:
                        f.close()
                    except Exception:
                        pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Estrategia diaria completa ─────────────────────────────────────────────

    def run_daily_strategy(self, slots: int = 4) -> int:
        """
        Ejecuta la estrategia de contenido del día.
        Publica `slots` posts con mix variado: confesiones + engagement + valor + urgencia.
        Retorna el número de posts publicados exitosamente.
        """
        if not self._ok():
            self.log("canal no configurado — omitiendo estrategia diaria")
            return 0

        from modules.scraper import get_story_for_channel

        # Borrar mensajes viejos antes de publicar los nuevos
        deleted = self.cleanup_old_channel_posts()
        if deleted:
            self.notify(f"🗑️ Canal: {deleted} mensaje(s) antiguo(s) eliminado(s)")

        plan      = self._plan_content_mix(slots)
        published = 0

        self.log(f"plan del día: {plan}")
        self.notify(f"📢 Canal: iniciando estrategia diaria ({slots} posts)")

        for i, content_type in enumerate(plan):
            try:
                ok = False

                if content_type == CONTENT_CONFESSION:
                    story = get_story_for_channel()
                    if not story:
                        self.log(f"slot {i+1} ({content_type}): sin historia disponible — saltando")
                        continue
                    ok = self._post_confession_pair(story)

                elif content_type == CONTENT_ENGAGEMENT:
                    ok = self._post_engagement()

                elif content_type == CONTENT_VALUE:
                    ok = self._post_value()

                elif content_type == CONTENT_URGENCY:
                    ok = self._post_urgency()

                if ok:
                    published += 1
                    self.log(f"slot {i+1}/{slots} [{content_type}] ✓")
                else:
                    self.log(f"slot {i+1}/{slots} [{content_type}] ✗")

                if i < len(plan) - 1:
                    time.sleep(30)

            except Exception as e:
                self.log(f"slot {i+1} [{content_type}] error: {e}")

        if published:
            self.notify(f"✅ Canal: {published}/{slots} posts publicados")
        return published
