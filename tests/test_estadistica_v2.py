"""Pruebas del paquete de estadística: diseño y unidad experimental (D6-3), St-Pierre (D6-4), HKSJ con
q < 1 (D6-7), intervalo de predicción y plantilla de R (D6-9), potencia con efecto de diseño (D6-11),
cobertura de la parte propia de la ciencia animal y de la ruta CLI (D6-12) y procedencia de `meta` (D9-7).

Los valores esperados se calculan a mano en la propia prueba (fórmula escrita y, cuando ayuda, el número),
no llamando a la función que se prueba.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import warnings

import numpy as np
import pytest
from scipy import stats

from nutria.cli import main
from nutria.stats import effect_sizes as es
from nutria.stats import meta, power
from nutria.stats.metareg import ModeradorNoEstimable, codificar_moderadores, meta_regression

# Columnas del CSV de análisis de `nutria extraccion exportar` que usan estas pruebas.
COLUMNAS = ["estudio", "comparacion", "variable", "unidad", "especie", "diseno", "unidad_experimental",
            "media_trt", "media_ctrl", "disp_trt", "disp_ctrl", "tipo_disp", "n_trt", "n_ctrl"]


# --------------------------------------------------------------------------- utilidades
def _csv(path, rows, campos=None):
    campos = campos or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=campos)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in campos})
    return path


def _fila(estudio, media_trt, media_ctrl, disp, n, tipo="SEM_combinado", diseno="paralelo", unidad="animal",
          disp_ctrl="", **extra):
    return {"estudio": estudio, "comparacion": f"{estudio}a", "variable": "leche", "unidad": "kg/d",
            "especie": "vacuno lechero", "diseno": diseno, "unidad_experimental": unidad, "media_trt": media_trt,
            "media_ctrl": media_ctrl, "disp_trt": disp, "disp_ctrl": disp_ctrl, "tipo_disp": tipo, "n_trt": n,
            "n_ctrl": n, **extra}


def _json(capsys, *argv):
    """Ejecuta `nutria --json ...` y devuelve (código, JSON o None, stderr)."""
    capsys.readouterr()
    code = main(["--json", *argv])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 and out.out.strip() else None), out.err


def _texto(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def _eventos(accion):
    from nutria.config import load_config
    from nutria.provenance import read_events
    return read_events(load_config(), action=accion)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Tres estudios paralelos casi idénticos (k = 3, SEM combinado): q = 0,1175 < 1, el caso de D6-7.
LECHE = [_fila("E1", 31.0, 30.0, 0.5, 12), _fila("E2", 29.6, 29.0, 0.6, 10), _fila("E3", 32.2, 31.0, 0.7, 8)]


# =========================================================================== D6-12: tamaños de efecto a mano
def test_smd_paralelo_con_correccion_de_hedges_a_mano():
    # g = J·d, J = 1 - 3/(4·gl - 1), gl = n_t + n_c - 2; vi = (n_t + n_c)/(n_t·n_c) + g²/(2·(n_t + n_c))
    # 1) DE iguales: d = (11 - 10)/2 = 0,5; J = 1 - 3/151; vi = 40/400 + g²/80
    g, v = es.effect_from_summary("SMD", 11, 10, 20, 20, "SD", 2, 2)
    assert g == pytest.approx(0.5 * (1 - 3 / 151)) == pytest.approx(0.4900662)
    assert v == pytest.approx(0.1 + g ** 2 / 80) == pytest.approx(0.1030021)
    # 2) DE y n distintos: S_p² = (9·16 + 11·9)/20 = 12,15; J = 1 - 3/79; vi = 22/120 + g²/44
    g, v = es.hedges_g(31, 30, 4, 3, 10, 12)
    assert g == pytest.approx((1 - 3 / 79) / math.sqrt(12.15)) == pytest.approx(0.2759932)
    assert v == pytest.approx(22 / 120 + g ** 2 / 44) == pytest.approx(0.1850645)
    # 3) Desde un SEM combinado (paralelo): DE = SEM·√n = 0,5·√12; J = 1 - 3/87; vi = 24/144 + g²/48
    g, v = es.effect_from_summary("SMD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5)
    assert g == pytest.approx((1 - 3 / 87) / (0.5 * math.sqrt(12))) == pytest.approx(0.5574416)
    assert v == pytest.approx(24 / 144 + g ** 2 / 48) == pytest.approx(0.1731404)


def test_rom_paralelo_a_mano():
    # yi = ln(m_t/m_c); vi = sd_t²/(n_t·m_t²) + sd_c²/(n_c·m_c²)
    y, v = es.effect_from_summary("ROM", 11, 10, 20, 20, "SD", 2, 2)
    assert y == pytest.approx(math.log(1.1))
    assert v == pytest.approx(4 / (20 * 121) + 4 / (20 * 100)) == pytest.approx(0.003652893)
    # Con SEM combinado: sd²/n = SEM², así que vi = SEM²·(1/m_t² + 1/m_c²)
    y, v = es.effect_from_summary("ROM", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5)
    assert y == pytest.approx(math.log(31 / 30))
    assert v == pytest.approx(0.25 * (1 / 961 + 1 / 900)) == pytest.approx(0.0005379235)


# =========================================================================== D6-3: diseños cruzados
def test_smd_cuadrado_latino_con_sem_residual_a_mano():
    # Borenstein et al. (2009, cap. 4): s_w = SEM·√n (DE intraanimal), S_within = s_w/√(1 - r),
    # d = D/S_within, V_d = (1/n + d²/(2n))·2(1 - r), J = 1 - 3/(4·(n - 1) - 1), g = J·d, V_g = J²·V_d.
    # SEM 0,6 y n = 8: s_w = 1,697; con r = 0,5, S_within = 2,4 y d = 1/2,4.
    g, v = es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SEM_combinado", 0.6, diseno="cuadrado_latino",
                                  rho_intrasujeto=0.5)
    j, d = 1 - 3 / 27, 1 / 2.4
    assert g == pytest.approx(j * d) == pytest.approx(0.3703704)
    assert v == pytest.approx(j ** 2 * (1 / 8 + d ** 2 / 16) * 2 * 0.5) == pytest.approx(0.1073388)
    # El SED equivalente (SED = SEM·√2) da lo mismo: s_w = SED·√(n/2)
    assert es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SED", 0.6 * math.sqrt(2), diseno="crossover",
                                  rho_intrasujeto=0.5) == pytest.approx((g, v))


def test_smd_cruzado_queda_en_la_escala_del_paralelo():
    # Reproducción de D6-3: el mismo efecto de 1 kg/d, DE entre animales 4 y DE intraanimal 1,5.
    paralelo, _ = es.effect_from_summary("SMD", 31.0, 30.0, 20, 20, "SD", 4.0, 4.0)
    assert paralelo == pytest.approx(0.2450331)
    sem = 1.5 / math.sqrt(8)
    # Antes (como si el SEM residual fuera el de un paralelo) salía 0,630, 2,6 veces más:
    antes, _ = es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SEM_combinado", sem)
    assert antes == pytest.approx(0.6303030)
    # Con el diseño declarado y la r coherente con esas DE (σ_w = σ·√(1 - r)), la escala es la del paralelo:
    r = 1 - (1.5 / 4) ** 2
    cruzado, _ = es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SEM_combinado", sem, diseno="cuadrado_latino",
                                        rho_intrasujeto=r)
    assert cruzado == pytest.approx((1 - 3 / 27) * 1 / 4)
    # Sin r, la SMD de un diseño cruzado no se calcula (falla en cerrado, con la salida indicada)
    with pytest.raises(ValueError, match="correlación intrasujeto"):
        es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SEM_combinado", sem, diseno="crossover")


def test_md_y_rom_cruzados_a_mano():
    # MD con dispersión residual: vi = 2·SEM², con o sin r (la varianza entre animales se cancela)
    for r in (None, 0.5):
        y, v = es.effect_from_summary("MD", 31.0, 30.0, 8, 8, "SEM_combinado", 0.6, diseno="crossover",
                                      rho_intrasujeto=r)
        assert (y, v) == pytest.approx((1.0, 2 * 0.6 ** 2))
    # MD con DE por tratamiento: vi = (sd_t² + sd_c² - 2·r·sd_t·sd_c)/n = (16 + 16 - 24)/8 = 1
    assert es.effect_from_summary("MD", 31, 30, 8, 8, "SD", 4, 4, diseno="crossover",
                                  rho_intrasujeto=0.75)[1] == pytest.approx(1.0)
    # Sin r: como grupos independientes (conservador), igual que un paralelo con 8 por grupo: 32/8 = 4
    assert es.effect_from_summary("MD", 31, 30, 8, 8, "SD", 4, 4, diseno="crossover")[1] == pytest.approx(4.0)
    # ROM con SEM residual y r = 0,5: s_w²/n·(1/m_t² + 1/m_c²) + r/(1 - r)·s_w²/n·(1/m_t - 1/m_c)², s_w²/n = 0,36
    _, v = es.effect_from_summary("ROM", 31.0, 30.0, 8, 8, "SEM_combinado", 0.6, diseno="cuadrado_latino",
                                  rho_intrasujeto=0.5)
    assert v == pytest.approx(0.36 * (1 / 961 + 1 / 900) + 0.36 * (1 / 31 - 1 / 30) ** 2)
    # ROM con DE por tratamiento (Lajeunesse 2011): sd_t²/(n·m_t²) + sd_c²/(n·m_c²) - 2·r·sd_t·sd_c/(n·m_t·m_c)
    y, v = es.effect_from_summary("ROM", 31, 30, 8, 8, "SD", 4, 3, diseno="crossover", rho_intrasujeto=0.6)
    assert y == pytest.approx(math.log(31 / 30))
    assert v == pytest.approx(16 / (8 * 961) + 9 / (8 * 900) - 2 * 0.6 * 12 / (8 * 930)) == pytest.approx(0.001395682)


def test_diseno_y_unidad_normalizados_y_r_validada():
    assert es.normalize_design("Cuadrado latino") == "cuadrado_latino"
    assert es.normalize_design("cross-over") == "crossover"
    assert es.normalize_design("Latin square") == "cuadrado_latino"
    assert es.normalize_design("") is None and es.normalize_design(None) is None
    with pytest.raises(ValueError, match="Diseño desconocido"):
        es.normalize_design("crossover 3x3")
    assert es.normalize_unit("Pen") == "corral" and es.normalize_unit("vaca") == "animal"
    for malo in (1.0, -0.1, "alta"):
        with pytest.raises(ValueError, match="rho_intrasujeto"):
            es.effect_from_summary("MD", 31, 30, 8, 8, "SD", 4, 4, diseno="crossover", rho_intrasujeto=malo)
    # En diseños no cruzados la r no se usa
    assert es.effect_from_summary("MD", 31, 30, 8, 8, "SD", 4, 4, diseno="paralelo", rho_intrasujeto=0.9)[1] == \
        pytest.approx(4.0)


# =========================================================================== D6-3: CLI con diseño y unidad
MEZCLA = [
    _fila("P1", 31.0, 30.0, 4.0, 20, tipo="SD", disp_ctrl=4.0),
    _fila("P2", 30.5, 30.0, 3.5, 16, tipo="SD", disp_ctrl=3.5),
    _fila("X1", 31.0, 30.0, 0.6, 8, diseno="cuadrado_latino"),
    _fila("X2", 30.8, 30.0, 0.5, 12, diseno="Crossover"),
    _fila("C1", 31.0, 30.0, 0.9, 4, unidad="corral"),
]


def test_cli_avisa_al_mezclar_disenos_y_unidades(project, capsys):
    path = _csv(project / "mezcla.csv", MEZCLA, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path))
    assert code == 0
    assert out["disenos"] == {"paralelo": 3, "cuadrado_latino": 1, "crossover": 1}
    assert out["unidades_experimentales"] == {"animal": 4, "corral": 1}
    avisos = " ".join(out["avisos_diseno"])
    assert "Se combinan diseños distintos" in avisos and "--moderador diseno" in avisos
    assert "Se combinan unidades experimentales distintas" in avisos
    assert any("Se combinan diseños distintos" in a for a in out["avisos"])  # también en la lista general
    assert any("residual (intraanimal)" in n for n in out["notas_diseno"])
    # Las filas cruzadas usan vi = 2·SEM² con n = animales (no la fórmula del paralelo)
    yi = meta.random_effects([1.0, 0.5, 1.0, 0.8, 1.0],
                             [2 * 16 / 20, 2 * 12.25 / 16, 2 * 0.36, 2 * 0.25, 2 * 0.81], ajuste="hksj-mod")
    assert out["modelo"]["estimate"] == pytest.approx(yi.estimate)
    # En texto, los avisos van al principio
    code, text, _ = _texto(capsys, "meta", "analizar", str(path))
    assert code == 0 and text.startswith("AVISO: Se combinan diseños distintos")


def test_cli_smd_cruzado_exige_r_y_la_usa(project, capsys):
    path = _csv(project / "mezcla.csv", MEZCLA, COLUMNAS)
    code, _, err = _json(capsys, "meta", "analizar", str(path), "--medida", "SMD")
    assert code == 2 and "correlación intrasujeto" in err and "--rho-intrasujeto" in err
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--medida", "SMD", "--rho-intrasujeto", "0.5")
    assert code == 0
    assert any("[SUP]" in a and "X1" in a for a in out["avisos_diseno"])  # r supuesta: aviso con las filas
    # La columna rho_intrasujeto manda sobre --rho-intrasujeto; "nan" cuenta como ausente
    filas = [dict(f, rho_intrasujeto="0.7" if f["estudio"] == "X1" else "nan") for f in MEZCLA]
    path2 = _csv(project / "mezcla_r.csv", filas, COLUMNAS + ["rho_intrasujeto"])
    code, out2, _ = _json(capsys, "meta", "exportar-r", str(path2), "--medida", "SMD", "--rho-intrasujeto", "0.5",
                          "--salida", str(project / "m.R"))
    assert code == 0
    with open(project / "m.csv", encoding="utf-8") as fh:
        exportadas = {r["estudio"]: r for r in csv.DictReader(fh)}
    esperado_x1 = es.effect_from_summary("SMD", 31.0, 30.0, 8, 8, "SEM_combinado", 0.6, diseno="cuadrado_latino",
                                         rho_intrasujeto=0.7)
    esperado_x2 = es.effect_from_summary("SMD", 30.8, 30.0, 12, 12, "SEM_combinado", 0.5, diseno="crossover",
                                         rho_intrasujeto=0.5)
    assert (float(exportadas["X1"]["yi"]), float(exportadas["X1"]["vi"])) == pytest.approx(esperado_x1)
    assert (float(exportadas["X2"]["yi"]), float(exportadas["X2"]["vi"])) == pytest.approx(esperado_x2)
    # Diseño exportado normalizado (las categorías que usó nutria)
    assert exportadas["X2"]["diseno"] == "crossover"
    # Una r fuera de [0, 1) es un error
    code, _, err = _json(capsys, "meta", "analizar", str(path), "--medida", "SMD", "--rho-intrasujeto", "1.2")
    assert code == 2 and "[0, 1)" in err


def test_cli_moderador_categorico_diseno(project, capsys):
    path = _csv(project / "mezcla.csv", MEZCLA, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--moderador", "diseno")
    assert code == 0
    reg = out["meta_regresion"]
    assert reg["names"] == ["intercepto", "diseno[crossover]", "diseno[cuadrado_latino]"]
    assert reg["moderadores"]["diseno"]["referencia"] == "paralelo"
    # Mismo resultado que la codificación ficticia a mano (paralelo = referencia)
    yi = [1.0, 0.5, 1.0, 0.8, 1.0]
    vi = [2 * 16 / 20, 2 * 12.25 / 16, 2 * 0.36, 2 * 0.25, 2 * 0.81]
    mods = [[0, 0], [0, 0], [0, 1], [1, 0], [0, 0]]
    manual = meta_regression(yi, vi, mods, ["c", "l"], ajuste="hksj-mod")
    assert reg["coef"] == pytest.approx(manual.coef)
    assert reg["p_values"] == pytest.approx(manual.p_values)
    # Otra referencia declarada
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--moderador", "diseno", "--referencia",
                         "diseno=crossover")
    assert out["meta_regresion"]["names"] == ["intercepto", "diseno[cuadrado_latino]", "diseno[paralelo]"]
    # Unidad experimental como moderador: referencia animal
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--moderador", "unidad_experimental")
    assert out["meta_regresion"]["names"] == ["intercepto", "unidad_experimental[corral]"]
    # Errores de los datos: fallan con código 2
    assert _json(capsys, "meta", "analizar", str(path), "--moderador", "disenno")[0] == 2
    assert _json(capsys, "meta", "analizar", str(path), "--moderador", "diseno", "--referencia",
                 "diseno=factorial")[0] == 2
    assert _json(capsys, "meta", "analizar", str(path), "--referencia", "diseno=paralelo")[0] == 2
    # Un moderador sin contraste (una sola categoría) se informa como no estimable, sin abortar
    solo = _csv(project / "solo.csv", LECHE, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(solo), "--moderador", "diseno")
    assert code == 0 and "una categoría" in out["meta_regresion"]["error"]


def test_cli_subgrupo_por_diseno_normalizado(project, capsys):
    path = _csv(project / "mezcla.csv", MEZCLA, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--subgrupo", "diseno")
    assert code == 0
    assert set(out["subgrupos"]["subgrupos"]) == {"paralelo", "cuadrado_latino", "crossover"}
    filas = [dict(f, diseno="") if f["estudio"] == "P2" else f for f in MEZCLA]
    vacia = _csv(project / "vacia.csv", filas, COLUMNAS)
    code, _, err = _json(capsys, "meta", "analizar", str(vacia), "--subgrupo", "diseno")
    assert code == 2 and "vacía" in err


def test_codificar_moderadores_numericos_y_categoricos():
    filas = [{"dosis": "10", "diseno": "paralelo", "lote": "A"}, {"dosis": "20", "diseno": "crossover", "lote": "B"},
             {"dosis": "30", "diseno": "paralelo", "lote": "A"}, {"dosis": "40", "diseno": "crossover", "lote": "C"}]
    matriz, nombres, desc = codificar_moderadores(filas, ["dosis", "diseno", "lote"])
    assert nombres == ["dosis", "diseno[crossover]", "lote[B]", "lote[C]"]
    assert desc["lote"]["referencia"] == "A" and desc["dosis"] == {"tipo": "numerico"}
    assert matriz.tolist() == [[10, 0, 0, 0], [20, 1, 1, 0], [30, 0, 0, 0], [40, 1, 0, 1]]
    # Una columna numérica se puede declarar categórica con su referencia
    _, nombres, _ = codificar_moderadores(filas, ["dosis"], {"dosis": "20"})
    assert nombres == ["dosis[10]", "dosis[30]", "dosis[40]"]
    with pytest.raises(ValueError, match="mezcla números y texto"):
        codificar_moderadores([{"d": "1"}, {"d": "n.d."}, {"d": "3"}], ["d"])
    with pytest.raises(ValueError, match="mayúsculas"):
        codificar_moderadores([{"g": "Alta"}, {"g": "alta"}, {"g": "baja"}], ["g"])
    with pytest.raises(ModeradorNoEstimable):
        codificar_moderadores([{"diseno": "paralelo"}] * 3, ["diseno"])


def test_cli_lectura_estricta_del_csv(project, capsys):
    # Una coma decimal sin comillas en un CSV separado por comas desplazaría las columnas: error, no datos erróneos
    (project / "coma.csv").write_text("estudio,yi,vi\nA,0,5,0.1\nB,0.4,0.2\nC,0.3,0.1\n", "utf-8")
    code, _, err = _json(capsys, "meta", "analizar", str(project / "coma.csv"))
    assert code == 2 and "más campos que la cabecera" in err
    # Una coma final (campo sobrante vacío) no desplaza nada y se acepta
    (project / "final.csv").write_text("estudio,yi,vi\nA,0.5,0.1,\nB,0.4,0.2,\nC,0.3,0.1\n", "utf-8")
    code, out, _ = _json(capsys, "meta", "analizar", str(project / "final.csv"))
    assert code == 0 and out["k"] == 3
    (project / "coma2.csv").write_text("estudio;yi;vi\nA;0,5;0,1\nB;0,4;0,2\nC;0,3;0,1\n", "utf-8")
    code, out, _ = _json(capsys, "meta", "analizar", str(project / "coma2.csv"), "--sep", ";", "--decimal", ",")
    assert code == 0 and out["k"] == 3


# =========================================================================== D6-7: HKSJ con q < 1
def test_hksj_modificado_por_defecto_y_nunca_mas_estrecho_que_wald(project, capsys):
    path = _csv(project / "leche.csv", LECHE, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path))
    assert code == 0
    mod = out["modelo"]
    assert mod["ajuste"] == "hksj-mod" and "HKSJ modificado" in mod["test"]
    assert mod["hksj_q"] == pytest.approx(0.1175, abs=1e-4)
    # Reproducción de D6-7: el modificado da p 0,193 e IC [-1,1288; 2,9693]; el estándar (0,030) es más estrecho
    # que Wald (p 0,053) y solo aparece como sensibilidad.
    assert mod["p_value"] == pytest.approx(0.193, abs=5e-4)
    assert (mod["ci_low"], mod["ci_high"]) == pytest.approx((-1.1288, 2.9693), abs=1e-4)
    assert mod["p_hksj"] == pytest.approx(0.0301, abs=1e-4) and mod["p_wald"] == pytest.approx(0.0533, abs=1e-4)
    assert mod["ci_high"] - mod["ci_low"] >= mod["ci_wald_high"] - mod["ci_wald_low"]
    code, text, _ = _texto(capsys, "meta", "analizar", str(path))
    assert "HKSJ estándar (q = 0.1175 < 1; más estrecho, solo sensibilidad)" in text


def test_hksj_estandar_con_q_menor_que_1_avisa_e_imprime_p_e_ic_modificados(project, capsys):
    path = _csv(project / "leche.csv", LECHE, COLUMNAS)
    code, text, _ = _texto(capsys, "meta", "analizar", str(path), "--ajuste", "hksj")
    assert code == 0
    primera = text.splitlines()[0]
    assert primera.startswith("AVISO: El IC95 del HKSJ estándar (q = 0.1175 < 1) es más estrecho que el de Wald")
    assert "p = 0.193" in primera and "[-1.1288; 2.9693]" in primera
    assert "IC95 HKSJ modificado (max(1, q), q = 0.1175): [-1.1288; 2.9693]; p = 0.193" in text
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--ajuste", "hksj")
    assert out["modelo"]["p_mod"] == pytest.approx(0.193, abs=5e-4) and out["modelo"]["avisos"]


def test_hksj_estandar_avisa_tambien_en_uno_fuera_subgrupos_y_meta_regresion(project, capsys):
    # Efectos casi idénticos: q << 1 en el modelo global, en cada análisis uno-fuera y en cada subgrupo
    filas = [{"estudio": f"S{i}", "yi": y, "vi": 0.1, "grupo": g, "dosis": d}
             for i, (y, g, d) in enumerate([(1.0, "A", 1), (1.01, "A", 2), (0.99, "A", 3), (1.0, "A", 4),
                                            (1.02, "B", 1), (0.98, "B", 2), (1.0, "B", 3), (1.01, "B", 4)])]
    path = _csv(project / "homogeneos.csv", filas)
    code, text, _ = _texto(capsys, "meta", "analizar", str(path), "--ajuste", "hksj", "--subgrupo", "grupo",
                           "--moderador", "dosis")
    assert code == 0 and text.startswith("AVISO: El IC95 del HKSJ estándar")
    assert "AVISO: en 8 de 8 análisis uno-fuera" in text
    assert "AVISO (subgrupo A)" in text and "AVISO (subgrupo B)" in text
    assert "AVISO: Knapp-Hartung estándar con s²" in text and "Knapp-Hartung modificado (max(1, s²)" in text
    # Los mismos avisos van en el JSON (clave avisos) y en la procedencia
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--ajuste", "hksj", "--subgrupo", "grupo",
                         "--moderador", "dosis")
    avisos = out["avisos"]
    assert any(a.startswith("AVISO: El IC95 del HKSJ estándar") for a in avisos)
    assert any("uno-fuera" in a for a in avisos) and any(a.startswith("AVISO (subgrupo B)") for a in avisos)
    assert any("Knapp-Hartung estándar" in a for a in avisos)
    assert _eventos("metaanalisis")[-1]["outputs"]["avisos"] == avisos
    # Con el ajuste por defecto (modificado) no hay ningún IC más estrecho que Wald que avisar
    code, text, _ = _texto(capsys, "meta", "analizar", str(path), "--subgrupo", "grupo", "--moderador", "dosis")
    assert code == 0 and "AVISO" not in text


def test_hksj_modificado_contiene_siempre_a_wald():
    rng = np.random.default_rng(11)
    for _ in range(100):
        k = int(rng.integers(2, 8))
        yi = rng.normal(0.5, rng.uniform(0.01, 0.5), k)
        vi = rng.uniform(0.01, 0.3, k)
        for metodo in ("REML", "DL", "PM"):
            res = meta.random_effects(yi, vi, method=metodo, ajuste="hksj-mod")
            assert res.ci_low <= res.ci_wald_low + 1e-12 and res.ci_high >= res.ci_wald_high - 1e-12
            std = meta.random_effects(yi, vi, method=metodo, ajuste="hksj")
            if std.ci_high - std.ci_low < std.ci_wald_high - std.ci_wald_low:
                assert std.avisos and std.p_mod == pytest.approx(res.p_value)


def test_hksj_modificado_igual_al_estandar_con_q_mayor_que_1(bcg):
    yi, vi, _ = bcg
    std = meta.random_effects(yi, vi, ajuste="hksj")
    mod = meta.random_effects(yi, vi, ajuste="hksj-mod")
    assert std.hksj_q > 1
    assert (mod.ci_low, mod.ci_high, mod.p_value) == pytest.approx((std.ci_low, std.ci_high, std.p_value))
    assert not std.avisos and not mod.avisos


# =========================================================================== D6-9: intervalo de predicción
def test_intervalo_de_prediccion_bcg_a_mano(bcg):
    yi, vi, _ = bcg
    res = meta.random_effects(yi, vi, method="REML")
    w = 1 / (vi + res.tau2)
    se_wald = math.sqrt(1 / w.sum())
    # Higgins et al. (2009): μ ± t(k - 2)·√(tau² + EE²), con el EE de Wald; k = 13 -> 11 gl
    half = stats.t.ppf(0.975, 11) * math.sqrt(res.tau2 + se_wald ** 2)
    assert (res.pi_low, res.pi_high) == pytest.approx((res.estimate - half, res.estimate + half))
    assert (res.pi_low, res.pi_high) == pytest.approx((-2.0084, 0.5793), abs=1e-4)
    assert res.pi_df == 11
    # Con z en lugar de t(k - 2), las mismas piezas reproducen predict() de metafor para dat.bcg con
    # test = "z": [-1,8667; 0,4376] (exp: [0,15; 1,55]), lo que valida tau², el efecto medio y el EE.
    half_z = stats.norm.ppf(0.975) * math.sqrt(res.tau2 + se_wald ** 2)
    assert (res.estimate - half_z, res.estimate + half_z) == pytest.approx((-1.8667, 0.4376), abs=1e-4)
    # El ajuste del IC no cambia el IP
    for ajuste in ("hksj", "hksj-mod"):
        otro = meta.random_effects(yi, vi, method="REML", ajuste=ajuste)
        assert (otro.pi_low, otro.pi_high) == pytest.approx((res.pi_low, res.pi_high))
    # DL: el mismo cálculo con su tau²
    dl = meta.random_effects(yi, vi, method="DL")
    w = 1 / (vi + dl.tau2)
    half = stats.t.ppf(0.975, 11) * math.sqrt(dl.tau2 + 1 / w.sum())
    assert (dl.pi_low, dl.pi_high) == pytest.approx((dl.estimate - half, dl.estimate + half))
    assert (dl.pi_low, dl.pi_high) == pytest.approx((-1.9988, 0.5706), abs=1e-4)


def test_intervalo_de_prediccion_casos_limite():
    # k = 3: 1 gl
    res = meta.random_effects([0.1, 0.5, 0.9], [0.04, 0.05, 0.06], method="DL")
    w = 1 / (np.array([0.04, 0.05, 0.06]) + res.tau2)
    half = stats.t.ppf(0.975, 1) * math.sqrt(res.tau2 + 1 / w.sum())
    assert res.pi_df == 1 and (res.pi_low, res.pi_high) == pytest.approx((res.estimate - half, res.estimate + half))
    # k = 2: no hay IP
    dos = meta.random_effects([0.1, 0.5], [0.04, 0.05])
    assert dos.pi_low is None and dos.pi_df is None and any("k < 3" in n for n in dos.notes)
    with pytest.raises(ValueError, match="k >= 3"):
        meta.prediction_interval(0.0, 0.1, 0.1, 2)
    assert meta.prediction_interval(0.0, 0.3, 0.16, 4, level=0.9) == pytest.approx(
        (-stats.t.ppf(0.95, 2) * 0.5, stats.t.ppf(0.95, 2) * 0.5))


def test_cli_intervalo_de_prediccion_y_texto(project, capsys):
    filas = LECHE + [_fila("E4", 30.0, 30.4, 0.6, 10), _fila("E5", 31.5, 30.0, 0.5, 12)]
    path = _csv(project / "cinco.csv", filas, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path))
    mod = out["modelo"]
    yi = [1.0, 0.6, 1.2, -0.4, 1.5]
    vi = [2 * 0.25, 2 * 0.36, 2 * 0.49, 2 * 0.36, 2 * 0.25]
    ref = meta.random_effects(yi, vi, method="REML")
    w = 1 / (np.array(vi) + ref.tau2)
    half = stats.t.ppf(0.975, 3) * math.sqrt(ref.tau2 + 1 / w.sum())
    assert (mod["pi_low"], mod["pi_high"]) == pytest.approx((ref.estimate - half, ref.estimate + half))
    code, text, _ = _texto(capsys, "meta", "analizar", str(path))
    assert "Intervalo de predicción 95 % (t con k - 2 = 3 gl y EE de Wald; Higgins et al. 2009)" in text


# =========================================================================== D6-9 y D6-12: exportar-r
def _inferencia_como_el_script(yi, vi, tau2, ajuste, metodo):
    """Las fórmulas del bloque 2) y 3) del script de R, escritas en Python."""
    y, v = np.asarray(yi), np.asarray(vi)
    k = y.size
    w = 1 / (v + tau2)
    mu = float(np.sum(w * y) / np.sum(w))
    if metodo == "FE" or ajuste == "ninguno" or k == 1:
        se, crit = math.sqrt(1 / w.sum()), stats.norm.ppf(0.975)
        p = 2 * stats.norm.sf(abs(mu / se))
    else:
        q = float(np.sum(w * (y - mu) ** 2) / (k - 1))
        factor = max(1, q) if ajuste == "hksj-mod" else (1 if q < 1e-10 else q)
        se, crit = math.sqrt(factor / w.sum()), stats.t.ppf(0.975, k - 1)
        p = 2 * stats.t.sf(abs(mu / se), k - 1)
    ip = None
    if metodo != "FE" and k >= 3:
        half = stats.t.ppf(0.975, k - 2) * math.sqrt(tau2 + 1 / w.sum())
        ip = (mu - half, mu + half)
    return mu, se, (mu - crit * se, mu + crit * se), p, ip


@pytest.mark.parametrize("metodo", ["REML", "DL", "PM", "FE"])
@pytest.mark.parametrize("ajuste", ["hksj-mod", "hksj", "ninguno"])
def test_exportar_r_plantilla_coherente_con_los_parametros(project, capsys, metodo, ajuste):
    filas = LECHE + [_fila("E4", 30.0, 30.4, 0.6, 10)]
    path = _csv(project / "cuatro.csv", filas, COLUMNAS)
    salida = project / "analisis.R"
    code, out, _ = _json(capsys, "meta", "exportar-r", str(path), "--salida", str(salida), "--metodo", metodo,
                         "--ajuste", ajuste)
    assert code == 0 and out["metodo"] == metodo and out["ajuste"] == ajuste
    script = salida.read_text("utf-8")
    assert f"--medida MD --metodo {metodo} --ajuste {ajuste}" in script
    assert f'method = "{metodo}"' in script
    codigo = [linea for linea in script.splitlines() if not linea.lstrip().startswith("#")]
    assert not any("predict(" in linea for linea in codigo)  # su convención de gl no es la de nutria
    if metodo == "FE":
        assert "intervalo de prediccion" not in script
    else:
        assert f'test = "{"z" if ajuste == "ninguno" else "knha"}"' in script
        assert "qt(1 - (1 - nivel) / 2, df = k - 2) * sqrt(tau2 + 1 / sum(w))" in script
        if ajuste == "hksj-mod":
            assert "se <- sqrt((max(1, q)) / sum(w))" in script
        elif ajuste == "hksj":
            assert "se <- sqrt((if (q < 1e-10) 1 else q) / sum(w))" in script
        else:
            assert "qnorm(1 - (1 - nivel) / 2)" in script and "q <- sum(" not in script
    # Los datos exportados son los yi y vi de nutria, con precisión completa
    with open(salida.with_suffix(".csv"), encoding="utf-8") as fh:
        datos = list(csv.DictReader(fh))
    yi = [float(r["yi"]) for r in datos]
    vi = [float(r["vi"]) for r in datos]
    assert yi == pytest.approx([1.0, 0.6, 1.2, -0.4], abs=1e-12)
    assert vi == pytest.approx([0.5, 0.72, 0.98, 0.72], abs=1e-12)
    # Las fórmulas del script, con el tau² del mismo estimador, dan lo mismo que `nutria meta analizar`
    code, res, _ = _json(capsys, "meta", "analizar", str(path), "--metodo", metodo, "--ajuste", ajuste)
    mod = res["modelo"]
    mu, se, ic, p, ip = _inferencia_como_el_script(yi, vi, mod["tau2"], ajuste, metodo)
    assert (mu, se, p) == pytest.approx((mod["estimate"], mod["se"], mod["p_value"]))
    assert ic == pytest.approx((mod["ci_low"], mod["ci_high"]))
    if ip is not None:
        assert ip == pytest.approx((mod["pi_low"], mod["pi_high"]))
    # Procedencia: huella del CSV de entrada y de lo generado
    ev = _eventos("meta_exportar_r")[-1]
    assert ev["inputs"]["sha256"] == _sha256(path)
    assert ev["inputs"]["metodo"] == metodo and ev["inputs"]["ajuste"] == ajuste
    assert ev["outputs"]["sha256_script"] == _sha256(salida)


def test_exportar_r_moderador_de_diseno_con_la_referencia_de_nutria(project, capsys):
    path = _csv(project / "mezcla.csv", MEZCLA, COLUMNAS)
    salida = project / "mezcla.R"
    assert _json(capsys, "meta", "exportar-r", str(path), "--salida", str(salida))[0] == 0
    script = salida.read_text("utf-8")
    assert 'relevel(dat$diseno_f, ref = "paralelo")' in script
    with open(salida.with_suffix(".csv"), encoding="utf-8") as fh:
        assert sorted({r["diseno"] for r in csv.DictReader(fh)}) == ["crossover", "cuadrado_latino", "paralelo"]
    # La salida debe ser el script, no un .csv (se sobrescribirían los datos)
    code, _, err = _json(capsys, "meta", "exportar-r", str(path), "--salida", str(project / "x.csv"))
    assert code == 2 and "script de R" in err


# =========================================================================== D6-4: St-Pierre
def _datos_dosis(rng, estudios=8, dosis=(0.0, 1.0, 2.0, 3.0), pendiente=1.0, de_pendiente=0.0, de_error=0.3,
                 de_nivel=2.0):
    rows = []
    for s in range(estudios):
        b = rng.normal(pendiente, de_pendiente) if de_pendiente else pendiente
        nivel = rng.normal(0, de_nivel)
        for d in dosis:
            rows.append({"estudio": f"S{s}", "dosis": d, "y": nivel + b * d + rng.normal(0, de_error)})
    return rows


def _st_pierre(*args, **kwargs):
    pytest.importorskip("statsmodels")
    from nutria.stats import mixed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return mixed.st_pierre(*args, **kwargs)


def test_st_pierre_elige_la_pendiente_aleatoria_con_la_razon_de_verosimilitudes():
    rows = _datos_dosis(np.random.default_rng(5), de_pendiente=0.8)
    res = _st_pierre(rows, "y", "dosis", "estudio")
    comp = res["comparacion_modelos"]
    lrt = comp["razon_verosimilitudes"]
    llf_ri, llf_rs = comp["intercepto_aleatorio"]["llf_reml"], comp["pendiente_aleatoria"]["llf_reml"]
    estadistico = 2 * (llf_rs - llf_ri)
    assert lrt["estadistico"] == pytest.approx(estadistico)
    # Corrección de frontera: mezcla 50:50 de chi² con 1 y 2 gl
    assert lrt["p"] == pytest.approx(0.5 * stats.chi2.sf(estadistico, 1) + 0.5 * stats.chi2.sf(estadistico, 2))
    assert lrt["p"] < 0.20 and res["modelo"] == "pendiente_aleatoria"
    assert "razón de verosimilitudes REML" in res["motivo_modelo"]
    assert res["pendiente_aleatoria"] == "auto" and comp["estimable"] and comp["estudios_con_3_o_mas_niveles"] == 8
    # t de Student con gl = estudios - efectos fijos (intercepto y dosis_intra: la dosis media es la misma)
    assert res["gl"] == 8 - 2
    pend = res["pendiente_intra"]
    assert pend["p"] == pytest.approx(2 * stats.t.sf(abs(pend["estimacion"] / pend["ee"]), 6))
    notas = " ".join(res["notas"])
    for frase in ("Modelo elegido: intercepto y pendiente aleatorios", "mezcla 50:50 de chi² con 1 y 2 gl",
                  "Self y Liang 1987", "t de Student con gl = estudios - efectos fijos",
                  "no es Satterthwaite ni Kenward-Roger", "Modelo sin ponderar"):
        assert frase in notas, frase


def test_st_pierre_sin_heterogeneidad_se_queda_con_el_intercepto_aleatorio():
    rows = _datos_dosis(np.random.default_rng(3), de_pendiente=0.0)
    res = _st_pierre(rows, "y", "dosis", "estudio")
    assert res["modelo"] == "intercepto_aleatorio"
    assert res["comparacion_modelos"]["razon_verosimilitudes"]["p"] >= 0.20
    assert "sin indicios de que la pendiente varíe" in res["motivo_modelo"]
    assert res["notas"][0].startswith("Modelo elegido: intercepto aleatorio (")


def test_st_pierre_pendiente_no_estimable_lo_declara():
    # Dos niveles de dosis por estudio: la varianza de la pendiente no es estimable
    rows = _datos_dosis(np.random.default_rng(2), dosis=(0.0, 2.0), de_pendiente=0.5)
    res = _st_pierre(rows, "y", "dosis", "estudio")
    assert res["modelo"] == "intercepto_aleatorio" and "no estimable" in res["motivo_modelo"]
    assert res["comparacion_modelos"]["pendiente_aleatoria"] is None
    assert any("liberal" in a for a in res["avisos"])
    with pytest.raises(ValueError, match="no es estimable"):
        _st_pierre(rows, "y", "dosis", "estudio", random_slope=True)
    # Forzar el intercepto aleatorio con pendientes heterogéneas avisa
    hetero = _datos_dosis(np.random.default_rng(5), de_pendiente=0.8)
    forzado = _st_pierre(hetero, "y", "dosis", "estudio", random_slope=False)
    assert forzado["modelo"] == "intercepto_aleatorio" and forzado["pendiente_aleatoria"] == "desactivada"
    assert any("Forzaste el intercepto aleatorio" in a for a in forzado["avisos"])
    with pytest.raises(ValueError, match="random_slope"):
        _st_pierre(hetero, "y", "dosis", "estudio", random_slope="si")


def test_st_pierre_sin_grados_de_libertad_no_da_p():
    # 3 estudios con dosis medias distintas: intercepto, dosis_intra y dosis_entre -> gl = 3 - 3 = 0
    rows = [{"estudio": f"S{s}", "dosis": s + d, "y": 1.0 * d + 0.3 * s + 0.01 * ((s + d) % 2)}
            for s in range(3) for d in (0.0, 1.0, 2.0)]
    res = _st_pierre(rows, "y", "dosis", "estudio")
    assert res["gl"] == 0 and res["pendiente_intra"]["p"] is None and res["pendiente_intra"]["ic95"] == [None, None]
    assert any("grados de libertad" in a for a in res["avisos"])


def test_razon_de_verosimilitudes_con_mezcla_de_chi2():
    from nutria.stats.mixed import razon_verosimilitudes
    assert razon_verosimilitudes(-10.0, -10.0)["p"] == pytest.approx(1.0)
    assert razon_verosimilitudes(-10.0, -10.5)["estadistico"] == 0.0  # truncado en 0
    r = razon_verosimilitudes(-10.0, -8.0)  # LRT = 4: 0,5·0,0455 + 0,5·0,1353 = 0,0904 (con chi² de 2 gl, 0,135)
    assert r["estadistico"] == pytest.approx(4.0)
    assert r["p"] == pytest.approx(0.5 * stats.chi2.sf(4, 1) + 0.5 * stats.chi2.sf(4, 2)) == pytest.approx(0.0904, abs=1e-4)


def test_st_pierre_error_de_tipo_I_con_pendientes_heterogeneas():
    """Simulación (semilla fija, 40 réplicas): pendiente media 0, 6 estudios × 4 dosis y DE de las pendientes
    0,4 (el escenario del escéptico de D6-4). La prueba antigua (intercepto aleatorio y Wald z) rechaza
    alrededor del 25-30 %; la opción por defecto se queda cerca del 5 % nominal."""
    rng = np.random.default_rng(1)
    rechazos = rechazos_antiguo = 0
    n = 40
    for _ in range(n):
        rows = _datos_dosis(rng, estudios=6, pendiente=0.0, de_pendiente=0.4)
        res = _st_pierre(rows, "y", "dosis", "estudio")
        p = res["pendiente_intra"]["p"]
        rechazos += p is not None and p < 0.05
        ri = res["comparacion_modelos"]["intercepto_aleatorio"]["pendiente"]
        rechazos_antiguo += 2 * stats.norm.sf(abs(ri["estimacion"] / ri["ee"])) < 0.05
    assert rechazos / n <= 0.10
    assert rechazos_antiguo / n >= 0.15


def test_st_pierre_ponderado_por_sem_es_el_gls_con_varianza_residual_sigma2_entre_w():
    rng = np.random.default_rng(8)
    rows = _datos_dosis(rng, estudios=6, dosis=(0.0, 1.0, 2.0), de_pendiente=0.0)
    sems = {f"S{s}": v for s, v in enumerate((0.2, 0.5, 0.3, 0.8, 0.25, 0.4))}
    rows = [dict(r, sem=sems[r["estudio"]]) for r in rows]
    sin = _st_pierre(rows, "y", "dosis", "estudio", random_slope=False)
    assert sin["ponderacion"] is None and any("Modelo sin ponderar" in n for n in sin["notas"])
    res = _st_pierre(rows, "y", "dosis", "estudio", random_slope=False, sem="sem")
    assert res["ponderacion"] == {"columna_sem": "sem", "peso": "1/SEM², normalizado a media 1"}
    assert any("ponderado por 1/SEM²" in n for n in res["notas"])
    # Con las varianzas estimadas, los efectos fijos son el GLS con V = tau²·11' + σ²·W⁻¹ (pesos de media 1)
    w = np.array([1 / sems[r["estudio"]] ** 2 for r in rows])
    w = w / w.mean()
    tau2, sigma2 = res["varianza_estudio"][0][0], res["varianza_residual"]
    estudios = [r["estudio"] for r in rows]
    V = np.array([[tau2 * (a == b) for b in estudios] for a in estudios]) + np.diag(sigma2 / w)
    dosis = np.array([r["dosis"] for r in rows])
    X = np.column_stack([np.ones(len(rows)), dosis - dosis.mean()])  # dosis_intra (misma dosis media por estudio)
    y = np.array([r["y"] for r in rows])
    vinv = np.linalg.inv(V)
    beta = np.linalg.solve(X.T @ vinv @ X, X.T @ vinv @ y)
    assert res["pendiente_intra"]["estimacion"] == pytest.approx(beta[1], rel=1e-6)
    assert res["pendiente_intra"]["ee"] == pytest.approx(math.sqrt(np.linalg.inv(X.T @ vinv @ X)[1, 1]), rel=1e-4)
    # Con todos los SEM iguales, ponderar no cambia nada
    iguales = [dict(r, sem=0.4) for r in rows]
    a = _st_pierre(iguales, "y", "dosis", "estudio", random_slope=False, sem="sem")
    assert a["pendiente_intra"]["estimacion"] == pytest.approx(sin["pendiente_intra"]["estimacion"])
    assert a["pendiente_intra"]["ee"] == pytest.approx(sin["pendiente_intra"]["ee"], rel=1e-5)
    # Un SEM ausente o no positivo es un error: no se descartan filas en silencio
    for malo in ("", "0", "-1", "n.d."):
        sucias = [dict(r, sem=malo) if r["estudio"] == "S2" else r for r in rows]
        with pytest.raises(ValueError, match="S2"):
            _st_pierre(sucias, "y", "dosis", "estudio", sem="sem")
    with pytest.raises(ValueError, match="Faltan columnas"):
        _st_pierre(rows, "y", "dosis", "estudio", sem="sem_publicado")


def test_cli_stpierre_por_defecto_y_procedencia(project, capsys):
    pytest.importorskip("statsmodels")
    path = _csv(project / "dosis.csv", _datos_dosis(np.random.default_rng(5), de_pendiente=0.8))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        code, out, _ = _json(capsys, "meta", "stpierre", str(path), "--respuesta", "y", "--dosis", "dosis",
                             "--estudio", "estudio")
        assert code == 0 and out["modelo"] == "pendiente_aleatoria" and out["pendiente_aleatoria"] == "auto"
        assert list(out)[:3] == ["avisos", "modelo", "motivo_modelo"]
        ev = _eventos("meta_stpierre")[-1]
        assert ev["inputs"]["sha256"] == _sha256(path)
        assert ev["inputs"]["pendiente_aleatoria"] == "auto" and ev["inputs"]["alfa_lrt"] == 0.20
        assert ev["outputs"]["pendiente_intra"]["estimacion"] == pytest.approx(out["pendiente_intra"]["estimacion"])
        code, out, _ = _json(capsys, "meta", "stpierre", str(path), "--respuesta", "y", "--dosis", "dosis",
                             "--estudio", "estudio", "--sin-pendiente-aleatoria")
        assert code == 0 and out["modelo"] == "intercepto_aleatorio" and out["avisos"]
        assert _eventos("meta_stpierre")[-1]["inputs"]["pendiente_aleatoria"] == "no"
        assert _eventos("meta_stpierre")[-1]["inputs"]["ponderar_sem"] is None
        # Ponderación por 1/SEM² desde la CLI; una columna inexistente es un error (código 2)
        filas = [dict(r, sem=0.3 + 0.1 * int(r["estudio"][1:])) for r in
                 _datos_dosis(np.random.default_rng(5), de_pendiente=0.8)]
        con_sem = _csv(project / "dosis_sem.csv", filas)
        code, out, _ = _json(capsys, "meta", "stpierre", str(con_sem), "--respuesta", "y", "--dosis", "dosis",
                             "--estudio", "estudio", "--ponderar-sem", "sem")
        assert code == 0 and out["ponderacion"]["columna_sem"] == "sem"
        assert _eventos("meta_stpierre")[-1]["inputs"]["ponderar_sem"] == "sem"
        assert _json(capsys, "meta", "stpierre", str(path), "--respuesta", "y", "--dosis", "dosis", "--estudio",
                     "estudio", "--ponderar-sem", "sem")[0] == 2
        # Diseños mezclados en el CSV de St-Pierre: aviso al principio
        mezclado = [dict(r, diseno="cuadrado_latino" if r["estudio"] in ("S0", "S1") else "paralelo")
                    for r in _datos_dosis(np.random.default_rng(5), de_pendiente=0.8)]
        code, out, _ = _json(capsys, "meta", "stpierre", str(_csv(project / "mezcla_dosis.csv", mezclado)),
                             "--respuesta", "y", "--dosis", "dosis", "--estudio", "estudio")
        assert code == 0 and out["avisos"][0].startswith("El CSV combina diseños distintos (cuadrado_latino, paralelo)")
    with pytest.raises(SystemExit):
        main(["meta", "stpierre", str(path), "--respuesta", "y", "--dosis", "dosis", "--estudio", "estudio",
              "--pendiente-aleatoria", "--sin-pendiente-aleatoria"])


# =========================================================================== D6-11: potencia
def _potencia_t(n_unidades, delta, de, gl):
    """Potencia bilateral exacta (t no central) para una diferencia con varianza 2·de²/n."""
    ncp = abs(delta) / (de * math.sqrt(2 / n_unidades))
    crit = stats.t.ppf(0.975, gl)
    return stats.nct.sf(crit, gl, ncp) + stats.nct.cdf(-crit, gl, ncp)


def test_potencia_con_corrales_cuenta_corrales_y_animales():
    # Reproducción de D6-11: delta 1,5, DE entre animales 2, 10 animales por corral, ICC 0,3
    res = power.n_means("paralelo", 1.5, sd=2.0, animals_per_pen=10, icc=0.3)
    deff = 1 + 9 * 0.3
    de_corral = 2.0 * math.sqrt(deff / 10)  # DE de la media de corral = 1,2166
    assert res["efecto_diseno"] == pytest.approx(3.7) and res["de_unidad"] == pytest.approx(de_corral)
    assert res["unidad_experimental"] == "corral"
    assert res["n_unidades_por_grupo"] == res["n_por_grupo"] == 12
    assert res["n_animales_por_grupo"] == 120 and res["n_animales_total"] == 240 and res["gl"] == 22
    assert res["n_animales_por_grupo_ignorando_corral"] == 29
    # Comprobación independiente: 12 corrales alcanzan el 80 % y 11 no
    assert _potencia_t(12, 1.5, de_corral, 22) >= 0.80 > _potencia_t(11, 1.5, de_corral, 20)
    # ICC = 0: el corral sigue siendo la unidad, pero con la DE de la media de m animales
    sin_icc = power.n_means("paralelo", 1.5, sd=2.0, animals_per_pen=10, icc=0.0)
    assert sin_icc["de_unidad"] == pytest.approx(2.0 / math.sqrt(10))


def test_potencia_crossover_tamano_muestral_a_mano():
    # AB/BA: Var(diferencia) = 2·σ_w²/n y gl = n - 2 (Senn 2002); delta 1 y σ_w 1,5 -> 38 animales
    res = power.n_crossover(1.0, 1.5)
    assert res["n_total"] == res["n_animales_total"] == 38 and res["gl"] == 36 and res["n_observaciones"] == 76
    assert _potencia_t(38, 1.0, 1.5, 36) >= 0.80 > _potencia_t(36, 1.0, 1.5, 34)
    # El paralelo con la misma DE necesita 37 por grupo (74 animales): el crossover, 38 en total
    assert power.n_parallel(1.0, 1.5)["n_por_grupo"] == 37
    # σ_w a partir de la DE entre animales y la correlación intrasujeto: 2·√(1 - 0,4375) = 1,5
    via_rho = power.n_means("crossover", 1.0, sd=2.0, rho=0.4375)
    assert via_rho["n_animales_total"] == 38 and via_rho["de_intra"] == pytest.approx(1.5)
    # Cuadrado latino 4×4: n múltiplo de 4 y gl = (n - 2)·(t - 1)
    cl = power.n_means("cuadrado_latino", 1.0, sd_within=1.5, treatments=4)
    n = cl["n_animales_total"]
    assert n % 4 == 0 and cl["gl"] == (n - 2) * 3 and cl["n_observaciones"] == 4 * n
    assert _potencia_t(n, 1.0, 1.5, (n - 2) * 3) >= 0.80 > _potencia_t(n - 4, 1.0, 1.5, (n - 6) * 3)


def test_potencia_validaciones_fallan_en_cerrado():
    with pytest.raises(ValueError, match="ambigua"):
        power.n_means("crossover", 1.0, sd=2.0)
    with pytest.raises(ValueError, match="a la vez --m"):
        power.n_means("paralelo", 1.0, sd=2.0, animals_per_pen=10)
    with pytest.raises(ValueError, match="solo están implementados en el diseño paralelo"):
        power.n_means("crossover", 1.0, sd_within=1.0, animals_per_pen=10, icc=0.1)
    with pytest.raises(ValueError, match="--tratamientos"):
        power.n_means("cuadrado_latino", 1.0, sd_within=1.0)
    with pytest.raises(ValueError, match="no ambas"):
        power.n_means("crossover", 1.0, sd=2.0, sd_within=1.0, rho=0.5)
    with pytest.raises(ValueError, match="diseños cruzados"):
        power.n_means("paralelo", 1.0, sd=2.0, rho=0.5)
    with pytest.raises(ValueError):
        power.n_means("paralelo", 1.0, sd=2.0, animals_per_pen=10, icc=1.5)


def test_potencia_proporciones_por_corrales():
    base = power.n_proportions(0.3, 0.2)
    assert base["n_por_grupo"] == 294 and base["unidad_experimental"] == "animal"
    res = power.n_proportions(0.3, 0.2, animals_per_pen=10, icc=0.05)
    # 294 animales × (1 + 9·0,05) / 10 = 42,6 -> 43 corrales (430 animales) por grupo
    assert res["n_unidades_por_grupo"] == 43 and res["n_animales_por_grupo"] == 430
    assert res["efecto_diseno"] == pytest.approx(1.45)


def test_cli_potencia_distingue_unidades_y_animales(project, capsys):
    code, text, _ = _texto(capsys, "potencia", "medias", "--delta", "1.5", "--de", "2", "--m", "10", "--icc", "0.3")
    assert code == 0
    assert "Unidades experimentales: 12 corrales por grupo (24 en total; gl = 22)." in text
    assert "Animales: 120 por grupo (240 en total)." in text and "29 animales por grupo" in text
    code, out, _ = _json(capsys, "potencia", "medias", "--delta", "1", "--diseno", "crossover", "--de-intra", "1.5")
    assert code == 0 and out["n_animales_total"] == 38 and out["unidad_experimental"].startswith("animal")
    code, _, err = _json(capsys, "potencia", "medias", "--delta", "1", "--diseno", "crossover", "--de", "2")
    assert code == 2 and "ambigua" in err


# =========================================================================== D9-7 y D6-12: ruta CLI y procedencia
def test_cli_meta_analizar_ruta_completa_y_procedencia(project, capsys):
    filas = LECHE + [_fila("E4", 30.0, 30.4, 0.6, 10), _fila("E5", 31.5, 30.0, 0.5, 12)]
    path = _csv(project / "cinco.csv", filas, COLUMNAS)
    code, out, _ = _json(capsys, "meta", "analizar", str(path), "--metodo", "DL", "--ajuste", "hksj")
    assert code == 0
    yi = [1.0, 0.6, 1.2, -0.4, 1.5]
    vi = [0.5, 0.72, 0.98, 0.72, 0.5]
    ref = meta.random_effects(yi, vi, method="DL", ajuste="hksj")
    mod = out["modelo"]
    assert (mod["estimate"], mod["ci_low"], mod["ci_high"], mod["p_value"], mod["tau2"]) == pytest.approx(
        (ref.estimate, ref.ci_low, ref.ci_high, ref.p_value, ref.tau2))
    assert out["k"] == 5 and out["estudios"] == ["E1", "E2", "E3", "E4", "E5"]
    assert set(out) >= {"ic_heterogeneidad", "sesgo_publicacion", "uno_fuera", "avisos_diseno", "notas_diseno"}
    ev = _eventos("metaanalisis")[-1]
    entradas = ev["inputs"]
    assert entradas["sha256"] == _sha256(path)
    assert {k: entradas[k] for k in ("medida", "metodo", "ajuste", "nivel", "agregar_estudio", "rho",
                                     "rho_intrasujeto", "moderadores", "subgrupo", "sep", "decimal")} == {
        "medida": "MD", "metodo": "DL", "ajuste": "hksj", "nivel": 0.95, "agregar_estudio": False, "rho": 0.5,
        "rho_intrasujeto": None, "moderadores": None, "subgrupo": None, "sep": ",", "decimal": "."}
    salidas = ev["outputs"]
    assert salidas["estimacion"] == pytest.approx(ref.estimate) and salidas["p"] == pytest.approx(ref.p_value)
    assert salidas["ic"] == pytest.approx([ref.ci_low, ref.ci_high])
    assert salidas["ip"] == pytest.approx([ref.pi_low, ref.pi_high]) and salidas["ip_gl"] == 3
    assert salidas["tau2"] == pytest.approx(ref.tau2) and salidas["k"] == 5
    # Cambiar un byte del CSV cambia la huella registrada
    path.write_text(path.read_text("utf-8").replace("31.5", "31.6"), "utf-8")
    assert _json(capsys, "meta", "analizar", str(path))[0] == 0
    assert _eventos("metaanalisis")[-1]["inputs"]["sha256"] == _sha256(path) != entradas["sha256"]


def test_cli_validar_predictivo_registra_huella(project, capsys):
    filas = [{"estudio": s, "x": str(off + j), "y": str(1.0 + 2.0 * (off + j))}
             for s, off in zip("ABCDE", (0.0, 1.0, 2.5, 4.0, 5.5)) for j in range(3)]
    path = _csv(project / "pred.csv", filas)
    code, out, _ = _json(capsys, "meta", "validar-predictivo", str(path), "--respuesta", "y", "--predictores", "x",
                         "--estudio", "estudio")
    assert code == 0
    ev = _eventos("meta_validar_predictivo")[-1]
    assert ev["inputs"]["sha256"] == _sha256(path) and ev["inputs"]["predictores"] == ["x"]
    assert ev["inputs"]["estudio"] == "estudio" and ev["outputs"]["rmse"] == pytest.approx(out["rmse"])
