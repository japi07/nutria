"""Pruebas de la documentación.

Cada prueba fija una corrección de la auditoría externa (D5-1 a D5-7, D5-11, D7-5, D10-1, D10-2, D10-4 y D10-5)
o la coherencia de los documentos con el contrato de diseño y con el código: pesos de la rúbrica, compuertas,
órdenes de la CLI, hooks configurados en `.claude/settings.json` y número de agentes, skills y workflows. Si una
prueba de coherencia falla tras cambiar el código, hay que actualizar el documento, no la prueba.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path

import pytest

from nutria.cli import build_parser
from nutria.reports.check import check_report
from nutria.scoring.engine import load_rubric

ROOT = Path(__file__).resolve().parents[1]
README = "README.md"
REGULACION = "docs/REGULACION_UE.md"
PROMPT = "docs/PROMPT_MAESTRO_v2.md"
REVISION = "docs/REVISION_PROMPT_ORIGINAL.md"
FUENTES = "docs/FUENTES.md"
ARQUITECTURA = "docs/ARQUITECTURA.md"
DOCS = (README, REGULACION, PROMPT, REVISION, FUENTES, ARQUITECTURA)

FECHA = "2026-11-04"
FEEDAP_2024 = "10.2903/j.efsa.2024.8856"
FEEDAP_2018 = "10.2903/j.efsa.2018.5274"
# Normas cuyo contenido figura en los hechos verificados el 2026-11-04: solo estas pueden llevar la marca
# "verificada (2026-11-04)" en la tabla de docs/REGULACION_UE.md.
NORMAS_VERIFICADAS = ("2019/6", "767/2009", "96/22/CE", "2019/1381", "178/2002", "2019/962", "2020/354",
                      "2011/25/UE", "1831/2003")
NOMBRES_COMPUERTAS = {"seguridad": "seguridad", "regulatoria": "regulatoria", "analitica": "analítica",
                      "propiedad_intelectual": "propiedad intelectual", "etica": "ética",
                      "destino_digestivo": "destino digestivo"}
OPCIONES_GLOBALES = {"--json", "--offline", "--version", "--help", "-h"}
OPCIONES_GLOBALES_CON_VALOR = {"--agente"}


# ============================================================================ utilidades
def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def _flat(text: str) -> str:
    """Texto en una sola línea: sin marcas de cita (>) y con los espacios colapsados."""
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text)


def _section(text: str, title: str) -> str:
    """Sección cuyo título empieza por `title`, hasta el siguiente título de su nivel o superior.
    Las líneas dentro de bloques de código (p. ej. comentarios `# ...` de bash) no cuentan como títulos."""
    lines = text.splitlines()
    headings = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2)))
    for k, (start, level, name) in enumerate(headings):
        if name.startswith(title):
            end = next((j for j, lv, _ in headings[k + 1:] if lv <= level), len(lines))
            return "\n".join(lines[start:end])
    raise AssertionError(f"No existe la sección «{title}»")


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and not re.fullmatch(r"\|[\s:|-]+\|", s):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def _paragraphs(text: str) -> list[str]:
    """Párrafos y elementos de lista, cada uno en una línea."""
    blocks, current = [], []
    for line in text.splitlines():
        new_item = re.match(r"^\s*(?:[-*]|\d+\.)\s", line)
        if not line.strip() or new_item:
            if current:
                blocks.append(" ".join(current))
            current = [line] if line.strip() else []
        else:
            current.append(line)
    if current:
        blocks.append(" ".join(current))
    return blocks


def _mentions(text: str, norm: str) -> bool:
    return re.search(r"(?<![\d/])" + re.escape(norm) + r"(?!\d)", text) is not None


def _normativa() -> list[list[str]]:
    """Filas de la tabla «Normativa de referencia» (sin cabecera), con los enlaces quitados de la 1.ª columna."""
    rows = _table_rows(_section(_read(REGULACION), "Normativa de referencia"))
    out = []
    for row in rows:
        if row[0] == "Norma":
            continue
        out.append([re.sub(r"\]\([^)]*\)", "]", row[0])] + row[1:])
    return out


def _subcommands(parser: argparse.ArgumentParser) -> dict | None:
    for action in parser._actions:  # noqa: SLF001 - argparse no expone las subórdenes de otra forma
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices
    return None


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _invalid_path(parser: argparse.ArgumentParser, command: str) -> str | None:
    """Comprueba que la ruta de subórdenes de `command` existe. Devuelve el motivo si no, o None.
    Admite marcadores (<ID>, ...), opciones y alternativas (`a|b`, `a/b`) en posición de suborden."""
    tokens = _tokens(command)[1:]
    i = 0
    while i < len(tokens) and (tokens[i] in OPCIONES_GLOBALES or tokens[i] in OPCIONES_GLOBALES_CON_VALOR):
        i += 2 if tokens[i] in OPCIONES_GLOBALES_CON_VALOR else 1
    current = parser
    while i < len(tokens):
        choices = _subcommands(current)
        token = tokens[i]
        if choices is None or token.startswith(("-", "<", "{", "[", "...")):
            return None
        alternatives = re.split(r"[|/]", token)
        unknown = [a for a in alternatives if a not in choices]
        if unknown:
            return f"suborden desconocida {', '.join(unknown)}"
        current = choices[alternatives[0]]
        i += 1
    return None


def _inline_nutria(text: str) -> list[str]:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return [m.group(1) for m in re.finditer(r"`([^`]+)`", text) if re.match(r"nutria(\s|$)", m.group(1))]


def _bash_nutria(text: str) -> list[str]:
    commands = []
    for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("nutria "):
                commands.append(line)
    return commands


def _references(text: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in re.finditer(r"^\[(R\d+)\].*$", text, re.MULTILINE)}


# ============================================================================ REGULACION_UE.md
def test_regulacion_arbol_numerado_en_el_orden_corregido():  # D5-1, D5-2, D5-3, D5-4, D5-11
    seccion = _section(_read(REGULACION), "Clasificación")
    arbol = re.search(r"```mermaid\n(.*?)```", seccion, re.DOTALL).group(1)
    etiquetas = re.findall(r'"(\d)\. ([^"]*)"', arbol)
    pasos = {int(n): texto for n, texto in etiquetas}
    assert sorted(pasos) == [1, 2, 3, 4, 5, 6, 7]
    assert "2020/354" in pasos[1]                                    # primero, pienso dietético
    assert "2019/6" in pasos[2] and "art. 4" in pasos[2]             # después, medicamento
    assert "biocida" in pasos[3]
    assert "2011/25/UE" in pasos[4]                                  # materia prima o aditivo
    assert "aditivos nutricionales" in pasos[5] and "1831/2003" in pasos[5]
    assert "anexo III" in pasos[6] and "96/22/CE" in pasos[6]        # prohibidas o restringidas
    assert "art. 13" in pasos[7]                                     # alegaciones
    assert "por alegación" in arbol and "por función" in arbol
    assert "K --> C" in arbol, "el camino del pienso dietético debe pasar también por la pregunta del medicamento"
    lista = [seccion.index(f"{n}. **{nombre}") for n, nombre in enumerate(
        ("Pienso para fines", "Medicamento veterinario", "Biocida", "Materia prima o aditivo", "Aditivo",
         "Sustancias prohibidas", "Alegaciones"), start=1)]
    assert lista == sorted(lista)


def test_regulacion_medicamento_por_alegacion_y_por_funcion():  # D5-1
    texto = _flat(_section(_read(REGULACION), "Clasificación"))
    assert "se presenta como tratamiento o prevención de enfermedades" in texto
    assert "restablecer, corregir o modificar funciones fisiológicas" in texto
    assert "acción farmacológica, inmunológica o metabólica" in texto
    assert "cambiar solo la alegación no basta" in texto
    assert "fuera_de_alcance_medicamento" in texto and "fuera_de_alcance_biocida" in texto


def test_regulacion_aditivos_nutricionales_frente_a_materias_primas():  # D5-3
    texto = _flat(_read(REGULACION))
    assert '"Cubrir necesidades nutricionales" no basta' in texto
    assert "vitaminas, compuestos de oligoelementos, aminoácidos y urea" in texto


def test_regulacion_sustancias_prohibidas_o_restringidas():  # D5-4
    seccion = _flat(_section(_read(REGULACION), "Sustancias prohibidas o restringidas"))
    for clave in ("art. 6 y anexo III", "semillas tratadas con fitosanitarios", "96/22/CE",
                  "hormonal o tireostático", "beta-agonistas", "2003/74/CE", "2008/97/CE"):
        assert clave in seccion, clave


def test_regulacion_tabla_con_las_normas_que_faltaban():  # D5-4, D5-5
    normas = [fila[0] for fila in _normativa()]
    assert any(_mentions(n, "96/22/CE") for n in normas)
    assert any(_mentions(n, "767/2009") and "anexo III" in n for n in normas)
    assert any(_mentions(n, "2019/1381") for n in normas)


def test_regulacion_via_de_autorizacion_con_2019_1381():  # D5-5
    seccion = _flat(_section(_read(REGULACION), "Vía de autorización"))
    for clave in ("2019/1381", "27-03-2021", "178/2002", "art. 32a", "art. 32b", "asesoramiento previo",
                  "notificar a EFSA"):
        assert clave in seccion, clave


def test_regulacion_guia_feedap_de_2024():  # D5-6
    texto = _read(REGULACION)
    seccion = _flat(_section(texto, "Guías de EFSA"))
    for clave in (FEEDAP_2024, "2024;22(7):e8856", "6-6-2024", "1-12-2024"):
        assert clave in seccion, clave


def test_la_guia_feedap_de_2018_solo_aparece_como_sustituida():  # D5-6
    for doc in DOCS:
        for parrafo in _paragraphs(_read(doc)):
            if FEEDAP_2018 in parrafo:
                assert re.search(r"sustitu", parrafo, re.IGNORECASE), f"{doc}: {parrafo[:120]}"


def test_regulacion_alegaciones_permitidas():  # D5-11
    seccion = _flat(_section(_read(REGULACION), "Alegaciones"))
    for clave in ("13.1", "13.2", "13.3", "objetivas, verificables por las autoridades y comprensibles",
                  "justificación científica", "optimización de la nutrición",
                  "apoyo o la protección de las condiciones fisiológicas"):
        assert clave in seccion, clave


def test_regulacion_solo_marca_verificado_lo_verificado():  # D5-7
    filas = _normativa()
    assert len(filas) >= 24
    for fila in filas:
        norma, estado = fila[0], fila[-1]
        if "verificada" in estado:
            assert estado.startswith(f"verificada ({FECHA})"), fila
            assert any(_mentions(norma, n) for n in NORMAS_VERIFICADAS), f"{norma}: no figura entre los hechos verificados"
        else:
            assert estado.startswith("pendiente"), fila
    texto = _flat(_read(REGULACION))
    sueltas = [texto[max(0, m.start() - 30):m.end() + 30]
               for m in re.finditer(r"verificad[ao](?! \(" + re.escape(FECHA) + r"\))", texto)]
    assert all("cuenta como verificada" in s for s in sueltas), sueltas


def test_regulacion_explica_que_falta_por_verificar():  # D5-7
    cabecera = _flat(_read(REGULACION).split("## Clasificación")[0])
    for clave in ("CELEX", "versión consolidada", "`/oj`", "nutria verificar manual <URL> --firma-humana NOMBRE",
                  "los agentes no pueden hacerlo"):
        assert clave in cabecera, clave


# ============================================================================ PROMPT_MAESTRO_v2.md
def test_prompt_maestro_nueve_criterios_con_los_pesos_de_la_rubrica():
    rubrica = load_rubric()
    pesos = {}
    for fila in _table_rows(_section(_read(PROMPT), "14. Priorización")):
        m = re.search(r"\(`(\w+)`\)", fila[0])
        if m:
            pesos[m.group(1)] = (float(fila[2]), float(fila[3]))
    assert set(pesos) == rubrica.criterion_ids and len(pesos) == 9
    for criterio, (v2, v1) in pesos.items():
        assert v2 == rubrica.profiles["recomendado"]["pesos"][criterio], criterio
        assert v1 == rubrica.profiles["original"]["pesos"][criterio], criterio
    assert sum(v2 for v2, _ in pesos.values()) == 100 and sum(v1 for _, v1 in pesos.values()) == 100


def test_prompt_maestro_seis_compuertas_propuestas_y_firmadas():
    seccion = _flat(_section(_read(PROMPT), "14. Priorización"))
    compuertas = seccion[seccion.index("**Compuertas**"):seccion.index("**Ranking**")]
    for gate in load_rubric().gate_ids:
        assert NOMBRES_COMPUERTAS[gate] in compuertas, gate
    for clave in ("proponen", "firma una persona", "salir de `bloqueada` exige firma humana",
                  "bloquean automáticamente"):
        assert clave in compuertas.replace("**", ""), clave
    fases = _flat(_section(_read(PROMPT), "3. Proceso por fases"))
    assert "destino_digestivo" in fases and "superada` solo cuenta cuando la firma una persona" in fases


def test_prompt_maestro_ranking_del_contrato():
    seccion = _flat(_section(_read(PROMPT), "14. Priorización"))
    for clave in ("APTA", "CONDICIONAL", "BLOQUEADA", "INCOMPLETA", "INSUFICIENTE", "después de las APTA",
                  "menos de tres evaluadores distintos", "alfa = 50", "cada perfil de pesos"):
        assert clave in seccion, clave


def test_prompt_maestro_economia_p10_p50_p90_y_rnpv():
    seccion = _flat(_section(_read(PROMPT), "13. Evaluación económica")).replace("**", "")
    for clave in ("P10", "P50", "P90", "simulación de resultados", "incertidumbre de los parámetros",
                  '"ROI > 1" equivale a beneficio neto positivo'):
        assert clave in seccion, clave
    assert "costes en su percentil 90), intermedio (valores modales)" not in seccion


def test_prompt_maestro_estadistica_del_contrato():
    seccion = _flat(_section(_read(PROMPT), "7. Estadística")).replace("**", "")
    for clave in ("HKSJ", "por defecto", "(RR)", "(OR)", "(RD)", "--agregar-estudio --rho 0.5",
                  "nutria meta validar-predictivo", "dejando un estudio fuera", "nutria extraccion comparar",
                  "nutria extraccion exportar --variable V --csv F", "nutria meta stpierre"):
        assert clave in seccion, clave


def test_prompt_maestro_destino_digestivo_y_lo_que_no_esta_implementado():
    seccion = _flat(_section(_read(PROMPT), "8. Modelización"))
    for clave in ("nutria destino ruminal", "Ørskov y McDonald (1979)", "DE = a + b·kd/(kd + kp)", "racionamiento",
                  "simulación metabólica", "fermentación ruminal", "mínimo coste", "docking", "PK/PD"):
        assert clave in seccion, clave


def test_prompt_maestro_informes_sello_completo_y_anexo_a():
    seccion = _flat(_section(_read(PROMPT), "15. Informes")).replace("**", "")
    for clave in ("verificación en línea completa", "| Ref | Afirmación | Respaldo | Motivo |", "`respalda`",
                  "`parcial`", "`no_respalda`", "`no_verificable`", "nutria verificar manual <URL> --firma-humana NOMBRE",
                  "ningún agente puede hacerlo", "Sin red no hay sello válido", "revisado_por_humano"):
        assert clave in seccion, clave


def test_prompt_maestro_regulacion_corregida():  # D5-1 a D5-6, D5-11
    texto = _read(PROMPT)
    seccion = _flat(_section(texto, "11. Seguridad y regulación"))
    orden = [seccion.index(n) for n in ("2020/354", "2019/6", "528/2012", "2011/25/UE", "96/22/CE", "(13.2)")]
    assert orden == sorted(orden)
    for clave in ("por función", "acción farmacológica, inmunológica o metabólica", "anexo III", FEEDAP_2024,
                  "2019/1381", "art. 32a", "art. 32b", "(13.1)", "aminoácidos y urea"):
        assert clave in seccion.replace("**", ""), clave
    validacion = _flat(_section(texto, "12. Validación"))
    assert "2024;22(7):e8856" in validacion and "2019/1381" in validacion
    assert "alegación prevista" in _flat(_section(texto, "9. Diseño de productos"))


def test_prompt_maestro_roles_independencia_y_firma_humana():
    texto = _read(PROMPT)
    seccion = _flat(_section(texto, "10. Arquitectura"))
    for clave in ("Evaluador independiente", "nunca puntúa", "INSUFICIENTE", "--firma-humana NOMBRE",
                  "un hook la bloquea a los agentes", "`confidencial`"):
        assert clave in seccion, clave
    assert "ESTADO_Y_LIMITACIONES.md" in texto.split("---")[0]


# ============================================================================ REVISION_PROMPT_ORIGINAL.md
def test_revision_r19_vigente_y_r15_sustituida():  # D5-6
    texto = _read(REVISION)
    refs = _references(texto)
    assert FEEDAP_2024 in refs["R19"] and "2024" in refs["R19"]
    assert "sustituida" in refs["R15"] and "[R19]" in refs["R15"]
    assert "[R19]" in _section(texto, "I5.")


def test_revision_termina_con_la_auditoria_posterior():
    texto = _read(REVISION)
    titulos = re.findall(r"^## (.+)$", texto, re.MULTILINE)
    assert titulos[-1] == "Auditoría posterior de la implementación"
    assert "docs/ESTADO_Y_LIMITACIONES.md" in _section(texto, "Auditoría posterior de la implementación")


def test_revision_frontera_por_funcion_y_r6_pendiente():  # D5-1
    texto = _read(REVISION)
    plano = _flat(texto)
    assert "categoría natural" not in plano
    assert "acción farmacológica, inmunológica o metabólica" in plano
    r6 = _references(texto)["R6"]
    assert "pendiente" in r6 and "(verificada" not in r6


def test_revision_pasa_el_comprobador_de_referencias():
    errores = [str(i) for i in check_report(_read(REVISION), require_sections=False, strict=False)
               if i.severity == "error"]
    assert errores == []


# ============================================================================ FUENTES.md (D7-5)
def test_fuentes_openalex_exige_clave_y_crossref_publica_retractaciones():
    filas = {fila[0]: " | ".join(fila) for fila in _table_rows(_read(FUENTES))}
    openalex = filas["OpenAlex"]
    for clave in ("13-02-2026", "https://openalex.org/settings/api", "obligatoria", "OPENALEX_API_KEY"):
        assert clave in openalex, clave
    assert "opcional" not in openalex.lower()
    assert "update-to" in filas["Crossref"] and "Retraction Watch" in filas["Crossref"]


def test_fuentes_dominios_de_webfetch_permitidos_en_settings():  # D10-5
    texto = _flat(_read(FUENTES))
    lista = texto[texto.index("Para lectura con WebFetch:"):texto.index("Comprueba con")]
    dominios = re.findall(r"`([a-z0-9.-]+\.[a-z]{2,})`", lista)
    assert len(dominios) >= 5
    permitidos = set(json.loads(_read(".claude/settings.json"))["permissions"]["allow"])
    for dominio in dominios:
        assert f"WebFetch(domain:{dominio})" in permitidos, dominio
    assert "pide permiso" in texto


def test_readme_openalex_clave_obligatoria():  # D7-5
    texto = _read(README)
    assert "OPENALEX_API_KEY" in texto and "13-02-2026" in texto
    assert "OpenAlex la exige en todas las peticiones" in _flat(texto).replace("**", "")
    for linea in texto.splitlines():
        if "OpenAlex" in linea:
            assert "acceso preferente" not in linea, linea
    for doc in (README, FUENTES):
        assert not re.search(r"clave\s+opcional", _read(doc), re.IGNORECASE), doc


# ============================================================================ ARQUITECTURA.md
def _hooks_configurados() -> tuple[set[str], set[str]]:
    settings = json.loads(_read(".claude/settings.json"))
    eventos, scripts = set(), set()
    for evento, grupos in settings.get("hooks", {}).items():
        eventos.add(evento)
        for grupo in grupos:
            for hook in grupo.get("hooks", []):
                scripts.update(re.findall(r"[\w.-]+\.(?:sh|py)\b", hook.get("command", "")))
    return eventos, scripts


def test_arquitectura_y_readme_documentan_los_hooks_configurados():
    eventos, scripts = _hooks_configurados()
    assert {"PreToolUse", "PostToolUse"} <= eventos
    for doc in (ARQUITECTURA, README):
        texto = _read(doc)
        for nombre in sorted(eventos | scripts):
            assert nombre in texto, f"{doc} no menciona {nombre}"


def test_arquitectura_compuertas_sello_y_hooks():
    texto = _flat(_read(ARQUITECTURA)).replace("**", "")
    for clave in ("Propuesta", "--firma-humana NOMBRE", "Solo una persona", "fuera_de_alcance_*", "destino_digestivo",
                  "Verificación en línea completa", "| Ref | Afirmación | Respaldo | Motivo |", "Sin red",
                  ".verificacion.json", "clave_sello", ".venv/bin/python", "Falla cerrado", "evaluador-independiente",
                  "extraccion comparar A.json B.json", "extraccion exportar --variable V --csv F"):
        assert clave in texto, clave


def test_recuentos_de_agentes_skills_y_workflows():
    claude = ROOT / ".claude"
    n_agentes = len(list((claude / "agents").glob("*.md")))
    n_skills = len(list((claude / "skills").glob("*/SKILL.md")))
    n_workflows = len(list((claude / "workflows").glob("*.js")))
    readme = _flat(_read(README))
    for frase in (f"{n_agentes} subagentes", f"{n_skills} skills", f"{n_workflows} workflows"):
        assert frase in readme, frase
    arquitectura = _read(ARQUITECTURA)
    for frase in (f"`.claude/agents/*.md` ({n_agentes})", f"`.claude/skills/*/SKILL.md` ({n_skills})",
                  f"`.claude/workflows/*.js` ({n_workflows}", f"{n_agentes} subagentes"):
        assert frase in arquitectura, frase


# ============================================================================ README.md (D10-1, D10-2, D10-4, D10-5)
def test_readme_garantias_exactas():  # D10-1
    texto = _flat(_read(README))
    for clave in ("verificación en línea **completa**", "Sin red no hay sello válido",
                  "nutria verificar manual <URL> --firma-humana NOMBRE", "En un borrador solo comprueba la forma",
                  "no un control de seguridad", "con pruebas automáticas"):
        assert clave in texto, clave
    assert "bloquea informes con referencias no verificables" not in texto
    assert "42 pruebas" not in texto


def test_readme_hook_busca_el_interprete_de_venv():  # D10-2
    texto = _flat(_read(README))
    for clave in ("scripts/hooks/run_hook.sh", ".venv/bin/python", "Activa el entorno antes de abrir Claude Code",
                  "falla cerrado"):
        assert clave in texto, clave
    assert ".venv/bin/python" in _read("scripts/hooks/run_hook.sh")


def test_readme_limitaciones_honestas():  # D10-4, D10-5
    texto = _flat(_read(README))
    for clave in ("nunca se ha ejecutado de principio a fin", "nunca ejecutados de principio a fin",
                  "control de flujo", "sin probar contra los servicios reales", "NUTRIA_PRUEBAS_EN_VIVO=1",
                  "racionamiento", "simulación metabólica", "fermentación ruminal", "mínimo coste", "docking",
                  "PK/PD", "Ørskov y McDonald", "R no es un requisito de instalación", "metafor y clubSandwich",
                  "docs/ESTADO_Y_LIMITACIONES.md"):
        assert clave in texto, clave


def test_readme_orden_de_magnitud_de_agentes_por_workflow():  # D10-4
    filas = {fila[0].strip("`"): fila for fila in _table_rows(_section(_read(README), "Workflows y número de agentes"))}
    workflows = sorted(p.stem for p in (ROOT / ".claude" / "workflows").glob("*.js"))
    assert workflows
    for nombre in workflows:
        assert nombre in filas, nombre
        assert re.search(r"Del orden de \*\*\d+(-\d+)?\*\*", filas[nombre][-1]), filas[nombre]


def test_readme_ejemplos_son_ordenes_validas():
    parser = build_parser()
    ordenes = _bash_nutria(_read(README))
    assert len(ordenes) >= 8
    for orden in ordenes:
        try:
            parser.parse_args(_tokens(orden)[1:])
        except SystemExit as exc:  # argparse sale con código 2 ante una opción o suborden no válida
            pytest.fail(f"Orden del README no válida ({exc.code}): {orden}")


# ============================================================================ todos los documentos
def test_documentos_citan_ordenes_que_existen():
    parser = build_parser()
    errores = []
    for doc in DOCS:
        for orden in _inline_nutria(_read(doc)):
            motivo = _invalid_path(parser, orden)
            if motivo:
                errores.append(f"{doc}: `{orden}`: {motivo}")
    assert errores == []


def test_comprobador_de_ordenes_detecta_subordenes_inexistentes():
    parser = build_parser()
    assert _invalid_path(parser, "nutria meta analizar datos.csv") is None
    assert _invalid_path(parser, "nutria --agente x puntuar compuerta <ID> regulatoria") is None
    assert _invalid_path(parser, "nutria oportunidad|hipotesis nueva") is None
    assert _invalid_path(parser, "nutria meta inventada") is not None
    assert _invalid_path(parser, "nutria inexistente") is not None


def test_diagramas_mermaid_con_comillas_equilibradas():
    for doc in DOCS:
        for bloque in re.findall(r"```mermaid\n(.*?)```", _read(doc), flags=re.DOTALL):
            for linea in bloque.splitlines():
                assert linea.count('"') % 2 == 0, f"{doc}: {linea}"
