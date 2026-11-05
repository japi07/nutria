"""Perspectiva del ganadero: presupuesto parcial por animal (beneficio frente a coste del producto).

Dosis: [coste] la declara en UNA base (nutria.dosis):
- dosis_g_dia: g de producto por animal y día;
- dosis_mg_kg_ms: mg por kg de MS de la ración total (exige [racion].ingestion_ms_kg_dia o la ingestión de la ración
  completa, de la que se deduce);
- dosis_g_t: g por tonelada del pienso portador (exige [racion].portador, [racion].base y la ingestión de ese
  pienso: ingestion_pienso_kg_dia o, para la ración completa, la ingestión de MS y, en base tal cual, ms_pienso).
Con el pienso portador declarado se calcula además el coste del producto por tonelada de pienso
(g/t × €/kg / 1000). Las ingestiones se declaran (con fuente o [SUP]): el modelo no predice la ingestión.

Periodos (días):
- el coste usa [coste].dias si existe y, si no, [escenario].dias;
- cada [[beneficio]] por día usa su propio "dias" (número o distribución) si lo declara y, si no,
  [escenario].dias. Así, un producto de transición puede pagarse 42 días y contar la leche solo los días
  posparto. Un beneficio por ciclo (por_dia = false) no admite "dias".

Escenarios coherentes: percentiles del beneficio neto simulado por Monte Carlo
- conservador: P10; intermedio: P50; optimista: P90;
más una fila de valores centrales como referencia determinista, con UN estadístico para todas las entradas
(`FarmModel.central`: "moda" por defecto o "mediana"; ver nutria.econ.dist). Combinar percentiles de las entradas
(p. ej., P10 de todos los beneficios con P90 de todos los costes) no da, en general, el percentil del resultado:
suele dar un caso bastante más extremo y de probabilidad desconocida.

Monte Carlo: las entradas son independientes salvo las correlaciones declaradas ([[correlacion]] por pares o
[correlaciones] con la matriz completa), que se aplican con una cópula gaussiana; la matriz se comprueba (simétrica,
diagonal 1, |rho| <= 1 y definida positiva) y, si no lo es, el modelo no se evalúa.

Sensibilidad (tornado): cada entrada con incertidumbre en su P10 y en su P90 con las demás en su valor central;
se ordena por la amplitud del neto. Es un análisis de una entrada cada vez (no aplica las correlaciones).

Respuesta umbral de cada beneficio: con valores centrales coherentes (coste, valor unitario y días con el mismo
estadístico central: moda con moda o mediana con mediana, declarado en el informe), (a) con los demás beneficios a cero
y (b) con los demás en su valor central.

Coste: [coste] declara la dosis (en una base) junto con precio_eur_kg, o solo otros_eur_dia o fijo_eur_ciclo; un
modelo sin ningún coste, o con dosis sin precio (o al revés), es un error.

Alternativa ([alternativa], opcional): "no hacer nada" (no_hacer_nada = true; el neto ya se mide frente a no usar el
producto) u otro producto con su coste y su respuesta en cada beneficio, evaluado en las mismas simulaciones
(valores unitarios, días y ración compartidos): diferencia de neto y P(producto > alternativa).

Fuentes: cada entrada puede declararse como tabla con "fuente" ({ dist = "pert", ..., fuente = "[SUP] ..." } o
{ valor = 50, fuente = "[PUB] doi:10.xxxx/yyyy" }); el informe las muestra y señala las declaradas sin fuente. Una
fuente [PUB] cita el identificador de una fuente o afirmación registrada (`nutria econ ... --exigir-fuentes` lo
comprueba en el registro).
ROI = beneficio/coste: ROI > 1 equivale a beneficio neto positivo.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import dosis
from .dist import (CENTRAL_TEXT, Dist, check_central, copula_uniforms, gaussian_copula, parse_dist,
                   source_has_label, sources_summary, sources_summary_line)

# Escenario -> percentil del beneficio neto simulado.
SCENARIO_PERCENTILES = (("conservador", 10), ("intermedio", 50), ("optimista", 90))
_PERCENTILES = (5, 10, 50, 90, 95)
DOSE_KEYS = {"dosis_g_dia": "g_animal_dia", "dosis_mg_kg_ms": "mg_kg_ms", "dosis_g_t": "g_t"}
_SECTIONS = {"escenario", "coste", "racion", "beneficio", "supuestos", "correlacion", "correlaciones", "alternativa"}
_ESCENARIO_KEYS = {"nombre", "especie", "unidad", "dias", "nota"}
_COSTE_KEYS = {"dias", "precio_eur_kg", "otros_eur_dia", "fijo_eur_ciclo", "nota", *DOSE_KEYS}
_RACION_KEYS = {"ingestion_ms_kg_dia", "portador", "base", "ingestion_pienso_kg_dia", "ms_pienso", "nota"}
_BENEFICIO_KEYS = {"nombre", "respuesta", "valor_unitario", "por_dia", "nota", "dias"}
_ALTERNATIVA_KEYS = {"nombre", "no_hacer_nada", "coste", "respuesta", "fuente", "nota"}


@dataclass
class Benefit:
    name: str
    response: Dist
    unit_value: Dist
    per_day: bool = True
    note: str = ""
    days: Dist | None = None  # None: usa [escenario].dias


@dataclass
class Ration:
    dmi: Dist | None = None          # [racion].ingestion_ms_kg_dia (kg MS/animal/día)
    carrier: str | None = None       # [racion].portador: racion_completa | concentrado
    basis: str | None = None         # [racion].base del g/t: tal_cual | ms
    feed_intake: Dist | None = None  # [racion].ingestion_pienso_kg_dia (kg/animal/día del portador en su base)
    dm_fraction: Dist | None = None  # [racion].ms_pienso (fracción de MS del portador)


@dataclass
class Alternative:
    name: str
    nothing: bool = False
    dose: Dist | None = None
    dose_basis: str = "g_animal_dia"
    price: Dist | None = None
    other: Dist | None = None
    fixed: Dist | None = None
    cost_days: Dist | None = None
    responses: list[Dist] = field(default_factory=list)
    source: str | None = None


@dataclass
class FarmModel:
    name: str
    species: str
    unit: str
    days: Dist
    dose: Dist
    price_eur_kg: Dist
    other_eur_day: Dist
    fixed_eur_cycle: Dist
    benefits: list[Benefit] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    cost_days: Dist | None = None  # None: usa [escenario].dias
    dose_basis: str = "g_animal_dia"
    ration: Ration = field(default_factory=Ration)
    alternative: Alternative | None = None
    corr_names: list[str] = field(default_factory=list)
    corr_matrix: np.ndarray | None = None
    corr_sources: list[str] = field(default_factory=list)
    corr_unsourced: list[str] = field(default_factory=list)
    declared: set[str] = field(default_factory=set)
    central: str = "moda"  # estadístico central de TODAS las entradas: "moda" o "mediana"

    @property
    def dose_key(self) -> str:
        return "coste." + _dose_field(self.dose_basis)


def _dose_field(basis: str) -> str:
    return next(k for k, b in DOSE_KEYS.items() if b == basis)


def _check_keys(where: str, table: dict, allowed: set[str]) -> None:
    if not isinstance(table, dict):
        raise ValueError(f"{where} debe ser una tabla")
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"{where}: claves desconocidas {sorted(unknown)}. Admitidas: {sorted(allowed)}")


def _parse_dose(where: str, coste: dict, declared: set[str], prefix: str) -> tuple[Dist, str]:
    present = [k for k in DOSE_KEYS if k in coste]
    if len(present) > 1:
        raise ValueError(f"{where}: declara la dosis en una sola base ({', '.join(present)}); usa "
                         "`nutria dosis convertir` para pasar de una a otra")
    if not present:
        return parse_dist(0), "g_animal_dia"
    declared.add(f"{prefix}.{present[0]}")
    return parse_dist(coste[present[0]]), DOSE_KEYS[present[0]]


def _dmi_available(r: Ration) -> bool:
    """Ingestión de MS declarada o deducible de la ración completa (su ingestión en MS, o tal cual con ms_pienso)."""
    if r.dmi is not None:
        return True
    return r.carrier == "racion_completa" and r.feed_intake is not None and (r.basis == "ms" or
                                                                              r.dm_fraction is not None)


def _check_dose_requirements(where: str, basis: str, ration: Ration) -> None:
    if basis == "mg_kg_ms" and not _dmi_available(ration):
        raise ValueError(f"{where}: una dosis en mg/kg de MS exige [racion].ingestion_ms_kg_dia (o la ingestión de la "
                         "ración completa, de la que se deduce)")
    if basis == "g_t":
        if ration.carrier is None:
            raise ValueError(f"{where}: una dosis en g/t exige [racion].portador (racion_completa o concentrado) y "
                             "[racion].base (tal_cual o ms)")
        if not _feed_derivable(ration):
            raise ValueError(f"{where}: una dosis en g/t exige la ingestión del pienso portador "
                             "([racion].ingestion_pienso_kg_dia o, para la ración completa, la ingestión de MS y, "
                             "en base tal cual, ms_pienso)")


def _feed_derivable(r: Ration) -> bool:
    if r.carrier is None:
        return False
    if r.feed_intake is not None:
        return True
    if r.carrier == "racion_completa":
        return r.dmi is not None and (r.basis == "ms" or r.dm_fraction is not None)
    return False


def _parse_ration(data: dict, declared: set[str]) -> Ration:
    rac = data.get("racion", {})
    _check_keys("[racion]", rac, _RACION_KEYS)
    carrier, basis = rac.get("portador"), rac.get("base")
    if (carrier is None) != (basis is None):
        raise ValueError("[racion]: el pienso portador exige 'portador' (racion_completa o concentrado) y 'base' "
                         "(tal_cual o ms)")
    if carrier is not None:
        if carrier not in dosis.PORTADORES:
            raise ValueError(f"[racion].portador no válido: {carrier!r}. Opciones: {', '.join(dosis.PORTADORES)}")
        if basis not in dosis.BASES_PIENSO:
            raise ValueError(f"[racion].base no válida: {basis!r}. Opciones: {', '.join(dosis.BASES_PIENSO)}")
    r = Ration(carrier=carrier, basis=basis)
    for key, attr in (("ingestion_ms_kg_dia", "dmi"), ("ingestion_pienso_kg_dia", "feed_intake"),
                      ("ms_pienso", "dm_fraction")):
        if key in rac:
            setattr(r, attr, parse_dist(rac[key]))
            declared.add(f"racion.{key}")
    if r.feed_intake is not None and r.carrier is None:
        raise ValueError("[racion].ingestion_pienso_kg_dia exige declarar el pienso portador ('portador' y 'base')")
    if r.dm_fraction is not None and r.carrier is None:
        raise ValueError("[racion].ms_pienso exige declarar el pienso portador ('portador' y 'base'): sin él no se usaría")
    if r.dm_fraction is not None and r.basis == "ms":
        raise ValueError("[racion].ms_pienso solo se usa con base = \"tal_cual\": en base MS no interviene; quítalo")
    if r.carrier == "racion_completa" and r.feed_intake is not None and r.dmi is not None and \
            (r.basis == "ms" or r.dm_fraction is not None):
        raise ValueError("[racion]: para la ración completa la ingestión del pienso se deduce de la ingestión de MS"
                         + (" y ms_pienso" if r.basis == "tal_cual" else "") + "; no declares también "
                         "ingestion_pienso_kg_dia (serían entradas redundantes que pueden contradecirse)")
    return r


def _parse_benefit(b: dict, index: int, declared: set[str]) -> Benefit:
    _check_keys(f"[[beneficio]] n.º {index}", b, _BENEFICIO_KEYS)
    for key in ("nombre", "respuesta", "valor_unitario"):
        if key not in b:
            raise ValueError(f"[[beneficio]] n.º {index}: falta '{key}'")
    name = str(b["nombre"])
    per_day = b.get("por_dia", True)
    if not isinstance(per_day, bool):
        raise ValueError(f"El beneficio '{name}': por_dia debe ser true o false")
    if "dias" in b and not per_day:
        raise ValueError(f"El beneficio '{name}' declara 'dias' pero por_dia = false: los días no se usarían. "
                         "Elimina 'dias' o usa por_dia = true.")
    days = parse_dist(b["dias"]) if "dias" in b else None
    declared.update({f"beneficio[{index}].respuesta", f"beneficio[{index}].valor_unitario"})
    if days is not None:
        declared.add(f"beneficio[{index}].dias")
    return Benefit(name, parse_dist(b["respuesta"]), parse_dist(b["valor_unitario"]), per_day,
                   str(b.get("nota", "")), days)


def _parse_alternative(data: dict, benefits: list[Benefit], ration: Ration, declared: set[str]) -> Alternative | None:
    alt = data.get("alternativa")
    if alt is None:
        return None
    _check_keys("[alternativa]", alt, _ALTERNATIVA_KEYS)
    if not str(alt.get("nombre", "")).strip():
        raise ValueError("[alternativa] necesita 'nombre' (p. ej. \"no hacer nada\" o el producto comercial)")
    nothing = alt.get("no_hacer_nada", False)
    if not isinstance(nothing, bool):
        raise ValueError("[alternativa].no_hacer_nada debe ser true o false")
    source = alt.get("fuente")
    if nothing:
        if "coste" in alt or "respuesta" in alt:
            raise ValueError("[alternativa] con no_hacer_nada = true no lleva coste ni respuestas")
        return Alternative(str(alt["nombre"]), nothing=True, source=source)
    coste = alt.get("coste")
    resp = alt.get("respuesta")
    if coste is None or resp is None:
        raise ValueError("[alternativa]: declara [alternativa.coste] (dosis y precio) y [alternativa.respuesta] (la "
                         "respuesta de la alternativa en cada beneficio, por su nombre), o no_hacer_nada = true")
    _check_keys("[alternativa.coste]", coste, _COSTE_KEYS)
    dose, basis = _parse_dose("[alternativa.coste]", coste, declared, "alternativa.coste")
    if not any(k in coste for k in DOSE_KEYS) or "precio_eur_kg" not in coste:
        raise ValueError("[alternativa.coste] necesita la dosis (en una base) y precio_eur_kg")
    _check_dose_requirements("[alternativa.coste]", basis, ration)
    names = [b.name for b in benefits]
    _check_keys("[alternativa.respuesta]", resp, set(names))
    missing = [n for n in names if n not in resp]
    if missing:
        raise ValueError("[alternativa.respuesta]: falta la respuesta de la alternativa en: " + "; ".join(missing)
                         + " (declara 0 si no tiene efecto)")
    for key in ("precio_eur_kg", "otros_eur_dia", "fijo_eur_ciclo", "dias"):
        if key in coste:
            declared.add(f"alternativa.coste.{key}")
    declared.update(f"alternativa.respuesta[{i}]" for i in range(1, len(names) + 1))
    return Alternative(
        str(alt["nombre"]), dose=dose, dose_basis=basis, price=parse_dist(coste["precio_eur_kg"]),
        other=parse_dist(coste.get("otros_eur_dia", 0)), fixed=parse_dist(coste.get("fijo_eur_ciclo", 0)),
        cost_days=parse_dist(coste["dias"]) if "dias" in coste else None,
        responses=[parse_dist(resp[n]) for n in names], source=source)


def _parse_correlations(data: dict, model: FarmModel) -> None:
    pairs = data.get("correlacion", [])
    table = data.get("correlaciones")
    if not pairs and table is None:
        return
    if pairs and table is not None:
        raise ValueError("Declara las correlaciones de una sola forma: [[correlacion]] por pares o [correlaciones] "
                         "con la matriz")
    available = {key: d for key, _label, d in inputs(model)}
    sources: list[str] = []
    unsourced: list[str] = []
    if table is not None:
        _check_keys("[correlaciones]", table, {"variables", "matriz", "fuente", "nota"})
        names = list(table.get("variables", []))
        if len(set(names)) != len(names):
            raise ValueError("[correlaciones].variables tiene nombres repetidos")
        triples, matrix = None, table.get("matriz")
        if matrix is None:
            raise ValueError("[correlaciones] necesita 'variables' y 'matriz'")
        if table.get("fuente"):
            sources.append(str(table["fuente"]))
        if not source_has_label(table.get("fuente")):
            unsourced.append("[correlaciones]")
    else:
        if not isinstance(pairs, list):
            raise ValueError("[[correlacion]] debe ser una lista de tablas")
        names, triples, matrix = [], [], None
        for c in pairs:
            _check_keys("[[correlacion]]", c, {"entre", "rho", "fuente", "nota"})
            between = c.get("entre")
            if not (isinstance(between, list) and len(between) == 2 and all(isinstance(x, str) for x in between)):
                raise ValueError("[[correlacion]].entre debe ser una lista con dos nombres de entrada")
            rho = c.get("rho")
            if isinstance(rho, bool) or not isinstance(rho, (int, float)) or not math.isfinite(float(rho)):
                raise ValueError(f"[[correlacion]] {between}: 'rho' debe ser un número")
            for x in between:
                if x not in names:
                    names.append(x)
            triples.append((between[0], between[1], float(rho)))
            if c.get("fuente"):
                sources.append(f"{between[0]} ~ {between[1]}: {c['fuente']}")
            if not source_has_label(c.get("fuente")):
                unsourced.append(f"correlación {between[0]} ~ {between[1]}")
    if len(names) < 2:
        raise ValueError("Las correlaciones necesitan al menos dos entradas")
    for name in names:
        if name not in available:
            raise ValueError(f"Correlación con una entrada desconocida: '{name}'. Entradas del modelo: "
                             + ", ".join(available))
        if not available[name].stochastic:
            raise ValueError(f"Correlación con '{name}', que es un valor fijo: solo se correlacionan entradas con "
                             "incertidumbre (distribución)")
    model.corr_matrix = gaussian_copula(names, pairs=triples, matrix=matrix)
    model.corr_names = names
    model.corr_sources = sources
    model.corr_unsourced = unsourced


def assumptions_list(data: dict) -> list[str]:
    """[supuestos].lista: lista de textos (se comprueba el tipo para que una errata no se lea letra a letra)."""
    items = data.get("supuestos", {}).get("lista", [])
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise ValueError("[supuestos].lista debe ser una lista de textos")
    return list(items)


def _check_cost(where: str, coste: dict) -> None:
    """La dosis y el precio van juntos (coste del producto = dosis × precio) y el modelo necesita algún coste."""
    has_dose = any(k in coste for k in DOSE_KEYS)
    if has_dose != ("precio_eur_kg" in coste):
        raise ValueError(f"{where}: la dosis y precio_eur_kg van juntos (coste del producto = dosis × precio / 1000); "
                         + ("falta precio_eur_kg" if has_dose else "falta la dosis (dosis_g_dia, dosis_mg_kg_ms o "
                                                                    "dosis_g_t)"))
    if not has_dose and not any(k in coste for k in ("otros_eur_dia", "fijo_eur_ciclo")):
        raise ValueError(f"{where}: el modelo necesita un coste: la dosis y precio_eur_kg del producto, otros_eur_dia o "
                         "fijo_eur_ciclo (sin coste, el ROI no tiene sentido)")


def load_farm_model(path: Path, central: str = "moda") -> FarmModel:
    check_central(central)
    data = tomllib.loads(Path(path).read_text("utf-8"))
    unknown = set(data) - _SECTIONS
    if unknown:
        raise ValueError(f"Secciones desconocidas en el modelo del ganadero: {sorted(unknown)}. Admitidas: "
                         f"{sorted(_SECTIONS)}")
    declared: set[str] = set()
    esc = data.get("escenario", {})
    coste = data.get("coste", {})
    _check_keys("[escenario]", esc, _ESCENARIO_KEYS)
    _check_keys("[coste]", coste, _COSTE_KEYS)
    _check_keys("[supuestos]", data.get("supuestos", {}), {"lista"})
    raw_benefits = data.get("beneficio", [])
    if not isinstance(raw_benefits, list):
        raise ValueError("[[beneficio]] debe ser una lista de tablas")
    benefits = [_parse_benefit(b, i, declared) for i, b in enumerate(raw_benefits, 1)]
    if not benefits:
        raise ValueError("El modelo necesita al menos un [[beneficio]]")
    names = [b.name for b in benefits]
    if len(set(names)) != len(names):
        raise ValueError("Los [[beneficio]] deben tener nombres distintos")
    _check_cost("[coste]", coste)
    ration = _parse_ration(data, declared)
    dose, basis = _parse_dose("[coste]", coste, declared, "coste")
    _check_dose_requirements("[coste]", basis, ration)
    for key in ("precio_eur_kg", "otros_eur_dia", "fijo_eur_ciclo", "dias"):
        if key in coste:
            declared.add(f"coste.{key}")
    if "dias" in esc:
        declared.add("escenario.dias")
    model = FarmModel(
        name=esc.get("nombre", Path(path).stem), species=esc.get("especie", ""), unit=esc.get("unidad", "animal"),
        days=parse_dist(esc.get("dias", 1)), dose=dose, dose_basis=basis,
        price_eur_kg=parse_dist(coste.get("precio_eur_kg", 0)), other_eur_day=parse_dist(coste.get("otros_eur_dia", 0)),
        fixed_eur_cycle=parse_dist(coste.get("fijo_eur_ciclo", 0)), benefits=benefits,
        assumptions=assumptions_list(data),
        cost_days=parse_dist(coste["dias"]) if "dias" in coste else None, ration=ration, declared=declared,
        central=central,
    )
    model.alternative = _parse_alternative(data, benefits, ration, declared)
    _parse_correlations(data, model)
    return model


# ---------------------------------------------------------------------------------------------- entradas
def inputs(model: FarmModel) -> list[tuple[str, str, Dist]]:
    """Entradas del modelo en orden de muestreo: (nombre, descripción, distribución). Los nombres se usan en las
    correlaciones, en la sensibilidad y en la lista de fuentes."""
    r = model.ration
    out = [("escenario.dias", "días del escenario", model.days)]
    if model.cost_days is not None:
        out.append(("coste.dias", "días de administración (coste)", model.cost_days))
    out += [(model.dose_key, f"dosis ({dosis.etiqueta(model.dose_basis, r.carrier, r.basis)})", model.dose),
            ("coste.precio_eur_kg", "precio del producto (€/kg)", model.price_eur_kg),
            ("coste.otros_eur_dia", "otros costes (€/animal/día)", model.other_eur_day),
            ("coste.fijo_eur_ciclo", "coste fijo (€/animal/ciclo)", model.fixed_eur_cycle)]
    if r.dmi is not None:
        out.append(("racion.ingestion_ms_kg_dia", "ingestión de MS de la ración (kg MS/animal/día)", r.dmi))
    if r.feed_intake is not None:
        out.append(("racion.ingestion_pienso_kg_dia",
                    f"ingestión de {dosis.pienso(r.carrier, r.basis)} (kg/animal/día)", r.feed_intake))
    if r.dm_fraction is not None:
        out.append(("racion.ms_pienso", "fracción de MS del pienso portador", r.dm_fraction))
    for i, b in enumerate(model.benefits, 1):
        out.append((f"beneficio[{i}].respuesta", f"respuesta en '{b.name}'", b.response))
        out.append((f"beneficio[{i}].valor_unitario", f"valor unitario de '{b.name}'", b.unit_value))
        if b.days is not None:
            out.append((f"beneficio[{i}].dias", f"días de '{b.name}'", b.days))
    alt = model.alternative
    if alt is not None and not alt.nothing:
        if alt.cost_days is not None:
            out.append(("alternativa.coste.dias", "alternativa: días de administración", alt.cost_days))
        out += [(f"alternativa.coste.{_dose_field(alt.dose_basis)}",
                 f"alternativa: dosis ({dosis.etiqueta(alt.dose_basis, r.carrier, r.basis)})", alt.dose),
                ("alternativa.coste.precio_eur_kg", "alternativa: precio (€/kg)", alt.price),
                ("alternativa.coste.otros_eur_dia", "alternativa: otros costes (€/animal/día)", alt.other),
                ("alternativa.coste.fijo_eur_ciclo", "alternativa: coste fijo (€/animal/ciclo)", alt.fixed)]
        for i, (b, d) in enumerate(zip(model.benefits, alt.responses, strict=True), 1):
            out.append((f"alternativa.respuesta[{i}]", f"alternativa: respuesta en '{b.name}'", d))
    return out


def input_table(model: FarmModel) -> list[dict]:
    """Entradas con su distribución, valor central, P10-P90 y fuente (para el informe y la trazabilidad)."""
    rows = []
    for key, label, d in inputs(model):
        rows.append({"entrada": key, "descripcion": label, "distribucion": d.describe(),
                     "central": d.central(model.central), "estadistico_central": model.central,
                     "p10": d.quantile(0.10), "p90": d.quantile(0.90), "fuente": d.source,
                     "declarada": key in model.declared})
    return rows


def declared_sources(model: FarmModel) -> list[tuple[str, str]]:
    """(dónde, fuente) de cada fuente declarada: entradas, correlaciones y alternativa (para comprobarlas en el
    registro)."""
    out = [(r["entrada"], r["fuente"]) for r in input_table(model) if r["fuente"]]
    out += [("correlaciones", s) for s in model.corr_sources]
    if model.alternative is not None and model.alternative.source:
        out.append(("alternativa", str(model.alternative.source)))
    return out


def sources_overview(model: FarmModel) -> dict:
    """Recuento de fuentes por etiqueta y entradas sin datos ([SUP], [DESC], sin fuente) entre las que más mueven el
    neto en el tornado."""
    return sources_summary(input_table(model), sensitivity(model))


def missing_sources(model: FarmModel) -> list[str]:
    """Entradas declaradas en el TOML sin fuente etiquetada ([PUB], [DER], [SUP], [DESC] o [HIP]), correlaciones sin
    fuente y [escenario].dias si se usa su valor por defecto (1 día) sin declararlo."""
    out = [r["entrada"] for r in input_table(model) if r["declarada"] and not source_has_label(r["fuente"])]
    uses_scenario_days = model.cost_days is None or any(b.per_day and b.days is None for b in model.benefits)
    if "escenario.dias" not in model.declared and uses_scenario_days:
        out.append("escenario.dias (valor por defecto de 1 día sin declarar)")
    return out + list(model.corr_unsourced)


# ---------------------------------------------------------------------------------------------- cálculo
def _draw(model: FarmModel, n: int, seed: int) -> dict:
    """Muestras de todas las entradas. Primero las independientes del producto (en orden), después el bloque
    correlacionado (cópula gaussiana) y al final las independientes de la alternativa: añadir una alternativa no
    cambia las simulaciones del producto."""
    if int(n) < 1:
        raise ValueError("El número de simulaciones debe ser >= 1")
    rng = np.random.default_rng(seed)
    items = inputs(model)
    correlated = set(model.corr_names)
    v = {}
    for key, _label, d in items:
        if key not in correlated and not key.startswith("alternativa."):
            v[key] = d.sample(rng, n)
    if model.corr_matrix is not None:
        u = copula_uniforms(rng, model.corr_matrix, n)
        dists = {key: d for key, _label, d in items}
        for j, key in enumerate(model.corr_names):
            v[key] = dists[key].ppf(u[:, j])
    for key, _label, d in items:
        if key not in correlated and key.startswith("alternativa."):
            v[key] = d.sample(rng, n)
    for key, x in v.items():
        if key.endswith(".dias") or key.endswith("precio_eur_kg"):
            bad = ~(np.asarray(x) >= 0)
            if np.any(bad):
                raise ValueError(f"{key} debe ser >= 0 (falla en {int(bad.sum())} de {n} simulaciones): usa una "
                                 "distribución acotada (pert, triangular, uniforme)")
    return v


def _central_values(model: FarmModel) -> dict:
    """Valor central de cada entrada con el mismo estadístico para todas (`model.central`: moda o mediana)."""
    return {key: d.central(model.central) for key, _label, d in inputs(model)}


def central_text(model: FarmModel) -> str:
    return CENTRAL_TEXT[model.central]


def _feed(model: FarmModel, v: dict):
    r = model.ration
    if r.carrier is None:
        return None
    return dosis.ingestion_portador(r.carrier, r.basis, v.get("racion.ingestion_ms_kg_dia"),
                                    v.get("racion.ingestion_pienso_kg_dia"), v.get("racion.ms_pienso"))


def _dmi(model: FarmModel, v: dict):
    """Ingestión de MS declarada o, si falta, deducida de la ración completa (nutria.dosis.ingestion_ms_deducida)."""
    dmi = v.get("racion.ingestion_ms_kg_dia")
    if dmi is not None:
        return dmi
    r = model.ration
    return dosis.ingestion_ms_deducida(r.carrier, r.basis, v.get("racion.ingestion_pienso_kg_dia"),
                                       v.get("racion.ms_pienso"))


def _ratio(num, den):
    if isinstance(num, np.ndarray) or isinstance(den, np.ndarray):
        num, den = np.broadcast_arrays(np.asarray(num, dtype=float), np.asarray(den, dtype=float))
        return np.divide(num, den, out=np.full(num.shape, np.nan), where=den > 0)
    return num / den if den > 0 else float("nan")


def _evaluate(model: FarmModel, v: dict) -> dict:
    days = v["escenario.dias"]
    cost_days = v.get("coste.dias", days)
    feed = _feed(model, v)
    dmi = _dmi(model, v)
    dose_g_d = dosis.a_g_animal_dia(v[model.dose_key], model.dose_basis, dmi, feed)
    price = v["coste.precio_eur_kg"]
    product_day = dose_g_d / 1000.0 * price
    cost = (product_day + v["coste.otros_eur_dia"]) * cost_days + v["coste.fijo_eur_ciclo"]
    benefit = 0.0
    parts = {}
    for i, b in enumerate(model.benefits, 1):
        value = v[f"beneficio[{i}].respuesta"] * v[f"beneficio[{i}].valor_unitario"] * (
            v.get(f"beneficio[{i}].dias", days) if b.per_day else 1.0)
        parts[b.name] = value
        benefit = benefit + value
    net = benefit - cost
    dose_g_t = None if feed is None else dosis.desde_g_animal_dia(dose_g_d, "g_t", dmi, feed)
    out = {"beneficio": benefit, "coste": cost, "neto": net, "roi": _ratio(benefit, cost), "componentes": parts,
           "dosis_g_animal_dia": dose_g_d,
           "dosis_mg_kg_ms": None if dmi is None else dosis.desde_g_animal_dia(dose_g_d, "mg_kg_ms", dmi, feed),
           "dosis_g_t": dose_g_t, "coste_producto_animal_dia": product_day,
           "coste_producto_tonelada": None if dose_g_t is None else dose_g_t * price / 1000.0}
    alt = model.alternative
    if alt is not None:
        if alt.nothing:
            out["neto_alternativa"] = net * 0.0
        else:
            alt_dose = dosis.a_g_animal_dia(v[f"alternativa.coste.{_dose_field(alt.dose_basis)}"], alt.dose_basis,
                                            dmi, feed)
            alt_day = alt_dose / 1000.0 * v["alternativa.coste.precio_eur_kg"] + v["alternativa.coste.otros_eur_dia"]
            alt_cost = alt_day * v.get("alternativa.coste.dias", cost_days) + v["alternativa.coste.fijo_eur_ciclo"]
            alt_benefit = 0.0
            for i, b in enumerate(model.benefits, 1):
                alt_benefit = alt_benefit + v[f"alternativa.respuesta[{i}]"] * v[f"beneficio[{i}].valor_unitario"] * (
                    v.get(f"beneficio[{i}].dias", days) if b.per_day else 1.0)
            out["neto_alternativa"] = alt_benefit - alt_cost
            out["coste_alternativa"] = alt_cost
        out["diferencia_frente_alternativa"] = net - out["neto_alternativa"]
    return out


def _simulate(model: FarmModel, n: int, seed: int) -> dict:
    """Una sola simulación compartida por scenarios() y monte_carlo(): con la misma (n, semilla) dan los mismos
    percentiles. Los días sin declarar reutilizan la muestra de [escenario].dias (mismo animal y periodo)."""
    res = _evaluate(model, _draw(model, n, seed))
    if not np.all(np.isfinite(np.asarray(res["neto"], dtype=float))):
        raise ValueError("El beneficio neto simulado tiene valores no finitos: revisa las entradas")
    return res


def _pct(x: np.ndarray | None) -> dict | None:
    if x is None:
        return None
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if not x.size:
        return None
    return {f"p{q:02d}": float(np.percentile(x, q)) for q in _PERCENTILES}


def scenarios(model: FarmModel, n: int = 10000, seed: int = 42) -> dict:
    """Escenarios coherentes: P10, P50 y P90 del beneficio neto simulado (conservador, intermedio, optimista)
    y la fila 'valores_centrales' (evaluación determinista con la moda de cada entrada)."""
    net = _pct(_simulate(model, n, seed)["neto"])
    if net is None:
        raise ValueError("El beneficio neto simulado no tiene valores finitos: revisa las entradas")
    out = {}
    for name, q in SCENARIO_PERCENTILES:
        out[name] = {"percentil": q, "neto": net[f"p{q:02d}"],
                     "definicion": f"P{q} del beneficio neto simulado ({n} simulaciones, semilla {seed})"}
    res = _evaluate(model, _central_values(model))
    central = {k: (None if x is None else float(x)) for k, x in res.items() if k != "componentes"}
    central["componentes"] = {name: float(y) for name, y in res["componentes"].items()}
    central["estadistico_central"] = model.central
    central["definicion"] = (f"Evaluación determinista con la {central_text(model)}; no es un percentil del neto")
    out["valores_centrales"] = central
    return out


def breakeven(model: FarmModel) -> list[dict]:
    """Respuesta umbral de cada beneficio con valores centrales coherentes (coste, valor unitario y días con el MISMO
    estadístico central, `model.central`): (a) con los demás beneficios a cero y (b) con los demás beneficios en su
    valor central."""
    base = _central_values(model)
    res = _evaluate(model, base)
    cost = float(res["coste"])
    out = []
    for i, b in enumerate(model.benefits, 1):
        unit = base[f"beneficio[{i}].valor_unitario"] * (
            base.get(f"beneficio[{i}].dias", base["escenario.dias"]) if b.per_day else 1.0)
        others = sum(float(x) for name, x in res["componentes"].items() if name != b.name)
        alone = cost / unit if unit > 0 else float("nan")
        with_others = (cost - others) / unit if unit > 0 else float("nan")
        out.append({"beneficio": b.name, "por_dia": b.per_day, "valor": alone,
                    "valor_con_otros_centrales": with_others,
                    "respuesta_central": base[f"beneficio[{i}].respuesta"], "coste_central": cost,
                    "estadistico_central": model.central,
                    "nota": "Respuesta mínima en este beneficio para cubrir el coste central. 'valor': SOLO con este "
                            "beneficio (EXCLUYE los demás, a cero); 'valor_con_otros_centrales': con el resto en su "
                            f"valor central. Coste, valor unitario y días con la {central_text(model)}."})
    return out


def sensitivity(model: FarmModel) -> list[dict]:
    """Tornado: neto con cada entrada con incertidumbre en su P10 y su P90 y las demás en su valor central."""
    base = _central_values(model)
    rows = []
    for key, label, d in inputs(model):
        if not d.stochastic or key.startswith("alternativa."):
            continue
        lo, hi = d.quantile(0.10), d.quantile(0.90)
        try:
            n_lo = float(_evaluate(model, {**base, key: lo})["neto"])
            n_hi = float(_evaluate(model, {**base, key: hi})["neto"])
        except ValueError as exc:
            rows.append({"entrada": key, "descripcion": label, "p10": lo, "p90": hi, "neto_p10": None,
                         "neto_p90": None, "amplitud": None, "error": str(exc)})
            continue
        rows.append({"entrada": key, "descripcion": label, "p10": lo, "p90": hi, "neto_p10": n_lo,
                     "neto_p90": n_hi, "amplitud": abs(n_hi - n_lo)})
    rows.sort(key=lambda r: -(r["amplitud"] if r["amplitud"] is not None else math.inf))
    return rows


def monte_carlo(model: FarmModel, n: int = 10000, seed: int = 42) -> dict:
    res = _simulate(model, n, seed)
    net = res["neto"]
    thresholds = breakeven(model)
    out = {
        "simulaciones": n, "semilla": seed,
        "neto_por_animal": _pct(net), "beneficio_por_animal": _pct(res["beneficio"]),
        "coste_por_animal": _pct(res["coste"]), "roi": _pct(res["roi"]),
        "p_neto_positivo": float(np.mean(net > 0)),
        "coste_producto_animal_dia": _pct(res["coste_producto_animal_dia"]),
        "coste_producto_tonelada": _pct(res["coste_producto_tonelada"]),
        "dosis_g_animal_dia": _pct(res["dosis_g_animal_dia"]),
        "respuesta_umbral": thresholds[0], "respuestas_umbral": thresholds,
        "correlaciones": ({"entradas": model.corr_names, "matriz": model.corr_matrix.tolist(),
                           "fuentes": model.corr_sources} if model.corr_matrix is not None else None),
    }
    alt = model.alternative
    if alt is not None:
        diff = res["diferencia_frente_alternativa"]
        out["alternativa"] = {"nombre": alt.name, "no_hacer_nada": alt.nothing, "fuente": alt.source,
                              "neto_alternativa": _pct(res["neto_alternativa"]), "diferencia": _pct(diff),
                              "p_producto_mejor": float(np.mean(diff > 0))}
    return out


def dose_g_day_quantiles(model: FarmModel, qs=(5, 95), n: int = 20000, seed: int = 42) -> tuple[float, ...]:
    """Percentiles de la dosis en g/animal/día simulada (convertida con las ingestiones muestreadas)."""
    v = _draw(model, n, seed)
    g = dosis.a_g_animal_dia(v[model.dose_key], model.dose_basis, _dmi(model, v), _feed(model, v))
    return tuple(float(x) for x in np.percentile(np.asarray(g, dtype=float) * np.ones(n), list(qs)))


def dose_summary(model: FarmModel) -> dict:
    """Dosis y coste del producto en valores centrales, en las tres bases cuando las entradas lo permiten."""
    res = _evaluate(model, _central_values(model))
    r = model.ration
    out = {"base_declarada": model.dose_basis,
           "g_animal_dia": float(res["dosis_g_animal_dia"]),
           "mg_kg_ms": None if res["dosis_mg_kg_ms"] is None else float(res["dosis_mg_kg_ms"]),
           "g_t": None if res["dosis_g_t"] is None else float(res["dosis_g_t"]),
           "pienso_portador": dosis.pienso(r.carrier, r.basis) if r.carrier else None,
           "coste_producto_eur_animal_dia": float(res["coste_producto_animal_dia"]),
           "coste_producto_eur_t": None if res["coste_producto_tonelada"] is None
           else float(res["coste_producto_tonelada"])}
    return out


# ---------------------------------------------------------------------------------------------- informe
def _days_for(model: FarmModel, b: Benefit) -> Dist:
    return b.days if b.days is not None else model.days


def _days_label(d: Dist, stat: str = "moda") -> str:
    text = f"{d.central(stat):g} días"
    return text if d.fixed is not None else f"{text} (valor central; distribución {d.kind})"


def _periods_line(model: FarmModel) -> str:
    parts = ["coste " + _days_label(model.cost_days if model.cost_days is not None else model.days, model.central)]
    for b in model.benefits:
        parts.append(f"'{b.name}' " + (_days_label(_days_for(model, b), model.central) if b.per_day else "por ciclo"))
    return "Periodos: " + "; ".join(parts)


def _dose_lines(model: FarmModel, mc: dict, unit: str) -> list[str]:
    ds = dose_summary(model)
    parts = [f"{ds['g_animal_dia']:.6g} g/{unit}/día"]
    parts.append(f"{ds['mg_kg_ms']:.6g} mg/kg de MS de la ración" if ds["mg_kg_ms"] is not None
                 else "mg/kg de MS: [DESC] declara [racion].ingestion_ms_kg_dia")
    parts.append(f"{ds['g_t']:.6g} g/t de {ds['pienso_portador']}" if ds["g_t"] is not None
                 else "g/t: [DESC] declara el pienso portador y su ingestión en [racion]")
    declared_in = dosis.etiqueta(model.dose_basis, model.ration.carrier, model.ration.basis)
    lines = [f"Dosis [DER] (valores centrales; declarada en {declared_in}; conversión con nutria.dosis): "
             + "; ".join(parts)]
    c = mc["coste_producto_animal_dia"]
    text = (f"Coste del producto por {unit} y día: central {ds['coste_producto_eur_animal_dia']:.3f} € "
            f"[P10 {c['p10']:.3f}; P90 {c['p90']:.3f}]")
    t = mc["coste_producto_tonelada"]
    if t is not None:
        text += (f"; por tonelada de {ds['pienso_portador']}: central {ds['coste_producto_eur_t']:.2f} €/t "
                 f"[P10 {t['p10']:.2f}; P90 {t['p90']:.2f}]")
    else:
        text += "; por tonelada de pienso: [DESC] declara el pienso portador y su ingestión en [racion]"
    lines.append(text + " (dosis × precio / 1000)")
    return lines


def _breakeven_lines(model: FarmModel, mc: dict) -> list[str]:
    lines = [f"Respuesta umbral (valores centrales coherentes: coste, valor unitario y días con la {central_text(model)}"
             "; cambia el estadístico con --valor-central):"]
    for t in mc["respuestas_umbral"]:
        per = "unidades/animal/día" if t["por_dia"] else "unidades/animal/ciclo"
        if not math.isfinite(t["valor"]):
            lines.append(f"- '{t['beneficio']}': no calculable (valor unitario × días central <= 0)")
            continue
        with_others = t["valor_con_otros_centrales"]
        others = (f"{with_others:.3f}" if with_others > 0 else
                  f"{with_others:.3f} (los demás beneficios ya cubren el coste)")
        lines.append(f"- '{t['beneficio']}': {t['valor']:.3f} {per} solo con este beneficio (EXCLUYE los demás "
                     f"beneficios, a cero); {others} con los demás en su valor central; respuesta central "
                     f"declarada {t['respuesta_central']:.3f}")
    return lines


def _sensitivity_lines(model: FarmModel, unit: str, top: int = 10) -> list[str]:
    rows = sensitivity(model)
    if not rows:
        return ["Sensibilidad: todas las entradas son fijas (sin incertidumbre)."]
    lines = [f"Sensibilidad (tornado): neto €/{unit} con cada entrada en su P10 y su P90 y las demás en su valor "
             "central (una entrada cada vez; no aplica las correlaciones)",
             f"  {'Entrada':<44}{'P10':>11}{'P90':>11}{'Neto P10':>11}{'Neto P90':>11}{'Amplitud':>10}"]
    for r in rows[:top]:
        if r["amplitud"] is None:
            lines.append(f"  {r['entrada']:<44}{r['p10']:>11.4g}{r['p90']:>11.4g}  no evaluable: {r['error']}")
            continue
        lines.append(f"  {r['entrada']:<44}{r['p10']:>11.4g}{r['p90']:>11.4g}{r['neto_p10']:>11.2f}"
                     f"{r['neto_p90']:>11.2f}{r['amplitud']:>10.2f}")
    if len(rows) > top:
        lines.append(f"  ... y {len(rows) - top} entradas más con menor amplitud (salida JSON: 'sensibilidad')")
    return lines


def _alternative_lines(model: FarmModel, mc: dict, unit: str) -> list[str]:
    alt = mc.get("alternativa")
    if alt is None:
        return ["Alternativa: no declarada; el neto se mide frente a no usar el producto (no hacer nada)."]
    if alt["no_hacer_nada"]:
        return [f"Alternativa '{alt['nombre']}': no hacer nada (neto 0); el neto del producto ya es la diferencia "
                "frente a ella."]
    d, a = alt["diferencia"], alt["neto_alternativa"]
    return [f"Comparación con la alternativa '{alt['nombre']}' (mismas simulaciones; valores unitarios, días y ración "
            "compartidos)" + (f"; fuente: {alt['fuente']}" if alt["fuente"] else ""),
            f"  Neto de la alternativa €/{unit}: P10 {a['p10']:.2f}; P50 {a['p50']:.2f}; P90 {a['p90']:.2f}",
            f"  Diferencia (producto − alternativa): P10 {d['p10']:.2f}; P50 {d['p50']:.2f}; P90 {d['p90']:.2f}; "
            f"P(producto > alternativa) = {alt['p_producto_mejor']:.2f}"]


def _sources_lines(model: FarmModel) -> list[str]:
    lines = ["Entradas y fuentes:"]
    for r in input_table(model):
        origin = r["fuente"] if r["fuente"] else ("SIN FUENTE (decláralo o márcalo [SUP])" if r["declarada"]
                                                   else "valor por defecto (no declarado)")
        lines.append(f"- {r['entrada']}: {r['distribucion']} | {origin}")
    if model.corr_matrix is not None:
        lines.append("- correlaciones (cópula gaussiana) entre " + ", ".join(model.corr_names) + " | "
                     + ("; ".join(model.corr_sources) if model.corr_sources else "SIN FUENTE"))
    missing = missing_sources(model)
    if missing:
        lines.append(f"AVISO: {len(missing)} entradas sin fuente etiquetada ([PUB], [DER], [SUP], [DESC]) o con valor "
                     "por defecto no neutro sin declarar: " + ", ".join(missing) + ". `--exigir-fuentes` las rechaza.")
    lines.append(sources_summary_line(sources_overview(model), "el neto"))
    return lines


def report(model: FarmModel, n: int = 10000, seed: int = 42) -> str:
    sc = scenarios(model, n, seed)
    mc = monte_carlo(model, n, seed)
    unit = model.unit
    lines = [f"Modelo del ganadero: {model.name} ({model.species}, por {unit})", _periods_line(model)]
    lines += _dose_lines(model, mc, unit)
    lines += ["", f"Escenarios coherentes: percentiles del beneficio neto simulado ({n} simulaciones, semilla {seed})",
              f"{'Escenario':<14}{'Percentil':>10}{f'Neto €/{unit}':>16}{'¿Neto > 0?':>12}"]
    for name, q in SCENARIO_PERCENTILES:
        value = sc[name]["neto"]
        lines.append(f"{name:<14}{f'P{q}':>10}{value:>16.2f}{'sí' if value > 0 else 'no':>12}")
    c = sc["valores_centrales"]
    lines += ["", f"Valores centrales ({central_text(model)}): "
                  f"beneficio {c['beneficio']:.2f} €; coste {c['coste']:.2f} €; neto {c['neto']:.2f} €; "
                  f"ROI (beneficio/coste) {c['roi']:.2f}"]
    neto, roi = mc["neto_por_animal"], mc["roi"]
    corr = ("entradas independientes salvo las correlaciones declaradas, con cópula gaussiana: "
            + ", ".join(model.corr_names)) if model.corr_matrix is not None else \
        "entradas independientes, sin correlaciones declaradas"
    lines += ["", f"Monte Carlo [{corr}]: P(neto > 0) = {mc['p_neto_positivo']:.2f} (fracción de simulaciones con "
                  f"ROI > 1); neto P5 {neto['p05']:.2f} €, P95 {neto['p95']:.2f} €"]
    if roi is not None:
        lines.append(f"ROI (beneficio/coste): P10 {roi['p10']:.2f}; P50 {roi['p50']:.2f}; P90 {roi['p90']:.2f}")
    lines += [""] + _breakeven_lines(model, mc)
    lines += [""] + _sensitivity_lines(model, unit)
    lines += [""] + _alternative_lines(model, mc, unit)
    lines += ["", "Cómo leer los escenarios: cada uno es un percentil del neto simulado, no una combinación de "
                  "percentiles de las entradas. Los valores centrales son una referencia determinista y pueden "
                  "diferir del P50."]
    if model.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in model.assumptions]
    lines += [""] + _sources_lines(model)
    lines += ["", "Las entradas deben proceder de fuentes registradas o marcarse como [SUP]; el modelo no "
                  "sustituye a un ensayo de campo."]
    return "\n".join(lines)
