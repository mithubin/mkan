from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from auth import current_user_id
from db import get_conn, uid

router = APIRouter()

MAX_FONTS = 12
MAX_SIZE  = 4 * 1024 * 1024  # 4 MB

_ALLOWED_EXT  = {'.ttf', '.otf', '.woff', '.woff2'}
_MIME_MAP     = {'ttf': 'font/ttf', 'otf': 'font/otf', 'woff': 'font/woff', 'woff2': 'font/woff2'}


@router.get('/fonts')
def list_fonts(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, name, mimetype, length(data) AS size FROM fonts ORDER BY name'
        ).fetchall()
    return [{'id': r['id'], 'name': r['name'], 'mimetype': r['mimetype'], 'size': r['size']} for r in rows]


@router.post('/fonts', status_code=201)
async def upload_font(
    name: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
):
    fn = (file.filename or '').lower()
    ext = '.' + fn.rsplit('.', 1)[-1] if '.' in fn else ''
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f'Nur {", ".join(_ALLOWED_EXT)} erlaubt')
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, f'Datei zu groß (max {MAX_SIZE // 1024 // 1024} MB)')
    mimetype = _MIME_MAP.get(ext.lstrip('.'), 'font/ttf')
    name = name.strip()
    if not name:
        raise HTTPException(400, 'Name darf nicht leer sein')
    with get_conn() as conn:
        count = conn.execute('SELECT COUNT(*) AS n FROM fonts').fetchone()['n']
        if count >= MAX_FONTS:
            raise HTTPException(400, f'Maximum von {MAX_FONTS} Schriftarten erreicht')
        font_id = uid()
        conn.execute(
            'INSERT INTO fonts (id, name, mimetype, data) VALUES (?, ?, ?, ?)',
            (font_id, name, mimetype, data),
        )
    return {'id': font_id, 'name': name, 'mimetype': mimetype, 'size': len(data)}


@router.get('/fonts/{font_id}/file')
def serve_font(font_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        row = conn.execute('SELECT name, mimetype, data FROM fonts WHERE id=?', (font_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Schriftart nicht gefunden')
    return Response(content=bytes(row['data']), media_type=row['mimetype'],
                    headers={'Cache-Control': 'public, max-age=86400'})


@router.delete('/fonts/{font_id}', status_code=204)
def delete_font(font_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        conn.execute('DELETE FROM fonts WHERE id=?', (font_id,))
