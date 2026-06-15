from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access
from routers.events import broadcast

router = APIRouter()


class LabelCreate(BaseModel):
    board_id: str
    text: str
    color: str = '#888888'


class LabelUpdate(BaseModel):
    text: str | None = None
    color: str | None = None


@router.post('', status_code=201)
def create_label(body: LabelCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(body.board_id, user_id, conn)
        lbl_id = uid()
        conn.execute(
            'INSERT INTO labels (id, board_id, text, color) VALUES (?,?,?,?)',
            (lbl_id, body.board_id, body.text.strip(), body.color),
        )
    broadcast(body.board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': body.board_id})
    return {'id': lbl_id}


@router.patch('/{label_id}')
def update_label(label_id: str, body: LabelUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        lbl = conn.execute('SELECT * FROM labels WHERE id=?', (label_id,)).fetchone()
        if not lbl:
            raise HTTPException(404, 'Label not found')
        require_board_access(lbl['board_id'], user_id, conn)
        _board_id = lbl['board_id']
        updates: dict = {}
        if 'text' in provided and body.text is not None:
            updates['text'] = body.text.strip()
        if 'color' in provided and body.color is not None:
            updates['color'] = body.color
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE labels SET {set_clause} WHERE id=?', list(updates.values()) + [label_id])
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}


@router.delete('/{label_id}')
def delete_label(label_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        lbl = conn.execute('SELECT * FROM labels WHERE id=?', (label_id,)).fetchone()
        if not lbl:
            raise HTTPException(404, 'Label not found')
        require_board_access(lbl['board_id'], user_id, conn)
        _board_id = lbl['board_id']
        conn.execute('DELETE FROM labels WHERE id=?', (label_id,))
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}


# Label-Zuweisung an Karten: in cards.py unter /cards/{id}/labels/{lid}
