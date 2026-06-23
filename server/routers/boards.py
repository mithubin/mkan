import base64
import json
import os
import re
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from db import get_conn, uid, now
from auth import current_user_id, require_board_access, hash_pw, get_col_scope
from routers.events import broadcast
from routers.mail_utils import send_mail, _MKAN_BASE

router = APIRouter()

UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')


# ── Helpers ──────────────────────────────────────────────────────────────────

def build_board_response(board_id: str, conn, requesting_user_id: str | None = None) -> dict:
    board = conn.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
    if not board:
        return None

    my_col_id = get_col_scope(requesting_user_id, board_id, conn) if requesting_user_id else None

    columns = [
        {
            'id': r['id'], 'title': r['title'], 'position': r['position'],
            'colType': r['col_type'] if 'col_type' in r.keys() else 'normal',
            'bgColor': r['bg_color'] if 'bg_color' in r.keys() else None,
        }
        for r in conn.execute(
            'SELECT id, title, position, col_type, bg_color FROM columns WHERE board_id=? ORDER BY position',
            (board_id,),
        ).fetchall()
    ]
    if my_col_id:
        columns = [c for c in columns if c['id'] == my_col_id]

    swimlanes = [
        {'id': r['id'], 'title': r['title'], 'note': r['note'], 'position': r['position']}
        for r in conn.execute(
            'SELECT id, title, note, position FROM swimlanes WHERE board_id=? ORDER BY position',
            (board_id,),
        ).fetchall()
    ]

    labels = [
        {'id': r['id'], 'text': r['text'], 'color': r['color']}
        for r in conn.execute(
            'SELECT id, text, color FROM labels WHERE board_id=?', (board_id,)
        ).fetchall()
    ]

    card_rows = conn.execute(
        'SELECT * FROM cards WHERE board_id=? ORDER BY position', (board_id,)
    ).fetchall()

    link_rows = conn.execute(
        '''SELECT cl.card_id_a, cl.card_id_b FROM card_links cl
           JOIN cards c ON c.id = cl.card_id_a WHERE c.board_id=?''',
        (board_id,),
    ).fetchall()
    links_by_card: dict[str, list[str]] = {}
    for r in link_rows:
        links_by_card.setdefault(r['card_id_a'], []).append(r['card_id_b'])
        links_by_card.setdefault(r['card_id_b'], []).append(r['card_id_a'])

    assignee_rows = conn.execute(
        '''SELECT ca.card_id, u.id, u.name, u.display_initials, u.badge_color
           FROM card_assignees ca JOIN users u ON ca.user_id=u.id
           WHERE ca.card_id IN (SELECT id FROM cards WHERE board_id=?)''',
        (board_id,),
    ).fetchall()
    assignees_by_card: dict[str, list] = {}
    for r in assignee_rows:
        assignees_by_card.setdefault(r['card_id'], []).append({'id': r['id'], 'name': r['name'], 'display_initials': r['display_initials'], 'badge_color': r['badge_color']})

    cards = {}
    cells = {}

    for c in card_rows:
        card_id = c['id']

        subtasks = [
            {'id': s['id'], 'text': s['text'], 'done': bool(s['done'])}
            for s in conn.execute(
                'SELECT id, text, done FROM subtasks WHERE card_id=? ORDER BY position',
                (card_id,),
            ).fetchall()
        ]

        label_ids = [
            r['label_id']
            for r in conn.execute(
                'SELECT label_id FROM card_labels WHERE card_id=?', (card_id,)
            ).fetchall()
        ]

        atts = [
            {
                'id': a['id'],
                'name': a['filename'],
                'size': a['size'],
                'type': a['mime'],
                'url': f'/attachments/{a["id"]}/file',
                'createdAt': a['created_at'],
            }
            for a in conn.execute(
                'SELECT id, filename, size, mime, created_at FROM attachments WHERE card_id=?', (card_id,)
            ).fetchall()
        ]

        cover_url = f'/attachments/cover/{card_id}' if c['cover_path'] else None

        parent_id = c['parent_card_id'] if 'parent_card_id' in c.keys() else None
        cards[card_id] = {
            'id': card_id,
            'position': c['position'],
            'title': c['title'],
            'notes': c['notes'] or '',
            'color': c['color'],
            'bgColor': c['bg_color'],
            'coverImage': cover_url,
            'subtasks': subtasks,
            'attachments': atts,
            'linkedCards': links_by_card.get(card_id, []),
            'labelIds': label_ids,
            'assignees': assignees_by_card.get(card_id, []),
            'personId': c['person_id'] if 'person_id' in c.keys() else None,
            'points': c['points'],
            'pointsMax': c['points_max'],
            'colId': c['col_id'],
            'laneId': c['lane_id'],
            'createdAt': c['created_at'],
            'cardType': c['card_type'] if 'card_type' in c.keys() else 'card',
            'cardSettings': c['card_settings'] if 'card_settings' in c.keys() else None,
            'parentCardId': parent_id,
            'cardMode': c['card_mode'] if 'card_mode' in c.keys() else 'org',
            'dueDate': c['due_date'] if 'due_date' in c.keys() else None,
            'timeSpent': c['time_spent'] if 'time_spent' in c.keys() else 0,
            'attendanceN': c['attendance_n'] if 'attendance_n' in c.keys() else None,
            'attendanceData': c['attendance_data'] if 'attendance_data' in c.keys() else None,
            'coverPos': c['cover_pos'] if 'cover_pos' in c.keys() else None,
            'dvShared': bool(c['dv_shared']) if 'dv_shared' in c.keys() else False,
            'updatedAt': c['updated_at'],
            'createdBy': c['created_by'] if 'created_by' in c.keys() else None,
        }

        if not parent_id:
            key = f"{c['lane_id']}::{c['col_id']}"
            cells.setdefault(key, []).append(card_id)

    if my_col_id:
        cards = {k: v for k, v in cards.items()
                 if v['colId'] == my_col_id or v.get('parentCardId') is not None}
        cells = {k: v for k, v in cells.items() if k.split('::')[1] == my_col_id}

    persons = [
        {'id': r['id'], 'code': r['code'], 'name': r['name'], 'position': r['position']}
        for r in conn.execute(
            'SELECT id, code, name, position FROM persons WHERE board_id=? ORDER BY position',
            (board_id,),
        ).fetchall()
    ]

    members = [
        {'id': r['id'], 'name': r['name'], 'role': r['role'], 'colId': r['col_id']}
        for r in conn.execute(
            '''SELECT u.id, u.name, bm.role, bm.col_id FROM board_members bm
               JOIN users u ON bm.user_id=u.id
               WHERE bm.board_id=? ORDER BY bm.role, u.name''',
            (board_id,),
        ).fetchall()
    ]

    return {
        'id': board['id'],
        'title': board['title'],
        'startDate': board['start_date'] if 'start_date' in board.keys() else None,
        'endDate': board['end_date'] if 'end_date' in board.keys() else None,
        'columns': columns,
        'swimlanes': swimlanes,
        'cards': cards,
        'cells': cells,
        'labels': labels,
        'persons': persons,
        'members': members,
        'myColId': my_col_id,
    }


def _save_base64(card_id: str, name: str, data_url: str) -> str | None:
    m = re.match(r'data:([^;]+);base64,(.+)', data_url, re.DOTALL)
    if not m:
        return None
    raw = base64.b64decode(m.group(2))
    card_dir = os.path.join(UPLOAD_PATH, card_id)
    os.makedirs(card_dir, exist_ok=True)
    safe = re.sub(r'[^\w.\-]', '_', name)[:100]
    path = os.path.join(card_dir, safe)
    with open(path, 'wb') as f:
        f.write(raw)
    return path


# ── Board CRUD ────────────────────────────────────────────────────────────────

class BoardCreate(BaseModel):
    title: str


class BoardUpdate(BaseModel):
    title: str | None = None
    startDate: str | None = None
    endDate: str | None = None


@router.get('')
def list_boards(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.id, b.title, b.created_at, bm.role, ubs.settings
               FROM boards b JOIN board_members bm ON b.id=bm.board_id
               LEFT JOIN user_board_settings ubs ON b.id=ubs.board_id AND ubs.user_id=?
               WHERE bm.user_id=? ORDER BY b.created_at""",
            (user_id, user_id),
        ).fetchall()
    result = []
    for r in rows:
        entry = {'id': r['id'], 'title': r['title'], 'created_at': r['created_at'], 'role': r['role']}
        if r['settings']:
            try:
                s = json.loads(r['settings'])
                if s.get('accent'):
                    entry['accent'] = s['accent']
                if s.get('chrome'):
                    entry['chrome'] = s['chrome']
            except Exception:
                pass
        result.append(entry)
    return result


def _create_board_for(user_id: str, title: str, conn) -> dict:
    board_id, ts = uid(), now()
    conn.execute(
        'INSERT INTO boards (id, title, owner_id, created_at, updated_at) VALUES (?,?,?,?,?)',
        (board_id, title.strip(), user_id, ts, ts),
    )
    conn.execute(
        'INSERT INTO board_members (board_id, user_id, role) VALUES (?,?,?)',
        (board_id, user_id, 'owner'),
    )
    col_id, lane_id = uid(), uid()
    conn.execute(
        'INSERT INTO columns (id, board_id, title, position) VALUES (?,?,?,?)',
        (col_id, board_id, 'Aufgaben', 0),
    )
    conn.execute(
        'INSERT INTO swimlanes (id, board_id, title, note, position) VALUES (?,?,?,?,?)',
        (lane_id, board_id, 'Allgemein', None, 0),
    )
    return {'id': board_id, 'title': title.strip()}


@router.post('', status_code=201)
def create_board(body: BoardCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        from auth import get_global_role
        if get_global_role(user_id, conn) != 'admin':
            raise HTTPException(403, 'Nur Admins können Boards anlegen')
        return _create_board_for(user_id, body.title, conn)


class InviteTokenUseIn(BaseModel):
    token: str
    title: str = 'Mein Board'


@router.post('/from-invite', status_code=201)
def create_board_from_invite(body: InviteTokenUseIn, user_id: str = Depends(current_user_id)):
    from datetime import datetime, timezone
    with get_conn() as conn:
        row = conn.execute(
            'SELECT created_by, expires_at FROM board_invite_tokens WHERE token=?', (body.token,)
        ).fetchone()
        if not row:
            raise HTTPException(400, 'Ungültiger oder bereits verwendeter Einladungs-Link')
        if datetime.now(timezone.utc).isoformat() > row['expires_at']:
            conn.execute('DELETE FROM board_invite_tokens WHERE token=?', (body.token,))
            raise HTTPException(400, 'Einladungs-Link abgelaufen')
        conn.execute('DELETE FROM board_invite_tokens WHERE token=?', (body.token,))
        # Upgrade user to 'user' if they're still 'viewer' (so they can own their board)
        cur_role = conn.execute('SELECT global_role FROM users WHERE id=?', (user_id,)).fetchone()
        if cur_role and cur_role['global_role'] == 'viewer':
            conn.execute("UPDATE users SET global_role='user' WHERE id=?", (user_id,))
        return _create_board_for(user_id, body.title, conn)


@router.get('/{board_id}')
def get_board(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        data = build_board_response(board_id, conn, requesting_user_id=user_id)
    if not data:
        raise HTTPException(404, 'Board not found')
    return data


@router.patch('/{board_id}')
def update_board(board_id: str, body: BoardUpdate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        updates: dict = {}
        if body.title is not None:
            updates['title'] = body.title.strip()
        if body.startDate is not None:
            updates['start_date'] = body.startDate
        if body.endDate is not None:
            updates['end_date'] = body.endDate
        if updates:
            updates['updated_at'] = now()
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(
                f'UPDATE boards SET {set_clause} WHERE id=?',
                list(updates.values()) + [board_id],
            )
    broadcast(board_id, {'type': 'board_updated', 'userId': user_id, 'boardId': board_id})
    return {'ok': True}


@router.delete('/{board_id}')
def delete_board(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        conn.execute('DELETE FROM boards WHERE id=?', (board_id,))
    return {'ok': True}


# ── Members ───────────────────────────────────────────────────────────────────

class InviteIn(BaseModel):
    email: str
    role: str = 'editor'


@router.get('/{board_id}/members')
def list_members(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        rows = conn.execute(
            """SELECT u.id, u.name, u.email, u.display_initials, u.badge_color, bm.role, bm.col_id
               FROM board_members bm JOIN users u ON bm.user_id=u.id
               WHERE bm.board_id=?""",
            (board_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post('/{board_id}/members', status_code=201)
def invite_member(board_id: str, body: InviteIn, user_id: str = Depends(current_user_id)):
    if body.role not in ('editor', 'viewer', 'owner'):
        raise HTTPException(400, 'Role must be editor, viewer or owner')
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        target = conn.execute(
            'SELECT id, global_role FROM users WHERE email=?', (body.email.lower().strip(),)
        ).fetchone()
        if not target:
            raise HTTPException(404, 'User not found')
        # Global viewer can only be added as board-viewer unless requester is admin or owner
        effective_role = body.role
        if target['global_role'] == 'viewer' and body.role == 'editor':
            from auth import get_global_role
            if get_global_role(user_id, conn) != 'admin':
                effective_role = 'viewer'
        conn.execute(
            'INSERT OR REPLACE INTO board_members (board_id, user_id, role) VALUES (?,?,?)',
            (board_id, target['id'], effective_role),
        )
        board_title = conn.execute('SELECT title FROM boards WHERE id=?', (board_id,)).fetchone()
        board_title = board_title['title'] if board_title else 'mkan'
        target_email = body.email.lower().strip()
        target_name = conn.execute('SELECT name FROM users WHERE id=?', (target['id'],)).fetchone()
        target_name = target_name['name'] if target_name else target_email
    try:
        send_mail(target_email, 'board_added', {
            'name': target_name, 'board_title': board_title, 'url': _MKAN_BASE,
        })
    except Exception:
        pass
    return {'ok': True, 'role': effective_role}


@router.patch('/{board_id}/members/{target_id}/role')
def change_member_role(board_id: str, target_id: str, body: dict, user_id: str = Depends(current_user_id)):
    new_role = body.get('role')
    if new_role not in ('editor', 'viewer', 'owner'):
        raise HTTPException(400, 'Rolle muss editor, viewer oder owner sein')
    with get_conn() as conn:
        from auth import get_global_role
        requester_board_role = require_board_access(board_id, user_id, conn, min_role='owner')
        is_admin = get_global_role(user_id, conn) == 'admin'
        # Only owner of the board or global admin may upgrade a global viewer to editor
        target_global = conn.execute('SELECT global_role FROM users WHERE id=?', (target_id,)).fetchone()
        if target_global and target_global['global_role'] == 'viewer' and new_role == 'editor':
            if not is_admin and requester_board_role['role'] != 'owner':
                raise HTTPException(403, 'Nur Owner oder Admin können Viewer zu Editor hochstufen')
        conn.execute(
            'UPDATE board_members SET role=? WHERE board_id=? AND user_id=?',
            (new_role, board_id, target_id),
        )
    return {'ok': True}


@router.patch('/{board_id}/members/{target_id}/scope')
def set_member_scope(board_id: str, target_id: str, body: dict, user_id: str = Depends(current_user_id)):
    col_id = body.get('col_id') or None
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        if col_id:
            col = conn.execute('SELECT id FROM columns WHERE id=? AND board_id=?', (col_id, board_id)).fetchone()
            if not col:
                raise HTTPException(404, 'Spalte nicht gefunden')
        conn.execute(
            'UPDATE board_members SET col_id=? WHERE board_id=? AND user_id=?',
            (col_id, board_id, target_id),
        )
    return {'ok': True}


@router.delete('/{board_id}/members/{target_id}')
def remove_member(board_id: str, target_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        row = conn.execute(
            'SELECT role FROM board_members WHERE board_id=? AND user_id=?', (board_id, target_id)
        ).fetchone()
        if row and row['role'] == 'owner' and target_id == user_id:
            raise HTTPException(400, 'Cannot remove yourself as owner')
        conn.execute(
            'DELETE FROM board_members WHERE board_id=? AND user_id=?', (board_id, target_id)
        )
    return {'ok': True}


class CreateMemberIn(BaseModel):
    name: str
    email: str
    password: str
    role: str = 'editor'
    global_role: str = 'user'


@router.post('/{board_id}/members/create', status_code=201)
def create_and_add_member(board_id: str, body: CreateMemberIn, user_id: str = Depends(current_user_id)):
    if body.role not in ('editor', 'viewer'):
        raise HTTPException(400, 'Role must be editor or viewer')
    if body.global_role not in ('admin', 'user', 'viewer'):
        raise HTTPException(400, 'Ungültige globale Rolle')
    if len(body.password) < 8:
        raise HTTPException(400, 'Passwort mindestens 8 Zeichen')
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        if conn.execute('SELECT id FROM users WHERE email=?', (body.email.lower().strip(),)).fetchone():
            raise HTTPException(409, 'E-Mail bereits registriert')
        new_id = uid()
        conn.execute(
            'INSERT INTO users (id, name, email, pw_hash, global_role, created_at) VALUES (?,?,?,?,?,?)',
            (new_id, body.name.strip(), body.email.lower().strip(), hash_pw(body.password), body.global_role, now()),
        )
        conn.execute(
            'INSERT OR REPLACE INTO board_members (board_id, user_id, role) VALUES (?,?,?)',
            (board_id, new_id, body.role),
        )
        board_title = conn.execute('SELECT title FROM boards WHERE id=?', (board_id,)).fetchone()
        board_title = board_title['title'] if board_title else 'mkan'
    email = body.email.lower().strip()
    name = body.name.strip()
    mail_sent = False
    try:
        send_mail(email, 'welcome_board', {
            'name': name, 'board_title': board_title, 'email': email, 'password': body.password,
        })
        send_mail(email, 'url_reminder', {'name': name, 'url': _MKAN_BASE})
        mail_sent = True
    except Exception:
        pass
    return {'id': new_id, 'name': name, 'role': body.role, 'mail_sent': mail_sent}


# ── Resend mails ─────────────────────────────────────────────────────────────

@router.post('/{board_id}/members/{target_id}/send-url')
def resend_url(board_id: str, target_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        u = conn.execute('SELECT name, email FROM users WHERE id=?', (target_id,)).fetchone()
        if not u:
            raise HTTPException(404)
    try:
        send_mail(u['email'], 'url_reminder', {'name': u['name'], 'url': _MKAN_BASE})
    except Exception as e:
        raise HTTPException(502, f'Mail fehlgeschlagen: {e}')
    return {'ok': True}


@router.post('/{board_id}/members/{target_id}/send-reset')
def resend_reset(board_id: str, target_id: str, user_id: str = Depends(current_user_id)):
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='owner')
        u = conn.execute('SELECT name, email FROM users WHERE id=?', (target_id,)).fetchone()
        if not u:
            raise HTTPException(404)
        token = _uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        conn.execute('DELETE FROM pw_reset_tokens WHERE user_id=?', (target_id,))
        conn.execute(
            'INSERT INTO pw_reset_tokens (token, user_id, expires_at) VALUES (?,?,?)',
            (token, target_id, expires),
        )
    link = f'{_MKAN_BASE}/?reset_token={token}'
    try:
        send_mail(u['email'], 'reset_link', {'name': u['name'], 'link': link})
    except Exception as e:
        raise HTTPException(502, f'Mail fehlgeschlagen: {e}')
    return {'ok': True}


# ── Card search (for inter-board linking) ────────────────────────────────────

@router.get('/{board_id}/cards/search')
def search_cards(board_id: str, q: str = '', user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        rows = conn.execute(
            '''SELECT c.id, c.title, col.title AS col_title
               FROM cards c JOIN columns col ON c.col_id=col.id
               WHERE c.board_id=? AND c.card_type='card' AND c.parent_card_id IS NULL
                 AND c.title LIKE ? LIMIT 30''',
            (board_id, f'%{q}%'),
        ).fetchall()
    return [{'id': r['id'], 'title': r['title'], 'colTitle': r['col_title']} for r in rows]


# ── Import (JSON → DB) ────────────────────────────────────────────────────────

@router.post('/{board_id}/import')
async def import_json(
    board_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
):
    content = await file.read()
    try:
        mb = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(400, 'Invalid JSON')

    boards_data = mb.get('boards', [mb])

    imported = []
    with get_conn() as conn:
        for bd in boards_data:
            _import_board(conn, bd, user_id)
            imported.append(bd.get('id', '?'))

    return {'imported': imported}


def _import_board(conn, data: dict, owner_id: str):
    b_id = data.get('id') or uid()
    ts = now()

    existing = conn.execute('SELECT id FROM boards WHERE id=?', (b_id,)).fetchone()
    if existing:
        member = conn.execute(
            'SELECT role FROM board_members WHERE board_id=? AND user_id=?', (b_id, owner_id)
        ).fetchone()
        if not member:
            raise HTTPException(403, f'Board {b_id} belongs to another user')
        # Reimport: wipe existing data
        conn.execute('DELETE FROM columns     WHERE board_id=?', (b_id,))
        conn.execute('DELETE FROM swimlanes   WHERE board_id=?', (b_id,))
        conn.execute('DELETE FROM labels      WHERE board_id=?', (b_id,))
        conn.execute('DELETE FROM cards       WHERE board_id=?', (b_id,))
        conn.execute('DELETE FROM persons     WHERE board_id=?', (b_id,))
        conn.execute('DELETE FROM klassenbuch WHERE board_id=?', (b_id,))
        conn.execute(
            'UPDATE boards SET title=?, start_date=?, end_date=?, updated_at=? WHERE id=?',
            (data.get('title', 'Imported'), data.get('startDate'), data.get('endDate'), ts, b_id),
        )
    else:
        conn.execute(
            'INSERT INTO boards (id, title, owner_id, start_date, end_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
            (b_id, data.get('title', 'Imported Board'), owner_id,
             data.get('startDate'), data.get('endDate'), ts, ts),
        )
        conn.execute(
            'INSERT INTO board_members (board_id, user_id, role) VALUES (?,?,?)',
            (b_id, owner_id, 'owner'),
        )

    for pos, col in enumerate(data.get('columns', [])):
        conn.execute(
            'INSERT INTO columns (id, board_id, title, position) VALUES (?,?,?,?)',
            (col['id'], b_id, col['title'], pos),
        )

    for pos, lane in enumerate(data.get('swimlanes', [])):
        conn.execute(
            'INSERT INTO swimlanes (id, board_id, title, note, position) VALUES (?,?,?,?,?)',
            (lane['id'], b_id, lane['title'], lane.get('note'), pos),
        )

    for lbl in data.get('labels', []):
        conn.execute(
            'INSERT INTO labels (id, board_id, text, color) VALUES (?,?,?,?)',
            (lbl['id'], b_id, lbl['text'], lbl.get('color', '#888888')),
        )

    for pos, p in enumerate(data.get('persons', [])):
        conn.execute(
            'INSERT INTO persons (id, board_id, code, name, position) VALUES (?,?,?,?,?)',
            (p['id'], b_id, p.get('code', ''), p.get('name'), pos),
        )

    cards_data = data.get('cards', {})
    cells = data.get('cells', {})

    # Positionsreihenfolge aus cells-Array ableiten
    card_positions: dict[str, int] = {}
    for ids in cells.values():
        for pos, cid in enumerate(ids):
            card_positions[cid] = pos

    for cid, c in cards_data.items():
        cover_path = None
        cover_img = c.get('coverImage', '')
        if cover_img and cover_img.startswith('data:'):
            cover_path = _save_base64(cid, 'cover', cover_img)

        conn.execute(
            """INSERT INTO cards
               (id, board_id, col_id, lane_id, position, title, notes,
                color, bg_color, cover_path, points, points_max, person_id,
                card_type, parent_card_id, card_mode, due_date, time_spent,
                attendance_n, attendance_data, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, b_id,
                c.get('colId', ''), c.get('laneId', ''),
                card_positions.get(cid, 0),
                c.get('title', ''), c.get('notes', ''),
                c.get('color'), c.get('bgColor'),
                cover_path,
                c.get('points', 0), c.get('pointsMax'),
                c.get('personId'),
                c.get('cardType', 'card'), c.get('parentCardId'),
                c.get('cardMode', 'org'), c.get('dueDate'),
                c.get('timeSpent', 0),
                c.get('attendanceN'), c.get('attendanceData'),
                c.get('createdAt', ts), ts,
            ),
        )

        for pos, st in enumerate(c.get('subtasks', [])):
            conn.execute(
                'INSERT INTO subtasks (id, card_id, text, done, position) VALUES (?,?,?,?,?)',
                (st.get('id') or uid(), cid, st['text'], int(st.get('done', False)), pos),
            )

        for lid in c.get('labelIds', []):
            conn.execute('INSERT OR IGNORE INTO card_labels (card_id, label_id) VALUES (?,?)', (cid, lid))

        for att in c.get('attachments', []):
            att_data = att.get('data', '')
            if not att_data or not att_data.startswith('data:'):
                continue
            att_path = _save_base64(cid, att.get('name', 'file'), att_data)
            if not att_path:
                continue
            m = re.match(r'data:([^;]+);base64,', att_data)
            mime = m.group(1) if m else None
            att_size = os.path.getsize(att_path)
            conn.execute(
                'INSERT INTO attachments (id, card_id, filename, path, size, mime, created_at) VALUES (?,?,?,?,?,?,?)',
                (att.get('id') or uid(), cid, att.get('name', 'file'), att_path, att_size, mime, ts),
            )

    # card_links aus linkedCards (kanonische Paare)
    for cid, c in cards_data.items():
        for other in c.get('linkedCards', []):
            pair = (min(cid, other), max(cid, other))
            conn.execute('INSERT OR IGNORE INTO card_links (card_id_a, card_id_b) VALUES (?,?)', pair)

    # klassenbuch
    for kb in data.get('klassenbuch', []):
        stunden = kb.get('stunden', [])
        conn.execute(
            'INSERT INTO klassenbuch (id, board_id, date, title, note, stunden, snapshot_id) VALUES (?,?,?,?,?,?,?)',
            (kb['id'], b_id, kb['date'], kb['title'], kb.get('note'),
             json.dumps(stunden) if isinstance(stunden, list) else stunden,
             kb.get('snapshotId') or kb.get('snapshot_id')),
        )


# ── Board Settings ────────────────────────────────────────────────────────────

from typing import Dict, Any


@router.get('/{board_id}/settings')
def get_board_settings(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        row = conn.execute(
            'SELECT settings FROM user_board_settings WHERE user_id=? AND board_id=?',
            (user_id, board_id),
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row['settings'])
    except Exception:
        return {}


@router.put('/{board_id}/settings')
def put_board_settings(board_id: str, body: Dict[str, Any], user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
        conn.execute(
            'INSERT OR REPLACE INTO user_board_settings (user_id, board_id, settings) VALUES (?,?,?)',
            (user_id, board_id, json.dumps(body)),
        )
    return {'ok': True}
