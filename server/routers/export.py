import csv
import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth import current_user_id, require_board_access, get_global_role, require_admin
from db import get_conn

router = APIRouter()
UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')


def _board_db_path(board_id: str) -> str:
    db_path = os.environ.get('DB_PATH', './data/db/kanban.sqlite')
    board_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'boards')
    return os.path.join(board_dir, f'board-{board_id}.db')


def _add_board_db(zf: zipfile.ZipFile, board_id: str, board_path: str):
    db_file = _board_db_path(board_id)
    if not os.path.exists(db_file):
        return
    # 1) Raw DB file for recreation
    zf.write(db_file, board_path + '_board.db')
    # 2) Each table as CSV for human access
    try:
        bconn = sqlite3.connect(db_file)
        bconn.row_factory = sqlite3.Row
        tables = bconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for t in tables:
            tname = t['name']
            rows = bconn.execute(f'SELECT * FROM "{tname}"').fetchall()
            if not rows:
                continue
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(rows[0].keys())
            w.writerows([list(r) for r in rows])
            zf.writestr(board_path + f'_db/{_safe(tname)}.csv', buf.getvalue())
        bconn.close()
    except Exception:
        pass


def _safe(s: str, maxlen: int = 55) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(s))
    return s[:maxlen].strip('_. ') or 'untitled'


def _add_card(zf: zipfile.ZipFile, path: str, card_id: str, conn, user_map: dict):
    card = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
    if not card:
        return

    subtasks = conn.execute(
        'SELECT text, done FROM subtasks WHERE card_id=? ORDER BY position', (card_id,)
    ).fetchall()

    label_rows = conn.execute(
        '''SELECT l.text, l.color FROM card_labels cl
           JOIN labels l ON l.id = cl.label_id WHERE cl.card_id=?''',
        (card_id,)
    ).fetchall()

    assignee_ids = [r['user_id'] for r in conn.execute(
        'SELECT user_id FROM card_assignees WHERE card_id=?', (card_id,)
    ).fetchall()]

    person_code = None
    if card['person_id']:
        p = conn.execute('SELECT code FROM persons WHERE id=?', (card['person_id'],)).fetchone()
        if p:
            person_code = p['code']

    linked = conn.execute(
        '''SELECT c.title FROM card_links cl
           JOIN cards c ON c.id = CASE WHEN cl.card_id_a=? THEN cl.card_id_b ELSE cl.card_id_a END
           WHERE cl.card_id_a=? OR cl.card_id_b=?''',
        (card_id, card_id, card_id)
    ).fetchall()

    table = conn.execute('SELECT * FROM card_tables WHERE card_id=?', (card_id,)).fetchone()

    attachments = conn.execute(
        'SELECT * FROM attachments WHERE card_id=? ORDER BY position, created_at', (card_id,)
    ).fetchall()

    meta = {
        'title': card['title'],
        'card_type': card['card_type'] or 'card',
        'card_mode': card['card_mode'] or 'org',
        'bg_color': card['bg_color'],
        'color': card['color'],
        'due_date': card['due_date'],
        'time_spent': card['time_spent'] or 0,
        'attendance_n': card['attendance_n'],
        'attendance_data': json.loads(card['attendance_data']) if card['attendance_data'] else None,
        'cover_pos': card['cover_pos'],
        'points': card['points'],
        'points_max': card['points_max'],
        'created_at': card['created_at'],
        'person_code': person_code,
        'assignees': [user_map.get(uid, uid) for uid in assignee_ids],
        'labels': [{'text': r['text'], 'color': r['color']} for r in label_rows],
        'subtasks': [{'text': s['text'], 'done': bool(s['done'])} for s in subtasks],
        'linked_card_titles': [r['title'] for r in linked],
    }
    zf.writestr(path + '_meta.json', json.dumps(meta, ensure_ascii=False, indent=2))

    if card['notes']:
        zf.writestr(path + 'notes.md', card['notes'])

    if card['cover_path'] and os.path.exists(card['cover_path']):
        ext = os.path.splitext(card['cover_path'])[1] or '.jpg'
        zf.write(card['cover_path'], path + 'cover' + ext)

    if table:
        zf.writestr(path + 'table.json', json.dumps({
            'title': table['title'],
            'mode': table['mode'],
            'col_labels': json.loads(table['col_labels'] or '[]'),
            'row_labels': json.loads(table['row_labels'] or '[]'),
            'cells': json.loads(table['cells'] or '{}'),
        }, ensure_ascii=False, indent=2))

    for att in attachments:
        if os.path.exists(att['path']):
            zf.write(att['path'], path + att['filename'])

    children = conn.execute(
        'SELECT id, title FROM cards WHERE parent_card_id=? ORDER BY position', (card_id,)
    ).fetchall()
    for i, ch in enumerate(children):
        child_path = path + f'{i+1:03d}_{_safe(ch["title"])}/'
        _add_card(zf, child_path, ch['id'], conn, user_map)


def _build_board_zip(board_id: str, conn, zf: zipfile.ZipFile):
    board = conn.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
    if not board:
        return

    board_path = _safe(board['title']) + '/'

    labels = conn.execute('SELECT * FROM labels WHERE board_id=? ORDER BY id', (board_id,)).fetchall()
    members = conn.execute(
        '''SELECT u.name, bm.role FROM board_members bm
           JOIN users u ON u.id = bm.user_id WHERE bm.board_id=?''',
        (board_id,)
    ).fetchall()
    users = conn.execute('SELECT id, name FROM users').fetchall()
    user_map = {r['id']: r['name'] for r in users}

    zf.writestr(board_path + '_meta.json', json.dumps({
        'format': 'mkan-zip-v1',
        'id': board['id'],
        'title': board['title'],
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'labels': [{'id': r['id'], 'text': r['text'], 'color': r['color']} for r in labels],
        'members': [{'name': r['name'], 'role': r['role']} for r in members],
    }, ensure_ascii=False, indent=2))

    cols = conn.execute('SELECT * FROM columns WHERE board_id=? ORDER BY position', (board_id,)).fetchall()
    lanes = conn.execute('SELECT * FROM swimlanes WHERE board_id=? ORDER BY position', (board_id,)).fetchall()

    for ci, col in enumerate(cols):
        col_path = board_path + f'{ci+1:03d}_{_safe(col["title"])}/'
        zf.writestr(col_path + '_meta.json', json.dumps(
            {'title': col['title'], 'bg_color': col['bg_color'] if 'bg_color' in col.keys() else None},
            ensure_ascii=False
        ))

        for li, lane in enumerate(lanes):
            lane_path = col_path + f'{li+1:03d}_{_safe(lane["title"])}/'
            zf.writestr(lane_path + '_meta.json', json.dumps(
                {'title': lane['title'], 'note': lane['note']},
                ensure_ascii=False
            ))

            top_cards = conn.execute(
                '''SELECT id, title FROM cards
                   WHERE board_id=? AND col_id=? AND lane_id=? AND parent_card_id IS NULL
                   ORDER BY position''',
                (board_id, col['id'], lane['id'])
            ).fetchall()

            for ki, card in enumerate(top_cards):
                card_path = lane_path + f'{ki+1:03d}_{_safe(card["title"])}/'
                _add_card(zf, card_path, card['id'], conn, user_map)

    _add_board_db(zf, board_id, board_path)


@router.get('/boards/{board_id}/export.zip')
def export_board(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        is_admin = get_global_role(user_id, conn) == 'admin'
        if not is_admin:
            require_board_access(board_id, user_id, conn, min_role='owner')
        board = conn.execute('SELECT title FROM boards WHERE id=?', (board_id,)).fetchone()
        if not board:
            raise HTTPException(404, 'Board not found')

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            _build_board_zip(board_id, conn, zf)
        buf.seek(0)

    fname = f'mkan_{_safe(board["title"])}_{datetime.utcnow().strftime("%Y%m%d")}.zip'
    return StreamingResponse(
        buf, media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@router.get('/admin/export-all.zip')
def export_all(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_admin(user_id, conn)
        boards = conn.execute('SELECT id FROM boards ORDER BY created_at').fetchall()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for b in boards:
                _build_board_zip(b['id'], conn, zf)
        buf.seek(0)

    fname = f'mkan_all_{datetime.utcnow().strftime("%Y%m%d_%H%M")}.zip'
    return StreamingResponse(
        buf, media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )
