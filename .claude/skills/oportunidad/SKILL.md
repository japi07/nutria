---
name: oportunidad
description: Evaluación en profundidad de una oportunidad registrada empezando por sus compuertas en el registro y el descarte rápido (regulación, toxicología, destino digestivo y modelo del ganadero), y después evidencia con doble extracción, cuantificación, especialistas, crítica, puntuación ciega por evaluadores independientes antes del informe e informe con sello comprobado.
argument-hint: "O-0001"
disable-model-invocation: true
---

# Evaluación en profundidad de $ARGUMENTS

El workflow `evaluar-oportunidad-auto` (`{"id": "O-xxxx"}`; no es una orden con `/`: se lanza por su nombre con la
herramienta Workflow, que está en Claude Code 2.1.318 pero no en el paquete npm 2.1.42) ejecuta este mismo
procedimiento.
Quien genera no evalúa: los evaluadores puntúan antes de que el director redacte la síntesis, el informe y la
decisión, y nunca los leen.

1. **Contexto y compuertas del registro**: `nutria --agente <tu-rol> oportunidad ver $ARGUMENTS --sin-puntuaciones --json`. Si alguna
   de `compuertas_efectivas` está `bloqueada` (o la oportunidad está `bloqueada` o `descartada`), **detente** antes de
   gastar en nada más y preséntalo a una persona. Lee `research/planes/$ARGUMENTS.md` si existe; si no, pide antes un
   plan a `director-cientifico`.
2. **Descarte rápido (primero, en paralelo)**: los análisis más baratos con más probabilidad de refutar la
   oportunidad, cada uno con su propuesta registrada sin `--firma-humana` por el rol propietario de la compuerta:
   - `regulacion-seguridad`: skill `triage-regulatorio` → compuerta `regulatoria`;
   - `compuestos-toxicologia`: señales de seguridad → compuerta `seguridad`;
   - `fisiologia-bioquimica`: destino digestivo con `nutria --agente fisiologia-bioquimica destino ruminal` → compuerta `destino_digestivo` (sin
     extrapolar efectos de incubaciones cortas en lote, que no capturan la adaptación de la microbiota ruminal);
   - `economia-mercado-pi`: modelo del ganadero con `nutria --agente economia-mercado-pi econ ganadero <toml> --exigir-fuentes` (coste por animal y día, respuesta
     umbral, P10/P50/P90).
   Si alguno propone `bloqueada`, **detente**: registra el motivo (`nutria --agente <tu-rol> decision registrar ...`, sin firma) y
   preséntalo a una persona. Salir de `bloqueada` exige firma humana. Vuelve a leer las compuertas del registro: el
   registro manda aunque un agente no lo haya dicho.
3. **Evidencia (F4)**: `investigador-bibliografico` hace la revisión de alcance; si el plan lo exige, usa
   `/revision-sistematica`.
4. **Doble extracción y cuantificación (F6)**: con estudios cuantitativos en la especie diana, dos `extractor-datos`
   independientes escriben `research/datos/$ARGUMENTS/extraccion_<fuente>_A.json` y `..._B.json`; cada extractor deposita su copia con su propia identidad (`nutria --agente extractor-datos-A extraccion depositar --datos <A.json>`, y la B con extractor-datos-B); un tercer agente, que no es ninguno de los dos, la compara registrándola (`nutria --agente extractor-datos-C extraccion comparar <A> <B> --registrar`: código 0, concordantes y ya registradas; código 1, comparación CE-xxxx con discrepancias) y, si hay discrepancias, las resuelve releyendo la fuente y registra la conciliación (`nutria --agente extractor-datos-C extraccion conciliar <CE-xxxx> --datos <conciliacion.json>`, formato en `ejemplos/conciliacion_ejemplo.json`). Nunca con `extraccion agregar`. Para repetir una fuente tras discrepancias hacen falta identidades nuevas (identidades por oportunidad y ejecución en el workflow). Con ≥ 3 estudios comparables,
   `estadistico` aplica `/metaanalisis`.
5. **Especialistas en paralelo (F5-F8)**, cada uno con su entregable y sin ver conclusiones ajenas, sin excluir a
   `investigador-bibliografico` (también la novedad en la literatura) ni a `estadistico` si el plan les asigna
   preguntas: `fisiologia-bioquimica`, `compuestos-toxicologia`, `nutricion-formulacion`, `bioinformatico` (solo si
   aporta a una decisión), `desarrollo-industrial` (compuerta `analitica`), `regulacion-seguridad`,
   `economia-mercado-pi` (rNPV, novedad frente a productos y patentes, compuerta `propiedad_intelectual`),
   `sostenibilidad-bienestar` (compuerta `etica`). Como máximo 10 preguntas (el workflow admite hasta 15).
6. **Crítica**: `critico-red-team` sobre cada aportación; `verificador-citas` sobre las fuentes. Si algún especialista
   propone una compuerta `bloqueada`, o el registro la tiene bloqueada, **detente** antes de puntuar y del informe.
7. **Hipótesis**: actualiza su estado con motivo (`nutria --agente <tu-rol> hipotesis estado H-xxxx refutada --motivo "..."`).
8. **Puntuación ciega, antes del informe**: tres `evaluador-independiente` (identificadores distintos y, si es
   posible, modelos distintos) con la skill `puntuacion`. Leen la oportunidad con `--sin-puntuaciones`, los análisis
   de los especialistas, la crítica y la verificación de fuentes; nunca un informe, una decisión propuesta (tampoco
   con `nutria --agente <tu-rol> decision listar`, que la CLI les rechaza) ni puntuaciones ajenas. Guardan su JSON sin registrarlo y
   devuelven su sha256: aquí el registro es diferido y esta indicación prevalece sobre el paso 5 de la skill
   `puntuacion`. Antes y después de puntuar se leen los eventos `puntuacion` de la procedencia (`nutria --agente <tu-rol> procedencia
   --accion puntuacion`, solo metadatos): un registro de esa oportunidad entre las dos lecturas se informa como
   registro prematuro, para que lo revise una persona.
9. **Informe**: `/informe $ARGUMENTS`, redactado por `director-cientifico` después de la puntuación, con las
   puntuaciones emitidas y sus desacuerdos y una **decisión propuesta** (continuar, reformular o descartar) pendiente
   de firma humana. El Anexo A de respaldo lo rellena `verificador-citas`.
10. **Sello y registro**: un agente ejecuta `nutria --agente <tu-rol> informe comprobar <md> --online --sellar --json` y
    `nutria --agente <tu-rol> informe sello <md> --json`; el sello vale solo si lo dice esa salida. Si es válido, cada evaluador registra
    su fichero sin modificarlo con una sola orden literal, `echo '<sha256>  <fichero>' | sha256sum -c - && nutria
    --agente <id> --json puntuar registrar --datos <fichero>`, y cuenta como registrada solo si su salida trae
    `<fichero>: OK` y la confirmación de `puntuar registrar`; si no, **no se registran las puntuaciones ni se da el
    informe por bueno**, y se informa del motivo. Después, `nutria --agente <tu-rol> puntuar ranking`.
    Informa también de los agentes que fallaron y de lo que quedó sin hacer.
