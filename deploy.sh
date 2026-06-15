#!/bin/bash
# Lokaler Deploy-Script: Frontend kopieren, paketieren, hochladen, NUC-Deploy starten.
# Live-Output via SSH-Heredoc (kein Buffering).
set -e
cd "$(dirname "$0")"

echo "→ Frontend kopieren"
cp multikanban-server.html server/static/index.html

echo "→ Paket bauen"
tar czf /tmp/mkan-deploy.tar.gz server/ docker-compose.yml deploy-mkan.sh

echo "→ Upload zum NUC"
scp -q /tmp/mkan-deploy.tar.gz user@yourserver:/tmp/
echo "   Upload fertig."

echo "→ Deploy auf NUC"
ssh user@yourserver 'bash -s' << 'REMOTE'
set -e
cd /tmp
rm -rf mkan-server
mkdir mkan-server
tar xzf mkan-deploy.tar.gz -C mkan-server
bash mkan-server/deploy-mkan.sh
REMOTE
