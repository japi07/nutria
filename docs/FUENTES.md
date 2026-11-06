# Fuentes de información: acceso, herramientas y licencias

Revisado el 2026-11-04. Lo marcado "verificado (2026-11-04)" se comprobó por búsqueda web en esa fecha, sin dejar
traza en el registro de nutria; el resto de condiciones de acceso, límites y licencias es orientativo y está pendiente
de verificar. Cambian a menudo: compruébalas antes de construir un flujo comercial sobre una fuente. La vía
reproducible para una referencia concreta es `nutria verificar` (y, para una página sin DOI ni PMID, la verificación
manual firmada por una persona). "Orden" indica cómo se consulta desde este sistema.

## Literatura científica

| Fuente | Acceso | Orden / uso | Notas |
|---|---|---|---|
| PubMed / MEDLINE | Abierto, API E-utilities | `nutria buscar pubmed "<q>" --resumenes` | 3 peticiones/s sin clave, 10/s con `NCBI_API_KEY`. MeSH y tipos de publicación (incluye "Retracted Publication"). `buscar` pagina hasta `--max`; esearch no pasa de 10 000 identificadores (avisa de que hay que dividir la búsqueda) |
| PubMed Central | Abierto | Vía Europe PMC o WebFetch | Licencias de reutilización distintas por artículo |
| Europe PMC | Abierto, API REST | `nutria buscar europepmc "<q>"` | Incluye texto completo en abierto, preprints y literatura de agencias; da la licencia de cada resultado |
| EFSA Journal | Acceso abierto | `nutria buscar efsa "<q>"` | Opiniones y guías del Panel FEEDAP |
| OpenAlex | Abierto (datos CC0), API con **clave obligatoria** | `nutria buscar openalex "<q>"` | Citas, acceso abierto, estado de retractación. Exige clave de API en todas las peticiones desde el 13-02-2026 (changelog oficial; verificado (2026-11-04)); claves gratuitas en https://openalex.org/settings/api. Guárdala en la variable de entorno `OPENALEX_API_KEY`, nunca en ficheros. Sin clave, `nutria` no consulta OpenAlex (error de credenciales, código 4) y la verificación de referencias comprueba la retractación con Crossref y PubMed |
| Crossref | Metadatos abiertos, API | `nutria verificar --doi`, `nutria fuente agregar` | Fuente autoritativa de metadatos por DOI. Incluye las retractaciones de sus miembros y de Retraction Watch en el campo `update-to` (cada entrada con DOI, `type` y `source`; verificado (2026-11-04)). Límites que aplica nutria entre los procesos que comparten la caché: pool público, 5 peticiones/s y concurrencia 1; con correo de contacto (pool «polite»), 10/s y 3 (según la auditoría; pendiente de comprobar en vivo) |
| Registro de DOI (doi.org) | Abierto | `nutria verificar`, `nutria fuente agregar`, `nutria informe comprobar --online` | API de handles (https://doi.org/api/handles/<doi>) y agencia de registro (https://doi.org/doiRA/<doi>). Solo ahí se declara inexistente un DOI. Un DOI de otra agencia (mEDRA, JaLC, OP...) se busca también en PubMed y, si nadie da metadatos comparables, queda pendiente (`existe_sin_metadatos`) |
| DataCite | Abierto, API REST | Verificación de DOI de datos y repositorios | https://api.datacite.org/dois/<doi>. No publica retractaciones |
| Semantic Scholar | API con clave y límites | WebFetch | Sujeta al Semantic Scholar API License Agreement: revisa sus términos de uso (en especial el uso comercial y la redistribución) antes de usarla en un proyecto comercial (pendiente de verificar) |
| Web of Science, Scopus | Suscripción | Manual o API institucional | Solo si la organización tiene licencia |
| CAB Abstracts | Suscripción (CABI) | Manual | Muy buena cobertura de ciencia animal y veterinaria |
| AGRIS (FAO), AGRICOLA (NAL) | Abierto | WebSearch / WebFetch | Literatura agraria y gris |
| Google Scholar | Sin API; sus condiciones prohíben la extracción automática | Solo manual | Útil como búsqueda complementaria, nunca como fuente automática |
| Cochrane Library | Suscripción parcial | Poco relevante | Casi no cubre producción animal |

### Licencia de cada fuente

- `nutria fuente agregar` (y `nutria verificar --pendientes`) guardan en el registro la licencia que declara la API.
  Crossref: la de la versión publicada o, si no la hay, la del manuscrito aceptado o la no especificada, nunca la de
  solo minería de textos. OpenAlex: `best_oa_location` o `primary_location`. DataCite: `rightsList`. Europe PMC la da
  en sus resultados (`buscar europepmc --registrar` la rellena si estaba vacía); PubMed no la aporta.
- Si no es la de la versión publicada o tiene embargo, se indica entre paréntesis: «<URL> (manuscrito aceptado;
  embargo: 365 días)». En un artículo de suscripción, una licencia CC suele ser solo la del manuscrito y tras el
  embargo.
- `nutria fuente listar` y `nutria fuente ver` la muestran. Es lo que declara el editor, no un dictamen jurídico.

## Regulación y seguridad

| Fuente | Acceso | Uso |
|---|---|---|
| EUR-Lex | Abierto | Textos consolidados; anota la fecha de consulta. `nutria normativa eurlex <CELEX>` da el título, la vigencia y la última versión consolidada (metadatos ELI, con caché y procedencia); no sustituye la lectura del texto ni la verificación manual firmada |
| Registro de aditivos de la UE (Comisión Europea) | Abierto (publicación periódica) | Aditivos autorizados, especie, condiciones, titular (sin cliente en nutria) |
| Catálogo de materias primas (Reglamento (UE) 68/2013) y Registro de materias primas | Abierto | Situación de materias primas (sin cliente en nutria) |
| EFSA: opiniones FEEDAP, guías, OpenFoodTox | Abierto | Seguridad, eficacia, datos toxicológicos (OpenFoodTox, sin cliente en nutria) |
| EURL-FA (JRC) | Abierto | Informes de evaluación de métodos analíticos de aditivos (sin cliente en nutria) |
| EMA | Abierto | Solo si hay frontera con medicamentos veterinarios |

## Química, biología y ómicas

| Fuente | Licencia (verificar) | Orden / uso |
|---|---|---|
| PubChem | Mayoritariamente dominio público | `nutria molecula pubchem "<nombre>" --peligros` (estado GHS: `con_frases_H`, `clasificado_sin_frases_H`, que no equivale a «sin peligro», `sin_clasificacion`, `error` o `no_consultado`) |
| ChEMBL | CC BY-SA 3.0 | `nutria molecula chembl ...`, `nutria diana ...` (por nombre, símbolo o sinónimo del gen y número de acceso de UniProt) |
| ChEBI, Rhea | CC BY 4.0 | WebFetch |
| UniProt | CC BY 4.0 | `nutria proteina "gene:X" --especie vacuno` |
| PDB | CC0 | WebFetch |
| AlphaFold DB (predicciones) | CC BY 4.0 | WebFetch. Los pesos de AlphaFold 3 tienen condiciones de uso no comercial |
| STRING | CC BY 4.0 | `nutria red GEN1 GEN2 --especie vacuno --enriquecimiento`. nutria consulta la dirección versionada (por defecto https://version-12-0.string-db.org, configurable en `nutria.toml`, sección `[fuentes]`, `string_url`) y registra la versión que comprueba |
| Reactome | CC0 según la auditoría (pendiente de verificar); antes figuraba CC BY 4.0, más restrictiva | `nutria rutas buscar` y `nutria rutas analizar` |
| Gene Ontology | CC BY 4.0 | Vía STRING o WebFetch |
| KEGG | Uso académico gratuito; uso comercial con licencia | Evitar en flujos comerciales sin licencia |
| DrugBank | Uso comercial con licencia | Evitar sin licencia |
| BRENDA | Consultar licencia | WebFetch |
| GEO, ArrayExpress, MetaboLights, PRIDE, Ensembl | Abierto (condiciones del depositante) | Reanálisis por `bioinformatico` |
| Animal QTLdb, FAANG | Abierto | Genética y anotación funcional de especies ganaderas |
| Bases de metaboloma bovino y ganadero (p. ej. BMDB, LMDB) | Verificar disponibilidad | Metabolitos de leche, sangre, rumen |
| MGnify, catálogos de genomas del microbioma ruminal | Abierto | Microbioma |

Las órdenes `molecula`, `diana`, `proteina`, `red` y `rutas` admiten `--sensibilidad confidencial`: la procedencia
guarda solo las huellas de la consulta y de la respuesta, pero la consulta viaja igualmente al servicio externo.

## Nutrición y producción animal

| Fuente | Acceso | Notas |
|---|---|---|
| NASEM 2021, Nutrient Requirements of Dairy Cattle (8.ª ed.) | Libro (lectura en línea en NAP) y software asociado | Cita la ecuación o tabla concreta |
| Normas NASEM/NRC de otras especies (vacuno de carne, porcino, avicultura, pequeños rumiantes, peces) | Libros | Comprueba la edición vigente |
| INRAE 2018 (sistema de alimentación de rumiantes) | Libro | |
| FEDNA (tablas y normas) | Abierto en línea | Referencia en España |
| CVB, NorFor | Consultar acceso | Tablas y sistemas de los Países Bajos y nórdicos |
| Feedipedia (INRAE, CIRAD, AFZ, FAO) | Consulta abierta; según su página de copyright, la copia solo se permite para usos no comerciales y los derechos comerciales se remiten a AFZ (pendiente de verificar) | Composición y valor nutritivo de materias primas |

## Patentes y mercado

| Fuente | Acceso | Orden / uso |
|---|---|---|
| Espacenet, Google Patents, WIPO Patentscope | Abierto | `nutria patentes "<q>"` genera los enlaces (la procedencia guarda solo la huella de la consulta, salvo `--registrar-consulta`) |
| Lens | Web abierta; API con token. Según fuentes secundarias, el acceso gratuito a la API es para uso no comercial y el comercial requiere licencia (pendiente de verificar en los términos oficiales de Lens) | `nutria patentes "<q>" --lens` (`LENS_API_TOKEN`) |
| FAOSTAT, Eurostat, estadísticas de FEFAC | Abierto | Censos, producción, piensos |

## Dominios que el entorno debe permitir

Para que `nutria` funcione, la red debe permitir: `eutils.ncbi.nlm.nih.gov`, `pubchem.ncbi.nlm.nih.gov`,
`www.ebi.ac.uk`, `api.openalex.org`, `api.crossref.org`, `rest.uniprot.org`, `version-12-0.string-db.org` (o la
dirección de STRING configurada), `reactome.org`, `doi.org` y `api.datacite.org` (verificación de DOI),
`eur-lex.europa.eu` (`nutria normativa`) y, si se usa, `api.lens.org`. Para lectura con WebFetch:
`eur-lex.europa.eu`, `efsa.onlinelibrary.wiley.com`, `food.ec.europa.eu`, `europepmc.org`, `pubmed.ncbi.nlm.nih.gov`,
`www.feedipedia.org`, `doi.org`. Comprueba con `nutria doctor`, que lee de esta frase los dominios de WebFetch.

La segunda opinión (`nutria llm preguntar`) y `nutria llm evaluar` necesitan además los dominios de los proveedores
activos en `config/modelos.toml`: `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com` o
`api.deepseek.com`, según el caso (un modelo local servido en la misma máquina no necesita ninguno). `nutria doctor`
no los comprueba.

En Claude Code, los dominios de lectura con WebFetch de esta lista figuran en la lista `allow` de
`.claude/settings.json`. WebFetch hacia otras fuentes de este documento que no están en esa lista (p. ej. Semantic
Scholar, Eurostat, KEGG, EURL-FA en joint-research-centre.ec.europa.eu, el BOE o la EMA) no está preautorizado: Claude
Code pide permiso para usarlo. `nutria` no usa WebFetch: sus peticiones solo dependen de que la red permita los
dominios de arriba.
