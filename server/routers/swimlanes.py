import os
import re
import tempfile
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access
from routers.events import broadcast

router = APIRouter()


class SwimlaneCreate(BaseModel):
    board_id: str
    title: str


class SwimlaneUpdate(BaseModel):
    title: str | None = None
    note: str | None = None
    position: int | None = None


@router.post('', status_code=201)
def create_swimlane(body: SwimlaneCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(body.board_id, user_id, conn)
        row = conn.execute(
            'SELECT COALESCE(MAX(position), -1) AS m FROM swimlanes WHERE board_id=?', (body.board_id,)
        ).fetchone()
        lane_id = uid()
        conn.execute(
            'INSERT INTO swimlanes (id, board_id, title, note, position) VALUES (?,?,?,?,?)',
            (lane_id, body.board_id, body.title.strip(), None, row['m'] + 1),
        )
        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), body.board_id))
    broadcast(body.board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': body.board_id})
    return {'id': lane_id}


@router.patch('/{lane_id}')
def update_swimlane(lane_id: str, body: SwimlaneUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        lane = conn.execute('SELECT * FROM swimlanes WHERE id=?', (lane_id,)).fetchone()
        if not lane:
            raise HTTPException(404, 'Swimlane not found')
        require_board_access(lane['board_id'], user_id, conn)
        _board_id = lane['board_id']

        if 'position' in provided and body.position is not None:
            old_pos = lane['position']
            new_pos = body.position
            board_id = lane['board_id']
            if new_pos < old_pos:
                conn.execute(
                    'UPDATE swimlanes SET position=position+1 WHERE board_id=? AND position>=? AND position<? AND id!=?',
                    (board_id, new_pos, old_pos, lane_id),
                )
            elif new_pos > old_pos:
                conn.execute(
                    'UPDATE swimlanes SET position=position-1 WHERE board_id=? AND position>? AND position<=? AND id!=?',
                    (board_id, old_pos, new_pos, lane_id),
                )
            conn.execute('UPDATE swimlanes SET position=? WHERE id=?', (new_pos, lane_id))

        if 'title' in provided and body.title is not None:
            conn.execute('UPDATE swimlanes SET title=? WHERE id=?', (body.title.strip(), lane_id))
        if 'note' in provided:
            conn.execute('UPDATE swimlanes SET note=? WHERE id=?', (body.note, lane_id))

        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), lane['board_id']))
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}


@router.get('/{lane_id}/attachments.zip')
def download_lane_attachments(
    lane_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(current_user_id),
):
    with get_conn() as conn:
        lane = conn.execute('SELECT * FROM swimlanes WHERE id=?', (lane_id,)).fetchone()
        if not lane:
            raise HTTPException(404, 'Swimlane not found')
        require_board_access(lane['board_id'], user_id, conn, min_role='viewer')
        cards = conn.execute('SELECT id, title FROM cards WHERE lane_id=?', (lane_id,)).fetchall()
        card_map = {c['id']: c['title'] for c in cards}
        # Pull all fields into plain dicts while connection is open
        atts: list[tuple[str, dict]] = []
        for cid in card_map:
            for r in conn.execute('SELECT id, filename, path FROM attachments WHERE card_id=?', (cid,)).fetchall():
                atts.append((cid, {'filename': r['filename'], 'path': r['path']}))

    tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    try:
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            seen: dict[str, int] = {}
            for cid, att in atts:
                if not os.path.exists(att['path']):
                    continue
                folder = re.sub(r'[^\w\-]', '_', card_map.get(cid, cid))[:40]
                name = f"{folder}/{att['filename']}"
                if name in seen:
                    seen[name] += 1
                    base, ext = os.path.splitext(att['filename'])
                    name = f"{folder}/{base}_{seen[name]}{ext}"
                else:
                    seen[name] = 0
                zf.write(att['path'], name)
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise HTTPException(500, 'ZIP-Erstellung fehlgeschlagen')

    background_tasks.add_task(os.unlink, tmp.name)
    return FileResponse(tmp.name, media_type='application/zip', filename='attachments.zip')


@router.get('/{lane_id}/covers.zip')
def download_lane_covers(
    lane_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(current_user_id),
):
    with get_conn() as conn:
        lane = conn.execute('SELECT * FROM swimlanes WHERE id=?', (lane_id,)).fetchone()
        if not lane:
            raise HTTPException(404, 'Swimlane not found')
        require_board_access(lane['board_id'], user_id, conn, min_role='viewer')
        cards = [
            {'title': r['title'], 'cover_path': r['cover_path']}
            for r in conn.execute(
                'SELECT title, cover_path FROM cards WHERE lane_id=? AND cover_path IS NOT NULL',
                (lane_id,),
            ).fetchall()
        ]

    tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    try:
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for card in cards:
                if not card['cover_path'] or not os.path.exists(card['cover_path']):
                    continue
                safe = re.sub(r'[^\w\-]', '_', card['title'])[:50]
                ext = os.path.splitext(card['cover_path'])[1] or ''
                zf.write(card['cover_path'], f"{safe}{ext}")
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise HTTPException(500, 'ZIP-Erstellung fehlgeschlagen')

    background_tasks.add_task(os.unlink, tmp.name)
    return FileResponse(tmp.name, media_type='application/zip', filename='covers.zip')


@router.delete('/{lane_id}')
def delete_swimlane(lane_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        lane = conn.execute('SELECT * FROM swimlanes WHERE id=?', (lane_id,)).fetchone()
        if not lane:
            raise HTTPException(404, 'Swimlane not found')
        require_board_access(lane['board_id'], user_id, conn)
        _board_id = lane['board_id']
        if conn.execute(
            'SELECT COUNT(*) AS n FROM cards WHERE lane_id=?', (lane_id,)
        ).fetchone()['n'] > 0:
            raise HTTPException(400, 'Swimlane still contains cards — move or delete them first')
        conn.execute('DELETE FROM swimlanes WHERE id=?', (lane_id,))
        conn.execute(
            'UPDATE swimlanes SET position=position-1 WHERE board_id=? AND position>?',
            (lane['board_id'], lane['position']),
        )
        conn.execute('UPDATE boards SET updated_at=? WHERE id=?', (now(), lane['board_id']))
    broadcast(_board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': _board_id})
    return {'ok': True}
