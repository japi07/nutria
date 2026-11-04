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
pendiente_entre la asociación entre estudios. Los p-valores son de Wald z (statsmodels), sin
Kenward-Roger.

Requiere `statsmodels` (pip install "nutria[mixto]").
"""

from __future__ import annotations

import csv
import math
from pathlib import Path


def _wald(estimate: float, se: float) -> dict:
    """Estimación, EE, p de Wald z e IC 95 % de Wald."""
    from scipy import stats

    if not (math.isfinite(se) and se > 0):
        return {"estimacion": float(estimate), "ee": float(se), "p": float("nan"), "ic95": [float("nan")] * 2}
    z = estimate / se
    crit = float(stats.norm.ppf(0.975))
    return {"estimacion": float(estimate), "ee": float(se), "p": float(2 * stats.norm.sf(abs(z))),
            "ic95": [float(estimate - crit * se), float(estimate + crit * se)]}


def st_pierre(rows: list[dict], response: str, dose: str, study: str,
              covariates: list[str] | None = None, random_slope: bool = False,
              descomponer: bool = True) -> dict:
    """Modelo mixto respuesta ~ dosis (+ covariables) con el estudio como efecto aleatorio.

    - descomponer=True (por defecto): crea <dosis>_intra = dosis - media de su estudio y
      <dosis>_entre = media del estudio, ajusta ambas y devuelve `pendiente_intra`,
      `pendiente_entre` y su diferencia (`diferencia_entre_intra`). Si la dosis media no varía
      entre estudios, pendiente_entre es None (no es estimable). Exige que algún estudio tenga
      más de un nivel de dosis.
    - descomponer=False: modelo clásico con la dosis sin descomponer; devuelve `pendiente`, que
      mezcla información intra y entre estudios.
    - random_slope=True añade una pendiente aleatoria por estudio (sobre <dosis>_intra si se descompone).

    Las covariables entran sin descomponer. Los p-valores son de Wald z, sin Kenward-Roger.
    """
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Instala las dependencias opcionales: pip install "nutria[mixto]"') from exc

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

    formula = f"Q('{response}') ~ " + " + ".join(f"Q('{term}')" for term in [*dose_terms, *covariates])
    re_formula = f"~Q('{slope_term}')" if random_slope else "~1"
    model = smf.mixedlm(formula, df, groups=df[study], re_formula=re_formula)
    fit = model.fit(reml=True)
    params = fit.fe_params
    bse = fit.bse_fe

    def clean(name: str) -> str:
        return name.replace("Q('", "").replace("')", "")

    fixed = {}
    for name in params.index:
        entry = _wald(float(params[name]), float(bse[name]))
        entry["p"] = float(fit.pvalues[name])  # p de statsmodels (Wald z), igual que antes
        fixed[clean(name)] = entry

    notes = [
        "Modelo sin ponderar; St-Pierre (2001) discute ponderar por la precisión de cada media.",
        "Los p-valores son de Wald z (aproximación normal), sin corrección de Kenward-Roger para los grados de "
        "libertad. Para inferencia con pocos estudios, reajusta el modelo en R con lme4/lmerTest "
        "(Kenward-Roger o Satterthwaite).",
        "Revisa residuos, puntos influyentes y el rango de dosis de cada estudio antes de extrapolar.",
        "Una pendiente significativa entre estudios no demuestra causalidad en cada granja.",
    ]
    out = {
        "n_observaciones": int(fit.nobs),
        "n_estudios": int(n_groups),
        "convergencia": bool(getattr(fit, "converged", True)),
        "descomponer": bool(descomponer),
        "prueba": "Wald z (sin Kenward-Roger)",
        "efectos_fijos": fixed,
        "varianza_estudio": [[float(x) for x in row] for row in fit.cov_re.values],
        "varianza_residual": float(fit.scale),
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
            out["diferencia_entre_intra"] = _wald(diff, math.sqrt(var) if var > 0 else float("nan"))
            notes.insert(0, "Descomposición intra/entre estudios (enfoque de Mundlak): pendiente_intra usa solo "
                            "la variación de dosis dentro de cada estudio; pendiente_entre es la asociación entre "
                            "las dosis medias y las respuestas medias de los estudios, expuesta a confusión "
                            "(sesgo ecológico). Si difieren, la pendiente sin descomponer mezcla ambas y no "
                            "estima la respuesta intraestudio.")
        else:
            notes.insert(0, "La dosis media es igual en todos los estudios: no hay información entre estudios y "
                            "la pendiente es intraestudio; pendiente_entre no es estimable.")
    else:
        out["pendiente"] = fixed[dose]
        notes.insert(0, "Sin descomponer, la pendiente mezcla información intra y entre estudios (el intercepto "
                        "aleatorio no la aísla); usa descomponer=True para separarlas.")
    if random_slope:
        notes.append("Con pendiente aleatoria, la heterogeneidad de la respuesta entre estudios queda en la matriz "
                     "de covarianzas del estudio.")
    return out


def read_csv(path: Path, sep: str = ",") -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=sep))
