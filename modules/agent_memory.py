"""
agent_memory.py — Memoria compartida entre agentes

Actúa como pizarrón de blackboard: analytics escribe lo que funciona,
script_generator y growth_agent lo leen para mejorar su trabajo.

Flujo:
  analytics_agent  → escribe insights de rendimiento
  script_generator → lee temas top y hooks ganadores para priorizar
  growth_agent     → lee keywords ganadoras para priorizar
  ceo_report       → lee todo y genera sugerencias de expansión/nichos
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config

logger = logging.getLogger(__name__)

MEMORY_FILE = Path(__file__).parent.parent / "agent_memory.json"

_DEFAULT_MEMORY = {
    "last_updated": "",
    "top_topics": [],
    "avoid_topics": [],
    "best_hooks": [],
    "best_keywords": [],
    "content_insights": {
        "avg_views_by_topic": {},
        "best_upload_hour": 20,
        "trend": "stable",
        "total_videos_analyzed": 0,
        "avg_views_per_video": 0,
        "top_video_title": "",
        "top_video_views": 0,
    },
    "growth_insights": {
        "best_comment_persona": "",
        "best_keywords_used": [],
        "total_comments": 0,
    },
    "expansion_suggestions": {
        "adjacent_niches": [],
        "pages_strategy": [],
        "automation_next_steps": [],
        "generated_at": "",
    },
}


def load() -> dict:
    try:
        if MEMORY_FILE.exists():
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"agent_memory load: {e}")
    return _DEFAULT_MEMORY.copy()


def save(memory: dict) -> None:
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"agent_memory save: {e}")


def update_from_analytics(snapshot) -> None:
    """
    Analytics agent llama esto tras cada sesión.
    Extrae los topics, hooks y tendencias más exitosas para que
    script_generator los priorice en el siguiente video.
    """
    mem = load()

    videos = getattr(snapshot, "videos", []) or []
    if not videos:
        return

    # Top videos por vistas
    sorted_vids = sorted(videos, key=lambda v: getattr(v, "views", 0), reverse=True)
    top3 = sorted_vids[:3]

    # Extraer keywords temáticas de los títulos top
    THEME_KEYWORDS = {
        "traicion": ["traicion", "engaño", "infiel", "cuernos", "amante", "esposo"],
        "secreto":  ["secreto", "verdad", "descubri", "encontre", "oculto"],
        "familia":  ["suegra", "hermano", "padre", "madre", "familia", "adoptado"],
        "amigos":   ["amigo", "amiga", "amistad", "mejora amiga"],
        "narci":    ["narcis", "manipul", "toxic", "abuso", "control"],
        "doble_vida": ["doble vida", "quien decia", "secreto oscuro"],
    }

    topic_views: dict[str, list[int]] = {}
    best_hooks = []

    for vid in sorted_vids[:10]:
        title_l = getattr(vid, "title", "").lower()
        views   = getattr(vid, "views", 0)

        for topic, kws in THEME_KEYWORDS.items():
            if any(kw in title_l for kw in kws):
                topic_views.setdefault(topic, []).append(views)
                break

        if views > 10_000:
            best_hooks.append(getattr(vid, "title", ""))

    avg_by_topic = {t: int(sum(v) / len(v)) for t, v in topic_views.items() if v}
    top_topics   = sorted(avg_by_topic, key=avg_by_topic.get, reverse=True)[:4]
    # Excluir de avoid los temas que ya están en top_topics para evitar contradicción
    top_set      = set(top_topics)
    avoid_topics = [t for t, avg in avg_by_topic.items() if avg < 5_000 and t not in top_set]

    total_views = sum(getattr(v, "views", 0) for v in videos)
    avg_views   = total_views // len(videos) if videos else 0

    mem["last_updated"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
    mem["top_topics"]    = top_topics
    mem["avoid_topics"]  = avoid_topics
    mem["best_hooks"]    = best_hooks[:5]
    mem["content_insights"]["avg_views_by_topic"]      = avg_by_topic
    mem["content_insights"]["total_videos_analyzed"]   = len(videos)
    mem["content_insights"]["avg_views_per_video"]     = avg_views
    mem["content_insights"]["top_video_title"]         = getattr(snapshot, "top_video_title", "")
    mem["content_insights"]["top_video_views"]         = getattr(snapshot, "top_video_views", 0)

    # Tendencia: subiendo / estable / cayendo
    views_delta = getattr(snapshot, "views_delta_pct", 0.0)
    if views_delta > 15:
        mem["content_insights"]["trend"] = "rising"
    elif views_delta < -15:
        mem["content_insights"]["trend"] = "falling"
    else:
        mem["content_insights"]["trend"] = "stable"

    # Regenerar sugerencias de expansión
    mem["expansion_suggestions"] = _generate_expansion(mem)

    save(mem)
    logger.info(
        f"agent_memory actualizada — top topics: {top_topics} | "
        f"avg vistas: {avg_views:,} | tendencia: {mem['content_insights']['trend']}"
    )


def _generate_expansion(mem: dict) -> dict:
    """Genera sugerencias de expansión de nicho basadas en lo que funciona."""
    top_topics = mem.get("top_topics", [])
    avg_views  = mem["content_insights"].get("avg_views_per_video", 0)
    trend      = mem["content_insights"].get("trend", "stable")

    # Mapa de expansión: si X funciona → explorar Y
    EXPANSION_MAP = {
        "traicion": [
            "Canal secundario: INFIDELIDAD VIRAL — historias en formato más crudo (sin música, tono confesional)",
            "TikTok: clips de 15s con solo el hook + pregunta (los Shorts de YouTube editados)",
            "Instagram Reels: mismos videos, captions con pregunta para debate",
        ],
        "secreto": [
            "Canal: SECRETOS OSCUROS — historias de secretos familiares multigeneracionales",
            "Formato POV: narrar desde el punto de vista del 'villano' de la historia",
            "Reddit thread style: mostrar el post original en pantalla mientras se narra",
        ],
        "familia": [
            "Canal: DRAMAS DE FAMILIA — enfoque en suegras, herencias, hijos adoptados",
            "Colaboración: reacciones en vivo a estas historias con otra cuenta",
            "Newsletter: resumir las historias más impactantes de la semana",
        ],
        "narci": [
            "Canal: RELACIONES TÓXICAS — guías de señales de alerta + historias reales",
            "Podcast: versión audio-only para Spotify/Apple Podcasts (mismo contenido)",
            "Pinterest: infografías '10 señales de que tu pareja es narcisista'",
        ],
    }

    adjacent: list[str] = []
    for topic in top_topics[:2]:
        adjacent.extend(EXPANSION_MAP.get(topic, []))

    # Siempre sugerir expansión multi-plataforma
    pages_strategy = [
        "Instagram Reels: reutilizar los mismos Shorts (ya están en 9:16, solo cambiar descripción)",
        "TikTok: subir los mismos videos — el algoritmo de TikTok es más agresivo para cuentas nuevas",
        "Facebook Reels: menor competencia que TikTok/IG, buen engagement en +30 años (tu audiencia objetivo)",
        "Pinterest Video: miniaturas con texto funcionan muy bien para nicho de confesiones",
        "Hilos (Threads): publicar la pregunta del video → dirige tráfico al canal",
    ]

    automation_steps = [
        "Bot de comentarios: ya operativo (growth_agent) — siguiente paso: responder automáticamente a los primeros 5 comentarios",
        "Scheduler multi-canal: instanciar este mismo pipeline con CHANNEL_ENV_FILE distinto para un segundo canal en otro nicho",
        "Reutilización automática: script que toma el MP4 final y lo sube a TikTok/IG vía API",
        "A/B testing: generar 2 títulos por video y alternar para medir CTR comparativo en YouTube Studio",
        "Email list: página de captura simple ('las historias que YouTube censura') para construir audiencia propia",
    ]

    if trend == "rising":
        automation_steps.insert(0, "CANAL CRECIENDO: momento ideal para lanzar canal secundario con nicho adyacente")
    elif trend == "falling":
        automation_steps.insert(0, "BAJA DE VISTAS: probar nuevo nicho o cambiar formato (más corto, hook más agresivo)")

    return {
        "adjacent_niches": adjacent[:4],
        "pages_strategy": pages_strategy,
        "automation_next_steps": automation_steps,
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
    }


def get_topic_bias() -> list[str]:
    """
    script_generator llama esto para saber qué temas priorizar.
    Retorna lista de temas recomendados por analytics, o [] si no hay datos.
    """
    mem = load()
    age_hours = _memory_age_hours(mem)
    if age_hours > 72:
        return []
    return mem.get("top_topics", [])


def get_avoid_topics() -> list[str]:
    """Temas que analytics marcó como bajo rendimiento."""
    mem = load()
    if _memory_age_hours(mem) > 72:
        return []
    return mem.get("avoid_topics", [])


def update_from_growth(results: dict, platform: str = "youtube") -> None:
    """
    growth_agent y tiktok_growth_agent llaman esto al final de cada sesión.
    Registra qué se hizo para que futuros reportes puedan correlacionar
    engagement → rendimiento del canal.
    """
    mem = load()
    gi = mem.setdefault("growth_insights", {
        "best_comment_persona": "",
        "best_keywords_used": [],
        "total_comments": 0,
    })
    external = results.get("external", results.get("comments", 0))
    own      = results.get("own", results.get("own_replies", 0))
    gi["total_comments"] = gi.get("total_comments", 0) + external + own
    gi["last_session"] = {
        "platform": platform,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "external": external,
        "own_replies": own,
        "hearts": results.get("hearts", 0),
        "likes": results.get("likes", 0),
        "videos_watched": results.get("videos_watched", 0),
    }
    save(mem)
    logger.info(
        f"agent_memory: growth_insights actualizados ({platform}) — "
        f"externos: {external} | propios: {own} | total acumulado: {gi['total_comments']}"
    )


def get_expansion_suggestions() -> dict:
    """ceo_report llama esto para incluir sugerencias en el reporte."""
    mem = load()
    return mem.get("expansion_suggestions", {})


def _memory_age_hours(mem: dict) -> float:
    ts = mem.get("last_updated", "")
    if not ts:
        return 999.0
    try:
        updated = datetime.strptime(ts, "%Y-%m-%d %H:%M")
        return (datetime.now() - updated).total_seconds() / 3600
    except Exception:
        return 999.0
