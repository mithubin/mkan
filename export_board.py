#!/usr/bin/env python3
"""
Exportiert ein Board aus der lokalen SQLite und importiert es direkt auf dem NUC.
Aufruf: python3 export_board.py <board_title_oder_id> [--dry-run]

Benötigt: NUC_USER und NUC_PASS als Env-Variablen, oder Eingabe-Prompt.
"""
import sys, os, json, base64, sqlite3, getpass, urllib.request, urllib.parse, io

DB_PATH     = os.path.join(os.path.dirname(__file__), 'server/data/db/kanban.sqlite')
SERVER_DIR  = os.path.join(os.path.dirname(__file__), 'server')
NUC_BASE    = 'https://mkan.milan.how'

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def b64file(path: str) -> str | None:
    full = os.path.join(SERVER_DIR, path) if not os.path.isabs(path) else path
    if not os.path.exists(full):
        print(f'  [WARN] Anhang nicht gefunden: {full}', file=sys.stderr)
        return None
    with open(full, 'rb') as f:
        return base64.b64encode(f.read()).decode()

def export_board(board_id_or_title: str) -> dict:
    conn = db()
    board = conn.execute(
        'SELECT * FROM boards WHERE id=? OR title=?',
        (board_id_or_title, board_id_or_title)
    ).fetchone()
    if not board:
        sys.exit(f'Board nicht gefunden: {board_id_or_title}')
    bid = board['id']
    print(f'Exportiere: "{board["title"]}" ({bid})')

    cols  = conn.execute('SELECT * FROM columns   WHERE board_id=? ORDER BY position', (bid,)).fetchall()
    lanes = conn.execute('SELECT * FROM swimlanes WHERE board_id=? ORDER BY position', (bid,)).fetchall()
    labels= conn.execute('SELECT * FROM labels    WHERE board_id=? ORDER BY id',       (bid,)).fetchall()
    persons=conn.execute('SELECT * FROM persons   WHERE board_id=? ORDER BY position', (bid,)).fetchall()
    kb    = conn.execute('SELECT * FROM klassenbuch WHERE board_id=?',                 (bid,)).fetchall()

    all_cards = conn.execute(
        'SELECT * FROM cards WHERE board_id=? ORDER BY position', (bid,)
    ).fetchall()

    # Topologische Sortierung: Eltern vor Kindern
    by_id = {c['id']: c for c in all_cards}
    sorted_cards = []
    visited = set()
    def visit(c):
        if c['id'] in visited:
            return
        if c['parent_card_id'] and c['parent_card_id'] in by_id:
            visit(by_id[c['parent_card_id']])
        visited.add(c['id'])
        sorted_cards.append(c)
    for c in all_cards:
        visit(c)

    cards_dict = {}
    cells = {}

    for c in sorted_cards:
        cid = c['id']

        # cover
        cover_img = None
        if c['cover_path']:
            raw = b64file(c['cover_path'])
            if raw:
                ext = c['cover_path'].rsplit('.', 1)[-1].lower()
                mime = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png',
                        'gif':'image/gif','webp':'image/webp'}.get(ext, 'image/jpeg')
                cover_img = f'data:{mime};base64,{raw}'

        # subtasks
        subs = conn.execute(
            'SELECT * FROM subtasks WHERE card_id=? ORDER BY position', (cid,)
        ).fetchall()

        # label ids
        lids = [r['label_id'] for r in conn.execute(
            'SELECT label_id FROM card_labels WHERE card_id=?', (cid,)
        ).fetchall()]

        # linked cards
        linked = []
        for row in conn.execute(
            'SELECT card_id_a, card_id_b FROM card_links WHERE card_id_a=? OR card_id_b=?', (cid, cid)
        ).fetchall():
            other = row['card_id_b'] if row['card_id_a'] == cid else row['card_id_a']
            linked.append(other)

        # attachments (mit base64)
        atts = []
        for a in conn.execute('SELECT * FROM attachments WHERE card_id=?', (cid,)).fetchall():
            raw = b64file(a['path'])
            if raw:
                mime = a['mime'] or 'application/octet-stream'
                atts.append({
                    'id':   a['id'],
                    'name': a['filename'],
                    'data': f'data:{mime};base64,{raw}',
                })
            else:
                print(f'  [SKIP] Anhang fehlt: {a["filename"]}')

        cards_dict[cid] = {
            'title':       c['title'],
            'notes':       c['notes'] or '',
            'color':       c['color'],
            'bgColor':     c['bg_color'],
            'coverImage':  cover_img,
            'points':      c['points'] or 0,
            'pointsMax':   c['points_max'],
            'personId':    c['person_id'],
            'cardType':    c['card_type'] or 'card',
            'parentCardId':c['parent_card_id'],
            'colId':       c['col_id'],
            'laneId':      c['lane_id'],
            'createdAt':   c['created_at'],
            'labelIds':    lids,
            'subtasks':    [{'id': s['id'], 'text': s['text'], 'done': bool(s['done'])} for s in subs],
            'attachments': atts,
            'linkedCards': linked,
        }

        # cells: nur Top-Level-Karten für Positions-Mapping
        if not c['parent_card_id']:
            key = f'{c["col_id"]}|{c["lane_id"]}'
            cells.setdefault(key, []).append(cid)

    result = {
        'id':         bid,
        'title':      board['title'],
        'startDate':  board['start_date'],
        'endDate':    board['end_date'],
        'columns':    [{'id': c['id'], 'title': c['title']} for c in cols],
        'swimlanes':  [{'id': l['id'], 'title': l['title'], 'note': l['note']} for l in lanes],
        'labels':     [{'id': l['id'], 'text': l['text'], 'color': l['color']} for l in labels],
        'persons':    [{'id': p['id'], 'code': p['code'], 'name': p['name']} for p in persons],
        'cells':      cells,
        'cards':      cards_dict,
        'klassenbuch':[{
            'id': k['id'], 'date': k['date'], 'title': k['title'],
            'note': k['note'], 'stunden': json.loads(k['stunden'] or '[]'),
            'snapshotId': k['snapshot_id'],
        } for k in kb],
    }
    conn.close()
    print(f'  {len(cards_dict)} Karten, {sum(len(c["attachments"]) for c in cards_dict.values())} Anhänge')
    return result

def nuc_login(user: str, pw: str) -> str:
    payload = json.dumps({'email': user, 'password': pw}).encode()
    req = urllib.request.Request(
        f'{NUC_BASE}/auth/login',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())['token']

def nuc_import(token: str, board: dict):
    body = json.dumps(board).encode()
    boundary = b'----mkanexport'
    parts  = b'--' + boundary + b'\r\n'
    parts += b'Content-Disposition: form-data; name="file"; filename="board.json"\r\n'
    parts += b'Content-Type: application/json\r\n\r\n'
    parts += body + b'\r\n'
    parts += b'--' + boundary + b'--\r\n'

    # board_id in URL ist egal, import liest id aus JSON
    req = urllib.request.Request(
        f'{NUC_BASE}/boards/{board["id"]}/import',
        data=parts,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': f'multipart/form-data; boundary={boundary.decode()}',
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('Aufruf: python3 export_board.py <board_title_oder_id> [--dry-run]')

    dry = '--dry-run' in sys.argv
    board = export_board(sys.argv[1])

    if dry:
        out = f'/tmp/mkan_export_{board["id"][:8]}.json'
        with open(out, 'w') as f:
            json.dump(board, f, indent=2)
        print(f'Dry-run: gespeichert als {out}')
        sys.exit(0)

    user = os.environ.get('NUC_USER') or input('NUC-Benutzername: ')
    pw   = os.environ.get('NUC_PASS') or getpass.getpass('NUC-Passwort: ')

    print('Login...')
    token = nuc_login(user, pw)
    print('Importiere...')
    result = nuc_import(token, board)
    print(f'Fertig: {result}')
