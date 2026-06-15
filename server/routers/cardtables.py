import json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from db import get_conn, uid, now
from auth import current_user_id, require_board_access

router = APIRouter()


class TableIn(BaseModel):
    title: Optional[str] = None
    mode: str = 'table'
    col_labels: list = []
    row_labels: list = []
    cells: dict = {}


def _card_board(conn, card_id: str) -> str:
    row = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Karte nicht gefunden')
    return row['board_id']


def _row_to_dict(row) -> dict:
    return {
        'id': row['id'],
        'cardId': row['card_id'],
        'title': row['title'],
        'mode': row['mode'],
        'colLabels': json.loads(row['col_labels'] or '[]'),
        'rowLabels': json.loads(row['row_labels'] or '[]'),
        'cells': json.loads(row['cells'] or '{}'),
        'createdAt': row['created_at'],
    }


@router.get('/boards/{board_id}/tables')
def get_board_tables(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        rows = conn.execute(
            '''SELECT ct.* FROM card_tables ct
               JOIN cards c ON ct.card_id = c.id
               WHERE c.board_id = ?''', (board_id,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get('/cards/{card_id}/table')
def get_table(card_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        board_id = _card_board(conn, card_id)
        require_board_access(board_id, user_id, conn)
        row = conn.execute('SELECT * FROM card_tables WHERE card_id=?', (card_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Kein Tabellenfeld')
    return _row_to_dict(row)


@router.put('/cards/{card_id}/table')
def put_table(card_id: str, body: TableIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        board_id = _card_board(conn, card_id)
        require_board_access(board_id, user_id, conn)
        existing = conn.execute('SELECT id FROM card_tables WHERE card_id=?', (card_id,)).fetchone()
        if existing:
            conn.execute(
                'UPDATE card_tables SET title=?,mode=?,col_labels=?,row_labels=?,cells=? WHERE card_id=?',
                (body.title, body.mode,
                 json.dumps(body.col_labels), json.dumps(body.row_labels),
                 json.dumps(body.cells), card_id)
            )
            tid = existing['id']
        else:
            tid = uid()
            conn.execute(
                'INSERT INTO card_tables (id,card_id,title,mode,col_labels,row_labels,cells,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (tid, card_id, body.title, body.mode,
                 json.dumps(body.col_labels), json.dumps(body.row_labels),
                 json.dumps(body.cells), now())
            )
        row = conn.execute('SELECT * FROM card_tables WHERE id=?', (tid,)).fetchone()
    return _row_to_dict(row)


@router.delete('/cards/{card_id}/table', status_code=204)
def delete_table(card_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        board_id = _card_board(conn, card_id)
        require_board_access(board_id, user_id, conn)
        conn.execute('DELETE FROM card_tables WHERE card_id=?', (card_id,))
