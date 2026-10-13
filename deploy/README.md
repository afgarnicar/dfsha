# Despliegue de DFSha en AWS Academy (4 VMs)

Guía para montar el sistema en 4 instancias EC2 del Learner Lab de AWS Academy:

| VM | Nombre en EC2 | Qué corre | Tipo sugerido |
|---|---|---|---|
| VM1 | `dfsha-control` | ControlNode + PostgreSQL + Nginx | t3.medium (4 GB de RAM) |
| VM2 | `dfsha-dn1` | DataNode DN1 | t3.small |
| VM3 | `dfsha-dn2` | DataNode DN2 | t3.small |
| VM4 | `dfsha-dn3` | DataNode DN3 | t3.small |

Las 4 VMs se comunican por la **red privada** de AWS (IP privadas 172.31.x.x).
El cliente solo habla con Nginx en la VM1, por **HTTPS** (certificado
autofirmado). Los DataNodes no quedan públicos, también hablan solo **HTTPS**
(entre ControlNode y DataNodes y entre DataNodes) y solo aceptan peticiones que
traigan la clave interna `X-DFSHA-API-Key`. El certificado interno se deriva de
esa misma clave, así que no hay que crear ni copiar certificados entre VMs.

---

## 1. Entrar al Learner Lab

1. En AWS Academy, abre el curso → **Modules** → **Learner Lab**.
2. Dale **Start Lab** y espera a que el círculo al lado de "AWS" se ponga verde.
3. Dale clic a **AWS** para abrir la consola. Revisa arriba a la derecha que la región sea **N. Virginia (us-east-1)**.
4. En el Learner Lab, entra a **AWS Details** → **Download PEM**. Se descarga `labsuser.pem`: es la llave para entrar por SSH a las VMs. Guárdala en una carpeta fija, por ejemplo `C:\Users\<tu-usuario>\.ssh\labsuser.pem`.

> La sesión del Learner Lab dura 4 horas. Al terminar, las VMs se **apagan** (no
> se borran). Cuando vuelvas a iniciar el lab, préndelas desde EC2. Las IP
> privadas se mantienen; las IP públicas cambian.

## 2. Crear los dos security groups

En la consola: **EC2** → **Security Groups** → **Create security group**.

**`dfsha-control`** (para la VM1):

| Tipo | Puerto | Origen | Para qué |
|---|---|---|---|
| SSH | 22 | My IP | Administrar la VM |
| HTTPS | 443 | Anywhere-IPv4 (0.0.0.0/0) | Que el cliente llegue a Nginx |
| HTTP | 80 | Anywhere-IPv4 (0.0.0.0/0) | Solo redirige a HTTPS (opcional) |

**`dfsha-datanode`** (para las VM2, VM3 y VM4). Créalo primero con la regla de
SSH, guárdalo, y después edítalo para agregar las dos reglas del puerto 8000
(así ya puedes elegirlo a él mismo como origen):

| Tipo | Puerto | Origen | Para qué |
|---|---|---|---|
| SSH | 22 | My IP | Administrar la VM |
| Custom TCP | 8000 | security group `dfsha-control` | Que el ControlNode hable con el DataNode |
| Custom TCP | 8000 | security group `dfsha-datanode` | Que los DataNodes se copien bloques entre ellos |

Nunca abras el 8000 a `0.0.0.0/0`.

## 3. Crear las 4 instancias

**EC2** → **Instances** → **Launch instances**. Para cada una:

- **Name**: el de la tabla de arriba.
- **AMI**: Ubuntu Server 24.04 LTS (o 22.04).
- **Instance type**: t3.medium para `dfsha-control`; t3.small para los DataNodes. Si el lab no deja alguno, usa el más cercano que permita.
- **Key pair**: `vockey` (es la que corresponde a `labsuser.pem`).
- **Network settings** → **Edit** → **Select existing security group**: `dfsha-control` para la VM1 y `dfsha-datanode` para las otras tres. Deja la VPC y la subred por defecto, la misma para las 4.
- **Storage**: 20 GB para la VM1 y 30 GB para cada DataNode.

Cuando estén en **Running**, anota de cada una la **Public IPv4 address** (para
entrar por SSH) y la **Private IPv4 address** (para la configuración).

## 4. Entrar a cada VM por SSH (desde PowerShell)

La primera vez, ajusta los permisos de la llave, o SSH la rechaza por "too open":

```powershell
icacls "$HOME\.ssh\labsuser.pem" /inheritance:r
icacls "$HOME\.ssh\labsuser.pem" /grant:r "$($env:USERNAME):(R)"
```

Para entrar (cambia la IP por la pública de cada VM):

```powershell
ssh -i "$HOME\.ssh\labsuser.pem" ubuntu@<IP-PUBLICA>
```

## 5. Preparar las 4 VMs (en cada una)

```bash
git clone https://github.com/afgarnicar/dfsha.git
cd dfsha
bash deploy/instalar-docker.sh
exit
```

Vuelve a entrar por SSH (para que el usuario quede en el grupo `docker`).

## 6. Levantar los DataNodes (VM2, VM3 y VM4)

Primero genera **una sola** clave interna (en cualquier VM) y guárdala, porque
va igual en las 4 VMs:

```bash
openssl rand -hex 32
```

En cada VM de DataNode, cambiando `DN1` por `DN2` o `DN3` según la VM, y
pegando la clave interna donde dice `<CLAVE-INTERNA>`:

```bash
cd ~/dfsha/deploy/datanode
cp .env.example .env
sed -i 's/^NODE_ID=.*/NODE_ID=DN1/' .env
sed -i 's/^INTERNAL_API_KEY=.*/INTERNAL_API_KEY=<CLAVE-INTERNA>/' .env
docker compose up -d --build
curl -k https://localhost:8000/health
```

Debe responder `{"node_id":"DN1","status":"ok",...}`.

## 7. Levantar el ControlNode (VM1)

```bash
cd ~/dfsha/deploy/control
cp .env.example .env
```

Abre el `.env` con `nano .env` y cambia:

1. `POSTGRES_PASSWORD` y la misma clave dentro de `DATABASE_URL`.
2. `JWT_SECRET`: genéralo con `openssl rand -hex 32`. `INTERNAL_API_KEY`: la **misma** clave interna que pusiste en los DataNodes.
3. `DATANODES`: las **IP privadas** de las VM2, VM3 y VM4.

Guarda con `Ctrl+O`, Enter, `Ctrl+X`. Después genera un certificado nuevo para
Nginx (el del repositorio es solo para pruebas locales, su llave es pública) y
dile a git que no lo tome como un cambio:

```bash
openssl req -x509 -nodes -newkey rsa:2048 -days 365 -subj "/CN=dfsha" \
  -keyout ~/dfsha/nginx/certs/dfsha.key -out ~/dfsha/nginx/certs/dfsha.crt
git -C ~/dfsha update-index --skip-worktree nginx/certs/dfsha.key nginx/certs/dfsha.crt
```

Y levanta todo:

```bash
docker compose build
KEY=$(docker compose run --rm --no-deps control_node python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
sed -i "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$KEY|" .env
docker compose run --rm control_node alembic upgrade head
docker compose up -d
```

> No cambies la `ENCRYPTION_KEY` después de subir archivos: lo que se cifró con
> la clave anterior ya no se podría leer.

## 8. Probar que todo funciona (en la VM1)

```bash
bash ~/dfsha/deploy/prueba-despliegue.sh
```

Debe decir `10 pasaron, 0 fallaron`: HTTP redirige a HTTPS, el ControlNode
responde y ve los 3 DataNodes ONLINE, los DataNodes solo aceptan HTTPS, un
archivo de 100 MB sube y baja idéntico pasando por las VMs, y se puede
reemplazar con overwrite.

Prueba también desde tu computador, con la IP pública de la VM1. El `-k` es
porque el certificado es autofirmado:

```powershell
curl.exe -k https://<IP-PUBLICA-VM1>/health
curl.exe -k https://<IP-PUBLICA-VM1>/datanodes
```

Y con el cliente CLI, desde la carpeta del repo en tu computador (la primera
vez instala sus dependencias con `pip install -r client/requirements.txt`; los
comandos `register` y `login` te piden la contraseña):

```powershell
python -m client.cli set-url https://<IP-PUBLICA-VM1>
python -m client.cli auth register beto
python -m client.cli auth login beto
python -m client.cli fs ls /home/beto
```

**Caída de un nodo:** en la VM3 corre `docker compose stop`, espera unos 40
segundos y en la VM1 corre `curl -sk https://localhost/datanodes`: DN2 debe
pasar a SUSPECT y luego a OFFLINE, y en `docker compose logs control_node` se ve
la re-replicación de sus bloques a los otros nodos. Con `docker compose start`
en la VM3 vuelve a ONLINE.

## 9. Actualizar el código en las VMs

Cuando haya cambios nuevos en `main`, en cada VM:

```bash
cd ~/dfsha && git pull
cd deploy/control     # o deploy/datanode en las VMs de DataNode
docker compose up -d --build
```

En la VM1, si hay migraciones nuevas, antes del `up`:
`docker compose run --rm control_node alembic upgrade head`.

## 10. Al terminar

- Los contenedores tienen `restart: unless-stopped`: cuando se prende una VM, arrancan solos.
- Cuando no estés usando el sistema, detén el lab (**End Lab**) o apaga las instancias para no gastar créditos.

## Notas de seguridad

- El certificado de `nginx/certs/` que está en el repositorio es solo para
  pruebas locales (su llave es pública en GitHub). En la VM1 se reemplaza por
  uno nuevo en el paso 7, que no se sube a git. Es autofirmado: por eso el
  cliente y curl no verifican el certificado (uso académico).
- El HTTPS interno usa un certificado derivado de `INTERNAL_API_KEY`: el
  ControlNode solo confía en DataNodes que conozcan esa clave. Si se cambia la
  clave, hay que cambiarla en las 4 VMs al mismo tiempo.
- Deja el puerto 22 solo para las IP del equipo (`My IP`) y nunca abras el 8000
  de los DataNodes a `0.0.0.0/0`.
