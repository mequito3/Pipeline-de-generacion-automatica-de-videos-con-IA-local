---
description: Conecta el Shorts Factory con servicios externos (Reddit API, YouTube Data API, WhatsApp, Google Sheets) vía Composio. Requiere COMPOSIO_API_KEY en .env.
---

# Connect — Integraciones vía Composio

Integra el Shorts Factory con 1000+ aplicaciones usando Composio para ejecutar acciones reales (no solo generar texto).

## Setup requerido (una sola vez)

```bash
pip install composio-claude
composio login   # abre browser para autenticarse
```

Agregar al `.env`:
```
COMPOSIO_API_KEY=tu_api_key_de_platform.composio.dev
```

## Integraciones útiles para Shorts Factory

### Reddit API (reemplaza scraper artesanal)
```bash
composio add reddit
```
Permite: buscar posts por subreddit, filtrar por upvotes/tiempo, obtener comentarios

### YouTube Data API
```bash
composio add youtube
```
Permite: obtener stats reales por video (CTR, retención, impresiones) sin Selenium

### Google Sheets (analytics dashboard)
```bash
composio add googlesheets
```
Permite: exportar analytics_log.json a una hoja de cálculo para visualizar tendencias

### WhatsApp Business
```bash
composio add whatsapp
```
Alternativa a Twilio para el CEO report (sin sandbox, sin costo por mensaje en tier gratuito)

## Uso

Cuando el usuario pide conectar o interactuar con un servicio, usar el Tool Router de Composio:

```python
from composio_claude import ComposioToolSet, App

toolset = ComposioToolSet()
tools = toolset.get_tools(apps=[App.REDDIT, App.YOUTUBE])
```

El sistema maneja OAuth automáticamente en la primera conexión.

## Estado actual del proyecto

- **Reddit**: Se usa scraper JSON artesanal (`modules/reddit_scraper.py`) — Composio daría acceso a la API oficial con rate limits claros
- **YouTube**: Studio se scrapea con nodriver — Composio daría CTR/retención sin necesidad de Chrome
- **WhatsApp**: Se usa Twilio — Composio podría ser alternativa más económica
- **Google Sheets**: No implementado — sería útil para dashboard visual de analytics

Para integrar cualquiera de estos, mencionar qué servicio quieres conectar y proceder con el setup.
