"""Conversión de dosis entre bases de expresión: g/animal/día, mg/kg de MS de la ración y g/t de pienso.

Bases (CLAUDE.md: "unidades SI con base explícita"):
- `g_animal_dia`: gramos de producto por animal y día;
- `mg_kg_ms`: miligramos de producto por kg de materia seca (MS) de la ración total ingerida;
- `g_t`: gramos de producto por tonelada del pienso portador, que se declara: ración completa (`racion_completa`) o
  concentrado (`concentrado`), en base tal cual (`tal_cual`) o en MS (`ms`).

Relaciones (I_MS: ingestión de MS de la ración, kg MS/animal/día; I_p: ingestión del pienso portador en su base,
kg/animal/día):

    g/animal/día = mg/kg MS × I_MS / 1000 = g/t × I_p / 1000;   1 mg/kg = 1 g/t (misma proporción en masa).

Para la ración completa, I_p = I_MS en base MS e I_MS / MS en base tal cual (MS: fracción de MS del pienso); a la
inversa, si solo se declara la ingestión de la ración completa, I_MS = I_p (base MS) o I_p × MS (tal cual): es la misma
ración, no un supuesto, y la salida lo indica. Para el concentrado, I_p se declara siempre (kg de concentrado/animal/día
en su base). Una entrada que no se usaría (ingestión o MS del pienso sin pienso portador, MS del pienso en base MS) es
un error, no se ignora.

Coste del producto: por animal y día = g/animal/día × €/kg / 1000; por tonelada de pienso portador = g/t × €/kg / 1000.

Las ingestiones son entradas que se declaran con fuente o como [SUP]: `nutria` no predice la ingestión ni implementa
sistemas de racionamiento (NASEM, INRAE, FEDNA). Todas las funciones admiten escalares o arrays de numpy (Monte Carlo) y
fallan con ValueError ante entradas incoherentes en lugar de devolver un valor dudoso.
"""

from __future__ import annotations

import math

import numpy as np

BASES = ("g_animal_dia", "mg_kg_ms", "g_t")
PORTADORES = ("racion_completa", "concentrado")
BASES_PIENSO = ("tal_cual", "ms")
ETIQUETAS_BASE = {"g_animal_dia": "g/animal/día", "mg_kg_ms": "mg/kg de MS de la ración"}
ETIQUETAS_PORTADOR = {"racion_completa": "ración completa", "concentrado": "concentrado"}
ETIQUETAS_BASE_PIENSO = {"tal_cual": "tal cual", "ms": "MS"}
# Tolerancia relativa para dar por iguales dos ingestiones declaradas de forma redundante.
TOLERANCIA = 0.01


def pienso(portador: str | None, base_pienso: str | None) -> str:
    """Nombre del pienso portador, p. ej. "concentrado (tal cual)"."""
    if not (portador and base_pienso):
        return "pienso"
    return f"{ETIQUETAS_PORTADOR[portador]} ({ETIQUETAS_BASE_PIENSO[base_pienso]})"


def etiqueta(base: str, portador: str | None = None, base_pienso: str | None = None) -> str:
    if base == "g_t":
        return f"g/t de {pienso(portador, base_pienso)}"
    return ETIQUETAS_BASE[base]


def _arr(x):
    return None if x is None else np.asarray(x, dtype=float)


def _ret(x, scalar: bool):
    if x is None:
        return None
    return float(x) if scalar else x


def _count(mask: np.ndarray) -> str:
    mask = np.atleast_1d(mask)
    return f"{int(np.sum(mask))} de {mask.size}" if mask.size > 1 else "el valor declarado"


def _positive(name: str, x) -> None:
    if x is None:
        return
    bad = ~(np.atleast_1d(x) > 0) | ~np.isfinite(np.atleast_1d(x))
    if np.any(bad):
        raise ValueError(f"{name} debe ser > 0 (falla en {_count(bad)}); si es una distribución, usa una acotada "
                         "(pert, triangular, uniforme)")


def _check_choice(name: str, value, options) -> None:
    if value not in options:
        raise ValueError(f"{name} no válido: {value!r}. Opciones: {', '.join(options)}")


def validar_racion(ingestion_ms=None, portador=None, base_pienso=None, ingestion_pienso=None, ms_pienso=None) -> None:
    """Comprueba tipos, rangos y que el pienso portador esté completo si se declara."""
    if (portador is None) != (base_pienso is None):
        raise ValueError("El pienso portador exige declarar a la vez el portador (racion_completa o concentrado) y "
                         "la base del g/t (tal_cual o ms)")
    if portador is not None:
        _check_choice("Portador", portador, PORTADORES)
        _check_choice("Base del pienso", base_pienso, BASES_PIENSO)
    elif ingestion_pienso is not None or ms_pienso is not None:
        raise ValueError("La ingestión del pienso portador y su fracción de MS (ms_pienso) exigen declarar el pienso "
                         "portador (racion_completa o concentrado) y su base (tal_cual o ms): sin él no se usarían")
    if base_pienso == "ms" and ms_pienso is not None:
        raise ValueError("ms_pienso solo se usa con el pienso en base tal_cual: en base MS no interviene en la "
                         "conversión; quítalo o declara la base tal_cual")
    _positive("La ingestión de MS (kg MS/animal/día)", _arr(ingestion_ms))
    _positive("La ingestión del pienso portador (kg/animal/día)", _arr(ingestion_pienso))
    ms = _arr(ms_pienso)
    if ms is not None:
        bad = ~((np.atleast_1d(ms) > 0) & (np.atleast_1d(ms) <= 1))
        if np.any(bad):
            raise ValueError(f"La fracción de MS del pienso (ms_pienso) debe estar en (0, 1] (falla en {_count(bad)})")


def ingestion_portador(portador: str, base_pienso: str, ingestion_ms=None, ingestion_pienso=None, ms_pienso=None):
    """Ingestión del pienso portador (kg/animal/día) en la base del g/t, o None si no puede calcularse.

    Si se declaran ingestiones redundantes deben ser coherentes (tolerancia 1 %): ración completa en MS = ingestión de
    MS; ración completa tal cual × MS = ingestión de MS; el concentrado en MS no puede superar la ingestión de MS."""
    validar_racion(ingestion_ms, portador, base_pienso, ingestion_pienso, ms_pienso)
    scalar = all(np.ndim(x) == 0 for x in (ingestion_ms, ingestion_pienso, ms_pienso) if x is not None)
    dmi, feed, ms = _arr(ingestion_ms), _arr(ingestion_pienso), _arr(ms_pienso)
    if portador == "racion_completa":
        if base_pienso == "ms":
            if feed is not None and dmi is not None:
                bad = np.abs(feed - dmi) > TOLERANCIA * dmi
                if np.any(bad):
                    raise ValueError("Ración completa en base MS: la ingestión del pienso es la ingestión de MS, pero "
                                     f"los valores declarados difieren (en {_count(bad)}); declara solo uno")
            return _ret(feed if feed is not None else dmi, scalar)
        if feed is not None:
            if dmi is not None and ms is not None:
                bad = np.abs(feed * ms - dmi) > TOLERANCIA * dmi
                if np.any(bad):
                    raise ValueError("Ración completa tal cual: ingestión del pienso × fracción de MS debe ser la "
                                     f"ingestión de MS (difiere en {_count(bad)}); declara solo dos de los tres")
            elif dmi is not None:
                bad = feed < dmi * (1 - TOLERANCIA)
                if np.any(bad):
                    raise ValueError("Ración completa tal cual: la ingestión tal cual no puede ser menor que la de MS "
                                     f"(falla en {_count(bad)})")
            return _ret(feed, scalar)
        if dmi is not None and ms is not None:
            return _ret(dmi / ms, scalar)
        return None
    if feed is None:
        return None
    if dmi is not None:
        feed_dm = feed if base_pienso == "ms" else (feed * ms if ms is not None else None)
        if feed_dm is not None:
            bad = feed_dm > dmi * (1 + TOLERANCIA)
            if np.any(bad):
                raise ValueError("La MS ingerida del concentrado supera la ingestión de MS de la ración "
                                 f"(en {_count(bad)}): entradas incoherentes")
    return _ret(feed, scalar)


def ingestion_ms_deducida(portador: str | None, base_pienso: str | None, ingestion_pienso=None, ms_pienso=None):
    """Ingestión de MS de la ración deducida de la ración completa (la ración completa ES la ración): en base MS, su
    ingestión; tal cual, su ingestión × ms_pienso. None si el portador es un concentrado o faltan datos."""
    if portador != "racion_completa" or ingestion_pienso is None:
        return None
    scalar = all(np.ndim(x) == 0 for x in (ingestion_pienso, ms_pienso) if x is not None)
    feed = _arr(ingestion_pienso)
    if base_pienso == "ms":
        return _ret(feed, scalar)
    if ms_pienso is None:
        return None
    return _ret(feed * _arr(ms_pienso), scalar)


def a_g_animal_dia(valor, desde: str, ingestion_ms=None, ingestion_p=None):
    """Dosis en g/animal/día a partir de cualquier base. `ingestion_p`: ingestión del pienso portador en su base."""
    _check_choice("Base de la dosis", desde, BASES)
    v = _arr(valor)
    bad = ~(np.atleast_1d(v) >= 0) | ~np.isfinite(np.atleast_1d(v))
    if np.any(bad):
        raise ValueError(f"La dosis debe ser >= 0 (falla en {_count(bad)})")
    scalar = np.ndim(valor) == 0 and all(np.ndim(x) == 0 for x in (ingestion_ms, ingestion_p) if x is not None)
    if desde == "g_animal_dia":
        return _ret(v, scalar)
    if desde == "mg_kg_ms":
        if ingestion_ms is None:
            raise ValueError("Una dosis en mg/kg de MS exige la ingestión de MS (kg MS/animal/día) para pasarla a "
                             "g/animal/día")
        _positive("La ingestión de MS (kg MS/animal/día)", _arr(ingestion_ms))
        return _ret(v * _arr(ingestion_ms) / 1000.0, scalar)
    if ingestion_p is None:
        raise ValueError("Una dosis en g/t exige el pienso portador (racion_completa o concentrado), su base (tal_cual "
                         "o ms) y su ingestión (o, para la ración completa, la ingestión de MS y, tal cual, ms_pienso)")
    _positive("La ingestión del pienso portador (kg/animal/día)", _arr(ingestion_p))
    return _ret(v * _arr(ingestion_p) / 1000.0, scalar)


def desde_g_animal_dia(g_dia, hacia: str, ingestion_ms=None, ingestion_p=None):
    """Dosis en la base `hacia` a partir de g/animal/día; None si falta la ingestión necesaria."""
    _check_choice("Base de la dosis", hacia, BASES)
    scalar = np.ndim(g_dia) == 0 and all(np.ndim(x) == 0 for x in (ingestion_ms, ingestion_p) if x is not None)
    g = _arr(g_dia)
    if hacia == "g_animal_dia":
        return _ret(g, scalar)
    if hacia == "mg_kg_ms":
        return None if ingestion_ms is None else _ret(g * 1000.0 / _arr(ingestion_ms), scalar)
    return None if ingestion_p is None else _ret(g * 1000.0 / _arr(ingestion_p), scalar)


def convertir(valor: float, desde: str, ingestion_ms: float | None = None, portador: str | None = None,
              base_pienso: str | None = None, ingestion_pienso: float | None = None, ms_pienso: float | None = None,
              precio_eur_kg: float | None = None) -> dict:
    """Conversión de una dosis (escalar) a las tres bases, con el coste del producto si se da el precio.

    Lo que no puede calcularse por falta de una entrada se devuelve como None con el motivo en "faltan" ([DESC]);
    nunca se rellena con un supuesto."""
    validar_racion(ingestion_ms, portador, base_pienso, ingestion_pienso, ms_pienso)
    if desde == "g_t" and portador is None:
        raise ValueError("Una dosis en g/t exige declarar el pienso portador (--portador) y su base (--base)")
    feed = ingestion_portador(portador, base_pienso, ingestion_ms, ingestion_pienso, ms_pienso) if portador else None
    dmi = ingestion_ms
    derived = None
    if dmi is None:
        derived = ingestion_ms_deducida(portador, base_pienso, ingestion_pienso, ms_pienso)
        dmi = derived
    g = a_g_animal_dia(valor, desde, dmi, feed)
    out = {b: desde_g_animal_dia(g, b, dmi, feed) for b in BASES}
    faltan = {}
    if out["mg_kg_ms"] is None:
        faltan["mg_kg_ms"] = "falta la ingestión de MS de la ración (kg MS/animal/día)"
    if out["g_t"] is None:
        faltan["g_t"] = ("falta el pienso portador y su base" if portador is None else
                         "falta la ingestión del pienso portador (o, para la ración completa tal cual, ms_pienso)")
    res = {"entrada": {"valor": float(valor), "base": desde, "ingestion_ms_kg_dia": ingestion_ms,
                       "portador": portador, "base_pienso": base_pienso, "ingestion_pienso_kg_dia": ingestion_pienso,
                       "ms_pienso": ms_pienso, "precio_eur_kg": precio_eur_kg},
           "ingestion_portador_kg_dia": feed, "ingestion_ms_deducida_kg_dia": derived, "dosis": out, "faltan": faltan,
           "etiquetas": {"g_animal_dia": etiqueta("g_animal_dia"), "mg_kg_ms": etiqueta("mg_kg_ms"),
                         "g_t": etiqueta("g_t", portador, base_pienso)}}
    if precio_eur_kg is not None:
        if not (math.isfinite(precio_eur_kg) and precio_eur_kg >= 0):
            raise ValueError("El precio (€/kg de producto) debe ser un número finito >= 0")
        res["coste"] = {"eur_animal_dia": out["g_animal_dia"] * precio_eur_kg / 1000.0,
                        "eur_t_pienso": None if out["g_t"] is None else out["g_t"] * precio_eur_kg / 1000.0}
    return res


def texto_conversion(res: dict) -> str:
    e = res["entrada"]
    lab = res["etiquetas"]
    lines = [f"Conversión de dosis [DER] (nutria dosis convertir): {e['valor']:g} {lab[e['base']]}"]
    racion = []
    if e["ingestion_ms_kg_dia"] is not None:
        racion.append(f"ingestión de MS {e['ingestion_ms_kg_dia']:g} kg MS/animal/día")
    if e["portador"]:
        racion.append(f"pienso portador: {pienso(e['portador'], e['base_pienso'])}")
    if e["ingestion_pienso_kg_dia"] is not None:
        racion.append(f"ingestión del portador {e['ingestion_pienso_kg_dia']:g} kg/animal/día")
    if e["ms_pienso"] is not None:
        racion.append(f"MS del portador {e['ms_pienso']:g}")
    if racion:
        lines.append("Entradas: " + "; ".join(racion))
    if res.get("ingestion_ms_deducida_kg_dia") is not None:
        lines.append(f"Ingestión de MS deducida de la ración completa: {res['ingestion_ms_deducida_kg_dia']:g} kg "
                     "MS/animal/día (la ración completa es toda la ración)")
    for base in BASES:
        value = res["dosis"][base]
        shown = f"{value:.6g}" if value is not None else f"[DESC] {res['faltan'][base]}"
        lines.append(f"  {lab[base]:<38} {shown}")
    if "coste" in res:
        c = res["coste"]
        lines.append(f"Coste del producto a {e['precio_eur_kg']:g} €/kg: {c['eur_animal_dia']:.4f} €/animal/día; "
                     + (f"{c['eur_t_pienso']:.2f} €/t de {pienso(e['portador'], e['base_pienso'])}"
                        if c["eur_t_pienso"] is not None
                        else "por tonelada: [DESC] " + res["faltan"]["g_t"]))
    lines.append("Fórmulas: g/animal/día = mg/kg MS × ingestión de MS / 1000 = g/t × ingestión del portador / 1000; "
                 "coste = dosis × €/kg / 1000. Las ingestiones y el precio son entradas: indícalas con fuente o como "
                 "[SUP]. nutria no predice la ingestión (el racionamiento queda fuera de alcance).")
    return "\n".join(lines)
