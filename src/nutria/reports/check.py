"""Comprobación de informes y sello de verificación (falla en cerrado).

Sin red (`check_report`):
- Estado: se lee del front matter YAML (primer bloque `---` del documento; lo que va dentro de comentarios HTML
  no cuenta, tampoco antes del front matter). Admite comillas y comentarios `#` de YAML. Un estado ilegible,
  duplicado, desconocido o fuera del front matter, un comentario HTML dentro del front matter o un «Estado: final»
  del cuerpo distinto del front matter es un error y se trata como estricto; en un informe (documento
  bajo research/informes del proyecto, también en subcarpetas; fuera de un proyecto, bajo cualquier carpeta
  `informes`) el estado es obligatorio. Lo leen igual `check_report`, `stamp_status` y el hook (`status_info`).
- Estructura: las 20 secciones numeradas (sin duplicados), marcadores `[POR COMPLETAR]` y secciones vacías (error
  en revisado o final, aviso en borrador), destino digestivo en la sección 7 (informes de rumiantes o de especie no
  declarada) y, en la sección 14, la alegación prevista, la ruta regulatoria y el estado de las compuertas de la
  rúbrica del proyecto; con registro, ese estado debe coincidir con el efectivo de la oportunidad del front matter, y
  la ruta declarada (la primera ruta conocida que sigue a «ruta», «vía» o «clasificación regulatoria» en su bloque)
  debe ser la del registro. El texto tachado u oculto (`~~...~~`, `~...~`, `<del>`, `<s>`, un `<span>` con
  line-through, hidden, display: none, tamaño u opacidad 0 o texto blanco) no cuenta como declarado.
- Etiquetas: se detectan sin distinguir mayúsculas (`[pub]` cuenta como `[PUB]`, con aviso de formato), también en
  encabezados, citas, tablas y bloques de código; una etiqueta escrita en código solo se ignora si el código no
  contiene nada más y no está pegado a un corchete o a una letra (mención del término: «las etiquetas `[PUB]` y
  `[HIP]`»; «[`PUB`]» sí cuenta), y un acento combinado dentro de un corchete corto no oculta una etiqueta ni una
  referencia. Cada segmento que empieza en una `[PUB]` necesita su propio `[Rn]` antes de la etiqueta siguiente o
  del final de la frase, aunque esté en la línea siguiente (los `[Rn]` escritos antes de la primera etiqueta de la
  frase cuentan para ella).
- Cifras, por frase (hasta «.», «!» o «?», aunque ocupe varias líneas del párrafo; una fila de tabla es una sola
  frase): una cifra con unidades (%, L/d, kg/d, mg/kg MS, g/t, Mcal, MJ, mmol/L, ppm, ºC, €, m², μL, lb...; también
  en notación científica, «1,2e6 UFC/g») o una medida de efecto («OR = 2,5», «SMD: 0,45», «R² = 0,91») necesita
  etiqueta o referencia, y una referencia `[Rn]`, una etiqueta; error en revisado o final y aviso en borrador. La
  cifra y su unidad se buscan en el texto del párrafo entero (también partidas por un salto de línea, con varios
  espacios, con un carácter invisible o una entidad entre ellas, con la unidad entre paréntesis: «2,5 (kg/d)», o
  separadas por etiquetas HTML de bloque: «<td>Leche</td><td>2,5 kg/d</td>», «leche:<br>2,5 kg/d») y, en las
  tablas (también las escritas sin «|» en los extremos), por su estructura: un número de una columna cuya cabecera
  lleva la unidad («Leche (kg/d)»), o de una fila con una celda que da la unidad (la etiqueta de la fila, aunque
  lleve cifras: «Leche, semana 4 (kg/d) | 30,1», «Leche (kg/d), semana 8 | 30,1»; una celda «+2,5 (kg/d)»; una
  columna «Unidad»; o una fila de unidades bajo la cabecera). En la cabecera, «Semana 4 (kg/d)» es el título de una
  columna; en una fila de datos es una cifra con unidades. No se detectan las cifras escritas con letras, las
  unidades fuera de la lista (las de tiempo se excluyen: «21 días» aparece en las etapas) ni la unidad que solo
  figura en el título o en una nota de la tabla. Quedan exentos el título (el primer encabezado de nivel 1) y la
  línea que lo repite en la sección 1, el front matter, la bibliografía, las tablas de respaldo del Anexo A y los
  anexos B y C (registros generados; encabezados de nivel 2 después de la bibliografía).
- Citas: una cita autor-año necesita su `[Rn]` en la misma frase (error siempre): entre paréntesis o corchetes
  («(García, 2018)», «(García Pérez, 2018)», «(van Soest, 1994)», «(de la Fuente y col., 2019)», «(St. Pierre,
  2001)», «(Smith, Jones, & Brown, 2019)», «(García, 2018: 23)»), con una entradilla de cita («(revisado por García,
  2018)», «(citado en ...)», «(véase también ...)», «(datos de ...)»), narrativa («García (2018)», «García y otros
  (2018)», «Smith J. (2019)»), con el año sustituido por «en prensa», «com. pers.» o «datos no publicados», con
  coautores sin paréntesis («García et al. 2018»), tras «según» o «véase» («según García 2018»), con el año y un
  verbo de cita junto al apellido («En 2018, García demostró», «García demostró en 2018», «García en 2018 mostró»,
  «publicado en 2018 por García», también en inglés: «In 2018, Smith showed»), «García et al.» o «García y col.» sin
  año, una línea con forma de referencia («García J, Pérez M. 2018. Título», «[^1]: García, 2018.»: con punto, coma
  o paréntesis entre los autores y el año) o, en una columna de estudios o fuentes de una tabla, «García 2018». Las
  instituciones cuentan como autores («(NASEM, 2021)», «La EFSA publicó en 2020»); «(Agenda 2030)», «(PAC, 2023)»,
  «Grupo A (2019)», «adoptado en 2009 por el Parlamento», los meses y las palabras de función («Se observó en 2019»)
  no. No se detecta «Autor Año» sin paréntesis en prosa (sin «según», «véase» ni verbo de cita) ni una cita tras una
  entradilla sin palabra de cita («(en García, 2018)», para no leer «(en España, 2019)» como cita). Son error
  siempre las citas numéricas («[1]», «[3, 4]», «[1-3]», «(ref. 3)»; no los intervalos con decimales, con unidad
  o tras «IC», «rango», «escala» o «%») y las referencias mal escritas («(R1)», «(R1, R2)», «{R1}»); una referencia
  sin corchetes tras una fórmula de cita («según R1», «véase R2») es error en los informes y en revisado o final, y
  aviso en otros documentos en borrador (una crítica o una nota de verificación hablan de las referencias).
  Una cita numérica entre paréntesis al final de la frase («... la cetosis (1).», salvo tras «ecuación», «tabla»,
  «dieta»...; si la línea tiene otros números entre paréntesis, «leve (1) o grave (2).», puede ser una enumeración:
  aviso) y un número volado tras una palabra («transición¹», «<sup>1</sup>»; no «m²», «R²», «tau²», «10⁶» ni
  «(kg/d)²») son error en revisado o final y aviso en borrador (en las tablas, aviso: pueden ser notas). Un DOI, un
  PMID, un PMCID, un arXiv, un ISBN o el enlace a un artículo (doi.org, PubMed, rutas /article/...) en el cuerpo no
  se verifica ni cuenta en el sello: error en los informes y en revisado o final, aviso en otros documentos en
  borrador, en los bloques de código y si es el identificador de una entrada de la bibliografía (que sí se verifica:
  se cita con su [Rn]); otros enlaces (normativa, herramientas), aviso, y el sello dice cuántos hay.
- Bibliografía: la sección 20 o, si no existe, la última sección de nivel 2 titulada Bibliografía o Referencias.
  Una entrada puede ocupar varias líneas (hasta la siguiente entrada, una línea en blanco o un encabezado). Un
  `[Rn]` duplicado es error; una entrada `[Rn]` con identificador fuera de la bibliografía (o un identificador de la
  bibliografía fuera de una entrada) no se verifica: error en los informes y en revisado o final; aviso en otros
  documentos en borrador.
- Anexo A, por afirmación: toda afirmación que cita una fuente (una `[PUB]` o cualquier otra etiqueta con su `[Rn]`:
  «... [SUP] [R1]» no elude el anexo) son las palabras de su frase (hasta «.», «!» o «?») que le corresponden: las
  anteriores a la etiqueta desde el inicio de la frase o desde la etiqueta anterior y, si es la última etiqueta de
  la frase, también las que la siguen; si la frase empieza con la etiqueta, las que la siguen hasta la etiqueta
  siguiente (ver `_claims`). Necesita una fila de su `[Rn]` cuya columna Afirmación la reproduzca y cuyo Respaldo
  sea `respalda` o `parcial`. Coincidencia: tras normalizar (minúsculas, sin acentos, sin etiquetas ni `[Rn]`, sin
  formato ni puntuación), el texto de la fila debe aparecer literalmente en el párrafo de la afirmación (`...` o `…`
  separan fragmentos de al menos 2 palabras que deben aparecer en orden) y cubrir al menos la mitad de sus palabras,
  con un mínimo de 4 (o todas si tiene menos). Cada fila cuenta para la afirmación de su referencia que mejor
  reproduce; una fila `respalda` o `parcial` que reproduce por igual afirmaciones distintas no cuenta para ninguna
  (una fila por afirmación) y una fila `no_respalda` o `no_verificable` cuenta para todas las que reproduce: es error
  aunque otra fila diga `respalda`, y las filas con valores distintos para el mismo par (Rn, afirmación) también.
  Las filas que no corresponden a ninguna afirmación, las que van antes de la cabecera y las que no tienen
  referencia son aviso en borrador y error en revisado o final (las negativas, siempre). Es una fila la línea que
  empieza por «|» o la que GFM muestra como fila de la tabla; la prosa con una «|» en medio, separada de la tabla, es
  texto del informe (y una valoración negativa escrita en la prosa del anexo, «R1 no respalda...», es error, salvo
  que la tabla ya tenga una fila negativa de esa referencia: entonces es una nota, aviso). En los informes
  y en los documentos con un Anexo A, una tabla con la columna Respaldo fuera del «## Anexo A» (en otra sección,
  bajo «## Anexo A1» o «## Annex A»), o una fila con una referencia y una valoración negativa fuera de él, es error
  siempre: ninguna valoración queda sin leer. En otros documentos (la crítica de red-team-auto con sus veredictos,
  una nota de verificación) esas tablas son su contenido y no se señalan.
- Remisión: una frase con `[PUB]` (o con otra etiqueta y su `[Rn]`) que remite a la anterior («Así lo indica el
  ensayo», «Esto se observó...», «Este efecto...», «Un ensayo con 60 vacas así lo indica», «... lo confirma») o que
  no tiene más de una palabra con contenido («Según el ensayo»), cuando la frase anterior no lleva etiqueta, es
  error en revisado o final y aviso en borrador: la afirmación real quedaría sin etiqueta y el Anexo A solo
  comprobaría la remisión. Un conector o una entradilla inicial («Así, ...», «Por ello, ...», «Dicho esto, ...»,
  «Según el ensayo, ...») no es una remisión si lo que sigue enuncia la afirmación. La detección es léxica: no ve la
  remisión con un encabezado de por medio ni una [PUB] sin verbo con contenido propio («Ensayo en vacas lecheras
  [PUB] [R1].»).
- Registro (si se pasa `conn`): las referencias con DOI o PMID citadas en el cuerpo con una etiqueta deben ser
  fuentes registradas (por cualquiera de sus identificadores) y verificadas, con la retractación comprobada y sin
  identificadores que contradigan los del registro (error en estricto, aviso en borrador; una fuente retractada es
  siempre error). En revisado o final, `revisado_por_humano` debe coincidir con una revisión registrada por una
  persona (`nutria informe revisar`) para el contenido exacto del informe: si el informe cambia, deja de valer.
- Revisión del crítico (informes en revisado o final): `revision_critica` en el front matter enlaza un documento de
  research/revisiones del proyecto que menciona el informe (su fichero o su oportunidad); no se juzga su contenido.

Con red (`check_report_online`, sin caché): cada DOI/PMID existe, corresponde a la cita (título, año y primer
autor) y su retractación es conocida y negativa; las referencias con solo URL valen únicamente si una persona las
registró con `nutria verificar manual`. `check_support_quotes`: si la columna Motivo del Anexo A cita texto literal
entre comillas, se busca en el resumen de la fuente (PubMed u OpenAlex); si no aparece o no hay resumen, aviso. El
texto completo de las fuentes no se comprueba.

No se comprueba: que cada fuente diga lo que se le atribuye (lo juzga `verificador-citas` en el Anexo A y lo revisa
una persona) ni la calidad del razonamiento.

El sello (`<informe>.verificacion.json`) guarda la huella SHA-256 del texto EXACTO comprobado y el detalle de la
verificación de cada referencia, y va firmado con HMAC (`nutria.sello`). Solo lo escribe `write_stamp` a partir del
resultado de `verify_report` (un proceso del mismo usuario podría fabricar ese resultado: es una barrera frente a
errores y atajos, no un control de seguridad). `stamp_status` vuelve a pasar `check_report` sobre el texto actual:
el sello solo es válido si la verificación en línea fue completa y coherente con el detalle, no hubo errores, la
estructura se comprobó entera (en informes), el documento no tiene apartados por completar y no ha cambiado.
"""
from __future__ import annotations

import bisect
import html
import json
import math
import os
import re
import sqlite3
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from functools import cached_property, lru_cache
from pathlib import Path

from .. import __version__
from .. import sello
from ..provenance import sha256_file, sha256_text, utc_now
from ..verify.citations import extract_identifiers, verify_entry

SECTION_TITLES = {
    1: "Título", 2: "Problema", 3: "Estado actual", 4: "Necesidad no resuelta", 5: "Hipótesis principal",
    6: "Hipótesis alternativas", 7: "Mecanismo", 8: "Candidatos", 9: "Evidencia", 10: "Favorables/desfavorables",
    11: "Estadística", 12: "Simulaciones", 13: "Limitaciones", 14: "Seguridad y regulación",
    15: "Formulación", 16: "Comparación comercial", 17: "Economía", 18: "Validación experimental",
    19: "Conclusión", 20: "Bibliografía",
}
LABEL_NAMES = ("PUB", "DER", "HIP", "SUP", "DESC")
LABELS = tuple(f"[{name}]" for name in LABEL_NAMES)
KNOWN_STATUSES = ("borrador", "revisado", "final")
STRICT_STATUSES = ("revisado", "final")
SUPPORT_OK = ("respalda", "parcial")
SUPPORT_VALUES = ("respalda", "parcial", "no_respalda", "no_verificable")
PENDING_MARK = "[POR COMPLETAR]"
DEFAULT_GATES = ("seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica")
MIN_ROW_WORDS = 4          # palabras mínimas que una fila del Anexo A debe reproducir de su afirmación
MIN_ROW_COVERAGE = 0.5     # ... y fracción mínima de las palabras de la afirmación
MIN_FRAGMENT_WORDS = 2     # palabras mínimas de cada fragmento separado por «...»
NO_REVIEWER = ("", "null", "none", "no", "pendiente", "~", "-", "false")
REVIEW_CODE = "revision_humana"

REF_RE = re.compile(r"\[(R\d+)\]")
LABEL_RE = re.compile(r"(?i:\[\s*(pub|der|hip|sup|desc)\s*\])|[({]\s*(PUB|DER|HIP|SUP|DESC)\s*[)}]")
BAD_LABEL_RE = re.compile(r"(?i:\[\s*(?:pub|der|hip|sup|desc)\b[^\]\n]*[^\]\s][^\]\n]*\])")
GROUPED_REFS_RE = re.compile(r"\[\s*R\d+\s*(?:[,;]|\by\b|-|–)\s*R?\d+[^\]\n]*\]")
LOWER_REF_RE = re.compile(r"\[(r\d+)\]")
PENDING_RE = re.compile(r"\[\s*por\s*completar", re.IGNORECASE)
# El marcador escrito como código al principio de una línea («`[POR COMPLETAR]` ...») sigue siendo un marcador; a
# mitad de frase es una mención del término (como en la convención de la plantilla).
CODE_PENDING_RE = re.compile(r"^\s*(?:[-*+>]\s*|\d+[.)]\s*)*`+\s*\[\s*por\s*completar", re.IGNORECASE)
ENTRY_RE = re.compile(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?\[(R\d+)\](?:\*\*|__)?\s*[:.]?\s*(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^\s{0,3}(?:>\s*)*(?:[-*+]|\d+[.)])\s+")
TERM_SPAN_RE = re.compile(r"(?i)^\[?\s*(?:pub|der|hip|sup|desc|rn|r\d+|por\s+completar)\s*\]?$")
ID_HINT_RE = re.compile(r"\b10\.\d{4,9}/|\bPMID:?\s*\d{5,9}\b|https?://", re.IGNORECASE)
QUOTE_RE = re.compile(r"«([^»]+)»|\"([^\"]+)\"|“([^”]+)”")

# Cifra, también en notación científica («1,2e6»; «10⁶» se lee «10^6»).
_NUM = r"(?<![\w.,/])[-+±−]?\d+(?:[.,]\d+)*(?:[eE][-+−]?\d+)?"
_WORD_UNITS = ("puntos porcentuales", "por ciento", "ppm", "ppb", "pp", "mEq", "mmol", "μmol", "umol", "nmol", "mol",
               "mg", "μg", "ug", "ng", "kgs", "kg", "g", "t", "Mcal", "kcal", "cal", "MJ", "kJ", "mL", "dL", "μL", "uL",
               "L", "UI", "IU", "UFC", "CFU", "EUR", "USD", "cm", "mm", "km", "μm", "m", "m^2", "m^3", "m2", "m3",
               "cm^2", "cm^3", "cm2", "cm3", "lb", "lbs", "oz", "kPa", "MPa", "UFL", "UFV", "UGM", "log", "pmol", "nm",
               "kWh", "MWh", "kW", "MW",
               # unidades escritas con todas sus letras
               "gramo", "gramos", "kilogramo", "kilogramos", "kilo", "kilos", "miligramo", "miligramos",
               "microgramo", "microgramos", "litro", "litros", "mililitro", "mililitros", "tonelada", "toneladas",
               "megacaloría", "megacalorías", "megacaloria", "megacalorias", "kilocaloría", "kilocalorías",
               "kilocaloria", "kilocalorias", "megajulio", "megajulios", "euro", "euros", "céntimo", "céntimos",
               "centimo", "centimos", "dólar", "dólares", "dolar", "dolares", "gram", "grams", "kilogram", "kilograms",
               "liter", "liters", "litre", "litres", "tonne", "tonnes", "ton", "tons", "percent")
_SYMBOL_UNITS = ("%", "‰", "°", "€", "£")
_WORD_UNITS_RE = "|".join(re.escape(u) for u in sorted(_WORD_UNITS, key=len, reverse=True))
_SYMBOL_UNITS_RE = "|".join(re.escape(u) for u in _SYMBOL_UNITS)
# Cifra seguida de su unidad, con cualquier espacio en medio: varios espacios o un salto de línea (la comprobación se
# hace sobre el texto del párrafo, no línea a línea). «ºC» (indicador ordinal, que NFKC convierte en «oC») cuenta como
# grados; la unidad entre paréntesis justo después de la cifra («2,5 (kg/d)») y la moneda delante de la cifra
# («€15», «US$ 2») también.
UNIT_RE = re.compile(
    r"(?:" + _NUM + r"\s*(?:(?:" + _WORD_UNITS_RE + r")(?![^\W\d_])|(?:" + _SYMBOL_UNITS_RE + r")"
    + r"|(?-i:o\s?C)(?![^\W\d_])"
    + r"|\(\s*(?:(?:" + _WORD_UNITS_RE + r")(?![^\W\d_])|(?:" + _SYMBOL_UNITS_RE + r"))"
    + r"(?:\s*[/·]\s*[^\s()/·]+){0,3}(?:\s+[A-Za-z]{1,3})?\s*\))"
    + r"|(?<![\w$€£])(?:US\$|\$|€|£|(?-i:EUR|USD)\s)\s*\d)",
    re.IGNORECASE)
# Unidad escrita sin cifra: la cabecera de una columna («Leche (kg/d)», «Dosis, g/d», «Grasa %», «Coste €/vaca»), la
# primera celda de una fila («Leche (kg/d) | 30,1 | 32,4») o una celda de unidades («| 2,5 | kg/d |»).
# («M2» o «m3» sin cifra pueden ser nombres de grupo o de modelo: en una cabecera solo cuentan con superíndice, «m²».)
_AMBIGUOUS_HEADER_UNITS = {"m2", "m3", "cm2", "cm3"}
_HEADER_UNIT_RE = re.compile(
    r"(?:(?<![^\W\d_])(?<![^\W\d_][-‐])(?:"
    + "|".join(re.escape(u) for u in sorted(_WORD_UNITS, key=len, reverse=True)
               if len(u) > 1 and u not in _AMBIGUOUS_HEADER_UNITS)
    + r")(?![^\W\d_])(?![-‐][^\W\d_])|[%‰°€£$]|(?<![^\W\d_])[gLt](?=\s*[/·)])|[(,]\s*[gLt]\s*\)"
    + r"|(?-i:(?<![^\W\d_])o\s?C)(?![^\W\d_]))",
    re.IGNORECASE)
# Palabras con mayúscula que pueden no ser un apellido delante de un año: nombres de programas, normas o documentos
# («(Agenda 2030)», «(Horizonte 2020)», «(Campaña 2019)»), preposiciones de tiempo («(Desde 2019)»), meses («(May
# 2019)»), siglas de políticas («(PAC, 2023)») y palabras de función al principio de una frase («Se observó en
# 2019», «Han observado en 2019»). No se excluyen al reconocer la cita (muchas son también apellidos: «Han», «Son»,
# «Ley», «Plan», «Vaca», «May»...): la cita se clasifica después (`_citation_kind`). Solo queda exenta donde la
# palabra no puede ser un apellido: delante de un verbo de cita sin iniciales ni coautores, o sola delante del año en
# un paréntesis sin coma ni coautores (las siglas de políticas, también con coma). En cualquier otra posición
# («(Han et al., 2020)», «(Bloque, 2016)», «Ley et al. (2006)», «(Kim y Han, 2020)») es una cita: error en revisado o
# final (falla en cerrado) y aviso en borrador.
_PROGRAMME_WORDS = frozenset("""
agenda horizonte horizon estrategia strategy plan programa programme program reglamento regulation directiva
directive decision ley decreto norma campana censo encuesta fase semana grupo lote tabla table figura figure fig anexo
annex seccion section version ano year mes cosecha temporada ejercicio edicion edition capitulo chapter vol volumen
ensayo tratamiento experimento estudio dieta racion rebano granja explotacion parcela bloque periodo muestra animal
vaca vacas raza modelo escenario caso calendario cronograma hito hitos presupuesto objetivo objetivos entregable
entregables etapa riesgo riesgos tarea tareas actividad actividades""".split())
# Palabras de las listas que también son apellidos reales («(Han et al., 2020)», «Ley et al. (2006)», «(Plan, 2016)»,
# «(Mayo, 2019)»): solo quedan exentas donde no pueden ser un apellido (un auxiliar delante de un participio: «Han
# observado en 2019»); en revisado o final, «(Plan 2016)» o «(Mayo 2019)» es un error (falla en cerrado).
_SURNAME_LIKE = frozenset("""
han ha hay son lo ni an tal toda como sin para era bajo plan ley bloque vaca vacas granja caso campana raza""".split())
_POLICY_ACRONYMS = frozenset({"pac", "cap", "ods", "sdg", "covid"})
_MONTH_WORDS = frozenset("""enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre
diciembre january february march april may june july august september october november december""".split())
# Preposiciones y adverbios que pueden ir delante de un año en un paréntesis: «(Desde 2019)», «(In 2019)».
_TEMPORAL_WORDS = frozenset("""en in desde hasta durante tras antes despues hacia entre para por sobre since until
from during before after by for circa""".split())
_FUNCTION_WORD_SET = frozenset("""se no lo ya si ni que como cuando donde esto este esta estos estas eso ese esa esos esas
aquello aquel aquella ello ellos ellas el ella nos les los las un una unos unas en con por para sin sobre tras ante bajo
desde hasta hacia segun durante mediante entre cada todo toda todos todas otro otra otros otras ambos ambas nuestro
nuestra nuestros nuestras pero aunque mientras ademas tambien asi tal tales dicho dicha dichos dichas es son fue fueron
ha han hay era eran the this these that those it we they in on at for with from by as of an our their there
here""".split())
# Palabra de un apellido: mayúscula y al menos otra letra (con apóstrofos o guiones: «O'Neill», «García-López»,
# «St. Pierre»). Las mayúsculas fuera de Latin-1 («Şahin», «Čermák», «Łukaszewicz», «İbrahim») se reconocen porque
# las citas se buscan en el texto con esas letras sustituidas por «A» o «a» (`_latin1_letters`).
_NAME_WORD = r"(?:(?:St|Ste|Sta)\.\s?)?[A-ZÀ-ÖØ-Þ](?:['’][A-ZÀ-ÖØ-Þ])?[^\W\d_]+(?:['’\-][^\W\d_]+)*"
# Partículas de un apellido, en minúscula o no («van Soest», «Van Soest», «de Vries», «da Silva», «de la Fuente»,
# «van der Merwe», «Le Roux»; también «La EFSA» o «El Ministerio»).
_PARTICLES = (r"(?i:van|von|der|den|de|del|della|delle|dei|degli|di|da|das|dos|do|du|la|las|le|lo|los|les|ten|"
              r"ter|zu|zur|bin|ibn|al|el)")
# Apellido de hasta tres palabras con sus partículas («García Pérez», «Van Soest», «Le Roux», «de la Fuente»).
_NAME = rf"(?:{_PARTICLES}\s+){{0,3}}{_NAME_WORD}(?:\s+(?:{_PARTICLES}\s+){{0,2}}{_NAME_WORD}){{0,2}}"
# Iniciales: con punto («J.», «J. M.», «J.-M.») o dos o tres mayúsculas («JM»); una mayúscula suelta sin punto no
# cuenta («Grupo A (2019)» no es una cita).
_INITIALS = r"(?:,?\s+(?:(?:[A-ZÀ-ÖØ-Þ]\.\s?-?){1,3}|[A-ZÀ-ÖØ-Þ]{2,3}\b\.?))?"
_INITIALS_REQ = r"(?:,?\s+(?:(?:[A-ZÀ-ÖØ-Þ]\.\s?-?){1,3}|[A-ZÀ-ÖØ-Þ]{1,3}\b\.?))"
# Autores al estilo Vancouver, con las iniciales detrás de cada apellido («García J, Pérez M, López A»).
_REF_AUTHORS = (rf"{_NAME}{_INITIALS_REQ}(?:\s*,\s*{_NAME}{_INITIALS_REQ}){{0,8}}"
                r"(?:\s*,?\s+(?:et\s+al\.?|y\s+otr[oa]s|and\s+others|(?:y|e|and|&)\s+[A-ZÀ-ÖØ-Þ][^\W\d_]+"
                r"(?:\s+[A-Z]{1,3}\b\.?)?))?")
_AUTHOR = rf"{_NAME}{_INITIALS}"
_COAUTHORS = (r"(?:et\s+al\.?|et\s+alii|[ye]\s+cols?\.?|y\s+colaboradores|and\s+colleagues|y\s+otr[oa]s|"
              r"and\s+others)")
# Hasta siete autores seguidos («García, Pérez, López y Ruiz», «Smith, Jones, & Brown»): la repetición acotada evita
# el coste cuadrático en líneas largas de nombres.
_AUTHORS = rf"(?:\s*,\s*{_AUTHOR}){{0,6}}(?:\s*,?\s+(?:{_COAUTHORS}|(?:y|e|and|&)\s+{_AUTHOR}))?"
_YEAR = r"(?P<year>(?:19|20)\d{2})[a-z]?"
# En lugar del año: «(García, en prensa)», «García (com. pers.)», «(Smith, unpublished data)».
_STATUS = (r"(?P<status>(?i:en\s+prensa|in\s+press|com\.\s?pers\.?|comunicaci[oó]n\s+personal|pers\.\s?comm\.?|"
           r"personal\s+communication|datos\s+no\s+publicados|datos\s+sin\s+publicar|sin\s+publicar|no\s+publicad[oa]|"
           r"unpublished(?:\s+data)?|s\.\s?f\.|n\.\s?d\.))")
_FIRST = rf"(?P<name>{_NAME}){_INITIALS}"
# Entradilla de una cita entre paréntesis («(revisado por García, 2018)», «(citado en García, 2018)», «(véase también
# García, 2018)», «(datos de García, 2018)»): vocabulario cerrado con al menos una palabra de cita, para que «(en
# España, 2019)» no se lea como una cita.
_LEAD_STRONG = (r"(?:v[eé]ase|ver|see|cf\.?|p\.\s?ej\.|e\.\s?g\.|por\s+ejemplo|for\s+example|seg[uú]n|revisad[oa]s?|"
                r"revisi[oó]n(?:es)?|reviewed|review|citad[oa]s?|cited|adaptad[oa]s?|adapted|tomad[oa]s?|taken|"
                r"modificad[oa]s?|modified|basad[oa]s?|based|datos|data|resumid[oa]s?|summari[sz]ed|"
                r"reanalizad[oa]s?|reanaly[sz]ed|descrit[oa]s?|described|propuest[oa]s?|proposed|recogid[oa]s?|"
                r"calculad[oa]s?|calculated|estimad[oa]s?|estimated|obtenid[oa]s?|obtained|extra[ií]d[oa]s?|"
                r"extracted|fuente|source|tambi[eé]n|also|metaan[aá]lisis|meta-?analysis)")
_LEAD_WEAK = r"(?:por|en|de|del|la|el|los|las|un|una|by|in|from|of|on|the|a|partir|y|and|o|or)"
_LEADIN = (rf"(?i:(?:{_LEAD_WEAK}\s+){{0,2}}{_LEAD_STRONG}[,:]?\s+"
           rf"(?:(?:{_LEAD_WEAK}|{_LEAD_STRONG})[,:]?\s+){{0,4}})")
# Verbos con los que se atribuye un resultado a un autor («García demostró en 2018», «En 2018, García observó»).
_CITE_VERBS = (r"(?:demostr\w*|mostr\w*|observ\w*|encontr\w*|describ\w*|descri\w*|propus\w*|propone\w*|public\w*|"
               r"hall[oóa]\w*|señal\w*|senal\w*|indic\w*|conclu\w*|comprob\w*|report\w*|document\w*|estudi\w*|"
               r"evalu\w*|analiz\w*|midi\w*|confirm\w*|sugiri\w*|sugier\w*|showed|found|reported|observed|"
               r"demonstrated|described|proposed|concluded)")
# El verbo entero, no en futuro ni en condicional («evaluará», «mostrarán», «indicaría», «evaluaremos»): un plan que
# dice «En 2027, Ana Pérez evaluará la dosis» no atribuye un resultado a nadie.
_CITE_VERB = rf"(?:{_CITE_VERBS})(?![^\W\d_])(?<!rá)(?<!rán)(?<!ría)(?<!rían)(?<!remos)"
# Entre los autores de una referencia y su año: «, 2018», «. 2018», «(2018)» o el punto de las iniciales («J. 2018»).
_REF_YEAR_SEP = r"(?:\s*[,.]\s*\(?|\s*\(|(?:(?<=\.)|(?<=\.\s))\s*)"
AUTHOR_YEAR_RES = (
    # (Smith et al., 2019; ...), [Smith y col. 2019], (véase Smith y Jones, 2019), (Smith, Jones y Brown, 2019),
    # (García Pérez, 2018), (de Vries, 2018), (revisado por García, 2018), (García, en prensa)
    re.compile(rf"[(\[]\s*{_LEADIN}?{_FIRST}{_AUTHORS}\s*,?\s+(?:{_YEAR}|{_STATUS})\s*(?=[;),:\]])"),
    # las siguientes de una lista entre paréntesis: (...; Jones, 2017); solo cuenta dentro de un paréntesis abierto
    re.compile(rf";\s*{_LEADIN}?{_FIRST}{_AUTHORS}\s*,?\s+(?:{_YEAR}|{_STATUS})\s*(?=[;),:\]])"),
    # cita narrativa: Smith (2019), Smith et al. (2019), Smith y col. (2019), Smith y Jones (2019), Smith J. (2019),
    # García y otros (2018), García (en prensa)
    re.compile(rf"(?<![\w'’\-]){_FIRST}{_AUTHORS}\s*,?\s*[(\[]\s*(?:{_YEAR}|{_STATUS})\s*"
               rf"(?:[,;:][^()\[\]\n]{{0,40}})?[)\]]"),
    # Smith et al. 2019, Smith y col., 2019 (sin paréntesis, con coautores)
    re.compile(rf"(?<![\w'’\-]){_FIRST}\s+{_COAUTHORS}\s*,?\s+{_YEAR}(?![\w/\-–])"),
    # según Smith 2019, véase Smith y Jones, 2019 (sin paréntesis, tras una fórmula de cita)
    re.compile(rf"(?i:\b(?:según|segun|véase|vease|de\s+acuerdo\s+con|conforme\s+a|cf\.?)\s+)"
               rf"{_FIRST}{_AUTHORS}\s*,?\s+(?:{_YEAR}(?![\w/\-–])|{_STATUS})"),
    # Smith et al., García y col. (sin año): sigue siendo una cita
    re.compile(rf"(?<![\w'’\-]){_FIRST}\s+(?:et\s+al\b|[ye]\s+cols?\.)"),
    # En 2018, García demostró...; García demostró en 2018...: el año y un verbo de cita junto al apellido
    re.compile(rf"(?i:\b(?:en|in)\s+(?:el\s+año\s+)?)(?:19|20)\d{{2}}\s*,?\s+{_FIRST}{_AUTHORS}\s+"
               rf"(?i:{_CITE_VERB})"),
    re.compile(rf"(?<![\w'’\-]){_FIRST}{_AUTHORS}\s+(?i:{_CITE_VERB})\s+(?i:(?:en|in)\s+(?:el\s+año\s+)?)"
               rf"(?:19|20)\d{{2}}(?![\w/\-–])"),
    # García en 2018 mostró...; un trabajo publicado (descrito, estudiado...) en 2018 por García (con un verbo de
    # cita: «adoptado en 2009 por el Parlamento Europeo» no es una cita)
    re.compile(rf"(?<![\w'’\-]){_FIRST}{_AUTHORS}\s*,?\s+(?i:en\s+)(?:19|20)\d{{2}}\s*,?\s+(?i:{_CITE_VERB})"),
    re.compile(rf"(?i:\b{_CITE_VERB}\s+en\s+)(?:19|20)\d{{2}}\s+(?i:por)\s+{_FIRST}"),
    # Referencia bibliográfica fuera de la bibliografía: una línea que empieza por un autor con sus iniciales y el año
    # («García J. 2018. Título...», «- García J, Pérez M. 2018.») o una nota al pie con autor y año («[^1]: García,
    # 2018.», «[^2]: García J, Pérez M. 2018. Título»). Entre los autores y el año va un punto, una coma o un
    # paréntesis, como en una referencia («- Cooperativa A 2020: 50 vacas» no lo es).
    re.compile(rf"^\s{{0,3}}(?:[-*+]\s+|\d+[.)]\s+)?(?P<name>{_REF_AUTHORS}|{_NAME}{_INITIALS_REQ}{_AUTHORS})"
               rf"{_REF_YEAR_SEP}(?:19|20)\d{{2}}[a-z]?\)?\s*[.,:;]", re.MULTILINE),
    re.compile(rf"^\s{{0,3}}\[\^[^\]\n]+\]:\s*(?:{_LEADIN})?(?P<name>{_REF_AUTHORS}|{_NAME}{_INITIALS}{_AUTHORS})"
               rf"{_REF_YEAR_SEP}"
               rf"(?:(?:19|20)\d{{2}}|{_STATUS})", re.MULTILINE),
    # el verbo de cita delante («Como demostraron García y Pérez en 2018») o el año sin «en» («García, 2018,
    # demostró», «García 2018 demostró»)
    re.compile(rf"(?i:\b{_CITE_VERB})\s+(?:(?i:por)\s+)?{_FIRST}{_AUTHORS}\s+(?i:en\s+)(?:19|20)\d{{2}}"
               rf"(?![\w/\-–])"),
    re.compile(rf"(?<![\w'’\-]){_FIRST}{_AUTHORS}\s*,?\s+(?:19|20)\d{{2}}\s*,?\s+(?i:{_CITE_VERB})"),
)
_IN_PARENS_ONLY = 1          # índice de AUTHOR_YEAR_RES que solo cuenta dentro de un paréntesis o corchete abierto
_PAREN_PATTERNS = (0, 1)      # entre paréntesis: «(García, 2018)», «(...; Jones, 2017)»
_FORMULA_PATTERN = 4          # tras una fórmula de cita: «según García 2018»
_VERB_PATTERNS = (6, 7, 8, 12, 13)  # con un verbo de cita junto al nombre: «En 2018, García demostró», «García
#                                      mostró en 2018», «demostraron García y Pérez en 2018», «García, 2018, demostró»
_REF_LINE_PATTERNS = (10, 11)  # líneas con forma de referencia o de nota al pie
# Patrones débiles (verbo de cita, «publicado en 2018 por», líneas con forma de referencia): en un borrador que no es
# un informe (un plan, una crítica) solo dan aviso: «En 2026, Ana Pérez evaluó la oportunidad» o «- Pérez A., 2026:
# coordinación» pueden ser notas del equipo.
_WEAK_PATTERNS = (6, 7, 8, 9, 10, 11, 12, 13)
# En una celda de tabla, la celda entera «García 2018», «Pérez et al. 2017» o «García y Pérez, 2018» es una cita si la
# columna es de estudios o fuentes; en cualquier columna, si lleva coautores, coma o paréntesis.
_CELL_CITATION_RE = re.compile(rf"^\s*{_FIRST}(?P<authors>{_AUTHORS})\s*(?P<comma>,?)\s*(?P<open>[(\[]?)\s*{_YEAR}"
                               rf"\s*[)\]]?\s*[.;]?\s*$")
_SOURCE_COLUMN_RE = re.compile(r"^(?:estudios?|referencias?|refs?\.?|fuentes?|autor(?:es)?|autoria|trabajos?|citas?|"
                               r"ensayos?|experimentos?|articulos?|publicacion(?:es)?|bibliografia|studies|study|"
                               r"references?|sources?|authors?|papers?|trials?)\b")
# Referencias escritas entre paréntesis o llaves en lugar de corchetes: «(R1)», «{R1}», «(R1, R2)», «(R1-R3)»; o sin
# corchetes tras una fórmula de cita: «según R1», «véase R2». («R²» se lee «R^2»: no es una referencia.)
PAREN_REF_RE = re.compile(r"[({]\s*R\d+(?:\s*(?:[,;&]|[-–—]|\by\b|\be\b|\band\b)\s*R?\d+)*\s*[)}]")
BARE_REF_RE = re.compile(r"(?i:\b(?:seg[uú]n|v[eé]ase|ver|cf\.?|see|referencia|ref\.)\s+)(R\d+)\b(?!\s*[=<>≈^])")
# Cita con clave de pandoc o de un gestor de referencias: «[@garcia2018]», «[véase @garcia2018, p. 3]»,
# «[-@garcia2018]», «[@garcia2018; @perez2019]».
PANDOC_CITATION_RE = re.compile(r"\[(?:[^\[\]\n@]{0,40}?\s)?-?@[\w][\w:.#$%&+?<>~/-]*[^\[\]\n]{0,80}\]")
# Cita numérica: «[1]», «[3, 4]», «[1-3]», «[1; 2]», «[1,2]» (no es una referencia [Rn]); no «[^1]» (nota),
# «[texto][1]», «[1]: url» ni «[1](url)» (enlaces), ni un índice pegado a una palabra («x[1]»). `_numeric_citations`
# descarta además los intervalos («IC 95 % [1,2; 3,4]», «[1,2-3,4]», «[2, 4] kg/d», «escala [1-5]»).
NUMERIC_CITATION_RE = re.compile(r"(?<![\]\w^])\[\s*([1-9]\d{0,2}(?:\s*(?:[,;]|[-–—])\s*[1-9]\d{0,2})*)\s*\]"
                                 r"(?![(:\[])")
# Cita numérica entre paréntesis al final de una frase («... la cetosis (1).», «(2, 3).», «(4-6).»), salvo tras una
# palabra que numera (ecuación, tabla, grupo, dieta...): `_paren_numeric_citations`. Y con «ref.»: «(ref. 3)».
PAREN_NUMERIC_RE = re.compile(r"(?<=[^\W\d_])\s?\((\d{1,3}(?:\s*(?:[,;]|[-–—])\s*\d{1,3})*)\)(?=\s*\.(?:\s|$))")
_ENUM_WORD_RE = re.compile(r"(?i)\b(?:uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|veinte|"
                           r"treinta|cuarenta|cincuenta|cien|ciento|mil|ecuaci[oó]n(?:es)?|f[oó]rmulas?|eqs?|tablas?|"
                           r"figuras?|figs?|pasos?|apartados?|"
                           r"secci[oó]n(?:es)?|puntos?|criterios?|niveles?|opci[oó]n(?:es)?|grupos?|tratamientos?|"
                           r"dietas?|fases?|semanas?|d[ií]as?|columnas?|filas?|modelos?|anexos?|notas?|hip[oó]tesis|"
                           r"escenarios?|casos?|ensayos?|experimentos?|lotes?|parcelas?|bloques?|per[ií]odos?|"
                           r"compuertas?|preguntas?|objetivos?|etapas?)\s*$")
REF_WORD_CITATION_RE = re.compile(r"(?i)[(\[]\s*(?:ref|refs|referencias?|citas?)\.?\s*\d{1,3}"
                                  r"(?:\s*(?:[,;]|[-–—]|\by\b)\s*\d{1,3})*\s*[)\]]")
# Medida de efecto sin unidad: «OR = 2,5», «SMD: 0,45», «R² = 0,91» (cuenta como una cifra con unidades).
STAT_FIGURE_RE = re.compile(r"(?<![\w^])(?:OR|RR|HR|IRR|SMD|DME|DMP|MD|DM|RD|ROM|I\^?2|R\^?2|tau\^?2)\s*[=:≈]\s*"
                            r"[-+−]?\d")
_INTERVAL_CONTEXT_RE = re.compile(r"(?i)(?:\bIC\b|\bCI\b|interval|rango|range|escala|scale|puntuaci|score|%|entre)"
                                  r"[^.;\[\]]{0,15}$")
# Número volado tras una palabra o un signo de puntuación («transición¹», «leche.²», «<sup>1</sup>»): una cita
# numérica o una nota escrita a mano; no «m²», «R²», «Ca²⁺» ni «10⁶» (palabras de menos de tres letras o cifras) ni
# una unidad al cuadrado tras un paréntesis («(kg/d)²», la de una varianza).
SUPERSCRIPT_CITATION_RE = re.compile(r"(?:(?<=[^\W\d_]{3})|(?<=[.,;:»”\"’']))"
                                     r"\^([1-9]\d{0,2}(?:[,–-]\^?[1-9]\d{0,2})*)(?![\d^])")
# Notación matemática con superíndice: «tau²», «chi²», «sigma²» (no son números volados).
_SUPERSCRIPT_EXEMPT = {"tau", "chi", "phi", "psi", "sigma", "rho", "eta", "omega", "theta", "lambda", "beta", "gamma",
                       "delta", "alpha", "kappa", "epsilon", "zeta", "iota", "upsilon", "omicron", "log", "exp"}
# Identificadores y enlaces de una fuente en el cuerpo (fuera de las entradas de la bibliografía): no se verifican.
_BODY_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>\])]+")
_BODY_PMID_RE = re.compile(r"\bPMID\s*:?\s*\d{5,9}\b|\bPMC\d{5,9}\b|pubmed(?:\.ncbi\.nlm\.nih\.gov)?/+\d{5,9}|"
                           r"\bPubMed(?:\s+ID)?\s*:?\s*\d{5,9}\b|\barXiv\s*:\s*\d{4}\.\d{4,5}|"
                           r"\bISBN(?:-1[03])?\s*:?\s*[\d\-–]{10,17}", re.IGNORECASE)
_BODY_URL_RE = re.compile(r"\bhttps?://[^\s)>\]`]+|(?<![\w/.@])www\.[^\s)>\]`]+", re.IGNORECASE)
_SCHOLARLY_URL_RE = re.compile(
    r"doi\.org|pubmed|ncbi\.nlm\.nih\.gov|europepmc|scholar\.google|semanticscholar|researchgate\.net/publication|"
    r"sciencedirect\.com/science/article|onlinelibrary\.wiley|link\.springer|tandfonline|academic\.oup|"
    r"journals\.plos|mdpi\.com/\d|frontiersin\.org/articles|nature\.com/articles|cambridge\.org/core|biorxiv|scielo|"
    r"jstor\.org|arxiv\.org|/(?:article|articles|abs|abstract|fulltext|full|doi)/", re.IGNORECASE)
_RUMINANT_RE = re.compile(
    r"vacun|bovin|vaca|ternero|ternera|novill|buey|toro|ovin|oveja|cordero|caprin|cabra|cabrit|bufal|rumiant|"
    r"ciervo|camel|llama|alpaca|cattle|\bcows?\b|dairy|beef|calf|calves|heifer|steer|\bbulls?\b|sheep|\bewes?\b|"
    r"lamb|goat|buffalo|ruminant|deer", re.IGNORECASE)
_NON_RUMINANT_RE = re.compile(
    r"porcin|cerdo|lechon|cerda|pollo|broiler|gallina|ponedora|\baves?\b|pavo|conejo|\bpez\b|peces|acuicult|salmon|"
    r"trucha|tilapia|caball|equin|perro|gato|\bpigs?\b|swine|\bsows?\b|piglet|poultry|chicken|\bhens?\b|turkey|"
    r"rabbit|\bfish|trout|horse|equine|\bdogs?\b|\bcats?\b", re.IGNORECASE)


@dataclass
class Issue:
    severity: str          # error | aviso
    message: str
    line: int | None = None
    code: str | None = None

    def __str__(self) -> str:
        where = f"línea {self.line}: " if self.line else ""
        return f"[{self.severity.upper()}] {where}{self.message}"


def issue_kind(issue: "Issue") -> str:
    """Código corto de una incidencia para la procedencia: su `code` o el principio del mensaje (hasta los dos puntos,
    un paréntesis o unas comillas), sin cifras ni referencias concretas."""
    if issue.code:
        return issue.code
    message = re.sub(r"^\s*R\d+[a-z]?\s*:\s*", "", issue.message)      # «R1: ...»: sin la referencia
    head = re.split(r"[:(«\[]", message, maxsplit=1)[0]
    head = re.sub(r"\bR\d+\b|\d+", "N", head)
    return " ".join(head.split())[:60] or "aviso"


class StampError(RuntimeError):
    """No se puede escribir el sello (el informe cambió durante la comprobación o el resultado no es coherente)."""


# ============================================================================ texto visible y estructura
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Letras cirílicas y griegas que se confunden con latinas (para que «[РUB]» o «еstado» no escapen a la comprobación).
_CONFUSABLES = str.maketrans("аеорсухіѕјԁһкԛԝАВЕКМНОРСТХІЅЈΑΒΕΗΙΚΜΝΟΡΤΧΥΖοακινρ",
                             "aeopcyxisjdhkqwABEKMHOPCTXISJABEHIKMNOPTXYZoakivp")


_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUPERSCRIPT_RUN_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+")
_SUP_TAG_RE = re.compile(r"<sup\b[^>]*>\s*([0-9+\-−,–\s]{1,16}?)\s*</sup\s*>", re.IGNORECASE)


def _visible(line: str, marks: bool = True) -> str:
    """Aproxima el texto que ve quien lee el Markdown, para comprobarlo: entidades HTML decodificadas, NFKC, sin
    caracteres de formato invisibles (anchura cero, marcas de dirección, también los escritos como entidad: «&#8203;»),
    letras cirílicas o griegas que imitan a las latinas y sin escapes con barra invertida. Los superíndices numéricos
    se leen «^n» y no se pegan a lo anterior: «m²» es «m^2» (unidad), «R²» no es la referencia «(R2)» y «transición¹»
    o «transición<sup>1</sup>» es un número volado. Con `marks=False` los superíndices quedan como en NFKC («Ca2+»):
    es el texto de las entradas de la bibliografía, que se comparan con los metadatos de la fuente."""
    if "&" in line:
        line = re.sub(r"[\r\n]", " ", html.unescape(line))
    if marks and "<" in line:
        line = _SUP_TAG_RE.sub(lambda m: "^" + re.sub(r"\s+", "", m.group(1)), line)
    if marks and _SUPERSCRIPT_RUN_RE.search(line):
        line = _SUPERSCRIPT_RUN_RE.sub(lambda m: "^" + m.group(0).translate(_SUPERSCRIPT_DIGITS), line)
    line = unicodedata.normalize("NFKC", line)
    if any(unicodedata.category(ch) == "Cf" for ch in line):
        line = "".join(ch for ch in line if unicodedata.category(ch) != "Cf")
    line = line.translate(_CONFUSABLES)
    if "\\" in line:
        line = _ESCAPE_RE.sub(r"\1", line)
    return line


def _plain(text: str) -> str:
    """Minúsculas, sin acentos y con '_' como espacio (comparación de títulos y nombres)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower().replace("_", " ")).strip()


def _code_spans(line: str) -> list[tuple[int, int]]:
    spans, i = [], 0
    while True:
        m = re.compile(r"`+").search(line, i)
        if not m:
            return spans
        close = re.compile(r"(?<!`)" + m.group(0) + r"(?!`)").search(line, m.end())
        if not close:
            i = m.end()
            continue
        spans.append((m.start(), close.end()))
        i = close.end()


def _strip_inline_comments(text: str, keep_unclosed: bool = False) -> tuple[str, bool]:
    """Quita los comentarios HTML completos de una línea (fuera de código). Devuelve (texto, queda_abierto)."""
    spans = _code_spans(text)
    out, pos = [], 0
    while True:
        start = text.find("<!--", pos)
        while start != -1 and any(a <= start < b for a, b in spans):
            start = text.find("<!--", start + 1)
        if start == -1:
            out.append(text[pos:])
            return "".join(out), False
        out.append(text[pos:start])
        end = text.find("-->", start + 2)
        if end == -1:
            if keep_unclosed:
                out.append(text[start:])
                return "".join(out), False
            return "".join(out), True
        pos = end + 3


def _opens_fence(line: str) -> str | None:
    m = FENCE_RE.match(line)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _closes_fence(line: str, fence: str) -> bool:
    m = FENCE_RE.match(line)
    return bool(m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip())


def _strip_comments(lines: list[str]) -> tuple[list[str], int | None]:
    """Quita los comentarios HTML fuera de bloques de código, conservando el número de líneas. Un comentario sin
    cerrar no se quita (su contenido se comprueba) y se devuelve su línea para informar del error."""
    out: list[str] = []
    fence = None
    in_comment = False
    opened_at = None
    for idx, line in enumerate(lines):
        if in_comment:
            end = line.find("-->")
            if end == -1:
                out.append("")
                continue
            in_comment = False
            rest, opened = _strip_inline_comments(line[end + 3:])
            out.append(rest)
            if opened:
                in_comment, opened_at = True, idx
            continue
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            out.append(line)
            continue
        opening = _opens_fence(line)
        if opening:
            fence = opening
            out.append(line)
            continue
        rest, opened = _strip_inline_comments(line)
        out.append(rest)
        if opened:
            in_comment, opened_at = True, idx
    if in_comment and opened_at is not None:
        out = out[:opened_at] + [_strip_inline_comments(line, keep_unclosed=True)[0] for line in lines[opened_at:]]
        return out, opened_at
    return out, None


@dataclass
class _Doc:
    lines: list[str]                          # texto visible, sin comentarios (mismo número de líneas)
    fence: list[bool]                         # línea dentro de un bloque de código cercado
    headings: list[tuple[int, int, str]]      # (índice, nivel, título)
    fm_start: int | None = None               # delimitador inicial del front matter
    fm_end: int | None = None                 # delimitador final (None si no se cerró)
    issues: list[Issue] = field(default_factory=list)
    fm_html_comment: int | None = None        # línea del front matter con un comentario HTML (YAML no lo admite)
    plain: list[str] = field(default_factory=list)   # texto visible sin marcar los superíndices (bibliografía)

    @property
    def fm_lines(self) -> range:
        if self.fm_start is None or self.fm_end is None:
            return range(0)
        return range(self.fm_start + 1, self.fm_end)

    def in_front_matter(self, idx: int) -> bool:
        if self.fm_start is None:
            return False
        return self.fm_start <= idx <= (self.fm_end if self.fm_end is not None else self.fm_start)

    def heading_at(self, idx: int) -> tuple[int, int, str] | None:
        return next((h for h in self.headings if h[0] == idx), None)

    def section_end(self, start: int, level: int) -> int:
        return next((i for i, lvl, _ in self.headings if i > start and lvl <= level), len(self.lines))


def _normalize_newlines(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return text[1:] if text.startswith("\ufeff") else text


def _parse(text: str) -> _Doc:
    raw = _normalize_newlines(text).split("\n")
    stripped, unclosed = _strip_comments(raw)
    lines = [_visible(line) for line in stripped]
    # Sin marcar los superíndices: las entradas de la bibliografía se comparan con los metadatos de la fuente
    # («Ca²⁺» es «Ca2+», como en Crossref y PubMed, no «Ca^2+»).
    plain = [_visible(line, marks=False) if ("<" in line or "&" in line or _SUPERSCRIPT_RUN_RE.search(line))
             else lines[k] for k, line in enumerate(stripped)]
    doc = _Doc(lines=lines, fence=[False] * len(lines), headings=[], plain=plain)
    if unclosed is not None:
        doc.issues.append(Issue("error", "Comentario HTML sin cerrar: su contenido no se ve al renderizar el "
                                         "documento (ciérralo con -->); se comprueba como texto", unclosed + 1))
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and lines[first].rstrip() == "---":
        doc.fm_start = first
        doc.fm_end = next((j for j in range(first + 1, len(lines)) if lines[j].rstrip() in ("---", "...")), None)
        if doc.fm_end is None:
            doc.issues.append(Issue("error", "El front matter (bloque --- del principio) no está cerrado", first + 1))
        # En YAML «<!--» no es un comentario: un comentario HTML dentro del front matter (o que entra o sale de él)
        # haría que esta comprobación y un lector de YAML leyeran campos distintos (p. ej. dos estados).
        last = doc.fm_end if doc.fm_end is not None else len(raw) - 1
        doc.fm_html_comment = next((j for j in range(first, last + 1) if "<!--" in raw[j] or "-->" in raw[j]), None)
    fence = None
    for i, line in enumerate(lines):
        if doc.in_front_matter(i):
            continue
        if fence is not None:
            doc.fence[i] = True
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = _opens_fence(line)
        if opening:
            fence = opening
            doc.fence[i] = True
            continue
        m = HEADING_RE.match(line)
        if m:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", m.group(2) or "").strip()
            doc.headings.append((i, len(m.group(1)), title))
    return doc


# ============================================================================ front matter y estado
_FM_KEY_RE = re.compile(r"""^(\s*)(?:-\s+)?(?:"([^"]*)"|'([^']*)'|([^\s:#'"\-?][^:]*?))[ \t]*:(?:[ \t]+(.*)|[ \t]*)$""")


def _yaml_value(rest: str | None) -> tuple[str | None, str | None]:
    """Valor escalar simple de YAML: (valor, problema). Admite texto simple, comillas y comentarios '#'."""
    rest = (rest or "").strip()
    if not rest or rest.startswith("#"):
        return None, None
    if rest[0] == "'":
        m = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", rest)
        return (m.group(1).replace("''", "'"), None) if m else (None, "comillas simples sin cerrar o con texto detrás")
    if rest[0] == '"':
        m = re.fullmatch(r'"([^"\\]*)"\s*(?:#.*)?', rest)
        return (m.group(1), None) if m else (None, "comillas dobles con escapes, sin cerrar o con texto detrás")
    if rest[0] in "[{&*!|>%@`" or rest == "-" or rest.startswith("- "):
        return None, f"valor YAML no admitido ('{rest[:12]}'): usa texto simple"
    return re.split(r"\s+#", rest, maxsplit=1)[0].strip(), None


def _fm_fields(doc: _Doc) -> tuple[dict[str, list[tuple[int, str | None, str | None]]], list[int]]:
    """{clave en minúsculas: [(línea, valor, problema)]} y las líneas que no se pudieron leer como clave: valor."""
    fields: dict[str, list[tuple[int, str | None, str | None]]] = {}
    unread: list[int] = []
    rng = list(doc.fm_lines)
    for pos, i in enumerate(rng):
        line = doc.lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _FM_KEY_RE.match(line)
        if not m:
            unread.append(i)
            continue
        indent = len(m.group(1))
        key = (m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4) or "")
        value, problem = _yaml_value(m.group(5))
        if problem is None:
            # Una línea siguiente más sangrada que no es otra clave continúa el valor (YAML lo uniría): ilegible.
            nxt = next((j for j in rng[pos + 1:] if doc.lines[j].strip() and not doc.lines[j].lstrip().startswith("#")),
                       None)
            if nxt is not None:
                nm = _FM_KEY_RE.match(doc.lines[nxt])
                nxt_indent = len(doc.lines[nxt]) - len(doc.lines[nxt].lstrip())
                if nxt_indent > indent and (nm is None or doc.lines[nxt].lstrip().startswith("-")):
                    problem = "valor en varias líneas: escríbelo en una sola línea"
        fields.setdefault(key.strip().lower(), []).append((i, value, problem))
    return fields, unread


@dataclass
class StatusInfo:
    value: str | None          # borrador | revisado | final | None (sin estado) o el valor desconocido leído
    strict: bool               # revisado o final, o estado ilegible (falla en cerrado)
    problem: str | None = None
    line: int | None = None


# «Estado: final», «**Estado:** revisado.», «- _estado_: 'final' (sellado)»; no «Estado: final de la gestación».
_BODY_STATUS_RE = re.compile(r"^\s*(?:[-+*>#]\s*|\d+[.)]\s*)*estado\s*:\s*[\"']?(revisado|final)\b"
                             r"(?!\s+(?:de|del|en|of)\b)", re.IGNORECASE)


def _status_in_body(doc: _Doc) -> StatusInfo | None:
    """Un 'estado: revisado|final' fuera del front matter: falla en cerrado (estricto y error)."""
    for i, line in enumerate(doc.lines):
        if doc.fence[i] or doc.in_front_matter(i):
            continue
        # Sin énfasis ni marcas de lista o cita: «**Estado:** final» o «- _estado_: final» también cuentan.
        m = _BODY_STATUS_RE.match(re.sub(r"[*_`]", "", line))
        if m:
            return StatusInfo(m.group(1).lower(), True, "El estado debe ir en el front matter (bloque --- al principio "
                                                        "del documento), no en el cuerpo", i + 1)
    return None


def _status_of(doc: _Doc) -> StatusInfo:
    info = _status_from_front_matter(doc)
    if info.problem:
        return info
    # Un «Estado: final» visible en el cuerpo que no coincide con el del front matter: quien lee el documento y la
    # comprobación verían estados distintos. Falla en cerrado.
    body = _status_in_body(doc) if doc.fm_start is not None else None
    if body is not None and body.value != info.value:
        strictest = next(s for s in ("final", "revisado") if s in (body.value, info.value))
        return StatusInfo(strictest, True, f"El cuerpo declara «estado: {body.value}» pero el front matter dice "
                          f"«{info.value or 'sin estado'}»: el estado va solo en el front matter", body.line)
    return info


def _status_from_front_matter(doc: _Doc) -> StatusInfo:
    if doc.fm_start is None:
        return _status_in_body(doc) or StatusInfo(None, False)
    if doc.fm_end is None:
        return StatusInfo(None, True, "Front matter sin cerrar: no se puede leer el estado", doc.fm_start + 1)
    if doc.fm_html_comment is not None:
        return StatusInfo(None, True, "Comentario HTML (<!-- -->) dentro del front matter: YAML no lo admite y los "
                                      "campos (p. ej. el estado) no se pueden leer con seguridad; usa # para comentar",
                          doc.fm_html_comment + 1)
    fields, unread = _fm_fields(doc)
    for i in unread:
        if re.search(r"estado", doc.lines[i], re.IGNORECASE):
            return StatusInfo(None, True, f"Línea del front matter ilegible con 'estado': «{doc.lines[i].strip()}»",
                              i + 1)
    for key, entries_ in fields.items():
        if key != "estado" and re.search(r"(?<![a-z0-9_])estado(?![a-z0-9_])", key):
            return StatusInfo(None, True, f"Clave del front matter ilegible con 'estado': «{key}»", entries_[0][0] + 1)
    entries = fields.get("estado", [])
    if not entries:
        return _status_in_body(doc) or StatusInfo(None, False)
    for line, _value, problem in entries:
        if problem:
            return StatusInfo(None, True, f"Estado ilegible en el front matter: {problem}", line + 1)
    values = [(line, (value or "").strip().lower()) for line, value, _ in entries]
    distinct = {v for _, v in values}
    if len(distinct) > 1:
        strictest = next((s for s in ("final", "revisado") if s in distinct), sorted(distinct)[0])
        return StatusInfo(strictest, True, "Estado duplicado en el front matter con valores distintos: "
                          + ", ".join(sorted(v or "(vacío)" for v in distinct)), values[1][0] + 1)
    line, value = values[0]
    if not value:
        return StatusInfo(None, False)
    if value not in KNOWN_STATUSES:
        return StatusInfo(value, True, f"Estado «{value}» no reconocido (usa borrador, revisado o final)", line + 1)
    return StatusInfo(value, value in STRICT_STATUSES, None, line + 1)


def status_info(text: str) -> StatusInfo:
    """Estado del documento tal como lo leen la comprobación, el sello y el hook."""
    return _status_of(_parse(text))


def report_status(text: str) -> str | None:
    """Valor del estado (compatibilidad); usa `status_info` para saber si es ilegible."""
    return status_info(text).value


def _reviewer_of(doc: _Doc) -> tuple[str | None, str | None]:
    fields, unread = _fm_fields(doc)
    for i in unread:
        if re.search(r"revisado", doc.lines[i], re.IGNORECASE):
            return None, f"Línea del front matter ilegible con 'revisado_por_humano': «{doc.lines[i].strip()}»"
    entries = fields.get("revisado_por_humano", [])
    problems = [p for _, _, p in entries if p]
    if problems:
        return None, f"revisado_por_humano ilegible: {problems[0]}"
    names = {(v or "").strip() for _, v, _ in entries if (v or "").strip().lower() not in NO_REVIEWER}
    if len(names) > 1:
        return None, "revisado_por_humano duplicado con valores distintos: " + ", ".join(sorted(names))
    return (names.pop() if names else None), None


def human_reviewer(text: str) -> str | None:
    return _reviewer_of(_parse(text))[0]


def reviewer_name_problem(name: str | None) -> str | None:
    """Motivo por el que `name` no vale como nombre de quien revisa un informe (vacío, de relleno como «pendiente»,
    «-» o «null», que la comprobación lee como «sin revisión», o sin al menos dos letras); None si vale."""
    text = " ".join((name or "").split())
    if text.lower() in NO_REVIEWER or sum(ch.isalpha() for ch in text) < 2:
        return (f"«{text}» no es el nombre de una persona (la comprobación lo leería como «sin revisión humana»): "
                "escribe el nombre de quien revisó el informe")
    return None


def front_matter_value(text: str, key: str) -> str | None:
    fields, _ = _fm_fields(_parse(text))
    values = [v for _, v, p in fields.get(key.lower(), []) if v and not p]
    return values[0] if values else None


def _yaml_scalar(value: str) -> str:
    text = " ".join(str(value).split())
    return "'" + text.replace("'", "''") + "'"


def set_front_matter_fields(text: str, updates: dict[str, str]) -> str:
    """Cambia (o añade) claves de primer nivel del front matter conservando el resto del texto."""
    newline = "\r\n" if "\r\n" in text else "\n"
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    norm = _normalize_newlines(text)
    lines = norm.split("\n")
    doc = _parse(norm)
    if doc.fm_start is None or doc.fm_end is None:
        raise ValueError("El documento no tiene front matter (bloque --- al principio) donde registrar el campo")
    fields, _ = _fm_fields(doc)
    for key, value in updates.items():
        entries = fields.get(key.lower(), [])
        if len(entries) > 1:
            raise ValueError(f"La clave '{key}' está duplicada en el front matter: corrígelo antes")
        new_line = f"{key}: {_yaml_scalar(value)}"
        if entries:
            lines[entries[0][0]] = new_line
        else:
            lines.insert(doc.fm_end, new_line)
            doc = _parse("\n".join(lines))
            fields, _ = _fm_fields(doc)
    return bom + newline.join(lines)


def review_digest(text: str) -> str:
    """Huella del contenido revisado por una persona: SHA-256 del texto normalizado (saltos de línea, espacios
    finales, NFC) con el valor de `revisado_por_humano` del front matter vaciado."""
    norm = _normalize_newlines(text)
    lines = norm.split("\n")
    doc = _parse(norm)
    fields, _ = _fm_fields(doc)
    for i, _value, _problem in fields.get("revisado_por_humano", []):
        lines[i] = "revisado_por_humano:"
    body = "\n".join(line.rstrip() for line in lines).strip("\n") + "\n"
    return sha256_text(unicodedata.normalize("NFC", body))


# ============================================================================ rutas y registro
def _path_variants(path: Path) -> list[Path]:
    path = Path(path)
    out = [Path(os.path.abspath(path))]
    try:
        out.append(path.resolve())
    except OSError:
        pass
    return out


def _root_of(variant: Path) -> Path | None:
    for candidate in variant.parents:
        if (candidate / "nutria.toml").is_file():
            return candidate
    return None


def is_report_path(path: Path | str) -> bool:
    """Un informe es un documento bajo research/informes del proyecto, también en subcarpetas. Dentro de un proyecto
    (carpeta con nutria.toml) se mira la ruta relativa a su raíz, así que una carpeta `informes` por encima del
    proyecto no convierte los planes en informes; fuera de un proyecto cuenta cualquier carpeta `informes` de la ruta
    (falla en cerrado). Se prueba con la ruta absoluta y con la resuelta (enlaces simbólicos), sin distinguir
    mayúsculas: no depende de cómo se escriba la ruta."""
    for variant in _path_variants(Path(path)):
        root = _root_of(variant)
        if root is not None:
            parts = [p.lower() for p in variant.relative_to(root).parts[:-1]]
            if parts[:2] == ["research", "informes"]:
                return True
        elif any(part.lower() == "informes" for part in variant.parts[:-1]):
            return True
    return False


def project_root(path: Path | str) -> Path | None:
    """Carpeta del proyecto (la que contiene nutria.toml) que contiene `path`, sin mirar NUTRIA_HOME."""
    for variant in _path_variants(Path(path)):
        root = _root_of(variant)
        if root is not None:
            return root
    return None


def gate_ids_for(path: Path | str | None) -> list[str]:
    """Compuertas de la rúbrica del proyecto del documento (config/rubrica.toml o la rúbrica del paquete), las mismas
    que usa la CLI; si no se puede leer, las seis compuertas por defecto (falla en cerrado)."""
    try:
        from ..scoring.engine import load_rubric
        root = project_root(path) if path is not None else None
        ids = [g.id for g in load_rubric(root / "config" / "rubrica.toml" if root else None).gates]
    except Exception:  # noqa: BLE001 - rúbrica ilegible: se exigen todas las compuertas por defecto
        return list(DEFAULT_GATES)
    return ids or list(DEFAULT_GATES)


def registry_path_for(path: Path | str) -> Path | None:
    root = project_root(path)
    if root is not None:
        rel = "research/nutria.sqlite"
        try:
            with open(root / "nutria.toml", "rb") as fh:
                rel = tomllib.load(fh).get("rutas", {}).get("base_datos", rel)
        except (OSError, tomllib.TOMLDecodeError):
            pass
        return root / rel
    for variant in _path_variants(Path(path)):
        for candidate in variant.parents:
            if candidate.name.lower() == "research":
                return candidate / "nutria.sqlite"
    return None


def open_registry_for(path: Path | str):
    """Conexión al registro del proyecto del documento, o None si no hay registro."""
    db_path = registry_path_for(path)
    if db_path is None or not db_path.is_file():
        return None
    from .. import db
    try:
        return db.connect(db_path)
    except sqlite3.Error:
        return None


def report_key(path: Path | str) -> str:
    """Identificador estable del informe en el registro: ruta relativa a la raíz del proyecto."""
    resolved = Path(path).resolve()
    root = project_root(resolved)
    if root is not None:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


# ============================================================================ bibliografía
@dataclass
class BibEntry:
    ref: str
    line: int          # 1-based
    text: str


@dataclass
class _Bib:
    start: int
    end: int
    entries: list[BibEntry]
    stray: list[int]
    entry_lines: set[int] = field(default_factory=set)     # índices de las líneas que forman las entradas


def _bibliography(doc: _Doc) -> _Bib | None:
    level2 = [(i, title) for i, lvl, title in doc.headings if lvl == 2]
    numbered = [i for i, title in level2 if re.match(r"20\.(\s|$)", title)]
    named = [i for i, title in level2 if re.match(r"(\d+\.\s*)?(bibliograf|referencias?\b)", _plain(title))]
    if numbered:
        start = numbered[-1]
    elif named:
        start = named[-1]
    else:
        return None
    end = doc.section_end(start, 2)
    entries: list[BibEntry] = []
    stray: list[int] = []
    entry_lines: set[int] = set()
    current = None
    for i in range(start + 1, end):
        line = doc.lines[i]
        if doc.fence[i] or not line.strip() or doc.heading_at(i):
            current = None
            if doc.fence[i] and ID_HINT_RE.search(line):
                stray.append(i + 1)
            continue
        text = doc.plain[i] if i < len(doc.plain) else line
        m = ENTRY_RE.match(text)
        if m:
            current = BibEntry(m.group(1), i + 1, m.group(2).strip())
            entries.append(current)
            entry_lines.add(i)
        elif current is not None:
            current.text = f"{current.text} {text.strip()}".strip()
            entry_lines.add(i)
        elif ID_HINT_RE.search(line):
            stray.append(i + 1)
    return _Bib(start, end, entries, stray, entry_lines)


def bibliography_entries(text: str) -> list[BibEntry]:
    """Todas las entradas de la bibliografía, incluidas las duplicadas."""
    bib = _bibliography(_parse(text))
    return bib.entries if bib else []


def _entry_urls(entry_text: str) -> list[str]:
    return [u.rstrip(".,;") for u in re.findall(r"https?://[^\s)>\]]+", entry_text)]


def _bibliography_ids(text: str) -> list[str]:
    ids = []
    for entry in bibliography_entries(text):
        found = extract_identifiers(entry.text)
        ids.extend(f"doi:{d}" for d in found["doi"])
        ids.extend(f"pmid:{p}" for p in found["pmid"])
        if not found["doi"] and not found["pmid"]:
            ids.extend(f"url:{u}" for u in _entry_urls(entry.text))
    return sorted(ids)


# ============================================================================ líneas, etiquetas y párrafos
def _drop_term_spans(line: str) -> str:
    """Quita las marcas de código; el código que solo contiene una etiqueta o un [Rn] (mención del término:
    «las etiquetas `[PUB]` y `[HIP]`») se ignora, el resto se comprueba como texto. El código pegado a un corchete o
    a una letra forma parte de lo que se ve («[`PUB`]» o «[P`UB`]» se leen como [PUB]); una cifra no se pega a la
    palabra anterior («valor`15 g/d`» sigue siendo una cifra con unidades)."""
    def repl(m: re.Match) -> str:
        inner = m.group(2).strip()
        before = line[m.start() - 1] if m.start() > 0 else ""
        after = line[m.end()] if m.end() < len(line) else ""
        glued_left = bool(inner) and (before == "[" or (before.isalpha() and not inner[0].isdigit()))
        glued_right = bool(inner) and (after == "]" or (after.isalpha() and not inner[-1].isdigit()))
        if TERM_SPAN_RE.match(inner) and not (glued_left or glued_right):
            return " "
        return ("" if glued_left else " ") + inner + ("" if glued_right else " ")
    return re.sub(r"(`+)(.+?)\1", repl, line)


def _strip_marks(span: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", span) if unicodedata.category(ch) != "Mn")


@dataclass
class _Line:
    text: str
    labels: list[tuple[int, str, str]]   # (posición, nombre canónico, texto original)
    refs: list[tuple[int, str]]
    tokens: list[tuple[str, str, int]] = field(default_factory=list)   # (tipo, valor, posición), en orden; las
    #                                     fichas «label» corresponden una a una, en el mismo orden, a `labels`


# Fichas de una línea, en orden: etiqueta, referencia, fin de frase (o «;», «:»), coma o palabra (normalizada después).
_TOKEN_RE = re.compile(
    r"(?P<label>\[\s*(?i:pub|der|hip|sup|desc)\s*\]|[({]\s*(?:PUB|DER|HIP|SUP|DESC)\s*[)}])"
    r"|(?P<ref>\[R\d+\])"
    r"|(?P<stop>[.!?;:](?=\s|$))"
    r"|(?P<comma>,(?=\s|$))"
    r"|(?P<word>[^\W_]+)")
# Abreviaturas tras las que un punto no cierra la frase («p. ej.», «e. g.»: una letra minúscula).
_ABBREVIATIONS = {"al", "ej", "eg", "ie", "art", "arts", "fig", "figs", "tab", "vol", "num", "aprox", "approx", "cf",
                  "vs", "dr", "dra", "sr", "sra", "pag", "pags", "cap", "p", "e"}
_LINK_RE = re.compile(r"\[([^\[\]]*)\]\([^()\s]*\)")
_BLOCK_TAG_RE = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|td|th|br|p|div|li|ul|ol|hr|h[1-6]|blockquote|pre|"
                           r"section|article|header|footer|dl|dt|dd|caption|figure|figcaption|details|summary)\b[^>]*>",
                           re.IGNORECASE)


def _word_pieces(run: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", run).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", plain)


def _scan(line: str) -> _Line:
    text = _drop_term_spans(line)
    # Las etiquetas HTML de bloque separan lo que contienen («<td>Leche</td><td>2,5 kg/d</td>», «leche:<br>2,5
    # kg/d»); el énfasis y las etiquetas en línea no deben partir una etiqueta: «[P**UB**]» o «[P<b>UB</b>]» se ven
    # como [PUB].
    if "<" in text:
        text = _BLOCK_TAG_RE.sub(" ", text)
        text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(r"[*_~]", "", text)
    # Un acento o signo combinado dentro de un corchete corto (una P seguida de un acento combinado) no oculta una
    # etiqueta ni una referencia.
    text = re.sub(r"\[[^\[\]\n]{1,12}\]", lambda m: _strip_marks(m.group(0)), text)
    # Un enlace cuenta por su texto («[texto](url)» -> «texto»); el de una referencia sigue siendo la referencia.
    text = _LINK_RE.sub(lambda m: (f"[{m.group(1)}]" if REF_RE.fullmatch(f"[{m.group(1)}]")
                                   or LABEL_RE.fullmatch(f"[{m.group(1)}]") else m.group(1)), text)
    tokens: list[tuple[str, str, int]] = []
    labels: list[tuple[int, str, str]] = []
    last_word = last_raw = prev_word = ""
    initial = False
    for m in _TOKEN_RE.finditer(text):
        kind, raw = m.lastgroup, m.group(0)
        if kind == "word":
            pieces = _word_pieces(raw)
            tokens += [("word", piece, m.start()) for piece in pieces]
            prev_word = last_word
            last_word = pieces[-1] if pieces else last_word
            last_raw = raw
            # Una inicial es una mayúscula suelta que no sigue a «/» ni a una cifra («D. Sauvant», no «15 g/d.»).
            initial = len(raw) == 1 and raw.isupper() and (m.start() == 0 or text[m.start() - 1] not in "/0123456789")
            continue
        if kind == "stop" and raw == ".":
            if last_word in _ABBREVIATIONS and (len(last_raw) > 1 or last_raw.islower()):
                continue   # «et al.», «p. ej.», «art.»: no cierran la frase
            if last_word in ("col", "cols") and prev_word in ("y", "e"):
                continue   # «Pérez y col. (2017)»
            if initial:
                # «D. Sauvant»: no corta la afirmación del Anexo A, pero sí la frase al buscar cifras sin etiqueta.
                tokens.append(("istop", raw, m.start()))
                last_word = last_raw = ""
                initial = False
                continue
        if kind == "label":
            name = re.search(r"(?i)pub|der|hip|sup|desc", raw).group(0).upper()
            labels.append((m.start(), name, raw))
            tokens.append(("label", name, m.start()))
        elif kind == "ref":
            tokens.append(("ref", raw[1:-1], m.start()))
        elif kind == "comma":
            tokens.append(("comma", raw, m.start()))
        else:
            tokens.append(("stop", raw, m.start()))
        last_word = last_raw = ""
        initial = False
    refs = [(pos, value) for kind, value, pos in tokens if kind == "ref"]
    return _Line(text, labels, refs, tokens)


def _norm_words(text: str) -> list[str]:
    """Palabras normalizadas (minúsculas, sin acentos, sin etiquetas ni [Rn], sin formato ni puntuación)."""
    return [value for kind, value, _pos in _scan(text).tokens if kind == "word"]


def _paragraphs(doc: _Doc, body: set[int], tables: set[int] | None = None) -> list[list[int]]:
    """Párrafos del cuerpo; cada encabezado, línea de código y fila de tabla (`tables`, o las líneas que empiezan
    por «|») es un párrafo de una línea."""
    paras: list[list[int]] = []
    current: list[int] = []

    def flush() -> None:
        if current:
            paras.append(current.copy())
            current.clear()

    for i, line in enumerate(doc.lines):
        stripped = line.strip()
        if i not in body or not stripped:
            flush()
            continue
        if (doc.heading_at(i) or doc.fence[i] or stripped.lstrip(">").strip().startswith("|")
                or (tables is not None and i in tables)):
            flush()
            paras.append([i])
            continue
        if LIST_ITEM_RE.match(line):
            flush()
        current.append(i)
    flush()
    return paras


def _is_table_text(line: str) -> bool:
    return line.strip().lstrip(">").strip().startswith("|")


# Fila de separación de una tabla GFM, con o sin barras en los extremos: «|---|:--:|», «--- | ---».
_DELIM_ROW_RE = re.compile(r"^\s*(?:>\s*)*(?:\|\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?|:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?)\s*$")


def _table_lines(doc: _Doc, body: set[int]) -> set[int]:
    """Líneas de tabla del cuerpo: las que empiezan por «|» y las de una tabla GFM escrita sin barras en los
    extremos («Leche (kg/d) | Control» / «--- | ---» / «30,1 | 32,4»), reconocida por su fila de separación."""
    def usable(k: int) -> bool:
        return k in body and not doc.fence[k] and bool(doc.lines[k].strip())
    out = {i for i in body if not doc.fence[i] and _is_table_text(doc.lines[i])}
    for i in range(1, len(doc.lines)):
        if usable(i) and usable(i - 1) and _DELIM_ROW_RE.match(doc.lines[i]) and "|" in doc.lines[i - 1]:
            out |= {i - 1, i}
            j = i + 1
            while j < len(doc.lines) and usable(j) and "|" in doc.lines[j]:
                out.add(j)
                j += 1
    return out


def _joined(para: list[int], scans: dict[int, _Line]) -> tuple[str, list[int], list[int]]:
    """Texto comprobable del párrafo (sus líneas unidas por saltos de línea), el desplazamiento en que empieza cada
    línea y su índice: una cifra y su unidad, o una cita, partidas entre dos líneas se buscan en el párrafo entero."""
    texts: list[str] = []
    starts: list[int] = []
    lines: list[int] = []
    pos = 0
    for j in para:
        scan = scans.get(j)
        if scan is None:
            continue
        starts.append(pos)
        lines.append(j)
        texts.append(scan.text)
        pos += len(scan.text) + 1
    return "\n".join(texts), starts, lines


def _locate(offset: int, starts: list[int], lines: list[int]) -> tuple[int, int]:
    k = max(0, bisect.bisect_right(starts, offset) - 1)
    return lines[k], offset - starts[k]


def _inside_brackets(text: str, pos: int) -> bool:
    return text.count("(", 0, pos) + text.count("[", 0, pos) > text.count(")", 0, pos) + text.count("]", 0, pos)


_UNIT_AFTER_RE = re.compile(r"\s*(?:(?:" + _WORD_UNITS_RE + r")(?![^\W\d_])|(?:" + _SYMBOL_UNITS_RE + r"))",
                            re.IGNORECASE)


_DECIMAL_INTERVAL_RE = re.compile(r"\s*\d+,\d+\s*[;\-–—]\s*\d+,\d+\s*")


def _numeric_citations(text: str):
    """Citas numéricas del texto («[1]», «[3, 4]», «[1-3]»), sin los intervalos: con decimales y un separador de
    intervalo («[1,2; 3,4]», «[1,2-3,4]»), seguidos de una unidad («[2, 4] kg/d») o tras «IC», «rango», «escala»,
    «%» o «entre»."""
    for m in NUMERIC_CITATION_RE.finditer(text):
        content = m.group(1)
        # dos cifras con decimales separadas por un intervalo («1,2; 3,4», «1,2-3,4»); una lista de enteros al estilo
        # Vancouver («[1,2,5-7]», «[2,4-6]») es una cita
        if _DECIMAL_INTERVAL_RE.fullmatch(content):
            continue
        listed = "," in content or ";" in content or re.search(r"[-–—]", content)
        if listed and (_UNIT_AFTER_RE.match(text, m.end()) or _INTERVAL_CONTEXT_RE.search(text, 0, m.start())):
            continue
        yield m.group(0)


def _paren_numeric_citations(text: str):
    """Citas numéricas entre paréntesis al final de una frase («... la cetosis (1).»), salvo tras una palabra que
    numera («ecuación (3).», «dieta (2).»)."""
    for m in PAREN_NUMERIC_RE.finditer(text):
        if not _ENUM_WORD_RE.search(text, 0, m.start()):
            yield m.group(0).strip()


def _known_identifiers(entries: list[BibEntry]) -> dict[str, str]:
    """{«doi:...» o «pmid:...»: [Rn]} de las entradas de la bibliografía (las que se verifican y cuentan en el
    sello)."""
    known: dict[str, str] = {}
    for entry in entries:
        ids = extract_identifiers(entry.text)
        for doi in ids["doi"]:
            known.setdefault(f"doi:{doi.lower()}", entry.ref)
        for pmid in ids["pmid"]:
            known.setdefault(f"pmid:{pmid}", entry.ref)
    return known


def _bibliography_ref_for(identifier: str, known: dict[str, str]) -> str | None:
    """La [Rn] de la bibliografía con ese DOI o PMID (un DOI cortado por un paréntesis, «10.1016/S0022-0302(20»,
    cuenta si es el principio del de la entrada), o None."""
    doi = re.search(r"10\.\d{4,9}/[^\s\"<>]+", identifier)
    if doi:
        value = doi.group(0).rstrip(".,;:)]").lower()
        for key, ref in known.items():
            if key.startswith("doi:") and (key[4:] == value or (len(value) >= 12 and key[4:].startswith(value))):
                return ref
        return None
    pmid = re.search(r"(?<!\d)(\d{5,9})(?!\d)", identifier)
    if pmid and re.search(r"(?i)pmid|pubmed", identifier):
        return known.get(f"pmid:{pmid.group(1)}")
    return None


def _body_identifier_issues(line: str, i: int, sev: str, known: dict[str, str] | None = None) -> list[Issue]:
    """Un DOI, un PMID o el enlace a un artículo (doi.org, PubMed, revistas) en el cuerpo cita una fuente que no se
    verifica: el sello contaría solo las de la bibliografía. Error en los informes y en revisado o final (`sev`); si
    el identificador es el de una entrada de la bibliografía (`known`), que sí se verifica, aviso: cítala con su
    [Rn]. Otro enlace, aviso (normativa, herramientas o datos: si es una fuente, va en la bibliografía)."""
    found = [m.group(0).rstrip(".,;:") for rx in (_BODY_DOI_RE, _BODY_PMID_RE) for m in rx.finditer(line)]
    spans = _code_spans(line)
    urls = [(m.group(0).rstrip(".,;:"), m.start()) for m in _BODY_URL_RE.finditer(line)]
    found += [u for u, _pos in urls if _SCHOLARLY_URL_RE.search(u) and not any(f in u for f in found)]
    # un nombre de servidor citado como código («`www.ebi.ac.uk`») no es un enlace
    others = [u for u, pos in urls if u not in found and not any(f in u for f in found)
              and not any(a <= pos < b for a, b in spans)]
    out = []
    in_bib = {f: _bibliography_ref_for(f, known or {}) for f in found}
    unknown = [f for f in found if in_bib[f] is None]
    if unknown:
        out.append(Issue(sev, f"Identificador de una fuente en el cuerpo («{unknown[0][:80]}»): no se verifica ni "
                              "cuenta en el sello; cita la fuente con [Rn] y pon el identificador en su entrada de la "
                              "bibliografía (sección 20)", i + 1))
    elif found:
        ref = in_bib[found[0]]
        out.append(Issue("aviso", f"Identificador de una fuente en el cuerpo («{found[0][:80]}»): es el de {ref} de la "
                                  f"bibliografía; cítala con [{ref}]", i + 1))
    if others:
        out.append(Issue("aviso", f"Enlace en el cuerpo («{others[0][:80]}»): no se verifica; si es una fuente, cítala "
                                  "con [Rn] y su entrada en la bibliografía", i + 1))
    return out


def _latin1_letters(text: str) -> str:
    """El texto con cada letra fuera de Latin-1 sustituida por «A» (mayúscula) o «a» (minúscula), carácter a carácter
    (las posiciones no cambian): las expresiones de las citas reconocen así «Şahin», «Čermák», «Łukaszewicz»,
    «İbrahim» u «Ōta» como apellidos sin enumerar todas las mayúsculas de Unicode."""
    if text.isascii():
        return text
    return "".join(ch if ord(ch) <= 0xFF or not ch.isalpha() else ("A" if ch.isupper() else "a") for ch in text)


def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))


def _head_word(name: str) -> str:
    """Primera palabra de un apellido sin sus partículas («de la Fuente» → «Fuente», «La EFSA» → «EFSA»); una
    partícula sola es el apellido («Lo» en «(Lo et al., 2019)»)."""
    words = re.findall(r"[^\W\d_][\w'’\-.]*", name)
    for k, word in enumerate(words):
        if k < len(words) - 1 and re.fullmatch(_PARTICLES, word.rstrip(".")):
            continue
        return word.rstrip(".")
    return ""


def _word_class(word: str) -> str | None:
    """Clase de una palabra que puede no ser un apellido (ver `_PROGRAMME_WORDS`), o None."""
    folded = _fold(word)
    if folded in _POLICY_ACRONYMS and word.isupper():
        return "sigla"
    if folded in _MONTH_WORDS:
        return "mes"
    if folded in _TEMPORAL_WORDS:
        return "tiempo"
    if folded in _PROGRAMME_WORDS:
        return "programa"
    if folded in _FUNCTION_WORD_SET:
        return "funcion"
    return None


# Lo que sigue al primer apellido y lo hace inequívoco: iniciales, otro autor o coautores.
_MORE_AUTHORS_RE = re.compile(r"\s*(?:,?\s+(?:[A-ZÀ-ÖØ-Þ]\.|[A-ZÀ-ÖØ-Þ]{2,3}\b)|,\s*(?:[^\W\d_]+\s+)?[A-ZÀ-ÖØ-Þ]"
                              r"|,?\s+(?:et\s+al|et\s+alii|[ye]\s+cols?\b|y\s+colaboradores|and\s+colleagues|"
                              r"y\s+otr[oa]s|and\s+others|(?:y|e|and|&)\s+[A-ZÀ-ÖØ-Þ]))")


# Participio, gerundio o infinitivo al final del verbo de cita («observado», «descrito», «propuesto», «analizando»,
# «evaluar»): tras un auxiliar («Han», «Son», «Era»), la palabra no es un apellido.
_NON_FINITE_RE = re.compile(r"(?:[ai]d[oa]s?|[ií]t[oa]s?|uest[oa]s?|ando|iendo|yendo|[aei]r)$", re.IGNORECASE)


def _citation_kind(n: int, m: re.Match, text: str) -> str | None:
    """Clase de una cita autor-año (`AUTHOR_YEAR_RES[n]`, encontrada en `text`): «normal», «debil» (patrones de
    `_WEAK_PATTERNS`) o «ambigua» (el apellido coincide con una palabra común: «(Han et al., 2020)», «(Bloque, 2016)»,
    «Ley et al. (2006)»); None si la palabra no puede ser un apellido en esa posición («Se observó en 2019», «(Agenda
    2030)», «(Desde 2019)», «(May 2019)», «(PAC, 2023)»)."""
    weak = n in _WEAK_PATTERNS
    if "name" not in m.re.groupindex or m.group("name") is None:
        return "debil" if weak else "normal"
    cls = _word_class(_head_word(text[m.start("name"):m.end("name")]))
    if cls is None:
        return "debil" if weak else "normal"
    single = len(m.group("name").split()) == 1 and not _MORE_AUTHORS_RE.match(text, m.end("name"))
    head = _fold(_head_word(text[m.start("name"):m.end("name")]))
    if n in _VERB_PATTERNS:
        # «Se observó en 2019», «Esto indica en 2020»: una palabra de función sola delante del verbo no es un apellido.
        # Si también es un apellido («Han», «Son», «Lo»...), solo cuando el verbo es un participio, un gerundio o un
        # infinitivo («Han observado en 2019»; «Han observó en 2019» es una cita). Con iniciales o coautores («Han y Kim
        # demostraron») o una palabra de las otras listas («Ley demostró en 2006», «Mayo observó en 2019»), es una cita.
        if single and cls in ("funcion", "tiempo"):
            verb = re.search(rf"(?i:{_CITE_VERB})", m.group(0))
            if head not in _SURNAME_LIKE or (verb and _NON_FINITE_RE.search(verb.group(0))):
                return None
        return "ambigua"
    if n in _PAREN_PATTERNS or n == _FORMULA_PATTERN:
        groups = m.re.groupindex
        status = "status" in groups and m.group("status") is not None
        lead = n in _PAREN_PATTERNS and bool(re.search(r"[^\W\d_]", text[m.start():m.start("name")]))
        year_at = m.start("year") if "year" in groups and m.group("year") is not None else m.end()
        comma = "," in text[m.end("name"):year_at]
        if single and not status and not lead:
            if cls == "sigla":
                return None
            # «(Agenda 2030)», «(Horizonte 2020)», «(Desde 2019)»: sin coma, una palabra que no puede ser un apellido;
            # un mes o una palabra que también es apellido («(Mayo 2019)», «(Plan 2016)») es una cita
            if not comma and (cls == "tiempo" or (cls == "programa" and head not in _SURNAME_LIKE)):
                return None
    return "ambigua"


def _citations(text: str) -> list[tuple[int, str]]:
    """(final, clase) de cada cita autor-año del texto (la cita cuenta en la frase donde termina: «Smith J. (2019)
    halló...»). La clase la da `_citation_kind`; las citas exentas no se devuelven."""
    scan = _latin1_letters(text)
    out: list[tuple[int, str]] = []
    for n, rx in enumerate(AUTHOR_YEAR_RES):
        pos = 0
        while pos <= len(scan):
            m = rx.search(scan, pos)
            if m is None:
                break
            if n == _IN_PARENS_ONLY and not _inside_brackets(text, m.start()):
                pos = max(m.end(), m.start() + 1)
                continue
            kind = _citation_kind(n, m, text)
            if kind is None:
                # exenta: otra cita puede empezar dentro de este tramo
                pos = m.start() + 1
                continue
            out.append((m.end() - 1, kind))
            pos = max(m.end(), m.start() + 1)
    return out


def _cell_spans(text: str) -> list[tuple[int, int]]:
    """(inicio, fin) del contenido de cada celda de una fila de tabla, en las posiciones de `text`."""
    lead = re.match(r"\s*(?:>\s*)*", text).end()
    seps = [m.start() for m in re.finditer(r"(?<!\\)\|", text) if m.start() >= lead]
    if not seps:
        return []
    edges = ([lead - 1] if seps[0] != lead else []) + seps
    if text[seps[-1] + 1:].strip():
        edges.append(len(text))
    return [(edges[k] + 1, edges[k + 1]) for k in range(len(edges) - 1)]


def _has_figure(cell: str) -> bool:
    return bool(re.search(r"\d", REF_RE.sub("", cell)))


def _unit_cell(cell: str) -> bool:
    """Celda que solo da una unidad, sin cifras: «kg/d», «% MS», «Leche (kg/d)» o una etiqueta de fila que termina con
    su unidad entre paréntesis («Leche corregida por grasa (kg/d)»). Una celda de texto que menciona una unidad
    («coste por tonelada de pienso») no lo es."""
    text = REF_RE.sub("", cell).strip()
    if not text or re.search(r"\d", text) or not _HEADER_UNIT_RE.search(text):
        return False
    tail = re.search(r"\(([^()]*)\)\s*$", text)
    return len(text.split()) <= 4 or bool(tail and _HEADER_UNIT_RE.search(tail.group(1)))


def _declares_unit(cell: str) -> bool:
    """Celda que da la unidad de las cifras de su fila: una celda de unidades (`_unit_cell`) o una con la unidad entre
    paréntesis, aunque lleve cifras («Leche, semana 4 (kg/d)», «Leche (kg/d), semana 8», «+2,5 (kg/d)»,
    «Leche (por vaca y día, kg)»)."""
    if _unit_cell(cell):
        return True
    return any(_HEADER_UNIT_RE.search(group) for group in re.findall(r"\(([^()]*)\)", REF_RE.sub("", cell)))


def _table_headers(doc: _Doc, tables: set[int]) -> set[int]:
    """Cabecera de cada tabla GFM: la fila que va seguida de la fila de separación («|---|---|»)."""
    return {i for i in tables if i + 1 in tables and not _DELIM_ROW_RE.match(doc.lines[i])
            and _DELIM_ROW_RE.match(doc.lines[i + 1])}


def _table_events(doc: _Doc, body: set[int], scans: dict[int, _Line], no_numbers: set[int],
                  tables: set[int] | None = None) -> dict[int, list[tuple[int, str]]]:
    """Cifras con unidades y citas que solo se ven por la estructura de una tabla (GFM: cabecera y fila de
    separación): un número de una columna cuya cabecera lleva la unidad («Leche (kg/d)»), o de una fila con una celda
    de unidades («Leche (kg/d) | 30,1» o «2,5 | kg/d»), y una celda «Autor Año» en una columna de estudios o fuentes
    («García 2018»; en cualquier columna, si lleva coautores, coma o paréntesis). {línea: [(posición, tipo)]}; el tipo
    de una cita es «cite:<clase>» (`_citation_kind`)."""
    out: dict[int, list[tuple[int, str]]] = {}
    n = len(doc.lines)
    i = 0

    tables = _table_lines(doc, body) if tables is None else tables

    def table_line(k: int) -> bool:
        return k in tables
    while i < n:
        if not table_line(i):
            i += 1
            continue
        block = [i]
        i += 1
        while i < n and table_line(i):
            block.append(i)
            i += 1
        if len(block) < 3:
            continue
        header = _cells(doc.lines[block[0]].strip().lstrip(">").strip())
        delim = _cells(doc.lines[block[1]].strip().lstrip(">").strip())
        if not delim or not all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in delim):
            continue
        unit_cols = {k for k, c in enumerate(header) if _HEADER_UNIT_RE.search(c)}
        source_cols = {k for k, c in enumerate(header) if _SOURCE_COLUMN_RE.match(_plain(re.sub(r"[*_`]", "", c)))}
        for row in block[2:]:
            scan = scans.get(row)
            if scan is None:
                continue
            spans = _cell_spans(scan.text)
            cells = [scan.text[a:b] for a, b in spans]
            row_unit = any(_declares_unit(c) for c in cells)
            if (row_unit and not any(_has_figure(c) for c in cells)
                    and all(_unit_cell(c) for c in cells[1:] if c.strip())):
                # fila de unidades bajo la cabecera («| | kg/d | % |» o «| Unidad | kg/d | % |»): sus columnas llevan
                # esas unidades
                unit_cols |= {k for k, c in enumerate(cells) if _unit_cell(c)}
                continue
            events = out.setdefault(row, [])
            for k, ((a, b), cell) in enumerate(zip(spans, cells)):
                if row not in no_numbers and (k in unit_cols or row_unit) and _has_figure(cell):
                    events.append((a, "unit"))
                m = _CELL_CITATION_RE.match(_latin1_letters(cell))
                if m and (k in source_cols or m.group("authors") or m.group("comma") or m.group("open")):
                    cls = _word_class(_head_word(cell[m.start("name"):m.end("name")]))
                    bare = not (m.group("authors") or m.group("comma") or m.group("open"))
                    head = _fold(_head_word(cell[m.start("name"):m.end("name")]))
                    if cls is not None and bare and (cls in ("tiempo", "sigla") or (
                            cls == "programa" and head not in _SURNAME_LIKE)):
                        continue        # «Ensayo 2019», «Ejercicio 2020» en una columna de estudios
                    events.append((max(a, b - 1), "cite:normal" if cls is None else "cite:ambigua"))
    return out


# Frases de una [PUB] que remiten a la frase anterior en lugar de enunciar la afirmación: «Así lo indica el ensayo
# [PUB] [R1].», «Esto se observó en vacas [PUB] [R1].», «Este efecto ... [PUB] [R1].». (Palabras normalizadas.)
_ANAPHORA_FIRST = {"asi", "esto", "ello", "eso", "aquello", "dicho", "dicha", "dichos", "dichas", "tal", "tales"}
_ANAPHORA_PAIRS = {("lo", "anterior"), ("lo", "cual"), ("lo", "mismo"), ("todo", "ello"), ("todo", "esto")}
# Sujeto que remite a lo anterior («Esto, según el ensayo, ...»): no es un conector aunque le siga una coma.
_SUBJECT_HEADS = {("esto",), ("ello",), ("eso",), ("aquello",)} | _ANAPHORA_PAIRS
# Conectores de inicio de frase («Así, ...», «Por ello ...», «Dicho esto, ...», «Esto es, ...», «Tal como se
# esperaba, ...»): no remiten por sí solos; se juzga lo que sigue.
_CONNECTOR_PHRASES = sorted({
    ("asi",), ("asi", "pues"), ("asi", "como"), ("asi", "que"), ("aun", "asi"), ("por", "ello"), ("por", "eso"),
    ("por", "tanto"), ("por", "lo", "tanto"), ("por", "consiguiente"), ("por", "ende"), ("en", "consecuencia"),
    ("de", "hecho"), ("en", "efecto"), ("dicho", "esto"), ("dicho", "lo", "anterior"), ("dicho", "de", "otro", "modo"),
    ("dicho", "de", "otra", "forma"), ("esto", "es"), ("es", "decir"), ("o", "sea"), ("en", "otras", "palabras"),
    ("tal", "y", "como", "se", "esperaba"), ("tal", "como", "se", "esperaba"), ("tal", "y", "como"), ("tal", "como"),
    ("como", "se", "esperaba"), ("como", "era", "de", "esperar"), ("segun", "esto"), ("segun", "ello"), ("ademas",),
    ("asimismo",), ("igualmente",), ("sin", "embargo"), ("no", "obstante"), ("en", "cambio"),
    ("por", "el", "contrario"), ("por", "otra", "parte"), ("en", "resumen"), ("en", "conclusion"), ("en", "general"),
    ("en", "particular"), ("finalmente",), ("por", "ultimo"), ("de", "este", "modo"), ("de", "esta", "forma"),
    ("de", "esta", "manera"), ("de", "ahi", "que"), ("pues",), ("entonces",), ("con", "todo"), ("en", "suma"),
    ("en", "definitiva")}, key=len, reverse=True)
_CONNECTOR_WORDS = {w for phrase in _CONNECTOR_PHRASES for w in phrase} | {"esperar", "esperaba", "previsto"}
# La fuente como sujeto de la remisión («Un ensayo con 60 vacas así lo indica»).
_SOURCE_NOUNS = {"ensayo", "ensayos", "estudio", "estudios", "trabajo", "trabajos", "articulo", "articulos", "autor",
                 "autores", "fuente", "fuentes", "revision", "revisiones", "metaanalisis", "literatura", "evidencia",
                 "evidencias", "publicacion", "publicaciones", "investigacion", "investigaciones", "experimento",
                 "experimentos", "analisis", "informe", "datos", "study", "trial", "authors"}
_DETERMINERS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "otro", "otra", "otros", "otras", "su", "sus",
                "nuestro", "nuestra", "y", "e", "pero", "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
                "aquel", "aquella", "dicho", "dicha", "mismo", "misma"}
_DEMONSTRATIVES = {"este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "aquel", "aquella", "aquellos",
                   "aquellas", "dicho", "dicha", "dichos", "dichas", "tal", "tales"}
_RESULT_NOUNS = {"efecto", "efectos", "resultado", "resultados", "hallazgo", "hallazgos", "dato", "datos",
                 "respuesta", "respuestas", "aumento", "aumentos", "incremento", "incrementos", "reduccion",
                 "reducciones", "descenso", "descensos", "mejora", "mejoras", "cambio", "cambios", "beneficio",
                 "beneficios", "observacion", "observaciones", "conclusion", "conclusiones", "relacion",
                 "asociacion", "tendencia", "patron", "fenomeno", "afirmacion", "punto", "extremo"}
_ATTRIBUTION_VERBS = {
    "indica", "indican", "indico", "indicaron", "muestra", "muestran", "mostro", "mostraron", "confirma", "confirman",
    "confirmo", "confirmaron", "demuestra", "demuestran", "demostro", "demostraron", "respalda", "respaldan",
    "respaldo", "sostiene", "sostienen", "senala", "senalan", "senalo", "senalaron", "describe", "describen",
    "describio", "describieron", "observa", "observan", "observo", "observaron", "documenta", "documentan",
    "documento", "documentaron", "encontro", "encontraron", "establece", "establecen", "establecio", "corrobora",
    "corroboran", "corroboro", "apoya", "apoyan", "apoyo", "sugiere", "sugieren", "sugirio", "avala", "avalan",
    "recoge", "recogen", "recogio", "comprobo", "comprobaron", "comprueba", "prueba", "prueban", "probo", "probaron",
    "refrenda", "atestigua", "constata", "constato", "afirma", "afirman", "afirmo", "concluye", "concluyen",
    "concluyo", "publica", "publico", "publicaron", "reporta", "reportan", "reporto", "reportaron", "registra",
    "registraron", "explica", "explican", "explico", "atribuye", "atribuyen", "propone", "proponen", "propuso"}


# Palabras que no aportan contenido a una afirmación (función, remisión y atribución genérica).
_NON_CONTENT = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "le", "les", "de", "del", "al", "a", "en", "con",
    "por", "para", "sin", "sobre", "entre", "hasta", "desde", "segun", "como", "que", "y", "e", "o", "u", "ni",
    "pero", "se", "su", "sus", "es", "son", "fue", "fueron", "ha", "han", "habia", "era", "eran", "sido", "ser",
    "esta", "estan", "asi", "esto", "ello", "eso", "aquello", "tambien", "ya", "muy", "mas", "no", "si", "otro",
    "otra", "otros", "otras", "cual", "cuales", "ademas", "anterior", "anteriores", "previo", "previa",
    "previamente", "mencionado", "mencionada", "citado", "citada", "referido", "referida", "conforme", "todo",
    "toda", "todos", "todas", "ambos", "ambas", "mismo", "misma", "mismos", "mismas", "the", "of", "and", "in",
    "this", "that", "it", "is", "as", "so", "ensayo", "ensayos", "estudio", "estudios", "trabajo", "trabajos",
    "articulo", "articulos", "autor", "autores", "fuente", "fuentes", "referencia", "referencias", "revision",
    "revisiones", "metaanalisis", "literatura", "evidencia", "evidencias", "publicacion", "publicaciones",
    "investigacion", "investigaciones", "experimento", "experimentos", "analisis", "informe", "caso", "campo",
    "study", "trial", "authors",
} | _DEMONSTRATIVES | _RESULT_NOUNS | _ATTRIBUTION_VERBS


def _demonstrative_subject(words) -> bool:
    """«Este efecto...», «Y estos resultados...»: un demostrativo y un sustantivo de resultado."""
    start = 1 if words and words[0] in ("y", "e", "pero") else 0
    return len(words) > start + 1 and words[start] in _DEMONSTRATIVES and words[start + 1] in _RESULT_NOUNS


def _anaphoric(seq: list[str]) -> bool:
    """La frase (palabras normalizadas; «,» marca las comas) no enuncia una afirmación propia: remite a la anterior
    («Así lo indica el ensayo», «Esto se observó...», «Este efecto...», «... lo confirma», «Es lo que muestra el
    estudio») o no tiene más de una palabra con contenido («Según el ensayo», «Como indica el trabajo»). Un conector o
    una entradilla inicial («Así, ...», «Por ello, ...», «Dicho esto, ...», «Según el ensayo, ...») no es una remisión:
    se juzga lo que sigue («Así, la colina protegida aumentó la producción de leche» enuncia la afirmación)."""
    words = [w for w in seq if w != ","]
    if not words:
        return True
    if "," in seq:
        k = seq.index(",")
        head, rest = tuple(seq[:k]), seq[k + 1:]
        if (head and len(head) <= 6 and head not in _SUBJECT_HEADS and not _demonstrative_subject(head)
                and all(w in _NON_CONTENT or w in _CONNECTOR_WORDS for w in head) and any(w != "," for w in rest)):
            return _anaphoric(rest)
    if words[0] == "asi" and (len(words) == 1 or words[1] in ("lo", "se", "ocurre", "ocurrio", "sucede", "sucedio",
                                                              "pasa", "paso", "fue", "es", "era", "son", "fueron")):
        return True       # «Así lo indica el ensayo», «Así se observó en vacas»
    for phrase in _CONNECTOR_PHRASES:
        if tuple(words[:len(phrase)]) == phrase and len(words) > len(phrase):
            return _anaphoric(words[len(phrase):])
    return _anaphoric_core(words)


def _anaphoric_core(words: list[str]) -> bool:
    if words[0] in _ANAPHORA_FIRST or tuple(words[:2]) in _ANAPHORA_PAIRS:
        return True
    if words[0] in ("segun", "como") and len(words) > 1 and words[1] in _DEMONSTRATIVES:
        # «Según este ensayo», «Como estos autores» remiten a la frase anterior; si sigue la afirmación («Según este
        # ensayo la colina aumentó la leche»), se juzga lo que sigue.
        rest = words[3:]
        if sum(1 for w in rest if w not in _NON_CONTENT) <= 1:
            return True
        return _anaphoric_core(rest)
    if _demonstrative_subject(words):
        return True

    def formula(k: int) -> int:
        """Longitud de la fórmula de remisión que empieza en `k` («así lo», «así se», «lo indica», «lo que muestra»)."""
        a, b = words[k], words[k + 1] if k + 1 < len(words) else ""
        c = words[k + 2] if k + 2 < len(words) else ""
        if a == "asi" and b in ("lo", "se"):
            return 2
        if a == "lo" and b in _ATTRIBUTION_VERBS:
            return 2
        if a == "lo" and b == "que" and c in _ATTRIBUTION_VERBS:
            return 3
        return 0
    # «El ensayo de campo así lo indica», «Es lo que muestra el estudio»: la fórmula de remisión, sin contenido antes
    # de ella («La colina aumenta la leche; así lo indica el ensayo» sí enuncia la afirmación).
    for k in range(len(words) - 1):
        if formula(k):
            return True
        if words[k] not in _NON_CONTENT:
            break
    # «Un ensayo con 60 vacas Holstein así lo indica»: el sujeto es la fuente y la fórmula no va seguida de una
    # afirmación («El ensayo lo confirma: la colina aumenta la leche en vacas» sí la enuncia).
    k = 0
    while k < len(words) and k < 2 and words[k] in _DETERMINERS:
        k += 1
    if k < len(words) and words[k] in _SOURCE_NOUNS:
        for n in range(k + 1, len(words) - 1):
            size = formula(n)
            if size and sum(1 for w in words[n + size:] if w not in _NON_CONTENT) <= 1:
                return True
    return sum(1 for w in words if w not in _NON_CONTENT) <= 1


# ============================================================================ Anexo A
@dataclass
class SupportRow:
    line: int            # 1-based
    refs: list[str]
    claim: str
    value: str
    raw_value: str
    reason: str


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def _support_value(raw: str) -> str:
    value = re.sub(r"[*`~]", "", _plain(raw)).strip(" .;:,")
    return re.sub(r"[\s\-]+", "_", value)


def _annex_ranges(doc: _Doc, pattern: str, after: int | None = None) -> list[tuple[int, int]]:
    """Secciones de anexo: encabezados de nivel 2 cuyo título empieza por el patrón (después de la línea `after`, si
    se indica). Un «#### Anexo A...» o un «### Anexo C» en mitad del informe no es un anexo."""
    return [(i, doc.section_end(i, 2)) for i, lvl, title in doc.headings
            if lvl == 2 and re.match(pattern, _plain(title)) and (after is None or i > after)]


@dataclass
class _Support:
    rows: list[SupportRow]
    issues: list[Issue]
    header: bool              # hay alguna cabecera con la columna Respaldo
    claim_col: bool           # ... y con la columna Afirmación
    lines: set[int]           # índices de las líneas de las tablas de respaldo (cabecera, separador y filas)
    stray: list[tuple[int, bool]] = field(default_factory=list)   # filas antes de la cabecera: (línea, negativa)
    hidden: list[tuple[int, bool]] = field(default_factory=list)  # filas en código o en una cita: (línea, negativa)
    outside: list[tuple[int, bool]] = field(default_factory=list)  # fuera del Anexo A: (línea, es una cabecera)
    refless: list[tuple[int, bool]] = field(default_factory=list)  # filas sin referencia: (línea, negativa)
    prose: list[int] = field(default_factory=list)  # prosa del Anexo A con una referencia y una valoración negativa


_NEGATIVE_SUPPORT = ("no_respalda", "no_verificable")
_NEGATIVE_PROSE_RE = re.compile(r"\bno[\s_\-]+(?:respalda|verificable)\b", re.IGNORECASE)
_PIPE_RE = re.compile(r"(?<!\\)\|")


def _header_cells(cells: list[str]) -> list[str]:
    return [_plain(re.sub(r"[*_`~]", "", c)) for c in cells]


def _support_rows(doc: _Doc) -> _Support:
    """Filas de respaldo del Anexo A: las filas de tabla que siguen, dentro de la sección, a una cabecera con la
    columna Respaldo (aunque haya líneas en blanco entre ellas: una fila no_respalda separada de la tabla también
    cuenta). Es una fila la línea que empieza por «|» o la que GFM muestra como fila de una tabla (sin «|» inicial,
    pegada a la tabla); la prosa que lleva una «|» en medio, separada de la tabla, es texto y se comprueba como el
    resto del informe. Las filas anteriores a la cabecera no cuentan como respaldo y se comprueban como texto; las que
    van en un bloque de código o en una cita («> | R1 | ... |») tampoco cuentan, y se señalan (una negativa, como
    error); la prosa del anexo con una referencia y una valoración negativa («R1 no respalda ...») también se señala.
    Fuera del «## Anexo A» (p. ej. en la sección 13, bajo «## Anexo A1» o «## Annex A») una tabla con la columna
    Respaldo, o una fila con una referencia y una valoración negativa, no cuenta como respaldo y se señala (`outside`;
    check_report solo lo trata como error en los informes y en los documentos con un Anexo A)."""
    rows: list[SupportRow] = []
    issues: list[Issue] = []
    lines: set[int] = set()
    stray: list[tuple[int, bool]] = []
    hidden: list[tuple[int, bool]] = []
    outside: list[tuple[int, bool]] = []
    refless: list[tuple[int, bool]] = []
    prose: list[int] = []
    header = claim_col = False
    annex = _annex_ranges(doc, r"anexo\s+a\b")
    in_annex = {i for start, end in annex for i in range(start, end)}
    # filas de tabla que se ven como tabla (no una mención en código: «la tabla `| Ref | ... |`»)
    rendered = _table_lines(doc, {i for i in range(len(doc.lines)) if not doc.in_front_matter(i)})
    for i, line in enumerate(doc.lines):
        if i in in_annex or i not in rendered:
            continue
        cells = _cells(line.strip().lstrip(">").strip())
        if "respaldo" in _header_cells(cells):
            outside.append((i + 1, True))
        elif (any(_support_value(c) in _NEGATIVE_SUPPORT for c in cells)
              and any(re.search(r"\bR\d+\b", c) for c in cells)):
            outside.append((i + 1, False))
    for start, end in annex:
        cols = None
        for i in range(start + 1, end):
            stripped = doc.lines[i].strip()
            quoted = stripped.startswith(">")
            if doc.fence[i] or quoted:
                inner = stripped.lstrip("> \t") if quoted else stripped
                cells = _cells(inner) if _PIPE_RE.search(inner) else []
                if cells and not all(re.fullmatch(r":?-+:?", c) or not c for c in cells):
                    hidden.append((i + 1, any(_support_value(c) in _NEGATIVE_SUPPORT for c in cells)))
                continue
            if not (stripped.startswith("|") or i in rendered):
                # Texto del anexo (se comprueba como el resto del informe): una valoración negativa escrita en prosa
                # no puede sustituir a su fila.
                if re.search(r"\bR\d+\b", stripped) and _NEGATIVE_PROSE_RE.search(stripped):
                    prose.append(i + 1)
                continue
            cells = _cells(stripped)
            if all(re.fullmatch(r":?-+:?", c) or not c for c in cells):
                if cols is not None:
                    lines.add(i)
                continue
            low = _header_cells(cells)
            if "respaldo" in low:
                cols = {"ref": next((k for k, c in enumerate(low) if c in ("ref", "refs", "referencia", "referencias",
                                                                           "rn")), 0),
                        "claim": next((k for k, c in enumerate(low) if c.startswith("afirmacion")), None),
                        "value": low.index("respaldo"),
                        "reason": next((k for k, c in enumerate(low) if c.startswith("motivo")), None)}
                header = True
                claim_col = claim_col or cols["claim"] is not None
                lines.add(i)
                continue
            if cols is None:
                stray.append((i + 1, any(_support_value(c) in _NEGATIVE_SUPPORT for c in cells)))
                continue
            lines.add(i)

            def cell(key: str) -> str:
                k = cols.get(key)
                return cells[k] if k is not None and k < len(cells) else ""
            refs = list(dict.fromkeys(re.findall(r"\bR\d+\b", cell("ref"))))
            if not refs:
                # Su texto queda fuera del cuerpo: una valoración negativa sin referencia es siempre un error; las
                # demás, error en revisado o final (check_report) y aviso en borrador.
                refless.append((i + 1, _support_value(cell("value")) in _NEGATIVE_SUPPORT))
                continue
            rows.append(SupportRow(i + 1, refs, cell("claim"), _support_value(cell("value")), cell("value"),
                                   cell("reason")))
    return _Support(rows, issues, header, claim_col, lines, stray, hidden, outside, refless, prose)


def _fragments(claim: str) -> list[list[str]] | None:
    """Fragmentos normalizados de la columna Afirmación (separados por «...» o «…»); None si alguno tiene menos de
    MIN_FRAGMENT_WORDS palabras (un fragmento de una palabra encaja en cualquier sitio y no reproduce nada)."""
    parts = re.split(r"\[\s*(?:\.\.\.|…)\s*\]|\(\s*(?:\.\.\.|…)\s*\)|\.\.\.|…", claim)
    frags = [w for w in (_norm_words(p) for p in parts) if w]
    if not frags or (len(frags) > 1 and any(len(f) < MIN_FRAGMENT_WORDS for f in frags)):
        return None
    return frags


def _word_index(words: list[str]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for k, word in enumerate(words):
        index.setdefault(word, []).append(k)
    return index


def _find(words: list[str], frag: list[str], start: int, index: dict[str, list[int]] | None = None) -> list[int]:
    """Posiciones (desde `start`) en que `frag` aparece en `words`; con `index` (palabra -> posiciones) solo se
    prueban las posiciones de su primera palabra."""
    n = len(frag)
    if not n:
        return []
    last = len(words) - n
    candidates = index.get(frag[0], ()) if index is not None else range(start, last + 1)
    return [k for k in candidates if start <= k <= last and words[k:k + n] == frag]


@dataclass
class _Claim:
    line: int              # índice de la línea con la etiqueta
    refs: list[str]        # referencias de esa etiqueta (las de su segmento de la frase)
    words: list[str]       # palabras normalizadas del párrafo
    span: set[int]         # posiciones (en `words`) de las palabras de la afirmación
    label: str = "PUB"     # etiqueta: [PUB] o, si cita una fuente, cualquier otra ([SUP] [R1], [DER] ... [R2])

    @property
    def needed(self) -> int:
        n = len(self.span)
        return min(n, max(MIN_ROW_WORDS, math.ceil(MIN_ROW_COVERAGE * n)))

    @cached_property
    def key(self) -> tuple[str, ...]:
        """Texto normalizado de la afirmación (dos afirmaciones con el mismo texto son la misma)."""
        return tuple(self.words[k] for k in sorted(self.span))

    @property
    def excerpt(self) -> str:
        text = " ".join(self.key)
        return text if len(text) <= 90 else text[:87] + "..."


# Conectores que abren el tramo de la segunda afirmación de una frase («... [PUB] [R1] y redujo la cetosis [PUB]»):
# no forman parte de lo que la fila debe reproducir.
_LEADING_CONNECTORS = {"y", "e", "o", "u", "ni", "pero", "and", "or", "but"}


def _claims(paragraph: list[int], scans: dict[int, _Line], label_refs: dict[int, list[list[str]]]
            ) -> list[_Claim]:
    """Afirmaciones con referencia de un párrafo: cada etiqueta con su [Rn] ([PUB] o cualquier otra que cite una
    fuente: «... [SUP] [R1]» no elude el Anexo A). Las palabras de cada frase (hasta «.», «!» o «?»; «;», «:» y las
    iniciales no la cierran) se reparten entre sus etiquetas, sin solaparse:
    - si la frase empieza con texto (lo habitual: «afirmación [PUB] [R1]»), cada etiqueta se queda con las palabras
      que la preceden desde el inicio de la frase o desde la etiqueta anterior (sin un conector inicial como «y»), y
      la última, también con las que la siguen hasta el final de la frase (los calificativos: «..., en vacas
      multíparas»);
    - si la frase empieza con una etiqueta («[PUB] [R1] afirmación»), cada etiqueta se queda con las palabras que la
      siguen hasta la etiqueta siguiente.
    Así «La colina aumentó la leche 5 kg/d; así [PUB] [R1]» o «Según el ensayo [R1] [PUB], la colina aumentó la leche»
    tienen por afirmación la frase entera, no «así» ni «según el ensayo». Sin palabras propias, la afirmación es la
    frase entera y, si tampoco las hay, el párrafo."""
    words: list[str] = []
    events: list[tuple[str, str, int, int]] = []        # (tipo, valor, línea, posición en `words` o índice)
    for j in paragraph:
        label_no = 0
        for kind, value, _pos in scans[j].tokens:
            if kind == "word":
                events.append(("word", value, j, len(words)))
                words.append(value)
            elif kind == "label":
                events.append(("label", value, j, label_no))
                label_no += 1
            elif kind == "ref":
                events.append(("ref", value, j, -1))
            elif kind == "stop" and value in ".!?":
                events.append(("stop", value, j, -1))
    sentences: list[list[tuple[str, str, int, int]]] = [[]]
    for ev in events:
        if ev[0] == "stop":
            sentences.append([])
        else:
            sentences[-1].append(ev)
    out: list[_Claim] = []
    for sent in sentences:
        labels = [k for k, ev in enumerate(sent) if ev[0] == "label"]
        if not labels:
            continue

        def words_between(a: int, b: int) -> list[int]:
            return [sent[x][3] for x in range(a + 1, b) if sent[x][0] == "word"]
        pre = words_between(-1, labels[0])
        gaps = [words_between(labels[n], labels[n + 1]) for n in range(len(labels) - 1)]
        post = words_between(labels[-1], len(sent))
        sentence_words = [ev[3] for ev in sent if ev[0] == "word"]
        for n, k in enumerate(labels):
            _kind, value, line, label_no = sent[k]
            refs_by_label = label_refs.get(line) or []
            refs = refs_by_label[label_no] if label_no < len(refs_by_label) else None
            if not refs:
                continue          # sin [Rn] ([PUB] sin [Rn] ya es un error): no hay respaldo que buscar
            if pre:
                own = list(pre if n == 0 else gaps[n - 1])
                while len(own) > 1 and words[own[0]] in _LEADING_CONNECTORS:
                    own.pop(0)
                if n == len(labels) - 1:
                    own += post
            else:
                own = list(gaps[n] if n < len(labels) - 1 else post)
            span = set(own) or set(sentence_words) or set(range(len(words)))
            out.append(_Claim(line, refs, words, span, value))
    return out


def _placements(frags: list[list[str]], words: list[str], index: dict[str, list[int]]) -> list[set[int]]:
    """Posiciones del párrafo que cubre cada colocación de una fila: sus fragmentos, en orden, a partir de cada
    aparición del primero. Se calculan una vez por fila y párrafo (todas las afirmaciones de un párrafo comparten sus
    palabras), no una vez por afirmación: el coste no crece con el cubo del número de afirmaciones."""
    out = []
    for first in _find(words, frags[0], 0, index):
        covered = set(range(first, first + len(frags[0])))
        pos, ok = first + len(frags[0]), True
        for frag in frags[1:]:
            hits = _find(words, frag, pos, index)
            if not hits:
                ok = False
                break
            covered.update(range(hits[0], hits[0] + len(frag)))
            pos = hits[0] + len(frag)
        if ok:
            out.append(covered)
    return out


def _row_coverage(frags: list[list[str]], claim: _Claim, placements: list[set[int]] | None = None) -> int:
    """Palabras de la afirmación que reproduce la fila: sus fragmentos deben aparecer en orden en el párrafo."""
    if placements is None:
        placements = _placements(frags, claim.words, _word_index(claim.words))
    return max((len(covered & claim.span) for covered in placements), default=0)


# ============================================================================ comprobación sin red
def _lookup_source(conn, kind: str, value: str):
    column = "doi" if kind == "doi" else "pmid"
    try:
        if column == "doi":
            return conn.execute("SELECT * FROM sources WHERE lower(doi) = ?", (value.lower(),)).fetchone()
        return conn.execute("SELECT * FROM sources WHERE pmid = ?", (str(value),)).fetchone()
    except sqlite3.Error:
        return "error"


def _registry_issues(conn, ref: str, entries: list[BibEntry], line: int, strict: bool) -> list[Issue]:
    """La fuente de una referencia citada con una etiqueta ([PUB] o cualquier otra: «... [SUP] [R1]»), por su DOI o su
    PMID (basta uno si el registro no guarda el otro), debe estar registrada, verificada y con la retractación
    comprobada; los identificadores de la entrada no pueden apuntar a fuentes distintas del registro ni contradecir los
    que este guarda."""
    out = []
    sev = "error" if strict else "aviso"
    for entry in entries:
        ids = extract_identifiers(entry.text)
        idents = [("doi", d.lower()) for d in ids["doi"]] + [("pmid", str(p)) for p in ids["pmid"]]
        if not idents:
            continue                     # referencia de solo URL: la cubre la verificación manual firmada
        label = f"{ref} ({', '.join(f'{k.upper()} {v}' for k, v in idents)})"
        found: dict[str, sqlite3.Row] = {}
        failed = False
        for kind, value in idents:
            row = _lookup_source(conn, kind, value)
            if isinstance(row, str):
                failed = True
            elif row is not None:
                found[row["id"]] = row
        if failed:
            out.append(Issue(sev, f"{label}: no se pudo consultar el registro de fuentes", line))
            continue
        if not found:
            kind, value = idents[0]
            out.append(Issue(sev, f"{label} se cita en una afirmación del informe pero no está registrada como "
                                  f"fuente verificada (`nutria fuente agregar --{kind} {value}`)", line))
            continue
        if len(found) > 1:
            out.append(Issue(sev, f"{label}: sus identificadores corresponden a fuentes distintas del registro "
                                  f"({', '.join(sorted(found))}): corrige la entrada", line))
        for row in found.values():
            for kind in ("doi", "pmid"):
                cited = {v for k, v in idents if k == kind}
                stored = str(row[kind]).lower() if row[kind] else None
                if cited and stored and stored not in cited:
                    out.append(Issue(sev, f"{label}: el registro guarda {kind.upper()} {row[kind]} para esa fuente "
                                          f"({row['id']}), no {', '.join(sorted(cited))}: corrige la entrada", line))
            if row["retracted"] == 1:
                out.append(Issue("error", f"{label} está RETRACTADA según el registro de fuentes ({row['id']})", line))
            elif not row["verified"]:
                out.append(Issue(sev, f"{label} está registrada ({row['id']}) pero sin verificar "
                                      "(`nutria verificar --pendientes`)", line))
            elif row["retracted"] is None:
                out.append(Issue(sev, f"{label}: el registro no tiene comprobada la retractación de {row['id']} "
                                      "(`nutria verificar --pendientes`)", line))
    return out


def _review_issues(text: str, doc: _Doc, status: str | None, conn, path) -> list[Issue]:
    reviewer, problem = _reviewer_of(doc)
    label = status if status in STRICT_STATUSES else "estricto"
    how = "una persona debe revisarlo y registrarlo con `nutria informe revisar <informe> --firma-humana NOMBRE`"
    if problem:
        return [Issue("error", problem, None, REVIEW_CODE)]
    if not reviewer:
        return [Issue("error", f"Estado '{label}' sin revisión humana (revisado_por_humano vacío): {how}", None,
                      REVIEW_CODE)]
    if conn is None:
        return [Issue("error", "No se puede comprobar la revisión humana sin el registro del proyecto "
                               "(research/nutria.sqlite): revisado_por_humano no basta por sí solo", None, REVIEW_CODE)]
    from .. import db
    digest = review_digest(text)
    rows = db.report_reviews(conn, digest=digest)
    if any(r["reviewer"].strip().casefold() == reviewer.casefold() for r in rows):
        return []
    if rows:
        names = ", ".join(sorted({r["reviewer"] for r in rows}))
        return [Issue("error", f"revisado_por_humano dice «{reviewer}», pero la revisión registrada para este "
                               f"contenido es de {names}", None, REVIEW_CODE)]
    prior = db.report_reviews(conn, report=report_key(path)) if path is not None else []
    if prior:
        last = prior[-1]
        return [Issue("error", f"El informe cambió después de la revisión humana de {last['reviewer']} "
                               f"({last['reviewed_at']}): esa revisión ya no vale; {how}", None, REVIEW_CODE)]
    return [Issue("error", f"revisado_por_humano «{reviewer}» no corresponde a ninguna revisión registrada de este "
                           f"contenido: {how}", None, REVIEW_CODE)]


def _critique_issues(doc: _Doc, path) -> list[Issue]:
    """En un informe revisado o final, la revisión adversarial del crítico (critico-red-team o el workflow
    red-team-auto) debe existir: el front matter la enlaza en `revision_critica` (un documento de research/revisiones
    del proyecto) y ese documento menciona el informe (su fichero o su oportunidad). No se comprueba su contenido ni la
    independencia del crítico: lo juzga la persona que revisa el informe."""
    fields, _ = _fm_fields(doc)
    value = next((v for _, v, p in fields.get("revision_critica", []) if v and not p
                  and v.strip().lower() not in NO_REVIEWER), None)
    line = (doc.fm_start + 1) if doc.fm_start is not None else None
    how = ("enlaza en el front matter la revisión adversarial del crítico (revision_critica: "
           "research/revisiones/<fichero>.md, de critico-red-team o red-team-auto)")
    if not value:
        return [Issue("error", f"Falta la revisión del crítico: {how}", line)]
    root = project_root(path) if path is not None else None
    if root is None:
        return [Issue("error", "No se puede comprobar la revisión del crítico fuera de un proyecto nutria", line)]
    target = Path(value).expanduser()
    target = target if target.is_absolute() else root / target
    try:
        rel = target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        rel = None
    if rel is None or [part.lower() for part in rel.parts[:2]] != ["research", "revisiones"] or not target.is_file():
        return [Issue("error", f"La revisión del crítico «{value}» no es un documento de research/revisiones del "
                               f"proyecto: {how}", line)]
    try:
        critique = target.read_text("utf-8", errors="replace")
    except OSError as exc:
        return [Issue("error", f"No se pudo leer la revisión del crítico «{value}» ({exc.__class__.__name__})", line)]
    opp_id = next((v for _, v, p in fields.get("oportunidad", []) if v and not p), None)
    names = {Path(path).name, Path(path).stem} | ({opp_id} if opp_id else set())
    if not any(name and name in critique for name in names):
        return [Issue("error", f"La revisión del crítico «{value}» no menciona este informe "
                               f"({', '.join(sorted(n for n in names if n))}): enlaza la revisión que le corresponde",
                      line)]
    return []


def _species(doc: _Doc) -> str | None:
    fields, _ = _fm_fields(doc)
    values = [v for _, v, p in fields.get("especie", []) if v and not p and v.lower() not in NO_REVIEWER]
    return values[0] if values else None


def is_ruminant(species: str | None) -> bool:
    """Falla en cerrado: sin especie declarada, o si no es claramente de otra especie, se trata como rumiante."""
    if not species:
        return True
    plain = _plain(species)
    if _RUMINANT_RE.search(plain):
        return True
    return not _NON_RUMINANT_RE.search(plain)


_GATE_STATE_RE = re.compile(r"\b(superada|bloqueada|no resuelta)\b")
# Texto tachado u oculto («~~no_resuelta~~ superada», «~no_resuelta~», <del>, <s>, <strike>, <span> con
# line-through, hidden, display: none, tamaño u opacidad 0 o texto blanco): no es lo que el documento declara.
_STRUCK_RE = re.compile(
    r"(?<!~)(~{1,2})(?!~)(?=\S)(.+?)(?<=\S)(?<!~)\1(?!~)"            # ~~tachado~~ y ~tachado~ (GFM)
    r"|<(del|s|strike)\b[^>]*>.*?</\3\s*>"
    r"|<(span|div|font|p|mark|small|em|i|b|u|sup|sub)\b[^>]*(?:line-through|\bhidden\b|display\s*:\s*none|"
    r"visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])|opacity\s*:\s*0(?![.\d]*[1-9])|"
    r"color\s*[:=]\s*[\"']?\s*(?:white|transparent|#fff\b|#ffffff\b))[^>]*>.*?</\4\s*>", re.IGNORECASE)
_ROUTE_PHRASE_RE = re.compile(r"\b(?:ruta|via|clasificacion)\s+(?:regulatoria|preliminar|propuesta|de autorizacion)\b")
_FALLBACK_ROUTES = ("materia_prima", "aditivo_tecnologico", "aditivo_sensorial", "aditivo_nutricional",
                    "aditivo_zootecnico", "pienso_dietetico", "fuera_de_alcance_medicamento",
                    "fuera_de_alcance_biocida", "indeterminada")


def _unstruck(line: str) -> str:
    return _STRUCK_RE.sub(" ", line)


@lru_cache(maxsize=1)
def _known_routes() -> tuple[str, ...]:
    """Rutas regulatorias del esquema de oportunidades (o, si no se puede leer, las de esta versión)."""
    try:
        from importlib import resources
        schema = json.loads(resources.files("nutria.data").joinpath("schemas/oportunidad.schema.json")
                            .read_text("utf-8"))
        routes = schema["properties"]["ruta_regulatoria_preliminar"]["enum"]
        return tuple(dict.fromkeys(list(routes) + list(_FALLBACK_ROUTES)))
    except Exception:  # noqa: BLE001 - esquema ilegible: se usan las rutas conocidas
        return _FALLBACK_ROUTES


def _declared_routes(lines: list[tuple[int, str]], routes) -> list[tuple[str, int]]:
    """Rutas que declara la sección 14: en cada bloque (líneas sin una en blanco entre ellas) que nombra la ruta,
    vía o clasificación regulatoria, la primera ruta conocida que sigue a esa mención (sin el texto tachado).
    «Ruta regulatoria preliminar: `materia_prima`; se descartó la de aditivo zootécnico» declara materia_prima.
    [(ruta en forma normalizada, línea)]."""
    plain_routes = sorted({_plain(r) for r in routes if r}, key=len, reverse=True)
    if not plain_routes:
        return []
    route_re = re.compile(r"\b(" + "|".join(re.escape(r) for r in plain_routes) + r")\b")
    blocks: list[list[tuple[int, str]]] = [[]]
    for idx, line in lines:
        if line.strip():
            blocks[-1].append((idx, line))
        elif blocks[-1]:
            blocks.append([])
    out = []
    for block in blocks:
        if not block:
            continue
        text = _plain(" ".join(_unstruck(line) for _idx, line in block))
        for phrase in _ROUTE_PHRASE_RE.finditer(text):
            m = route_re.search(text, phrase.end())
            if m:
                out.append((m.group(1), block[0][0]))
                break
    return out


def _stated_gate_states(lines: list[tuple[int, str]], gate_ids) -> list[tuple[str, str, int]]:
    """Estados de compuerta que declara la sección 14: [(compuerta, estado, línea)]. En las filas de tabla cuya
    primera celda es una compuerta, el estado es el primero que aparece después (como en la tabla que genera
    `nutria informe nuevo`); en cualquier otra línea, el primer estado que sigue a cada mención de una compuerta, antes
    de la mención siguiente («seguridad: superada; etica: bloqueada»). El texto tachado no cuenta. Las mismas líneas
    sirven para comprobar que la sección recoge el estado de cada compuerta y para contrastarlo con el registro."""
    out = []
    for idx, line in lines:
        line = _unstruck(line)
        stripped = line.strip().lstrip(">").strip()
        if stripped.startswith("|"):
            cells = [_plain(c).strip("` ") for c in _cells(stripped)]
            gate = next((g for g in gate_ids if cells and cells[0] == _plain(g)), None)
            if gate:
                m = _GATE_STATE_RE.search(" | ".join(cells[1:]))
                if m:
                    out.append((gate, m.group(1).replace(" ", "_"), idx))
                continue
        plain = _plain(line)
        mentions = sorted((gm.start(), gm.end(), gate) for gate in gate_ids
                          for gm in re.finditer(rf"\b{re.escape(_plain(gate))}\b", plain))
        for n, (_start, end, gate) in enumerate(mentions):
            stop = mentions[n + 1][0] if n + 1 < len(mentions) else len(plain)
            m = _GATE_STATE_RE.search(plain, end, stop)
            if m:
                out.append((gate, m.group(1).replace(" ", "_"), idx))
    return out


@dataclass
class _RegistryOpportunity:
    opp_id: str | None          # oportunidad declarada en el front matter
    opp: dict | None            # su registro, si existe
    problem: str | None = None  # error al leer el registro


def _registry_opportunity(conn, doc: _Doc) -> _RegistryOpportunity:
    fields, _ = _fm_fields(doc)
    opp_id = next((v for _, v, p in fields.get("oportunidad", []) if v and not p), None)
    if not opp_id:
        return _RegistryOpportunity(None, None)
    from .. import db
    try:
        return _RegistryOpportunity(opp_id, db.get_opportunity(conn, opp_id))
    except (sqlite3.Error, db.RegistryError) as exc:
        return _RegistryOpportunity(opp_id, None, str(exc))


def _context_issues(doc: _Doc, reg: _RegistryOpportunity, sev: str) -> list[Issue]:
    """La especie y la etapa del front matter son las de la oportunidad en el registro (de ellas depende, p. ej., que se
    exija el destino digestivo de los rumiantes)."""
    if reg.opp is None:
        return []
    fields, _ = _fm_fields(doc)
    out = []
    for key, column, name in (("especie", "species", "especie"), ("etapa", "production_stage", "etapa")):
        registered = reg.opp.get(column)
        declared = next((v for _, v, p in fields.get(key, []) if v and not p), None)
        if registered and _plain(declared or "") != _plain(registered):
            out.append(Issue(sev, f"La {name} del front matter ({declared or 'sin declarar'}) no es la de la "
                                  f"oportunidad {reg.opp_id} en el registro ({registered}): corrígela",
                             (doc.fm_start + 1) if doc.fm_start is not None else None))
    return out


def _gate_registry_issues(conn, doc: _Doc, reg: _RegistryOpportunity, lines: list[tuple[int, str]], gate_ids,
                          sev: str) -> list[Issue]:
    """La sección 14 no puede declarar para una compuerta un estado distinto del efectivo en el registro, y debe
    recoger la ruta regulatoria que el registro tiene para la oportunidad del front matter."""
    opp_id, opp = reg.opp_id, reg.opp
    if not opp_id:
        return [Issue(sev, "El front matter no declara la oportunidad del informe (oportunidad: O-xxxx): no se puede "
                           "contrastar el estado de las compuertas de la sección 14 con el registro",
                      (doc.fm_start + 1) if doc.fm_start is not None else None)]
    from .. import db
    if reg.problem:
        return [Issue(sev, f"No se pudo leer la oportunidad {opp_id} en el registro ({reg.problem})")]
    if opp is None:
        return [Issue(sev, f"La oportunidad {opp_id} del front matter no está en el registro: no se puede contrastar "
                           "el estado de las compuertas de la sección 14")]
    try:
        effective = db.effective_gates(conn, opp_id, list(gate_ids))
    except (sqlite3.Error, db.RegistryError) as exc:
        return [Issue(sev, f"No se pudo leer el estado de las compuertas de {opp_id} en el registro ({exc})")]
    out = []
    route = opp.get("regulatory_route")
    if route:
        declared = _declared_routes(lines, _known_routes() + (route,))
        for stated, idx in declared:
            if stated != _plain(route):
                out.append(Issue(sev, f"La sección 14 declara la ruta regulatoria «{stated.replace(' ', '_')}», pero "
                                      f"el registro tiene «{route}» para {opp_id} (cuenta la primera ruta que sigue a "
                                      "«ruta regulatoria»): actualiza la sección o la clasificación de la oportunidad",
                                 idx + 1))
        if not declared:
            out.append(Issue(sev, f"La sección 14 no recoge la ruta regulatoria que el registro tiene para {opp_id} "
                                  f"(«{route}»; escríbela tras «Ruta regulatoria»): actualiza la sección o la "
                                  "clasificación de la oportunidad", lines[0][0] + 1 if lines else None))
    for gate, stated, idx in _stated_gate_states(lines, gate_ids):
        real = effective.get(gate)
        if real and stated != real:
            out.append(Issue(sev, f"La sección 14 dice «{stated}» para la compuerta {gate}, pero su estado efectivo en "
                                  f"el registro de {opp_id} es «{real}» («superada» solo cuenta con firma humana; una "
                                  "propuesta «bloqueada» actúa de inmediato): actualiza la sección", idx + 1))
    return out


def _label_refs(plines: list[int], scans: dict[int, _Line], by_line: dict[int, list[list[str]]]
                ) -> list[tuple[int, str, list[str]]]:
    """Referencias de cada etiqueta de un párrafo: las [Rn] de su segmento (hasta la etiqueta siguiente o el final de
    la frase, aunque estén en la línea siguiente: «... [PUB]\n[R1].») y, para la primera etiqueta de la frase, también
    las que la preceden en esa frase («Según [R1] [PUB] ...»). Rellena `by_line` (línea -> referencias de cada
    etiqueta de la línea, en orden) y devuelve [(línea, etiqueta, referencias)] por etiqueta."""
    seq = [(j, kind, value) for j in plines for kind, value, _pos in scans[j].tokens
           if kind in ("label", "ref") or (kind == "stop" and value in ".!?")]
    out: list[tuple[int, str, list[str]]] = []
    sentence_start = 0
    first_in_sentence = True
    for t, (j, kind, value) in enumerate(seq):
        if kind == "stop":
            sentence_start, first_in_sentence = t + 1, True
            continue
        if kind != "label":
            continue
        refs: list[str] = []
        if first_in_sentence:
            refs = [v for _j, k, v in seq[sentence_start:t] if k == "ref"]
            first_in_sentence = False
        k = t + 1
        while k < len(seq) and seq[k][1] == "ref":      # el segmento: las [Rn] hasta la etiqueta o el punto siguiente
            refs.append(seq[k][2])
            k += 1
        entry = list(dict.fromkeys(refs))
        by_line.setdefault(j, []).append(entry)
        out.append((j, value, entry))
    return out


@dataclass
class _Sentence:
    seq: list[str]                      # palabras normalizadas y «,» (las comas, para los conectores iniciales)
    labels: list[tuple[str, int]]       # (etiqueta, línea)
    refs: int = 0                       # número de referencias [Rn]

    @property
    def words(self) -> list[str]:
        return [w for w in self.seq if w != ","]


def _sentences(para: list[int], scans: dict[int, _Line]) -> list[_Sentence]:
    """Frases de un párrafo (hasta «.», «!» o «?»)."""
    out = [_Sentence([], [])]
    for j in para:
        scan = scans.get(j)
        if scan is None:
            continue
        for kind, value, _pos in scan.tokens:
            if kind == "word":
                out[-1].seq.append(value)
            elif kind == "comma" or (kind == "stop" and value in ";:"):
                out[-1].seq.append(",")
            elif kind == "label":
                out[-1].labels.append((value, j))
            elif kind == "ref":
                out[-1].refs += 1
            elif kind == "stop" and value in ".!?":
                out.append(_Sentence([], []))
    return [s for s in out if s.words or s.labels]


def _anaphora_issues(doc: _Doc, paragraphs: list[list[int]], scans: dict[int, _Line], sev: str,
                     tables: set[int] = frozenset()) -> list[Issue]:
    """Una frase con [PUB] (o con otra etiqueta y su [Rn]) que remite a la anterior («Así lo indica el ensayo», «Esto
    se observó...», «Este efecto...», «... lo confirma») cuando la frase anterior (del mismo párrafo, o la última del
    párrafo inmediatamente anterior) no lleva etiqueta: la afirmación real quedaría sin etiqueta y el Anexo A solo
    comprobaría la frase de la etiqueta. Un conector inicial seguido de coma («Así, ...», «Por ello, ...», «Dicho
    esto, ...») no es una remisión si lo que sigue enuncia la afirmación."""
    out: list[Issue] = []
    previous: tuple[bool, list[str], int] | None = None    # (tiene etiqueta, palabras, última línea del párrafo)
    for para in paragraphs:
        first = para[0]
        if doc.heading_at(first) or doc.fence[first] or first in tables or _is_table_text(doc.lines[first]):
            previous = None
            continue
        if previous is not None and any(doc.lines[k].strip() for k in range(previous[2] + 1, first)):
            previous = None                                 # no son párrafos contiguos
        prev = previous[:2] if previous is not None else None
        for sentence in _sentences(para, scans):
            cited = [(name, line) for name, line in sentence.labels if name == "PUB" or sentence.refs]
            if cited and prev is not None and not prev[0] and _anaphoric(sentence.seq):
                excerpt = " ".join(prev[1])
                excerpt = excerpt if len(excerpt) <= 80 else excerpt[:77] + "..."
                out.append(Issue(sev, f"La afirmación [{cited[0][0]}] remite a la frase anterior («{excerpt}»), que no "
                                      "lleva etiqueta: escribe la afirmación en la frase de su etiqueta (el Anexo A "
                                      "comprueba esa frase) o etiqueta también la frase anterior", cited[0][1] + 1))
            prev = (bool(sentence.labels), sentence.words)
        previous = (prev[0], prev[1], para[-1]) if prev is not None else None
    return out


def _pending_and_empty(doc: _Doc, numbered: dict[int, list[int]], require_sections: bool
                       ) -> tuple[dict[str, int], list[tuple[int, int]]]:
    """({apartado con [POR COMPLETAR]: primera línea}, [(sección obligatoria vacía, línea)])."""
    titles = {i: (lvl, title) for i, lvl, title in doc.headings}
    current = "(antes de la primera sección)"
    pending: dict[str, int] = {}
    for i, line in enumerate(doc.lines):
        if i in titles and titles[i][0] <= 2:
            current = titles[i][1]
        if doc.in_front_matter(i):
            continue
        if PENDING_RE.search(_drop_term_spans(line)) or CODE_PENDING_RE.match(line):
            pending.setdefault(current, i + 1)
    empty = []
    if require_sections:
        for number, idxs in sorted(numbered.items()):
            if number in SECTION_TITLES and idxs:
                start = idxs[0]
                if not any(doc.lines[j].strip() for j in range(start + 1, doc.section_end(start, 2))):
                    empty.append((number, start + 1))
    return pending, empty


def _numbered_sections(doc: _Doc) -> dict[int, list[int]]:
    numbered: dict[int, list[int]] = {}
    for i, lvl, title in doc.headings:
        m = re.match(r"(\d+)\.(?:\s|$)", title)
        if lvl == 2 and m:
            numbered.setdefault(int(m.group(1)), []).append(i)
    return numbered


def check_report(text: str, require_sections: bool = True, strict: bool | None = None, *, conn=None,
                 path: Path | str | None = None, gate_ids=None) -> list[Issue]:
    """Comprueba un documento. `strict` (por defecto: estado revisado o final, o ilegible) exige respaldo por
    afirmación, revisión humana registrada, ningún marcador [POR COMPLETAR] y cifras etiquetadas. `conn` (registro
    del proyecto) activa las comprobaciones de fuentes y de revisión humana. `path` indica si es un informe."""
    doc = _parse(text)
    issues: list[Issue] = list(doc.issues)
    is_report = bool(require_sections) or (path is not None and is_report_path(path))
    st = _status_of(doc)
    if st.problem:
        issues.append(Issue("error", st.problem, st.line))
    elif is_report and st.value is None:
        issues.append(Issue("error", "El informe no declara su estado en el front matter (estado: borrador, revisado "
                                     "o final)", (doc.fm_start + 1) if doc.fm_start is not None else None))
    if strict is None:
        strict = st.strict or (is_report and st.value is None)
    sev_strict = "error" if strict else "aviso"
    # Un identificador que no se verifica (fuera de las entradas de la bibliografía) es error en los informes aunque
    # estén en borrador: el sello diría «N de N referencias verificadas» sin haberlo comprobado.
    sev_unverified = "error" if strict or is_report else "aviso"
    gate_ids = list(gate_ids) if gate_ids else gate_ids_for(path)

    def cite_severity(kind: str) -> str:
        """Una cita autor-año sin [Rn] es error en revisado o final (falla en cerrado, también si el apellido
        coincide con una palabra común). En borrador: aviso si es ambigua («(Han et al., 2020)») o, fuera de los
        informes, si es de un patrón débil (verbo de cita, línea con forma de referencia); error en lo demás."""
        if strict:
            return "error"
        if kind == "ambigua" or (kind == "debil" and not is_report):
            return "aviso"
        return "error"

    # ---- secciones numeradas
    numbered = _numbered_sections(doc)
    if require_sections:
        for number, name in SECTION_TITLES.items():
            found = numbered.get(number, [])
            if not found:
                issues.append(Issue("error", f"Falta la sección {number} ({name})"))
            elif len(found) > 1:
                issues.append(Issue("error", f"Sección {number} duplicada (líneas "
                                             f"{', '.join(str(i + 1) for i in found)})", found[1] + 1))

    # ---- bibliografía y zonas que no son cuerpo
    bib = _bibliography(doc)
    if bib is None:
        issues.append(Issue("error", "No hay sección de bibliografía (sección 20, o «## Bibliografía» o "
                                     "«## Referencias»)"))
    entries = bib.entries if bib else []
    support = _support_rows(doc)
    # Anexos de registro (B: decisiones; C: procedencia y versiones), que se generan a partir del registro: solo los
    # encabezados de nivel 2 después de la bibliografía. Sus cifras y referencias no necesitan etiqueta.
    records = _annex_ranges(doc, r"anexo\s+[bc]\b", after=bib.start if bib else len(doc.lines))
    # Fuera del cuerpo solo quedan el front matter, las entradas de la bibliografía y las tablas de respaldo del
    # Anexo A: cualquier otro texto de esas secciones se comprueba como el resto del informe.
    excluded: set[int] = {i for i, _end in _annex_ranges(doc, r"anexo\s+a\b")} | support.lines
    if doc.fm_start is not None:
        fm_range = range(doc.fm_start, (doc.fm_end if doc.fm_end is not None else doc.fm_start) + 1)
        excluded |= set(fm_range)
        for i in fm_range:
            if LABEL_RE.search(_scan(doc.lines[i]).text):
                issues.append(Issue("error", "Etiqueta de evidencia en el front matter: las afirmaciones van en el "
                                             "cuerpo del informe", i + 1))
    if bib:
        excluded |= {bib.start} | bib.entry_lines
        for entry in entries:
            if LABEL_RE.search(_scan(entry.text).text):
                issues.append(Issue("error", f"Etiqueta de evidencia dentro de la entrada {entry.ref} de la "
                                             "bibliografía: las afirmaciones van en el cuerpo, separadas de las "
                                             "referencias",
                                    entry.line))
    body = {i for i in range(len(doc.lines)) if i not in excluded}
    record_lines: set[int] = set()
    for start, end in records:
        record_lines |= set(range(start, end))
    # Exentos de la comprobación de cifras: el título (el primer encabezado de nivel 1; otro «# ...» del cuerpo se
    # comprueba) y, en la sección 1, la línea que lo repite.
    title_heading = [(i, title) for i, lvl, title in doc.headings if lvl == 1][:1]
    no_numbers = {i for i, _title in title_heading} | record_lines
    titles = {tuple(_norm_words(title)) for _i, title in title_heading}
    for i in numbered.get(1, [])[:1]:
        for j in range(i + 1, doc.section_end(i, 2)):
            if doc.lines[j].strip() and tuple(_norm_words(doc.lines[j])) in titles:
                no_numbers.add(j)

    # ---- cuerpo: etiquetas, referencias, cifras
    used: dict[str, int] = {}
    cited_lines: list[tuple[int, list[str]]] = []
    label_refs: dict[int, list[list[str]]] = {}   # línea -> referencias de cada etiqueta de la línea, en orden
    scans: dict[int, _Line] = {}
    unlabeled: list[int] = []
    unlabeled_refs: list[int] = []
    stray_entries: set[int] = set()
    tables = _table_lines(doc, body)
    table_headers = _table_headers(doc, tables)
    bib_lines = set(range(bib.start, bib.end)) if bib else set()
    known_ids = _known_identifiers(entries)
    for i in sorted(body):
        line = doc.lines[i]
        if not line.strip():
            continue
        scan = scans[i] = _scan(line)
        for _pos, ref in scan.refs:
            used.setdefault(ref, i + 1)
        for m in BAD_LABEL_RE.finditer(scan.text):
            issues.append(Issue("error", f"Etiqueta con formato no válido «{m.group(0)}»: escribe la etiqueta y cada "
                                         "referencia por separado, p. ej. [PUB] [R1]", i + 1))
        for m in GROUPED_REFS_RE.finditer(scan.text):
            issues.append(Issue("error", f"Referencias agrupadas «{m.group(0)}»: escribe cada una entre corchetes "
                                         "([R1] [R2])", i + 1))
        for m in LOWER_REF_RE.finditer(scan.text):
            issues.append(Issue("error", f"Referencia «{m.group(0)}» en minúsculas: escribe [{m.group(1).upper()}]",
                                i + 1))
        for m in PAREN_REF_RE.finditer(scan.text):
            refs = " ".join(f"[R{n}]" for n in re.findall(r"\d+", m.group(0)))
            issues.append(Issue("error", f"Referencia «{m.group(0)}» mal escrita: escribe {refs} (cada una entre "
                                         "corchetes, para que se compruebe)", i + 1))
        # «según R1» sin corchetes: error en los informes y en revisado o final; en otros borradores (una crítica o una
        # nota de verificación hablan de las referencias del informe) y en el código, aviso.
        for m in BARE_REF_RE.finditer(scan.text):
            issues.append(Issue("aviso" if doc.fence[i] else sev_unverified,
                                f"Referencia «{m.group(0)}» sin corchetes: escribe [{m.group(1)}] (para que se "
                                "compruebe con su entrada de la bibliografía)", i + 1))
        for _pos, name, raw in scan.labels:
            if raw != f"[{name}]":
                issues.append(Issue("aviso", f"Etiqueta «{raw}» con formato no canónico: cuenta como [{name}]; "
                                             f"escribe [{name}]", i + 1))
        if any(name == "DER" for _p, name, _r in scan.labels) and not re.search(
                r"c[aá]lculo|calculad|nutria |script|anexo c|ecuaci[oó]n|f[oó]rmula|modelo", scan.text, re.IGNORECASE):
            issues.append(Issue("aviso", "[DER] sin indicar el cálculo u orden usada (una referencia [Rn] no lo "
                                         "indica)", i + 1))
        if not doc.fence[i]:
            numeric = next(_numeric_citations(scan.text), None)
            word_numeric = next((m.group(0) for m in REF_WORD_CITATION_RE.finditer(scan.text)), None)
            if numeric or word_numeric:
                # Una lista o un intervalo entre corchetes («[1-5]», «[2, 4]») puede ser una escala o un rango en un
                # plan o una crítica: fuera de los informes, en borrador, aviso (como los identificadores); un número
                # suelto («[1]») y «(ref. 3)», error.
                listed = numeric is not None and not word_numeric and bool(re.search(r"[,;\-–—]", numeric))
                issues.append(Issue(sev_unverified if listed else "error",
                                    f"Cita numérica «{word_numeric or numeric}»: no se puede verificar; cita con [Rn] "
                                    "y su entrada en la bibliografía (sección 20)", i + 1))
            pandoc = PANDOC_CITATION_RE.search(scan.text)
            if pandoc:
                issues.append(Issue(sev_unverified, f"Cita con clave «{pandoc.group(0)[:60]}»: no se puede verificar; "
                                                    "cita con [Rn] y su entrada en la bibliografía (sección 20)", i + 1))
            paren = next(_paren_numeric_citations(scan.text), None)
            if paren and i not in tables and not doc.heading_at(i):
                # Con otros números entre paréntesis en la línea («leve (1), moderada (2) o grave (3).») puede ser una
                # enumeración: aviso.
                listed = len(re.findall(r"\(\s*\d{1,3}\s*\)", scan.text)) > 1
                issues.append(Issue("aviso" if listed else sev_strict,
                                    f"Posible cita numérica «{paren}» al final de la frase: si es una cita, escríbela "
                                    "como [Rn] (y su entrada en la bibliografía)", i + 1))
            for m in SUPERSCRIPT_CITATION_RE.finditer(scan.text):
                before = re.search(r"([^\W\d_]+)$", scan.text[:m.start()])
                if before and before.group(1).lower() in _SUPERSCRIPT_EXEMPT:
                    continue
                issues.append(Issue("aviso" if i in tables else sev_strict,
                                    f"Número volado «{m.group(0)}»: si es una cita, escríbela como [Rn] (y su entrada "
                                    "en la bibliografía); si es una nota, usa una nota al pie [^n]", i + 1))
                break
        m = ENTRY_RE.match(line)
        if m and ID_HINT_RE.search(m.group(2)):
            issues.append(Issue(sev_unverified, f"Entrada bibliográfica [{m.group(1)}] fuera de la bibliografía: no "
                                                "se verifica; muévela a la sección 20", i + 1))
            stray_entries.add(i)
        elif i in record_lines:
            # Anexos B y C (decisiones y procedencia): sus cifras no necesitan etiqueta, pero un DOI, un PMID o un
            # enlace a un artículo escrito ahí no se verifica: aviso, que cuenta en el sello.
            for issue in _body_identifier_issues(line, i, "aviso", known_ids):
                issue.severity = "aviso"
                issue.message = issue.message.replace("en el cuerpo", "en el Anexo B o C", 1)
                issues.append(issue)
        elif i not in bib_lines:
            issues += _body_identifier_issues(line, i, "aviso" if doc.fence[i] else sev_unverified, known_ids)
    # Cifras con unidades, citas autor-año y referencias sin etiqueta, por frase: una frase (hasta «.», «!» o «?»)
    # puede ocupar varias líneas de un párrafo, y una línea puede tener varias frases. Las cifras y las citas se buscan
    # en el texto del párrafo entero (una cifra y su unidad partidas por un salto de línea siguen siendo una cifra con
    # unidades) y, en las tablas, también por su estructura (unidad en la cabecera o en otra celda; «Autor Año» en
    # una columna de estudios). Cada frase con una cifra necesita una etiqueta o una referencia; cada frase con una
    # referencia, una etiqueta; y cada frase con una cita autor-año, su [Rn].
    paragraphs = _paragraphs(doc, body, tables)
    table_extra = _table_events(doc, body, scans, no_numbers, tables)
    uncited: list[int] = []
    cited_by_line: dict[int, list[str]] = {}
    for para in paragraphs:
        joined, starts, plines = _joined(para, scans)
        if not plines:
            continue
        for j, name, refs in _label_refs(plines, scans, label_refs):
            if name == "PUB" and not refs:
                issues.append(Issue("error", "Afirmación [PUB] sin referencia [Rn] (cada [PUB] necesita su propio [Rn] "
                                             "antes de la etiqueta siguiente, en la misma frase)", j + 1))
            cited_by_line.setdefault(j, []).extend(refs)
        events: list[tuple[int, int, str]] = []
        for j in plines:
            for kind, value, pos in scans[j].tokens:
                if kind in ("label", "ref"):
                    events.append((j, pos, kind))
                elif kind == "istop" or (kind == "stop" and value in ".!?"):
                    events.append((j, pos, "stop"))
            events += [(j, pos, kind) for pos, kind in table_extra.get(j, [])]
        in_table = plines[0] in tables
        if in_table:
            # Una fila de tabla es un registro: sus celdas forman una sola «frase» (un punto dentro de una celda no
            # separa la cita de la celda «García et al. 2017.» de la referencia de otra celda de la misma fila).
            events = [ev for ev in events if ev[2] != "stop"]
        for m in UNIT_RE.finditer(joined):
            j, pos = _locate(m.start(), starts, plines)
            # En la cabecera de una tabla, «Semana 4 (kg/d)» es el título de una columna con su unidad (las cifras de
            # esa columna se cuentan por la estructura de la tabla); en una fila de datos, «Semana 4 (kg/d)» o
            # «+2,5 (kg/d)» son cifras con unidades.
            if j in no_numbers or (j in table_headers and "(" in m.group(0)):
                continue
            events.append((j, pos, "unit"))
        for m in STAT_FIGURE_RE.finditer(joined):          # «OR = 2,5», «R² = 0,91»: una medida de efecto
            j, pos = _locate(m.start(), starts, plines)
            if j not in no_numbers:
                events.append((j, pos, "unit"))
        for end, cite_kind in _citations(joined):
            j, pos = _locate(end, starts, plines)
            events.append((j, pos, f"cite:{cite_kind}"))
        events.sort(key=lambda e: (e[0], e[1]))
        sentence: list[tuple[int, int, str]] = []
        for ev in events + [(-1, -1, "stop")]:
            if ev[2] != "stop":
                sentence.append(ev)
                continue
            kinds = {kind for _j, _p, kind in sentence}
            if "label" not in kinds:
                if "unit" in kinds and "ref" not in kinds:
                    unlabeled.append(next(j for j, _p, kind in sentence if kind == "unit") + 1)
                refs_at = [j for j, _p, kind in sentence if kind == "ref"]
                if refs_at and not all(j in record_lines or j in stray_entries for j in refs_at):
                    unlabeled_refs.append(refs_at[0] + 1)
            cites = [(j, kind[5:]) for j, _p, kind in sentence if kind.startswith("cite:")]
            if cites and "ref" not in kinds:
                sev = max((cite_severity(kind) for _j, kind in cites), key=("aviso", "error").index)
                uncited.append((cites[0][0] + 1, sev, all(kind == "ambigua" for _j, kind in cites)))
            sentence = []
    cited_lines = [(j, list(dict.fromkeys(refs))) for j, refs in sorted(cited_by_line.items()) if refs]
    worst: dict[int, tuple[str, bool]] = {}
    for line_no, sev, ambiguous in uncited:
        prev = worst.get(line_no)
        if prev is None or (sev == "error" and prev[0] != "error"):
            worst[line_no] = (sev, ambiguous)
    for line_no, (sev, ambiguous) in worst.items():
        note = (" (el nombre coincide con una palabra común; en revisado o final es error)"
                if ambiguous and sev == "aviso" else "")
        issues.append(Issue(sev, "Cita autor-año sin referencia [Rn] verificable en la misma frase: añade su [Rn] "
                                 "(y su entrada en la bibliografía) o reescribe la frase sin la cita" + note, line_no))
    for n, line_no in enumerate(unlabeled):
        if n < 10:
            issues.append(Issue(sev_strict, "Cifra con unidades sin etiqueta de evidencia ni referencia", line_no))
    if len(unlabeled) > 10:
        issues.append(Issue(sev_strict, f"{len(unlabeled)} frases con cifras con unidades sin etiqueta en total"))
    # Una afirmación atribuida a una fuente sin etiqueta eludiría el Anexo A y el registro de fuentes: si es un
    # resultado publicado, va con [PUB] (y su fila de respaldo); si no, con su etiqueta ([DER], [HIP], [SUP]...).
    for n, line_no in enumerate(unlabeled_refs):
        if n < 10:
            issues.append(Issue(sev_strict, "Referencia [Rn] sin etiqueta de evidencia: marca la afirmación con [PUB] "
                                            "(y su fila del Anexo A) o con la etiqueta que le corresponda", line_no))
    if len(unlabeled_refs) > 10:
        issues.append(Issue(sev_strict, f"{len(unlabeled_refs)} frases con referencias sin etiqueta en total"))

    # ---- bibliografía: referencias, duplicados, identificadores
    by_ref: dict[str, list[BibEntry]] = {}
    for entry in entries:
        by_ref.setdefault(entry.ref, []).append(entry)
    for ref, line_no in used.items():
        if ref not in by_ref:
            issues.append(Issue("error", f"La referencia {ref} se cita pero no está en la bibliografía", line_no))
    seen_ids: dict[str, str] = {}
    for ref, group in by_ref.items():
        if len(group) > 1:
            issues.append(Issue("error", f"{ref} está duplicada en la bibliografía (líneas "
                                         f"{', '.join(str(e.line) for e in group)}): cada [Rn] debe aparecer una vez",
                                group[1].line))
        if ref not in used:
            issues.append(Issue("aviso", f"{ref} está en la bibliografía pero no se cita", group[0].line))
        for entry in group:
            ids = extract_identifiers(entry.text)
            if not ids["doi"] and not ids["pmid"]:
                if _entry_urls(entry.text):
                    issues.append(Issue("aviso", f"{ref} solo tiene URL: necesita verificación manual firmada",
                                        entry.line))
                else:
                    issues.append(Issue("error", f"{ref} sin DOI, PMID ni URL verificable", entry.line))
            for ident in [f"doi:{d}" for d in ids["doi"]] + [f"pmid:{p}" for p in ids["pmid"]]:
                other = seen_ids.setdefault(ident, ref)
                if other != ref:
                    issues.append(Issue("aviso", f"{ref} y {other} citan el mismo trabajo ({ident})", entry.line))
    for line_no in (bib.stray if bib else []):
        issues.append(Issue(sev_unverified, "Identificador en la bibliografía fuera de una entrada [Rn]: no se "
                                            "verifica", line_no))

    # ---- Anexo A: respaldo por afirmación
    rows = support.rows
    issues += support.issues
    for line_no, negative in support.stray:
        issues.append(Issue("error" if negative else sev_strict,
                            "Fila del Anexo A antes de la cabecera | Ref | Afirmación | Respaldo | Motivo |: no cuenta "
                            "como respaldo y se comprueba como texto"
                            + ("; declara que una fuente no respalda una afirmación o no es verificable, y esa "
                               "valoración no puede quedar fuera de la tabla" if negative else ""), line_no))
    for line_no, negative in support.hidden:
        issues.append(Issue("error" if negative else sev_strict,
                            "Fila del Anexo A dentro de un bloque de código o de una cita: no cuenta como respaldo"
                            + ("; declara que una fuente no respalda una afirmación o no es verificable, y esa "
                               "valoración no puede quedar fuera de la tabla" if negative else ""), line_no))
    # Solo en los informes y en los documentos con un Anexo A: en ellos una valoración escrita fuera del anexo quedaría
    # sin leer. En otros documentos (la crítica de red-team-auto con sus veredictos «no_verificable», una nota de
    # verificación con la columna Respaldo) esas tablas son el contenido del documento.
    annex_doc = is_report or any(lvl == 2 and re.match(r"(?:anexo|annex)\s+a(?:\b|\d)", _plain(title))
                                 for _i, lvl, title in doc.headings)
    for line_no, is_header in (support.outside if annex_doc else []):
        issues.append(Issue("error", ("Tabla de respaldo (columna Respaldo) fuera del Anexo A" if is_header else
                                      "Fila con una referencia y una valoración de respaldo negativa fuera del Anexo A")
                            + ": no cuenta como respaldo y ninguna valoración puede quedar sin leer; las filas de "
                              "respaldo van en la tabla de la sección «## Anexo A»", line_no))
    for line_no, negative in support.refless:
        issues.append(Issue("error" if negative else sev_strict,
                            "Fila del Anexo A sin referencia [Rn] reconocible: no cuenta como respaldo y su texto no "
                            "se comprueba como el del cuerpo" + (" (declara no_respalda o no_verificable)" if negative
                                                              else ""), line_no))
    # Una valoración negativa escrita como texto en el anexo: error, salvo que la tabla ya tenga una fila negativa de
    # cada referencia que menciona (una nota que la explica: «R2 no verificable: sin acceso al texto completo»).
    negative_refs = {ref for r in support.rows if r.value in _NEGATIVE_SUPPORT for ref in r.refs}
    for line_no in support.prose:
        mentioned = set(re.findall(r"\bR\d+\b", doc.lines[line_no - 1]))
        if mentioned and mentioned <= negative_refs:
            issues.append(Issue("aviso", "Valoración de respaldo negativa también escrita como texto en el Anexo A: "
                                         "cuenta la fila de la tabla", line_no))
        else:
            issues.append(Issue("error", "Valoración de respaldo negativa escrita como texto en el Anexo A: va en su "
                                         "fila de la tabla (| Ref | Afirmación | Respaldo | Motivo |)", line_no))
    if support.header and not support.claim_col:
        issues.append(Issue(sev_strict, "La tabla del Anexo A no tiene la columna Afirmación: el respaldo se "
                                        "comprueba por afirmación, no por referencia"))
    for row in rows:
        if row.value not in SUPPORT_VALUES:
            issues.append(Issue("error", f"Anexo A: respaldo '{row.raw_value}' no válido para {', '.join(row.refs)} "
                                         f"(usa {', '.join(SUPPORT_VALUES)})", row.line))
    frags_of = {r.line: _fragments(r.claim) for r in rows}

    def row_sev(row: SupportRow) -> str:
        """Una fila no_respalda o no_verificable que no se puede asignar es siempre un error (nada se enmascara)."""
        return sev_strict if row.value in SUPPORT_OK else "error"
    for r in rows:
        if r.value in SUPPORT_VALUES and frags_of[r.line] is None:
            issues.append(Issue(row_sev(r), f"Fila del Anexo A ({', '.join(r.refs)}, {r.value}): la columna Afirmación "
                                            f"está vacía o tiene fragmentos de menos de {MIN_FRAGMENT_WORDS} palabras "
                                            "entre «...»: no reproduce ninguna afirmación", r.line))
    claims: list[_Claim] = []
    for para in paragraphs:
        if any(any(refs) for j in para for refs in label_refs.get(j, [])):
            claims += _claims(para, scans, label_refs)
    # Cada fila cuenta para la afirmación de su referencia que mejor reproduce (mayor fracción de sus palabras). Una
    # fila respalda o parcial que reproduce por igual afirmaciones distintas no cuenta para ninguna (una fila por
    # afirmación); una fila no_respalda o no_verificable cuenta para todas las que reproduce (nada se enmascara).
    assigned: dict[tuple[int, str], list[SupportRow]] = {}
    ambiguous: dict[tuple[int, str], list[int]] = {}
    handled: set[int] = set()
    indexes: dict[int, dict[str, list[int]]] = {}
    placed: dict[tuple[int, int], list[set[int]]] = {}

    def placements(row: SupportRow, frags: list[list[str]], claim: _Claim) -> list[set[int]]:
        key = (row.line, id(claim.words))
        if key not in placed:
            index = indexes.get(id(claim.words))
            if index is None:
                index = indexes[id(claim.words)] = _word_index(claim.words)
            placed[key] = _placements(frags, claim.words, index)
        return placed[key]
    for r in rows:
        frags = frags_of[r.line]
        if not frags or r.value not in SUPPORT_VALUES:
            continue
        for ref in r.refs:
            scored = []
            for idx, claim in enumerate(claims):
                if ref in claim.refs:
                    covered = _row_coverage(frags, claim, placements(r, frags, claim))
                    if covered >= max(1, claim.needed):
                        scored.append((Fraction(covered, max(1, len(claim.span))), idx))
            if not scored:
                continue
            handled.add(r.line)
            if r.value in SUPPORT_OK:
                best = max(score for score, _idx in scored)
                targets = [idx for score, idx in scored if score == best]
                if len({claims[idx].key for idx in targets}) > 1:
                    for idx in targets:
                        ambiguous.setdefault((idx, ref), []).append(r.line)
                    issues.append(Issue(sev_strict, f"Fila del Anexo A ({ref}, {r.value}) que reproduce por igual "
                                                    "varias afirmaciones distintas (líneas "
                                                    f"{', '.join(str(claims[idx].line + 1) for idx in targets)}): no "
                                                    "cuenta para ninguna; escribe una fila por afirmación, con su "
                                                    "texto", r.line))
                    continue
            else:
                targets = [idx for _score, idx in scored]
            for idx in targets:
                assigned.setdefault((idx, ref), []).append(r)
    for idx, claim in enumerate(claims):
        i = claim.line
        kind = "" if claim.label == "PUB" else f"[{claim.label}] "     # «la afirmación [SUP] de la línea 9»
        for ref in claim.refs:
            matching = assigned.get((idx, ref), [])
            values = {r.value for r in matching}
            where = ", ".join(str(r.line) for r in matching)
            if "no_respalda" in values:
                issues.append(Issue("error", f"{ref} NO respalda la afirmación {kind}de la línea {i + 1} (Anexo A, "
                                             f"fila {where})", i + 1))
            elif len(values) > 1:
                issues.append(Issue("error", f"Filas contradictorias del Anexo A ({where}) para {ref} y la "
                                             f"afirmación {kind}de la línea {i + 1}: {', '.join(sorted(values))}",
                                    i + 1))
            elif values == {"no_verificable"}:
                issues.append(Issue(sev_strict, f"Respaldo de {ref} no verificable: no puede sostener la "
                                                f"afirmación [{claim.label}] de la línea {i + 1}", i + 1))
            elif not values:
                extra = ""
                if ambiguous.get((idx, ref)):
                    extra = (f"; la(s) fila(s) {', '.join(str(n) for n in ambiguous[(idx, ref)])} la reproducen por "
                             "igual que a otra afirmación")
                cited = "" if claim.label == "PUB" else (
                    f"; toda afirmación que cita una fuente necesita su fila, también con [{claim.label}]")
                issues.append(Issue(sev_strict, f"Sin comprobación de respaldo de {ref} para la afirmación {kind}de la "
                                                f"línea {i + 1} («{claim.excerpt}»): el Anexo A necesita una fila de "
                                                f"{ref} cuya columna Afirmación reproduzca esa afirmación (al menos "
                                                f"{max(1, claim.needed)} de sus {len(claim.span)} palabras, en orden; "
                                                f"«...» separa fragmentos){extra}{cited}", i + 1))
    for r in rows:
        if r.line in handled or r.value not in SUPPORT_VALUES or frags_of[r.line] is None:
            continue
        issues.append(Issue(row_sev(r), f"Fila del Anexo A ({', '.join(r.refs)}, {r.value}) que no corresponde a "
                                        "ninguna afirmación actual con esa referencia: si la afirmación se "
                                        "reformuló, actualiza la fila y comprueba la nueva versión", r.line))

    # ---- [PUB] que remite a una frase anterior sin etiqueta («La colina aumenta la leche. Así lo indica el ensayo
    # [PUB] [R1].»): la afirmación quedaría sin etiqueta y la fila del Anexo A solo reproduciría «así lo indica».
    issues += _anaphora_issues(doc, paragraphs, scans, sev_strict, tables)

    # ---- registro: oportunidad del front matter y fuentes
    reg = _registry_opportunity(conn, doc) if conn is not None else None
    if reg is not None and is_report:
        issues += _context_issues(doc, reg, sev_strict)
    # `informe nuevo` crea research/informes/<O-xxxx>.md con «oportunidad: O-xxxx»: un informe que declara otra
    # oportunidad tomaría su especie, su ruta y sus compuertas.
    stem = Path(path).stem if path is not None and is_report else ""
    declared_opp = next((v for _, v, p in _fm_fields(doc)[0].get("oportunidad", []) if v and not p), None)
    if re.fullmatch(r"O-\d+", stem) and declared_opp and declared_opp.strip().upper() != stem.upper():
        issues.append(Issue(sev_strict, f"El informe {Path(path).name} declara la oportunidad {declared_opp}: el "
                                        f"front matter debe decir «oportunidad: {stem}»",
                            (doc.fm_start + 1) if doc.fm_start is not None else None))
    if conn is not None:
        first_line: dict[str, int] = {}
        for i, refs in cited_lines:
            for ref in refs:
                first_line.setdefault(ref, i + 1)
        for ref, line_no in first_line.items():
            issues += _registry_issues(conn, ref, by_ref.get(ref, []), line_no, strict)

    # ---- secciones con contenido obligatorio
    if require_sections and numbered.get(14):
        start = numbered[14][0]
        content = [doc.lines[j] for j in range(start + 1, doc.section_end(start, 2))]
        joined = _plain("\n".join(content))
        if not re.search(r"alegacion", joined):
            issues.append(Issue(sev_strict, "La sección 14 no recoge la alegación prevista (Reglamento (CE) 767/2009, "
                                            "art. 13)", start + 1))
        if not re.search(r"(ruta|via|clasificacion) (regulatoria|de autorizacion)", joined):
            issues.append(Issue(sev_strict, "La sección 14 no recoge la ruta o clasificación regulatoria", start + 1))
        numbered_content = [(j, doc.lines[j]) for j in range(start + 1, doc.section_end(start, 2))]
        stated = {gate for gate, _state, _idx in _stated_gate_states(numbered_content, gate_ids)}
        missing = [g for g in gate_ids if g not in stated]
        if missing:
            issues.append(Issue(sev_strict, "La sección 14 no recoge el estado (superada, bloqueada o no_resuelta) de "
                                            f"las compuertas: {', '.join(missing)}", start + 1))
        if conn is not None:
            issues += _gate_registry_issues(conn, doc, reg, numbered_content, gate_ids, sev_strict)
    # Rumiante si lo dice el front matter o la oportunidad del registro (o si ninguno declara la especie).
    ruminant = is_ruminant(_species(doc)) or (reg is not None and reg.opp is not None
                                              and is_ruminant(reg.opp.get("species")))
    if require_sections and numbered.get(7) and ruminant:
        start = numbered[7][0]
        content = [doc.lines[j] for j in range(start + 1, doc.section_end(start, 2))]
        labeled = [_plain(c) for c in content if _scan(c).labels]
        ok = ("destino digestivo" in _plain("\n".join(content))
              and any(re.search(r"rumin|rumen|escape|bypass|by pass|biohidrogen|protegid|proteccion", c)
                      for c in labeled)
              and any(re.search(r"intestin|absor|posrumin|duoden|digestibilidad", c) for c in labeled))
        if not ok:
            issues.append(Issue(sev_strict, "La sección 7 no trata el destino digestivo (regla 5): en rumiantes, "
                                            "degradación o escape ruminal y digestibilidad o absorción intestinal, con "
                                            "etiqueta ([PUB] [Rn], [DER] con el cálculo o [DESC] con cómo obtenerlo)",
                                start + 1))

    # ---- marcadores y secciones vacías
    pending, empty = _pending_and_empty(doc, numbered, require_sections)
    if strict:
        for title, line_no in pending.items():
            issues.append(Issue("error", f"Contenido por completar ({PENDING_MARK}) en «{title}»", line_no))
        for number, line_no in empty:
            issues.append(Issue("error", f"La sección {number} ({SECTION_TITLES[number]}) está vacía", line_no))
    elif pending or empty:
        parts = []
        if pending:
            parts.append(f"{len(pending)} apartado(s) con {PENDING_MARK}")
        if empty:
            parts.append("secciones vacías: " + ", ".join(str(n) for n, _ in empty))
        issues.append(Issue("aviso", "Informe por completar: " + "; ".join(parts)
                            + " (en revisado o final es un error)"))

    # ---- modo estricto: revisión humana y referencias
    if strict:
        issues += _review_issues(text, doc, st.value, conn, path)
        if is_report and require_sections:
            issues += _critique_issues(doc, path)
        if is_report and not entries:
            issues.append(Issue("error", f"Un informe en estado '{st.value or 'estricto'}' necesita referencias "
                                         "verificables"))
        if is_report and not _species(doc):
            issues.append(Issue("aviso", "El front matter no declara la especie: el destino digestivo se exige como en "
                                         "rumiantes"))
    return issues


def pending_summary(text: str, require_sections: bool = True) -> tuple[int, list[int]]:
    """(número de apartados con [POR COMPLETAR], secciones obligatorias vacías)."""
    doc = _parse(text)
    pending, empty = _pending_and_empty(doc, _numbered_sections(doc), require_sections)
    return len(pending), [n for n, _ in empty]


# ============================================================================ comprobación en línea
def check_report_online(client, text: str, conn=None) -> tuple[list[Issue], list[dict], dict]:
    """Verifica en línea cada entrada (también las duplicadas). Devuelve (incidencias, detalle, resumen).

    Falla en cerrado: lo que no se pueda comprobar (sin red, retractación desconocida, URL sin verificación manual
    firmada) cuenta como pendiente y deja la verificación incompleta.
    """
    issues: list[Issue] = []
    details: list[dict] = []
    summary = {"total": 0, "verificadas": 0, "pendientes": 0, "con_error": 0}
    for entry in bibliography_entries(text):
        ref, line_no = entry.ref, entry.line
        summary["total"] += 1
        ids = extract_identifiers(entry.text)
        if not ids["doi"] and not ids["pmid"]:
            # Todas las URL de la entrada deben tener su verificación manual firmada (D3-2): una entrada con una URL
            # verificada y otra sin verificar no cuenta como verificada (falla en cerrado).
            urls = list(dict.fromkeys(_entry_urls(entry.text)))
            manuals: dict[str, dict | None] = {url: None for url in urls}
            if conn is not None:
                from .. import db
                for url in urls:
                    try:
                        manuals[url] = db.get_manual_verification(conn, url)
                    except sqlite3.Error:
                        manuals[url] = None
            missing = [url for url, manual in manuals.items() if not manual]
            if urls and not missing:
                summary["verificadas"] += 1
                for manual in manuals.values():
                    details.append({"ref": ref, "identifier": manual["url"], "kind": "url",
                                    "status": "verificada_manual", "firmado_por": manual["signed_by"],
                                    "fecha": manual["signed_at"]})
            else:
                summary["pendientes"] += 1
                details.append({"ref": ref, "identifier": missing[0] if missing else None, "kind": "url",
                                "status": "pendiente_manual"})
                which = (f" ({len(missing)} de {len(urls)} URL sin verificar: {', '.join(missing[:3])})"
                         if len(urls) > 1 and missing else "")
                issues.append(Issue("aviso", f"{ref}: referencia con URL sin verificación manual firmada{which} "
                                             "(`nutria verificar manual <URL> --firma-humana NOMBRE`, solo personas; "
                                             "cada URL de la entrada necesita la suya)", line_no))
            continue
        results = verify_entry(client, entry.text)
        ref_ok = True
        for result in results:
            row = result.to_dict()
            row["ref"] = ref
            details.append(row)
            if result.status == "no_encontrada":
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.identifier} no existe ({result.message})", line_no))
            elif result.status in ("error_red", "offline"):
                ref_ok = False
                summary["pendientes"] += 1
                issues.append(Issue("aviso", f"{ref}: {result.identifier} NO verificado ({result.message})", line_no))
            elif result.status != "verificada":
                ref_ok = False
                summary["pendientes"] += 1
                # el motivo de la verificación (p. ej. existe_sin_metadatos: la agencia de registro del DOI y por qué
                # queda pendiente; petición de P3)
                why = f": {result.message}" if getattr(result, "message", None) else ""
                issues.append(Issue("aviso", f"{ref}: {result.identifier} en estado '{result.status}'{why}", line_no))
            elif result.retracted:
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.identifier} está RETRACTADO", line_no))
            elif result.mismatch:
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.message}", line_no))
            elif result.retracted is None:
                ref_ok = False
                summary["pendientes"] += 1
                issues.append(Issue("aviso", f"{ref}: no se pudo comprobar si {result.identifier} está retractado",
                                    line_no))
        if not results:
            ref_ok = False
            summary["pendientes"] += 1
        if ref_ok:
            summary["verificadas"] += 1
    summary["completa"] = summary["pendientes"] == 0 and summary["con_error"] == 0
    return issues, details, summary


def fetch_abstract(client, entry_text: str) -> tuple[str | None, str]:
    """Resumen de la fuente (PubMed por PMID o por DOI; si no, OpenAlex). Devuelve (resumen, origen o motivo)."""
    from ..http import NetworkError, redact
    from ..sources import openalex, pubmed
    cfg = getattr(client, "cfg", None)
    ids = extract_identifiers(entry_text)
    problems = []
    pmids = list(ids["pmid"])
    try:
        if not pmids:
            for doi in ids["doi"]:
                pmid = pubmed.pmid_for_doi(client, doi)
                if pmid:
                    pmids.append(pmid)
                    break
        if pmids:
            info = pubmed.abstracts(client, pmids[:1]).get(pmids[0]) or {}
            if info.get("abstract"):
                return info["abstract"], "PubMed"
    except NetworkError as exc:
        problems.append(f"PubMed: {redact(str(exc), cfg)}")
    except Exception as exc:  # noqa: BLE001 - respuesta inesperada: se informa y se sigue
        problems.append(f"PubMed: {exc.__class__.__name__}")
    for doi in ids["doi"]:
        try:
            record = openalex.work_by_doi(client, doi)
            if record.abstract:
                return record.abstract, "OpenAlex"
        except NetworkError as exc:
            problems.append(f"OpenAlex: {redact(str(exc), cfg)}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"OpenAlex: {exc.__class__.__name__}")
    return None, "; ".join(problems) or "sin resumen en PubMed ni en OpenAlex"


def check_support_quotes(client, text: str) -> tuple[list[Issue], list[dict]]:
    """Compara las citas literales entre comillas de la columna Motivo del Anexo A con el resumen de la fuente.
    Solo el resumen: el texto completo no se comprueba."""
    doc = _parse(text)
    rows = _support_rows(doc).rows
    entries: dict[str, list[BibEntry]] = {}
    for entry in bibliography_entries(text):
        entries.setdefault(entry.ref, []).append(entry)
    cache: dict[str, tuple[str | None, str]] = {}
    issues: list[Issue] = []
    details: list[dict] = []
    for row in rows:
        quotes = [q for m in QUOTE_RE.finditer(row.reason) for q in m.groups() if q]
        quotes = [q for q in quotes if len(_norm_words(q)) >= 2]
        for ref in row.refs:
            for quote in quotes:
                if ref not in cache:
                    found = [fetch_abstract(client, e.text) for e in entries.get(ref, [])]
                    good = [f for f in found if f[0]]
                    cache[ref] = good[0] if good else (None, "; ".join(f[1] for f in found) or "sin entrada")
                abstract, origin = cache[ref]
                short = quote if len(quote) <= 80 else quote[:77] + "..."
                if abstract is None:
                    issues.append(Issue("aviso", f"{ref}: no se pudo comprobar la cita «{short}» del Anexo A: resumen "
                                                 f"no disponible ({origin}); el texto completo no se comprueba",
                                        row.line))
                    details.append({"ref": ref, "cita": quote, "estado": "sin_resumen"})
                    continue
                words, needle = _norm_words(_visible(abstract)), _norm_words(quote)
                hit = bool(_find(words, needle, 0))
                details.append({"ref": ref, "cita": quote, "estado": "en_resumen" if hit else "no_en_resumen",
                                "origen": origin})
                if not hit:
                    issues.append(Issue("aviso", f"{ref}: la cita «{short}» del Anexo A no aparece en el resumen de la "
                                                 f"fuente ({origin}); solo se comprueba el resumen, no el texto "
                                                 "completo: indica la página o la tabla", row.line))
    return issues, details


# ============================================================================ verificación y sello
@dataclass
class ReportCheck:
    path: Path
    text: str
    sha256: str
    issues: list[Issue]
    details: list[dict]
    summary: dict | None
    online: bool
    structure: bool
    quotes: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def verify_report(path: Path | str, client=None, conn=None, require_sections: bool = True,
                  gate_ids=None) -> ReportCheck:
    """Comprueba un documento: sin red siempre; en línea si se pasa `client` (sin caché)."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    issues = check_report(text, require_sections=require_sections, conn=conn, path=path, gate_ids=gate_ids)
    details: list[dict] = []
    summary = None
    quotes: list[dict] = []
    if client is not None:
        more, details, summary = check_report_online(client, text, conn)
        issues += more
        quote_issues, quotes = check_support_quotes(client, text)
        issues += quote_issues
    return ReportCheck(path, text, sha256_text(raw), issues, details, summary, client is not None, require_sections,
                       quotes)


def stamp_path(report: Path) -> Path:
    report = Path(report)
    return report.with_name(report.name + ".verificacion.json")


def write_stamp(check: ReportCheck) -> dict:
    """Escribe el sello firmado del resultado de `verify_report` (nunca de datos sueltos)."""
    if not isinstance(check, ReportCheck):
        raise StampError("El sello solo se escribe a partir del resultado de verify_report")
    if sha256_text(check.text) != check.sha256 or sha256_file(check.path) != check.sha256:
        raise StampError("El informe cambió durante la verificación: no se sella. Vuelve a ejecutar la comprobación.")
    info = status_info(check.text)
    data = {
        "formato": 2,
        "informe": check.path.name,
        "sha256": check.sha256,
        "fecha": utc_now(),
        "en_linea": check.online,
        "verificacion_completa": bool(check.online and check.summary and check.summary.get("completa")),
        "estructura_completa": check.structure,
        "estado_informe": info.value,
        "revisado_por_humano": human_reviewer(check.text),
        "huella_revision": review_digest(check.text),
        "referencias": _bibliography_ids(check.text),
        "resumen": check.summary or {},
        "errores": len(check.errors),
        "avisos": len(check.issues) - len(check.errors),
        "incidencias": [asdict(i) for i in check.issues],
        "detalle": check.details or [],
        "citas_anexo_a": check.quotes,
        "no_comprobado": "que cada fuente respalde la afirmación (Anexo A, juicio de verificador-citas revisado por "
                         "una persona) y el texto completo de las fuentes",
        "nutria": __version__,
    }
    data["firma"] = sello.sign(data)
    stamp_path(check.path).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def _detail_gaps(text: str, details) -> list[str]:
    """Referencias de la bibliografía actual sin una verificación correcta en el detalle del sello."""
    ok: set[tuple[str, str]] = set()
    manual: set[tuple[str, str]] = set()
    for row in details if isinstance(details, list) else []:
        if not isinstance(row, dict):
            continue
        if (row.get("status") == "verificada" and row.get("retracted") is False and not row.get("discrepancia")
                and row.get("ok") is True):
            ok.add((str(row.get("ref")), str(row.get("identifier")).lower()))
        elif row.get("status") == "verificada_manual":
            manual.add((str(row.get("ref")), str(row.get("identifier"))))
    gaps = []
    for entry in bibliography_entries(text):
        ids = extract_identifiers(entry.text)
        if not ids["doi"] and not ids["pmid"]:
            urls = list(dict.fromkeys(_entry_urls(entry.text)))
            if not urls or any((entry.ref, url) not in manual for url in urls):   # cada URL, la suya (D3-2)
                gaps.append(entry.ref)
            continue
        for ident in ids["doi"] + ids["pmid"]:
            if (entry.ref, ident.lower()) not in ok:
                gaps.append(f"{entry.ref} ({ident})")
    return gaps


def stamp_status(report: Path | str, conn=None) -> tuple[bool, str]:
    report = Path(report)
    path = stamp_path(report)
    if not path.is_file():
        return False, "Sin sello de verificación: ejecuta `nutria informe comprobar <informe> --online --sellar`"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (ValueError, OSError) as exc:
        return False, f"Sello ilegible o corrupto ({exc.__class__.__name__}): vuelve a verificar"
    if not isinstance(data, dict) or not sello.verify(data):
        return False, "Sello sin firma válida (escrito a mano, alterado o creado en otro equipo): vuelve a verificar"
    if not report.is_file():
        return False, "El informe no existe"
    raw = report.read_bytes()
    try:
        current = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "El informe no está en UTF-8: no se puede comprobar"
    if data.get("sha256") != sha256_text(raw):
        return False, "El informe ha cambiado después del sello: vuelve a verificar"
    if not data.get("en_linea"):
        return False, "El sello no incluye verificación en línea de las referencias"
    if not data.get("verificacion_completa"):
        resumen = data.get("resumen") or {}
        return False, (f"Verificación incompleta: {resumen.get('pendientes', '?')} referencia(s) sin comprobar y "
                       f"{resumen.get('con_error', '?')} con error")
    report_doc = is_report_path(report)
    if report_doc and not data.get("estructura_completa"):
        return False, ("El sello no comprobó la estructura completa del informe (usa `nutria informe comprobar "
                       "<informe> --online --sellar`)")
    if data.get("errores", 1) != 0:
        return False, f"El sello registra {data.get('errores')} error(es)"
    if data.get("referencias") != _bibliography_ids(current):
        return False, "Las referencias del informe no coinciden con las del sello"
    gaps = _detail_gaps(current, data.get("detalle"))
    if gaps:
        return False, f"El sello no contiene una verificación correcta de: {', '.join(gaps[:5])}: vuelve a verificar"
    own = conn is None
    if own:
        conn = open_registry_for(report)
    try:
        structure = report_doc or bool(data.get("estructura_completa"))
        rerun = [i for i in check_report(current, require_sections=structure, conn=conn, path=report)
                 if i.severity == "error"]
    finally:
        if own and conn is not None:
            conn.close()
    if rerun:
        return False, (f"El informe tiene {len(rerun)} error(es) al volver a comprobarlo (p. ej.: {rerun[0].message}): "
                       "corrígelo y vuelve a sellar")
    info = status_info(current)
    # Un borrador por completar (p. ej. el esqueleto de `informe nuevo`) no tiene sello válido: no hay nada que
    # garantizar todavía (en revisado o final los marcadores ya son errores de la comprobación anterior).
    n_pending, empty = pending_summary(current, require_sections=structure)
    if n_pending or empty:
        what = [f"{n_pending} apartado(s) con {PENDING_MARK}"] if n_pending else []
        what += [f"secciones vacías: {', '.join(str(n) for n in empty)}"] if empty else []
        return False, (f"Documento por completar ({'; '.join(what)}): el sello solo registra la comprobación de un "
                       "borrador incompleto y no vale como sello del informe; complétalo y vuelve a sellar")
    total = (data.get("resumen") or {}).get("total", 0)
    links = sum(1 for i in data.get("incidencias") or [] if isinstance(i, dict)
                and str(i.get("message", "")).startswith("Enlace en el cuerpo"))
    refs = ("no había referencias que verificar" if not total else
            f"{(data.get('resumen') or {}).get('verificadas', 0)} de {total} referencia(s) verificadas en línea "
            "(existencia, correspondencia con la cita y retractación)")
    if links:
        refs += f"; {links} enlace(s) del cuerpo sin verificar (no son referencias de la bibliografía)"
    annex_ids = sum(1 for i in data.get("incidencias") or [] if isinstance(i, dict)
                    and str(i.get("message", "")).startswith("Identificador de una fuente en el Anexo B o C"))
    if annex_ids:
        refs += f"; {annex_ids} identificador(es) en los anexos B o C sin verificar (no son referencias de la bibliografía)"
    parts = [f"estado '{info.value or 'sin estado'}'", refs,
             "estructura de informe comprobada" if structure else "documento sin estructura de informe"]
    if info.strict:
        parts.append(f"revisión humana registrada de {human_reviewer(current)}")
    else:
        parts.append("sin revisión humana (un borrador no vale como informe revisado ni final)")
    return True, (f"Sello válido ({data.get('fecha')}): " + "; ".join(parts) + f"; {data.get('avisos', 0)} aviso(s). "
                  "No certifica que las fuentes respalden las afirmaciones (Anexo A, revisado por una persona)")
