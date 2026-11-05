---
name: red-team
description: Revisión adversarial de un documento, hipótesis o decisión por el crítico y el verificador de citas, con veredicto por afirmación (sostenida, debilitada, refutada, no_verificable), regla de fuentes, segunda opinión opcional de otra familia de modelos y acciones propuestas.
argument-hint: "<ruta del fichero o ID>"
disable-model-invocation: true
---

# Revisión adversarial de $ARGUMENTS

El workflow `red-team-auto` (`{"archivo": "<ruta>"}`; no es una orden con `/`: se lanza por su nombre con la
herramienta Workflow) ejecuta este procedimiento.

1. `critico-red-team` identifica las 5-15 afirmaciones que sostienen la conclusión del documento, ordenadas de más a
   menos decisiva (el workflow revisa como máximo `max_afirmaciones`, 15 por defecto y 30 como tope, e informa de las
   que quedan sin revisar). **Nunca las elige el autor del documento** (p. ej. el director en un informe o un plan
   suyo).
2. Lanza en paralelo `critico-red-team` (refutación, enfoques de biología y de viabilidad) y `verificador-citas`
   (enfoque de fuentes). Pásales el documento y las afirmaciones, no tu opinión. Veredictos: `sostenida`,
   `debilitada`, `refutada` o `no_verificable` (si no se puede comprobar, nunca `sostenida`).
3. Agrega con quórum y con la **regla de fuentes** (el enfoque de fuentes es el único que comprueba las fuentes):
   hacen falta al menos 2 veredictos válidos por afirmación (si no, `sin_quorum`); `refutada` si el enfoque de fuentes
   da `refutada` (la fuente no existe, no dice eso o la contradice) o con 2 o más `refutada`; `no_verificable` con 2 o
   más `no_verificable`; `debilitada` si hay alguna `refutada` o `debilitada`; `sostenida` solo con 2 o más
   `sostenida`, ninguna `refutada` y el enfoque de fuentes en `sostenida`; en cualquier otro caso, `no_verificable`.
   Así, si el enfoque de fuentes da `no_verificable` (p. ej. sin red) o no da resultado, la afirmación nunca sale
   `sostenida`: como mucho `debilitada` o `no_verificable`.
4. Segunda opinión de otra familia de modelos (opcional): `nutria llm modelos --json`; si algún modelo es utilizable
   con el rol `critico`, `nutria llm preguntar --rol critico --archivo <fichero>`. La sensibilidad por defecto es
   `confidencial` (solo modelos locales o con acuerdo de no retención); `--sensibilidad normal` solo para información
   no confidencial. La opinión de un modelo no es evidencia.
5. `critico-red-team` consolida en `research/revisiones/<directorio>_<nombre>_redteam.md` (el nombre incluye el
   directorio de origen: `research/informes/O-0001.md` → `informes_O-0001_redteam.md` y `research/planes/O-0001.md` →
   `planes_O-0001_redteam.md`) una tabla `afirmación | veredicto | motivo | acción` sin cambiar los veredictos
   agregados, e integra la segunda opinión, si la hubo, señalando coincidencias y discrepancias.
6. Registra las acciones que resulten como propuestas (`nutria --agente critico-red-team decision registrar`, cambios
   de estado con motivo; sin firma: las decide una persona).
