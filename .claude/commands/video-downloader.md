---
description: Descarga videos de YouTube con calidad y formato configurable. Útil para descargar Shorts virales del nicho para analizar su estructura.
---

# Video Downloader

Descarga videos de YouTube usando `yt-dlp` via el script `tools/download_video.py`.

## Uso

El usuario puede pedir:
- Descargar un Short o video de YouTube para analizar
- Extraer solo el audio (MP3)
- Elegir calidad: best, 1080p, 720p, 480p, 360p

## Comportamiento

1. Verificar que el usuario proporcionó una URL de YouTube
2. Preguntar si quiere video completo o solo audio, y la calidad deseada (default: best, mp4)
3. Determinar la carpeta de salida:
   - Shorts de referencia del nicho → `output/reference/`
   - Videos de competencia → `output/research/`
   - Otros → `output/downloads/`
4. Ejecutar el script:

```bash
python tools/download_video.py "<URL>" -o "<output_path>" -q <quality>
```

Para solo audio:
```bash
python tools/download_video.py "<URL>" -o "<output_path>" -a
```

5. Reportar título, duración y dónde se guardó el archivo

## Casos de uso en Shorts Factory

- Descargar Shorts virales del nicho (confesiones/drama en español) para analizar hook, ritmo, estructura
- Descargar el audio de música viral para estudiar el estilo
- Guardar videos de competencia para comparar thumbnails y titulos

## Notas

- `yt-dlp` se instala automáticamente si no está presente
- Los Shorts de YouTube también son válidos (usar URL `/shorts/ID` o `/watch?v=ID`)
- No descargar contenido protegido por derechos de autor para uso comercial
