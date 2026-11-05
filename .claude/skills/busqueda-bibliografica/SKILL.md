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

Las búsquedas de novedad que revelan la combinación inventiva van con `--sensibilidad confidencial` (la
  procedencia guarda solo la huella de la consulta, que viaja igualmente al servicio externo); también en
  `molecula`, `diana`, `proteina`, `red` y `rutas` cuando la consulta la revela. En WebSearch y WebFetch no escribas
  la combinación completa; el hook web guarda por defecto solo el dominio y la huella, y `NUTRIA_PROCEDENCIA_WEB=claro` se usa
  solo con temas no confidenciales.

Códigos de salida: `buscar` o `diana` con 1 = resultados incompletos (repite o divide la búsqueda y dilo);
  `doctor` con 1 = fallan comprobaciones esenciales (dilo y continúa con lo que no dependa de ellas).

## 3. Fuentes

Cada orden lleva tu rol escrito literalmente en `--agente` (`investigador-bibliografico`, o el tuyo si otro especialista usa esta skill): dentro de un subagente, el hook bloquea una orden `nutria` sin él o con el rol de otro.

| Fuente | Orden | Para qué |
|---|---|---|
| PubMed | `nutria --agente <tu-rol> buscar pubmed "<q>" --resumenes` | MEDLINE, MeSH, tipos de publicación, retractaciones |
| Europe PMC | `nutria --agente <tu-rol> buscar europepmc "<q>"` | PMC con texto completo, preprints, agencias |
| OpenAlex | `nutria --agente <tu-rol> buscar openalex "<q>" --desde 2000` | cobertura amplia, citas, retractaciones |
| EFSA Journal | `nutria --agente <tu-rol> buscar efsa "<q>"` | opiniones del Panel FEEDAP y guías |
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
`nutria --agente <tu-rol> fuente agregar --doi <doi> --tipo-estudio <tipo> --especie "<especie>"`.

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
