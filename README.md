# mkan — Multi-Kanban Server

Self-hosted collaborative Kanban with deep card layering, file management,
serial mail/document generation, and a graphical day planner.
Built for small non-IT groups. Runs entirely in one browser tab.

**Live demo:** https://mkan.milan.how/

---

## Features

- Boards with columns and swimlanes; drag & drop
- Cards up to 3 levels deep (card → sub-card → sub-sub-card)
- File cards: upload, preview, edit (PDF, images, Office via OnlyOffice, Excalidraw)
- 6 card modes: Org · Knowledge · Student · Monster · Mail · Doc
- Serial mail and serial document generation from a board-internal database
- Graphical day planner with free-slot calculation
- Snapshots, class log, history panel
- Real-time sync via SSE; role-based access (owner/editor/viewer/col-scope)
- AGPL-3.0

---

## Local mode (no Docker)

Requires Python 3.10+. OnlyOffice not included — all other features work.

```bash
git clone https://github.com/mithubin/mkan.git
cd mkan
bash start-local.sh
```

Sets up a virtualenv on first run, installs dependencies, starts the server
on port 8000, and opens the browser automatically. Data is stored in
`server/data/` (gitignored).

Promote your first user to admin (see below), then reload.

---

## Quick start (local / simple)

**Requirements:** Docker, Docker Compose

```bash
git clone https://github.com/mithubin/mkan.git
cd mkan

cp .env.example .env
# Edit .env: set SECRET_KEY (openssl rand -hex 32)

mkdir -p data/db data/uploads
docker compose -f docker-compose.simple.yml up -d --build
```

mkan is now running at **http://localhost:8000**.

### First user & admin

Register via the login page (or via API):

```bash
curl -s -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"Your Name","email":"you@example.com","password":"yourpassword"}'
```

Promote to admin (required to create boards and invite users):

```bash
sqlite3 data/db/kanban.sqlite \
  "UPDATE users SET global_role='admin' WHERE email='you@example.com';"
```

Log in — the Admin panel is now visible in the user menu.

> Note: `/auth/register` is open by default. After creating your admin account
> you may want to firewall the endpoint or restrict registration to invite tokens
> (Admin panel → Invite tokens).

---

## Production setup (with nginx + SSL)

See [DEPLOY.md](DEPLOY.md) for a full Docker Compose setup with nginx reverse
proxy, SSL via certbot, and OnlyOffice integration.

The production `docker-compose.yml` expects an external Docker network called
`webproxy` and a reverse-proxying nginx container on that network.

---

## OnlyOffice (optional)

OnlyOffice enables in-browser editing of `.docx`, `.xlsx`, `.pptx` files and
powers the serial document generation feature. Without it, mkan works fully
except for those two functions.

Set `OO_URL`, `OO_MKAN_BASE`, and `OO_SECRET` in your `.env`. The included
`docker-compose.yml` starts an OnlyOffice container alongside mkan. You need
to provide two config files (`oo-local.json`, `oo-ds.conf`) — see DEPLOY.md
for details.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, uvicorn, SQLite (WAL) |
| Frontend | Single-file SPA — no build step, no framework |
| Auth | JWT (HS256), bcrypt |
| Storage | SQLite + local filesystem |
| Optional | OnlyOffice 9.x, Excalidraw |

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).
