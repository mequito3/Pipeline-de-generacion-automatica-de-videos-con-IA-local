---
description: Optimiza tweets/posts de X (Twitter) aplicando insights del algoritmo abierto de Twitter. Útil para cross-postear clips del canal con máximo alcance.
---

# Tweet Optimizer para Shorts Factory

Optimiza posts de X/Twitter para el nicho de confesiones y drama en español, aplicando las señales que más pondera el algoritmo.

## Señales del algoritmo (en orden de importancia)

1. **Respuestas** — El algoritmo prioriza tweets que generan debate/conversación
2. **Likes** — Señal de calidad, especialmente de cuentas con buena credibilidad
3. **Retweets con comentario** — Mejor que RT simple
4. **Profile visits** — Indica curiosidad generada
5. **SimClusters** — Resuena con comunidades específicas del nicho (drama en español, confesiones)

## Comportamiento

El usuario proporciona:
- El clip/Short que quiere postear (URL o descripción)
- O un draft de tweet a mejorar

Generar 3 versiones optimizadas del tweet:

**Versión 1: Debate trigger**
- Hace una pregunta directa que divide opiniones
- Máximo 240 caracteres
- Termina con pregunta para forzar respuesta

**Versión 2: Curiosidad + cliffhanger**
- Hook intrigante que no revela el final
- Emojis estratégicos (1-3 máximo)
- CTA explícito al video

**Versión 3: Comunidad + identificación**
- Lenguaje del nicho latino (barrio, cotidiano)
- Referencia a algo con lo que el público se identifique
- Hashtags del nicho: #confesiones #dramareal #storytime

## Análisis del draft

Si el usuario da un draft, analizar:
- Qué señales algorítmicas activa
- Qué debilidades tiene
- Proponer mejora concreta

## Formato de respuesta

Para cada versión:
```
[VERSIÓN X — tipo]
[tweet optimizado]

Por qué funciona: [1 línea]
Señal principal: [replies/likes/retweets/curiosity]
```
