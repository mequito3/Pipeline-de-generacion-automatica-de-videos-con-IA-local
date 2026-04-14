# Shorts Factory v2

Generador automático de YouTube Shorts de crypto/finanzas.
**100% local — $0 en APIs de pago.**

```
Ollama (LLM) → pyttsx3 (TTS) → Stable Diffusion (imágenes) → moviepy (video) → Selenium (YouTube)
```

---

## Requisitos de hardware

Para correr todo el pipeline 100% local necesitas una laptop/PC con al menos:

| Componente | Mínimo | Recomendado |
|------------|--------|-------------|
| **RAM** | 16 GB | 32 GB |
| **GPU VRAM** | 4 GB (Stable Diffusion CPU fallback funciona sin GPU) | 8 GB NVIDIA (RTX 3060 / 4060 o superior) |
| **CPU** | Intel i5 / Ryzen 5 (cualquier generación reciente) | Intel i7 / Ryzen 7 |
| **Almacenamiento** | 20 GB libres (modelos de IA pesan bastante) | 50 GB SSD |
| **SO** | Windows 10/11 64-bit | Windows 11 |

### ¿Qué hace cada componente?

- **RAM** — Ollama carga el modelo LLM en memoria (llama3.2 ocupa ~4 GB). Con 16 GB funciona bien.
- **GPU VRAM** — Stable Diffusion genera las imágenes. Con 4 GB genera en ~30s por imagen. Sin GPU usa la CPU (más lento, ~2-5 min por imagen).
- **CPU** — Procesa el audio TTS y el ensamblado del video con FFmpeg (rápido en cualquier CPU moderno).
- **SSD** — Los modelos de IA y los videos temporales se generan mucho más rápido en SSD vs HDD.

### Ejemplos de laptops que funcionan bien

- **Presupuesto (~$600-800):** ASUS VivoBook 16 con Ryzen 5 + 16 GB RAM + RTX 3050 (4 GB VRAM)
- **Balance (~$900-1200):** Lenovo IdeaPad Gaming 3 con Ryzen 7 + 16 GB RAM + RTX 4060 (8 GB VRAM)
- **Sin GPU dedicada:** Cualquier laptop con 16 GB RAM puede correr todo excepto SD (usa PIL fallback para imágenes)

> **Nota:** El pipeline funciona incluso sin GPU dedicada. En ese caso las imágenes se generan con gradientes de color (PIL fallback) en vez de Stable Diffusion. Todo lo demás (LLM, TTS, video) corre normal en CPU.

---

## Prerequisitos

### 1. Ollama (LLM local)

```bash
# Instalar Ollama
# https://ollama.ai → descargar para Windows

# Iniciar el servidor
ollama serve

# En otra terminal, descargar un modelo (elegir uno):
ollama pull llama3       # recomendado — mejor JSON
ollama pull mistral      # más rápido
ollama pull gemma2       # balance velocidad/calidad
ollama pull phi3         # para PCs con poca RAM
```

### 2. Stable Diffusion (imágenes locales)

**Opción A — Automatic1111:**
```bash
# Desde el directorio de A1111:
python webui.py --api --listen

# La API queda en http://localhost:7860
```

**Opción B — ComfyUI:**
```bash
# Desde el directorio de ComfyUI:
python main.py

# La API queda en http://localhost:8188
```

> Sin SD, el sistema funciona igual usando imágenes de color degradado como fallback.

### 3. ffmpeg

```bash
winget install ffmpeg
# Reiniciar terminal después de instalar
```

Verificar:
```bash
ffmpeg -version
```

### 4. Python 3.10+

```bash
python --version   # debe ser 3.10 o superior
```

### 5. Chrome

Descargar desde google.com/chrome si no lo tienes instalado.

---

## Instalación

```bash
# 1. Clonar / descargar el proyecto
cd crypto_shorts_factory

# 2. Instalar dependencias Python
pip install -r requirements.txt

# 3. Configurar credenciales
copy .env.example .env
# Editar .env con tu email y contraseña de YouTube
```

---

## Configuración

Editar el archivo `.env`:

```env
YOUTUBE_EMAIL=tucorreo@gmail.com
YOUTUBE_PASSWORD=tu_password_aqui

OLLAMA_MODEL=llama3        # o mistral, gemma2, phi3
SD_BACKEND=auto            # auto | a1111 | comfyui
SD_STEPS=20                # 15=rápido, 20=balance, 30=calidad
```

Ajustes avanzados en `config.py`.

---

## Uso

### Generar un video ahora mismo
```bash
python main.py --now
```

### Generar con tema específico
```bash
python main.py --now --topic "Solana price prediction 2025"
```

### Scheduler automático (cada 8 horas)
```bash
python main.py
```

### Probar cada módulo individualmente
```bash
python main.py --test
```

---

## Estructura de archivos

```
crypto_shorts_factory/
├── main.py                 # Orquestador + scheduler
├── config.py               # Configuración central
├── .env                    # Credenciales (NO subir a git)
├── modules/
│   ├── script_generator.py # Guión con Ollama
│   ├── tts_engine.py       # Voz con pyttsx3
│   ├── image_generator.py  # Imágenes con SD (A1111/ComfyUI)
│   ├── video_assembler.py  # Video con moviepy
│   └── youtube_uploader.py # Upload con Selenium
├── assets/
│   ├── fonts/              # Fuentes TTF para subtítulos (opcional)
│   └── music/              # Música de fondo (opcional, no implementada aún)
├── output/
│   └── run_YYYYMMDD_HHMMSS/
│       └── final_video.mp4
└── logs/
    └── run_YYYYMMDD_HHMMSS.log
```

---

## Troubleshooting en Windows

### ffmpeg no encontrado
```bash
# Opción 1: winget
winget install ffmpeg

# Opción 2: manual
# Descargar de https://www.gyan.dev/ffmpeg/builds/
# Extraer y agregar la carpeta bin\ al PATH del sistema
# Panel de control → Sistema → Variables de entorno → Path
```

### pyttsx3 sin voces / voz en inglés en vez de español
```
Settings → Time & Language → Speech → Add voices
Instalar "Spanish (Mexico)" o "Spanish (Spain)"
```

### Ollama responde lento
```bash
# Usar modelo más pequeño
ollama pull phi3
# En .env cambiar:
OLLAMA_MODEL=phi3
```

### Ollama devuelve JSON inválido
El sistema reintenta automáticamente 3 veces. Si persiste:
```bash
# Modelos más confiables para JSON estructurado:
ollama pull mistral    # muy bueno con JSON
ollama pull gemma2     # también consistente
```

### Stable Diffusion tarda mucho
```env
# En .env reducir pasos:
SD_STEPS=15
```

### Selenium / Chrome no inicia
```bash
# Actualizar undetected-chromedriver
pip install --upgrade undetected-chromedriver

# Verificar que Chrome está actualizado
```

### YouTube pide verificación / CAPTCHA
El perfil de Chrome se guarda en `chrome_profile/`.
La primera vez puede pedir verificación manual. Después usa las cookies guardadas.

### moviepy error al exportar
```
RuntimeError: ...
```
Verificar que ffmpeg está en el PATH:
```bash
where ffmpeg
```

---

## Modelos de Ollama recomendados para crypto scripts

| Modelo | Calidad JSON | Velocidad | VRAM requerida |
|--------|-------------|-----------|----------------|
| `llama3` | ⭐⭐⭐⭐⭐ | Lento | 8GB |
| `mistral` | ⭐⭐⭐⭐ | Rápido | 4GB |
| `gemma2` | ⭐⭐⭐⭐ | Medio | 5GB |
| `phi3` | ⭐⭐⭐ | Muy rápido | 2GB |
| `llama3:8b` | ⭐⭐⭐⭐ | Medio | 5GB |

**Recomendación:** `mistral` para el mejor balance entre velocidad y calidad de JSON.

---

## Configuración recomendada de SD para crypto visuals

```env
# A1111 — configuración óptima para crypto:
SD_STEPS=20
SD_CFG_SCALE=7.0
# Sampler: DPM++ 2M Karras (configurado en config.py)

# Modelos de SD recomendados para estilo crypto/fintech:
# - Realistic Vision v5 → fotorrealista, ideal para personajes/escenarios
# - DreamShaper → dramático, buen contraste
# - Juggernaut XL → alta calidad, requiere SDXL
```

Los prompts se generan automáticamente por Ollama con estilo:
*"cinematic, 8k, photorealistic, dramatic lighting, dark background"*

---

## Costos

| Servicio | Costo |
|----------|-------|
| Ollama (LLM) | $0 — local |
| Stable Diffusion (imágenes) | $0 — local |
| pyttsx3 (TTS) | $0 — local |
| moviepy (video) | $0 — local |
| Selenium (YouTube) | $0 — local |
| **TOTAL** | **$0** |
