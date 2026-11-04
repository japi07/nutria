---
name: red-team
description: Revisión adversarial de un documento, hipótesis o decisión por el crítico y el verificador de citas, con veredicto por afirmación y acciones.
argument-hint: "<ruta del fichero o ID>"
disable-model-invocation: true
---

# Revisión adversarial de $ARGUMENTS

1. Identifica las 5-15 afirmaciones que sostienen la conclusión del documento.
2. Lanza en paralelo `critico-red-team` (refutación) y `verificador-citas` (fuentes). Pásales el documento y las
   afirmaciones, no tu opinión.
3. Segunda opinión de otra familia de modelos, solo si hay modelos configurados y la política de datos lo permite:
   `nutria llm preguntar --rol critico --archivo <fichero>` (añade `--sensibilidad confidencial` si procede).
4. Consolida en `research/revisiones/<nombre>_redteam.md` una tabla `afirmación | veredicto | motivo | acción`.
5. Registra las decisiones que resulten (`nutria decision registrar`, cambios de estado con motivo).
