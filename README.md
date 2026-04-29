# Pipeline de Generación Automática de Videos con IA Local

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-Local_LLM-black?logo=ollama)
![License](https://img.shields.io/badge/License-MIT-green)
![Last Commit](https://img.shields.io/badge/último_commit-Abril_2026-blue)

> Pipeline end-to-end para generar y publicar YouTube Shorts sobre criptomonedas y finanzas de forma completamente automatizada — sin depender de ninguna API de pago. Todo corre en tu propia máquina.

---

## ✨ Características

- **⚙️ Pipeline 100% local** — LLM, TTS, generación de imágenes y renderizado corren en tu hardware. Costo de APIs: $0.
- **🤖 Guión generado por LLM** — Ollama produce el contenido del video en base a temas configurables (cripto, finanzas, tecnología).
- **🗣️ Narración con TTS** — Voz sintética generada localmente, sin servicios externos.
- **🎨 Imágenes con Stable Diffusion** — Cada escena tiene su imagen generada por SD, con fallback automático a CPU si no hay GPU disponible.
- **🎬 Renderizado con FFmpeg** — Las escenas se combinan en un video final listo para subir.
- **📅 Scheduler integrado** — Publicación automática en horarios configurables directamente desde la config.
- **🔧 Temas configurables** — El pipeline adapta el contenido al nicho que definas en `.env`.
- **🪟 Compatible con Windows** — Incluye instrucciones de troubleshooting para PATH de FFmpeg y dependencias TTS.

---

## 🏗️ Arquitectura del Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     PIPELINE PRINCIPAL                          │
└─────────────────────────────────────────────────────────────────┘

  [config.py / .env]
        │
        ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
│   Ollama     │────▶│  TTS Local   │────▶│  Stable Diffusion    │
│  (LLM local) │     │  (narración) │     │  (imágenes / escena) │
│              │     │              │     │                      │
│ Genera guión │     │ Genera audio │     │  GPU  →  ~30s/img    │
│  por escena  │     │   .wav/.mp3  │     │  CPU  →  ~2-5 min    │
└──────────────┘     └──────────────┘     └──────────────────────┘
        │                   │                        │
        └───────────────────┴────────────────────────┘
                                │
                                ▼
                     ┌──────────────────┐
                     │     FFmpeg       │
                     │  (renderizado)   │
                     │                  │
                     │ audio + imágenes │
                     │  → video final   │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  YouTube Upload  │
                     │   (scheduler)    │
                     │                  │
                     │ Publica en los   │
                     │ horarios config. │
                     └──────────────────┘
```

---

## 🛠️ Tech Stack

| Componente | Tecnología | Rol |
|---|---|---|
| **Lenguaje** | Python 3.10+ | Orquestación del pipeline |
| **LLM** | Ollama (local) | Generación de guiones |
| **Síntesis de voz** | TTS local | Narración del video |
| **Generación de imágenes** | Stable Diffusion | Imágenes por escena |
| **Renderizado** | FFmpeg | Composición del video final |
| **Publicación** | YouTube API / automatización | Upload y scheduling |
| **Configuración** | `.env` + `config.py` | Variables de entorno y parámetros |

---

## 💻 Requisitos del Sistema

| Componente | Mínimo | Recomendado |
|---|---|---|
| **RAM** | 8 GB | 16 GB o más |
| **GPU** | No requerida (CPU fallback) | NVIDIA con soporte CUDA |
| **CPU** | Cualquier moderno | Multi-core para generación SD |
| **Almacenamiento** | ~10 GB libres | 20+ GB (modelos SD + Ollama) |
| **Python** | 3.10+ | 3.11+ |
| **FFmpeg** | En PATH del sistema | Ídem |
| **SO** | Linux / macOS / Windows | Linux para mejor rendimiento GPU |

> **Nota sobre tiempos de generación:** con GPU NVIDIA la generación de imágenes toma ~30 segundos por imagen. Sin GPU, el fallback a CPU toma entre 2 y 5 minutos por imagen.

---

## 🚀 Instalación

### 1. Prerrequisitos

Instalá los servicios locales necesarios antes de continuar:

- **Ollama** → [ollama.com](https://ollama.com) — descargá un modelo compatible (ej: `llama3`, `mistral`)
- **Stable Diffusion** → servidor local corriendo y accesible via HTTP
- **FFmpeg** → instalado y disponible en el `PATH` del sistema

> En Windows: si FFmpeg no se reconoce en terminal, agregá la ruta de `ffmpeg/bin` a las variables de entorno del sistema y reiniciá la terminal.

### 2. Clonar el repositorio

```bash
git clone https://github.com/mequito3/Pipeline-de-generacion-automatica-de-videos-con-IA-local.git
cd Pipeline-de-generacion-automatica-de-videos-con-IA-local
```

### 3. Crear entorno virtual e instalar dependencias

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
```

Editá `.env` con tus credenciales y URLs (ver sección [Configuración](#-configuración)).

### 5. Verificar la instalación de TTS

Si el módulo de voz no se instala automáticamente con `requirements.txt`, seguí las instrucciones de instalación manual incluidas en la documentación del proyecto para tu sistema operativo.

---

## ⚙️ Configuración

Copiá `.env.example` como `.env` y completá los valores:

```env
# Credenciales de YouTube
YOUTUBE_EMAIL=tu_email@gmail.com
YOUTUBE_PASSWORD=tu_contraseña
CHANNEL_NAME=NombreDeTuCanal

# URLs de servicios locales
OLLAMA_URL=http://localhost:11434
STABLE_DIFFUSION_URL=http://localhost:7860

# Tema del contenido generado
# Opciones: cripto, finanzas, tecnologia, etc.
CONTENT_TOPIC=cripto
```

> **IMPORTANTE:** Nunca subas el archivo `.env` a git. Ya está incluido en `.gitignore`.

Los parámetros adicionales del pipeline (modelo Ollama a usar, resolución de video, horarios del scheduler, etc.) se configuran en `config.py`.

---

## 📖 Uso

### Ejecutar el pipeline manualmente

```bash
python main.py
```

### Scheduler automático

El scheduler está configurado en `config.py`. Una vez activo, el pipeline se ejecuta y publica en los horarios definidos sin intervención manual.

---

## 🗺️ Roadmap

- [x] Generación de guión con LLM local (Ollama)
- [x] Narración con TTS local
- [x] Generación de imágenes con Stable Diffusion
- [x] Fallback automático CPU cuando no hay GPU
- [x] Renderizado y composición con FFmpeg
- [x] Upload automático a YouTube
- [x] Scheduler con horarios configurables
- [ ] **Música de fondo** — mezcla de audio ambiente sobre la narración *(en desarrollo)*
- [ ] Soporte multi-idioma en TTS
- [ ] Panel de estadísticas de videos publicados

---

## 📝 Estado

🔧 **En desarrollo activo** — Pipeline funcional. La funcionalidad de música de fondo está pendiente de implementación.

---

## 📄 Licencia

Distribuido bajo licencia MIT. Ver `LICENSE` para más información.

---

## 👤 Autor

**Américo Álvarez** — Desarrollador Full-Stack & Especialista en Automatizaciones con IA

- GitHub: [@mequito3](https://github.com/mequito3)
- Email: americooficial23@gmail.com
- Ubicación: Bolivia
