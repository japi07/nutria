"""Genera docs/ESTADO_Y_LIMITACIONES.md y docs/auditoria/*.json a partir de la reauditoría (sin redacción a mano).

Uso: python scripts/auditoria/gen_estado.py RESULTADOS.json HALLAZGOS.json RAIZ_REPO COMMIT [FECHA]

RESULTADOS.json es la salida del workflow scripts/auditoria/reauditoria.js ({"resultados": [...]}, o la salida completa
de la tarea con la clave "result"). HALLAZGOS.json es docs/auditoria/hallazgos_linea_base.json.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

res_path, hall_path, root, commit = sys.argv[1:5]
fecha = sys.argv[5] if len(sys.argv) > 5 else "2026-11-04"
root = Path(root)
data = json.loads(Path(res_path).read_text("utf-8"))
if isinstance(data, dict) and "resultados" not in data and "result" in data:
    data = data["result"]
resultados = data["resultados"]
hallazgos = json.loads(Path(hall_path).read_text("utf-8"))
by_id = {h["id"]: h for h in hallazgos}

check, refute, fallos = {}, {}, []
for blk in resultados:
    if blk.get("fallo"):
        fallos.append(f"{blk['bloque']}: {blk['fallo']} ({len(blk.get('hallazgos', []))} hallazgos)")
        continue
    for it in blk.get("comprobacion") or []:
        check[it["id"]] = it
    if blk.get("refutacion") is None and any(i["estado"] in ("corregido", "mitigado") for i in blk.get("comprobacion") or []):
        fallos.append(f"{blk['bloque']}: el escéptico no devolvió resultado")
    for it in blk.get("refutacion") or []:
        refute[it["id"]] = it
    if blk.get("omitidos"):
        fallos.append(f"{blk['bloque']}: la comprobación omitió {', '.join(blk['omitidos'])}")

FINAL_ORDER = ["pendiente", "corregido_sin_confirmar", "mitigado_sin_confirmar", "sin_reauditar", "mitigado",
               "corregido", "no_aplica"]
LABEL = {
    "pendiente": "Pendiente (el defecto sigue, total o parcialmente)",
    "corregido_sin_confirmar": "Corregido según el verificador, sin confirmar por el escéptico",
    "mitigado_sin_confirmar": "Mitigado según el verificador, sin confirmar por el escéptico",
    "sin_reauditar": "Sin reauditar (fallo o ausencia de respuesta del agente)",
    "mitigado": "Mitigado y declarado (defecto de diseño que el código no puede eliminar del todo)",
    "corregido": "Corregido (confirmado por el escéptico)",
    "no_aplica": "No aplica",
}

final = {}
for h in hallazgos:
    c = check.get(h["id"])
    r = refute.get(h["id"])
    if c is None:
        estado, motivo = "sin_reauditar", "Sin respuesta del verificador"
    elif c["estado"] in ("pendiente", "no_aplica"):
        estado, motivo = c["estado"], c["explicacion"]
    elif r is None:
        estado, motivo = f"{c['estado']}_sin_confirmar", c["explicacion"]
    elif r["veredicto"] == "confirmado":
        estado, motivo = c["estado"], c["explicacion"]
    elif r["veredicto"] == "refutado":
        estado, motivo = "pendiente", f"Refutado por el escéptico: {r['motivo']}"
    else:
        estado, motivo = f"{c['estado']}_sin_confirmar", f"Dudoso para el escéptico: {r['motivo']}"
    final[h["id"]] = {"id": h["id"], "dimension": h["dimension"], "gravedad": h["gravedad"], "estado_final": estado,
                      "verificador": c, "esceptico": r, "motivo": motivo}


def resumen(texto: str, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", texto).strip()
    m = re.match(r"(.{40,}?[.;:])\s", t)
    t = m.group(1) if m and len(m.group(1)) <= n else t
    return (t[: n - 1] + "…") if len(t) > n else t


def celda(texto: str, n: int = 400) -> str:
    return resumen(texto, n).replace("|", "\\|")


SEV = ["alta", "media", "baja"]
DIM = {"D1": "Corrección del prompt", "D2": "Arquitectura y orquestación", "D3": "Antialucinación y trazabilidad",
       "D4": "Rigor en nutrición y fisiología", "D5": "Regulación UE y seguridad", "D6": "Estadística",
       "D7": "Fuentes de datos", "D8": "Priorización, compuertas y economía", "D9": "Ingeniería y pruebas",
       "D10": "Operación y documentación", "D11": "Resultados"}
cnt = Counter((f["estado_final"], f["gravedad"]) for f in final.values())
tot = Counter(f["estado_final"] for f in final.values())

out = []
w = out.append
w("# Estado y limitaciones de nutria")
w("")
w(f"Generado a partir de la reauditoría del {fecha} sobre la rama `correcciones-auditoria` (commit `{commit}`). "
  "Este documento lo produce un script a partir de los resultados guardados en `docs/auditoria/`: no se edita a mano.")
w("")
w("> **Cómo leerlo.** La auditoría de línea base (commit `236a161`) confirmó 151 defectos en 11 dimensiones "
  "(`docs/auditoria/AUDITORIA_LINEA_BASE.md`, `docs/auditoria/hallazgos_linea_base.json`). Tras las correcciones, una "
  "reauditoría revisó cada defecto contra el código actual: en cada bloque, un agente verificador lo clasificó "
  "(corregido, mitigado, pendiente o no aplica) leyendo el código y ejecutando pruebas, y un agente escéptico intentó "
  "refutar cada «corregido» o «mitigado». Solo cuenta como corregido lo que el escéptico confirmó. Los verificadores son "
  "modelos de lenguaje: pueden equivocarse y otra ejecución puede dar resultados distintos. Ninguna persona ha revisado "
  "aún esta clasificación.")
w("")
w("## Resumen")
w("")
w("| Estado final | Alta | Media | Baja | Total |")
w("|---|---|---|---|---|")
for e in FINAL_ORDER:
    if tot.get(e):
        w(f"| {LABEL[e]} | " + " | ".join(str(cnt.get((e, s), 0)) for s in SEV) + f" | {tot[e]} |")
w(f"| **Total** | " + " | ".join(str(sum(cnt.get((e, s), 0) for e in FINAL_ORDER)) for s in SEV) + f" | {len(final)} |")
w("")
if fallos:
    w("**Incidencias de la reauditoría** (se informan, no se ocultan): " + "; ".join(fallos) + ".")
    w("")


def seccion(titulo, estados, intro=None, detalle=True):
    items = [f for f in final.values() if f["estado_final"] in estados]
    if not items:
        return
    items.sort(key=lambda f: (SEV.index(f["gravedad"]), int(f["dimension"][1:]), int(f["id"].split("-")[1])))
    w(f"## {titulo} ({len(items)})")
    w("")
    if intro:
        w(intro)
        w("")
    if detalle:
        w("| ID | Gravedad | Defecto original | Situación actual |")
        w("|---|---|---|---|")
        for f in items:
            w(f"| {f['id']} | {f['gravedad']} | {celda(by_id[f['id']]['texto'], 260)} | {celda(f['motivo'], 420)} |")
    else:
        w("| ID | Gravedad | Defecto original |")
        w("|---|---|---|")
        for f in items:
            w(f"| {f['id']} | {f['gravedad']} | {celda(by_id[f['id']]['texto'], 200)} |")
    w("")


seccion("Pendientes", {"pendiente"},
        "Defectos que siguen presentes total o parcialmente, incluidos los «corregidos» que el escéptico refutó. "
        "Son el trabajo prioritario antes de usar el sistema para decisiones.")
seccion("Sin confirmar", {"corregido_sin_confirmar", "mitigado_sin_confirmar", "sin_reauditar"},
        "El verificador los da por corregidos o mitigados, pero el escéptico no pudo confirmarlo (o no hubo respuesta). "
        "Trátalos como pendientes hasta que una persona los compruebe.")
seccion("Mitigados y declarados", {"mitigado"},
        "Defectos de diseño que el código no puede eliminar del todo (dependen de personas, de servicios externos o "
        "de la disciplina de los agentes). Están reducidos y declarados.")
seccion("Corregidos y confirmados", {"corregido"}, detalle=False)
seccion("No aplican", {"no_aplica"})

w("## Limitaciones que no dependen de un defecto concreto")
w("")
for line in (
    "- **Agentes, skills y workflows sin ejecución real de principio a fin.** Se han revisado leyendo su código y con "
    "pruebas que simulan las respuestas de los agentes (`tests/test_orquestacion.py`); su comportamiento con modelos "
    "reales, su coste en tokens y su tiempo no se han medido.",
    "- **Clientes de API sin probar contra los servicios reales.** La red de este entorno bloquea las fuentes "
    "científicas; las pruebas en vivo existen (`tests/test_vivo.py`) y se activan con `NUTRIA_PRUEBAS_EN_VIVO=1`.",
    "- **Normativa pendiente de verificación humana.** Lo marcado «pendiente» en `docs/REGULACION_UE.md` debe "
    "comprobarlo una persona en EUR-Lex (texto consolidado, CELEX y fecha) y registrarlo con "
    "`nutria verificar manual <URL> --firma-humana NOMBRE`.",
    "- **La ceguera de los evaluadores es una barrera, no un control de seguridad.** La CLI oculta puntuaciones, ranking "
    "y procedencia a `--agente evaluador-*`, pero un agente con Bash podría leer la base de datos directamente.",
    "- **Los hooks frenan errores y atajos de los agentes, no a un atacante.** Comparan texto de órdenes y rutas.",
    "- **Sin modelos de racionamiento ni simulación metabólica.** La única herramienta de modelización es el destino "
    "ruminal de Ørskov y McDonald; los requerimientos se calculan con software oficial o validado (p. ej. NASEM 2021).",
    "- **La versión exacta del modelo de cada subagente no se registra automáticamente**: anótala en el Anexo C de cada "
    "informe.",
    "- **Fase exclusivamente computacional.** Nada de lo que produce el sistema es un resultado experimental; las "
    "hipótesis de IA no son hallazgos.",
):
    w(line)
w("")
w("## Cómo se regenera")
w("")
w("Los datos de la reauditoría están en `docs/auditoria/reauditoria.json` (veredicto del verificador y del escéptico "
  "para cada defecto). Para repetirla, lanza desde Claude Code el workflow `scripts/auditoria/reauditoria.js` (herramienta "
  "Workflow con `scriptPath`; argumentos: `archivo` = `docs/auditoria/hallazgos_linea_base.json`, `raiz` = la raíz del "
  "repositorio e `ids` = los identificadores por dimensión) y regenera este documento con "
  "`python scripts/auditoria/gen_estado.py <resultados.json> docs/auditoria/hallazgos_linea_base.json . <commit> [fecha]`.")
w("")

(root / "docs" / "auditoria").mkdir(parents=True, exist_ok=True)
(root / "docs" / "ESTADO_Y_LIMITACIONES.md").write_text("\n".join(out), "utf-8")
(root / "docs" / "auditoria" / "reauditoria.json").write_text(
    json.dumps({"commit": commit, "fecha": fecha, "incidencias": fallos,
                "hallazgos": [final[h["id"]] for h in hallazgos]}, ensure_ascii=False, indent=1) + "\n", "utf-8")
print(json.dumps({"total": len(final), "por_estado": tot, "incidencias": fallos}, ensure_ascii=False, default=dict))
