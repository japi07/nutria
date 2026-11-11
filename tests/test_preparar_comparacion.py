"""Carpeta de trabajo neutral para comparar nutria con otro sistema (scripts/preparar_comparacion.py) y coherencia
del prompt de docs/PROMPT_COMPARACION.md con el workflow comparar-sistemas."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "preparar_comparacion.py"
DOC = ROOT / "docs" / "PROMPT_COMPARACION.md"
FLUJO = ROOT / ".claude" / "workflows" / "comparar-sistemas.js"

spec = importlib.util.spec_from_file_location("preparar_comparacion", SCRIPT)
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


@pytest.fixture
def otro(tmp_path):
    carpeta = tmp_path / "proyectos" / "motor-ganadero"
    carpeta.mkdir(parents=True)
    (carpeta / "README.md").write_text("otro sistema", "utf-8")
    return carpeta


def _ejecutar(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, check=False)


def test_prepara_la_carpeta_de_trabajo_sin_tocar_los_sistemas(tmp_path, otro):
    trabajo = tmp_path / "comparacion-sistemas"
    antes = sorted(p.name for p in otro.iterdir())
    proc = _ejecutar("--otro", otro, "--trabajo", trabajo)
    assert proc.returncode == 0, proc.stderr
    assert "Lee PROMPT.md y ejecútalo" in proc.stdout
    assert sorted(p.name for p in otro.iterdir()) == antes                      # el otro sistema no se toca
    for nombre in ("CLAUDE.md", "PROMPT.md", ".claude/settings.json", "comparacion/args_dimensiones.json"):
        assert (trabajo / nombre).is_file(), nombre
    for carpeta in ("copias", "privado", "pruebas", "tmp"):
        assert (trabajo / "comparacion" / carpeta).is_dir()

    reglas = (trabajo / "CLAUDE.md").read_text("utf-8")
    prompt = (trabajo / "PROMPT.md").read_text("utf-8")
    for texto in (reglas, prompt):
        assert not re.search(r"RUTA_[A-Z]+", texto)                             # ningún marcador sin sustituir
    assert ROOT.as_posix() in reglas and otro.resolve().as_posix() in reglas
    assert all(f"R{i}." in reglas for i in range(1, 9)) and all(f"E{i}." in reglas for i in range(1, 7))
    assert all(f"Fase {i}." in prompt for i in range(8))
    assert "Te pido expresamente que uses la herramienta Workflow" in prompt     # el permiso lo da la persona

    ajustes = json.loads((trabajo / ".claude" / "settings.json").read_text("utf-8"))
    permisos = ajustes["permissions"]
    assert "Workflow" in permisos["allow"] and "WebFetch(domain:eur-lex.europa.eu)" in permisos["allow"]
    assert f"Edit(/{ROOT.as_posix()}/**)" in permisos["deny"]                   # originales en solo lectura
    assert f"Edit(/{otro.resolve().as_posix()}/**)" in permisos["deny"]
    assert "Read(~/.config/nutria/**)" in permisos["deny"] and "Read(**/.env)" in permisos["deny"]
    assert permisos["additionalDirectories"] == [ROOT.as_posix(), otro.resolve().as_posix()]
    assert "bypass" not in json.dumps(ajustes).lower()
    entorno = ajustes["env"]
    assert entorno["NUTRIA_HOME"] == ""                                          # nunca el proyecto real de la persona
    ruta_clave = next(v for k, v in entorno.items() if k.endswith("_SELLO"))    # clave de sellos solo de pruebas
    assert ruta_clave.startswith((trabajo / "comparacion" / "tmp").resolve().as_posix())

    args = json.loads((trabajo / "comparacion" / "args_dimensiones.json").read_text("utf-8"))
    copias = (trabajo / "comparacion" / "copias").resolve().as_posix()
    assert [s["ruta"] for s in args["sistemas"]] == [f"{copias}/nutria", f"{copias}/motor-ganadero"]
    assert args["evaluadores"] == 2 and args["verificar_desde"] == "alta" and len(args["dimensiones_extra"]) == 6
    assert len(args["notas"]) <= 3400                                            # deja sitio a lo que añada quien audita


def test_rechaza_carpetas_de_trabajo_que_dan_ventaja_a_un_sistema(tmp_path, otro):
    for trabajo in (otro, otro / "dentro", ROOT / "comparacion-x"):
        proc = _ejecutar("--otro", otro, "--trabajo", trabajo)
        assert proc.returncode == 2 and "jugaría en casa" in proc.stderr, trabajo
        assert not (Path(trabajo) / "PROMPT.md").exists()
    assert not (ROOT / "comparacion-x").exists()                                 # no deja nada en nutria
    assert _ejecutar("--otro", tmp_path / "no-existe", "--trabajo", tmp_path / "t").returncode == 2
    assert _ejecutar("--otro", ROOT, "--trabajo", tmp_path / "t").returncode == 2
    assert _ejecutar("--otro", otro, "--trabajo", tmp_path / "t", "--nombre-otro", "nutria").returncode == 2
    assert _ejecutar("--otro", otro, "--trabajo", tmp_path / "t", "--nombre-otro", "con espacios").returncode == 2
    assert not (tmp_path / "t").exists()


def test_no_sobrescribe_sin_forzar_y_admite_otro_nombre(tmp_path, otro):
    trabajo = tmp_path / "trabajo"
    assert _ejecutar("--otro", otro, "--trabajo", trabajo).returncode == 0
    (trabajo / "CLAUDE.md").write_text("cambiado por la persona", "utf-8")
    repetido = _ejecutar("--otro", otro, "--trabajo", trabajo)
    assert repetido.returncode == 2 and "--forzar" in repetido.stderr
    assert (trabajo / "CLAUDE.md").read_text("utf-8") == "cambiado por la persona"
    assert _ejecutar("--otro", otro, "--trabajo", trabajo, "--forzar", "--nombre-otro", "sistema-b").returncode == 0
    reglas = (trabajo / "CLAUDE.md").read_text("utf-8")
    assert "sistema-b" in reglas and "motor-ganadero" not in reglas.replace(otro.resolve().as_posix(), "")
    args = json.loads((trabajo / "comparacion" / "args_dimensiones.json").read_text("utf-8"))
    assert args["sistemas"][1]["nombre"] == "sistema-b" and args["sistemas"][1]["ruta"].endswith("/copias/sistema-b")


def test_avisa_si_un_sistema_esta_dentro_del_otro(tmp_path):
    """Caso real: el otro sistema es la carpeta que contiene a nutria (p. ej. mithril/ con mithril/nutria)."""
    madre = tmp_path / "mithril"
    falsa_nutria = madre / "nutria"
    (falsa_nutria / "docs").mkdir(parents=True)
    (falsa_nutria / ".claude" / "workflows").mkdir(parents=True)
    (falsa_nutria / "docs" / "PROMPT_COMPARACION.md").write_text(DOC.read_text("utf-8"), "utf-8")
    (falsa_nutria / ".claude" / "workflows" / "comparar-sistemas.js").write_text(FLUJO.read_text("utf-8"), "utf-8")
    trabajo = tmp_path / "trabajo"
    r = pc.preparar(falsa_nutria, madre, trabajo)
    assert any("dentro de la carpeta del otro" in a for a in r["avisos"])
    reglas = (trabajo / "CLAUDE.md").read_text("utf-8")
    assert "nutria está dentro de la carpeta de motor-ganadero" in reglas
    assert reglas.index("Aviso: nutria está dentro") < reglas.index("\nREGLAS\n")
    # una nutria sin el workflow actualizado se rechaza con un mensaje que dice qué hacer
    (falsa_nutria / ".claude" / "workflows" / "comparar-sistemas.js").write_text("export const meta = {}", "utf-8")
    with pytest.raises(pc.Fallo, match="evaluadores"):
        pc.preparar(falsa_nutria, madre, tmp_path / "otro-trabajo")
    assert not (tmp_path / "otro-trabajo").exists()


def test_regla_de_ruta_absoluta_en_posix_y_en_windows():
    assert pc.regla_ruta(PurePosixPath("/home/ana/mithril/nutria")) == "//home/ana/mithril/nutria"
    assert pc.regla_ruta(PureWindowsPath("C:/Users/Ana/mithril/nutria")) == "//c/Users/Ana/mithril/nutria"


def test_los_argumentos_del_prompt_los_acepta_el_workflow(tmp_path, otro):
    """Los argumentos que escribe el script son válidos para comparar-sistemas.js (17 dimensiones, 2 evaluadores)."""
    orq = pytest.importorskip("test_orquestacion")
    if orq.NODE is None:
        pytest.skip("hace falta node para ejecutar el arnés de workflows")
    trabajo = tmp_path / "trabajo"
    pc.preparar(ROOT, otro, trabajo)
    args = json.loads((trabajo / "comparacion" / "args_dimensiones.json").read_text("utf-8"))
    out = orq.run_workflow("comparar-sistemas", args)
    res = out["resultado"]
    assert "error" not in res, res
    assert len(res["dimensiones"]) == 17 and res["evaluadores"] == 2 and res["verificar_desde"] == "alta"
    anadidas = [d["titulo"] for d in res["dimensiones"] if d["anadida_por_quien_lanza"]]
    assert len(anadidas) == 6 and anadidas[0].startswith("Fidelidad al encargo")
    assert sum(1 for e in orq.labels(out) if e.startswith("eval:")) == 34
    # las notas llegan a los agentes del workflow, y el encargo neutro es la referencia
    evaluador = orq.call(out, "eval:D12")["prompt"]
    assert "copias desechables" in evaluador and "comparacion/ENCARGO.md" in evaluador


def test_el_documento_tiene_los_tres_bloques_y_remite_al_script():
    texto = DOC.read_text("utf-8")
    for encabezado in pc.SECCIONES.values():
        assert encabezado in texto
        assert pc.bloque(texto, encabezado).strip()
    assert "scripts/preparar_comparacion.py" in texto
    json.loads(pc.bloque(texto, pc.SECCIONES["args"]))
    prompt = pc.bloque(texto, pc.SECCIONES["prompt"])
    assert "```" not in prompt and len(prompt.splitlines()) < 260
    # el prompt no da por bueno lo que nutria dice de sí misma ni usa sus bancos como respuesta correcta
    assert "No uses como respuesta ningún" in prompt
    assert "no haberla ejecutado es un incumplimiento" in prompt
    # los argumentos opcionales que nombra el prompt existen en el workflow
    flujo = FLUJO.read_text("utf-8")
    for argumento in ("dimensiones_extra", "notas", "verificar_desde", "evaluadores"):
        assert f"A.{argumento}" in flujo and argumento in prompt
