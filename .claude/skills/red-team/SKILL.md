---
name: red-team
description: Revisión adversarial de un documento, hipótesis o decisión por el crítico y el verificador de citas, con veredicto por afirmación (sostenida, debilitada, refutada, no_verificable), segunda opinión opcional de otra familia de modelos y acciones.
argument-hint: "<ruta del fichero o ID>"
disable-model-invocation: true
---

# Revisión adversarial de $ARGUMENTS

El workflow `red-team-auto` (`{"archivo": "<ruta>"}`) ejecuta este procedimiento.

1. Identifica las 5-15 afirmaciones que sostienen la conclusión del documento.
2. Lanza en paralelo `critico-red-team` (refutación) y `verificador-citas` (fuentes). Pásales el documento y las
   afirmaciones, no tu opinión. Veredictos: `sostenida`, `debilitada`, `refutada` o `no_verificable` (si no se puede
   comprobar, nunca `sostenida`).
3. Agrega con quórum: hacen falta al menos 2 veredictos válidos por afirmación (si no, `sin_quorum`); `refutada` con
   2 o más `refutada`; `no_verificable` con 2 o más `no_verificable`; `debilitada` si hay alguna `refutada` o
   `debilitada`; `sostenida` solo con 2 o más `sostenida` y ninguna `refutada`.
4. Segunda opinión de otra familia de modelos (opcional): `nutria llm modelos --json`; si algún modelo es utilizable
   con el rol `critico`, `nutria llm preguntar --rol critico --archivo <fichero>`. La sensibilidad por defecto es
   `confidencial` (solo modelos locales o con acuerdo de no retención); `--sensibilidad normal` solo para información
   no confidencial. La opinión de un modelo no es evidencia.
5. Consolida en `research/revisiones/<nombre>_redteam.md` una tabla `afirmación | veredicto | motivo | acción` e
   integra la segunda opinión, si la hubo, señalando coincidencias y discrepancias.
6. Registra las decisiones que resulten (`nutria decision registrar`, cambios de estado con motivo; sin firma).
