# mkan — Multikanban Server

Selbstgehostetes kollaboratives Kanban + Dateiverwaltung für kleine nicht-IT-affine Gruppen.
Live: **https://mkan.milan.how/** · Git: Branch `mkan`

---

## Vision

Karten + Anhänge = kommentierte Dateiverwaltung mit Kontext. Sinnlich (Bilder, Farben, Drag), datensicher (Snapshots), retrospektiv (Klassenbuch).

Marktlücke: Nextcloud Deck (blass), Planka (keine Swimlanes), Notion (US-Cloud) — kein rundes selbstgehostetes visuelles Tool für die Zielgruppe.

**Leitprinzipien:**
- Granulare API — jede Aktion ist ein eigener Call mit minimalem Payload
- Frontend bleibt HTML-Einzeldatei — keine Build-Pipeline
- Keine Komplexität nach außen — Zielgruppe ist nicht-IT-affin

---

## Infra

| | |
|---|---|
| **Live** | https://mkan.milan.how/ |
| **Backend** | Python 3.12, FastAPI, uvicorn, sqlite3 (WAL), bcrypt, PyJWT, wsgidav |
| **Frontend** | Single-File-SPA (`multikanban-server.html`), JWT in sessionStorage/localStorage |
| **Infrastruktur** | Docker Compose, nginx-Reverse-Proxy, NUC-Home-Server |
| **OnlyOffice** | https://onlo.milan.how/ — docx/xlsx/pptx/odt editierbar |
| **Excalidraw** | https://cali.milan.how/ — iframe-Overlay für .excalidraw-Dateikarten |
| **Lokal** | `cd server && .venv/bin/uvicorn main:app --port 8000 --reload` → http://localhost:8000 |
| **Deploy** | `bash deploy.sh` oder manuell: cp + tar + scp + ssh (Ablauf in DEPLOY.md) |

---

## Phasenplan

| Phase | Inhalt | Status |
|-------|--------|--------|
| 0–5 | Backend-Fundament, API, Frontend, SSE, Hardening, Persons/Snapshots | ✅ |
| 6 | PDF-Vollbild-Viewer, Hover-Preview, Syntax-Highlighting | ✅ |
| 7 | Geschachtelte Karten: Unterkarten + Dateikarten | ✅ |
| 8 | OnlyOffice-Integration (docx/xlsx/pptx/odt) | ✅ |
| 9 | Karten-Modi + Timer + Anwesenheit | ✅ |
| 10 | Cover-Repositionierung, Optik-Menü | ✅ |
| 11 | Assignees, Mitglieder-Dialog, Board-Optik per User, Formatpinsel | ✅ |
| 12 | Kalender, SQLite-Backup, Drag-Reorder, Tabellen-Layout | ✅ |
| 13 | Desktop-Bridge: WebDAV `/dav/` | ✅ |
| 14 | Hilfe-System (9 Kapitel + Shortcuts) | ✅ |
| 15 | Excalidraw-Integration | ✅ |
| 16 | Board-DB: CSV-Import, SQL-Abfragen, Template/Seriendruck | ⚠️ in Arbeit |
| 17 | MD-Dateikarten-Editor (`.md`-Anhang direkt editierbar) | ✅ |
| 18 | Grafischer Planer, Multi-Select-Färbung, Spaltenhintergründe | ✅ |
| 19 | Mail-System, Inter-Board-Links, Planer-Ownership + Freiraum | ✅ |
| 20 | Planer-Fixes: Z-Order-Persistenz, Monatsrand | ✅ |
| 21 | Galerie-Vollbild: Cropper.js-Bildeditor | ✅ |
| 22 | OO-Verwerfen-Fix, Level-III-Unterkarten, Shift+Klick-Chip | ✅ |
| 23 | Board-ZIP-Export, Wissenskarten-Dreieck, Nutzerrollen-Hilfe | ✅ |
| 24 | Nur-Spalten-Zugriff (col_id pro Mitglied) | ✅ |
| 25 | Pipeline-Flicker-Fix, 7z-Extraktion | ✅ |
| 26 | Dateimodal-Inline-Editor (TXT/MD), MD-Vorschau-Fixes | ✅ |
| 27 | Galerie-MD WYSIWYG + Auto-Save, marked.js-Links | ✅ 2026-06-11 |
| 28 | Git eingerichtet, Reorder Eb. II/III fix, Galerie-Filter, Code-Review | ✅ 2026-06-13 |
| 29 | Dok-Modus: DOCX-Vorlage in OO, Platzhalter-Generierung, Seriendokumente (docs.py) | ✅ 2026-06-14 |
| 30 | doclink_card (zentrales Dok-Hub), Labels+Assignees in mMetaRow, Enter=Vollbild | ✅ 2026-06-14 |
| 31 | Unterkarten-Chips Badge-Spalten, Filter Tiefe, Badge-Farben (Admin), Board-Tab-Chrome aus DB, Alt+Klick Vollbild, Datei-Chips 22px, Dok/Wissen-Indikatoren Mini-Card | ✅ 2026-06-15 |

---

## Features

### Board & Struktur
- Mehrere Boards, Tab-Leiste mit Drag-Reorder und Parking Zone
- Spalten + Swimlanes; Spaltenhintergründe; Board-Optik pro Nutzer
- Kalender-Ansicht (Karten mit Fälligkeitsdatum), grafischer Tagesplaner mit Freiräumen

### Karten
- **6 Modi:** Org · Wissen (▟) · Schüler · Monster · Mail · Dok (rotes Dreieck)
- **3-Ebenen-Hierarchie:** Karte → Unterkarte → Unter-Unterkarte; Dateikarte an jeder Tiefe
- Labels + Assignees (Avatar-Badges, editierbare Farbe im Admin) immer sichtbar in mMetaRow
- Felder je nach Modus: Fälligkeitsdatum, Timer, Anwesenheit, Punkte, Assignees, Person-Kürzel
- Tabellenfeld: Pipeline (Dreizustand) oder freie Tabelle, Zeilen/Spalten-Drag-Reorder
- Undo-Stack, Formatpinsel, Volltextsuche, Drag-Kopieren (flach + tief)
- **Alt+Klick** auf Karte / Mini-Karte / Datei-Chip → Modal direkt im Vollbild

### Anhänge & Medien
- Beliebige Datei-Uploads; Titelbild mit Crop/Reposition
- PDF-Vollbild-Viewer, Video-Player (mp4/webm/mov, Range-Request-Streaming)
- Bildeditor (Crop, Rotate) im Galerie-Vollbild; Zoom per Scroll
- OnlyOffice: docx/xlsx/pptx/odt direkt im Browser bearbeiten
- Galerie-Filter (Bilder/PDF/Text/Office/Andere) + Navigation (← → und ⏮⏭/Tab, beide filterabhängig)
- WebDAV-Mount (`gio mount davs://mkan.milan.how/dav/`) für Desktop-Dateizugriff

### Zusammenarbeit
- Echtzeit-Sync via SSE; Assignees + Person-Roster; Inter-Board-Links
- Mail-System: Einladung, URL-Reminder, Passwort-Reset (SMTP)
- Snapshots + Klassenbuch; SQLite-Backup/Restore; Board-ZIP-Download

### Serienfunktionen
- **Serienmail** (✉ Mail-Template-Karte): Board-DB als Datenquelle → `{{feld}}`-Platzhalter → Massen-Versand per SMTP
- **Seriendokumente** (Dok-Modus + ▤ Dok-Template-Karte): DOCX-Vorlage in OO bearbeiten → python-docx befüllt → Ausgabe-Dateikarten
- **Board-DB**: Tabellen-Karten (CSV-Import), Abfrage-Karten (SQL), MD-Template-Karten

### Werkzeuge
- Excalidraw-Integration (iframe + postMessage-Bridge)
- Admin-Panel: Nutzer (Badge-Farben editierbar), E-Mail-Templates, Board-Delete, DB-Export

---

## Nutzerrollen

| Rolle | Rechte |
|-------|--------|
| Viewer | Boards lesen |
| Editor | Karten anlegen und bearbeiten |
| Owner | + Mitglieder verwalten, Spalten-Zugriff zuweisen |
| Admin | systemweit: alle Boards, Nutzerverwaltung, Tokens |

**Nur-Spalten-Zugriff** (Owner-Einstellung pro Mitglied): sieht/bearbeitet nur eine Spalte; kein Verschieben, keine Board-Links, kein Snapshot; Löschen nur eigener Karten.

---

## Offene Punkte

### Serienfunktionen (Phase 29/30, weitgehend fertig)
Noch offen:
- Hilfe-Modal-Seite für Mail- und Dok-Serie: Schritt-für-Schritt-Anleitung mit Beispiel-Datenquelle
- Briefkopf-Grafik in DOCX (Anhang → base64 → `<img>`) — aktuell: Text-Platzhalter
- ODT-Einzeldateien als ZIP (aktuell: ein Dokument mit allen Datensätzen)

### Zurückgestellt
- **Rate-Limiting** (Phase 4): kleine private Gruppe, kein dringender Bedarf
- **Verschlüsselung**: Zero-Knowledge inkompatibel mit OO/DAV/Seriendruck; Optionen: Client-side AES-GCM, SQLCipher, Extra-Key beim Start

### Aufräumen
```bash
# Planka-Reste (sudo nötig)
sudo rm -rf /mnt/planka/{user-avatars,project-background-images,background-images,attachments}
rm ~/planka-backup-PROD-20260523-2040.sql
```
