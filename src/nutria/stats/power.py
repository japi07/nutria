"""Potencia y tamaño muestral para diseñar ensayos (in vivo) con varianzas realistas.

La DE debe venir de datos (p. ej. la DE combinada de los estudios del metaanálisis o la
varianza residual de ensayos previos en la misma especie y etapa), no de supuestos.

La unidad experimental manda (`n_means`, la que usa `nutria potencia medias`):

- Paralelo con el animal como unidad: prueba t de dos muestras con la DE entre animales.
- Paralelo con el corral como unidad (tratamiento aplicado por corral, m animales por corral): la
  media de un corral tiene varianza σ²·[1 + (m - 1)·ICC]/m, con σ la DE entre animales e ICC la
  correlación intraclase dentro del corral; el efecto de diseño es 1 + (m - 1)·ICC (Donner y Klar
  2000, Design and Analysis of Cluster Randomization Trials in Health Research). Se calcula el
  número de corrales por grupo con la prueba t sobre las medias de corral (gl = 2·c - 2) y, de ahí,
  los animales (c·m). Contar animales como si fueran unidades subestima el tamaño necesario.
- Crossover AB/BA y cuadrado latino de t tratamientos: cada animal recibe todos los tratamientos;
  la diferencia entre dos medias de tratamiento tiene varianza 2·σ_w²/n, con σ_w la DE intraanimal
  (residual) y n animales, y gl = (n - 2)·(t - 1) (animal, periodo y tratamiento como efectos; para
  t = 2 son los n - 2 de la prueba t de las diferencias entre periodos por secuencia, Senn 2002,
  Cross-over Trials in Clinical Research). σ_w se da directamente o se obtiene de la DE entre animales
  σ y la correlación intrasujeto ρ: σ_w = σ·√(1 - ρ). Supone que no hay efecto residual (carry-over)
  y periodos comunes a todos los animales.
"""

from __future__ import annotations

import math

from scipy import stats

#: Diseños de `n_means`.
DESIGNS = ("paralelo", "crossover", "cuadrado_latino")


def _power_two_sample(n: int, delta: float, sd: float, alpha: float) -> float:
    df = 2 * n - 2
    ncp = abs(delta) / (sd * math.sqrt(2.0 / n))
    tcrit = stats.t.ppf(1 - alpha / 2, df)
    return float(stats.nct.sf(tcrit, df, ncp) + stats.nct.cdf(-tcrit, df, ncp))


def _check_common(delta: float, alpha: float, power: float) -> None:
    if not (isinstance(delta, (int, float)) and math.isfinite(delta)) or delta == 0:
        raise ValueError("delta debe ser un número finito distinto de 0")
    if not 0 < alpha < 1:
        raise ValueError("alfa debe estar entre 0 y 1")
    if not 0 < power < 1:
        raise ValueError("la potencia debe estar entre 0 y 1")


def n_parallel(delta: float, sd: float, alpha: float = 0.05, power: float = 0.80, max_n: int = 100000) -> dict:
    """Unidades experimentales por grupo en un diseño paralelo de dos grupos (t bilateral exacta).

    `sd` es la DE entre unidades experimentales (entre animales si el animal es la unidad; entre medias de
    corral si lo es el corral)."""
    if delta == 0 or sd <= 0:
        raise ValueError("delta debe ser distinta de 0 y sd > 0")
    n = 2
    while n <= max_n:
        if _power_two_sample(n, delta, sd, alpha) >= power:
            return {"n_por_grupo": n, "n_total": 2 * n, "potencia": _power_two_sample(n, delta, sd, alpha)}
        n += 1
    raise ValueError("Tamaño muestral fuera de rango: revisa delta y sd")


def sd_within_from_total(sd_total: float, rho: float) -> float:
    """DE intraanimal (residual) a partir de la DE entre animales σ y la correlación intrasujeto ρ:
    σ_w = σ·√(1 - ρ)."""
    if not sd_total > 0:
        raise ValueError("La DE entre animales debe ser > 0")
    if not (math.isfinite(rho) and 0 <= rho < 1):
        raise ValueError("La correlación intrasujeto debe estar en [0, 1)")
    return sd_total * math.sqrt(1.0 - rho)


def n_crossover(delta: float, sd_within: float, alpha: float = 0.05, power: float = 0.80,
                max_n: int = 100000, treatments: int = 2) -> dict:
    """Animales en un crossover AB/BA (treatments = 2) o en un cuadrado latino de `treatments` tratamientos,
    para comparar dos tratamientos con la DE intraanimal `sd_within`.

    Var(diferencia) = 2·σ_w²/n y gl = (n - 2)·(t - 1); n es múltiplo de t (equilibrio). Supone ausencia de
    efecto residual (carry-over) y periodos comunes; con periodos cortos no es seguro."""
    if delta == 0 or sd_within <= 0:
        raise ValueError("delta debe ser distinta de 0 y sd_within > 0")
    if isinstance(treatments, bool) or int(treatments) != treatments or treatments < 2:
        raise ValueError("El número de tratamientos debe ser un entero >= 2")
    t = int(treatments)
    n = 4 if t == 2 else t
    while n <= max_n:
        df = (n - 2) * (t - 1)
        ncp = abs(delta) / (sd_within * math.sqrt(2.0 / n))
        tcrit = stats.t.ppf(1 - alpha / 2, df)
        pw = float(stats.nct.sf(tcrit, df, ncp) + stats.nct.cdf(-tcrit, df, ncp))
        if pw >= power:
            nota = ("Aproximación: supone que no hay efecto residual (carry-over) y periodos comunes a todos los "
                    "animales; verifica con simulación del modelo mixto real (periodo, secuencia o cuadrado).")
            if t > 2:
                nota += (f" Es la potencia de una comparación entre dos de los {t} tratamientos, sin ajuste por "
                         "comparaciones múltiples.")
            return {"diseno": "crossover" if t == 2 else "cuadrado_latino", "tratamientos": t,
                    "unidad_experimental": "animal (periodo dentro de animal)", "n_total": n,
                    "n_animales_total": n, "n_observaciones": n * t, "gl": df, "potencia": pw,
                    "de_intra": sd_within, "nota": nota}
        n += 2 if t == 2 else t
    raise ValueError("Tamaño muestral fuera de rango")


def design_effect(animals_per_pen: int, icc: float) -> float:
    """Efecto de diseño cuando se miden animales dentro de corrales pero el corral es la unidad
    experimental: DE = 1 + (m - 1)·ICC."""
    if animals_per_pen < 1 or not 0 <= icc <= 1:
        raise ValueError("m >= 1 y 0 <= ICC <= 1")
    return 1 + (animals_per_pen - 1) * icc


def n_pens(delta: float, sd_animal: float, animals_per_pen: int, icc: float, alpha: float = 0.05,
           power: float = 0.80, max_n: int = 100000) -> dict:
    """Corrales por grupo (y animales) en un diseño paralelo con el corral como unidad experimental.

    DE de la media de corral = σ·√([1 + (m - 1)·ICC]/m); prueba t de dos muestras sobre las medias de
    corral (gl = 2·c - 2). `sd_animal` es la DE entre animales (total) e `icc` la correlación intraclase
    dentro del corral."""
    if isinstance(animals_per_pen, bool) or int(animals_per_pen) != animals_per_pen or animals_per_pen < 1:
        raise ValueError("Los animales por corral (m) deben ser un entero >= 1")
    m = int(animals_per_pen)
    if not sd_animal > 0:
        raise ValueError("La DE entre animales debe ser > 0")
    deff = design_effect(m, icc)
    sd_pen = sd_animal * math.sqrt(deff / m)
    res = n_parallel(delta, sd_pen, alpha, power, max_n)
    c = res["n_por_grupo"]
    ignoring = n_parallel(delta, sd_animal, alpha, power, max_n)["n_por_grupo"]
    return {"diseno": "paralelo", "unidad_experimental": "corral", "n_por_grupo": c, "n_unidades_por_grupo": c,
            "n_total": 2 * c, "n_unidades_total": 2 * c, "animales_por_unidad": m, "n_animales_por_grupo": c * m,
            "n_animales_total": 2 * c * m, "icc": icc, "efecto_diseno": deff, "de_animal": sd_animal,
            "de_unidad": sd_pen, "gl": 2 * c - 2, "potencia": res["potencia"],
            "n_animales_por_grupo_ignorando_corral": ignoring,
            "nota": "n_por_grupo cuenta corrales (unidades experimentales); los animales son corrales × m. "
                    f"Tratar a los animales como unidades daría {ignoring} animales por grupo, una cifra que no "
                    "tiene en cuenta la correlación dentro del corral."}


def n_means(design: str, delta: float, sd: float | None = None, sd_within: float | None = None,
            rho: float | None = None, animals_per_pen: int | None = None, icc: float | None = None,
            treatments: int | None = None, alpha: float = 0.05, power: float = 0.80) -> dict:
    """Tamaño muestral para comparar dos medias según el diseño y la unidad experimental.

    - "paralelo": `sd` = DE entre animales. Con `animals_per_pen` (m >= 2) e `icc`, el corral es la unidad
      experimental (`n_pens`); sin ellos, el animal.
    - "crossover" (AB/BA) y "cuadrado_latino" (`treatments` >= 3): DE intraanimal `sd_within`, o `sd` (DE
      entre animales) con `rho` (correlación intrasujeto). `sd` sola es ambigua y se rechaza.

    Lanza ValueError ante combinaciones incoherentes. La salida distingue unidades experimentales y animales.
    """
    _check_common(delta, alpha, power)
    if design not in DESIGNS:
        raise ValueError(f"Diseño no admitido: {design!r}. Opciones: {', '.join(DESIGNS)}")
    if design == "paralelo":
        if sd_within is not None or rho is not None:
            raise ValueError("En un diseño paralelo usa --de (DE entre animales); --de-intra y --rho-intrasujeto "
                             "son para diseños cruzados")
        if treatments not in (None, 2):
            raise ValueError("--tratamientos solo se usa con el cuadrado latino")
        if sd is None or not sd > 0:
            raise ValueError("Indica --de (DE entre animales, > 0)")
        if (animals_per_pen is None) != (icc is None):
            raise ValueError("Con el corral como unidad experimental indica a la vez --m (animales por corral) e "
                             "--icc (correlación intraclase dentro del corral)")
        if animals_per_pen is not None:
            return n_pens(delta, sd, animals_per_pen, icc, alpha, power)
        res = n_parallel(delta, sd, alpha, power)
        n = res["n_por_grupo"]
        return {"diseno": "paralelo", "unidad_experimental": "animal", "n_por_grupo": n, "n_unidades_por_grupo": n,
                "n_total": 2 * n, "n_unidades_total": 2 * n, "n_animales_por_grupo": n, "n_animales_total": 2 * n,
                "de_animal": sd, "gl": 2 * n - 2, "potencia": res["potencia"],
                "nota": "El animal es la unidad experimental (tratamiento asignado a cada animal). Si el "
                        "tratamiento se aplica por corral, el corral es la unidad: indica --m y --icc."}
    if animals_per_pen is not None or icc is not None:
        raise ValueError("Los corrales (--m, --icc) solo están implementados en el diseño paralelo")
    if design == "crossover":
        if treatments not in (None, 2):
            raise ValueError("Un crossover AB/BA tiene 2 tratamientos; para más, usa --diseno cuadrado_latino")
        t = 2
    else:
        if treatments is None or isinstance(treatments, bool) or int(treatments) != treatments or treatments < 3:
            raise ValueError("Con el cuadrado latino indica --tratamientos (número de tratamientos y de periodos, "
                             ">= 3); para 2 tratamientos usa --diseno crossover")
        t = int(treatments)
    if sd_within is not None:
        if sd is not None or rho is not None:
            raise ValueError("Indica la DE intraanimal (--de-intra) o la DE entre animales con la correlación "
                             "intrasujeto (--de y --rho-intrasujeto), no ambas")
        origen = "DE intraanimal indicada (--de-intra)"
        sw = sd_within
        if not sw > 0:
            raise ValueError("La DE intraanimal debe ser > 0")
    elif sd is not None and rho is not None:
        sw = sd_within_from_total(sd, rho)
        origen = f"DE intraanimal = DE entre animales ({sd:g}) · √(1 - r), r = {rho:g}"
    else:
        raise ValueError("En un diseño cruzado indica la DE intraanimal (--de-intra) o la DE entre animales con la "
                         "correlación intrasujeto (--de y --rho-intrasujeto): --de sola es ambigua")
    res = n_crossover(delta, sw, alpha, power, treatments=t)
    res["origen_de_intra"] = origen
    if sd is not None:
        res["de_animal"] = sd
        res["rho_intrasujeto"] = rho
    return res


def describe(res: dict, delta: float, alpha: float, power: float) -> str:
    """Resumen en texto de `n_means` que separa unidades experimentales y animales."""
    head = f"Diferencia a detectar {delta:g}; alfa {alpha:g}; potencia objetivo {power:g} (alcanzada {res['potencia']:.3f})."
    if res["diseno"] == "paralelo" and res["unidad_experimental"] == "corral":
        return "\n".join([
            f"Diseño paralelo; unidad experimental: corral ({res['animales_por_unidad']} animales por corral, ICC "
            f"{res['icc']:g}, efecto de diseño {res['efecto_diseno']:.2f}; DE entre animales {res['de_animal']:g} y "
            f"de la media de corral {res['de_unidad']:.4g}).",
            f"Unidades experimentales: {res['n_unidades_por_grupo']} corrales por grupo ({res['n_unidades_total']} en "
            f"total; gl = {res['gl']}).",
            f"Animales: {res['n_animales_por_grupo']} por grupo ({res['n_animales_total']} en total).",
            head, f"Nota: {res['nota']}"])
    if res["diseno"] == "paralelo":
        return "\n".join([
            f"Diseño paralelo; unidad experimental: animal (DE entre animales {res['de_animal']:g}).",
            f"Unidades experimentales = animales: {res['n_por_grupo']} por grupo ({res['n_total']} en total; "
            f"gl = {res['gl']}).", head, f"Nota: {res['nota']}"])
    nombre = "crossover AB/BA" if res["diseno"] == "crossover" else f"cuadrado latino {res['tratamientos']}×{res['tratamientos']}"
    return "\n".join([
        f"Diseño {nombre}; cada animal recibe los {res['tratamientos']} tratamientos ({res['origen_de_intra']}: "
        f"{res['de_intra']:.4g}).",
        f"Animales: {res['n_animales_total']} en total ({res['n_observaciones']} observaciones animal-periodo; "
        f"gl = {res['gl']}).", head, f"Nota: {res['nota']}"])


def n_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.80,
                  animals_per_pen: int | None = None, icc: float | None = None) -> dict:
    """Animales por grupo para comparar incidencias (p. ej. cetosis clínica). Aproximación normal.

    Con `animals_per_pen` (m) e `icc`, el corral (o lote) es la unidad experimental: los animales
    necesarios se multiplican por el efecto de diseño 1 + (m - 1)·ICC y se redondean a corrales completos
    (Donner y Klar 2000); la salida distingue corrales y animales."""
    if not (0 < p1 < 1 and 0 < p2 < 1) or p1 == p2:
        raise ValueError("Proporciones en (0, 1) y distintas")
    if not 0 < alpha < 1:
        raise ValueError("alfa debe estar entre 0 y 1")
    if not 0 < power < 1:
        raise ValueError("la potencia debe estar entre 0 y 1")
    if (animals_per_pen is None) != (icc is None):
        raise ValueError("Con el corral como unidad experimental indica a la vez --m (animales por corral) e --icc")
    za = stats.norm.ppf(1 - alpha / 2)
    zb = stats.norm.ppf(power)
    pbar = (p1 + p2) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    n = math.ceil(num / (p1 - p2) ** 2)
    if animals_per_pen is not None:
        if isinstance(animals_per_pen, bool) or int(animals_per_pen) != animals_per_pen or animals_per_pen < 1:
            raise ValueError("Los animales por corral (m) deben ser un entero >= 1")
        m = int(animals_per_pen)
        deff = design_effect(m, icc)
        pens = math.ceil(n * deff / m)
        return {"unidad_experimental": "corral", "n_por_grupo": pens, "n_unidades_por_grupo": pens,
                "n_total": 2 * pens, "n_unidades_total": 2 * pens, "animales_por_unidad": m,
                "n_animales_por_grupo": pens * m, "n_animales_total": 2 * pens * m, "icc": icc,
                "efecto_diseno": deff, "n_animales_por_grupo_ignorando_corral": n,
                "nota": "n_por_grupo cuenta corrales (unidades experimentales): animales sin corrales × efecto de "
                        "diseño, redondeado a corrales completos. Aproximación normal; con pocos corrales es "
                        "optimista: verifica por simulación."}
    return {"unidad_experimental": "animal", "n_por_grupo": n, "n_total": 2 * n, "n_animales_por_grupo": n,
            "n_animales_total": 2 * n,
            "nota": "Las variables de salud requieren muestras grandes. Si el tratamiento se aplica por corral o "
                    "lote, el corral es la unidad experimental: indica --m y --icc."}


def detectable_difference(n_per_group: int, sd: float, alpha: float = 0.05, power: float = 0.80) -> float:
    """Diferencia mínima detectable con n por grupo (diseño paralelo)."""
    # Intervalo acotado de forma adaptativa: con no-centralidades enormes la t no central
    # pierde precisión numérica, así que no se parte de un extremo arbitrariamente alto.
    lo, hi = 0.0, sd
    for _ in range(40):
        if _power_two_sample(n_per_group, hi, sd, alpha) >= power:
            break
        lo, hi = hi, hi * 2
    for _ in range(100):
        mid = (lo + hi) / 2
        if _power_two_sample(n_per_group, mid, sd, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi
