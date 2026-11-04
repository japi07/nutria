---
name: extraccion-datos
description: Reglas para extraer datos cuantitativos de estudios de nutrición animal (tipo de dispersión, unidad experimental, diseño, dieta basal, dosis) y registrarlos con trazabilidad y doble extracción. Úsalo antes de cualquier tabla de evidencia o metaanálisis.
---

# Extracción de datos

Esquema: `src/nutria/data/schemas/extraccion.schema.json`. Ejemplo de flujo:
`nutria fuente agregar --doi <doi>` → JSON en `research/datos/<tema>/` →
`nutria --agente extractor-datos extraccion agregar --datos <fichero>`.

## Reglas

1. Un registro por comparación y por variable; `ubicacion` siempre (tabla, figura "digitalizada" o página).
2. **Tipo de dispersión** exacto: `SD`, `SE` (del grupo), `SEM_combinado` (SEM único del ANOVA, lo más habitual),
   `SED` (error estándar de la diferencia) o `IC95` (semiamplitud). La conversión la hace `nutria`:
   SEM combinado → DE = SEM·√n; SED → DE = SED/√(1/n1 + 1/n2).
3. `n` = unidades experimentales por tratamiento (corrales si el corral es la unidad).
4. Diseño (`paralelo`, `cuadrado_latino`, `crossover`, `factorial`, `bloques`), duración, número y duración de
   periodos en `notas`.
5. Dieta basal (forraje y concentrado, grasa, almidón, FND, proteína si constan), dosis con su base y forma del
   producto (protegido, forma química, vehículo).
6. Financiación y conflictos de interés.
7. Lo que no consta se deja vacío y se explica. **Nunca se imputa ni se estima.**

## Doble extracción

Dos extracciones independientes (dos subagentes `extractor-datos`, el segundo sin ver el primero). El orquestador
compara campo a campo; las discrepancias se resuelven releyendo la fuente y se marcan `discrepancia` → `resuelta`;
si coinciden, `doble_concordante`.
