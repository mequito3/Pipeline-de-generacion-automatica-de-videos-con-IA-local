# Shorts Factory — Generador Automático de YouTube Shorts + TikTok

Pipeline 100% automatizado que genera, aprueba y publica **YouTube Shorts y TikToks** de confesiones dramáticas en español. Sin edición manual. Sin intervención humana más allá de aprobar el video por Telegram.

```
Reddit/Groq → Edge TTS → Pexels → FFmpeg → Telegram (aprobación) → YouTube API + TikTok
```

> Canal activo generado 100% con este sistema:
> **YouTube:** https://www.youtube.com/@gatacuriosa001 · **TikTok:** https://www.tiktok.com/@gatacuriosa001

---

## Qué hace exactamente

1. **Busca historias reales** en Reddit (confesiones, secretos familiares, traiciones)
2. **Genera un guión dramático** con Groq (llama-3.3-70b) — gancho, narración en 3 actos, pregunta viral al final
3. **Convierte a voz** con Microsoft Edge TTS (voz neural en español)
4. **Descarga clips de Pexels** que encajan emocionalmente con cada acto
5. **Ensambla el Short** (1080x1920, subtítulos animados, música CC0, efectos de sonido)
6. **Envía por Telegram** el video + thumbnail para aprobación manual (✅/❌)
7. **Sube a YouTube** vía Data API v3 como **Privado** (sin Chrome, sin Selenium)
8. **Sube a TikTok** automáticamente (video público inmediato)
9. **Publica en YouTube** en horarios pico via bot en servidor (12h, 18h, 21h)
10. **Responde comentarios** con IA automáticamente una vez al día
11. **Growth agent**: comenta en videos del nicho para ganar visibilidad orgánica
12. **CEO Report**: reporte diario de métricas por Telegram

---

## Arquitectura de publicación

```
[Genera video] → [Aprueba en Telegram] → [Sube a YouTube PRIVADO + TikTok PUBLICO]
                                                       |
                                       [Bot en servidor publica en horarios pico]
                                             12:00 · 18:00 · 21:00
```

Esto permite controlar exactamente cuando aparece cada video en el feed de YouTube, maximizando el alcance en horas de mayor audiencia.

---

## Stack técnico

| Componente | Tecnología |
|---|---|
| LLM | Groq (llama-3.3-70b) → OpenAI → Ollama (fallback) |
| Voz | Microsoft Edge TTS (neural, español) |
| Video stock | Pexels API |
| Ensamblado | FFmpeg |
| Aprobación | Telegram Bot |
| Upload YouTube | YouTube Data API v3 (OAuth2) |
| Upload TikTok | nodriver (Chrome automation) |
| Servidor publisher | Python + cron (Hetzner) |
| Growth | nodriver (comentarios humanos en el nicho) |

**No requiere GPU. No requiere Stable Diffusion. Costo mensual: $0.**

---

## Instalación

```bash
git clone https://github.com/mequito3/Pipeline-de-generacion-automatica-de-videos-con-IA-local.git
cd Pipeline-de-generacion-automatica-de-videos-con-IA-local
pip install -r requirements.txt
```

Requiere `ffmpeg` instalado en el sistema.

---

## Configuración

```bash
cp .env.example .env
# Editar .env con tus claves
```

Variables principales:

```env
GROQ_API_KEY=tu_key
PEXELS_API_KEY=tu_key
TELEGRAM_BOT_TOKEN=tu_bot_token
TELEGRAM_CHAT_ID=tu_chat_id
YOUTUBE_UPLOAD_ENABLED=true
YOUTUBE_PRIVACY_STATUS=private
TIKTOK_UPLOAD_ENABLED=true
TIKTOK_USERNAME=tu_usuario
VIDEOS_PER_DAY=3
```

### Setup YouTube API (una sola vez)

```bash
python setup_youtube_publisher.py
```

Abre el navegador, autorizas con tu cuenta de Google y guarda el token. No necesitas volver a hacerlo.

---

## Uso

```bash
python main.py                                    # Piloto automático, 3 videos/dia
python main.py --now                              # Generar y subir ahora
python main.py --now --topic "secreto familiar"  # Con tema especifico
python main.py --comments                         # Responder comentarios con IA
python main.py --analytics                        # Ver estadisticas del canal
```

También puedes controlar todo desde Telegram con `/generate`, `/queue`, `/stats` y más.

---

## Módulos principales

| Módulo | Descripción |
|---|---|
| `youtube_uploader.py` | Upload vía YouTube Data API v3, sin Chrome |
| `tiktok_uploader.py` | Upload con nodriver, anti-detección multicapa |
| `comment_agent.py` | Responde comentarios con IA, filtra spam, límite 30/día |
| `playlist_manager.py` | Clasifica videos en playlists automáticamente vía API |
| `growth_agent.py` | Comenta en videos del nicho con IA para ganar visibilidad |
| `analytics_agent.py` | Métricas del canal + CEO Report diario por Telegram |
| `telegram_commander.py` | Bot Telegram completo con comandos de control |
| `publish_next_private.py` | Bot standalone para servidor: privado → público en horarios pico |

---

## Estructura del proyecto

```
├── main.py                        # Orquestador principal + scheduler
├── config.py                      # Configuración central
├── setup_youtube_publisher.py     # Setup OAuth YouTube (una vez)
├── publish_next_private.py        # Bot publisher para servidor
├── modules/                       # Todos los agentes
├── output/                        # Videos generados
└── logs/                          # Logs por sesión
```

---

## Anti-detección

- YouTube usa la **API oficial** — completamente permitido por Google
- TikTok usa **nodriver** (no inyecta `window.webdriver` como Selenium)
- Perfiles Chrome reales con sesión guardada
- Escritura caracter a caracter con pausas aleatorias
- Delays variables entre acciones

---

## Costos

| Servicio | Costo |
|---|---|
| Groq API | $0 (500k tokens/día gratis) |
| Pexels API | $0 |
| Edge TTS | $0 |
| YouTube Data API | $0 (10k unidades/día, 3 uploads = 4.8k) |
| **Total** | **$0/mes** |
