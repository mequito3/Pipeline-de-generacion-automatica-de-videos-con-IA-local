"""
company.py — Organigrama completo del Shorts Factory

Cada clase es un empleado real con su cargo, departamento y herramientas.
NO modifica la lógica existente — llama a los módulos que ya funcionan.

Estructura:
  CEO Orchestrator
  ├── Dpto. Contenido
  │   ├── ResearchAgent     — busca historias (scraper + trending_monitor)
  │   ├── ScriptAgent       — escribe y aprueba guiones (script_generator + script_scorer)
  │   └── ProductionAgent   — produce el video (tts + pexels + assembler)
  ├── Dpto. Distribución
  │   └── PublishingAgent   — sube a YouTube y TikTok
  ├── Dpto. Growth
  │   └── EngagementAgent   — comentarios, likes, viral monitor
  └── Dpto. Analytics & BI
      └── AnalyticsAgent    — métricas, CEO report, weekly report
"""

import logging
import random
import time
from pathlib import Path

import config
from modules.base_agent import BaseAgent

logger = logging.getLogger(__name__)


# ==============================================================================
# DPTO. CONTENIDO
# ==============================================================================

class ResearchAgent(BaseAgent):
    name       = "Agente de Investigación"
    role       = "Investigador de Contenido"
    department = "Dpto. Contenido"

    def run(self) -> dict:
        """
        Busca la mejor historia disponible.
        Prioridad: Reddit → Trending → Tópico rotatorio.
        Retorna: {"story": dict|None, "topic": str, "source": str}
        """
        from modules import scraper
        from modules.trending_monitor import get_trending_topic

        self.log("buscando historia en Reddit...")
        story = scraper.get_story()

        if story:
            viral_score, viral_reason = self._score_story_viral(story)
            story["viral_score"] = viral_score
            self.log(
                f"historia encontrada: '{story['titulo'][:60]}' "
                f"({story['upvotes']} upvotes) | LLM viral: {viral_score}/10"
            )
            self.notify(
                f"Historia encontrada en Reddit\n"
                f"📌 {story['titulo'][:70]}\n"
                f"👍 {story['upvotes']} upvotes | Viral: {viral_score}/10\n"
                f"💡 {viral_reason[:80]}"
            )
            return {"story": story, "topic": None, "source": story["fuente"]}

        self.log("Reddit sin resultados — consultando trending...")
        try:
            topic = get_trending_topic()
            self.log(f"tema trending detectado: '{topic}'")
        except Exception:
            from main import get_next_topic
            topic = get_next_topic()
            self.log(f"usando tópico rotatorio: '{topic}'")

        self.notify(f"Sin historia en Reddit.\nTema seleccionado: {topic}")
        return {"story": None, "topic": topic, "source": "trending/rotatorio"}

    def _score_story_viral(self, story: dict) -> tuple[int, str]:
        """LLM evalua potencial viral de la historia. Retorna (score 1-10, razon)."""
        import re
        prompt = (
            "Evalua el potencial viral para YouTube Shorts de confesiones dramaticas en espanol.\n\n"
            f"Titulo: {story.get('titulo', '')}\n"
            f"Texto: {story.get('texto', '')[:350]}\n"
            f"Upvotes: {story.get('upvotes', 0)}\n\n"
            "Responde SOLO asi: SCORE:X RAZON:motivo_breve (X es 1-10, sé brutal y honesto)"
        )
        system = (
            "Eres experto en contenido viral para YouTube Shorts en espanol. "
            "Evaluas historias con criterio brutal. 10=historia del año, 1=nadie hara clic."
        )
        result = self._llm_call(prompt, system, max_tokens=80)
        m = re.search(r"SCORE:(\d+)", result)
        score = int(m.group(1)) if m else 5
        reason = re.sub(r"SCORE:\d+\s*(?:RAZON:\s*)?", "", result).strip() or "sin evaluacion"
        return min(10, max(1, score)), reason


class ScriptAgent(BaseAgent):
    name       = "Agente de Guión"
    role       = "Redactor y Editor de Guiones"
    department = "Dpto. Contenido"

    def run(self, story: dict | None, topic: str | None) -> dict:
        """
        Escribe el guión y lo aprueba (score ≥ 7/10).
        Hasta 3 intentos de reescritura automática.
        Retorna el guión aprobado.
        """
        from modules import script_generator
        from modules.script_scorer import score_script, MIN_SCORE

        self.log("redactando guión...")

        if story:
            script = script_generator.generate_script_from_story(story)
        else:
            script = script_generator.generate_script(topic)

        score, feedback = score_script(script)
        attempts = 0

        while score < MIN_SCORE and attempts < 2:
            attempts += 1
            self.log(f"score {score}/10 insuficiente ({feedback}) — reescribiendo (intento {attempts})...")
            self.notify(f"Score {score}/10 bajo el mínimo. Reescribiendo guión...")
            script = script_generator.generate_script_from_story(story) if story \
                else script_generator.generate_script(topic)
            score, feedback = score_script(script)

        optimized_hook = self._optimize_hook(script)
        if optimized_hook:
            script["hook"] = optimized_hook
            self.log(f"hook LLM: {optimized_hook[:70]}")

        gender_label = {"female": "👩 Narradora", "male": "👨 Narrador"}.get(
            script.get("narrator_gender", ""), "🔍 Auto"
        )
        self.log(
            f"guión aprobado | '{script['title'][:55]}' | "
            f"score {score}/10 | {gender_label} | "
            f"{len(script['script_text'].split())} palabras"
        )
        self.notify(
            f"Guión listo ✍️\n"
            f"📝 {script['title'][:60]}\n"
            f"⭐ Score: {score}/10 | {gender_label}\n"
            f"🎣 Hook: {script.get('hook', '—')[:60]}"
        )
        return script


    def _optimize_hook(self, script: dict) -> str:
        """LLM mejora el hook usando memoria de rendimiento del canal."""
        try:
            from modules import agent_memory as _am
            mem = _am.load()
            top_topics = mem.get("top_topics", [])
            top_title = mem.get("content_insights", {}).get("top_video_title", "")
        except Exception:
            top_topics, top_title = [], ""

        prompt = (
            "Mejora este hook para maximizar retencion en los primeros 3 segundos de un Short.\n\n"
            f"Hook actual: {script.get('hook', '')}\n"
            f"Titulo del video: {script.get('title', '')}\n"
            f"Temas que mas funcionan en el canal: {top_topics}\n"
            f"Titulo mas exitoso del canal: {top_title}\n\n"
            "Genera UN hook mejorado (max 15 palabras, en espanol, dramatico y directo). "
            "Solo el texto del hook, sin comillas ni explicaciones."
        )
        system = (
            "Eres redactor especializado en hooks virales para YouTube Shorts de confesiones "
            "dramaticas en espanol. Cada palabra cuenta — los primeros 3 segundos lo son todo."
        )
        return self._llm_call(prompt, system, max_tokens=60)


class ProductionAgent(BaseAgent):
    name       = "Agente de Producción"
    role       = "Director de Producción"
    department = "Dpto. Contenido"

    def run(self, script: dict, run_dir: Path) -> dict:
        """
        Genera audio (TTS) y descarga clips en paralelo, luego ensambla el video.
        Retorna: {"video_path": str, "audio_duration": float, "thumbnail_path": str}
        """
        import concurrent.futures
        from modules import tts_engine, pexels_fetcher, video_assembler

        images_dir = run_dir / "images"
        narrator   = script.get("narrator_gender", "auto")
        if narrator not in ("female", "male"):
            narrator = "auto"

        self.log(f"produciendo: TTS + {len(script['scenes'])} clips Pexels en paralelo...")

        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tts_fut    = pool.submit(
                tts_engine.generate_audio,
                script["script_text"],
                output_path=str(run_dir / "narration.mp3"),
                gender=narrator,
            )
            clips_fut  = pool.submit(
                pexels_fetcher.fetch_videos,
                script["scenes"],
                str(images_dir),
            )
            audio_path  = tts_fut.result()
            image_paths = clips_fut.result()

        audio_duration = tts_engine.get_audio_duration(Path(audio_path))
        elapsed = time.time() - t0
        self.log(f"TTS + clips listos en {elapsed:.0f}s | audio: {audio_duration:.1f}s | clips: {len(image_paths)}")

        self.log("ensamblando video final...")
        video_path = video_assembler.assemble_video(
            script=script,
            audio_path=audio_path,
            images=image_paths,
            output_path=str(run_dir / "final_video.mp4"),
        )

        thumbnail_path = ""
        try:
            thumbnail_path = video_assembler.generate_thumbnail(
                script=script,
                images=image_paths,
                output_path=str(run_dir / "thumbnail.jpg"),
            )
        except Exception:
            pass

        size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        self.log(f"video listo: {size_mb:.1f} MB | {audio_duration:.0f}s de duración")
        self.notify(
            f"Video producido 🎬\n"
            f"⏱️ {audio_duration:.0f}s | 📦 {size_mb:.1f} MB\n"
            f"🎙️ {len(image_paths)} clips ensamblados"
        )
        from modules import llm_service
        return {
            "video_path":    video_path,
            "audio_duration": audio_duration,
            "thumbnail_path": thumbnail_path,
            "image_paths":   image_paths,
            "tts_backend":   tts_engine.get_last_tts_backend(),
            "llm_provider":  llm_service.get_last_provider(),
        }


# ==============================================================================
# DPTO. DISTRIBUCIÓN
# ==============================================================================

class PublishingAgent(BaseAgent):
    name       = "Agente de Publicación"
    role       = "Community Manager Técnico"
    department = "Dpto. Distribución"

    def run(self, video_path: str, script: dict, thumbnail_path: str,
            audio_duration: float) -> dict:
        """
        Sube el video a YouTube y TikTok.
        Retorna: {"youtube_url": str, "tiktok_url": str, "video_id": str}
        """
        from modules import youtube_uploader, playlist_manager, end_screen_manager
        import threading, re

        pool   = getattr(config, "HASHTAG_POOL", getattr(config, "BASE_HASHTAGS", []))
        n      = random.randint(10, 14)
        sample = random.sample(pool, min(n, len(pool)))
        tags   = list(dict.fromkeys([t for t in script.get("tags", []) if t not in sample] + sample))

        raw_desc = script["description"].strip()
        desc     = " ".join(w for w in raw_desc.split() if not w.startswith("#"))[:130].rsplit(" ", 1)[0]
        cta_f    = random.choice(config.CTA_FOLLOW)
        cta_c    = random.choice(config.CTA_COMMENTS)
        hook     = script.get("hook", "")[:90]

        _DISCLAIMER = config.VIDEO_DISCLAIMER
        pregunta     = script.get("pregunta", "")[:100]
        voiced_cta   = script.get("voiced_cta", "")[:100]
        desc_formats = [
            lambda: f"{hook}...\n\n{desc}\n\n{cta_c} 👇\n{cta_f}\n\n{_DISCLAIMER}",
            lambda: f"{desc}\n\n{pregunta}\n\n{cta_f}\n\n{_DISCLAIMER}",
            lambda: f"{hook}...\n\n{desc}\n\n{cta_c} ⬇️\n\n{_DISCLAIMER}",
            lambda: f"🔴 {desc}\n\n{cta_f}\n\n{cta_c} ⬇️\n\n{_DISCLAIMER}",
            lambda: f"{desc}\n\n{voiced_cta}\n\n{cta_f}\n\n{_DISCLAIMER}",
            lambda: f"{hook}...\n\n{pregunta}\n\n{cta_c} 👇\n\n{_DISCLAIMER}",
            lambda: f"🔴 {hook}...\n\n{desc}\n\n{pregunta}\n\n{_DISCLAIMER}",
            lambda: f"{desc}\n\nLink al canal de Telegram en mi bio 👆\n\n{_DISCLAIMER}",
        ]
        description = random.choice(desc_formats)()
        channel_link = getattr(config, "TELEGRAM_CHANNEL_LINK", "")
        if channel_link:
            _tg_cta = random.choice(config.TELEGRAM_CTA_POOL).format(link=channel_link)
            description = f"{description}\n\n{_tg_cta}"
        affiliate = getattr(config, "AFFILIATE_FOOTER", "")
        if affiliate:
            description = f"{description}\n\n{affiliate}"

        self.log("subiendo a YouTube...")
        youtube_url = youtube_uploader.upload_to_youtube(
            video_path=video_path,
            title=script["title"],
            description=description,
            tags=tags,
            thumbnail_path=thumbnail_path if thumbnail_path and Path(thumbnail_path).exists() else "",
            comment=script.get("comment", ""),
        ) or ""

        video_id = ""
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", youtube_url)
        if m:
            video_id = m.group(1)

        if youtube_url:
            self.log(f"YouTube OK → {youtube_url}")
            # Playlist + end screen en background
            if video_id:
                try:
                    playlist_manager.add_video_to_playlist(video_id, script["title"])
                except Exception:
                    pass
                threading.Thread(
                    target=self._delayed_end_screen,
                    args=(video_id, audio_duration),
                    daemon=True,
                ).start()

        # Pausa para que Chrome de YouTube se cierre completamente antes de abrir TikTok
        import time as _time
        _time.sleep(5)

        # TikTok
        tiktok_url = ""
        if getattr(config, "TIKTOK_UPLOAD_ENABLED", False):
            try:
                from modules import tiktok_uploader, telegram_commander
                self.log("subiendo a TikTok...")
                tiktok_url = tiktok_uploader.upload_to_tiktok(
                    video_path=video_path,
                    title=script["title"],
                    description=script.get("description", ""),
                    tags=script.get("tags", []),
                    comment=script.get("comment", ""),
                ) or ""
                if tiktok_url:
                    self.log(f"TikTok OK → {tiktok_url}")
                else:
                    self.log("TikTok: upload completado pero sin URL de confirmación")
            except Exception as e:
                err = str(e)
                self.log(f"TikTok falló (no crítico): {err}")
                from modules import telegram_commander as _tc
                if "sesión" in err.lower() or "login" in err.lower() or "404" in err.lower() or "no se encontró" in err.lower():
                    _tc.notify(
                        "⚠️ <b>TikTok sin sesión</b> — Chrome no tiene login guardado.\n"
                        "Abre Chrome con el perfil TikTok y loguéate manualmente:\n"
                        "<code>chrome.exe --user-data-dir=\"chrome_profile_tiktok\" https://www.tiktok.com/login</code>"
                    )
                else:
                    _tc.notify(f"⚠️ TikTok upload falló: <code>{err[:200]}</code>")

        return {"youtube_url": youtube_url, "tiktok_url": tiktok_url, "video_id": video_id}

    @staticmethod
    def _delayed_end_screen(video_id: str, duration: float) -> None:
        from modules import end_screen_manager
        time.sleep(320)
        try:
            end_screen_manager.add_end_screen(video_id, duration)
        except Exception:
            pass


# ==============================================================================
# DPTO. GROWTH
# ==============================================================================

class EngagementAgent(BaseAgent):
    name       = "Agente de Crecimiento"
    role       = "Growth & Community Manager"
    department = "Dpto. Growth"

    def run(self, youtube_url: str = "", do_own: bool = True) -> dict:
        """
        Ejecuta sesión de crecimiento en YouTube y TikTok.
        Retorna resultados de ambas plataformas.
        """
        from modules import growth_agent
        from modules.tiktok_growth_agent import run_tiktok_growth

        self.log("iniciando sesión de growth en YouTube...")
        yt_results = {}
        try:
            yt_results = growth_agent.run_growth_session(do_own=do_own, own_video_url=youtube_url)
            self.log(
                f"YouTube growth — externos: {yt_results.get('external', 0)} | "
                f"propios: {yt_results.get('own', 0)} | ❤️ {yt_results.get('hearts', 0)}"
            )
        except Exception as e:
            self.log(f"YouTube growth falló (no crítico): {e}")

        tt_results = {}
        if getattr(config, "TIKTOK_UPLOAD_ENABLED", False):
            self.log("iniciando sesión de growth en TikTok...")
            try:
                tt_results = run_tiktok_growth()
                self.log(
                    f"TikTok growth — vistos: {tt_results.get('videos_watched', 0)} | "
                    f"likes: {tt_results.get('likes', 0)} | comentarios: {tt_results.get('comments', 0)}"
                )
            except Exception as e:
                self.log(f"TikTok growth falló (no crítico): {e}")

        self.notify(
            f"Growth completado 📈\n"
            f"YouTube: {yt_results.get('external', 0)} comentarios | {yt_results.get('hearts', 0)} ❤️\n"
            f"TikTok: {tt_results.get('likes', 0)} likes | {tt_results.get('comments', 0)} comentarios"
        )
        return {"youtube": yt_results, "tiktok": tt_results}

    def monitor_viral(self, video_id: str, title: str, url: str) -> None:
        """Lanza monitor viral en background para las primeras 2h post-upload."""
        try:
            from modules import viral_monitor
            viral_monitor.start_monitor(video_id, title, url)
            self.log(f"monitor viral activo para '{title[:40]}' (2h, cada 30min)")
        except Exception as e:
            self.log(f"viral monitor no iniciado: {e}")


# ==============================================================================
# DPTO. ANALYTICS & BI
# ==============================================================================

class AnalyticsAgent(BaseAgent):
    name       = "Agente de Analítica"
    role       = "Analista de Canal & Business Intelligence"
    department = "Dpto. Analytics & BI"

    def run(self, send_report: bool = True) -> dict:
        """
        Lee métricas del canal, actualiza agent_memory y envía el CEO report.
        Los lunes también genera el reporte semanal.
        Retorna el briefing con tendencia y recomendaciones.
        """
        from modules import analytics_agent, ceo_report, weekly_report

        self.log("analizando métricas del canal...")
        try:
            analytics_agent.run_analytics_session()
            self.log("métricas actualizadas en analytics_log.json")
        except Exception as e:
            self.log(f"analytics_agent falló (no crítico): {e}")

        if send_report:
            try:
                ceo_report.run_ceo_report(send=True)
                self.log("CEO Report enviado por Telegram")
            except Exception as e:
                self.log(f"CEO Report falló (no crítico): {e}")

            if weekly_report.is_monday():
                try:
                    weekly_report.generate_weekly_report(send=True)
                    self.log("Weekly Report enviado (lunes)")
                except Exception as e:
                    self.log(f"Weekly Report falló (no crítico): {e}")

        briefing = self._briefing()
        insight = self._generate_llm_insight(briefing)
        briefing["action"] = insight if insight else "continuar estrategia actual"
        if insight:
            self.log(f"insight LLM: {insight[:80]}")
            if send_report:
                self.notify(f"Recomendacion del dia:\n{insight}")
        return briefing

    def _briefing(self) -> dict:
        """Lee agent_memory y retorna un briefing estructurado para el CEO."""
        try:
            from modules import agent_memory as _am
            mem  = _am.load()
            ci   = mem.get("content_insights", {})
            return {
                "trend":        ci.get("trend", "stable"),
                "top_topics":   mem.get("top_topics", []),
                "avoid_topics": mem.get("avoid_topics", []),
                "avg_views":    ci.get("avg_views_per_video", 0),
                "top_title":    ci.get("top_video_title", ""),
                "top_views":    ci.get("top_video_views", 0),
            }
        except Exception:
            return {"trend": "stable", "top_topics": [], "avoid_topics": [], "avg_views": 0}

    def _generate_llm_insight(self, briefing: dict) -> str:
        """Opus genera una recomendacion accionable basada en las metricas reales del canal."""
        prompt = (
            "Canal de YouTube Shorts de confesiones dramaticas en espanol.\n\n"
            f"Metricas actuales:\n"
            f"- Tendencia: {briefing.get('trend', 'stable')}\n"
            f"- Promedio vistas: {briefing.get('avg_views', 0):,}\n"
            f"- Temas exitosos: {briefing.get('top_topics', [])}\n"
            f"- Temas a evitar: {briefing.get('avoid_topics', [])}\n"
            f"- Video mas exitoso: \"{briefing.get('top_title', 'N/A')}\" "
            f"({briefing.get('top_views', 0):,} vistas)\n\n"
            "Da UNA sola recomendacion accionable para hoy (max 20 palabras, sin numeros ni bullets). "
            "Ejemplo: 'Publica traiciones maritales martes 8PM, evita temas laborales'"
        )
        system = (
            "Eres analista de datos de un canal de YouTube Shorts. "
            "Das recomendaciones concisas y directamente accionables basadas en metricas reales. "
            "Sin rodeos, sin explicaciones — solo la accion."
        )
        return self._llm_call(prompt, system, max_tokens=80)


# ==============================================================================
# CEO ORCHESTRATOR
# ==============================================================================

class CEOOrchestrator(BaseAgent):
    """
    Director General del Shorts Factory.
    Contrata a sus empleados, delega tareas y recibe reportes.
    No ejecuta trabajo operativo directamente — eso lo hacen sus agentes.
    """
    name       = "CEO Orchestrator"
    role       = "Director General"
    department = "Dirección"
    reports_to = "—"

    def __init__(self):
        from modules.channel_manager_bot import ChannelManagerBot
        self.research    = ResearchAgent()
        self.scriptwriter = ScriptAgent()
        self.production  = ProductionAgent()
        self.publishing  = PublishingAgent()
        self.engagement  = EngagementAgent()
        self.analytics   = AnalyticsAgent()
        self.channel     = ChannelManagerBot()

        self._team = [
            self.research, self.scriptwriter, self.production,
            self.publishing, self.engagement, self.analytics,
            self.channel,
        ]

    def brief_team(self) -> None:
        """Imprime el organigrama activo al arrancar."""
        print("\n" + "=" * 60)
        print("  SHORTS FACTORY — EQUIPO ACTIVO")
        print("=" * 60)
        print(f"  {self}\n")
        depts: dict[str, list] = {}
        for agent in self._team:
            depts.setdefault(agent.department, []).append(agent)
        for dept, agents in depts.items():
            print(f"  [ {dept} ]")
            for a in agents:
                print(f"     -> {a.name} -- {a.role}")
        print("=" * 60 + "\n")

    def run_pipeline(self, run_dir: Path, topic: str | None = None, skip_publish: bool = False) -> dict:
        """
        Pipeline completo delegado a los agentes:
          1. Research Agent    → encuentra la historia
          2. Script Agent      → escribe y aprueba el guión
          3. Production Agent  → produce el video
         3.5 Aprobación CEO   → ✅/❌ en Telegram (si TELEGRAM_APPROVAL_ENABLED)
          4. Publishing Agent  → sube a YouTube y TikTok (omitido si skip_publish=True)
          5. Engagement Agent  → growth post-upload + viral monitor

        skip_publish=True: usado por /generate en Telegram — el video queda en cola
        para publicarse en el próximo slot programado, no inmediatamente.
        """
        self.log("iniciando pipeline — delegando a equipo...")

        # 1. Investigación
        research_result = self.research.run()

        # 2. Guión (topic manual tiene prioridad sobre el tópico del research agent)
        script = self.scriptwriter.run(
            story=research_result["story"],
            topic=topic or research_result["topic"],
        )

        # 3. Producción
        production_result = self.production.run(script=script, run_dir=run_dir)

        # 3.5 Aprobación vía Telegram
        if getattr(config, "TELEGRAM_APPROVAL_ENABLED", False):
            from modules import telegram_notifier
            thumb_p = Path(production_result["thumbnail_path"]) if production_result.get("thumbnail_path") else None
            approved = telegram_notifier.send_approval_request(
                video_path=Path(production_result["video_path"]),
                thumbnail_path=thumb_p if thumb_p and thumb_p.exists() else None,
                title=script["title"],
                duration_s=production_result["audio_duration"],
                description=script.get("description", ""),
                tags=script.get("tags", []),
                narrator_gender=script.get("narrator_gender", "auto"),
                llm_provider=production_result.get("llm_provider", ""),
                tts_backend=production_result.get("tts_backend", ""),
            )
            if not approved:
                self.log("video RECHAZADO por el CEO — descartando...")
                return {
                    "script": script, "production": production_result,
                    "publish": {}, "approved": False,
                    "research_source": research_result["source"],
                }
            self.log("video APROBADO por el CEO.")

        # Si skip_publish → encolar para el próximo slot, no publicar ahora
        if skip_publish:
            self.log("skip_publish=True — video guardado en cola para publicación programada.")
            return {
                "script":          script,
                "production":      production_result,
                "publish":         {},
                "approved":        True,
                "queued":          True,
                "research_source": research_result["source"],
            }

        self.log("publicando...")

        # 4. Publicación
        publish_result = {"youtube_url": "", "tiktok_url": "", "video_id": ""}
        if getattr(config, "YOUTUBE_UPLOAD_ENABLED", False):
            publish_result = self.publishing.run(
                video_path=production_result["video_path"],
                script=script,
                thumbnail_path=production_result["thumbnail_path"],
                audio_duration=production_result["audio_duration"],
            )
        else:
            self.log("subida a YouTube desactivada en config")

        # 4.5 Confirmación con thumbnail al CEO por Telegram
        if getattr(config, "TELEGRAM_BOT_TOKEN", ""):
            try:
                from modules import telegram_notifier
                thumb_p = Path(production_result["thumbnail_path"]) if production_result.get("thumbnail_path") else None
                telegram_notifier.send_upload_confirmation(
                    title=script["title"],
                    youtube_url=publish_result.get("youtube_url", ""),
                    thumbnail_path=thumb_p if thumb_p and thumb_p.exists() else None,
                    duration_s=production_result["audio_duration"],
                    video_size_mb=Path(production_result["video_path"]).stat().st_size / (1024 * 1024),
                    word_count=len(script["script_text"].split()),
                    description=script.get("description", ""),
                    tags=script.get("tags", []),
                    hook=script.get("hook", ""),
                    pregunta=script.get("pregunta", ""),
                    tiktok_url=publish_result.get("tiktok_url", ""),
                )
            except Exception as e_tg:
                self.log(f"confirmación Telegram falló (no crítico): {e_tg}")

        # 5. Growth + viral monitor post-upload
        if publish_result.get("youtube_url"):
            if getattr(config, "TELEGRAM_CHANNEL_ID", ""):
                try:
                    self.channel.post_youtube_teaser(
                        youtube_url=publish_result["youtube_url"],
                        title=script["title"],
                        hook=script.get("hook", script["title"]),
                    )
                except Exception as _e_ch:
                    self.log(f"teaser canal Telegram falló (no crítico): {_e_ch}")
                try:
                    self.channel.post_extended_story_paid(script=script)
                except Exception as _e_ext:
                    self.log(f"extended story Telegram falló (no crítico): {_e_ext}")

            if publish_result.get("video_id"):
                self.engagement.monitor_viral(
                    video_id=publish_result["video_id"],
                    title=script["title"],
                    url=publish_result["youtube_url"],
                )
            self.engagement.run(
                youtube_url=publish_result["youtube_url"],
                do_own=True,
            )

        self.log(
            f"pipeline completado — '{script['title'][:55]}' | "
            f"YouTube: {'✅' if publish_result.get('youtube_url') else '❌'} | "
            f"TikTok: {'✅' if publish_result.get('tiktok_url') else '⏭️'}"
        )
        return {
            "script":           script,
            "production":       production_result,
            "publish":          publish_result,
            "approved":         True,
            "research_source":  research_result["source"],
        }

    def run_analytics(self) -> dict:
        """Delega el análisis diario al Analytics Agent."""
        self.log("solicitando reporte al Analytics Agent...")
        briefing = self.analytics.run(send_report=True)
        trend_emoji = {"rising": "📈", "stable": "➡️", "falling": "📉"}.get(briefing["trend"], "➡️")
        self.log(
            f"briefing recibido {trend_emoji} | tendencia: {briefing['trend']} | "
            f"avg vistas: {briefing.get('avg_views', 0):,} | "
            f"top temas: {briefing.get('top_topics', [])[:2]}"
        )
        plan = self.morning_briefing(briefing)
        if plan:
            briefing["daily_plan"] = plan
            self.log(f"plan del dia generado ({len(plan)} chars)")
            self.notify(f"Plan del dia:\n{plan}")
        return briefing

    def run_growth_session(self, do_own: bool = False) -> None:
        """Sesión de growth autónoma entre videos."""
        self.log("solicitando sesión de growth al Engagement Agent...")
        self.engagement.run(youtube_url="", do_own=do_own)

    def morning_briefing(self, briefing: dict) -> str:
        """Opus genera el plan estrategico del dia para el CEO."""
        try:
            from modules import agent_memory as _am
            gi = _am.load().get("growth_insights", {})
            growth_comments = gi.get("total_comments", 0)
        except Exception:
            growth_comments = 0

        prompt = (
            "Soy el CEO del Shorts Factory (canal YouTube Shorts de confesiones dramaticas en espanol).\n\n"
            f"Estado del canal hoy:\n"
            f"- Tendencia: {briefing.get('trend', 'stable')}\n"
            f"- Promedio vistas: {briefing.get('avg_views', 0):,}\n"
            f"- Mejor video: \"{briefing.get('top_title', 'N/A')}\"\n"
            f"- Temas ganadores: {briefing.get('top_topics', [])}\n"
            f"- Growth acumulado: {growth_comments} comentarios\n"
            f"- Recomendacion analytics: {briefing.get('action', 'N/A')}\n\n"
            "Dame el plan del dia en exactamente 3 bullets cortos (max 55 palabras total). "
            "Incluye: que tipo de contenido publicar hoy, una accion de growth, y un ajuste estrategico urgente."
        )
        system = (
            "Eres Chief Strategy Officer del Shorts Factory. "
            "Tus briefings son ejecutivos: 3 bullets, cortos, con verbo de accion al inicio. "
            "Sin introducciones, sin explicaciones largas."
        )
        return self._llm_call(prompt, system, max_tokens=150)
