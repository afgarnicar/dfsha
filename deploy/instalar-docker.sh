#!/usr/bin/env bash
# Prepara una VM Ubuntu nueva (22.04 o 24.04) para correr DFSha:
# instala Git, Docker Engine y el plugin de Docker Compose.
#
# Uso (en la VM, como el usuario ubuntu):
#   bash instalar-docker.sh
# Después cierre la sesión SSH y vuelva a entrar para poder usar docker sin sudo.

set -euo pipefail

echo "==> Actualizando paquetes e instalando dependencias..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git

echo "==> Agregando el repositorio oficial de Docker..."
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "==> Instalando Docker Engine y Docker Compose..."
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> Permitiendo usar docker sin sudo..."
sudo usermod -aG docker "$USER"

echo "==> Creando la carpeta de bloques /data/dfsha (la usan los DataNodes)..."
sudo mkdir -p /data/dfsha

echo
docker --version
docker compose version
echo
echo "Listo. Cierre la sesión SSH (exit) y vuelva a entrar antes de usar docker."
