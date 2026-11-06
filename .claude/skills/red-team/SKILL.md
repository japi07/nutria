---
name: red-team
description: Revisión adversarial de un documento, hipótesis o decisión por el crítico y el verificador de citas, con veredicto por afirmación (sostenida, debilitada, refutada, no_verificable), regla de fuentes, segunda opinión opcional de otra familia de modelos y acciones propuestas.
argument-hint: "<ruta del fichero o ID>"
disable-model-invocation: true
---

# Revisión adversarial de $ARGUMENTS

El workflow `red-team-auto` (`{"archivo": "<ruta>"}`; no es una orden con `/`: se lanza por su nombre con la
herramienta Workflow) ejecuta este procedimiento.

0. **Quién escribió el documento**: ningún paso lo hace su autor. Los informes y planes (`research/informes/`,
   `research/planes/`) son de `director-cientifico`; los análisis que llevan el rol en el nombre, de ese rol. Si no se
   deduce de la ruta, indica el autor (`{"archivo": "<ruta>", "autor": "<rol>"}`, o `"persona"`): sin él, el workflow no
   empieza.
1. `critico-red-team` (o `verificador-citas` si el autor es el crítico) identifica las 5-15 afirmaciones que sostienen
   la conclusión del documento, ordenadas de más a menos decisiva (el workflow revisa como máximo `max_afirmaciones`,
   15 por defecto y 30 como tope, e informa de las que quedan sin revisar). **Nunca las elige el autor del documento**
   (p. ej. el director en un informe o un plan suyo).
2. Lanza en paralelo `critico-red-team` (refutación, enfoques de biología y de viabilidad) y `verificador-citas`
   (enfoque de fuentes); si uno de ellos es el autor, lo sustituye otro rol (`fisiologia-bioquimica`,
   `regulacion-seguridad` o `investigador-bibliografico`). Pásales el documento y las afirmaciones, no tu opinión.
   Veredictos: `sostenida`, `debilitada`, `refutada` o `no_verificable` (si no se puede comprobar, nunca `sostenida`).
3. Agrega con quórum y con la **regla de fuentes** (el enfoque de fuentes es el único que comprueba las fuentes):
   hacen falta al menos 2 veredictos válidos por afirmación (si no, `sin_quorum`); `refutada` si el enfoque de fuentes
   da `refutada` (la fuente no existe, no dice eso o la contradice) o con 2 o más `refutada`; `no_verificable` con 2 o
   más `no_verificable`; `debilitada` si hay alguna `refutada` o `debilitada`; `sostenida` solo con 2 o más
   `sostenida`, ninguna `refutada` y el enfoque de fuentes en `sostenida`; en cualquier otro caso, `no_verificable`.
   Así, si el enfoque de fuentes da `no_verificable` (p. ej. sin red) o no da resultado, la afirmación nunca sale
   `sostenida`: como mucho `debilitada` o `no_verificable`.
4. Segunda opinión de otra familia de modelos (opcional): `nutria --agente <tu-rol> llm modelos --json`; si algún modelo es utilizable
   con el rol `critico`, `nutria --agente <tu-rol> llm preguntar --rol critico --archivo <fichero>`. La sensibilidad por defecto es
   `confidencial` (solo modelos locales o con acuerdo de no retención); `--sensibilidad normal` solo para información
   no confidencial. Si `llm preguntar` termina con código 1, ningún modelo respondió (o alguno falló): la segunda
   opinión no está disponible, y se dice así; no es un fallo de la revisión. La opinión de un modelo no es evidencia.
5. El mismo rol que extrajo las afirmaciones consolida en `research/revisiones/<directorio>_<nombre>_redteam.md` (el nombre incluye el
   directorio de origen: `research/informes/O-0001.md` → `informes_O-0001_redteam.md` y `research/planes/O-0001.md` →
   `planes_O-0001_redteam.md`; una revisión anterior del mismo documento no se sobrescribe: la nueva va a
   `..._redteam-2.md`, `-3.md`...) una tabla `afirmación | veredicto | motivo | acción` sin cambiar los veredictos
   agregados, e integra la segunda opinión, si la hubo, señalando coincidencias y discrepancias.
6. Registra las acciones que resulten como propuestas (`nutria --agente <rol que consolida> decision registrar`,
   cambios de estado con motivo; sin firma: las decide una persona).
