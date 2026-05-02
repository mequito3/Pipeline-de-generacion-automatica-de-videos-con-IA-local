"""
youtube_analytics.py — YouTube Analytics API v2 + Data API v3

Proporciona métricas reales del canal que solo el dueño puede ver:
  - CTR (click-through rate) por video
  - Retención promedio (average view percentage)
  - Fuentes de tráfico (Shorts, búsqueda, sugeridos, etc.)
  - Ingresos estimados (si está monetizado)
  - Datos por país y demografía

Requiere autenticación OAuth una sola vez:
  python setup_youtube_analytics.py

Las credenciales se guardan en youtube_token.json y se renuevan automáticamente.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sys

logger = logging.getLogger(__name__)

_TOKEN_FILE         = Path(__file__).parent.parent / "youtube_token.json"
_SECRETS_FILE       = Path(__file__).parent.parent / "youtube_client_secrets.json"
_SCOPES             = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


# ─── Autenticación ─────────────────────────────────────────────────────────────

def _get_credentials():
    """Carga o renueva las credenciales OAuth. Retorna None si no están configuradas."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        logger.error("google-auth no instalado: pip install google-auth google-auth-oauthlib google-api-python-client")
        return None

    if not _TOKEN_FILE.exists():
        logger.warning("youtube_token.json no encontrado. Ejecuta: python setup_youtube_analytics.py")
        return None

    creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            logger.debug("Token OAuth renovado automáticamente")
        except Exception as e:
            logger.warning(f"Error renovando token: {e} — re-ejecuta setup_youtube_analytics.py")
            return None

    return creds if creds.valid else None


def _build_services():
    """Construye los clientes de YouTube Data API y Analytics API."""
    creds = _get_credentials()
    if not creds:
        return None, None
    try:
        from googleapiclient.discovery import build
        yt       = build("youtube",          "v3",  credentials=creds, cache_discovery=False)
        yt_analy = build("youtubeAnalytics", "v2",  credentials=creds, cache_discovery=False)
        return yt, yt_analy
    except Exception as e:
        logger.error(f"Error construyendo servicios de Google: {e}")
        return None, None


def is_configured() -> bool:
    """Retorna True si el token OAuth existe y es válido."""
    return _TOKEN_FILE.exists() and _get_credentials() is not None


# ─── Métricas del canal ────────────────────────────────────────────────────────

def get_channel_id(yt) -> Optional[str]:
    """Obtiene el channel ID del canal autenticado."""
    try:
        resp = yt.channels().list(part="id", mine=True).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None
    except Exception as e:
        logger.warning(f"get_channel_id: {e}")
        return None


def get_channel_overview(days: int = 28) -> Optional[dict]:
    """
    Métricas generales del canal de los últimos N días.

    Returns dict con:
        views, watch_time_minutes, subscribers_gained, subscribers_lost,
        estimated_revenue (si monetizado), cpm, rpm
    """
    yt, yt_analy = _build_services()
    if not yt or not yt_analy:
        return None

    channel_id = get_channel_id(yt)
    if not channel_id:
        return None

    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = yt_analy.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics=(
                "views,estimatedMinutesWatched,subscribersGained,"
                "subscribersLost,averageViewDuration,averageViewPercentage"
            ),
            dimensions="",
        ).execute()

        row = resp.get("rows", [[0]*6])[0]
        headers = [h["name"] for h in resp.get("columnHeaders", [])]
        data = dict(zip(headers, row))

        # Suscriptores totales del canal via Data API v3
        total_subs = 0
        try:
            ch_resp = yt.channels().list(part="statistics", id=channel_id).execute()
            ch_items = ch_resp.get("items", [])
            if ch_items:
                total_subs = int(ch_items[0]["statistics"].get("subscriberCount", 0))
        except Exception:
            pass

        result = {
            "views":                  int(data.get("views", 0)),
            "watch_time_minutes":     float(data.get("estimatedMinutesWatched", 0)),
            "watch_time_hours":       float(data.get("estimatedMinutesWatched", 0)) / 60,
            "subscribers":            total_subs,
            "subscribers_gained":     int(data.get("subscribersGained", 0)),
            "subscribers_lost":       int(data.get("subscribersLost", 0)),
            "avg_view_duration_s":    float(data.get("averageViewDuration", 0)),
            "avg_view_pct":           float(data.get("averageViewPercentage", 0)),
            "period_days":            days,
            "channel_id":             channel_id,
        }

        # Ingresos (solo si monetizado — no falla si no está disponible)
        try:
            rev_resp = yt_analy.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start_date,
                endDate=end_date,
                metrics="estimatedRevenue,cpm,rpm",
            ).execute()
            rev_row = rev_resp.get("rows", [[0, 0, 0]])[0]
            result["estimated_revenue_usd"] = float(rev_row[0]) if rev_row else 0.0
            result["cpm"]                   = float(rev_row[1]) if len(rev_row) > 1 else 0.0
            result["rpm"]                   = float(rev_row[2]) if len(rev_row) > 2 else 0.0
        except Exception:
            result["estimated_revenue_usd"] = 0.0
            result["cpm"] = 0.0
            result["rpm"] = 0.0

        return result

    except Exception as e:
        logger.error(f"get_channel_overview: {e}")
        return None


def get_videos_analytics(max_results: int = 10) -> list[dict]:
    """
    Métricas detalladas por video: CTR, retención, fuentes de tráfico.

    Returns lista de dicts ordenada por vistas descendente.
    """
    yt, yt_analy = _build_services()
    if not yt or not yt_analy:
        return []

    channel_id = get_channel_id(yt)
    if not channel_id:
        return []

    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    # 1. Obtener los video IDs del canal
    try:
        search_resp = yt.search().list(
            part="id",
            channelId=channel_id,
            maxResults=max_results,
            order="date",
            type="video",
        ).execute()
        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
        if not video_ids:
            return []
    except Exception as e:
        logger.warning(f"get_videos_analytics search: {e}")
        return []

    # 2. Stats públicas (vistas, likes, comentarios)
    try:
        stats_resp = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        ).execute()
        stats_by_id = {
            item["id"]: {
                "title":       item["snippet"]["title"],
                "published":   item["snippet"]["publishedAt"][:10],
                "views":       int(item["statistics"].get("viewCount", 0)),
                "likes":       int(item["statistics"].get("likeCount", 0)),
                "comments":    int(item["statistics"].get("commentCount", 0)),
            }
            for item in stats_resp.get("items", [])
        }
    except Exception as e:
        logger.warning(f"get_videos_analytics stats: {e}")
        stats_by_id = {}

    # 3. Analytics privados (CTR, retención) por video
    results = []
    for vid_id in video_ids:
        base = stats_by_id.get(vid_id, {"title": vid_id, "views": 0})
        try:
            analy_resp = yt_analy.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,averageViewPercentage,clickThroughRate,averageViewDuration",
                filters=f"video=={vid_id}",
            ).execute()
            row = analy_resp.get("rows", [[0]*5])[0] if analy_resp.get("rows") else [0]*5
            base.update({
                "analytics_views":     int(row[0])   if len(row) > 0 else 0,
                "watch_time_min":      float(row[1]) if len(row) > 1 else 0.0,
                "avg_view_pct":        round(float(row[2]), 1) if len(row) > 2 else 0.0,
                "ctr_pct":             round(float(row[3]) * 100, 2) if len(row) > 3 else 0.0,
                "avg_view_duration_s": float(row[4]) if len(row) > 4 else 0.0,
                "video_id":            vid_id,
            })
        except Exception as e:
            logger.debug(f"Analytics para {vid_id}: {e}")
            base["video_id"] = vid_id

        results.append(base)

    return sorted(results, key=lambda x: x.get("views", 0), reverse=True)


def get_traffic_sources(days: int = 28) -> list[dict]:
    """Fuentes de tráfico: Shorts, búsqueda, sugeridos, canal, externo."""
    yt, yt_analy = _build_services()
    if not yt or not yt_analy:
        return []

    channel_id = get_channel_id(yt)
    if not channel_id:
        return []

    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = yt_analy.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched",
            dimensions="insightTrafficSourceType",
            sort="-views",
        ).execute()

        total_views = sum(int(r[1]) for r in resp.get("rows", []))
        sources = []
        for row in resp.get("rows", []):
            views = int(row[1])
            sources.append({
                "source":  row[0],
                "views":   views,
                "pct":     round(views / total_views * 100, 1) if total_views else 0,
                "watch_time_min": float(row[2]),
            })
        return sources
    except Exception as e:
        logger.warning(f"get_traffic_sources: {e}")
        return []


def get_top_countries(days: int = 28, limit: int = 5) -> list[dict]:
    """Top países por vistas."""
    yt, yt_analy = _build_services()
    if not yt or not yt_analy:
        return []

    channel_id = get_channel_id(yt)
    if not channel_id:
        return []

    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = yt_analy.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched",
            dimensions="country",
            sort="-views",
            maxResults=limit,
        ).execute()
        return [
            {"country": r[0], "views": int(r[1]), "watch_time_min": float(r[2])}
            for r in resp.get("rows", [])
        ]
    except Exception as e:
        logger.warning(f"get_top_countries: {e}")
        return []

