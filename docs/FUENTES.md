# Fuentes de información: acceso, herramientas y licencias

Revisado el 2026-11-04. Lo marcado "verificado (2026-11-04)" se comprobó por búsqueda web en esa fecha; el resto de
condiciones de acceso, límites y licencias es orientativo y está pendiente de verificar. Cambian a menudo: compruébalas
antes de construir un flujo comercial sobre una fuente. "Orden" indica cómo se consulta desde este sistema.

## Literatura científica

| Fuente | Acceso | Orden / uso | Notas |
|---|---|---|---|
| PubMed / MEDLINE | Abierto, API E-utilities | `nutria buscar pubmed "<q>" --resumenes` | 3 peticiones/s sin clave, 10/s con `NCBI_API_KEY`. MeSH y tipos de publicación (incluye "Retracted Publication") |
| PubMed Central | Abierto | Vía Europe PMC o WebFetch | Licencias de reutilización distintas por artículo |
| Europe PMC | Abierto, API REST | `nutria buscar europepmc "<q>"` | Incluye texto completo en abierto, preprints y literatura de agencias |
| EFSA Journal | Acceso abierto | `nutria buscar efsa "<q>"` | Opiniones y guías del Panel FEEDAP |
| OpenAlex | Abierto (datos CC0), API con **clave obligatoria** | `nutria buscar openalex "<q>"` | Citas, acceso abierto, estado de retractación. Exige clave de API en todas las peticiones desde el 13-02-2026 (changelog oficial; verificado (2026-11-04)); claves gratuitas en https://openalex.org/settings/api. Guárdala en la variable de entorno `OPENALEX_API_KEY`, nunca en ficheros. Sin clave, `nutria` no consulta OpenAlex (error con instrucciones) y la verificación de referencias comprueba la retractación con Crossref y PubMed |
| Crossref | Metadatos abiertos, API | `nutria verificar --doi`, `nutria fuente agregar` | Fuente autoritativa de metadatos por DOI. Incluye las retractaciones de sus miembros y de Retraction Watch en el campo `update-to` (cada entrada con DOI, `type` y `source`; verificado (2026-11-04)) |
| Semantic Scholar | API con límites | WebFetch | Revisar condiciones de la API |
| Web of Science, Scopus | Suscripción | Manual o API institucional | Solo si la organización tiene licencia |
| CAB Abstracts | Suscripción (CABI) | Manual | Muy buena cobertura de ciencia animal y veterinaria |
| AGRIS (FAO), AGRICOLA (NAL) | Abierto | WebSearch / WebFetch | Literatura agraria y gris |
| Google Scholar | Sin API; sus condiciones prohíben la extracción automática | Solo manual | Útil como búsqueda complementaria, nunca como fuente automática |
| Cochrane Library | Suscripción parcial | Poco relevante | Casi no cubre producción animal |

## Regulación y seguridad

| Fuente | Acceso | Uso |
|---|---|---|
| EUR-Lex | Abierto | Textos consolidados; anota la fecha de consulta |
| Registro de aditivos de la UE (Comisión Europea) | Abierto (publicación periódica) | Aditivos autorizados, especie, condiciones, titular |
| Catálogo de materias primas (Reglamento (UE) 68/2013) y Registro de materias primas | Abierto | Situación de materias primas |
| EFSA: opiniones FEEDAP, guías, OpenFoodTox | Abierto | Seguridad, eficacia, datos toxicológicos |
| EURL-FA (JRC) | Abierto | Informes de evaluación de métodos analíticos de aditivos |
| EMA | Abierto | Solo si hay frontera con medicamentos veterinarios |

## Química, biología y ómicas

| Fuente | Licencia (verificar) | Orden / uso |
|---|---|---|
| PubChem | Mayoritariamente dominio público | `nutria molecula pubchem "<nombre>" --peligros` |
| ChEMBL | CC BY-SA 3.0 | `nutria molecula chembl ...`, `nutria diana ...` |
| ChEBI, Rhea | CC BY 4.0 | WebFetch |
| UniProt | CC BY 4.0 | `nutria proteina "gene:X" --especie vacuno` |
| PDB | CC0 | WebFetch |
| AlphaFold DB (predicciones) | CC BY 4.0 | WebFetch. Los pesos de AlphaFold 3 tienen condiciones de uso no comercial |
| STRING | CC BY 4.0 | `nutria red GEN1 GEN2 --especie vacuno --enriquecimiento` |
| Reactome | CC BY 4.0 | `nutria rutas buscar` y `nutria rutas analizar` |
| Gene Ontology | CC BY 4.0 | Vía STRING o WebFetch |
| KEGG | Uso académico gratuito; uso comercial con licencia | Evitar en flujos comerciales sin licencia |
| DrugBank | Uso comercial con licencia | Evitar sin licencia |
| BRENDA | Consultar licencia | WebFetch |
| GEO, ArrayExpress, MetaboLights, PRIDE, Ensembl | Abierto (condiciones del depositante) | Reanálisis por `bioinformatico` |
| Animal QTLdb, FAANG | Abierto | Genética y anotación funcional de especies ganaderas |
| Bases de metaboloma bovino y ganadero (p. ej. BMDB, LMDB) | Verificar disponibilidad | Metabolitos de leche, sangre, rumen |
| MGnify, catálogos de genomas del microbioma ruminal | Abierto | Microbioma |

## Nutrición y producción animal

| Fuente | Acceso | Notas |
|---|---|---|
| NASEM 2021, Nutrient Requirements of Dairy Cattle (8.ª ed.) | Libro (lectura en línea en NAP) y software asociado | Cita la ecuación o tabla concreta |
| Normas NASEM/NRC de otras especies (vacuno de carne, porcino, avicultura, pequeños rumiantes, peces) | Libros | Comprueba la edición vigente |
| INRAE 2018 (sistema de alimentación de rumiantes) | Libro | |
| FEDNA (tablas y normas) | Abierto en línea | Referencia en España |
| CVB, NorFor | Consultar acceso | Tablas y sistemas de los Países Bajos y nórdicos |
| Feedipedia (INRAE, CIRAD, AFZ, FAO) | Abierto | Composición y valor nutritivo de materias primas |

## Patentes y mercado

| Fuente | Acceso | Orden / uso |
|---|---|---|
| Espacenet, Google Patents, WIPO Patentscope | Abierto | `nutria patentes "<q>"` genera los enlaces |
| Lens | Web abierta; API con token | `nutria patentes "<q>" --lens` (`LENS_API_TOKEN`) |
| FAOSTAT, Eurostat, estadísticas de FEFAC | Abierto | Censos, producción, piensos |

## Dominios que el entorno debe permitir

Para que `nutria` funcione, la red debe permitir: `eutils.ncbi.nlm.nih.gov`, `pubchem.ncbi.nlm.nih.gov`,
`www.ebi.ac.uk`, `api.openalex.org`, `api.crossref.org`, `rest.uniprot.org`, `string-db.org`, `reactome.org` y, si se
usa, `api.lens.org`. Para lectura con WebFetch: `eur-lex.europa.eu`, `efsa.onlinelibrary.wiley.com`,
`food.ec.europa.eu`, `europepmc.org`, `pubmed.ncbi.nlm.nih.gov`, `www.feedipedia.org`, `doi.org`. Comprueba con
`nutria doctor`.

En Claude Code, los dominios de lectura con WebFetch de esta lista figuran en la lista `allow` de
`.claude/settings.json`. WebFetch hacia otras fuentes de este documento que no están en esa lista (p. ej. Semantic
Scholar, Eurostat o KEGG) no está preautorizado: Claude Code pide permiso para usarlo. `nutria` no usa WebFetch: sus
peticiones solo dependen de que la red permita los dominios de arriba.
