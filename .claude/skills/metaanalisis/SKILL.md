---
name: metaanalisis
description: Procedimiento de síntesis cuantitativa con nutria, desde las extracciones registradas con doble extracción hasta el metaanálisis, la meta-regresión, la dosis-respuesta de St-Pierre, la sensibilidad, el sesgo y la certeza. Úsalo con al menos 3 estudios comparables en la especie diana.
argument-hint: "O-xxxx \"variable\""
disable-model-invocation: true
---

# Metaanálisis: $ARGUMENTS

1. **Doble extracción previa**: solo entran extracciones con la doble extracción registrada (`doble_concordante` o
   `resuelta`): depósito de las dos copias, `extraccion comparar <A> <B> --registrar` y, si hubo discrepancias,
   `extraccion conciliar` (skill `extraccion-datos`). Un CSV exportado con `--incluir-sin-conciliar` (columna
   `doble_extraccion_verificada` = `no`) es solo exploratorio: decláralo.
2. **Exporta** en formato de análisis: `nutria --agente estadistico extraccion exportar --variable "<variable>" --especie "<especie>" [--etapa "<etapa>"] [--casos-completos <moderador>] --csv research/datos/<ID>/<variable>.csv`
   (columnas `estudio, comparacion, media_trt, media_ctrl, disp_trt, disp_ctrl, tipo_disp, n_trt, n_ctrl, variable,
   unidad, especie, diseno, unidad_experimental, dosis, ...` y la dieta basal numérica). Separa o excluye especies
   distintas, unidades incompatibles y diseños no combinables, y documenta por qué.
3. **Diseño y unidad experimental** (columnas `diseno` y `unidad_experimental` del CSV; nutria las lee):
   - Los crossovers y cuadrados latinos (`diseno` = `crossover` o `cuadrado_latino`) se calculan con las fórmulas de
     grupos emparejados (n = animales con ambos tratamientos). Un SEM combinado o un SED de esos diseños se
     interpreta como residual (intraanimal).
   - La **SMD** de un diseño cruzado exige la correlación intrasujeto r: columna `rho_intrasujeto` (de la
     publicación) o `--rho-intrasujeto r` como supuesto `[SUP]`, con sensibilidad (p. ej. 0,3 y 0,8). Sin r, la
     orden se detiene: usa `--medida MD` o analiza ese diseño por separado. En MD y ROM, sin r, la varianza se
     calcula como si los grupos fueran independientes (conservador) y se avisa.
   - Si se mezclan diseños o unidades experimentales, nutria lo avisa (`AVISO:` al principio de la salida y
     `avisos_diseno` en el JSON). Entonces modela el diseño como moderador categórico (`--moderador diseno`;
     referencia `paralelo`, cada coeficiente `diseno[x]` es la diferencia frente a ella; otra referencia con
     `--referencia diseno=<categoría>`) o analiza por separado (`--subgrupo diseno`); igual con
     `unidad_experimental` (referencia `animal`).
   - Con el corral (o la granja) como unidad experimental, n_trt y n_ctrl cuentan corrales, no animales.
4. **Modelo principal**: `nutria --agente estadistico meta analizar <csv> --medida MD --metodo REML` (Hartung-Knapp-Sidik-Jonkman
   modificado por defecto, `--ajuste hksj-mod`, que coincide con el estándar si q >= 1). Opciones:
   `--medida MD|SMD|ROM|RR|OR|RD`, `--metodo REML|DL|PM|FE`, `--ajuste hksj|hksj-mod|ninguno`. Informa estimación
   con IC, tau² con su IC Q-profile, I², intervalo de predicción y uno-fuera.
   - **q < 1**: el HKSJ estándar puede dar un IC más estrecho que el de Wald (pocos estudios con efectos muy
     parecidos); el modificado max(1, q) no. Con `--ajuste hksj` y q < 1, la salida empieza con un `AVISO:` e
     imprime también la p y el IC modificados: informa el modificado como resultado principal y el estándar solo
     como sensibilidad.
   - **Intervalo de predicción**: t con k - 2 gl y el EE de Wald (Higgins et al. 2009), igual con cualquier
     ajuste; no lo compares con `predict()` de metafor, que usa otros gl o z.
5. **Dependencia**: si hay varios tratamientos frente al mismo control o varias comparaciones por estudio,
   `--agregar-estudio --rho 0.5` (declara la correlación supuesta y prueba otros valores) o
   `nutria --agente estadistico meta exportar-r <csv> --salida <script.R>` y el modelo multinivel con varianza robusta en R. El script
   de `exportar-r` acepta las mismas `--medida`, `--metodo`, `--ajuste` y `--rho-intrasujeto` que `analizar` y
   reproduce su estimación, IC, p e intervalo de predicción (líneas `nutria:`).
6. **Dosis-respuesta entre estudios** (St-Pierre, estudio como efecto aleatorio):
   `nutria --agente estadistico meta stpierre <csv> --respuesta <col> --dosis <col> --estudio estudio [--covariable ...]`.
   - Por defecto descompone la pendiente en sus componentes intra y entre estudios; informa ambas.
   - **Pendiente aleatoria**: por defecto, si al menos 3 estudios tienen 3 o más niveles de dosis, se ajustan el
     modelo con intercepto aleatorio y el de intercepto y pendiente aleatorios y se elige con la razón de
     verosimilitudes REML (mezcla 50:50 de chi² con 1 y 2 gl; pendiente aleatoria si p < 0,20). Informa el modelo
     elegido y el motivo (`modelo`, `motivo_modelo`). Si no es estimable, la salida lo avisa: la p de la pendiente
     puede ser liberal si la respuesta varía entre estudios. `--pendiente-aleatoria` la fuerza (si la pregunta es
     la respuesta media de estudios heterogéneos) y `--sin-pendiente-aleatoria` la quita (solo como sensibilidad).
   - La p usa t con gl = estudios - efectos fijos (conservadora; no es Satterthwaite ni Kenward-Roger): con pocos
     estudios puede no haber gl. Confírmalo en R con lme4/lmerTest antes de concluir.
7. **Moderadores y subgrupos** solo preespecificados: `--moderador dosis`, `--subgrupo diseno`. Con menos de unos 10
   estudios por moderador, el resultado es exploratorio.
8. **Sensibilidad**: uno-fuera; sin estudios de alto riesgo de sesgo; sin estudios financiados por el fabricante;
   DL frente a REML; `--ajuste hksj-mod` frente a `--ajuste ninguno`; otros valores de `--rho` y de
   `--rho-intrasujeto`.
9. **Sesgo de publicación**: embudo y Egger solo si k ≥ 10; si no, decláralo como no evaluable.
10. **Modelos predictivos**: validación agrupada por estudio,
    `nutria --agente estadistico meta validar-predictivo <csv> --respuesta <Y> --predictores <X1> <X2> --estudio <S>`.
11. **Certeza** por variable: riesgo de sesgo, inconsistencia, evidencia indirecta, imprecisión, sesgo de publicación.
12. **Interpretación**: efecto en unidades naturales frente al efecto mínimo relevante fijado de antemano; traducción
    económica con `nutria --agente estadistico econ ganadero`.
13. **Potencia del ensayo de validación** con la unidad experimental real:
    `nutria --agente estadistico potencia medias --delta <efecto mínimo relevante> --de <DE entre animales>` (animal como unidad);
    con corrales, añade `--m <animales por corral> --icc <ICC>` (la salida da corrales y animales por grupo); en
    un crossover, `--diseno crossover --de-intra <DE intraanimal>` (o `--de` con `--rho-intrasujeto`); en un
    cuadrado latino, además `--diseno cuadrado_latino --tratamientos <t>`.
14. **Registro**: guarda órdenes y salidas en `research/datos/<ID>/` y añade afirmaciones `[DER]` con la orden usada.
    La procedencia guarda la huella sha256 del CSV, los parámetros y las estimaciones de cada análisis.
