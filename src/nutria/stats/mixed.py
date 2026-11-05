"""Metaanálisis de medias de tratamiento con el estudio como efecto aleatorio
(St-Pierre 2001, J Dairy Sci 84:741-755; Sauvant et al. 2008, Animal 2:1203-1214).

Es el enfoque habitual en nutrición animal para relaciones dosis-respuesta entre estudios.
Cuidado con la interpretación: con un intercepto aleatorio por estudio, la pendiente fija de
la dosis NO se estima solo dentro de cada estudio. El intercepto aleatorio mezcla información
intra y entre estudios: la pendiente es una media ponderada de la pendiente intraestudio y de
la pendiente entre estudios (la de las dosis medias frente a las respuestas medias). Coincide
con la intraestudio cuando la dosis media es la misma en todos los estudios, y se le acerca
cuanto mayor es la varianza entre estudios respecto a la residual y cuantos más tratamientos
tiene cada estudio; si no, el peso de la parte entre estudios no es despreciable. La parte entre
estudios está expuesta a la confusión que se quiere evitar (dieta basal, animales, condiciones):
si la dosis se asocia con las condiciones del estudio, la pendiente queda sesgada hacia la
relación entre estudios.

Por eso `st_pierre` descompone por defecto (descomponer=True) la dosis en <dosis>_intra (dosis
menos la media de su estudio) y <dosis>_entre (media del estudio) y ajusta ambas como efectos
fijos (enfoque de Mundlak): pendiente_intra estima la respuesta dentro de los estudios y
pendiente_entre la asociación entre estudios.

Pendiente aleatoria (por defecto, random_slope="auto"): si la respuesta a la dosis varía entre
estudios, el modelo con solo intercepto aleatorio infravalora el error estándar de la pendiente
y la prueba es liberal (en simulaciones con 5 a 10 estudios y pendientes heterogéneas, la prueba
de Wald z de ese modelo, la que se usaba antes por defecto, rechazaba del 11 al 27 % de las veces
en lugar del 5 % nominal). Por eso, cuando es estimable (al menos MIN_ESTUDIOS_PENDIENTE estudios con
al menos MIN_NIVELES_PENDIENTE niveles de dosis distintos), se ajustan los dos modelos por REML
(mismos efectos fijos, así que sus verosimilitudes REML son comparables) y se comparan con la razón
de verosimilitudes. La varianza de la pendiente está en la frontera del espacio de parámetros bajo
la hipótesis nula, así que el estadístico no sigue una chi² con 2 gl: con la covarianza
intercepto-pendiente libre, su distribución nula es la mezcla 50:50 de chi² con 1 y 2 gl (Self y
Liang 1987, JASA 82:605-610; Stram y Lee 1994, Biometrics 50:1171-1177), y p = 0,5·P(chi²₁ > LRT) +
0,5·P(chi²₂ > LRT). Se elige la pendiente aleatoria si p < ALFA_LRT = 0,20: un umbral más
permisivo que 0,05 para no quedarse con el modelo liberal por falta de potencia de la prueba
(Matuschek et al. 2017, J Mem Lang 94:305-315). El resultado declara el modelo elegido y el
motivo; random_slope=True o False fuerzan uno de los dos.

Inferencia: t de Student con gl = número de estudios - número de efectos fijos, una aproximación
conservadora (el estudio es la unidad de replicación de la pendiente). No es Satterthwaite ni
Kenward-Roger: con pocos estudios, reajusta el modelo en R con lme4/lmerTest para comparar. El
modelo no pondera por la precisión de cada media (St-Pierre 2001 discute hacerlo).

Requiere `statsmodels` (pip install "nutria[mixto]").
"""

from __future__ import annotations

import csv
import math
import warnings
from pathlib import Path

#: Umbral de la razón de verosimilitudes para preferir la pendiente aleatoria (Matuschek et al. 2017).
ALFA_LRT = 0.20
#: Estudios con al menos MIN_NIVELES_PENDIENTE niveles de dosis necesarios para estimar la pendiente aleatoria.
MIN_ESTUDIOS_PENDIENTE = 3
MIN_NIVELES_PENDIENTE = 3

_MODELOS = {"intercepto_aleatorio": "intercepto aleatorio", "pendiente_aleatoria": "intercepto y pendiente aleatorios"}
_METODOS_PENDIENTE = ["powell", "lbfgs", "bfgs"]


def _t(estimate: float, se: float, df: int) -> dict:
    """Estimación, EE, t, gl, p bilateral e IC 95 % con t de Student (None si no son calculables)."""
    from scipy import stats

    out = {"estimacion": float(estimate), "ee": float(se) if math.isfinite(se) else None, "t": None, "gl": df,
           "p": None, "ic95": [None, None]}
    if not (math.isfinite(se) and se > 0) or df < 1:
        return out
    t = estimate / se
    crit = float(stats.t.ppf(0.975, df))
    out.update({"t": float(t), "p": float(2 * stats.t.sf(abs(t), df)),
                "ic95": [float(estimate - crit * se), float(estimate + crit * se)]})
    return out


def razon_verosimilitudes(llf_intercepto: float, llf_pendiente: float) -> dict:
    """LRT REML de la pendiente aleatoria (con covarianza intercepto-pendiente libre) frente al intercepto
    aleatorio: estadístico 2·(llf_pendiente - llf_intercepto) (truncado en 0) y p con la mezcla 50:50 de chi²
    con 1 y 2 gl (Self y Liang 1987; Stram y Lee 1994)."""
    from scipy import stats

    lrt = max(0.0, 2.0 * (float(llf_pendiente) - float(llf_intercepto)))
    p = 0.5 * float(stats.chi2.sf(lrt, 1)) + 0.5 * float(stats.chi2.sf(lrt, 2))
    return {"estadistico": lrt, "p": min(1.0, p),
            "distribucion_nula": "mezcla 50:50 de chi² con 1 y 2 gl (corrección de frontera; Self y Liang 1987; "
                                 "Stram y Lee 1994)"}


def _ajustar(smf, formula: str, datos, grupos, re_formula: str, metodos: list[str] | None = None):
    """Ajuste REML; devuelve (fit o None, estado) sin dejar avisos de statsmodels en la salida estándar.

    `metodos`: optimizadores de scipy que statsmodels prueba en orden (None = los de statsmodels)."""
    import numpy as np

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            model = smf.mixedlm(formula, datos, groups=grupos, re_formula=re_formula)
            fit = model.fit(reml=True, method=metodos) if metodos else model.fit(reml=True)
        except (np.linalg.LinAlgError, ValueError, ZeroDivisionError, FloatingPointError) as exc:
            return None, {"convergencia": False, "error": str(exc), "avisos_statsmodels": []}
    mensajes = sorted({str(w.message).splitlines()[0] for w in caught})
    bse = np.asarray(fit.bse_fe, dtype=float)
    ok = bool(getattr(fit, "converged", True)) and bool(np.all(np.isfinite(bse))) and math.isfinite(float(fit.llf))
    return fit, {"convergencia": ok, "avisos_statsmodels": mensajes}


def _niveles_por_estudio(df, study: str, dose: str) -> dict:
    return {str(k): int(v) for k, v in df.groupby(study)[dose].nunique().items()}


def st_pierre(rows: list[dict], response: str, dose: str, study: str,
              covariates: list[str] | None = None, random_slope: bool | str = "auto",
              descomponer: bool = True, alfa_lrt: float = ALFA_LRT) -> dict:
    """Modelo mixto respuesta ~ dosis (+ covariables) con el estudio como efecto aleatorio.

    - descomponer=True (por defecto): crea <dosis>_intra = dosis - media de su estudio y
      <dosis>_entre = media del estudio, ajusta ambas y devuelve `pendiente_intra`,
      `pendiente_entre` y su diferencia (`diferencia_entre_intra`). Si la dosis media no varía
      entre estudios, pendiente_entre es None (no es estimable). Exige que algún estudio tenga
      más de un nivel de dosis.
    - descomponer=False: modelo clásico con la dosis sin descomponer; devuelve `pendiente`, que
      mezcla información intra y entre estudios.
    - random_slope="auto" (por defecto): ajusta el modelo con intercepto aleatorio y, si es estimable, el
      de intercepto y pendiente aleatorios (sobre <dosis>_intra si se descompone); elige con la razón de
      verosimilitudes REML (mezcla 50:50 de chi² con 1 y 2 gl, umbral `alfa_lrt`). random_slope=True fuerza
      la pendiente aleatoria (ValueError si no es estimable); False fuerza el intercepto aleatorio.

    Inferencia: t de Student con gl = n_estudios - número de efectos fijos (conservadora; no es
    Satterthwaite ni Kenward-Roger). Si no quedan gl (gl < 1), p e IC son None. Las covariables entran
    sin descomponer. Devuelve `modelo`, `motivo_modelo`, `comparacion_modelos` (los dos ajustes y el LRT)
    y notas que explican todo ello.
    """
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Instala las dependencias opcionales: pip install "nutria[mixto]"') from exc

    if not (random_slope == "auto" or isinstance(random_slope, bool)):
        raise ValueError(f"random_slope debe ser 'auto', True o False: {random_slope!r}")
    if isinstance(alfa_lrt, bool) or not (isinstance(alfa_lrt, (int, float)) and 0 < alfa_lrt < 1):
        raise ValueError(f"alfa_lrt debe estar entre 0 y 1: {alfa_lrt!r}")
    covariates = list(covariates or [])
    df = pd.DataFrame(rows)
    absent = [col for col in dict.fromkeys([response, dose, study, *covariates]) if col not in df.columns]
    if absent:
        raise ValueError(f"Faltan columnas en los datos: {', '.join(absent)}")
    for col in [response, dose, *covariates]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[response, dose, study, *covariates])
    df = df[df[study].astype(str).str.strip() != ""].copy()
    n_groups = df[study].nunique()
    if n_groups < 3:
        raise ValueError("Se necesitan al menos 3 estudios")

    intra, entre = f"{dose}_intra", f"{dose}_entre"
    between_estimable = False
    if descomponer:
        clash = [col for col in (intra, entre) if col in df.columns]
        if clash:
            raise ValueError(f"La columna {', '.join(clash)} ya existe en los datos: renómbrala para descomponer la dosis")
        study_mean = df.groupby(study)[dose].transform("mean")
        df[intra] = df[dose] - study_mean
        df[entre] = study_mean
        scale = max(1.0, float(df[dose].abs().max()))
        if float(df[intra].abs().max()) <= 1e-12 * scale:
            raise ValueError("Ningún estudio tiene más de un nivel de dosis: la pendiente intraestudio no es "
                             "estimable. Con un solo nivel por estudio solo hay asociación entre estudios "
                             "(descomponer=False), expuesta a confusión.")
        between_estimable = float(df[entre].max() - df[entre].min()) > 1e-12 * scale
        dose_terms = [intra, entre] if between_estimable else [intra]
        slope_term = intra
    else:
        dose_terms = [dose]
        slope_term = dose

    niveles = _niveles_por_estudio(df, study, dose)
    con_niveles = sum(1 for n in niveles.values() if n >= MIN_NIVELES_PENDIENTE)
    slope_estimable = con_niveles >= MIN_ESTUDIOS_PENDIENTE
    if random_slope is True and not slope_estimable:
        raise ValueError(f"La pendiente aleatoria no es estimable: solo {con_niveles} estudio(s) tienen al menos "
                         f"{MIN_NIVELES_PENDIENTE} niveles de dosis distintos (hacen falta "
                         f"{MIN_ESTUDIOS_PENDIENTE}); con 2 niveles por estudio la varianza de la pendiente se "
                         "confunde con la residual. Usa el modelo con intercepto aleatorio.")

    formula = f"Q('{response}') ~ " + " + ".join(f"Q('{term}')" for term in [*dose_terms, *covariates])
    groups = df[study]
    fit_ri, estado_ri = _ajustar(smf, formula, df, groups, "~1")
    if fit_ri is None:
        raise ValueError(f"No se pudo ajustar el modelo con intercepto aleatorio: {estado_ri.get('error')}")
    fit_rs, estado_rs = (None, {"convergencia": False, "avisos_statsmodels": [], "no_ajustado": True})
    if slope_estimable:
        # También con random_slope=False, para avisar si los datos indican heterogeneidad de la pendiente.
        # Powell (sin derivadas) primero: con la varianza de la pendiente cerca de 0, los optimizadores por
        # gradiente que statsmodels usa por defecto se quedan a menudo lejos del máximo de la REML.
        fit_rs, estado_rs = _ajustar(smf, formula, df, groups, f"~Q('{slope_term}')", _METODOS_PENDIENTE)
        if fit_rs is not None and estado_rs["convergencia"]:
            tol = 1e-4 * max(1.0, abs(float(fit_ri.llf)))
            if float(fit_rs.llf) < float(fit_ri.llf) - tol:
                # El modelo con pendiente anida al de intercepto: una verosimilitud menor es un fallo del optimizador.
                estado_rs = {**estado_rs, "convergencia": False,
                             "error": "verosimilitud REML menor que la del modelo anidado (optimización fallida)"}
    lrt = None
    if fit_rs is not None and estado_rs["convergencia"]:
        lrt = razon_verosimilitudes(float(fit_ri.llf), float(fit_rs.llf))

    avisos: list[str] = []
    if random_slope is True:
        if fit_rs is None or not estado_rs["convergencia"]:
            raise ValueError("El modelo con pendiente aleatoria no convergió: "
                             + "; ".join(estado_rs.get("avisos_statsmodels") or [estado_rs.get("error", "")]))
        modelo, motivo = "pendiente_aleatoria", "pendiente aleatoria forzada (--pendiente-aleatoria)"
    elif random_slope is False:
        modelo, motivo = "intercepto_aleatorio", "intercepto aleatorio forzado (--sin-pendiente-aleatoria)"
        if lrt is not None and lrt["p"] < alfa_lrt:
            avisos.append(f"Forzaste el intercepto aleatorio, pero la razón de verosimilitudes indica que la "
                          f"pendiente varía entre estudios (LRT = {lrt['estadistico']:.3f}, p = {lrt['p']:.3g} < "
                          f"{alfa_lrt:g}): la prueba de la pendiente será liberal.")
    elif not slope_estimable:
        modelo = "intercepto_aleatorio"
        motivo = (f"pendiente aleatoria no estimable: {con_niveles} estudio(s) con al menos {MIN_NIVELES_PENDIENTE} "
                  f"niveles de dosis (hacen falta {MIN_ESTUDIOS_PENDIENTE})")
        avisos.append(f"No se pudo comprobar si la respuesta a la dosis varía entre estudios ({motivo}). Si varía, "
                      "la prueba de la pendiente con intercepto aleatorio es liberal (error de tipo I por encima "
                      "del nominal); la t con gl = estudios - efectos fijos lo atenúa, pero interpreta la p con "
                      "cautela.")
    elif lrt is None:
        modelo = "intercepto_aleatorio"
        motivo = "el modelo con pendiente aleatoria no convergió"
        avisos.append("El modelo con pendiente aleatoria no convergió ("
                      + "; ".join(estado_rs.get("avisos_statsmodels") or [estado_rs.get("error", "sin detalle")])
                      + "): se usa el intercepto aleatorio, cuya prueba de la pendiente es liberal si la respuesta "
                        "varía entre estudios. Reajusta en R (lme4/lmerTest) antes de concluir.")
    elif lrt["p"] < alfa_lrt:
        modelo = "pendiente_aleatoria"
        motivo = (f"razón de verosimilitudes REML = {lrt['estadistico']:.3f}, p = {lrt['p']:.3g} < {alfa_lrt:g} "
                  "(mezcla 50:50 de chi² con 1 y 2 gl): la pendiente varía entre estudios")
    else:
        modelo = "intercepto_aleatorio"
        motivo = (f"razón de verosimilitudes REML = {lrt['estadistico']:.3f}, p = {lrt['p']:.3g} >= {alfa_lrt:g} "
                  "(mezcla 50:50 de chi² con 1 y 2 gl): sin indicios de que la pendiente varíe entre estudios")
    fit = fit_rs if modelo == "pendiente_aleatoria" else fit_ri
    estado = estado_rs if modelo == "pendiente_aleatoria" else estado_ri
    if not estado["convergencia"]:
        avisos.append(f"El modelo elegido ({_MODELOS[modelo]}) no convergió o tiene errores estándar no finitos ("
                      + "; ".join(estado.get("avisos_statsmodels") or ["sin detalle"])
                      + "): no uses sus p ni sus IC sin reajustarlo (en R, lme4/lmerTest).")
    params = fit.fe_params
    bse = fit.bse_fe
    gl = int(n_groups) - int(len(params))

    def clean(name: str) -> str:
        return name.replace("Q('", "").replace("')", "")

    fixed = {clean(name): _t(float(params[name]), float(bse[name]), gl) for name in params.index}
    if gl < 1:
        avisos.append(f"Con {n_groups} estudios y {len(params)} efectos fijos no quedan grados de libertad para la "
                      "prueba t conservadora: p e IC no se calculan. Hacen falta más estudios (o menos "
                      "covariables, o descomponer=False).")

    def resumen_modelo(f, e) -> dict | None:
        if f is None:
            if e.get("no_ajustado"):
                return None  # no estimable: no se ajustó
            return {"ajustado": False, "convergencia": False, "error": e.get("error")}
        name = f"Q('{slope_term}')"
        return {"ajustado": True, "convergencia": e["convergencia"], "llf_reml": float(f.llf),
                "pendiente": {"estimacion": float(f.fe_params[name]), "ee": float(f.bse_fe[name])},
                "varianza_estudio": [[float(x) for x in row] for row in f.cov_re.values],
                "varianza_residual": float(f.scale), "avisos_statsmodels": e.get("avisos_statsmodels", [])}

    prueba = (f"t de Student con gl = estudios - efectos fijos = {n_groups} - {len(params)} = {gl} (conservadora; "
              "no es Satterthwaite ni Kenward-Roger ni Wald z)")
    notes = [
        f"Modelo elegido: {_MODELOS[modelo]} ({motivo}).",
        "Comparación de modelos: con al menos " f"{MIN_ESTUDIOS_PENDIENTE} estudios con {MIN_NIVELES_PENDIENTE} o más "
        "niveles de dosis se ajustan por REML el modelo con intercepto aleatorio y el de intercepto y pendiente "
        "aleatorios, y se comparan con la razón de verosimilitudes; como la varianza de la pendiente está en la "
        "frontera bajo la hipótesis nula, la p usa la mezcla 50:50 de chi² con 1 y 2 gl (Self y Liang 1987; Stram "
        f"y Lee 1994). Se elige la pendiente aleatoria si p < {alfa_lrt:g} (umbral permisivo para proteger el error "
        "de tipo I de la pendiente; Matuschek et al. 2017).",
        f"Inferencia: {prueba}. Para afinar con pocos estudios, reajusta el modelo en R con lme4/lmerTest "
        "(Kenward-Roger o Satterthwaite).",
        "Modelo sin ponderar; St-Pierre (2001) discute ponderar por la precisión de cada media.",
        "Revisa residuos, puntos influyentes y el rango de dosis de cada estudio antes de extrapolar.",
        "Una pendiente significativa entre estudios no demuestra causalidad en cada granja.",
    ]
    out = {
        "n_observaciones": int(fit.nobs),
        "n_estudios": int(n_groups),
        "convergencia": bool(estado["convergencia"]),
        "descomponer": bool(descomponer),
        "modelo": modelo,
        "motivo_modelo": motivo,
        "pendiente_aleatoria": {"auto": "auto", True: "forzada", False: "desactivada"}[random_slope],
        "prueba": prueba,
        "gl": gl,
        "efectos_fijos": fixed,
        "varianza_estudio": [[float(x) for x in row] for row in fit.cov_re.values],
        "varianza_residual": float(fit.scale),
        "comparacion_modelos": {
            "estimable": bool(slope_estimable),
            "estudios_con_3_o_mas_niveles": int(con_niveles),
            "niveles_por_estudio": niveles,
            "alfa_lrt": float(alfa_lrt),
            "razon_verosimilitudes": lrt,
            "intercepto_aleatorio": resumen_modelo(fit_ri, estado_ri),
            "pendiente_aleatoria": resumen_modelo(fit_rs, estado_rs),
        },
        "avisos": avisos,
        "notas": notes,
    }
    if descomponer:
        out["pendiente_intra"] = fixed[intra]
        out["pendiente_entre"] = fixed[entre] if between_estimable else None
        out["diferencia_entre_intra"] = None
        if between_estimable:
            cov = fit.cov_params()
            qi, qe = f"Q('{intra}')", f"Q('{entre}')"
            diff = float(params[qe] - params[qi])
            var = float(cov.loc[qe, qe] + cov.loc[qi, qi] - 2 * cov.loc[qe, qi])
            out["diferencia_entre_intra"] = _t(diff, math.sqrt(var) if var > 0 else float("nan"), gl)
            notes.insert(1, "Descomposición intra/entre estudios (enfoque de Mundlak): pendiente_intra usa solo "
                            "la variación de dosis dentro de cada estudio; pendiente_entre es la asociación entre "
                            "las dosis medias y las respuestas medias de los estudios, expuesta a confusión "
                            "(sesgo ecológico). Si difieren, la pendiente sin descomponer mezcla ambas y no "
                            "estima la respuesta intraestudio.")
        else:
            notes.insert(1, "La dosis media es igual en todos los estudios: no hay información entre estudios y "
                            "la pendiente es intraestudio; pendiente_entre no es estimable.")
    else:
        out["pendiente"] = fixed[dose]
        notes.insert(1, "Sin descomponer, la pendiente mezcla información intra y entre estudios (el intercepto "
                        "aleatorio no la aísla); usa descomponer=True para separarlas.")
    if modelo == "pendiente_aleatoria":
        notes.append("Con pendiente aleatoria, la heterogeneidad de la respuesta entre estudios queda en la matriz "
                     "de covarianzas del estudio (varianza_estudio: intercepto y pendiente).")
    # Lo que hay que leer primero (avisos, modelo elegido y pendientes) va al principio de la salida.
    primero = ["avisos", "modelo", "motivo_modelo", "pendiente_aleatoria", "prueba", "gl", "pendiente_intra",
               "pendiente_entre", "diferencia_entre_intra", "pendiente"]
    return {**{k: out[k] for k in primero if k in out}, **{k: v for k, v in out.items() if k not in primero}}


def read_csv(path: Path, sep: str = ",") -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=sep))
