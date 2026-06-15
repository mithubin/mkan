# Deployment auf dem NUC

Folgt exakt dem Muster der anderen Services (webproxy-Netz, zentrale nginx, certbot).

## 1. DDNS-Eintrag bei kasserver.com

Neuen DDNS-Host `mkan.yourdomain.example` anlegen, Credentials in
`/usr/local/bin/update-ddns-all.sh` eintragen (analog zu den anderen Einträgen).

## 2. Verzeichnis und Daten anlegen

```bash
sudo mkdir -p /mnt/mkan/db /mnt/mkan/uploads
sudo chown -R mkanuser:mkanuser /mnt/mkan

mkdir ~/mkan
cd ~/mkan
# Projektdateien hierher kopieren/clonen
```

## 3. .env anlegen

```bash
cat > ~/mkan/.env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
EOF
```

## 4. SSL-Zertifikat

```bash
sudo certbot certonly --webroot -w /mnt/nextcloud/letsencrypt -d mkan.yourdomain.example
sudo cp /etc/letsencrypt/live/mkan.yourdomain.example/fullchain.pem /mnt/nextcloud/ssl/mkan-fullchain.pem
sudo cp /etc/letsencrypt/live/mkan.yourdomain.example/privkey.pem   /mnt/nextcloud/ssl/mkan-privkey.pem
sudo chmod 644 /mnt/nextcloud/ssl/mkan-*.pem
```

Deploy-Hook `/etc/letsencrypt/renewal-hooks/deploy/copy-certs.sh` um mkan ergänzen:
```bash
cp /etc/letsencrypt/live/mkan.yourdomain.example/fullchain.pem /mnt/nextcloud/ssl/mkan-fullchain.pem
cp /etc/letsencrypt/live/mkan.yourdomain.example/privkey.pem   /mnt/nextcloud/ssl/mkan-privkey.pem
chmod 644 /mnt/nextcloud/ssl/mkan-*.pem
```

## 5. Nginx-Block einbinden

Inhalt von `nginx-block.conf` in `~/nexcloc/nginx.conf` einfügen, dann:

```bash
cd ~/nexcloc
docker compose exec nginx nginx -t        # Syntax prüfen
docker compose exec nginx nginx -s reload
```

## 6. Container bauen und starten

```bash
cd ~/mkan
docker compose up -d --build
docker compose logs -f mkan   # kurz beobachten
```

## 7. Ersten User anlegen

```bash
curl -X POST https://mkan.yourdomain.example/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"Milan","email":"linux@milan.how","password":"SICHERES_PASSWORT"}'
```

## Betrieb

```bash
docker compose logs -f mkan          # Logs
docker compose restart mkan          # Neustart
docker compose pull && docker compose up -d --build   # Update

# Backup (in nextcloud-backup.sh ergänzen):
sqlite3 /mnt/mkan/db/kanban.sqlite ".backup /mnt/nextcloud/backups/mkan-$(date +%F).sqlite"
rsync -a /mnt/mkan/uploads/ /mnt/nextcloud/backups/mkan-uploads/
```
