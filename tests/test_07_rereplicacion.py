"""Suite 7 — Re-replicación: al caer un DataNode se recuperan todas sus copias.

Mide también que el ControlNode siga respondiendo mientras copia bloques en
segundo plano.
"""

import os
import threading
import time

from helpers import MB, Results, backend, get, put, raw, stop_dn, unique, user, wait_status, CN

CONFIG = {"FAILURE_THRESHOLD": 3}
BLOCKS = 40  # archivo de 40 MB = 40 bloques de 1 MB = 80 copias


def copias_en_vivos() -> int:
    return int(backend.sql(
        "select count(*) from block_locations l join datanodes d on d.id=l.datanode_id where d.name <> 'DN2'"))


def main() -> None:
    t = Results()
    name = unique("rerep")
    h = user(name)
    data = os.urandom(BLOCKS * MB)
    r = put(h, f"/home/{name}", "grande.bin", data)
    t.check(f"PUT de {BLOCKS} MB ({BLOCKS} bloques)", r.status_code == 201 and r.json()["block_count"] == BLOCKS,
            r.text[:200])
    antes = copias_en_vivos()
    t.check("antes de la caída, parte de las copias están en DN2", antes < 2 * BLOCKS, antes)

    latencias, parar = [], threading.Event()

    def medir():
        while not parar.is_set():
            inicio = time.time()
            try:
                raw().get(f"{CN}/health", timeout=60)
            except Exception:  # noqa: BLE001 — solo medimos el tiempo
                pass
            latencias.append(time.time() - inicio)
            time.sleep(0.2)

    hilo = threading.Thread(target=medir)
    hilo.start()
    stop_dn(2)
    inicio = time.time()
    t.check("DN2 pasa a OFFLINE", wait_status("DN2", "OFFLINE"))
    while time.time() - inicio < 120 and copias_en_vivos() < 2 * BLOCKS:
        time.sleep(1)
    duracion = time.time() - inicio
    parar.set()
    hilo.join()

    t.check(f"se recuperaron todas las copias: {copias_en_vivos()} de {2 * BLOCKS}",
            copias_en_vivos() == 2 * BLOCKS)
    print(f"        tiempo desde la caída hasta tener todo recuperado: {duracion:.1f} s", flush=True)
    peor = max(latencias)
    print(f"        /health consultado {len(latencias)} veces; la respuesta más lenta: {peor:.2f} s", flush=True)
    t.check("el ControlNode siguió respondiendo durante la re-replicación (todas las respuestas < 2 s)", peor < 2)
    r = get(h, f"/home/{name}/grande.bin")
    t.check("el archivo se descarga idéntico sin DN2", r.status_code == 200 and r.content == data)
    t.finish()


if __name__ == "__main__":
    main()
