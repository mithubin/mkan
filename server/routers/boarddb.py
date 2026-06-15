import csv
import io
import os
import re
import smtplib
import sqlite3
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user_id, require_board_access
from db import get_conn, uid

router = APIRouter()

_WRITE_OPS = re.compile(
    r'\b(DROP|CREATE|INSERT|UPDATE|DELETE|ALTER|ATTACH|DETACH|PRAGMA)\b',
    re.IGNORECASE,
)


def _board_db_path(board_id: str) -> str:
    db_path = os.environ.get('DB_PATH', './data/db/kanban.sqlite')
    board_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'boards')
    os.makedirs(board_dir, exist_ok=True)
    return os.path.join(board_dir, f'board-{board_id}.db')


def _board_conn(board_id: str) -> sqlite3.Connection:
    conn = sqlite3.connect(_board_db_path(board_id))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def _safe_tname(raw: str) -> str:
    name = re.sub(r'[^\w\s\-]', '', raw).strip()
    if not name:
        raise HTTPException(400, 'Ungültiger Tabellenname')
    return name


# ── Ensure DB column ──────────────────────────────────────────────────────────

@router.post('/{board_id}/db/ensure-col', status_code=201)
def ensure_db_col(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
        existing = conn.execute(
            "SELECT id FROM columns WHERE board_id=? AND col_type='db'", (board_id,)
        ).fetchone()
        if existing:
            return {'colId': existing['id'], 'created': False}
        max_pos = conn.execute(
            'SELECT COALESCE(MAX(position), -1) AS m FROM columns WHERE board_id=?',
            (board_id,),
        ).fetchone()['m']
        col_id = uid()
        conn.execute(
            "INSERT INTO columns (id, board_id, title, position, col_type) VALUES (?,?,?,?,?)",
            (col_id, board_id, 'Datenbank', max_pos + 1, 'db'),
        )
    return {'colId': col_id, 'created': True}


# ── Tables list ───────────────────────────────────────────────────────────────

@router.get('/{board_id}/db/tables')
def list_tables(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
    path = _board_db_path(board_id)
    if not os.path.exists(path):
        return []
    bconn = _board_conn(board_id)
    try:
        rows = bconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        result = []
        for r in rows:
            cnt = bconn.execute(f'SELECT COUNT(*) AS n FROM "{r["name"]}"').fetchone()['n']
            cols = [d[1] for d in bconn.execute(f'PRAGMA table_info("{r["name"]}")').fetchall()]
            result.append({'name': r['name'], 'rowCount': cnt, 'columns': cols})
        return result
    finally:
        bconn.close()


# ── CSV import ────────────────────────────────────────────────────────────────

class ImportCsvIn(BaseModel):
    tableName: str
    csv: str
    append: bool = False


@router.post('/{board_id}/db/import-csv')
def import_csv(board_id: str, body: ImportCsvIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    tname = _safe_tname(body.tableName)
    reader = csv.DictReader(io.StringIO(body.csv), delimiter=';')
    rows = list(reader)
    if not rows:
        raise HTTPException(400, 'CSV ist leer')
    cols = list(rows[0].keys())
    bconn = _board_conn(board_id)
    try:
        if not body.append:
            bconn.execute(f'DROP TABLE IF EXISTS "{tname}"')
            col_defs = ', '.join(f'"{c}" TEXT' for c in cols)
            bconn.execute(f'CREATE TABLE "{tname}" ({col_defs})')
        else:
            existing = [r[1] for r in bconn.execute(f'PRAGMA table_info("{tname}")').fetchall()]
            if existing and set(cols) != set(existing):
                raise HTTPException(400, f'Spalten stimmen nicht überein: {cols} vs {existing}')
        for row in rows:
            vals = [row.get(c, '') for c in cols]
            placeholders = ', '.join('?' * len(cols))
            bconn.execute(f'INSERT INTO "{tname}" VALUES ({placeholders})', vals)
        bconn.commit()
        return {'ok': True, 'rows': len(rows), 'cols': cols}
    finally:
        bconn.close()


# ── Data mutation ─────────────────────────────────────────────────────────────

class MutateIn(BaseModel):
    op: str  # delete_row | update_cell | clear_table
    table: str
    rowid: Optional[int] = None
    col: Optional[str] = None
    value: Optional[str] = None


@router.post('/{board_id}/db/mutate')
def mutate_db(board_id: str, body: MutateIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    tname = _safe_tname(body.table)
    bconn = _board_conn(board_id)
    try:
        if body.op == 'delete_row':
            if body.rowid is None:
                raise HTTPException(400, 'rowid erforderlich')
            bconn.execute(f'DELETE FROM "{tname}" WHERE rowid=?', (body.rowid,))
        elif body.op == 'update_cell':
            if body.rowid is None or not body.col:
                raise HTTPException(400, 'rowid und col erforderlich')
            col = re.sub(r'[^\w\s\-]', '', body.col).strip()
            bconn.execute(f'UPDATE "{tname}" SET "{col}"=? WHERE rowid=?', (body.value or '', body.rowid))
        elif body.op == 'clear_table':
            bconn.execute(f'DELETE FROM "{tname}"')
        else:
            raise HTTPException(400, 'Unbekannte Operation')
        bconn.commit()
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f'Fehler: {e}')
    finally:
        bconn.close()


# ── Column rename ─────────────────────────────────────────────────────────────

class RenameColumnIn(BaseModel):
    tableName: str
    oldName: str
    newName: str


@router.post('/{board_id}/db/rename-column')
def rename_column(board_id: str, body: RenameColumnIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    tname = _safe_tname(body.tableName)
    old_name = body.oldName.strip()
    new_name = body.newName.strip()
    if not old_name or not new_name:
        raise HTTPException(400, 'Spaltenname darf nicht leer sein')
    bconn = _board_conn(board_id)
    try:
        bconn.execute(f'ALTER TABLE "{tname}" RENAME COLUMN "{old_name}" TO "{new_name}"')
        bconn.commit()
        return {'ok': True}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        bconn.close()


# ── Query ─────────────────────────────────────────────────────────────────────

class QueryIn(BaseModel):
    sql: str
    subFilter: Optional[str] = None


MAX_ROWS = 100


@router.post('/{board_id}/db/query')
def run_query(board_id: str, body: QueryIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
    path = _board_db_path(board_id)
    if not os.path.exists(path):
        raise HTTPException(400, 'Keine Datenbank für dieses Board')
    sql = body.sql.strip().rstrip(';')
    if _WRITE_OPS.search(sql):
        raise HTTPException(400, 'Nur SELECT-Abfragen erlaubt')
    if body.subFilter:
        sf = body.subFilter.strip()
        if _WRITE_OPS.search(sf):
            raise HTTPException(400, 'Ungültiger Sub-Filter')
        sql = f'SELECT * FROM ({sql}) AS _q {sf}'
    bconn = _board_conn(board_id)
    try:
        bconn.execute('PRAGMA query_only=ON')
        cur = bconn.execute(sql)
        columns = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(fetched) > MAX_ROWS
        rows = [['' if v is None else str(v) for v in r] for r in fetched[:MAX_ROWS]]
        return {'columns': columns, 'rows': rows, 'truncated': truncated}
    except Exception as e:
        raise HTTPException(400, f'SQL-Fehler: {e}')
    finally:
        bconn.close()


# ── Board-Email-Konten ────────────────────────────────────────────────────────

_MAIL_TABLE_DDL = """CREATE TABLE IF NOT EXISTS email_accounts (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    smtp_host    TEXT NOT NULL,
    smtp_port    INTEGER NOT NULL DEFAULT 587,
    smtp_user    TEXT NOT NULL,
    smtp_pass    TEXT NOT NULL,
    use_starttls INTEGER NOT NULL DEFAULT 1,
    from_name    TEXT NOT NULL DEFAULT '',
    from_address TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now'))
)"""


def _ensure_mail_table(bconn: sqlite3.Connection) -> None:
    bconn.execute(_MAIL_TABLE_DDL)
    bconn.commit()


def _mail_send_one(acc: dict, to: str, subject: str, body_text: str, body_html: Optional[str] = None) -> None:
    msg = MIMEMultipart('alternative')
    from_str = f"{acc['from_name']} <{acc['from_address']}>" if acc['from_name'] else acc['from_address']
    msg['From'] = from_str
    msg['To'] = to
    msg['Subject'] = str(Header(subject, 'utf-8'))
    msg.attach(MIMEText(body_text or '', 'plain', 'utf-8'))
    if body_html:
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(acc['smtp_host'], int(acc['smtp_port']), timeout=20) as smtp:
        smtp.ehlo()
        if acc['use_starttls']:
            smtp.starttls(context=ctx)
            smtp.ehlo()
        smtp.login(acc['smtp_user'], acc['smtp_pass'])
        smtp.sendmail(acc['from_address'], [to], msg.as_string())


class MailAccountCreate(BaseModel):
    name: str
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    use_starttls: bool = True
    from_name: str = ''
    from_address: str


class MailMessageIn(BaseModel):
    to: str
    subject: str
    body_text: str
    body_html: Optional[str] = None


class MailSendBatchIn(BaseModel):
    account_id: str
    messages: List[MailMessageIn]


@router.get('/{board_id}/mail/accounts')
def list_board_mail_accounts(board_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
    bconn = _board_conn(board_id)
    try:
        _ensure_mail_table(bconn)
        rows = bconn.execute(
            'SELECT id, name, smtp_host, smtp_port, smtp_user, use_starttls, from_name, from_address '
            'FROM email_accounts ORDER BY name'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        bconn.close()


@router.post('/{board_id}/mail/accounts', status_code=201)
def create_board_mail_account(board_id: str, body: MailAccountCreate, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    if not body.name.strip() or not body.from_address.strip():
        raise HTTPException(400, 'Name und Absenderadresse erforderlich')
    bconn = _board_conn(board_id)
    try:
        _ensure_mail_table(bconn)
        aid = uid()
        bconn.execute(
            'INSERT INTO email_accounts '
            '(id,name,smtp_host,smtp_port,smtp_user,smtp_pass,use_starttls,from_name,from_address) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (aid, body.name.strip(), body.smtp_host.strip(), body.smtp_port,
             body.smtp_user.strip(), body.smtp_pass,
             1 if body.use_starttls else 0,
             body.from_name.strip(), body.from_address.strip()),
        )
        bconn.commit()
        return {'id': aid, 'name': body.name.strip()}
    finally:
        bconn.close()


@router.delete('/{board_id}/mail/accounts/{account_id}', status_code=204)
def delete_board_mail_account(board_id: str, account_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    bconn = _board_conn(board_id)
    try:
        _ensure_mail_table(bconn)
        bconn.execute('DELETE FROM email_accounts WHERE id=?', (account_id,))
        bconn.commit()
    finally:
        bconn.close()


@router.post('/{board_id}/mail/test-account')
def test_board_mail_account(board_id: str, body: dict, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    bconn = _board_conn(board_id)
    try:
        _ensure_mail_table(bconn)
        acc = bconn.execute('SELECT * FROM email_accounts WHERE id=?', (body.get('account_id'),)).fetchone()
        if not acc:
            raise HTTPException(404, 'Konto nicht gefunden')
        acc = dict(acc)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(acc['smtp_host'], int(acc['smtp_port']), timeout=10) as smtp:
            smtp.ehlo()
            if acc['use_starttls']:
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(acc['smtp_user'], acc['smtp_pass'])
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f'Verbindungsfehler: {e}')
    finally:
        bconn.close()


@router.post('/{board_id}/mail/send-batch')
def send_board_mail_batch(board_id: str, body: MailSendBatchIn, user_id: str = Depends(current_user_id)):
    if len(body.messages) > 500:
        raise HTTPException(400, 'Maximal 500 Nachrichten pro Batch')
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn)
    bconn = _board_conn(board_id)
    try:
        _ensure_mail_table(bconn)
        acc = bconn.execute('SELECT * FROM email_accounts WHERE id=?', (body.account_id,)).fetchone()
        if not acc:
            raise HTTPException(404, 'Konto nicht gefunden')
        acc = dict(acc)
        results = []
        for msg in body.messages:
            try:
                _mail_send_one(acc, msg.to, msg.subject, msg.body_text, msg.body_html)
                results.append({'to': msg.to, 'ok': True})
            except Exception as e:
                results.append({'to': msg.to, 'ok': False, 'error': str(e)})
        return {'sent': sum(1 for r in results if r['ok']), 'total': len(results), 'results': results}
    finally:
        bconn.close()
