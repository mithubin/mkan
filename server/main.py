import json
import os
import pathlib
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.wsgi import WSGIMiddleware

from db import get_conn, init_db
from routers import auth, boards, cards, columns, swimlanes, labels, attachments, events
from routers import persons, snapshots, klassenbuch, cardtables, onlyoffice, oo_proxy, admin, boarddb, fonts, mail, convert, adminpanel, planner, export, mail_composer, docs
from auth import current_user_id, promote_admin_on_startup
from routers.dav import get_dav_app

STATIC_DIR = pathlib.Path(__file__).parent / 'static'

UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')
os.makedirs(UPLOAD_PATH, exist_ok=True)

app = FastAPI(title='Multikanban', version='1.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
def startup():
    init_db()
    promote_admin_on_startup()


app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/')
def frontend():
    return FileResponse(STATIC_DIR / 'index.html', media_type='text/html',
                        headers={'Cache-Control': 'no-cache'})


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/theme')
def get_theme(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        row = conn.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
    try:
        prefs = json.loads(row['settings'] or '{}')
        t = prefs.get('theme')
        if not t:
            raise HTTPException(status_code=404)
        return t
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404)


@app.post('/theme', status_code=204)
async def post_theme(request: Request, user_id: str = Depends(current_user_id)):
    body = await request.json()
    with get_conn() as conn:
        row = conn.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
        prefs = {}
        try:
            prefs = json.loads(row['settings'] or '{}')
        except Exception:
            pass
        prefs['theme'] = body
        conn.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(prefs), user_id))


@app.get('/snapshots')
def get_snapshots(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        row = conn.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
    try:
        prefs = json.loads(row['settings'] or '{}')
        return prefs.get('snapshots') or []
    except Exception:
        return []


@app.post('/snapshots', status_code=204)
async def post_snapshots(request: Request, user_id: str = Depends(current_user_id)):
    body = await request.json()
    with get_conn() as conn:
        row = conn.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
        prefs = {}
        try:
            prefs = json.loads(row['settings'] or '{}')
        except Exception:
            pass
        prefs['snapshots'] = body
        conn.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(prefs), user_id))


app.include_router(auth.router,        prefix='/auth',        tags=['auth'])
app.include_router(boards.router,      prefix='/boards',      tags=['boards'])
app.include_router(cards.router,       prefix='/cards',       tags=['cards'])
app.include_router(columns.router,     prefix='/columns',     tags=['columns'])
app.include_router(swimlanes.router,   prefix='/swimlanes',   tags=['swimlanes'])
app.include_router(labels.router,      prefix='/labels',      tags=['labels'])
app.include_router(attachments.router, prefix='/attachments', tags=['attachments'])
app.include_router(events.router,      prefix='/boards',      tags=['events'])
app.include_router(persons.router,                            tags=['persons'])
app.include_router(snapshots.router,                          tags=['snapshots'])
app.include_router(klassenbuch.router,                        tags=['klassenbuch'])
app.include_router(cardtables.router,                         tags=['cardtables'])
app.include_router(onlyoffice.router,                         tags=['onlyoffice'])
app.include_router(oo_proxy.router,                           tags=['oo_proxy'])
app.include_router(admin.router,                              tags=['admin'])
app.include_router(boarddb.router,     prefix='/boards',      tags=['boarddb'])
app.include_router(fonts.router,                              tags=['fonts'])
app.include_router(mail.router,                               tags=['mail'])
app.include_router(convert.router,                            tags=['convert'])
app.include_router(adminpanel.router,                         tags=['adminpanel'])
app.include_router(planner.router,     prefix='/planner',     tags=['planner'])
app.include_router(export.router,                             tags=['export'])
app.include_router(mail_composer.router,                      tags=['mail_composer'])
app.include_router(docs.router,          prefix='/cards',      tags=['docs'])

from fastapi.responses import RedirectResponse

_dav_wsgi = WSGIMiddleware(get_dav_app())


@app.api_route('/dav', methods=['GET', 'HEAD', 'OPTIONS', 'PROPFIND', 'PROPPATCH',
                                 'MKCOL', 'PUT', 'DELETE', 'COPY', 'MOVE',
                                 'LOCK', 'UNLOCK'])
def dav_root_redirect(request: Request):
    return RedirectResponse(url='https://mkan.milan.how/dav/', status_code=301)


app.mount('/dav', _dav_wsgi)
