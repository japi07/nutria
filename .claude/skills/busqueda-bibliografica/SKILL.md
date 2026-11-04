---
name: busqueda-bibliografica
description: Procedimiento para diseñar, ejecutar y documentar búsquedas bibliográficas repetibles (PECO, sinónimos, MeSH, varias fuentes, búsqueda de evidencia contraria y recuentos PRISMA). Úsalo siempre que haya que buscar literatura científica.
---

# Búsqueda bibliográfica documentada

## 1. Pregunta y criterios (antes de buscar)

PECO: especie y etapa; exposición (compuesto, dosis, forma); comparador; resultados. Escribe los criterios de
inclusión y exclusión.

## 2. Consulta

Bloques en inglés unidos con AND; sinónimos con OR dentro de cada bloque; MeSH en PubMed (`"Cattle"[MeSH]`);
truncamiento con `*`. Guarda la consulta exacta de cada fuente.

## 3. Fuentes

| Fuente | Orden | Para qué |
|---|---|---|
| PubMed | `nutria buscar pubmed "<q>" --resumenes` | MEDLINE, MeSH, tipos de publicación, retractaciones |
| Europe PMC | `nutria buscar europepmc "<q>"` | PMC con texto completo, preprints, agencias |
| OpenAlex | `nutria buscar openalex "<q>" --desde 2000` | cobertura amplia, citas, retractaciones |
| EFSA Journal | `nutria buscar efsa "<q>"` | opiniones del Panel FEEDAP y guías |
| Web | WebSearch / WebFetch | EUR-Lex, Registro UE de aditivos, FAO/AGRIS, Feedipedia, literatura gris |

Con suscripción de la organización: Web of Science, Scopus y CAB Abstracts (buena cobertura de ciencia animal).
Google Scholar no tiene API: solo búsqueda manual complementaria. OpenAlex exige clave de API en todas las
peticiones desde el 13-02-2026 (`OPENALEX_API_KEY`; claves gratuitas en https://openalex.org/settings/api): sin
ella, documenta que la fuente no se consultó.

## 4. Evidencia contraria (obligatoria)

Consultas específicas de resultados nulos o adversos: `("no effect" OR "did not affect" OR "not different" OR
"no difference")`, efectos adversos, metaanálisis con resultado negativo, otras etapas o especies.

## 5. Cribado y registro

Cribado por título y resumen con motivo de exclusión. Registra las incluidas con verificación:
`nutria fuente agregar --doi <doi> --tipo-estudio <tipo> --especie "<especie>"`.

## 6. Documentación

```
# Búsqueda: <tema> (<fecha>)
PECO: ...
Criterios de inclusión / exclusión: ...
| Fuente | Consulta exacta | Fecha | Resultados |
PRISMA: identificados N | duplicados N | cribados N | excluidos N (motivos) | texto completo N | incluidos N
Incluidos: | ID | tipo | especie y etapa | n | intervención | variables |
Evidencia contraria encontrada: ...
Limitaciones: solo resúmenes, fuentes no accesibles, idioma...
```
