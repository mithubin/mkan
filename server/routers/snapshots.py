import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access, get_col_scope
from routers.events import broadcast

router = APIRouter()


class SnapshotCreate(BaseModel):
    note: str | None = None


class SnapshotUpdate(BaseModel):
    ts: str | None = None
    note: str | None = None


class SnapshotCardUpdate(BaseModel):
    personCode: str | None = None
    colTitle: str | None = None
    laneTitle: str | None = None
    points: int | None = None
    pointsMax: int | None = None
    cardNotes: str | None = None


def _freeze_board(conn, board_id: str, snap_id: str):
    cols = {r['id']: r['title'] for r in conn.execute(
        'SELECT id, title FROM columns WHERE board_id=?', (board_id,)
    ).fetchall()}
    lanes = {r['id']: r['title'] for r in conn.execute(
        'SELECT id, title FROM swimlanes WHERE board_id=?', (board_id,)
    ).fetchall()}

    for c in conn.execute('SELECT * FROM cards WHERE board_id=?', (board_id,)).fetchall():
        person_code = None
        if c['person_id']:
            p = conn.execute('SELECT code FROM persons WHERE id=?', (c['person_id'],)).fetchone()
            person_code = p['code'] if p else None

        subtasks = conn.execute(
            'SELECT text, done FROM subtasks WHERE card_id=? ORDER BY position', (c['id'],)
        ).fetchall()
        subtasks_done = sum(1 for s in subtasks if s['done'])

        label_ids = [r['label_id'] for r in conn.execute(
            'SELECT label_id FROM card_labels WHERE card_id=?', (c['id'],)
        ).fetchall()]

        att_names = [r['filename'] for r in conn.execute(
            'SELECT filename FROM attachments WHERE card_id=?', (c['id'],)
        ).fetchall()]

        assignee_names = json.dumps([r['name'] for r in conn.execute(
            '''SELECT u.name FROM card_assignees ca JOIN users u ON ca.user_id=u.id
               WHERE ca.card_id=?''',
            (c['id'],),
        ).fetchall()])

        ck = c.keys()
        conn.execute(
            '''INSERT INTO snapshot_cards
               (id, snapshot_id, card_id, person_id, person_code, title,
                col_id, col_title, lane_id, lane_title, color, bg_color,
                label_ids, points, points_max, subtasks_done, subtasks_total,
                subtasks, attachments, card_notes,
                card_mode, due_date, time_spent, attendance_n, attendance_data,
                assignee_names)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                uid(), snap_id, c['id'], c['person_id'], person_code, c['title'],
                c['col_id'], cols.get(c['col_id']),
                c['lane_id'], lanes.get(c['lane_id']),
                c['color'], c['bg_color'],
                json.dumps(label_ids),
                c['points'], c['points_max'],
                subtasks_done, len(subtasks),
                json.dumps([{'text': s['text'], 'done': bool(s['done'])} for s in subtasks]),
                json.dumps(att_names),
                c['notes'],
                c['card_mode'] if 'card_mode' in ck else 'org',
                c['due_date'] if 'due_date' in ck else None,
                c['time_spent'] if 'time_spent' in ck else 0,
                c['attendance_n'] if 'attendance_n' in ck else None,
                c['attendance_data'] if 'attendance_data' in ck else None,
                assignee_names,
            ),
        )


# ── Snapshots ─────────────────────────────────────────────────────────────────

@router.get('/boards/{board_id}/snapshots')
def list_snapshots(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        rows = conn.execute(
            'SELECT id, ts, note FROM snapshots WHERE board_id=? ORDER BY ts DESC',
            (board_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post('/boards/{board_id}/snapshots', status_code=201)
def create_snapshot(board_id: str, body: SnapshotCreate, user_id: str = Depends(current_user_id)):
    snap_id = uid()
    ts = now()
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        if get_col_scope(user_id, board_id, conn):
            raise HTTPException(403, 'Snapshots nicht verfügbar')
        conn.execute(
            'INSERT INTO snapshots (id, board_id, ts, note) VALUES (?,?,?,?)',
            (snap_id, board_id, ts, body.note),
        )
        _freeze_board(conn, board_id, snap_id)
    broadcast(board_id, {'type': 'snapshot_created', 'boardId': board_id, 'snapshotId': snap_id})
    return {'id': snap_id, 'ts': ts}


@router.get('/snapshots/{snapshot_id}')
def get_snapshot(snapshot_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        snap = conn.execute('SELECT * FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not snap:
            raise HTTPException(404, 'Snapshot not found')
        require_board_access(snap['board_id'], user_id, conn, min_role='viewer')
        sc_rows = conn.execute(
            'SELECT * FROM snapshot_cards WHERE snapshot_id=?', (snapshot_id,)
        ).fetchall()

    def _parse(r):
        d = dict(r)
        d['labelIds'] = json.loads(d.pop('label_ids') or '[]')
        d['subtasks'] = json.loads(d.get('subtasks') or '[]')
        d['attachments'] = json.loads(d.get('attachments') or '[]')
        return d

    return {
        'id': snap['id'],
        'boardId': snap['board_id'],
        'ts': snap['ts'],
        'note': snap['note'],
        'cards': [_parse(r) for r in sc_rows],
    }


@router.patch('/snapshots/{snapshot_id}')
def update_snapshot(snapshot_id: str, body: SnapshotUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        snap = conn.execute('SELECT * FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not snap:
            raise HTTPException(404, 'Snapshot not found')
        require_board_access(snap['board_id'], user_id, conn)
        _board_id = snap['board_id']
        updates: dict = {}
        if 'ts' in provided and body.ts is not None:
            updates['ts'] = body.ts
        if 'note' in provided:
            updates['note'] = body.note
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE snapshots SET {set_clause} WHERE id=?', list(updates.values()) + [snapshot_id])
    return {'ok': True}


@router.delete('/snapshots/{snapshot_id}')
def delete_snapshot(snapshot_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        snap = conn.execute('SELECT * FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not snap:
            raise HTTPException(404, 'Snapshot not found')
        require_board_access(snap['board_id'], user_id, conn)
        conn.execute('DELETE FROM snapshots WHERE id=?', (snapshot_id,))
    return {'ok': True}


# ── Snapshot-Card nachträgliche Korrektur ─────────────────────────────────────

@router.patch('/snapshot-cards/{sc_id}')
def update_snapshot_card(sc_id: str, body: SnapshotCardUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    with get_conn() as conn:
        sc = conn.execute(
            '''SELECT sc.*, s.board_id FROM snapshot_cards sc
               JOIN snapshots s ON sc.snapshot_id=s.id WHERE sc.id=?''',
            (sc_id,),
        ).fetchone()
        if not sc:
            raise HTTPException(404, 'Snapshot card not found')
        require_board_access(sc['board_id'], user_id, conn)
        updates: dict = {}
        if 'personCode' in provided:
            updates['person_code'] = body.personCode
        if 'colTitle' in provided:
            updates['col_title'] = body.colTitle
        if 'laneTitle' in provided:
            updates['lane_title'] = body.laneTitle
        if 'points' in provided and body.points is not None:
            updates['points'] = body.points
        if 'pointsMax' in provided:
            updates['points_max'] = body.pointsMax
        if 'cardNotes' in provided:
            updates['card_notes'] = body.cardNotes
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE snapshot_cards SET {set_clause} WHERE id=?', list(updates.values()) + [sc_id])
    return {'ok': True}
