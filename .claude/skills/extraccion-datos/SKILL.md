---
name: extraccion-datos
description: Reglas para extraer datos cuantitativos de estudios de nutrición animal (tipo de dispersión, unidad experimental, diseño, dieta basal, dosis) y registrarlos con trazabilidad y doble extracción. Úsalo antes de cualquier tabla de evidencia o metaanálisis.
---

# Extracción de datos

Esquema: `src/nutria/data/schemas/extraccion.schema.json`; plantilla: `ejemplos/extraccion_ejemplo.json`. Flujo:
`nutria fuente agregar --doi <doi>` → copias A y B en `research/datos/<tema>/` → depósito de cada copia → comparación
registrada → conciliación si hay discrepancias → exportación para el análisis.

## Reglas

1. Un registro por comparación y por variable; `ubicacion` siempre (tabla, figura "digitalizada" o página).
2. **Tipo de dispersión** exacto: `SD`, `SE` (del grupo), `SEM_combinado` (SEM único del ANOVA, lo más habitual),
   `SED` (error estándar de la diferencia) o `IC95` (semiamplitud). La conversión la hace `nutria`:
   SEM combinado → DE = SEM·√n; SED → DE = SED/√(1/n1 + 1/n2).
3. `n` = unidades experimentales por tratamiento (corrales si el corral es la unidad).
4. Diseño (`paralelo`, `cuadrado_latino`, `crossover`, `factorial`, `bloques`), duración, número y duración de
   periodos en `notas`.
5. Dieta basal en texto y en los campos numéricos cuando consten: forraje, FND, almidón, grasa y PB (% de la MS),
   proteína degradable en el rumen (`pdr_pct_pb`, % de la PB, y/o `pdr_pct_ms`, % de la MS; no puede superar
   `pb_pct_ms` y, si constan las dos, deben cuadrar con la PB) y DCAD (mEq/kg de MS). Dosis con su base y forma del
   producto (protegido, forma química, vehículo).
6. Momento respecto al parto o al hito declarado en `hito_referencia` (días con signo; negativo = antes):
   suplementación (`suplementacion_inicio_dias`, `suplementacion_fin_dias`) y ventana de observación o muestreo
   (`ventana_inicio_dias`, `ventana_fin_dias`; para un único muestreo, el mismo día en ambos).
7. Incidencias (`eventos_control`, `eventos_tratamiento`, o una proporción de animales con un trastorno aunque se
   publique como porcentaje): hito, ventana y `definicion_caso` con umbral y matriz (p. ej. "BHB en sangre ≥ 1,2
   mmol/L") son obligatorios. Se copia la definición del artículo, sin unificarla ni convertir sus unidades. El
   registro reconoce la proporción por el trastorno ('cetosis', 'SCK'), por la unidad ('% de vacas', 'casos/100
   vacas'), por los animales ('vacas cetósicas', 'vacas con BHB alto') o por un umbral o un nivel en el nombre ('BHB ≥
   1,2 mmol/L' o 'BHB alto' en '%'); si no la reconoce, la regla vale igual: regístrala con eventos y definición.
8. Periodo de transición: un registro por marcador y momento, con su matriz y su ventana respecto al parto. Ingestión
   de MS antes y después del parto (su caída preparto), balance energético calculado (indica la ecuación en `notas`),
   AGNE y BHB (balance energético negativo, cetosis subclínica o hipercetonemia), calcio total o ionizado
   (hipocalcemia) con la DCAD de la dieta preparto, triglicéridos hepáticos (lipidosis), marcadores de la exportación
   hepática de lípidos (VLDL, apolipoproteína B100) y de inflamación (haptoglobina y otras proteínas de fase aguda).
   Cada umbral es el del artículo, nunca uno de referencia escrito de memoria.
9. Financiación y conflictos de interés.
10. Un dato obligatorio que no consta va a `null` y se declara en `no_consta`; uno opcional se omite. Nada de cadenas
    vacías, de relleno ni genéricas. **Nunca se imputa ni se estima.**

## Doble extracción

1. Dos extracciones independientes (dos subagentes `extractor-datos`, ninguno ve la del otro), cada una en su
   fichero y sin registrar: `research/datos/<ID>/extraccion_<fuente>_A.json` y `..._B.json`, con los registros en
   el orden en que aparecen en la fuente.
2. Cada extractor deposita su copia: `nutria --agente extractor-datos-A extraccion depositar --datos <A.json>` (y
   `extractor-datos-B` con la suya). Se guarda el sha256 del fichero y quién la depositó. Las identidades se comparan
   sin mayúsculas, tildes ni signos (`Extractor_Datos A` es `extractor-datos-a`) y solo con letras latinas y cifras.
   Una copia depositada es definitiva: hasta que se compare, su extractor no deposita otra de la misma fuente. En un
   flujo automático, usa una identidad por extractor, oportunidad y ejecución (p. ej. `extractor-datos-A-O-0003-1`):
   una ejecución interrumpida deja su copia depositada (la siguiente puede compararla con la B de otro extractor o
   usar identidades nuevas), y quien participó en una comparación con discrepancias no vuelve a extraer esa fuente.
3. Comparación registrada: `nutria --agente <id> extraccion comparar <A.json> <B.json> --registrar` (dos copias
   depositadas por extractores distintos; código de salida 1 si hay discrepancias). Quien la registra ve las
   discrepancias: tiene que identificarse con `--agente` (o NUTRIA_AGENT) y, si las hay, ya no extrae esa fuente. Sin
   `--registrar`, `comparar` se niega (código 3) con cualquier par de ficheros, también desde un directorio fuera del
   proyecto: las discrepancias solo se ven al registrar la comparación, para que nadie iguale los borradores antes de
   depositarlos ni sustituya las copias por otras. La comparación avisa si las dos copias son idénticas byte a byte o
   si ya había extracciones simples de esa fuente y variable (`extraccion agregar`), visibles en el registro.
4. Si coinciden, el sistema registra los registros como `doble_concordante`. Si no, cada discrepancia se resuelve
   releyendo la fuente y se registra la conciliación: `nutria --agente <id> extraccion conciliar <CE-xxxx> --datos
   <conciliacion.json>` (plantilla: `ejemplos/conciliacion_ejemplo.json`). La hace otro extractor o el orquestador,
   identificado con `--agente`: quien depositó la copia A o la B no concilia esa comparación. El sistema construye
   cada registro a partir de la copia A y de las resoluciones (origen `A`, `B` o `fuente` y motivo) y lo marca
   `resuelta`; lo que no se pueda resolver va a `no_resueltos` y no se registra. Si la fuente no publica un dato,
   la resolución toma la copia que lo declaró en `no_consta` (origen `A` o `B`, también para `no_consta`) o, si las
   dos copias leyeron una cifra, `fuente` con `"valor": null` (el sistema lo declara en `no_consta`). Comprueba que
   cada pareja `a`/`b` es el mismo dato: el resultado avisa si emparejas registros de variables distintas. `nutria
   extraccion comparaciones --pendientes` lista las comparaciones sin conciliar.
5. Las copias no se corrigen después de ver las discrepancias: mientras una fuente tenga una comparación con
   discrepancias sin conciliar, no se registra otra comparación de esa fuente, y quien participó en una comparación
   con discrepancias (copia A o B, comparación o conciliación) ya no deposita copias de esa fuente. Para repetir la
   doble extracción, se cierra antes la comparación con una conciliación que declare cada registro en `no_resueltos`,
   con su motivo, y la repiten dos extractores que no participaron en ella. Solo cuentan las filas que salieron de
   una comparación o conciliación registradas, con sus valores: una fila añadida o cambiada por fuera de `nutria` no
   llega al análisis.
6. `estado_verificacion` y `verificado_por` los fija el sistema: `extraccion agregar` solo registra extracciones
   simples, que no pasan al análisis.
7. Exportación para el análisis: `nutria extraccion exportar --variable <V> --especie <especie> --csv <f>` (formato de
   análisis: `estudio, comparacion, media_trt, media_ctrl, disp_trt, disp_ctrl, tipo_disp, n_trt, n_ctrl, variable,
   unidad, especie, diseno, dosis, ...`, la dieta basal numérica con la PDR, las ventanas y la definición de caso).
   Solo exporta extracciones con doble extracción verificada; se niega a mezclar especies salvo con
   `--mezclar-especies` (y avisa); `--etapa` filtra por estado fisiológico y un aviso lista lo que los filtros dejan
   fuera; la columna `moderadores_no_informados` y un aviso señalan los estudios sin algún moderador numérico, y
   `--casos-completos <moderador>` exporta solo los que lo informan. También avisa si una variable mezcla ventanas de
   observación, si una incidencia mezcla definiciones de caso y si un estudio aporta varias comparaciones de la misma
   variable.
