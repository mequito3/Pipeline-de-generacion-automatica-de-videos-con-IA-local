"""
analytics_agent.py — Analista de canal YouTube

Extrae métricas completas del canal y de cada video desde tres fuentes:
  1. YouTube Studio Analytics (nodriver) — vistas 28d, watch time, subs ganados,
     CTR e impresiones para los top N videos
  2. Canal público de YouTube (nodriver) — lista de videos con vistas públicas,
     likes, comentarios, fecha de publicación
  3. YouTube Data API v3 (opcional, si YOUTUBE_API_KEY en .env) — stats exactas

Guarda historial en analytics_log.json y calcula deltas entre snapshots.
"""

import asyncio
import json
import logging
import os
import platform
import random
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from modules.youtube_uploader import (
    _cleanup_chrome_profile,
    _cursor,
    _delay,
    _human_click,
    _inject_stealth,
    _random_mouse_wander,
    _scroll,
)
from modules.growth_agent import _dismiss_consent, _get_channel_id

logger = logging.getLogger(__name__)

ANALYTICS_LOG_FILE  = Path(__file__).parent.parent / "analytics_log.json"
_CHANNEL_ID_CACHE   = Path(__file__).parent.parent / "channel_id_cache.txt"
MAX_SNAPSHOTS       = 60   # ~2 meses de historial diario
TOP_VIDEOS_DETAIL   = 3    # cuántos videos con CTR/retención desde Studio


def _load_channel_id_cache() -> str:
    try:
        return _CHANNEL_ID_CACHE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _save_channel_id_cache(channel_id: str) -> None:
    try:
        _CHANNEL_ID_CACHE.write_text(channel_id, encoding="utf-8")
    except Exception:
        pass


# ─── Modelos de datos ─────────────────────────────────────────────────────────

@dataclass
class VideoStats:
    video_id:              str
    title:                 str
    published_at:          str   = ""
    views:                 int   = 0
    likes:                 int   = 0
    comments:              int   = 0
    impressions:           int   = 0
    ctr_pct:               float = 0.0
    avg_view_pct:          float = 0.0   # % retención media
    watch_time_h:          float = 0.0
    is_short:              bool  = True
    views_delta_pct:       float = 0.0   # vs snapshot anterior


@dataclass
class ChannelSnapshot:
    timestamp:             str
    channel_id:            str
    # ── Métricas de canal ──────────────────────────────────────────────────────
    subscribers:           int   = 0
    views_28d:             int   = 0
    views_prev_28d:        int   = 0
    watch_time_h_28d:      float = 0.0
    subs_gained_28d:       int   = 0
    # ── Deltas calculados ──────────────────────────────────────────────────────
    views_delta_pct:       float = 0.0   # vistas 28d vs 28d anterior
    subs_delta_pct:        float = 0.0   # suscriptores vs snapshot anterior
    # ── Videos ────────────────────────────────────────────────────────────────
    videos:                list  = field(default_factory=list)   # list[VideoStats]
    # ── Identificadores del mejor video ───────────────────────────────────────
    top_video_id:          str   = ""
    top_video_title:       str   = ""
    top_video_views:       int   = 0
    top_video_ctr:         float = 0.0
    # ── Diagnóstico ───────────────────────────────────────────────────────────
    errors:                list  = field(default_factory=list)


# ─── Utilidades numéricas ─────────────────────────────────────────────────────

def _parse_number(text: str) -> float:
    """
    Convierte texto de métricas a float.
    Maneja K/M/B, formato EU/US, espacios, signos, guiones.

    Ejemplos:
      "1,234"  → 1234.0
      "1.234"  → 1234.0  (formato europeo)
      "1.2K"   → 1200.0
      "45,8%"  → 45.8
      "+5"     → 5.0
      "—"      → 0.0
    """
    if not text:
        return 0.0
    text = str(text).strip().replace("\xa0", "").replace("\u202f", "")

    if text in ("—", "–", "-", "N/A", "n/a", ""):
        return 0.0

    # Quitar % si viene con él
    text = text.replace("%", "").strip()

    sign = 1.0
    if text.startswith("+"):
        text = text[1:]
    elif text.startswith("-"):
        sign = -1.0
        text = text[1:]

    multiplier = 1.0
    upper = text.upper()
    # "mil" (Spanish) — puede tener espacio: "10,9 mil" o "10,9mil"
    if upper.endswith(" MIL"):
        multiplier = 1_000.0
        text = text[:-4].strip()
    elif upper.endswith("MIL"):
        multiplier = 1_000.0
        text = text[:-3].strip()
    elif upper.endswith("K"):
        multiplier = 1_000.0
        text = text[:-1]
    elif upper.endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif upper.endswith("B"):
        multiplier = 1_000_000_000.0
        text = text[:-1]

    text = text.strip()

    # Detectar formato: si hay ambos . y ,
    if "." in text and "," in text:
        if text.rfind(".") > text.rfind(","):
            text = text.replace(",", "")          # 1,234.5 → 1234.5
        else:
            text = text.replace(".", "").replace(",", ".")  # 1.234,5 → 1234.5
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2 and len(parts[0]) <= 3:
            text = text.replace(",", ".")          # 1,5 → 1.5 (decimal ES)
        else:
            text = text.replace(",", "")           # 1,234 → 1234
    elif "." in text:
        parts = text.split(".")
        # "10.876" en formato europeo (3 dígitos tras punto) → separador de miles
        if len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
            text = text.replace(".", "")           # 10.876 → 10876

    try:
        return sign * float(text) * multiplier
    except ValueError:
        return 0.0


def _parse_duration_iso(iso: str) -> int:
    """Convierte ISO 8601 (PT1M30S) a segundos."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h  = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    s  = int(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


# ─── Historial en JSON ────────────────────────────────────────────────────────

def _load_history() -> list[dict]:
    if ANALYTICS_LOG_FILE.exists():
        try:
            data = json.loads(ANALYTICS_LOG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def _save_snapshot(snapshot: ChannelSnapshot) -> None:
    history = _load_history()
    snap_dict = asdict(snapshot)
    history.append(snap_dict)
    # Mantener solo los últimos MAX_SNAPSHOTS
    if len(history) > MAX_SNAPSHOTS:
        history = history[-MAX_SNAPSHOTS:]
    ANALYTICS_LOG_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"  Snapshot guardado — historial: {len(history)} entradas")


def get_latest_snapshot() -> Optional[ChannelSnapshot]:
    """Retorna el snapshot más reciente o None si no hay historial."""
    history = _load_history()
    if not history:
        return None
    d = history[-1]
    videos = [VideoStats(**v) for v in d.pop("videos", [])]
    snap = ChannelSnapshot(**d)
    snap.videos = videos
    return snap


def get_previous_snapshot() -> Optional[ChannelSnapshot]:
    """Retorna el penúltimo snapshot (para comparación)."""
    history = _load_history()
    if len(history) < 2:
        return None
    d = history[-2]
    videos = [VideoStats(**v) for v in d.pop("videos", [])]
    snap = ChannelSnapshot(**d)
    snap.videos = videos
    return snap


# ─── Delta ────────────────────────────────────────────────────────────────────

def _delta_pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return round((current - previous) / abs(previous) * 100, 1)


# ─── YouTube Data API v3 (opcional) ──────────────────────────────────────────

async def _api_channel_stats(channel_id: str) -> dict:
    """Obtiene stats del canal via Data API v3. Requiere YOUTUBE_API_KEY en .env."""
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    if not api_key:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics", "id": channel_id, "key": api_key},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return {}
            s = items[0]["statistics"]
            return {
                "subscribers": int(s.get("subscriberCount", 0)),
                "total_views":  int(s.get("viewCount", 0)),
                "video_count":  int(s.get("videoCount", 0)),
            }
    except Exception as e:
        logger.warning(f"  Data API channel stats falló: {e}")
        return {}


async def _api_video_stats(channel_id: str, max_results: int = 10) -> list[dict]:
    """Obtiene stats por video via Data API v3."""
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Obtener IDs recientes del canal
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "id",
                    "channelId": channel_id,
                    "type": "video",
                    "order": "date",
                    "maxResults": max_results,
                    "key": api_key,
                },
            )
            r.raise_for_status()
            video_ids = [item["id"]["videoId"] for item in r.json().get("items", [])]
            if not video_ids:
                return []

            # Stats detalladas por video
            r2 = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "statistics,snippet,contentDetails",
                    "id": ",".join(video_ids),
                    "key": api_key,
                },
            )
            r2.raise_for_status()
            results = []
            for item in r2.json().get("items", []):
                stats   = item.get("statistics", {})
                snippet = item.get("snippet", {})
                dur_s   = _parse_duration_iso(item.get("contentDetails", {}).get("duration", ""))
                results.append({
                    "video_id":     item["id"],
                    "title":        snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt", "")[:10],
                    "views":        int(stats.get("viewCount", 0)),
                    "likes":        int(stats.get("likeCount", 0)),
                    "comments":     int(stats.get("commentCount", 0)),
                    "is_short":     dur_s <= 60,
                })
            return results
    except Exception as e:
        logger.warning(f"  Data API video stats falló: {e}")
        return []


# ─── Fuente principal: RSS feed de YouTube ────────────────────────────────────

async def _get_videos_from_rss(channel_id: str) -> list[dict]:
    """
    Obtiene lista de videos via RSS feed de YouTube.
    Incluye: video_id, título, fecha, vistas (media:statistics).
    No requiere autenticación ni API key.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url)
            r.raise_for_status()

        root = ET.fromstring(r.text)
        ns = {
            "atom":  "http://www.w3.org/2005/Atom",
            "yt":    "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }

        videos = []
        for entry in root.findall("atom:entry", ns):
            vid_el  = entry.find("yt:videoId", ns)
            tit_el  = entry.find("atom:title", ns)
            pub_el  = entry.find("atom:published", ns)
            stat_el = entry.find(".//media:statistics", ns)

            if vid_el is None or tit_el is None:
                continue

            views = 0
            if stat_el is not None:
                views = int(stat_el.get("views", 0) or 0)

            videos.append({
                "video_id":     vid_el.text or "",
                "title":        tit_el.text or "",
                "published_at": (pub_el.text or "")[:10],
                "views":        views,
                "likes":        0,
                "comments":     0,
                "is_short":     True,   # canal de Shorts
            })

        logger.info(f"  RSS feed — {len(videos)} videos | "
                    f"top vistas: {max((v['views'] for v in videos), default=0)}")
        return videos

    except Exception as e:
        logger.warning(f"  RSS feed falló: {e}")
        return []


# ─── Fallback DOM: canal público cuando RSS no tiene vistas ────────────────────

async def _scrape_public_channel_dom(browser, channel_id: str) -> list[dict]:
    """
    Fallback: extrae videos desde el DOM del canal público.
    Cubre tanto Shorts (ytd-reel-item-renderer) como videos normales.
    """
    videos: list[dict] = []
    for tab in ["shorts", "videos"]:
        try:
            page = await browser.get(
                f"https://www.youtube.com/channel/{channel_id}/{tab}"
            )
            await _delay(3.0, 5.0)
            await _dismiss_consent(page)
            await _scroll(page, random.randint(400, 600))
            await _delay(2.0, 3.0)

            raw_json = await _eval_timed(page, """(function() {
                var seen = {}, items = [];
                var all = document.querySelectorAll('a[href]');
                all.forEach(function(a) {
                    try {
                        var href = a.href || '';
                        var vid = null, isShort = false;
                        var m = href.match(/\\/shorts\\/([a-zA-Z0-9_-]{11})/);
                        if (m) { vid = m[1]; isShort = true; }
                        if (!vid) {
                            m = href.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
                            if (m) vid = m[1];
                        }
                        if (!vid || seen[vid]) return;
                        // aria-label suele incluir: "Título, 2,1 K visualizaciones, hace 2 semanas"
                        var aria = a.getAttribute('aria-label') || '';
                        var title = aria || a.getAttribute('title') || '';
                        if (!title) {
                            var p = a.parentElement;
                            for (var k = 0; k < 4 && p; k++, p = p.parentElement) {
                                var t = p.querySelector('#video-title, .title, [aria-label]');
                                if (t) { title = t.getAttribute('title') || t.innerText || ''; break; }
                            }
                        }
                        title = (title || '').trim();
                        if (title.length < 3) return;
                        seen[vid] = true;
                        items.push({ id: vid, aria: aria, title: title, is_short: isShort });
                    } catch(e) {}
                });
                return JSON.stringify(items.slice(0, 15));
            })()""", timeout=10.0)

            try:
                raw = json.loads(raw_json) if raw_json else []
            except Exception:
                raw = []

            if raw:
                logger.info(f"  DOM fallback ({tab}) — {len(raw)} videos")
                parsed = []
                for v in raw:
                    aria = v.get("aria", "")
                    views = 0
                    # aria-label: "Título, 2,1 K visualizaciones, hace X semanas"
                    vm = re.search(
                        r"([0-9][0-9,.\s]*(?:mil|[KkMmBb])?)\s*visualizaciones",
                        aria, re.IGNORECASE,
                    )
                    if vm:
                        views = int(_parse_number(vm.group(1).strip()))
                    # Limpiar título: quitar el sufijo de vistas y fecha
                    title = v["title"]
                    if aria:
                        title = re.sub(r",\s*[0-9][^,]*(?:visualizaciones|views)[^,]*", "", aria, flags=re.IGNORECASE)
                        title = re.sub(r",\s*hace\s+.+$", "", title, flags=re.IGNORECASE).strip()
                    if not title or len(title) < 3:
                        title = v["title"]
                    parsed.append({"video_id": v["id"], "title": title,
                                   "published_at": "", "views": views,
                                   "likes": 0, "comments": 0,
                                   "is_short": v.get("is_short", True)})
                videos = parsed
                break

        except Exception as e:
            logger.warning(f"  DOM fallback tab {tab} error: {type(e).__name__}: {e}")

    return videos


async def _scrape_public_channel(browser, channel_id: str) -> list[dict]:
    """
    Obtiene lista de videos: RSS primero (fiable, incluye vistas),
    fallback a DOM si RSS falla o no retorna datos.
    """
    videos = await _get_videos_from_rss(channel_id)
    if not videos:
        logger.info("  RSS sin datos → intentando DOM...")
        videos = await _scrape_public_channel_dom(browser, channel_id)
    return videos


# ─── Scraping — Analytics overview (vistas 28d, watch time, subs) ─────────────

async def _eval_timed(page, js: str, timeout: float = 6.0) -> Optional[str]:
    """page.evaluate con timeout absoluto para evitar bloqueos en SPAs pesadas."""
    try:
        task = asyncio.ensure_future(page.evaluate(js))
        done, pending = await asyncio.wait({task}, timeout=timeout)
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if not done:
            return None
        try:
            return task.result()
        except Exception:
            return None
    except StopIteration:
        return None
    except BaseException as e:
        logger.debug(f"  _eval_timed err: {type(e).__name__}: {e}")
        return None


async def _scrape_analytics_overview(browser, channel_id: str) -> dict:
    """
    Extrae métricas de canal desde YouTube Studio Analytics (tab Overview).
    Usa timeouts agresivos para evitar que el SPA pesado bloquee el browser.
    """
    try:
        page = await browser.get(
            f"https://studio.youtube.com/channel/{channel_id}"
            f"/analytics/tab-overview/period-default"
        )
        await _delay(4.0, 6.0)

        # Esperar que carguen los datos — timeout de 4s por check, máx 8 intentos = ~56s
        loaded = False
        for _ in range(8):
            length_str = await _eval_timed(page, "document.body.innerText.length", timeout=4.0)
            try:
                if length_str and int(str(length_str)) > 800:
                    loaded = True
                    break
            except (ValueError, TypeError):
                pass
            await asyncio.sleep(3.0)

        if not loaded:
            logger.warning("  Analytics overview: sin datos tras espera")
            return {}

        # Extraer texto con timeout
        page_text = await _eval_timed(
            page,
            "(function(){ return (document.body.innerText || '').slice(0, 12000); })()",
            timeout=8.0,
        ) or ""

        if not page_text:
            logger.warning("  Analytics overview: texto vacío")
            return {}

        # Log diagnóstico (primeros 1200c sin saltos)
        logger.info(f"  Analytics texto: {page_text[:1200].replace(chr(10), ' | ')}")

        result = _parse_analytics_text(page_text)
        if result:
            logger.info(
                f"  Analytics overview — vistas: {result.get('views_28d', '?')} | "
                f"watch time: {result.get('watch_time_h_28d', '?')}h | "
                f"subs: {result.get('subs_gained_28d', '?')}"
            )
        else:
            logger.warning("  Analytics overview — no se extrajo ninguna métrica")
        return result

    except Exception as e:
        logger.warning(f"  Error scraping analytics overview: {e}")
        return {}


def _parse_analytics_text(text: str) -> dict:
    """
    Parsea el cuerpo de texto de la página de Analytics de Studio.
    Dos estrategias: línea a línea y regex sobre texto plano (para layouts flex).
    """
    result: dict = {}

    # ── Estrategia 1: línea a línea ───────────────────────────────────────────
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    label_map = {
        "views_28d": [
            "visualizaciones", "vistas", "views", "reproducciones",
        ],
        "watch_time_h_28d": [
            "tiempo de visualización (horas)", "tiempo de visualización",
            "tiempo de reproducción (horas)", "tiempo de reproducción",
            "watch time", "horas de reproducción", "horas vistas",
        ],
        "subs_gained_28d": [
            "nuevos suscriptores", "suscriptores conseguidos",
            "suscriptores", "subscribers",
        ],
    }

    def _looks_like_number(s: str) -> bool:
        """True si la cadena es un número (incluye 0, decimales, K/M, +/-)."""
        s = s.strip().replace("\u2212", "-").replace("−", "-")
        return bool(re.match(r"^[+\-]?[0-9]", s))

    for i, line in enumerate(lines):
        line_low = line.lower()
        for key, labels in label_map.items():
            if key in result:
                continue
            if any(lbl in line_low for lbl in labels):
                for j in range(i + 1, min(i + 6, len(lines))):
                    candidate = lines[j].replace("\u2212", "-").replace("−", "-")
                    if _looks_like_number(candidate):
                        result[key] = _parse_number(candidate)
                        # Delta en línea siguiente (ej: "↑ 12,3 %")
                        if j + 1 < len(lines):
                            dm = re.search(r"([+\-]?\d+[.,]?\d*)\s*%", lines[j + 1])
                            if dm:
                                delta_key = key.replace("_28d", "_delta_pct")
                                result.setdefault(delta_key, _parse_number(dm.group(1)))
                        break

    # ── Estrategia 2: regex sobre texto plano (sin saltos de línea) ───────────
    # Útil cuando YouTube Studio aplasta todo en una sola línea por flex layout.
    flat = " ".join(lines)

    regex_patterns = [
        # Frase resumen en español: "ha conseguido 10.876 visualizaciones"
        ("views_28d",        r"conseguido\s+([\d.,]+)\s+visualizaciones"),
        ("views_28d",        r"(?:visualizaciones|vistas|views|reproducciones)\s*\|\s*(?:visualizaciones|vistas|views)\s*\|\s*([0-9][0-9,. ]*(?:mil|[KkMmBb])?)"),
        ("views_28d",        r"(?:visualizaciones|vistas|views|reproducciones)\s+([0-9][0-9,. ]*[KkMmBb]?)"),
        ("watch_time_h_28d", r"(?:tiempo de visualizaci[oó]n|tiempo de reproducci[oó]n|watch time)[^0-9]*([0-9][0-9,.]*[0-9])\s*(?:h\b|hora)?"),
        ("subs_gained_28d",  r"(?:nuevos suscriptores|suscriptores conseguidos|suscriptores|subscribers)\s*([+\-]?[0-9][0-9,. ]*)"),
    ]

    for key, pattern in regex_patterns:
        if key in result:
            continue
        m = re.search(pattern, flat, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            if _looks_like_number(candidate):
                result[key] = _parse_number(candidate)

    return result


# ─── Scraping — Analytics por video (CTR, impresiones, retención) ──────────────

async def _scrape_video_analytics(browser, video_id: str) -> dict:
    """
    Extrae CTR, impresiones y retención media de un video específico
    desde YouTube Studio (tabs Reach y Engagement).
    """
    data: dict = {}
    _EXTRACT_JS = "(function(){ return (document.body.innerText || '').slice(0, 5000); })()"

    async def _wait_and_extract(page, label: str) -> str:
        for _ in range(6):
            length_str = await _eval_timed(page, "document.body.innerText.length", timeout=4.0)
            try:
                if length_str and int(str(length_str)) > 500:
                    break
            except (ValueError, TypeError):
                pass
            await asyncio.sleep(3.0)
        text = await _eval_timed(page, _EXTRACT_JS, timeout=8.0) or ""
        logger.debug(f"  Video analytics {label} ({video_id}): {len(text)} chars")
        return text

    try:
        # Tab Reach: impresiones + CTR
        page = await browser.get(
            f"https://studio.youtube.com/video/{video_id}"
            f"/analytics/tab-reach/period-default"
        )
        await _delay(4.0, 6.0)
        reach_text = await _wait_and_extract(page, "reach")
        data.update(_parse_video_analytics_text(reach_text, "reach"))

        # Tab Engagement: retención
        page2 = await browser.get(
            f"https://studio.youtube.com/video/{video_id}"
            f"/analytics/tab-engagement/period-default"
        )
        await _delay(4.0, 6.0)
        eng_text = await _wait_and_extract(page2, "engagement")
        data.update(_parse_video_analytics_text(eng_text, "engagement"))

    except Exception as e:
        logger.debug(f"  Video analytics {video_id}: {e}")

    return data


def _parse_video_analytics_text(text: str, tab: str) -> dict:
    """Extrae métricas del texto de una página de analytics por video."""
    result: dict = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if tab == "reach":
        patterns = {
            "impressions": ["impresiones", "impressions"],
            "ctr_pct":     ["tasa de clics", "click-through rate", "ctr"],
        }
    else:
        patterns = {
            "avg_view_pct":  ["porcentaje medio", "average percentage viewed", "% medio"],
            "watch_time_h":  ["tiempo de reproducción", "watch time"],
        }

    for i, line in enumerate(lines):
        line_low = line.lower()
        for key, labels in patterns.items():
            if any(lbl in line_low for lbl in labels) and key not in result:
                for j in range(i + 1, min(i + 4, len(lines))):
                    candidate = lines[j].strip()
                    if re.match(r"^[+\-]?[0-9]", candidate):
                        result[key] = _parse_number(candidate)
                        break

    return result


# ─── Suscriptores desde canal público ────────────────────────────────────────

async def _scrape_subscriber_count(browser, channel_id: str) -> int:
    """Extrae el conteo público de suscriptores del canal."""
    try:
        page = await browser.get(f"https://www.youtube.com/channel/{channel_id}/about")
        await _delay(3.0, 5.0)
        subs_text = await _eval_timed(page, """(function() {
            var el = document.querySelector('#subscriber-count, yt-formatted-string#subscriber-count');
            if (el) return el.innerText || '';
            var spans = document.querySelectorAll('span');
            for (var i = 0; i < spans.length; i++) {
                var t = spans[i].innerText || '';
                if ((t.includes('suscriptor') || t.includes('subscriber')) && t.match(/[0-9]/))
                    return t;
            }
            return '';
        })()""", timeout=8.0) or ""
        m = re.search(r"([0-9][0-9.,]*\s*[KkMmBb]?)", subs_text)
        if m:
            return int(_parse_number(m.group(1)))
    except Exception as e:
        logger.debug(f"  Subs count: {e}")
    return 0


# ─── Sesión principal ─────────────────────────────────────────────────────────

async def _analytics_session_async() -> ChannelSnapshot:
    timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    channel_id = ""
    errors     = []

    profile_dir = Path(config.CHROME_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"

    _cleanup_chrome_profile(profile_dir)
    _cursor["x"] = 960.0
    _cursor["y"] = 540.0

    chrome_bin = getattr(config, "CHROME_BINARY", "")
    if not chrome_bin:
        for c in ["/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"]:
            if Path(c).exists():
                chrome_bin = c
                break

    browser = None
    try:
        browser = await __import__("nodriver").start(
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

        # ── Obtener channel ID (con caché para no recargar Studio cada vez) ──────
        channel_id = _load_channel_id_cache()
        if channel_id:
            logger.info(f"  Channel ID (caché): {channel_id}")
        else:
            logger.info("  Obteniendo channel ID desde Studio...")
            channel_id = await _get_channel_id(browser) or ""
            if not channel_id:
                errors.append("No se pudo obtener channel ID — sesión no activa")
                return ChannelSnapshot(timestamp=timestamp, channel_id="", errors=errors)
            _save_channel_id_cache(channel_id)

        logger.info(f"  Channel ID: {channel_id}")

        # ── Warm-up natural ────────────────────────────────────────────────────
        p = await browser.get("https://www.youtube.com")
        await _delay(3.0, 5.0)
        await _dismiss_consent(p)

        # ── 1. YouTube Data API (si está configurada) ──────────────────────────
        api_channel = await _api_channel_stats(channel_id)
        api_videos  = await _api_video_stats(channel_id, max_results=10)

        # ── 2. Canal público — lista de videos con vistas ──────────────────────
        logger.info("  Scraping canal público...")
        public_videos = await _scrape_public_channel(browser, channel_id)

        # ── 3. Analytics overview — métricas de canal ─────────────────────────
        logger.info("  Scraping Analytics overview...")
        overview = await _scrape_analytics_overview(browser, channel_id)

        # ── 4. Suscriptores ────────────────────────────────────────────────────
        subs = api_channel.get("subscribers", 0)
        if not subs:
            subs = await _scrape_subscriber_count(browser, channel_id)

        # ── 5. Merge video data (API + scraping público) ───────────────────────
        api_by_id = {v["video_id"]: v for v in api_videos}
        merged_videos: list[VideoStats] = []

        source = api_videos if api_videos else public_videos
        for v in source:
            vid_id = v["video_id"]
            pub    = api_by_id.get(vid_id, v)
            vs = VideoStats(
                video_id     = vid_id,
                title        = pub.get("title", v.get("title", "")),
                published_at = pub.get("published_at", v.get("published_at", "")),
                views        = pub.get("views", v.get("views", 0)),
                likes        = pub.get("likes", v.get("likes", 0)),
                comments     = pub.get("comments", v.get("comments", 0)),
                is_short     = pub.get("is_short", v.get("is_short", True)),
            )
            merged_videos.append(vs)

        # Ordenar por vistas descendente
        merged_videos.sort(key=lambda x: x.views, reverse=True)

        # ── 6. Studio analytics por video (top N) ─────────────────────────────
        logger.info(f"  Scraping analytics de top {TOP_VIDEOS_DETAIL} videos...")
        for vs in merged_videos[:TOP_VIDEOS_DETAIL]:
            await _delay(2.0, 4.0)
            detail = await _scrape_video_analytics(browser, vs.video_id)
            if detail.get("impressions"):
                vs.impressions = int(detail["impressions"])
            if detail.get("ctr_pct"):
                vs.ctr_pct = round(detail["ctr_pct"], 2)
            if detail.get("avg_view_pct"):
                vs.avg_view_pct = round(detail["avg_view_pct"], 1)
            if detail.get("watch_time_h"):
                vs.watch_time_h = round(detail["watch_time_h"], 2)
            logger.info(
                f"    {vs.title[:45]} — {vs.views} vistas | "
                f"CTR {vs.ctr_pct}% | Retención {vs.avg_view_pct}%"
            )

        # ── 7. Construir snapshot ──────────────────────────────────────────────
        top = merged_videos[0] if merged_videos else None
        snapshot = ChannelSnapshot(
            timestamp          = timestamp,
            channel_id         = channel_id,
            subscribers        = subs,
            views_28d          = int(overview.get("views_28d", 0)),
            watch_time_h_28d   = round(float(overview.get("watch_time_h_28d", 0.0)), 1),
            subs_gained_28d    = int(overview.get("subs_gained_28d", 0)),
            videos             = merged_videos,
            top_video_id       = top.video_id   if top else "",
            top_video_title    = top.title       if top else "",
            top_video_views    = top.views       if top else 0,
            top_video_ctr      = top.ctr_pct     if top else 0.0,
            errors             = errors,
        )

        # ── 8. Deltas vs snapshot anterior ────────────────────────────────────
        previous = get_latest_snapshot()
        if previous:
            snapshot.views_delta_pct = _delta_pct(snapshot.views_28d, previous.views_28d)
            snapshot.subs_delta_pct  = _delta_pct(snapshot.subscribers, previous.subscribers)
            # Delta por video
            prev_by_id = {v.video_id: v for v in previous.videos}
            for vs in snapshot.videos:
                prev_vs = prev_by_id.get(vs.video_id)
                if prev_vs and prev_vs.views > 0:
                    vs.views_delta_pct = _delta_pct(vs.views, prev_vs.views)

        # ── 9. Guardar ────────────────────────────────────────────────────────
        _save_snapshot(snapshot)

        logger.info(
            f"  Analítica completa — {len(merged_videos)} videos | "
            f"subs: {subs} | vistas 28d: {snapshot.views_28d} | "
            f"watch time: {snapshot.watch_time_h_28d}h"
        )
        return snapshot

    except Exception as e:
        logger.error(f"Error en sesión de analítica: {e}", exc_info=True)
        errors.append(str(e))
        return ChannelSnapshot(timestamp=timestamp, channel_id=channel_id, errors=errors)
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass


# ─── API pública ──────────────────────────────────────────────────────────────

def run_analytics_session() -> ChannelSnapshot:
    """
    Ejecuta una sesión completa de analítica.
    Llamado desde main.py --analytics o desde el scheduler diario.
    """
    logger.info("=== ANALYTICS AGENT — inicio ===")

    if platform.system() == "Windows":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_analytics_session_async())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)
    else:
        return asyncio.run(_analytics_session_async())
