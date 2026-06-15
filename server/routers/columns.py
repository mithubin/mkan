from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access
from routers.events import broadcast

router = APIRouter()


class ColumnCreate(BaseModel):
    board_id: str
    title: str


class ColumnUpdate(BaseModel):
    title: str | None = None
    position: int | None = None
    bg_color: str | None = None


@router.post('', status_code=201)
def create_column(body: ColumnCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(body.board_id, user_id, conn)
        row = conn.execute(
            'SELECT COALESCE(MAX(position), -1) AS m FROM columns WHERE board_id=?', (body.board_id,)
        ).fetchone()
        col_id = uid()
        conn.execute(
            'INSERT INTO columns (id, board_id, title, position) VALUES (?,?,?,?)',
            (col_id, body.board_id, body.title.strip(), row['m'] + 1),
        )
        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), body.board_id))
    broadcast(body.board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': body.board_id})
    return {'id': col_id}


@router.patch('/{col_id}')
def update_column(col_id: str, body: ColumnUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        col = conn.execute('SELECT * FROM columns WHERE id=?', (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, 'Column not found')
        require_board_access(col['board_id'], user_id, conn)
        _board_id = col['board_id']

        if 'position' in provided and body.position is not None:
            old_pos = col['position']
            new_pos = body.position
            board_id = col['board_id']
            if new_pos < old_pos:
                conn.execute(
                    'UPDATE columns SET position=position+1 WHERE board_id=? AND position>=? AND position<? AND id!=?',
                    (board_id, new_pos, old_pos, col_id),
                )
            elif new_pos > old_pos:
                conn.execute(
                    'UPDATE columns SET position=position-1 WHERE board_id=? AND position>? AND position<=? AND id!=?',
                    (board_id, old_pos, new_pos, col_id),
                )
            conn.execute('UPDATE columns SET position=? WHERE id=?', (new_pos, col_id))

        if 'title' in provided and body.title is not None:
            conn.execute('UPDATE columns SET title=? WHERE id=?', (body.title.strip(), col_id))

        if 'bg_color' in provided:
            conn.execute('UPDATE columns SET bg_color=? WHERE id=?', (body.bg_color, col_id))

        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), col['board_id']))
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}


@router.delete('/{col_id}')
def delete_column(col_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        col = conn.execute('SELECT * FROM columns WHERE id=?', (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, 'Column not found')
        require_board_access(col['board_id'], user_id, conn)
        _board_id = col['board_id']
        if conn.execute(
            'SELECT COUNT(*) AS n FROM cards WHERE col_id=?', (col_id,)
        ).fetchone()['n'] > 0:
            raise HTTPException(400, 'Column still contains cards — move or delete them first')
        conn.execute('DELETE FROM columns WHERE id=?', (col_id,))
        conn.execute(
            'UPDATE columns SET position=position-1 WHERE board_id=? AND position>?',
            (col['board_id'], col['position']),
        )
        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), col['board_id']))
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}
