#!/bin/bash
set -e

echo "=== mkan Deploy ==="

# Verzeichnis und .env
mkdir -p ~/mkan
cp -r /tmp/mkan-server/* ~/mkan/
cd ~/mkan

if [ ! -f .env ]; then
    echo "SECRET_KEY=$(openssl rand -hex 32)" > .env
    echo ".env angelegt"
fi

# DB-Backup vor Migration
DB_PATH=/mnt/mkan/db/kanban.sqlite
if [ -f "$DB_PATH" ]; then
    BACKUP="${DB_PATH}.bak-$(date +%Y%m%d-%H%M)"
    cp "$DB_PATH" "$BACKUP"
    echo "DB-Backup: $BACKUP"
fi

# Container bauen und starten
docker compose up -d --build
echo "Warte 8s..."
sleep 8
docker compose ps
docker compose logs mkan --tail 15
echo "=== mkan läuft ==="
