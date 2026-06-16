#!/bin/bash
# Synct trello-klon-sv → mkan-public (sensitives entfernt) und pusht nach GitHub.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
PUB="$SRC/../mkan-public"

echo "=== mkan public sync ==="

# Alle tracked files kopieren (außer userdata) — IFS+read -r -d '' für Sonderzeichen in Namen
git -C "$SRC" ls-files -z | grep -zv "^trel_sv userdata.md$" | while IFS= read -r -d '' f; do
  mkdir -p "$PUB/$(dirname "$f")"
  cp "$SRC/$f" "$PUB/$f"
done

# Sensitive Strings ersetzen
cd "$PUB"
TECREF="trello-klon-sv – Technische Referenz für Claude & Milan.md"
sed -i 's|9Zm8fplquxCfGWfQoHwe|YOUR_OO_SECRET_HERE|g' "$TECREF" docker-compose.yml 2>/dev/null || true
sed -i 's|milnuc@milnus|user@yourserver|g' "$TECREF" deploy.sh deploy-mkan.sh DEPLOY.md 2>/dev/null || true
sed -i 's|/home/milnuc/mkan/|/path/to/mkan/|g' "$TECREF" docker-compose.yml 2>/dev/null || true
sed -i 's|milnuc:milnuc|mkanuser:mkanuser|g' DEPLOY.md 2>/dev/null || true
sed -i 's|mkan\.milan\.how|mkan.yourdomain.example|g' nginx-block.conf DEPLOY.md mkan-architektur.html 2>/dev/null || true

# Commit + Push
git add .
if git diff --cached --quiet; then
  echo "Keine Änderungen."
else
  MSG="${1:-sync from trello-klon-sv}"
  git commit -m "$MSG"
  git push
  echo "=== Gepusht ==="
fi
