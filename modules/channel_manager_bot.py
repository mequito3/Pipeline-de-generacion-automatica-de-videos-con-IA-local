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

    def _record_post(self, content_type: str, hook: str) -> None:
        stats = self._load_stats()
        stats["posts"].append({
            "type": content_type,
            "hook": hook[:100],
            "ts": int(time.time()),
        })
        stats["hooks_used"].append(hook[:80])
        # Mantener solo los últimos 200
        stats["posts"]      = stats["posts"][-200:]
        stats["hooks_used"] = stats["hooks_used"][-200:]
        self._save_stats(stats)

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

        prompt = (
            "Sos una escritora adulta especializada en confesiones prohibidas y literatura erotica.\n"
            "Tu voz es la de una mujer latina que confiesa en secreto algo que nunca le dijo "
            "a nadie. Cruda. Real. Sin filtro. Sin censura.\n\n"
            f"HISTORIA BASE:\nTitulo: {titulo}\n{texto[:2000]}\n\n"
            "REGLAS OBLIGATORIAS:\n\n"
            "1. PRIMERA LINEA DE IMPACTO — arranca con algo que deje sin aliento:\n"
            "   'Tenia 24 anos cuando me acosté por primera vez con el marido de mi hermana.'\n"
            "   'Lo hicimos tres veces esa noche. El era el novio de mi mejor amiga.'\n"
            "   'Nunca pense que iba a desear tanto a alguien que no me pertenecia.'\n\n"
            "2. MUNDO REAL SIEMPRE — si hay fantasia, transformala:\n"
            "   Fantasia/anime/Hogwarts → universidad, oficina, hotel, edificio, bar\n"
            "   Poderes/hechizos → alcohol, celos, llamadas, secretos, fotos\n"
            "   Criaturas → personas reales con personalidades intensas\n"
            "   Ciudad real: Buenos Aires, Bogota, Ciudad de Mexico, Madrid, Lima\n\n"
            "3. NOMBRES LATINOS siempre:\n"
            "   Mujeres: Sofia, Valeria, Camila, Andrea, Lucia, Daniela, Renata\n"
            "   Hombres: Diego, Mateo, Sebastian, Andres, Carlos, Tomas, Emilio\n\n"
            "4. TENSION SEXUAL MAXIMA — esto es lo mas importante:\n"
            "   - Describí el deseo fisico con detalle: miradas, manos, calor, respiracion\n"
            "   - Las escenas de intimidad tienen que ser explicitas, no sugeridas\n"
            "   - Describí como se sintio, que hizo, que le hicieron, que quisieron mas\n"
            "   - El morbo tiene que estar en cada parrafo: traicion, secreto, lo prohibido\n"
            "   - Usa vocabulario adulto directo cuando sea necesario\n"
            "   - Que el lector sienta que esta leyendo algo que no deberia leer\n\n"
            "5. ESTRUCTURA CRITICA — esto determina si la gente paga:\n"
            "   - Parrafo 1 (primeras 3-4 oraciones): presenta la situacion Y el momento prohibido.\n"
            "     El lector tiene que sentir la tension ANTES de los 400 caracteres.\n"
            "     Ejemplo: 'Llevabamos 6 meses trabajando juntos. Esa tarde se quedo hasta tarde\n"
            "     y quando apague la luz... el estaba detras de mi.'\n"
            "   - Parrafo 2-3: la escena se intensifica, detalle fisico y emocional\n"
            "   - Parrafo 4-5: el acto prohibido con maximo detalle sensorial\n"
            "   - Final: consecuencia o revelacion impactante — CORTAR sin resolver\n\n"
            "6. Longitud: entre 600 y 900 palabras. Sin titulos internos. Solo prosa continua.\n\n"
            "Responde SOLO en este formato:\n"
            "TITULO: <frase que enganche, maximo 80 chars, SIN emojis>\n"
            "HISTORIA: <historia completa, cruda, sin censura>"
        )
        try:
            raw = self._llm_call(prompt, max_tokens=2000)
            t_match = re.search(r"TITULO:\s*(.+)",   raw)
            h_match = re.search(r"HISTORIA:\s*(.+)", raw, re.DOTALL)
            nuevo_titulo   = t_match.group(1).strip()[:100] if t_match else titulo[:100]
            nueva_historia = h_match.group(1).strip()       if h_match else texto
            if len(nueva_historia) < 300:
                raise ValueError("muy corta")
            self.log(f"historia reescrita: {len(nueva_historia)} chars")
            return nuevo_titulo, nueva_historia
        except Exception as e:
            self.log(f"rewrite fallo ({e}) — usando original")
            return titulo, texto

    # ── Copy: hook de la historia (solo UNA linea, el gancho) ────────────────

    def _craft_hook(self, titulo: str, texto: str) -> str:
        """
        Genera UNA sola frase gancho en primera persona.
        Si Groq está disponible la usa; si no, extrae directamente del título.
        """
        import re
        prompt = (
            "Eres el admin de un canal de confesiones en Telegram. "
            "Escribe UNA sola frase gancho en primera persona (maximo 90 caracteres). "
            "Tiene que crear intriga sin revelar nada. Sin emojis. Sin punto al final.\n"
            "Ejemplos:\n"
            "  - Mi marido no sabe que yo se todo\n"
            "  - Esa noche descubri quien era realmente\n"
            "  - Lleve anos mintiendole a todos\n\n"
            f"Historia base: {titulo} — {texto[:300]}\n\n"
            "Responde SOLO con la frase gancho."
        )
        try:
            raw  = self._llm_call(prompt, max_tokens=60).strip()
            hook = raw.split("\n")[0].strip()[:120]
            if len(hook) > 15:
                return hook
        except Exception:
            pass
        # Fallback: limpiar el titulo
        return re.sub(r"[^\w\s,\.\!\?áéíóúüñÁÉÍÓÚÜÑ]", "", titulo)[:100].strip()

    # ── Templates de copy profesional (no dependen del LLM débil) ────────────

    # Post gratis: solo el hook + CTA minimo. No se revela nada del contenido.
    _FREE_TEMPLATES = [
        (
            "🔥 <b>CONFESIÓN REAL</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "Esta historia no está en YouTube.\n"
            "Solo aquí. Solo por <b>{stars} ⭐</b>\n\n"
            "⬇️ Desbloqueá justo abajo."
        ),
        (
            "⚠️ <b>HISTORIA REAL | ANÓNIMA</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "La guardé para este canal.\n"
            "Por <b>{stars} ⭐ Stars</b> la lees completa.\n\n"
            "👇 Está justo abajo."
        ),
        (
            "🗣️ <b>ME LO CONTARON EN SECRETO</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "No puedo publicar esto en otro lado.\n"
            "<b>{stars} ⭐</b> y es tuya.\n\n"
            "⬇️ Historia completa debajo."
        ),
        (
            "🔒 <b>CONFESIÓN #GATA</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "Demasiado real para YouTube.\n"
            "Aquí está completa — <b>{stars} ⭐ Stars</b>.\n\n"
            "👇 Tap para desbloquear."
        ),
    ]

    # Caption del paid media: lo que se ve ANTES de pagar (sin spoilers)
    _PAID_CAPTIONS = [
        (
            "🔒 <b>CONTENIDO EXCLUSIVO</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "Pagá <b>{stars} ⭐ Stars</b> y lees la historia completa.\n"
            "Borrosa hasta que desbloquees."
        ),
        (
            "🔞 <b>SOLO PARA LOS QUE SE ATREVEN</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "<b>{stars} ⭐</b> → historia completa, sin cortes.\n"
            "La imagen se desbloquea al pagar."
        ),
        (
            "🗝️ <b>HISTORIA COMPLETA AQUÍ ADENTRO</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "Desbloqueá con <b>{stars} ⭐ Stars</b>.\n"
            "Sin pagar, no se ve."
        ),
        (
            "⛔ <b>ACCESO RESTRINGIDO</b>\n\n"
            "<i>«{hook}»</i>\n\n"
            "Esta historia cuesta <b>{stars} ⭐</b>.\n"
            "Pagá y lees todo. Sin censura."
        ),
    ]

    # Posts de engagement: preguntas que dividen la audiencia
    _ENGAGEMENT_POSTS = [
        "Él la engañó con su mejor amiga.\nLe pidió perdón llorando. Dice que fue \"un error\".\n\n¿Tú perdonarías o lo dejarías sin pensarlo dos veces? 👇",
        "Lleva 3 años con él. Acaba de encontrar fotos borradas en su teléfono.\nÉl dice que no son nada.\n\n¿Le crees o ya empezás a dudar? 🤔",
        "Su ex la llama después de 1 año llorando.\nDice que cometió el peor error de su vida.\n\n¿Contestarías o colgarías directo? Comenten ⬇️",
        "Descubrió que su novio le mentía sobre su pasado.\nNo la engañó con otra, pero sí ocultó cosas importantes.\n\n¿Eso también es traición o no? 💭",
        "Él nunca le dijo \"te amo\" en 2 años.\nAhora que ella lo dejó, se lo dice todos los días.\n\n¿Lo tomarías en serio o ya es tarde? 👇",
        "Su mejor amiga le contaba sus secretos al ex de ella.\nTodo este tiempo. Sin decirle nada.\n\n¿La perdonás o eso no tiene perdón? Comenten 💬",
        "Llevan 5 años juntos. Ella encontró mensajes de otra.\nÉl dice que \"no llegó a nada\".\n\n¿Intentional o te vas? 🤷‍♀️",
        "Él la trataba mal. Ella se fue.\nAhora él cambió, dice que es otra persona.\n\n¿Las personas realmente cambian o solo aprenden a esconder mejor? 👇",
    ]

    # Posts de valor: reflexiones que engancha sin vender nada
    _VALUE_POSTS = [
        "Hay una diferencia entre perdonar y seguir aguantando.\n\nPerdonar es para vos. Seguir aguantando es para él.\n\nNo las confundas.",
        "A veces no es que no viste las señales.\nEs que las viste y elegiste creer que las cosas iban a cambiar.\n\nEso no te hace tonta. Te hace humana.",
        "El que te quiere de verdad no te hace dudar de su amor cada dos días.\n\nSi estás constantemente adivindando si le importás — ya tenés la respuesta.",
        "Hay personas que te quieren y personas que te necesitan.\nAprender a distinguirlos es la lección más cara que existe.",
        "No fue que te engañó.\nFue que después de que lo hizo, seguiste dudando de vos en lugar de dudar de él.",
        "La gente que te trata mal casi siempre encuentra la forma de hacerte sentir que es tu culpa.\n\nNo lo es.",
        "Alejarse de alguien que amás es difícil.\nQuedarte con alguien que te hace daño es más difícil todavía.\nElegís tu tipo de dolor.",
        "El amor que duele todo el tiempo no es amor.\nEs costumbre. Es miedo. Es no conocer otra cosa.\n\nPero no es amor.",
    ]

    # Posts de urgencia: empujan conversiones a Stars
    _URGENCY_POSTS = [
        "🔒 Esta semana subí 3 historias que no voy a volver a publicar.\n\nUna de ellas involucra a alguien de su propia familia.\n\n{stars} ⭐ y la desbloqueás.",
        "Hay historias que no puedo subir a YouTube.\nDemasiado reales. Demasiado crudas.\n\nEsas solo están aquí. Por {stars} ⭐ Stars.",
        "La historia que acabo de subir tiene más de 1.000 palabras.\nNo te la cuento gratis. No en este canal.\n\n{stars} ⭐ → historia completa, sin cortes.",
        "El contenido que más me piden es el que no puedo publicar en otro lado.\n\nEse contenido está aquí. {stars} ⭐ y es tuyo.",
        "Las historias detrás del candado son las que la gente quiere leer de verdad.\n\nVos sabés cuál es el precio. {stars} ⭐",
    ]

    def _craft_engagement_post(self) -> str:
        return random.choice(self._ENGAGEMENT_POSTS)

    def _craft_value_post(self) -> str:
        return random.choice(self._VALUE_POSTS)

    def _craft_stars_cta(self, hook: str = "") -> str:
        stars = self._stars()
        opciones = [
            f"Lo que pasa después no está en ningún otro lado. {stars} Stars y es tuyo.",
            f"La historia completa está debajo. {stars} Stars para verla.",
            f"Solo {stars} ⭐ y sabés cómo termina de verdad.",
            f"El final te va a dejar sin palabras. {stars} Stars para desbloquearlo.",
            f"No subo esto a YouTube. Solo aquí, solo por {stars} Stars.",
        ]
        return random.choice(opciones)

    def _craft_urgency_post(self) -> str:
        """
        Post de urgencia/escasez para empujar conversiones a Stars.
        """
        stars   = self._stars()
        channel = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
        options = [
            (
                f"🔒 Esta semana desbloqueé 3 historias que no voy a repetir.\n\n"
                f"Cada una cuesta solo {stars} ⭐ Stars.\n"
                f"La de hoy involucra a alguien de su propia familia.\n\n"
                f"¿La ves o la dejas pasar?"
                + (f"\n👉 {channel}" if channel else "")
            ),
            (
                f"Hay historias que no puedo publicar en YouTube.\n"
                f"Demasiado reales. Demasiado crudas.\n\n"
                f"Esas solo están aquí. Por {stars} ⭐ Stars las desbloqueas.\n"
                f"La última que subí dejó a la gente sin palabras."
                + (f"\n📲 {channel}" if channel else "")
            ),
            (
                f"📸 Las imágenes que acompañan esta historia no las vas a encontrar en ningún lado.\n\n"
                f"Historia completa + fotos reales = {stars} ⭐ Stars.\n"
                f"Hoy hay una nueva. Y es de las que no se olvidan."
            ),
        ]
        return random.choice(options)

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

            # Header — SIN emojis
            draw.text((W // 2, 52), ">> HISTORIA COMPLETA <<", font=_font(50),
                      fill=(255, 255, 255), anchor="mm")
            draw.line([(50, 108), (W - 50, 108)], fill=(160, 0, 28), width=3)

            # Hook en rojo
            hook_clean = hook.replace('"', '').replace("'", "")[:110]
            hook_lines = textwrap.wrap(f'"{hook_clean}"', width=36)
            y = 138
            for line in hook_lines[:3]:
                draw.text((W // 2, y), line, font=_font(42),
                          fill=(255, 70, 70), anchor="mm")
                y += 52

            draw.line([(50, y + 8), (W - 50, y + 8)], fill=(50, 50, 70), width=2)
            y += 32

            # Texto de la historia
            font_story = _font(33)
            story_clean = full_story.replace("\n\n", "\n").strip()

            story_lines: list[str] = []
            for paragraph in story_clean.split("\n"):
                story_lines.extend(textwrap.wrap(paragraph.strip(), width=43) or [""])

            for line in story_lines:
                if y > H - 110:
                    draw.text((60, y), "[ continua... ]", font=font_story,
                              fill=(120, 120, 140))
                    break
                draw.text((60, y), line, font=font_story, fill=(215, 215, 230))
                y += 44

            # Footer
            draw.line([(50, H - 88), (W - 50, H - 88)], fill=(50, 50, 70), width=2)
            draw.text((W // 2, H - 52), "GATA CURIOSA  |  Confesiones Reales",
                      font=_font(28), fill=(120, 120, 140), anchor="mm")

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

        # ── POST 1: GRATIS — comienzo de la historia, corte en el momento tenso ─
        preview, _ = self._split_story_for_preview(texto, target=480)

        # Intros que rotan para que no sea siempre igual
        _INTROS = [
            "🗣️ <b>CONFESIÓN ANÓNIMA</b>",
            "⚠️ <b>HISTORIA REAL | ANÓNIMA</b>",
            "🔥 <b>CONFESIÓN REAL</b>",
            "📩 <b>ME LO CONTARON EN SECRETO</b>",
        ]
        _CORTES = [
            f"\n...\n\n🔒 <b>El resto está justo abajo — {stars} ⭐ Stars</b>",
            f"\n...\n\n👇 <b>Continúa abajo. Solo {stars} ⭐</b>",
            f"\n...\n\n⬇️ <b>La historia completa: {stars} ⭐ Stars</b>",
            f"\n...\n\n🔓 <b>Desbloqueá el final — {stars} ⭐</b>",
        ]

        free_text = (
            f"{random.choice(_INTROS)}\n\n"
            f"{preview}\n"
            f"{random.choice(_CORTES)}"
        )

        r1 = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     free_text[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        })
        if not r1.get("ok"):
            self.log(f"post gratis fallo: {r1.get('description', '?')}")
            return False

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

            # Caption del paid media: minimalista, no repite el hook (ya está arriba)
            _PAID_SIMPLE = [
                f"🔒 Historia completa adentro — {stars} ⭐ para desbloquear.",
                f"⬆️ Sigue la historia. {stars} ⭐ Stars y la lees entera.",
                f"El final que no te esperás. {stars} ⭐ → historia completa.",
                f"Sin censura. {stars} ⭐ Stars y es tuya.",
            ]
            caption    = random.choice(_PAID_SIMPLE)
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
                    paid_ok = True
                    self._record_post(CONTENT_CONFESSION, hook)
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
            self._record_post(CONTENT_ENGAGEMENT, post[:80])
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
            self._record_post(CONTENT_VALUE, post[:80])
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
            self._record_post(CONTENT_URGENCY, post[:80])
            self.log("post de urgencia publicado")
        return ok

    # ── Teaser de YouTube con copy profesional ─────────────────────────────────

    def post_youtube_teaser(self, youtube_url: str, title: str, hook: str) -> bool:
        if not self._ok():
            return False
        channel_link = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
        prompt = (
            f"Escribe un post para Telegram anunciando este video de YouTube: '{title}'\n"
            f"Hook del video: '{hook[:100]}'\n"
            f"URL: {youtube_url}\n\n"
            "El post debe: crear curiosidad, dar ganas de verlo YA, y mencionar que "
            "la historia completa con imágenes está en el canal de Telegram.\n"
            "Máximo 200 caracteres antes del link. Tono íntimo y dramático.\n"
            "Responde solo con el texto del post (incluye el link del video)."
        )
        try:
            copy = self._llm_call(prompt, max_tokens=200).strip()
            if youtube_url not in copy:
                copy = f"{copy}\n\n📺 {youtube_url}"
            if channel_link and channel_link not in copy:
                copy += f"\n📲 Más aquí → {channel_link}"
        except Exception:
            copy = (
                "\U0001f3ac <b>NUEVO VIDEO</b>\n\n"
                f"<i>&ldquo;{hook[:180]}&rdquo;</i>\n\n"
                f"\U0001f4fa {youtube_url}"
                + (f"\n\U0001f4f2 Historia completa → {channel_link}" if channel_link else "")
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
