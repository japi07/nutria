"""Metaanálisis de medias de tratamiento con el estudio como efecto aleatorio
(St-Pierre 2001, J Dairy Sci 84:741-755; Sauvant et al. 2008, Animal 2:1203-1214).

Es el enfoque estándar en nutrición animal para relaciones dosis-respuesta entre estudios:
la pendiente se estima DENTRO de cada estudio, eliminando el sesgo que produce comparar
medias de estudios con dietas, animales y condiciones distintas.

Requiere `statsmodels` (pip install "nutria[mixto]").
"""

from __future__ import annotations

import csv
from pathlib import Path


def st_pierre(rows: list[dict], response: str, dose: str, study: str,
              covariates: list[str] | None = None, random_slope: bool = False) -> dict:
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Instala las dependencias opcionales: pip install "nutria[mixto]"') from exc

    covariates = covariates or []
    df = pd.DataFrame(rows)
    for col in [response, dose, *covariates]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[response, dose, study, *covariates])
    n_groups = df[study].nunique()
    if n_groups < 3:
        raise ValueError("Se necesitan al menos 3 estudios")
    formula = f"Q('{response}') ~ Q('{dose}')" + "".join(f" + Q('{c}')" for c in covariates)
    re_formula = f"~Q('{dose}')" if random_slope else "~1"
    model = smf.mixedlm(formula, df, groups=df[study], re_formula=re_formula)
    fit = model.fit(reml=True)
    params = fit.fe_params
    out = {
        "n_observaciones": int(fit.nobs),
        "n_estudios": int(n_groups),
        "convergencia": bool(getattr(fit, "converged", True)),
        "efectos_fijos": {
            name.replace("Q('", "").replace("')", ""): {
                "estimacion": float(params[name]), "ee": float(fit.bse_fe[name]),
                "p": float(fit.pvalues[name]),
            } for name in params.index
        },
        "varianza_estudio": [[float(x) for x in row] for row in fit.cov_re.values],
        "varianza_residual": float(fit.scale),
        "notas": [
            "Modelo sin ponderar; St-Pierre (2001) discute ponderar por la precisión de cada media.",
            "Revisa residuos, puntos influyentes y el rango de dosis de cada estudio antes de extrapolar.",
            "Una pendiente significativa entre estudios no demuestra causalidad en cada granja.",
        ],
    }
    if random_slope:
        out["notas"].append("Con pendiente aleatoria, la heterogeneidad de la respuesta entre estudios queda "
                            "en la matriz de covarianzas del estudio.")
    return out


def read_csv(path: Path, sep: str = ",") -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=sep))
