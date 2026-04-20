---
description: Genera changelog profesional desde los commits de git. Útil para documentar qué cambió en cada versión del Shorts Factory.
---

# Changelog Generator

Analiza el historial de git y genera notas de release formateadas profesionalmente.

## Comportamiento

1. Ejecutar `git log` para obtener commits recientes:

```bash
git log --oneline --no-merges -50
```

2. Si el usuario especifica un rango de versiones o fechas, usar:
```bash
git log --oneline --no-merges v1.0..HEAD
# o
git log --oneline --no-merges --since="2 weeks ago"
```

3. Agrupar los commits en categorías:
   - **Nuevas funciones** (feat:)
   - **Correcciones** (fix:)
   - **Mejoras de rendimiento** (perf:, refactor:)
   - **Cambios internos** (chore:, docs:)

4. Convertir lenguaje técnico a descripción clara para el usuario/equipo

5. Formato de salida:

```markdown
## [fecha] — vX.Y

### Nuevas funciones
- ...

### Correcciones
- ...

### Mejoras
- ...
```

## Casos de uso en Shorts Factory

- Documentar mejoras semanales al pipeline
- Generar notas antes de un merge dev → main
- Crear un resumen de todo lo que cambió en el agente de analítica, growth agent, etc.

Genera el changelog para el período o rango que indique el usuario. Si no especifica, usar los últimos 30 commits.
