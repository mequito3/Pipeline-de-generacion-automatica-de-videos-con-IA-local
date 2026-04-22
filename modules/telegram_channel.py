"""
telegram_channel.py — Admin bot del Canal de Confesiones en Telegram

Gestiona el canal con dos niveles de contenido:
  - Gratis   : confesión moderada (texto + hook) → visible para todos
  - Premium  : historia completa + imágenes reales → pagar con Stars

Funnel completo:
  YouTube/TikTok → "más en mi Telegram" → canal gratis → pagan Stars para ver más

Config necesaria en .env:
  TELEGRAM_CHANNEL_ID     = @tucanalname  (o ID numérico -100xxxxxxxx)
  TELEGRAM_CHANNEL_STARS  = 50            (precio por contenido premium)
"""

import json
import logging
import shutil
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

import requests

import config
from modules.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramChannelManager(BaseAgent):
    name       = "Agente del Canal"
    role       = "Admin del Canal de Confesiones"
    department = "Dpto. Distribución"

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

    # ── Contenido GRATUITO ─────────────────────────────────────────────────────

    def post_free_confession(self, hook: str, preview: str) -> bool:
        """
        Publica una confesión moderada gratis en el canal.
        Incluye CTA hacia el contenido premium con Stars.
        """
        if not self._ok():
            self.log("canal no configurado (TELEGRAM_CHANNEL_ID faltante)")
            return False

        stars = self._stars()
        text = (
            f"🔥 <b>CONFESIÓN</b>\n\n"
            f'<i>"{hook[:200]}"</i>\n\n'
            f"{preview[:400]}\n\n"
            f"{'─' * 26}\n"
            f"📸 Historia completa + imágenes reales\n"
            f"👉 <b>{stars} ⭐ Stars</b> para desbloquear"
        )

        result = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     text[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        })
        if result.get("ok"):
            self.log(f"confesion gratis publicada: '{hook[:55]}'")
        return result.get("ok", False)

    # ── Tarjeta de confesión (imagen con el texto cubierto) ───────────────────

    def _render_story_card(self, hook: str, full_story: str, out_dir: Path) -> Optional[str]:
        """
        Renderiza la historia completa como imagen oscura estilo 'tarjeta de confesión'.
        La imagen va detrás del paywall — aparece borrosa hasta que pagan Stars.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            W, H = 1080, 1350  # formato 4:5 portrait — ideal para Telegram

            # ── Fondo degradado oscuro ────────────────────────────────────────
            img = Image.new("RGB", (W, H), (8, 8, 18))
            draw = ImageDraw.Draw(img)

            # Franja de color dramática arriba
            for y in range(180):
                alpha = int(255 * (1 - y / 180))
                draw.line([(0, y), (W, y)], fill=(180, 0, 40, alpha))

            # Bordes sutiles
            draw.rectangle([0, 0, W - 1, H - 1], outline=(180, 0, 40), width=6)

            # ── Fuente ────────────────────────────────────────────────────────
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

            # ── Header ────────────────────────────────────────────────────────
            draw.text((W // 2, 50), "🔒 CONFESIÓN COMPLETA", font=_font(52),
                      fill=(255, 255, 255), anchor="mm")
            draw.line([(60, 110), (W - 60, 110)], fill=(180, 0, 40), width=3)

            # ── Hook ──────────────────────────────────────────────────────────
            hook_clean = f'"{hook[:120]}"'
            hook_lines = textwrap.wrap(hook_clean, width=38)
            y = 140
            for line in hook_lines[:3]:
                draw.text((W // 2, y), line, font=_font(44),
                          fill=(255, 80, 80), anchor="mm")
                y += 55

            draw.line([(60, y + 10), (W - 60, y + 10)], fill=(60, 60, 80), width=2)
            y += 35

            # ── Historia completa ─────────────────────────────────────────────
            story_clean = full_story.replace("\n\n", "\n").strip()
            # Limitar a lo que cabe en la imagen (~1200 chars)
            if len(story_clean) > 1200:
                story_clean = story_clean[:1180] + "…"

            story_lines = []
            for paragraph in story_clean.split("\n"):
                story_lines.extend(textwrap.wrap(paragraph.strip(), width=44) or [""])

            font_story = _font(34)
            for line in story_lines:
                if y > H - 120:
                    draw.text((W // 2, y), "…", font=font_story,
                              fill=(160, 160, 180), anchor="mm")
                    break
                draw.text((60, y), line, font=font_story, fill=(220, 220, 235))
                y += 46

            # ── Footer ────────────────────────────────────────────────────────
            draw.line([(60, H - 90), (W - 60, H - 90)], fill=(60, 60, 80), width=2)
            draw.text((W // 2, H - 55), "✦ GATA CURIOSA ✦ Confesiones Reales",
                      font=_font(30), fill=(140, 140, 160), anchor="mm")

            out_path = out_dir / "story_card.jpg"
            img.save(str(out_path), "JPEG", quality=92)
            return str(out_path)

        except Exception as e:
            self.log(f"render story card fallo: {e}")
            return None

    # ── Contenido PREMIUM (Stars) ──────────────────────────────────────────────

    def post_paid_confession(
        self,
        hook: str,
        full_story: str,
        image_paths: list[str],
        price_stars: Optional[int] = None,
    ) -> bool:
        """
        Publica media pagada con Telegram Stars.
        - Caption (gratis): solo el hook — genera intriga sin revelar nada
        - Paid media: tarjeta con el texto completo + fotos de Pexels
          → todo aparece borroso hasta que el usuario paga Stars
        """
        if not self._ok():
            return False

        stars = price_stars or self._stars()

        # Directorio temporal para la tarjeta de texto
        tmp_dir = Path(tempfile.mkdtemp(prefix="tgpaid_"))
        try:
            # 1. Renderizar historia como imagen (el texto "cubierto")
            card_path = self._render_story_card(hook, full_story, tmp_dir)

            # 2. Armar lista de medios: tarjeta primero, luego fotos de Pexels
            all_media: list[str] = []
            if card_path and Path(card_path).exists():
                all_media.append(card_path)
            for p in image_paths:
                if Path(p).exists():
                    all_media.append(p)

            if not all_media:
                self.log("sin media para paid confession")
                return False

            # Máximo 10 items por sendPaidMedia
            all_media = all_media[:10]

            # Caption libre (visible sin pagar): solo el hook + CTA misterioso
            caption = (
                f"🔒 <b>HISTORIA COMPLETA DESBLOQUEADA</b>\n\n"
                f'<i>"{hook[:200]}"</i>\n\n'
                f"⭐ <b>{stars} Stars</b> para ver la historia completa + imágenes"
            )

            media_json = [{"type": "photo", "media": f"attach://m{i}"}
                          for i in range(len(all_media))]
            files = {f"m{i}": open(p, "rb") for i, p in enumerate(all_media)}

            try:
                result = self._api("sendPaidMedia", data={
                    "chat_id":    self._channel(),
                    "star_count": str(stars),
                    "caption":    caption[:1024],
                    "parse_mode": "HTML",
                    "media":      json.dumps(media_json),
                }, files=files)
                ok = result.get("ok", False)
                if ok:
                    self.log(f"paid media: {len(all_media)} items ({stars}⭐)")
                else:
                    self.log(f"paid media fallo: {result.get('description', '?')}")
                return ok
            finally:
                for f in files.values():
                    try:
                        f.close()
                    except Exception:
                        pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Teaser de YouTube ──────────────────────────────────────────────────────

    def post_youtube_teaser(self, youtube_url: str, title: str, hook: str) -> bool:
        """
        Publica teaser del video de YouTube en el canal.
        Mueve la audiencia de YouTube/TikTok hacia el canal de Telegram.
        """
        if not self._ok():
            return False

        channel_link = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
        text = (
            f"🎬 <b>NUEVO VIDEO</b>\n\n"
            f'<i>"{hook[:180]}"</i>\n\n'
            f"📺 {title[:100]}\n"
            f"{youtube_url}\n\n"
            f"{'─' * 26}\n"
            f"👆 Míralo y luego vuelve aquí\n"
            f"📸 La historia completa con fotos está en este canal"
            + (f"\n🔗 {channel_link}" if channel_link else "")
        )

        result = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     text[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": False,
        })
        if result.get("ok"):
            self.log(f"teaser YouTube publicado: '{title[:55]}'")
        return result.get("ok", False)

    # ── Imágenes de Pexels para premium ───────────────────────────────────────

    def _fetch_images(self, query: str, n: int = 3) -> list[str]:
        """Descarga N fotos de Pexels relacionadas con la historia."""
        api_key = getattr(config, "PEXELS_API_KEY", "")
        if not api_key:
            return []
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={
                    "query":       query[:60],
                    "per_page":    n,
                    "orientation": "portrait",
                },
                timeout=15,
            )
            photos = r.json().get("photos", [])
            tmp = Path(tempfile.mkdtemp(prefix="tgch_"))
            paths = []
            for i, photo in enumerate(photos[:n]):
                url = photo.get("src", {}).get("large2x", photo.get("src", {}).get("large", ""))
                if not url:
                    continue
                img = requests.get(url, timeout=20)
                if img.status_code == 200:
                    p = tmp / f"img_{i}.jpg"
                    p.write_bytes(img.content)
                    paths.append(str(p))
            return paths
        except Exception as e:
            self.log(f"fetch images fallo: {e}")
            return []

    # ── Hook dramático con IA ──────────────────────────────────────────────────

    # ── Clasificador de picante ────────────────────────────────────────────────

    _HOT_KEYWORDS = {
        "sex", "sexo", "sexual", "naked", "desnud", "porn", "infidel",
        "engañ", "traicion", "affair", "threesome", "trio", "fetish",
        "fetiche", "masturbat", "orgas", "seduci", "seduct", "cuckold",
        "revenge porn", "nudes", "nude", "nsfw", "explicit", "erotic",
        "erotica", "lujuria", "deseo carnal", "cama", "intimo", "intimidad",
    }

    def _spice_score(self, titulo: str, texto: str) -> int:
        """0-10: cuanto mas alto mas picante/sexual/morbosa es la historia."""
        combined = (titulo + " " + texto).lower()
        return sum(1 for kw in self._HOT_KEYWORDS if kw in combined)

    # ── Reescritura dramatica con IA ──────────────────────────────────────────

    def _rewrite_confession(self, titulo: str, texto: str, spicy: bool = False) -> tuple[str, str]:
        """
        Reescribe la historia de Reddit como confesion anonima dramatica en espanol.
        Cambia nombres por nombres latinos comunes.
        Retorna (titulo_gancho, historia_reescrita).
        """
        tono = (
            "muy sexual, morbosa y explicita, sin censura, con detalles intimos"
            if spicy else
            "dramatica, emocional y envolvente, con giros inesperados"
        )
        prompt = (
            f"Historia original de Reddit:\nTITULO: {titulo}\nTEXTO: {texto[:1200]}\n\n"
            f"Reescribela como una confesion anonima en espanol latino, tono {tono}.\n"
            "Reglas:\n"
            "- Cambia TODOS los nombres por nombres latinos comunes (ej: Andrea, Carlos, Valeria, Miguel)\n"
            "- Escribe en primera persona ('Yo soy...' / 'Me llamo...')\n"
            "- Cuenta la historia COMPLETA con principio, desarrollo y final\n"
            "- Maximo 900 palabras\n"
            "- Al inicio escribe una linea de TITULO: <gancho impactante en mayusculas>\n"
            "- Luego el texto de la confesion directamente, sin encabezados extra\n"
            "Responde SOLO con: TITULO: <titulo>\n<texto de la confesion>"
        )
        raw = self._llm_call(prompt, max_tokens=1200)
        import re
        title_match = re.search(r"TITULO:\s*(.+)", raw)
        gancho = title_match.group(1).strip()[:120] if title_match else titulo[:120]
        # Texto: todo despues de la primera linea
        body = raw[raw.find("\n"):].strip() if "\n" in raw else raw
        return gancho, body

    # ── Post de texto libre (gratis) ──────────────────────────────────────────

    def post_text_confession(self, gancho: str, historia: str) -> bool:
        """Publica confesion completa en texto plano, visible para todos."""
        if not self._ok():
            return False

        separador = "─" * 28
        text = (
            f"🔥 <b>{gancho.upper()}</b>\n\n"
            f"{historia[:3800]}\n\n"
            f"{separador}\n"
            f"<i>Confesion anonima — historia real modificada</i>"
        )
        result = self._api("sendMessage", json={
            "chat_id":                  self._channel(),
            "text":                     text[:4096],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        })
        if result.get("ok"):
            self.log(f"confesion gratis publicada: '{gancho[:55]}'")
        return result.get("ok", False)

    # ── Sesión diaria de confesiones ──────────────────────────────────────────

    def run_daily_confessions(self, n: int = 5) -> int:
        """
        Publica N confesiones clasificadas por nivel de picante:
          - Normales (spice < 3) → texto completo gratis, todo visible
          - Hot (spice >= 3)     → Stars paywall con historia reescrita + imagenes

        Retorna el numero de confesiones publicadas.
        """
        if not self._ok():
            self.log("canal no configurado — omitiendo confesiones diarias")
            return 0

        from modules import wattpad_fetcher

        published = 0
        tmp_dirs: list[Path] = []

        for i in range(n):
            try:
                # Wattpad como fuente principal — mas ricas y dramaticas que Reddit
                story = wattpad_fetcher.get_story(prefer_mature=(i % 2 == 1))
                if not story:
                    self.log(f"confesion {i+1}: sin historia en Wattpad")
                    continue

                titulo  = story.get("titulo", "")
                texto   = story.get("historia", "")
                # Si la propia Wattpad la marca como madura, ya es hot
                is_hot  = story.get("mature", False) or self._spice_score(titulo, texto) >= 3
                spice   = self._spice_score(titulo, texto)

                self.log(f"confesion {i+1}: spice={spice} mature={story.get('mature')} → {'HOT (Stars)' if is_hot else 'GRATIS (texto)'}")

                gancho, historia = self._rewrite_confession(titulo, texto, spicy=is_hot)
                self.log(f"  gancho: '{gancho[:60]}'")

                if is_hot:
                    # Historia sexual/morbosa → detras del paywall de Stars
                    imgs = self._fetch_images(gancho, n=3)
                    if imgs:
                        tmp_dirs.append(Path(imgs[0]).parent)
                    self.post_paid_confession(
                        hook=gancho,
                        full_story=historia,
                        image_paths=imgs,
                    )
                else:
                    # Historia dramatica normal → texto completo gratis
                    self.post_text_confession(gancho=gancho, historia=historia)

                published += 1
                self.log(f"confesion {i+1}/{n} publicada")

                if i < n - 1:
                    time.sleep(60)  # pausa entre posts para no parecer spam

            except Exception as e:
                self.log(f"confesion {i+1} fallo: {e}")

        # Limpiar temporales de imagenes Pexels
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

        if published:
            self.notify(f"Canal Telegram: {published}/{n} confesiones publicadas")
        return published
