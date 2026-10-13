#!/usr/bin/env bash
# Prueba rápida del despliegue. Se corre en la VM del ControlNode (VM1),
# después de levantar las 4 VMs. Usa curl contra Nginx por HTTPS (localhost).
# El certificado es autofirmado, por eso curl va con -k.
#
# Comprueba: el ControlNode responde, los 3 DataNodes están ONLINE, y un
# archivo de 100 MB se sube y se descarga idéntico (pasando por las VMs).
#
# Uso:  bash prueba-despliegue.sh

set -uo pipefail

CN="${CN:-https://localhost}"
curl() { command curl -k "$@"; }
WORK=$(mktemp -d)
OK=0
FAIL=0
trap 'rm -rf "$WORK"' EXIT

check() {
  if [ "$2" = "$3" ]; then
    echo "[PASA]  $1 ($2)"; OK=$((OK + 1))
  else
    echo "[FALLA] $1: esperaba $3, llegó $2"; FAIL=$((FAIL + 1))
  fi
}

echo "=== Servicios ==="
check "HTTP redirige a HTTPS" \
  "$(command curl -s -o /dev/null -w '%{http_code}' http://localhost/health)" 301
check "El ControlNode responde por HTTPS a través de Nginx" \
  "$(curl -s -o /dev/null -w '%{http_code}' $CN/health)" 200

ONLINE=$(curl -s $CN/datanodes | grep -o '"status":"ONLINE"' | wc -l)
check "Los 3 DataNodes están ONLINE" "$ONLINE" 3
curl -s $CN/datanodes | tr '}' '\n' | grep -o '"name":"[^"]*","host":"[^"]*".*"status":"[^"]*"' \
  | sed 's/"port":[0-9]*,//; s/"//g; s/,/  /g' | sed 's/^/        /'

echo
echo "=== Subir y descargar un archivo de 100 MB ==="
USER_NAME="prueba$RANDOM"
CRED="{\"username\":\"$USER_NAME\",\"password\":\"clave12345\"}"
curl -s -o /dev/null -X POST $CN/auth/register -H "Content-Type: application/json" -d "$CRED"
TOKEN=$(curl -s -X POST $CN/auth/login -H "Content-Type: application/json" -d "$CRED" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

head -c 100M /dev/urandom > "$WORK/original.bin"
check "Subir el archivo (PUT)" \
  "$(curl -s -o "$WORK/put.txt" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
     -F "directory_path=/home/$USER_NAME" -F "file=@$WORK/original.bin" $CN/files)" 201
check "Descargar el archivo (GET)" \
  "$(curl -s -o "$WORK/bajado.bin" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
     -G --data-urlencode "path=/home/$USER_NAME/original.bin" $CN/files/download)" 200
check "El archivo descargado es idéntico" \
  "$(cmp -s "$WORK/original.bin" "$WORK/bajado.bin" && echo si || echo no)" si

curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"path\":\"/home/$USER_NAME/original.bin\"}" $CN/files

echo
echo "=== Resultado: $OK pasaron, $FAIL fallaron ==="
