from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid
from auth import current_user_id, require_board_access
from routers.events import broadcast

router = APIRouter()


class PersonCreate(BaseModel):
    code: str
    name: str | None = None


class PersonUpdate(BaseModel):
    code: str | None = None
    name: str | None = None


@router.get('/boards/{board_id}/persons')
def list_persons(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        rows = conn.execute(
            'SELECT id, code, name, position FROM persons WHERE board_id=? ORDER BY position',
            (board_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post('/boards/{board_id}/persons', status_code=201)
def create_person(board_id: str, body: PersonCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        row = conn.execute(
            'SELECT COALESCE(MAX(position), -1) AS m FROM persons WHERE board_id=?', (board_id,)
        ).fetchone()
        person_id = uid()
        conn.execute(
            'INSERT INTO persons (id, board_id, code, name, position) VALUES (?,?,?,?,?)',
            (person_id, board_id, body.code.strip(), body.name, row['m'] + 1),
        )
    broadcast(board_id, {'type': 'persons_updated', 'boardId': board_id})
    return {'id': person_id, 'code': body.code.strip(), 'name': body.name}


@router.patch('/persons/{person_id}')
def update_person(person_id: str, body: PersonUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        p = conn.execute('SELECT * FROM persons WHERE id=?', (person_id,)).fetchone()
        if not p:
            raise HTTPException(404, 'Person not found')
        require_board_access(p['board_id'], user_id, conn)
        _board_id = p['board_id']
        updates: dict = {}
        if 'code' in provided and body.code is not None:
            updates['code'] = body.code.strip()
        if 'name' in provided:
            updates['name'] = body.name
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE persons SET {set_clause} WHERE id=?', list(updates.values()) + [person_id])
    broadcast(_board_id, {'type': 'persons_updated', 'boardId': _board_id})
    return {'ok': True}


@router.delete('/persons/{person_id}')
def delete_person(person_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        p = conn.execute('SELECT * FROM persons WHERE id=?', (person_id,)).fetchone()
        if not p:
            raise HTTPException(404, 'Person not found')
        require_board_access(p['board_id'], user_id, conn)
        _board_id = p['board_id']
        conn.execute('UPDATE cards SET person_id=NULL WHERE person_id=?', (person_id,))
        conn.execute('DELETE FROM persons WHERE id=?', (person_id,))
    broadcast(_board_id, {'type': 'persons_updated', 'boardId': _board_id})
    return {'ok': True}
