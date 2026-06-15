#!/bin/bash
# OO local.json: IP-Blocking deaktiviert + siteUrl auf Proxy gesetzt
# Ausführen mit: bash oo-siteurl-fix.sh

sudo docker exec onlyoffice bash -c 'cat > /etc/onlyoffice/documentserver/local.json << '"'"'EOF'"'"'
{
  "externalRequest": {
    "action": {
      "blockPrivateIP": false
    }
  },
  "services": {
    "CoAuthoring": {
      "request-filtering-agent": {
        "allowPrivateIPAddress": true,
        "allowMetaIPAddress": true
      },
      "server": {
        "siteUrl": "http://localhost:7879/oo/"
      }
    }
  }
}
EOF'

sudo docker exec onlyoffice supervisorctl restart ds:docservice ds:converter

echo "Fertig. Jetzt im Browser: F12 -> Anwendung -> Service Workers -> Registrierung aufheben -> F5"
