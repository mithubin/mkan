import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid
from auth import current_user_id, require_board_access

router = APIRouter()


class KlassenbuchCreate(BaseModel):
    date: str
    title: str
    note: str | None = None
    stunden: list | None = None
    snapshot_id: str | None = None


class KlassenbuchUpdate(BaseModel):
    title: str | None = None
    note: str | None = None
    stunden: list | None = None
    snapshot_id: str | None = None


def _row_to_dict(r):
    d = {k: r[k] for k in ('id', 'date', 'title', 'note')}
    d['stunden'] = json.loads(r['stunden'])
    d['snapshotId'] = r['snapshot_id'] if 'snapshot_id' in r.keys() else None
    return d


@router.get('/boards/{board_id}/klassenbuch')
def list_klassenbuch(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        rows = conn.execute(
            'SELECT id, date, title, note, stunden, snapshot_id FROM klassenbuch WHERE board_id=? ORDER BY date DESC',
            (board_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post('/boards/{board_id}/klassenbuch', status_code=201)
def create_entry(board_id: str, body: KlassenbuchCreate, user_id: str = Depends(current_user_id)):
    entry_id = uid()
    stunden_json = json.dumps(body.stunden or [])
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        conn.execute(
            'INSERT INTO klassenbuch (id, board_id, date, title, note, stunden, snapshot_id) VALUES (?,?,?,?,?,?,?)',
            (entry_id, board_id, body.date, body.title.strip(), body.note, stunden_json, body.snapshot_id),
        )
    return {'id': entry_id}


@router.patch('/klassenbuch/{entry_id}')
def update_entry(entry_id: str, body: KlassenbuchUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    with get_conn() as conn:
        e = conn.execute('SELECT * FROM klassenbuch WHERE id=?', (entry_id,)).fetchone()
        if not e:
            raise HTTPException(404, 'Entry not found')
        require_board_access(e['board_id'], user_id, conn)
        updates: dict = {}
        if 'title' in provided and body.title is not None:
            updates['title'] = body.title.strip()
        if 'note' in provided:
            updates['note'] = body.note
        if 'stunden' in provided and body.stunden is not None:
            updates['stunden'] = json.dumps(body.stunden)
        if 'snapshot_id' in provided:
            updates['snapshot_id'] = body.snapshot_id
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE klassenbuch SET {set_clause} WHERE id=?', list(updates.values()) + [entry_id])
    return {'ok': True}


@router.delete('/klassenbuch/{entry_id}')
def delete_entry(entry_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        e = conn.execute('SELECT * FROM klassenbuch WHERE id=?', (entry_id,)).fetchone()
        if not e:
            raise HTTPException(404, 'Entry not found')
        require_board_access(e['board_id'], user_id, conn)
        conn.execute('DELETE FROM klassenbuch WHERE id=?', (entry_id,))
    return {'ok': True}
