"""Genera docs/ESTADO_Y_LIMITACIONES.md y docs/auditoria/reauditoria.json a partir de las rondas de reauditoría.

El documento no se redacta a mano: todo sale de los resultados guardados en docs/auditoria/.

Uso:
    python scripts/auditoria/gen_estado.py \
        --hallazgos docs/auditoria/hallazgos_linea_base.json \
        --ronda docs/auditoria/reauditoria_ronda1.json 17cce15 2026-11-04 \
        [--ronda docs/auditoria/reauditoria_ronda2.json <commit> <fecha> ...] \
        [--raiz .]

Cada fichero de ronda es la salida del workflow scripts/auditoria/reauditoria.js ({"resultados": [...]}, o la salida
completa de la tarea, con la clave "result"). Una ronda posterior sustituye a las anteriores en los hallazgos que
reaudita; el historial de cada hallazgo se conserva en reauditoria.json.

Regla del estado final de cada hallazgo en una ronda:
- sin respuesta del verificador → sin_reauditar;
- el verificador dice pendiente o no_aplica → ese estado;
- el verificador dice corregido o mitigado y el escéptico lo confirma → ese estado;
- el escéptico lo refuta → pendiente;
- el escéptico duda o no responde → <estado>_sin_confirmar.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

FINAL_ORDER = ["pendiente", "corregido_sin_confirmar", "mitigado_sin_confirmar", "sin_reauditar", "mitigado",
               "corregido", "no_aplica"]
LABEL = {
    "pendiente": "Pendiente (el defecto sigue, total o parcialmente)",
    "corregido_sin_confirmar": "Corregido según el verificador, sin confirmar por el escéptico",
    "mitigado_sin_confirmar": "Mitigado según el verificador, sin confirmar por el escéptico",
    "sin_reauditar": "Sin reauditar (fallo o ausencia de respuesta del agente)",
    "mitigado": "Mitigado y declarado (lo que el código no puede eliminar del todo está reducido y declarado)",
    "corregido": "Corregido (confirmado por el escéptico)",
    "no_aplica": "No aplica",
}
SEV = ["alta", "media", "baja"]
TMP_PATH = re.compile(r"(/tmp/claude-0|/root/\.claude)/[^\s)'\"`,;]*")


def load_round(path: Path) -> list[dict]:
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, dict) and "resultados" not in data and "result" in data:
        data = data["result"]
    return data["resultados"]


def evaluate_round(resultados: list[dict]) -> tuple[dict, list[str]]:
    """Estado final de cada hallazgo reauditado en una ronda, e incidencias de la ronda."""
    check, refute, incidencias = {}, {}, []
    for blk in resultados:
        if blk.get("fallo"):
            incidencias.append(f"{blk['bloque']}: {blk['fallo']} ({len(blk.get('hallazgos', []))} hallazgos)")
            continue
        for it in blk.get("comprobacion") or []:
            check[it["id"]] = it
        if blk.get("refutacion") is None and any(i["estado"] in ("corregido", "mitigado")
                                                 for i in blk.get("comprobacion") or []):
            incidencias.append(f"{blk['bloque']}: el escéptico no devolvió resultado")
        for it in blk.get("refutacion") or []:
            refute[it["id"]] = it
        if blk.get("omitidos"):
            incidencias.append(f"{blk['bloque']}: la comprobación omitió {', '.join(blk['omitidos'])}")
    final = {}
    for hid, c in check.items():
        r = refute.get(hid)
        if c["estado"] in ("pendiente", "no_aplica"):
            estado, motivo = c["estado"], c["explicacion"]
        elif r is None:
            estado, motivo = f"{c['estado']}_sin_confirmar", c["explicacion"]
        elif r["veredicto"] == "confirmado":
            estado, motivo = c["estado"], c["explicacion"]
        elif r["veredicto"] == "refutado":
            estado, motivo = "pendiente", f"Refutado por el escéptico. {r['motivo']}"
        else:
            estado, motivo = f"{c['estado']}_sin_confirmar", f"Dudoso para el escéptico. {r['motivo']}"
        final[hid] = {"estado_final": estado, "motivo": motivo, "verificador": c, "esceptico": r}
    return final, incidencias


def clean(text: str) -> str:
    text = TMP_PATH.sub("(directorio temporal de la reauditoría)", text)
    return re.sub(r"\s+", " ", text).strip()


def summary(text: str, n: int) -> str:
    """Primeras frases completas (que no acaben en dos puntos) hasta unos n caracteres."""
    t = clean(text)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"'0-9-])", t)
    out = ""
    for s in sentences:
        cand = (out + " " + s).strip()
        if len(cand) > n:
            break
        out = cand
        if len(out) >= n * 0.5 and not out.endswith(":"):
            break
    if not out:
        out = t[: n - 1] + "…" if len(t) > n else t
    elif out.endswith(":"):
        rest = t[len(out):].strip()
        extra = (out + " " + rest)[:n].rstrip()
        out = extra + ("…" if len(out + " " + rest) > n else "")
    return out


def cell(text: str, n: int) -> str:
    return summary(text, n).replace("|", "\\|")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hallazgos", required=True, type=Path)
    ap.add_argument("--ronda", nargs=3, action="append", required=True, metavar=("RESULTADOS", "COMMIT", "FECHA"))
    ap.add_argument("--raiz", type=Path, default=Path("."))
    ap.add_argument("--rama", default="correcciones-auditoria")
    a = ap.parse_args()

    hallazgos = json.loads(a.hallazgos.read_text("utf-8"))
    by_id = {h["id"]: h for h in hallazgos}
    rondas, actual, historial = [], {}, {h["id"]: [] for h in hallazgos}
    for i, (path, commit, fecha) in enumerate(a.ronda, start=1):
        final, incid = evaluate_round(load_round(Path(path)))
        rondas.append({"ronda": i, "fichero": str(path), "commit": commit, "fecha": fecha, "incidencias": incid,
                       "reauditados": len(final),
                       "por_estado": dict(Counter(f["estado_final"] for f in final.values()))})
        for hid, f in final.items():
            if hid not in by_id:
                continue
            actual[hid] = {**f, "ronda": i}
            historial[hid].append({"ronda": i, "commit": commit, "estado_final": f["estado_final"]})
    final = {}
    for h in hallazgos:
        f = actual.get(h["id"]) or {"estado_final": "sin_reauditar", "motivo": "Sin respuesta del verificador",
                                     "verificador": None, "esceptico": None, "ronda": None}
        final[h["id"]] = {"id": h["id"], "dimension": h["dimension"], "gravedad": h["gravedad"], **f,
                          "historial": historial[h["id"]]}

    ult = rondas[-1]
    cnt = Counter((f["estado_final"], f["gravedad"]) for f in final.values())
    tot = Counter(f["estado_final"] for f in final.values())
    out: list[str] = []
    w = out.append
    w("# Estado y limitaciones de nutria")
    w("")
    w(f"Generado el {ult['fecha']} a partir de {len(rondas)} ronda(s) de reauditoría sobre la rama `{a.rama}` (última: "
      f"commit `{ult['commit']}`). Este documento lo produce `scripts/auditoria/gen_estado.py` a partir de los "
      "resultados guardados en `docs/auditoria/`: no se edita a mano.")
    w("")
    w("> **Cómo leerlo.** La auditoría de línea base (commit `236a161`) confirmó 151 defectos en 11 dimensiones "
      "(`docs/auditoria/AUDITORIA_LINEA_BASE.md`, `docs/auditoria/hallazgos_linea_base.json`). Tras cada tanda de "
      "correcciones, una reauditoría revisa cada defecto contra el código de ese momento: en cada bloque, un agente "
      "verificador lo clasifica (corregido, mitigado, pendiente o no aplica) leyendo el código y ejecutando pruebas, y un "
      "agente escéptico intenta refutar cada «corregido» o «mitigado». Solo cuenta como corregido lo que el escéptico "
      "confirma. Verificadores y escépticos son modelos de lenguaje: pueden equivocarse, y otra ejecución puede dar "
      "resultados distintos. Ninguna persona ha revisado todavía esta clasificación.")
    w("")
    w("## Resumen")
    w("")
    w("| Estado final | Alta | Media | Baja | Total |")
    w("|---|---|---|---|---|")
    for e in FINAL_ORDER:
        if tot.get(e):
            w(f"| {LABEL[e]} | " + " | ".join(str(cnt.get((e, s), 0)) for s in SEV) + f" | {tot[e]} |")
    w("| **Total** | " + " | ".join(str(sum(cnt.get((e, s), 0) for e in FINAL_ORDER)) for s in SEV)
      + f" | {len(final)} |")
    w("")
    w("### Rondas")
    w("")
    w("| Ronda | Fecha | Commit | Hallazgos reauditados | Resultado de la ronda |")
    w("|---|---|---|---|---|")
    for r in rondas:
        res = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(r["por_estado"].items(),
                                                                         key=lambda kv: FINAL_ORDER.index(kv[0])))
        w(f"| {r['ronda']} | {r['fecha']} | `{r['commit']}` | {r['reauditados']} | {res} |")
    w("")
    incid = [f"ronda {r['ronda']}: {x}" for r in rondas for x in r["incidencias"]]
    if incid:
        w("**Incidencias de la reauditoría** (se informan, no se ocultan): " + "; ".join(incid) + ".")
        w("")

    def seccion(titulo: str, estados: set[str], intro: str | None = None, detalle: bool = True) -> None:
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
                w(f"| {f['id']} | {f['gravedad']} | {cell(by_id[f['id']]['texto'], 240)} | {cell(f['motivo'], 420)} |")
        else:
            w("| ID | Gravedad | Defecto original |")
            w("|---|---|---|")
            for f in items:
                w(f"| {f['id']} | {f['gravedad']} | {cell(by_id[f['id']]['texto'], 200)} |")
        w("")

    seccion("Pendientes", {"pendiente"},
            "Defectos que siguen presentes total o parcialmente, incluidos los «corregidos» que el escéptico refutó. "
            "Son el trabajo prioritario antes de usar el sistema para decisiones.")
    seccion("Sin confirmar", {"corregido_sin_confirmar", "mitigado_sin_confirmar", "sin_reauditar"},
            "El verificador los da por corregidos o mitigados, pero el escéptico no pudo confirmarlo (o no hubo "
            "respuesta). Trátalos como pendientes hasta que una persona los compruebe.")
    seccion("Mitigados y declarados", {"mitigado"},
            "Defectos que el código no puede eliminar del todo (dependen de personas, de servicios externos, de "
            "ejecuciones reales o de la disciplina de los agentes). Están reducidos y declarados.")
    seccion("Corregidos y confirmados", {"corregido"}, detalle=False)
    seccion("No aplican", {"no_aplica"})

    w("## Limitaciones que no dependen de un defecto concreto")
    w("")
    for line in (
        "- **Agentes, skills y workflows sin ejecución real de principio a fin.** Se han revisado leyendo su código y "
        "con pruebas que simulan las respuestas de los agentes (`tests/test_orquestacion.py`); su comportamiento con "
        "modelos reales, su coste en tokens y su tiempo no se han medido. No se ha ejecutado ninguna misión.",
        "- **Clientes de API sin probar contra los servicios reales.** La red del entorno donde se construyó bloquea las "
        "fuentes científicas; las pruebas en vivo existen (`tests/test_vivo.py`) y se activan con "
        "`NUTRIA_PRUEBAS_EN_VIVO=1`.",
        "- **Normativa pendiente de verificación humana.** Lo marcado «pendiente» en `docs/REGULACION_UE.md` debe "
        "comprobarlo una persona en EUR-Lex (texto consolidado, CELEX y fecha) y registrarlo con "
        "`nutria verificar manual <URL> --firma-humana NOMBRE`.",
        "- **La firma humana es una atestación, no una autenticación.** La CLI la rechaza dentro de Claude Code y con "
        "`--agente`, y los hooks bloquean sus abreviaturas, pero nada identifica criptográficamente a la persona.",
        "- **La ceguera de los evaluadores y los sellos son barreras, no controles de seguridad.** La CLI oculta "
        "puntuaciones, ranking y procedencia a `--agente evaluador-*` y el sello va firmado con HMAC, pero un proceso "
        "con los permisos del usuario podría leer la base de datos o la clave.",
        "- **Los hooks frenan errores y atajos de los agentes, no a un atacante.** Comparan texto de órdenes y rutas; "
        "el hook de verificación actúa después de escribir y no ve lo escrito con Bash.",
        "- **Sin modelos de racionamiento ni simulación metabólica.** La modelización propia se limita al destino "
        "ruminal (Ørskov y McDonald) y a la conversión de dosis; los requerimientos se calculan con software oficial o "
        "validado (p. ej. NASEM 2021).",
        "- **La versión exacta del modelo de cada subagente no se registra automáticamente**: anótala en el Anexo C de "
        "cada informe.",
        "- **Fase exclusivamente computacional.** Nada de lo que produce el sistema es un resultado experimental; las "
        "hipótesis de IA no son hallazgos.",
    ):
        w(line)
    w("")
    w("## Cómo se regenera")
    w("")
    w("Los resultados de cada ronda están en `docs/auditoria/reauditoria_ronda<N>.json`, y el estado combinado, con el "
      "veredicto del verificador y del escéptico y el historial de cada defecto, en `docs/auditoria/reauditoria.json`. "
      "Para una ronda nueva, lanza desde Claude Code el workflow `scripts/auditoria/reauditoria.js` (herramienta "
      "Workflow con `scriptPath`; argumentos: `archivo` = `docs/auditoria/hallazgos_linea_base.json`, "
      "`previo` = `docs/auditoria/reauditoria.json`, `raiz` = la raíz del repositorio e `ids` = los identificadores por "
      "dimensión), guarda su resultado como `docs/auditoria/reauditoria_ronda<N>.json` y regenera este documento con "
      "`python scripts/auditoria/gen_estado.py --hallazgos docs/auditoria/hallazgos_linea_base.json` seguido de un "
      "`--ronda <fichero> <commit> <fecha>` por ronda, en orden.")
    w("")

    root = a.raiz
    (root / "docs" / "auditoria").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "ESTADO_Y_LIMITACIONES.md").write_text("\n".join(out), "utf-8")
    (root / "docs" / "auditoria" / "reauditoria.json").write_text(
        json.dumps({"rondas": rondas, "hallazgos": [final[h["id"]] for h in hallazgos]}, ensure_ascii=False,
                   indent=1) + "\n", "utf-8")
    print(json.dumps({"total": len(final), "por_estado": dict(tot), "rondas": rondas}, ensure_ascii=False))


if __name__ == "__main__":
    main()
