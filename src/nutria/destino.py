"""Destino ruminal: degradabilidad efectiva, fracción que escapa a la degradación y dosis que sale del rumen.

Modelo de Ørskov y McDonald (1979; cita completa en REFERENCIAS):

    DE = a + b·kd/(kd + kp)

- a: fracción que el modelo cuenta como degradada por completo en el rumen (no depende de kp);
- b: fracción que se degrada con cinética de primer orden a la tasa kd;
- 1 - a - b: fracción que el modelo cuenta como no degradable;
- kd: tasa de degradación de b y kp: tasa de paso desde el rumen, ambas en h⁻¹ (fracción por hora).

a y b son fracciones entre 0 y 1, no porcentajes. La fracción que escapa a la degradación ruminal es 1 - DE.
Para un compuesto degradable con cinética de primer orden (a = 0, b = 1) es kp/(kd + kp).

Los resultados son [DER] (cálculo propio) y valen lo que valgan las entradas: a, b, kd y kp necesitan
fuente registrada o la etiqueta [SUP]. El módulo no incluye valores por defecto ni rangos "típicos".
"""

from __future__ import annotations

import math

REFERENCIAS = (
    "Ørskov ER, McDonald I. 1979. The estimation of protein degradability in the rumen from incubation "
    "measurements weighted according to rate of passage. J Agric Sci 92(2):499-503. "
    "DOI 10.1017/S0021859600063048.",
    "McDonald (1981), J Agric Sci 96:251-252: revisión del modelo para incluir un tiempo de latencia "
    "(título, autores completos y DOI pendientes de verificar).",
)

SUPUESTOS_CINETICA = (
    "Cinética de primer orden: la fracción b se degrada a la tasa kd (h⁻¹).",
    "Tasa de paso kp constante (h⁻¹), expresada en las mismas unidades que kd.",
    "Sin tiempo de latencia: McDonald (1981) revisó el modelo para incluirlo; si la degradación de b empieza "
    "con retraso, este cálculo no lo representa.",
    "La fracción a se cuenta como degradada por completo en el rumen y 1 - a - b como no degradable "
    "(definición del modelo).",
    "El modelo se formuló para la degradabilidad de la proteína (Ørskov y McDonald 1979); aplicarlo a otro "
    "compuesto supone que su desaparición ruminal sigue la misma cinética [SUP].",
    "La desaparición ruminal se trata como pérdida del compuesto: el modelo no representa su transformación "
    "en otros compuestos ni otras vías de desaparición distintas de la degradación.",
    "a, b y kd deben proceder de medidas de incubación con fuente registrada y kp de una fuente para la "
    "especie y la situación evaluadas; no hay valores por defecto.",
)

SUPUESTO_ESCAPE_DECLARADO = (
    "Fracción de escape declarada directamente: debe tener fuente registrada ([PUB]) o marcarse [SUP]."
)

SUPUESTO_ABSORCION = (
    "Escapar del rumen no implica absorberse: la dosis absorbible necesita una digestibilidad intestinal con "
    "fuente registrada o marcada [SUP]; si falta, queda sin calcular [DESC]."
)

# Tolerancia para sumas de fracciones que deberían valer 1 (p. ej., a + b) con error de redondeo.
_TOL = 1e-12


def _number(name: str, x) -> float:
    if isinstance(x, bool):
        raise ValueError(f"{name} debe ser un número, no un booleano")
    try:
        value = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser un número; recibido {x!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} debe ser un número finito; recibido {x!r}")
    return value


def _fraction(name: str, x) -> float:
    value = _number(name, x)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} debe ser una fracción entre 0 y 1 (no un porcentaje); recibido {value}")
    return value


def _rate(name: str, x) -> float:
    value = _number(name, x)
    if not value > 0.0:
        raise ValueError(f"{name} debe ser una tasa positiva en h⁻¹; recibido {value}")
    return value


def _kinetics(a, b, kd, kp) -> tuple[float, float, float, float]:
    a, b = _number("a", a), _number("b", b)
    if a < 0.0:
        raise ValueError(f"a debe ser >= 0; recibido {a}")
    if b < 0.0:
        raise ValueError(f"b debe ser >= 0; recibido {b}")
    if a + b > 1.0 + _TOL:
        raise ValueError(f"a + b debe ser <= 1 (a y b son fracciones, no porcentajes); recibido a + b = {a + b}")
    return a, b, _rate("kd", kd), _rate("kp", kp)


def _effective_degradability(a: float, b: float, kd: float, kp: float) -> float:
    return a + b * kd / (kd + kp)


def _escape(a: float, b: float, kd: float, kp: float) -> float:
    return min(1.0, max(0.0, 1.0 - _effective_degradability(a, b, kd, kp)))


def degradabilidad_efectiva(a: float, b: float, kd: float, kp: float) -> float:
    """Degradabilidad ruminal efectiva DE = a + b·kd/(kd + kp) (Ørskov y McDonald 1979).

    a, b: fracciones (a >= 0, b >= 0, a + b <= 1); kd, kp: tasas > 0 en h⁻¹."""
    return _effective_degradability(*_kinetics(a, b, kd, kp))


def fraccion_escape(a: float, b: float, kd: float, kp: float) -> float:
    """Fracción que escapa a la degradación ruminal: 1 - DE. Incluye la fracción no degradable (1 - a - b) y
    la parte de b que sale del rumen antes de degradarse, b·kp/(kd + kp)."""
    return _escape(*_kinetics(a, b, kd, kp))


def escape_primer_orden(kd: float, kp: float) -> float:
    """Fracción que escapa del rumen para un compuesto degradable con cinética de primer orden:
    kp/(kd + kp). Es el caso a = 0, b = 1 del modelo de Ørskov y McDonald (1979). kd, kp > 0 en h⁻¹."""
    kd, kp = _rate("kd", kd), _rate("kp", kp)
    return kp / (kd + kp)


def dosis_posruminal(dosis: float, fraccion_escape: float) -> float:
    """Cantidad que sale del rumen sin degradar: dosis × fracción de escape, en las unidades de la dosis."""
    dose = _number("dosis", dosis)
    if dose < 0.0:
        raise ValueError(f"dosis debe ser >= 0; recibido {dose}")
    return dose * _fraction("fraccion_escape", fraccion_escape)


def dosis_absorbible(dosis: float, fraccion_escape: float, digestibilidad_intestinal: float) -> float:
    """Cantidad absorbible en el intestino: dosis × fracción de escape × digestibilidad intestinal (fracción),
    en las unidades de la dosis."""
    return dosis_posruminal(dosis, fraccion_escape) * _fraction("digestibilidad_intestinal",
                                                                 digestibilidad_intestinal)


def informe_destino(a: float | None = None, b: float | None = None, kd: float | None = None,
                    kp: float | None = None, *, fraccion_escape: float | None = None,
                    dosis: float | None = None, digestibilidad_intestinal: float | None = None,
                    unidad_dosis: str | None = None) -> dict:
    """Informe del destino ruminal con resultados [DER], supuestos y referencias.

    Dos modos excluyentes: cinética (a, b, kd y kp, todos) o fraccion_escape declarada. La dosis y la
    digestibilidad intestinal son opcionales; sin ellas, los resultados que dependen de ellas quedan en None."""
    kinetic = [x is not None for x in (a, b, kd, kp)]
    if fraccion_escape is not None and any(kinetic):
        raise ValueError("Usa la cinética (a, b, kd, kp) o la fracción de escape declarada, no ambas")
    if fraccion_escape is None and not all(kinetic):
        if any(kinetic):
            missing = [name for name, given in zip(("a", "b", "kd", "kp"), kinetic) if not given]
            raise ValueError("Faltan parámetros de la cinética: " + ", ".join(missing))
        raise ValueError("Indica la cinética (a, b, kd, kp) o la fracción de escape declarada")

    results: dict = {"degradabilidad_efectiva": None, "fraccion_escape": None,
                     "escape_fraccion_no_degradable": None, "escape_fraccion_b": None}
    if fraccion_escape is None:
        a, b, kd, kp = _kinetics(a, b, kd, kp)
        escape = _escape(a, b, kd, kp)
        results.update({"degradabilidad_efectiva": _effective_degradability(a, b, kd, kp),
                        "fraccion_escape": escape,
                        "escape_fraccion_no_degradable": max(0.0, 1.0 - a - b),
                        "escape_fraccion_b": b * kp / (kd + kp)})
        model = "Ørskov y McDonald (1979): DE = a + b·kd/(kd + kp); fracción de escape = 1 - DE"
        inputs: dict = {"a": a, "b": b, "kd_h-1": kd, "kp_h-1": kp}
        assumptions = list(SUPUESTOS_CINETICA)
        references = list(REFERENCIAS)
    else:
        escape = _fraction("fraccion_escape", fraccion_escape)
        results["fraccion_escape"] = escape
        model = "Fracción de escape declarada (sin cálculo cinético)"
        inputs = {"fraccion_escape": escape}
        assumptions = [SUPUESTO_ESCAPE_DECLARADO]
        references = []  # sin modelo cinético: la fuente es la de la fracción declarada

    di = None
    if digestibilidad_intestinal is not None:
        di = _fraction("digestibilidad_intestinal", digestibilidad_intestinal)
        results["fraccion_absorbible"] = escape * di
    else:
        results["fraccion_absorbible"] = None
    results["dosis_posruminal"] = dosis_posruminal(dosis, escape) if dosis is not None else None
    results["dosis_absorbible"] = (dosis_absorbible(dosis, escape, di)
                                   if dosis is not None and di is not None else None)
    # Las dosis resultantes van en las mismas unidades que la dosis de entrada (None: no declaradas).
    results["unidad_dosis"] = unidad_dosis
    inputs.update({"dosis": None if dosis is None else _number("dosis", dosis),
                   "digestibilidad_intestinal": di, "unidad_dosis": unidad_dosis})
    assumptions.append(SUPUESTO_ABSORCION)
    return {"modelo": model, "etiqueta": "[DER]", "calculo": "nutria.destino.informe_destino",
            "entradas": inputs, "resultados": results, "supuestos": assumptions,
            "referencias": references}


def _fmt(x: float | None, digits: int = 4) -> str:
    return "sin calcular" if x is None else f"{x:.{digits}g}"


def texto_informe_destino(informe: dict) -> str:
    """Versión legible de informe_destino() para la salida de texto de la CLI."""
    r, e = informe["resultados"], informe["entradas"]
    unit = r["unidad_dosis"] or "sin unidad declarada; los resultados van en las unidades de la dosis"
    lines = [f"Destino ruminal {informe['etiqueta']}: {informe['modelo']}"]
    if "a" in e:
        lines.append(f"Entradas: a = {e['a']:g}; b = {e['b']:g}; kd = {e['kd_h-1']:g} h⁻¹; "
                     f"kp = {e['kp_h-1']:g} h⁻¹")
        lines.append(f"Degradabilidad efectiva: {_fmt(r['degradabilidad_efectiva'])}")
        lines.append(f"Fracción de escape: {_fmt(r['fraccion_escape'])} (no degradable "
                     f"{_fmt(r['escape_fraccion_no_degradable'])} + fracción b que sale sin degradarse "
                     f"{_fmt(r['escape_fraccion_b'])})")
    else:
        lines.append(f"Fracción de escape declarada: {_fmt(r['fraccion_escape'])}")
    if e["digestibilidad_intestinal"] is not None:
        lines.append(f"Digestibilidad intestinal: {e['digestibilidad_intestinal']:g}; fracción absorbible: "
                     f"{_fmt(r['fraccion_absorbible'])}")
    if e["dosis"] is not None:
        lines.append(f"Dosis: {e['dosis']:g} ({unit}); posruminal: {_fmt(r['dosis_posruminal'])}; "
                     f"absorbible: {_fmt(r['dosis_absorbible'])}")
    lines += ["", "Supuestos:"] + [f"- {s}" for s in informe["supuestos"]]
    if informe["referencias"]:
        lines += ["", "Referencias:"] + [f"- {ref}" for ref in informe["referencias"]]
    return "\n".join(lines)
