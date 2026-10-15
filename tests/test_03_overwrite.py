"""Suite 3 — PUT con overwrite: reemplazo seguro de un archivo existente."""

import os

from helpers import (MB, Results, api, cl, dn_total, get, ls, op, put, rm, unique, user)

CONFIG = {}


def main() -> None:
    t = Results()
    beto, juan = unique("beto"), unique("juan")
    hb, hj = user(beto), user(juan)
    home = f"/home/{beto}"
    path = f"{home}/doc.bin"
    v1, v2, v3 = os.urandom(int(2.5 * MB)), os.urandom(int(4.2 * MB)), b"chiquito"

    t.section("PUT y overwrite")
    antes = dn_total()
    r = put(hb, home, "doc.bin", v1)
    t.check("PUT nuevo -> 201 con 3 bloques",
            r.status_code == 201 and r.json()["block_count"] == 3 and not r.json()["overwritten"], r.text)
    t.check("se guardaron 6 copias (3 bloques x 2)", dn_total() - antes == 6, dn_total() - antes)
    t.check("PUT repetido sin overwrite -> 409", put(hb, home, "doc.bin", v2).status_code == 409)
    t.check("PUT con overwrite=false -> 409", put(hb, home, "doc.bin", v2, overwrite=False).status_code == 409)
    r = put(hb, home, "doc.bin", v2, overwrite=True)
    t.check("PUT con overwrite -> 201, 5 bloques, overwritten=true",
            r.status_code == 201 and r.json()["block_count"] == 5 and r.json()["overwritten"], r.text)
    t.check("GET devuelve el contenido nuevo", get(hb, path).content == v2)
    t.check("las copias de la versión vieja se borraron (quedan 10)", dn_total() - antes == 10, dn_total() - antes)
    t.check("ls muestra un solo doc.bin", ls(hb, home).count("doc.bin") == 1)
    r = put(hb, home, "doc.bin", v3, overwrite=True)
    t.check("overwrite con un archivo pequeño (1 bloque)",
            r.status_code == 201 and r.json()["block_count"] == 1 and get(hb, path).content == v3, r.text)
    t.check("quedan solo 2 copias", dn_total() - antes == 2, dn_total() - antes)
    r = put(hb, home, "vacio.txt", b"")
    t.check("PUT de un archivo vacío -> 201, 0 bloques", r.status_code == 201 and r.json()["block_count"] == 0, r.text)
    r = put(hb, home, "nuevo.txt", b"hola", overwrite=True)
    t.check("overwrite sobre un archivo que no existe lo crea",
            r.status_code == 201 and not r.json()["overwritten"], r.text)

    t.section("Lock y permisos")
    h = op(hb, path, "WRITE").json()["handle"]
    r = put(hb, home, "doc.bin", v1, overwrite=True)
    t.check("overwrite con el archivo abierto en WRITE por otra sesión -> 423", r.status_code == 423, r.text)
    cl(hb, h)
    h = op(hb, path, "READ").json()["handle"]
    t.check("overwrite con el archivo abierto solo en READ -> 201",
            put(hb, home, "doc.bin", v1, overwrite=True).status_code == 201)
    cl(hb, h)
    r2 = op(hb, path, "WRITE")
    t.check("después del overwrite el archivo no queda bloqueado (open WRITE -> 200)", r2.status_code == 200, r2.text)
    cl(hb, r2.json()["handle"])
    api(hb, "PATCH", "/access/mode", json={"path": home, "mode": "777"})
    t.check("juan, sin permiso de escritura sobre el archivo, no puede sobrescribir -> 403",
            put(hj, home, "doc.bin", v3, overwrite=True).status_code == 403)
    t.check("el contenido de beto sigue intacto", get(hb, path).content == v1)
    api(hb, "PUT", "/access/acl", json={"path": path, "subject_type": "user", "subject": juan,
                                        "read": True, "write": True})
    r = put(hj, home, "doc.bin", v3, overwrite=True)
    t.check("con ACL rw- sobre el archivo, juan sí puede sobrescribir",
            r.status_code == 201 and get(hb, path).content == v3, r.text)
    info = api(hb, "GET", "/access", params={"path": path}).json()
    t.check("el dueño y la ACL se conservan después de sobrescribir",
            info["owner"] == beto and len(info["acl"]) == 1, info)

    t.section("rm y volver a subir")
    rm(hb, path)
    r = put(hb, home, "doc.bin", v1)
    t.check("después de rm se puede subir de nuevo sin overwrite",
            r.status_code == 201 and get(hb, path).content == v1, r.text)
    t.finish()


if __name__ == "__main__":
    main()
