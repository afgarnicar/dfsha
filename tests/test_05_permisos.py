"""Suite 5 — Permisos: chmod, chgrp, ACL de usuario y de grupo, y grupos."""

from helpers import Results, api, get, put, raw, unique, user, CN, PASSWORD

CONFIG = {}


def main() -> None:
    t = Results()
    beto, juan, ana = unique("beto"), unique("juan"), unique("ana")
    hb, hj, ha = user(beto), user(juan), user(ana)
    home, proj = f"/home/{beto}", f"/home/{beto}/proyecto"
    equipo = unique("equipo")
    api(hb, "POST", "/directories", json={"path": proj})
    put(hb, proj, "plan.txt", b"plan secreto")
    ls = lambda h: api(h, "GET", "/filesystem/list", params={"path": proj}).status_code
    descargar = lambda h: get(h, f"{proj}/plan.txt")

    t.section("Consultar permisos")
    r = api(hb, "GET", "/access", params={"path": proj})
    t.check("info muestra dueño y modo del directorio",
            r.status_code == 200 and r.json()["owner"] == beto and r.json()["mode_string"] == "rwxr-x---", r.text)
    r = api(hb, "GET", "/access", params={"path": f"{proj}/plan.txt"})
    t.check("info de un archivo (modo 640 por defecto)",
            r.status_code == 200 and r.json()["type"] == "file" and r.json()["mode"] == "640", r.text)
    t.check("info de una ruta que no existe -> 404",
            api(hb, "GET", "/access", params={"path": f"{proj}/nada"}).status_code == 404)

    t.section("ACL por usuario")
    t.check("juan no puede listar el proyecto de beto", ls(hj) == 403)
    r = api(hb, "PUT", "/access/acl", json={"path": home, "subject_type": "user", "subject": juan, "execute": True})
    t.check("beto le da a juan permiso de atravesar su home (--x)",
            r.status_code == 200 and r.json()["acl"][0]["execute"], r.text)
    api(hb, "PUT", "/access/acl", json={"path": proj, "subject_type": "user", "subject": juan,
                                        "read": True, "execute": True})
    t.check("con ACL r-x, juan ya puede listar el proyecto", ls(hj) == 200)
    t.check("pero no puede descargar plan.txt (no tiene ACL en el archivo)", descargar(hj).status_code == 403)
    api(hb, "PUT", "/access/acl", json={"path": f"{proj}/plan.txt", "subject_type": "user", "subject": juan,
                                        "read": True})
    r = descargar(hj)
    t.check("con ACL r-- en el archivo, juan lo descarga", r.status_code == 200 and r.content == b"plan secreto")
    t.check("juan no puede crear dentro del proyecto (su ACL es r-x)",
            api(hj, "POST", "/directories", json={"path": f"{proj}/juan"}).status_code == 403)
    api(hb, "DELETE", "/access/acl", json={"path": proj, "subject_type": "user", "subject": juan})
    t.check("al quitar la ACL, juan vuelve a no poder listar", ls(hj) == 403)

    t.section("Grupos")
    t.check("beto crea un grupo", api(hb, "POST", "/access/groups", json={"name": equipo}).status_code == 201)
    t.check("un grupo con nombre repetido -> 409",
            api(hb, "POST", "/access/groups", json={"name": equipo}).status_code == 409)
    t.check("beto agrega a ana al grupo",
            api(hb, "POST", f"/access/groups/{equipo}/members", json={"username": ana}).status_code == 204)
    t.check("juan, que no es miembro, no puede agregar gente al grupo -> 403",
            api(hj, "POST", f"/access/groups/{equipo}/members", json={"username": juan}).status_code == 403)
    r = api(hb, "PATCH", "/access/group", json={"path": proj, "group": equipo})
    t.check("beto asocia el proyecto al grupo (chgrp)", r.status_code == 200 and r.json()["group"] == equipo, r.text)
    api(hb, "PUT", "/access/acl", json={"path": home, "subject_type": "group", "subject": equipo, "execute": True})
    t.check("beto, dueño y miembro del grupo, sigue pudiendo usar su home (el dueño va primero, como en POSIX)",
            api(hb, "GET", "/filesystem/list", params={"path": home}).status_code == 200)
    t.check("ana (del grupo) puede listar: el grupo tiene r-x", ls(ha) == 200)
    t.check("ana no puede crear dentro: el grupo no tiene w",
            api(ha, "POST", "/directories", json={"path": f"{proj}/ana"}).status_code == 403)
    r = api(hb, "PATCH", "/access/mode", json={"path": proj, "mode": "770"})
    t.check("beto cambia el modo a 770 (chmod)", r.status_code == 200 and r.json()["mode_string"] == "rwxrwx---", r.text)
    t.check("ahora ana sí puede crear dentro del proyecto",
            api(ha, "POST", "/directories", json={"path": f"{proj}/ana"}).status_code == 201)
    t.check("juan sigue sin acceso (no es del grupo)", ls(hj) == 403)

    t.section("Grupos por id (/groups): nadie se mete solo a un grupo ajeno")
    ida = raw().post(f"{CN}/auth/register", json={"username": unique("pedro"), "password": PASSWORD})
    r = api(ha, "POST", "/groups", json={"name": unique("g")})
    t.check("POST /groups crea el grupo", r.status_code == 201, r.text)
    gid = r.json()["id"]
    juan_id = api(hj, "GET", "/auth/me").json()["id"]
    t.check("juan no puede agregarse solo al grupo de ana -> 403",
            api(hj, "POST", f"/groups/{gid}/members", json={"user_id": juan_id}).status_code == 403)
    t.check("ana (creadora y miembro) sí puede agregar a juan -> 204",
            api(ha, "POST", f"/groups/{gid}/members", json={"user_id": juan_id}).status_code == 204)
    t.check("juan, ya miembro, puede agregar a otro -> 204",
            api(hj, "POST", f"/groups/{gid}/members", json={"user_id": ida.json()["id"]}).status_code == 204)

    t.section("Solo el dueño cambia permisos")
    t.check("ana no puede hacer chmod del proyecto de beto",
            api(ha, "PATCH", "/access/mode", json={"path": proj, "mode": "777"}).status_code == 403)
    t.check("ana no puede ponerse una ACL a sí misma",
            api(ha, "PUT", "/access/acl", json={"path": proj, "subject_type": "user", "subject": ana,
                                                "read": True}).status_code == 403)
    otro = unique("otro")
    api(hj, "POST", "/access/groups", json={"name": otro})
    t.check("chgrp a un grupo del que beto no es miembro -> 403",
            api(hb, "PATCH", "/access/group", json={"path": proj, "group": otro}).status_code == 403)
    t.check("ACL para un usuario que no existe -> 404",
            api(hb, "PUT", "/access/acl", json={"path": proj, "subject_type": "user", "subject": "fantasma",
                                                "read": True}).status_code == 404)
    t.check("modo inválido (no es octal) -> 422",
            api(hb, "PATCH", "/access/mode", json={"path": proj, "mode": "999"}).status_code == 422)

    t.section("Los permisos se borran junto con su recurso")
    api(hb, "PATCH", "/access/mode", json={"path": proj, "mode": "750"})
    put(hb, home, "x.txt", b"x")
    api(hb, "PUT", "/access/acl", json={"path": f"{home}/x.txt", "subject_type": "user", "subject": juan, "read": True})
    api(hb, "DELETE", "/files", json={"path": f"{home}/x.txt"})
    put(hb, home, "x.txt", b"y")
    r = api(hb, "GET", "/access", params={"path": f"{home}/x.txt"}).json()
    t.check("un archivo nuevo con el mismo nombre no hereda la ACL del borrado", r.get("acl") == [], r)
    api(hb, "POST", "/directories", json={"path": f"{home}/d"})
    api(hb, "PUT", "/access/acl", json={"path": f"{home}/d", "subject_type": "user", "subject": juan, "read": True})
    t.check("rmdir de un directorio con ACL funciona",
            api(hb, "DELETE", "/directories", json={"path": f"{home}/d"}).status_code == 204)
    t.finish()


if __name__ == "__main__":
    main()
