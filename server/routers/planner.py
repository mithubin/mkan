import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from db import get_conn, uid, now
from auth import current_user_id

router = APIRouter()

UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')
_COVER_DIR = os.path.join(UPLOAD_PATH, 'planner_covers')
os.makedirs(_COVER_DIR, exist_ok=True)


class PlannerItemCreate(BaseModel):
    title: str = ''
    date: str
    time_start: str
    time_end: str
    color: Optional[str] = None
    bg_color: Optional[str] = None
    notes: Optional[str] = None
    is_freiraum: bool = False


class PlannerItemUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    color: Optional[str] = None
    bg_color: Optional[str] = None
    notes: Optional[str] = None
    is_freiraum: Optional[bool] = None
    cover_pos: Optional[str] = None


class SplitRequest(BaseModel):
    at: str  # HH:MM


def _hhmm_to_min(s):
    h, m = s.split(':')
    return int(h) * 60 + int(m)


def _row_to_dict(r):
    return {
        'id': r['id'],
        'user_id': r['user_id'],
        'title': r['title'],
        'date': r['date'],
        'time_start': r['time_start'],
        'time_end': r['time_end'],
        'color': r['color'],
        'notes': r['notes'],
        'cover_path': r['cover_path'] if 'cover_path' in r.keys() else None,
        'cover_pos': r['cover_pos'] if 'cover_pos' in r.keys() else None,
        'bg_color': r['bg_color'] if 'bg_color' in r.keys() else None,
        'is_freiraum': bool(r['is_freiraum']) if 'is_freiraum' in r.keys() else False,
        'updated_at': r['updated_at'],
        'user_name': r['user_name'] if 'user_name' in r.keys() else '',
        'updated_by_name': r['updated_by_name'] if 'updated_by_name' in r.keys() else '',
    }


@router.get('/items')
def get_items(
    date_from: str = Query(...),
    date_to: str = Query(...),
    user_id: str = Depends(current_user_id),
):
    with get_conn() as conn:
        rows = conn.execute(
            '''SELECT pi.id, pi.user_id, pi.title, pi.date, pi.time_start, pi.time_end,
                      pi.color, pi.bg_color, pi.is_freiraum, pi.notes, pi.cover_path, pi.updated_at,
                      u.name  AS user_name,
                      ub.name AS updated_by_name
               FROM planner_items pi
               LEFT JOIN users u  ON pi.user_id    = u.id
               LEFT JOIN users ub ON pi.updated_by  = ub.id
               WHERE pi.date >= ? AND pi.date <= ?
               ORDER BY pi.date, pi.time_start''',
            (date_from, date_to),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get('/due-dates')
def get_due_dates(
    date_from: str = Query(...),
    date_to: str = Query(...),
    user_id: str = Depends(current_user_id),
):
    with get_conn() as conn:
        rows = conn.execute(
            '''SELECT c.id, c.title, c.due_date, c.board_id, b.title AS board_title
               FROM cards c
               JOIN boards b       ON c.board_id  = b.id
               JOIN board_members bm ON bm.board_id = c.board_id AND bm.user_id = ?
               WHERE c.due_date >= ? AND c.due_date <= ?
                 AND c.due_date IS NOT NULL AND c.due_date != ''
               ORDER BY c.due_date''',
            (user_id, date_from, date_to),
        ).fetchall()
    return [
        {'id': r['id'], 'title': r['title'], 'dueDate': r['due_date'],
         'boardId': r['board_id'], 'boardTitle': r['board_title']}
        for r in rows
    ]


@router.post('/items', status_code=201)
def create_item(body: PlannerItemCreate, user_id: str = Depends(current_user_id)):
    item_id = uid()
    with get_conn() as conn:
        conn.execute(
            '''INSERT INTO planner_items
               (id, user_id, title, date, time_start, time_end, color, bg_color, notes, is_freiraum, updated_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (item_id, user_id, body.title.strip(), body.date,
             body.time_start, body.time_end, body.color, body.bg_color,
             body.notes, 1 if body.is_freiraum else 0,
             user_id, now(), now()),
        )
    return {'id': item_id}


@router.patch('/items/{item_id}')
def update_item(item_id: str, body: PlannerItemUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    with get_conn() as conn:
        item = conn.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if item['user_id'] != user_id:
            raise HTTPException(403, 'Nur eigene Einträge bearbeiten')
        sets, vals = ['updated_by=?', 'updated_at=?'], [user_id, now()]
        for field in ['title', 'date', 'time_start', 'time_end', 'color', 'bg_color', 'notes', 'is_freiraum', 'cover_pos']:
            if field in provided:
                v = getattr(body, field)
                sets.append(f'{field}=?')
                if field == 'is_freiraum':
                    vals.append(1 if v else 0)
                elif isinstance(v, str) and field == 'title':
                    vals.append(v.strip())
                else:
                    vals.append(v)
        vals.append(item_id)
        conn.execute(f'UPDATE planner_items SET {", ".join(sets)} WHERE id=?', vals)
    return {'ok': True}


@router.delete('/items/{item_id}')
def delete_item(item_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        item = conn.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if item['user_id'] != user_id:
            raise HTTPException(403, 'Nur eigene Einträge löschen')
        if item['cover_path'] and os.path.exists(item['cover_path']):
            os.remove(item['cover_path'])
        conn.execute('DELETE FROM planner_items WHERE id=?', (item_id,))
    return {'ok': True}


@router.post('/items/{item_id}/split', status_code=201)
def split_item(item_id: str, body: SplitRequest, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        item = conn.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if item['user_id'] != user_id:
            raise HTTPException(403, 'Nur eigene Einträge teilen')
        at_min    = _hhmm_to_min(body.at)
        start_min = _hhmm_to_min(item['time_start'])
        end_min   = _hhmm_to_min(item['time_end'])
        if not (start_min < at_min < end_min):
            raise HTTPException(400, 'Split time outside item range')
        new_id = uid()
        conn.execute(
            '''INSERT INTO planner_items
               (id, user_id, title, date, time_start, time_end, color, notes, updated_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (new_id, item['user_id'], item['title'], item['date'],
             body.at, item['time_end'], item['color'], item['notes'],
             user_id, now(), now()),
        )
        conn.execute(
            'UPDATE planner_items SET time_end=?, updated_by=?, updated_at=? WHERE id=?',
            (body.at, user_id, now(), item_id),
        )
    return {'id': new_id}


@router.post('/items/{item_id}/cover', status_code=201)
async def upload_cover(
    item_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
):
    with get_conn() as conn:
        item = conn.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if item['user_id'] != user_id:
            raise HTTPException(403, 'Nur eigene Einträge bearbeiten')
        if item['cover_path'] and os.path.exists(item['cover_path']):
            os.remove(item['cover_path'])
        ext = os.path.splitext(file.filename or '')[1].lower() or '.jpg'
        dest = os.path.join(_COVER_DIR, f'{item_id}{ext}')
        with open(dest, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        conn.execute(
            'UPDATE planner_items SET cover_path=?, updated_by=?, updated_at=? WHERE id=?',
            (dest, user_id, now(), item_id),
        )
    return {'ok': True}


@router.get('/items/{item_id}/cover')
def get_cover(item_id: str, token: str = Query(...)):
    from auth import _decode_token
    try:
        _decode_token(token)
    except Exception:
        raise HTTPException(401)
    with get_conn() as conn:
        item = conn.execute('SELECT cover_path FROM planner_items WHERE id=?', (item_id,)).fetchone()
    if not item or not item['cover_path'] or not os.path.exists(item['cover_path']):
        raise HTTPException(404)
    return FileResponse(item['cover_path'])


@router.delete('/items/{item_id}/cover')
def delete_cover(item_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        item = conn.execute('SELECT * FROM planner_items WHERE id=?', (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if item['user_id'] != user_id:
            raise HTTPException(403, 'Nur eigene Einträge bearbeiten')
        if item['cover_path'] and os.path.exists(item['cover_path']):
            os.remove(item['cover_path'])
        conn.execute(
            'UPDATE planner_items SET cover_path=NULL, updated_by=?, updated_at=? WHERE id=?',
            (user_id, now(), item_id),
        )
    return {'ok': True}
