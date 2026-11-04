"""Validación de modelos predictivos dejando un estudio fuera (LOSO, leave-one-study-out).

Un modelo de predicción construido con datos de varios estudios (p. ej. producción de leche en
función de la dosis y de la dieta basal) se valida bien solo si se predice cada estudio con un
modelo ajustado sin él: así se estima el error al aplicar el modelo a un estudio nuevo (otra
dieta, otra granja), que es su uso real. Validar dejando fuera filas sueltas del mismo estudio
da una imagen optimista, porque las filas de un estudio comparten condiciones.

`loso_cv` ajusta una regresión lineal con intercepto por mínimos cuadrados (numpy), sin
ponderar y sin efectos aleatorios, y la compara con el modelo nulo (media de la respuesta en
los estudios de entrenamiento).
"""

from __future__ import annotations

import math

import numpy as np

from .metareg import _redundant_columns


def _number(value) -> float | None:
    """Valor numérico finito o None si falta o no es numérico."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _study_label(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def loso_cv(rows: list[dict], response: str, predictors: list[str] | str, study: str) -> dict:
    """Validación cruzada dejando un estudio fuera de una regresión lineal con intercepto.

    Para cada estudio s: ajusta respuesta ~ predictores con los demás estudios, predice las filas de
    s y calcula los errores (observado - predicho); el modelo nulo predice la media de la respuesta
    en el entrenamiento. Devuelve los errores por estudio, RMSE y MAE fuera de estudio y
    R² fuera de estudio = 1 - Σe²/Σe0² (e0: errores del modelo nulo). Un R² negativo indica que el
    modelo predice peor que la media. Se descartan (y se cuentan) las filas con respuesta,
    predictor o estudio ausente o no numérico. Exige al menos 3 estudios.
    """
    if isinstance(predictors, str):
        predictors = [predictors]
    predictors = list(predictors or [])
    if not predictors:
        raise ValueError("Indica al menos un predictor")
    if len(set(predictors)) != len(predictors):
        raise ValueError("Hay predictores repetidos")
    if response in predictors or study in predictors:
        raise ValueError("La respuesta y la columna de estudio no pueden ser predictores")
    columns = set().union(*(row.keys() for row in rows)) if rows else set()
    absent = [col for col in dict.fromkeys([response, *predictors, study]) if col not in columns]
    if absent:
        raise ValueError(f"Faltan columnas en los datos: {', '.join(absent)}")

    y_list, x_list, s_list = [], [], []
    dropped = 0
    for row in rows:
        y = _number(row.get(response))
        xs = [_number(row.get(col)) for col in predictors]
        s = _study_label(row.get(study))
        if y is None or s is None or any(x is None for x in xs):
            dropped += 1
            continue
        y_list.append(y)
        x_list.append(xs)
        s_list.append(s)
    studies = list(dict.fromkeys(s_list))
    if len(studies) < 3:
        raise ValueError(f"La validación dejando un estudio fuera necesita al menos 3 estudios con datos "
                         f"completos (hay {len(studies)})")
    y_all = np.asarray(y_list, dtype=float)
    X_all = np.column_stack([np.ones(len(y_list)), np.asarray(x_list, dtype=float)])
    labels = np.asarray([studies.index(s) for s in s_list])
    p = X_all.shape[1]
    names = ["intercepto", *predictors]

    per_study = []
    errors = np.empty_like(y_all)
    null_errors = np.empty_like(y_all)
    for idx, name in enumerate(studies):
        test = labels == idx
        train = ~test
        X_train, y_train = X_all[train], y_all[train]
        if X_train.shape[0] <= p:
            raise ValueError(f"Sin el estudio {name} quedan {X_train.shape[0]} filas para {p} coeficientes: "
                             "hacen falta más filas que coeficientes")
        redundant = _redundant_columns(X_train)
        if redundant:
            raise ValueError(f"Sin el estudio {name}, los predictores {', '.join(names[j] for j in redundant)} son "
                             "constantes o colineales en el entrenamiento: el modelo no está identificado")
        beta, *_ = np.linalg.lstsq(X_train, y_train, rcond=None)
        e = y_all[test] - X_all[test] @ beta
        e0 = y_all[test] - float(np.mean(y_train))
        errors[test] = e
        null_errors[test] = e0
        per_study.append({
            "estudio": name,
            "n": int(test.sum()),
            "errores": [float(x) for x in e],
            "sesgo_medio": float(np.mean(e)),
            "mae": float(np.mean(np.abs(e))),
            "rmse": float(math.sqrt(np.mean(e ** 2))),
            "rmse_nulo": float(math.sqrt(np.mean(e0 ** 2))),
        })

    sse = float(np.sum(errors ** 2))
    sse_null = float(np.sum(null_errors ** 2))
    n = y_all.size
    r2 = 1.0 - sse / sse_null if sse_null > 0 else None
    beta_full, *_ = np.linalg.lstsq(X_all, y_all, rcond=None)
    notes = [
        "Errores = observado - predicho, con un modelo ajustado sin el estudio que se predice: estiman el error "
        "en un estudio nuevo, no en animales nuevos del mismo estudio.",
        "Regresión lineal sin ponderar y sin efectos aleatorios de estudio; las filas de un mismo estudio no son "
        "independientes, así que los coeficientes del modelo completo son descriptivos.",
    ]
    if r2 is None:
        notes.append("La respuesta es constante: el R² fuera de estudio no está definido.")
    elif r2 < 0:
        notes.append("R² fuera de estudio < 0: el modelo predice los estudios nuevos peor que la media de "
                     "entrenamiento.")
    if dropped:
        notes.append(f"Se descartaron {dropped} fila(s) con respuesta, predictor o estudio ausente o no numérico.")
    if len(studies) < 10:
        notes.append(f"Solo {len(studies)} estudios: las métricas fuera de estudio son muy imprecisas.")
    return {
        "respuesta": response,
        "predictores": predictors,
        "n_observaciones": int(n),
        "n_estudios": len(studies),
        "filas_descartadas": dropped,
        "por_estudio": per_study,
        "rmse": float(math.sqrt(sse / n)),
        "mae": float(np.mean(np.abs(errors))),
        "rmse_nulo": float(math.sqrt(sse_null / n)),
        "mae_nulo": float(np.mean(np.abs(null_errors))),
        "r2_fuera_estudio": r2,
        "coeficientes_modelo_completo": {name: float(b) for name, b in zip(names, beta_full)},
        "notas": notes,
    }
