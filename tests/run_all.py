"""Corre las pruebas de punta a punta de DFSha y muestra un resumen.

Uso (desde la raíz del repositorio, con Docker Desktop abierto):

    python tests/run_all.py                # todas las suites
    python tests/run_all.py permisos       # solo las suites cuyo nombre contenga "permisos"
    python tests/run_all.py --keep         # no borra el clúster de pruebas al terminar

Para cada suite se borra el clúster de pruebas y se levanta de cero (base de
datos vacía, DataNodes sin bloques), con los tiempos que esa suite necesita.
Al final se borra todo. No toca el entorno de desarrollo ni AWS.
"""

import importlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

RESULT = re.compile(r"Resultado: (\d+) pasaron, (\d+) fallaron")


def suites(filters: list[str]) -> list[Path]:
    found = sorted(HERE.glob("test_*.py"))
    if filters:
        found = [p for p in found if any(f.lower() in p.stem.lower() for f in filters)]
    return found


def run_suite(path: Path) -> tuple[int, int, float]:
    module = importlib.import_module(path.stem)
    config = getattr(module, "CONFIG", {})
    title = (module.__doc__ or path.stem).strip().splitlines()[0]
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)
    print("Preparando un clúster de pruebas nuevo...", flush=True)
    helpers.backend.reset(config)
    if not helpers.wait_cluster_ready():
        print("[FALLA] El clúster de pruebas no quedó listo (ControlNode + 3 DataNodes ONLINE).", flush=True)
        return 0, 1, 0.0
    start = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-u", str(path)], cwd=str(HERE), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8",
                 PYTHONPATH=os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")])),
    )
    ok = fail = None
    for line in proc.stdout:
        print(line, end="", flush=True)
        m = RESULT.search(line)
        if m:
            ok, fail = int(m.group(1)), int(m.group(2))
    proc.wait()
    if ok is None:  # la suite se cayó antes de terminar
        ok, fail = 0, 1
    return ok, fail, time.time() - start


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep" in sys.argv
    selected = suites(args)
    if not selected:
        print("No hay suites con ese nombre.")
        return 1

    print("Construyendo las imágenes del clúster de pruebas (la primera vez tarda unos minutos)...", flush=True)
    helpers.backend.build()

    summary = []
    try:
        for path in selected:
            summary.append((path.stem, *run_suite(path)))
    finally:
        if not keep:
            print("\nBorrando el clúster de pruebas...", flush=True)
            helpers.backend.down()

    print(f"\n{'=' * 78}\nRESUMEN\n{'=' * 78}")
    print(f"{'Suite':<34}{'Pasaron':>9}{'Fallaron':>10}{'Tiempo':>10}")
    for name, ok, fail, secs in summary:
        print(f"{name:<34}{ok:>9}{fail:>10}{secs:>9.0f}s")
    total_ok = sum(s[1] for s in summary)
    total_fail = sum(s[2] for s in summary)
    print(f"{'TOTAL':<34}{total_ok:>9}{total_fail:>10}")
    print("\nTODO EN VERDE" if total_fail == 0 else "\nHAY PRUEBAS QUE FALLARON (ver arriba)")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
