# Shorts Factory — Confesiones Dramáticas

Generador automático de **YouTube Shorts + TikTok** de historias de confesiones dramáticas en español.
Pipeline 100% automatizado: genera el guión, la voz, el video y lo sube solo.

```
Reddit/Groq (historia) → Edge TTS (voz) → Pexels (video) → FFmpeg (ensamblado) → YouTube + TikTok
```

---

## Resultados reales

Canal activo en producción con este sistema:

**YouTube:** https://www.youtube.com/@gatacuriosa001
**TikTok:** https://www.tiktok.com/@gatacuriosa001

Videos producidos y subidos 100% automáticamente — sin edición manual.

---

## Qué hace exactamente

1. **Busca una historia real** en Reddit (confesiones, secretos familiares, traiciones)
2. **Genera un guión dramático** con Groq (llama-3.3-70b) — gancho, narración, pregunta final
3. **Convierte a voz** con Microsoft Edge TTS (voz neural femenina/masculina en español)
4. **Descarga clips de Pexels** que encajan emocionalmente con la historia
5. **Ensambla el Short** (1080×1920, subtítulos animados, música de fondo)
6. **Envía por WhatsApp** el thumbnail para aprobación manual (SI/NO desde tu celular)
7. **Sube a YouTube** automáticamente (nodriver — sin detección de bot)
8. **Sube a TikTok** automáticamente (mismo video, caption correcto)
9. **Notifica por WhatsApp** con los links de YouTube y TikTok
10. **Growth agent**: pinea comentario en tu video, comenta en videos del nicho

Todo esto ocurre 3 veces al día en piloto automático con `python main.py`.

---

## Requisitos

| Componente | Versión |
|---|---|
| Python | 3.10+ |
| Chrome | Cualquier versión reciente |
| ffmpeg | Cualquier versión reciente |
| Groq API | Gratuita (500k tokens/día) |
| Pexels API | Gratuita |
| Twilio | Gratuito (sandbox WhatsApp) |

No requiere GPU. No requiere Stable Diffusion. No requiere Ollama (opcional como fallback).

---

## Instalación

```bash
# 1. Clonar el proyecto
git clone <repo>
cd crypto_shorts_factory

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar ffmpeg
winget install ffmpeg

# 4. Configurar credenciales
copy .env.example .env
# Editar .env con tus claves
```

---

## Configuración (.env)

```env
# APIs gratuitas
PEXELS_API_KEY=tu_key_de_pexels
GROQ_API_KEY=tu_key_de_groq

# YouTube (perfil Chrome con sesión activa)
YOUTUBE_UPLOAD_ENABLED=true

# TikTok (perfil Chrome separado con sesión activa)
TIKTOK_UPLOAD_ENABLED=true
TIKTOK_USERNAME=tu_usuario_sin_arroba

# WhatsApp (aprobación manual antes de subir)
WHATSAPP_APPROVAL_ENABLED=true
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_FROM=+14155238886
WHATSAPP_TO=+591xxxxxxxx

# Canal
CHANNEL_NAME=GATA CURIOSA

# Scheduler: 3 videos/día en horas pico
SCHEDULE_HOURS=8
SCHEDULE_PEAK_HOURS=7,19,21
```

### Configurar sesión de Chrome

Para YouTube:
```bash
# Abrir Chrome con el perfil del bot
chrome.exe --user-data-dir="C:\ruta\al\proyecto\chrome_profile"
# Ir a studio.youtube.com e iniciar sesión
# Cerrar Chrome
```

Para TikTok:
```bash
chrome.exe --user-data-dir="C:\ruta\al\proyecto\chrome_profile_tiktok"
# Ir a tiktok.com e iniciar sesión
# Cerrar Chrome
```

---

## Uso

### Piloto automático (recomendado)
```bash
python main.py
# Produce 3 videos/día, growth agent automático, CEO report diario
```

### Producir un video ahora
```bash
python main.py --now
```

### Con tema específico
```bash
python main.py --now --topic "secreto familiar devastador"
```

### Mantener corriendo indefinidamente (Windows PowerShell)
```powershell
while ($true) { python main.py; Start-Sleep 30 }
```

---

## Arquitectura

```
crypto_shorts_factory/
├── main.py                      # Orquestador + scheduler automático
├── config.py                    # Configuración central
├── .env                         # Credenciales (NO subir a git)
├── modules/
│   ├── script_generator.py      # Guión con Groq (llama-3.3-70b)
│   ├── tts_engine.py            # Voz con Microsoft Edge TTS
│   ├── video_assembler.py       # Video con FFmpeg + Pexels
│   ├── youtube_uploader.py      # Upload YouTube (nodriver)
│   ├── tiktok_uploader.py       # Upload TikTok (nodriver)
│   ├── whatsapp_notifier.py     # Aprobación + notificaciones
│   ├── growth_agent.py          # Comentarios automáticos (Groq)
│   ├── analytics_agent.py       # Métricas del canal
│   └── ceo_report.py            # Reporte diario por WhatsApp
├── assets/
│   ├── music/                   # Música de fondo
│   └── pexels_cache/            # Cache de clips descargados
├── output/
│   └── run_YYYYMMDD_HHMMSS/
│       ├── final_video.mp4
│       └── thumbnail.jpg
└── logs/
    └── run_YYYYMMDD_HHMMSS.log
```

---

## Módulos

### Growth Agent
Simula comportamiento humano para ganar visibilidad:
- Comenta en 2–5 videos del nicho por sesión (máx 5/día)
- Comentarios 100% generados por IA — sin plantillas fijas
- Lee comentarios existentes antes de escribir
- Likea videos, mira clips, pausa entre acciones
- Jamás usa "sígueme" o "te sigo" (bot-tell obvio)
- Filtra automáticamente videos fuera del nicho (anime, gaming, etc.)

### Analytics Agent
- Lee métricas reales del canal (views, likes, retención)
- Genera CEO Report diario por WhatsApp con los videos de mejor rendimiento

---

## Costos

| Servicio | Plan | Costo |
|---|---|---|
| Groq (LLM) | Free tier | $0 |
| Pexels (videos) | Free tier | $0 |
| Edge TTS (voz) | Incluido en Windows | $0 |
| Twilio WhatsApp | Sandbox gratuito | $0 |
| nodriver (Chrome) | Open source | $0 |
| **TOTAL** | | **$0/mes** |

---

## Anti-detección

- **nodriver** en lugar de Selenium — no inyecta `window.webdriver`
- Perfiles Chrome reales con sesión guardada (no cookies ficticias)
- Delays variables entre acciones (no timing fijo)
- Escritura carácter a carácter con pausas aleatorias
- Warm-up en home antes de ir a Studio
