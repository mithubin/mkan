import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access, get_col_scope
from routers.events import broadcast

UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')

router = APIRouter()


class CardCreate(BaseModel):
    board_id: str
    col_id: str | None = None
    lane_id: str | None = None
    title: str
    parentCardId: str | None = None
    cardType: str = 'card'


class CardUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    color: str | None = None
    bgColor: str | None = None
    points: int | None = None
    pointsMax: int | None = None
    colId: str | None = None
    laneId: str | None = None
    position: int | None = None
    linkedCards: list[str] | None = None
    personId: str | None = None
    parentCardId: str | None = None
    cardType: str | None = None
    cardMode: str | None = None
    dueDate: str | None = None
    timeSpent: int | None = None
    attendanceN: int | None = None
    attendanceData: str | None = None
    createdAt: str | None = None
    coverPos: str | None = None
    assigneeIds: list[str] | None = None
    cardSettings: str | None = None


class SubtaskCreate(BaseModel):
    text: str


class SubtaskUpdate(BaseModel):
    text: str | None = None
    done: bool | None = None
    position: int | None = None


# ── Cards ─────────────────────────────────────────────────────────────────────

@router.post('', status_code=201)
def create_card(body: CardCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(body.board_id, user_id, conn)
        my_col = get_col_scope(user_id, body.board_id, conn)

        col_id = body.col_id
        lane_id = body.lane_id
        parent_id = body.parentCardId

        if parent_id:
            parent = conn.execute('SELECT * FROM cards WHERE id=?', (parent_id,)).fetchone()
            if not parent:
                raise HTTPException(404, 'Parent card not found')
            col_id = parent['col_id']
            lane_id = parent['lane_id']
            # Depth check: max 3 levels
            depth = 2
            cur = parent
            while cur['parent_card_id']:
                depth += 1
                if depth > 3 and body.cardType != 'file_card':
                    raise HTTPException(400, 'Max. Kartentiefe (3 Ebenen) erreicht')
                cur = conn.execute('SELECT * FROM cards WHERE id=?', (cur['parent_card_id'],)).fetchone()
            row = conn.execute(
                'SELECT COALESCE(MAX(position), -1) AS m FROM cards WHERE parent_card_id=?',
                (parent_id,),
            ).fetchone()
        else:
            if not col_id or not lane_id:
                raise HTTPException(400, 'col_id und lane_id erforderlich für Top-Level-Karten')
            if my_col and col_id != my_col:
                raise HTTPException(403, 'Nur Zugriff auf zugewiesene Spalte')
            row = conn.execute(
                'SELECT COALESCE(MAX(position), -1) AS m FROM cards WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL',
                (body.board_id, col_id, lane_id),
            ).fetchone()

        pos = row['m'] + 1
        card_id = uid()
        ts = now()
        conn.execute(
            """INSERT INTO cards
               (id, board_id, col_id, lane_id, position, title, notes,
                color, bg_color, cover_path, points, points_max,
                card_type, parent_card_id, card_mode, time_spent, created_at, updated_at, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (card_id, body.board_id, col_id, lane_id, pos,
             body.title.strip(), '', None, None, None, 0, None,
             body.cardType, parent_id, 'org', 0, ts, ts, user_id),
        )
    broadcast(body.board_id, {
        'type': 'card_created', 'userId': user_id, 'boardId': body.board_id,
        'id': card_id, 'title': body.title.strip(),
        'colId': col_id, 'laneId': lane_id,
        'color': None, 'bgColor': None, 'coverImage': None, 'notes': '',
        'subtasks': [], 'attachments': [], 'labelIds': [],
        'points': 0, 'pointsMax': None, 'createdAt': ts,
        'cardType': body.cardType, 'parentCardId': parent_id,
    })
    return {'id': card_id, 'position': pos, 'createdAt': ts}


@router.get('/{card_id}')
def get_card(card_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        c = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn, min_role='viewer')

        subtasks = [
            {'id': s['id'], 'text': s['text'], 'done': bool(s['done'])}
            for s in conn.execute(
                'SELECT id, text, done FROM subtasks WHERE card_id=? ORDER BY position', (card_id,)
            ).fetchall()
        ]
        label_ids = [
            r['label_id']
            for r in conn.execute('SELECT label_id FROM card_labels WHERE card_id=?', (card_id,)).fetchall()
        ]
        atts = [
            {'id': a['id'], 'name': a['filename'], 'size': a['size'], 'type': a['mime'],
             'url': f'/attachments/{a["id"]}/file', 'position': a['position'],
             'createdAt': a['created_at']}
            for a in conn.execute(
                'SELECT * FROM attachments WHERE card_id=? ORDER BY position, created_at',
                (card_id,),
            ).fetchall()
        ]
        assignees = [
            {'id': r['id'], 'name': r['name']}
            for r in conn.execute(
                '''SELECT u.id, u.name FROM card_assignees ca
                   JOIN users u ON ca.user_id=u.id WHERE ca.card_id=?''',
                (card_id,),
            ).fetchall()
        ]

    keys = c.keys()
    return {
        'id': c['id'], 'title': c['title'], 'notes': c['notes'] or '',
        'color': c['color'], 'bgColor': c['bg_color'],
        'coverImage': f'/attachments/cover/{card_id}' if c['cover_path'] else None,
        'subtasks': subtasks, 'attachments': atts, 'linkedCards': [],
        'labelIds': label_ids, 'assignees': assignees,
        'personId': c['person_id'] if 'person_id' in keys else None,
        'points': c['points'], 'pointsMax': c['points_max'],
        'colId': c['col_id'], 'laneId': c['lane_id'], 'createdAt': c['created_at'],
        'cardType': c['card_type'] if 'card_type' in keys else 'card',
        'parentCardId': c['parent_card_id'] if 'parent_card_id' in keys else None,
        'cardMode': c['card_mode'] if 'card_mode' in keys else 'org',
        'dueDate': c['due_date'] if 'due_date' in keys else None,
        'timeSpent': c['time_spent'] if 'time_spent' in keys else 0,
        'attendanceN': c['attendance_n'] if 'attendance_n' in keys else None,
        'attendanceData': c['attendance_data'] if 'attendance_data' in keys else None,
        'coverPos': c['cover_pos'] if 'cover_pos' in keys else None,
        'cardSettings': c['card_settings'] if 'card_settings' in keys else None,
        'updatedAt': c['updated_at'],
    }


@router.patch('/{card_id}')
def update_card(card_id: str, body: CardUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = None
    with get_conn() as conn:
        c = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn)
        _board_id = c['board_id']
        my_col = get_col_scope(user_id, c['board_id'], conn)
        if my_col:
            if 'colId' in body.model_fields_set and body.colId and body.colId != my_col:
                raise HTTPException(403, 'Karte verschieben nicht erlaubt')
            if 'laneId' in body.model_fields_set and body.laneId and body.laneId != c['lane_id']:
                raise HTTPException(403, 'Karte verschieben nicht erlaubt')

        updates: dict = {}

        if 'title' in provided and body.title is not None:
            updates['title'] = body.title.strip()
        if 'notes' in provided:
            updates['notes'] = body.notes
        if 'color' in provided:
            updates['color'] = body.color or None
        if 'bgColor' in provided:
            updates['bg_color'] = body.bgColor or None
        if 'points' in provided and body.points is not None:
            updates['points'] = body.points
        if 'pointsMax' in provided:
            updates['points_max'] = body.pointsMax
        if 'personId' in provided:
            updates['person_id'] = body.personId or None

        if 'cardType' in provided and body.cardType is not None:
            updates['card_type'] = body.cardType
        if 'cardMode' in provided and body.cardMode is not None:
            updates['card_mode'] = body.cardMode
        if 'dueDate' in provided:
            updates['due_date'] = body.dueDate or None
        if 'timeSpent' in provided and body.timeSpent is not None:
            updates['time_spent'] = body.timeSpent
        if 'attendanceN' in provided:
            updates['attendance_n'] = body.attendanceN
        if 'attendanceData' in provided:
            updates['attendance_data'] = body.attendanceData
        if 'createdAt' in provided and body.createdAt is not None:
            updates['created_at'] = body.createdAt
        if 'coverPos' in provided:
            updates['cover_pos'] = body.coverPos or None
        if 'cardSettings' in provided:
            updates['card_settings'] = body.cardSettings
        if 'assigneeIds' in provided and body.assigneeIds is not None:
            conn.execute('DELETE FROM card_assignees WHERE card_id=?', (card_id,))
            for uid_val in body.assigneeIds:
                conn.execute(
                    'INSERT OR IGNORE INTO card_assignees (card_id, user_id) VALUES (?,?)',
                    (card_id, uid_val),
                )

        reparenting = False
        if 'parentCardId' in provided:
            old_parent = c['parent_card_id'] if 'parent_card_id' in c.keys() else None
            new_parent = body.parentCardId
            if new_parent != old_parent:
                reparenting = True
                updates['parent_card_id'] = new_parent
                if old_parent:
                    conn.execute(
                        'UPDATE cards SET position=position-1 WHERE parent_card_id=? AND position>?',
                        (old_parent, c['position']),
                    )
                else:
                    conn.execute(
                        'UPDATE cards SET position=position-1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>?',
                        (c['board_id'], c['col_id'], c['lane_id'], c['position']),
                    )
                _nc = body.colId if 'colId' in provided and body.colId else c['col_id']
                _nl = body.laneId if 'laneId' in provided and body.laneId else c['lane_id']
                updates['col_id'] = _nc
                updates['lane_id'] = _nl
                if new_parent:
                    m = conn.execute(
                        'SELECT COALESCE(MAX(position),-1) AS m FROM cards WHERE parent_card_id=? AND id!=?',
                        (new_parent, card_id),
                    ).fetchone()['m']
                else:
                    m = conn.execute(
                        'SELECT COALESCE(MAX(position),-1) AS m FROM cards WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND id!=?',
                        (c['board_id'], _nc, _nl, card_id),
                    ).fetchone()['m']
                updates['position'] = m + 1

        moving = not reparenting and ('colId' in provided or 'laneId' in provided or 'position' in provided)
        if moving:
            new_col  = body.colId     if body.colId     is not None else c['col_id']
            new_lane = body.laneId    if body.laneId    is not None else c['lane_id']
            new_pos  = body.position  if body.position  is not None else 0
            old_col, old_lane, old_pos = c['col_id'], c['lane_id'], c['position']
            board_id = c['board_id']
            is_subcard = bool(c['parent_card_id'] if 'parent_card_id' in c.keys() else None)

            if is_subcard:
                parent_id = c['parent_card_id']
                if new_pos < old_pos:
                    conn.execute(
                        'UPDATE cards SET position=position+1 WHERE parent_card_id=? AND position>=? AND position<? AND id!=?',
                        (parent_id, new_pos, old_pos, card_id),
                    )
                elif new_pos > old_pos:
                    conn.execute(
                        'UPDATE cards SET position=position-1 WHERE parent_card_id=? AND position>? AND position<=? AND id!=?',
                        (parent_id, old_pos, new_pos, card_id),
                    )
            else:
                if new_col != old_col or new_lane != old_lane:
                    conn.execute(
                        'UPDATE cards SET position=position-1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>? AND id!=?',
                        (board_id, old_col, old_lane, old_pos, card_id),
                    )
                    conn.execute(
                        'UPDATE cards SET position=position+1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>=?',
                        (board_id, new_col, new_lane, new_pos),
                    )
                    # Kinder + Enkel erben neue col/lane
                    conn.execute(
                        'UPDATE cards SET col_id=?, lane_id=? WHERE parent_card_id=?',
                        (new_col, new_lane, card_id),
                    )
                    conn.execute(
                        '''UPDATE cards SET col_id=?, lane_id=? WHERE parent_card_id IN
                           (SELECT id FROM cards WHERE parent_card_id=?)''',
                        (new_col, new_lane, card_id),
                    )
                else:
                    if new_pos < old_pos:
                        conn.execute(
                            'UPDATE cards SET position=position+1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>=? AND position<? AND id!=?',
                            (board_id, old_col, old_lane, new_pos, old_pos, card_id),
                        )
                    elif new_pos > old_pos:
                        conn.execute(
                            'UPDATE cards SET position=position-1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>? AND position<=? AND id!=?',
                            (board_id, old_col, old_lane, old_pos, new_pos, card_id),
                        )

            updates['col_id']  = new_col
            updates['lane_id'] = new_lane
            updates['position'] = new_pos

        if updates:
            updates['updated_at'] = now()
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(
                f'UPDATE cards SET {set_clause} WHERE id=?',
                list(updates.values()) + [card_id],
            )

        if 'linkedCards' in provided and body.linkedCards is not None:
            conn.execute(
                'DELETE FROM card_links WHERE card_id_a=? OR card_id_b=?',
                (card_id, card_id),
            )
            for other_id in body.linkedCards:
                a, b = (card_id, other_id) if card_id < other_id else (other_id, card_id)
                conn.execute(
                    'INSERT OR IGNORE INTO card_links (card_id_a, card_id_b) VALUES (?,?)',
                    (a, b),
                )

    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': card_id, 'boardId': _board_id})
    return {'ok': True}


@router.delete('/{card_id}')
def delete_card(card_id: str, user_id: str = Depends(current_user_id)):
    _board_id = _col_id = _lane_id = None
    with get_conn() as conn:
        c = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn)
        my_col = get_col_scope(user_id, c['board_id'], conn)
        if my_col:
            created_by = c['created_by'] if 'created_by' in c.keys() else None
            if created_by != user_id:
                raise HTTPException(403, 'Nur selbst angelegte Karten dürfen gelöscht werden')
        _board_id, _col_id, _lane_id = c['board_id'], c['col_id'], c['lane_id']
        parent_id = c['parent_card_id'] if 'parent_card_id' in c.keys() else None
        conn.execute('DELETE FROM cards WHERE id=?', (card_id,))
        if parent_id:
            conn.execute(
                'UPDATE cards SET position=position-1 WHERE parent_card_id=? AND position>?',
                (parent_id, c['position']),
            )
        else:
            conn.execute(
                'UPDATE cards SET position=position-1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>?',
                (c['board_id'], c['col_id'], c['lane_id'], c['position']),
            )
    broadcast(_board_id, {
        'type': 'card_deleted', 'userId': user_id,
        'id': card_id, 'boardId': _board_id, 'colId': _col_id, 'laneId': _lane_id,
    })
    return {'ok': True}


@router.post('/{card_id}/duplicate', status_code=201)
def duplicate_card(card_id: str, linked: bool = False, with_attachments: bool = True, user_id: str = Depends(current_user_id)):
    _board_id = _new_id = _ts = _new_pos = None
    _ev_data = {}
    with get_conn() as conn:
        src = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not src:
            raise HTTPException(404, 'Card not found')
        require_board_access(src['board_id'], user_id, conn)

        src_parent = src['parent_card_id'] if 'parent_card_id' in src.keys() else None
        if src_parent:
            conn.execute(
                'UPDATE cards SET position=position+1 WHERE parent_card_id=? AND position>?',
                (src_parent, src['position']),
            )
        else:
            conn.execute(
                'UPDATE cards SET position=position+1 WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL AND position>?',
                (src['board_id'], src['col_id'], src['lane_id'], src['position']),
            )
        new_id = uid()
        ts = now()
        new_pos = src['position'] + 1

        # Copy cover file if present
        new_cover_path = None
        if src['cover_path'] and os.path.exists(src['cover_path']):
            new_card_dir = os.path.join(UPLOAD_PATH, new_id)
            os.makedirs(new_card_dir, exist_ok=True)
            new_cover_path = os.path.join(new_card_dir, 'cover')
            shutil.copy2(src['cover_path'], new_cover_path)

        src_keys = src.keys()
        conn.execute(
            """INSERT INTO cards
               (id, board_id, col_id, lane_id, position, title, notes,
                color, bg_color, cover_path, points, points_max,
                card_type, parent_card_id, card_mode, due_date, time_spent,
                attendance_n, attendance_data, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id, src['board_id'], src['col_id'], src['lane_id'], new_pos,
             src['title'], src['notes'], src['color'], src['bg_color'],
             new_cover_path, src['points'], src['points_max'],
             src['card_type'] if 'card_type' in src_keys else 'card',
             src_parent,
             src['card_mode'] if 'card_mode' in src_keys else 'org',
             src['due_date'] if 'due_date' in src_keys else None,
             src['time_spent'] if 'time_spent' in src_keys else 0,
             src['attendance_n'] if 'attendance_n' in src_keys else None,
             src['attendance_data'] if 'attendance_data' in src_keys else None,
             ts, ts),
        )

        for st in conn.execute(
            'SELECT * FROM subtasks WHERE card_id=? ORDER BY position', (card_id,)
        ).fetchall():
            conn.execute(
                'INSERT INTO subtasks (id, card_id, text, done, position) VALUES (?,?,?,?,?)',
                (uid(), new_id, st['text'], st['done'], st['position']),
            )

        label_ids = []
        for row in conn.execute('SELECT label_id FROM card_labels WHERE card_id=?', (card_id,)).fetchall():
            conn.execute('INSERT INTO card_labels (card_id, label_id) VALUES (?,?)', (new_id, row['label_id']))
            label_ids.append(row['label_id'])

        # Copy attachments
        new_atts = []
        if with_attachments:
            att_rows = conn.execute(
                'SELECT id, filename, path, size, mime FROM attachments WHERE card_id=?', (card_id,)
            ).fetchall()
            for att in att_rows:
                if not os.path.exists(att['path']):
                    continue
                new_att_id = uid()
                new_card_dir = os.path.join(UPLOAD_PATH, new_id)
                os.makedirs(new_card_dir, exist_ok=True)
                new_att_path = os.path.join(new_card_dir, f'{new_att_id}_{att["filename"]}')
                shutil.copy2(att['path'], new_att_path)
                conn.execute(
                    'INSERT INTO attachments (id, card_id, filename, path, size, mime, created_at) VALUES (?,?,?,?,?,?,?)',
                    (new_att_id, new_id, att['filename'], new_att_path, att['size'], att['mime'], ts),
                )
                new_atts.append({'id': new_att_id, 'name': att['filename'], 'size': att['size'], 'type': att['mime'],
                                 'url': f'/attachments/{new_att_id}/file'})

        if linked:
            a, b = (card_id, new_id) if card_id < new_id else (new_id, card_id)
            conn.execute(
                'INSERT OR IGNORE INTO card_links (card_id_a, card_id_b) VALUES (?,?)', (a, b)
            )

        _board_id = src['board_id']
        _new_id, _ts, _new_pos = new_id, ts, new_pos
        _new_cover = f'/attachments/cover/{new_id}' if new_cover_path else None
        _ev_data = {
            'colId': src['col_id'], 'laneId': src['lane_id'],
            'title': src['title'], 'color': src['color'], 'bgColor': src['bg_color'],
            'points': src['points'], 'pointsMax': src['points_max'],
            'notes': src['notes'] or '', 'labelIds': label_ids,
            'coverImage': _new_cover, 'attachments': new_atts,
        }

    broadcast(_board_id, {
        'type': 'card_created', 'userId': user_id, 'boardId': _board_id,
        'id': _new_id, 'title': _ev_data['title'],
        'colId': _ev_data['colId'], 'laneId': _ev_data['laneId'],
        'color': _ev_data['color'], 'bgColor': _ev_data['bgColor'],
        'coverImage': _ev_data['coverImage'], 'notes': _ev_data['notes'],
        'subtasks': [], 'attachments': _ev_data['attachments'], 'labelIds': _ev_data['labelIds'],
        'points': _ev_data['points'], 'pointsMax': _ev_data['pointsMax'],
        'createdAt': _ts,
    })
    return {'id': _new_id, 'position': _new_pos}


# ── Copy to board ─────────────────────────────────────────────────────────────

class CopyToBody(BaseModel):
    target_board_id: str
    target_col_id: str
    target_lane_id: str


def _copy_card_recursive(conn, src_id, board_id, col_id, lane_id, parent_id, ts):
    src = conn.execute('SELECT * FROM cards WHERE id=?', (src_id,)).fetchone()
    if not src:
        return None
    new_id = uid()

    new_cover_path = None
    if src['cover_path'] and os.path.exists(src['cover_path']):
        new_dir = os.path.join(UPLOAD_PATH, new_id)
        os.makedirs(new_dir, exist_ok=True)
        new_cover_path = os.path.join(new_dir, 'cover')
        shutil.copy2(src['cover_path'], new_cover_path)

    if parent_id:
        row = conn.execute(
            'SELECT COALESCE(MAX(position),-1) AS m FROM cards WHERE parent_card_id=?', (parent_id,)
        ).fetchone()
    else:
        row = conn.execute(
            'SELECT COALESCE(MAX(position),-1) AS m FROM cards WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL',
            (board_id, col_id, lane_id),
        ).fetchone()
    pos = row['m'] + 1

    keys = src.keys()
    conn.execute(
        """INSERT INTO cards
           (id, board_id, col_id, lane_id, position, title, notes,
            color, bg_color, cover_path, points, points_max,
            card_type, parent_card_id, card_mode, due_date, time_spent,
            attendance_n, attendance_data, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (new_id, board_id, col_id, lane_id, pos,
         src['title'], src['notes'], src['color'], src['bg_color'],
         new_cover_path, src['points'], src['points_max'],
         src['card_type'] if 'card_type' in keys else 'card',
         parent_id,
         src['card_mode'] if 'card_mode' in keys else 'org',
         src['due_date'] if 'due_date' in keys else None,
         src['time_spent'] if 'time_spent' in keys else 0,
         src['attendance_n'] if 'attendance_n' in keys else None,
         src['attendance_data'] if 'attendance_data' in keys else None,
         ts, ts),
    )

    for st in conn.execute('SELECT * FROM subtasks WHERE card_id=? ORDER BY position', (src_id,)).fetchall():
        conn.execute(
            'INSERT INTO subtasks (id, card_id, text, done, position) VALUES (?,?,?,?,?)',
            (uid(), new_id, st['text'], st['done'], st['position']),
        )

    if board_id == src['board_id']:
        for row in conn.execute('SELECT label_id FROM card_labels WHERE card_id=?', (src_id,)).fetchall():
            conn.execute('INSERT INTO card_labels (card_id, label_id) VALUES (?,?)', (new_id, row['label_id']))

    for att in conn.execute('SELECT id, filename, path, size, mime FROM attachments WHERE card_id=?', (src_id,)).fetchall():
        if not os.path.exists(att['path']):
            continue
        new_att_id = uid()
        new_dir = os.path.join(UPLOAD_PATH, new_id)
        os.makedirs(new_dir, exist_ok=True)
        new_att_path = os.path.join(new_dir, f'{new_att_id}_{att["filename"]}')
        shutil.copy2(att['path'], new_att_path)
        conn.execute(
            'INSERT INTO attachments (id, card_id, filename, path, size, mime, created_at) VALUES (?,?,?,?,?,?,?)',
            (new_att_id, new_id, att['filename'], new_att_path, att['size'], att['mime'], ts),
        )

    for child in conn.execute('SELECT id FROM cards WHERE parent_card_id=? ORDER BY position', (src_id,)).fetchall():
        _copy_card_recursive(conn, child['id'], board_id, col_id, lane_id, new_id, ts)

    return new_id


@router.post('/{card_id}/copy-to', status_code=201)
def copy_card_to_board(card_id: str, body: CopyToBody, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        src = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not src:
            raise HTTPException(404, 'Card not found')
        require_board_access(src['board_id'], user_id, conn)
        require_board_access(body.target_board_id, user_id, conn)
        ts = now()
        new_id = _copy_card_recursive(conn, card_id, body.target_board_id, body.target_col_id, body.target_lane_id, None, ts)
    broadcast(body.target_board_id, {
        'type': 'card_created', 'userId': user_id, 'boardId': body.target_board_id,
        'id': new_id, 'title': src['title'],
        'colId': body.target_col_id, 'laneId': body.target_lane_id,
    })
    return {'id': new_id}


# ── Subtasks ──────────────────────────────────────────────────────────────────

@router.post('/{card_id}/subtasks', status_code=201)
def add_subtask(card_id: str, body: SubtaskCreate, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn)
        _board_id = c['board_id']
        row = conn.execute(
            'SELECT COALESCE(MAX(position), -1) AS m FROM subtasks WHERE card_id=?', (card_id,)
        ).fetchone()
        st_id = uid()
        conn.execute(
            'INSERT INTO subtasks (id, card_id, text, done, position) VALUES (?,?,?,?,?)',
            (st_id, card_id, body.text.strip(), 0, row['m'] + 1),
        )
    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': card_id, 'boardId': _board_id})
    return {'id': st_id}


@router.patch('/subtasks/{subtask_id}')
def update_subtask(subtask_id: str, body: SubtaskUpdate, user_id: str = Depends(current_user_id)):
    provided = body.model_fields_set
    _board_id = _card_id = None
    with get_conn() as conn:
        st = conn.execute(
            'SELECT s.*, c.board_id FROM subtasks s JOIN cards c ON s.card_id=c.id WHERE s.id=?',
            (subtask_id,),
        ).fetchone()
        if not st:
            raise HTTPException(404, 'Subtask not found')
        require_board_access(st['board_id'], user_id, conn)
        _board_id, _card_id = st['board_id'], st['card_id']

        updates: dict = {}
        if 'text' in provided and body.text is not None:
            updates['text'] = body.text.strip()
        if 'done' in provided and body.done is not None:
            updates['done'] = int(body.done)
        if 'position' in provided and body.position is not None:
            updates['position'] = body.position

        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE subtasks SET {set_clause} WHERE id=?', list(updates.values()) + [subtask_id])
    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': _card_id, 'boardId': _board_id})
    return {'ok': True}


@router.delete('/subtasks/{subtask_id}')
def delete_subtask(subtask_id: str, user_id: str = Depends(current_user_id)):
    _board_id = _card_id = None
    with get_conn() as conn:
        st = conn.execute(
            'SELECT s.*, c.board_id FROM subtasks s JOIN cards c ON s.card_id=c.id WHERE s.id=?',
            (subtask_id,),
        ).fetchone()
        if not st:
            raise HTTPException(404, 'Subtask not found')
        require_board_access(st['board_id'], user_id, conn)
        _board_id, _card_id = st['board_id'], st['card_id']
        conn.execute('DELETE FROM subtasks WHERE id=?', (subtask_id,))
    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': _card_id, 'boardId': _board_id})
    return {'ok': True}


# ── Label-Zuweisung ───────────────────────────────────────────────────────────

@router.post('/{card_id}/labels/{label_id}', status_code=201)
def assign_label(card_id: str, label_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn)
        _board_id = c['board_id']
        conn.execute('INSERT OR IGNORE INTO card_labels (card_id, label_id) VALUES (?,?)', (card_id, label_id))
    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': card_id, 'boardId': _board_id})
    return {'ok': True}


@router.delete('/{card_id}/labels/{label_id}')
def remove_label_from_card(card_id: str, label_id: str, user_id: str = Depends(current_user_id)):
    _board_id = None
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404, 'Card not found')
        require_board_access(c['board_id'], user_id, conn)
        _board_id = c['board_id']
        conn.execute('DELETE FROM card_labels WHERE card_id=? AND label_id=?', (card_id, label_id))
    broadcast(_board_id, {'type': 'card_updated', 'userId': user_id, 'id': card_id, 'boardId': _board_id})
    return {'ok': True}


# ── Inter-board links ─────────────────────────────────────────────────────────

@router.get('/{card_id}/board-links')
def get_board_links(card_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404)
        require_board_access(c['board_id'], user_id, conn)
        result = []
        # outgoing: this card linked to others
        for r in conn.execute(
            'SELECT target_card_id, target_board_id FROM inter_board_links WHERE card_id=?',
            (card_id,),
        ).fetchall():
            tc = conn.execute('SELECT title FROM cards WHERE id=?', (r['target_card_id'],)).fetchone()
            tb = conn.execute('SELECT title FROM boards WHERE id=?', (r['target_board_id'],)).fetchone()
            result.append({
                'targetCardId': r['target_card_id'],
                'targetBoardId': r['target_board_id'],
                'targetCardTitle': tc['title'] if tc else '(gelöscht)',
                'targetBoardTitle': tb['title'] if tb else '(unbekannt)',
                'missing': not tc,
                'deleteCardId': card_id,
            })
        # incoming: other cards linked to this card
        for r in conn.execute(
            '''SELECT ibl.card_id AS src_id, c.board_id AS src_board_id,
                      c.title AS src_title, b.title AS src_board_title
               FROM inter_board_links ibl
               JOIN cards c ON ibl.card_id = c.id
               JOIN boards b ON c.board_id = b.id
               WHERE ibl.target_card_id = ?''',
            (card_id,),
        ).fetchall():
            result.append({
                'targetCardId': r['src_id'],
                'targetBoardId': r['src_board_id'],
                'targetCardTitle': r['src_title'],
                'targetBoardTitle': r['src_board_title'],
                'missing': False,
                'deleteCardId': r['src_id'],  # delete from source side
            })
    return result


class BoardLinkIn(BaseModel):
    target_card_id: str
    target_board_id: str


@router.post('/{card_id}/board-links', status_code=201)
def add_board_link(card_id: str, body: BoardLinkIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404)
        if get_col_scope(user_id, c['board_id'], conn):
            raise HTTPException(403, 'Board-Links nicht erlaubt')
        require_board_access(c['board_id'], user_id, conn)
        # Verify target card exists and user has access
        tc = conn.execute('SELECT board_id FROM cards WHERE id=?', (body.target_card_id,)).fetchone()
        if not tc:
            raise HTTPException(404, 'Zielkarte nicht gefunden')
        try:
            require_board_access(body.target_board_id, user_id, conn)
        except Exception:
            raise HTTPException(403, 'Kein Zugang zum Ziel-Board')
        conn.execute(
            'INSERT OR IGNORE INTO inter_board_links (card_id, target_card_id, target_board_id) VALUES (?,?,?)',
            (card_id, body.target_card_id, body.target_board_id),
        )
    return {'ok': True}


@router.delete('/{card_id}/board-links/{other_card_id}')
def remove_board_link(card_id: str, other_card_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        c = conn.execute('SELECT board_id FROM cards WHERE id=?', (card_id,)).fetchone()
        if not c:
            raise HTTPException(404)
        require_board_access(c['board_id'], user_id, conn)
        # delete in both directions so either side can remove the link
        conn.execute(
            'DELETE FROM inter_board_links WHERE (card_id=? AND target_card_id=?) OR (card_id=? AND target_card_id=?)',
            (card_id, other_card_id, other_card_id, card_id),
        )
    return {'ok': True}
