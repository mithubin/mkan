import base64
import hashlib
import imaplib
import mimetypes
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import Header, decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user_id, require_admin
from db import get_conn, uid

router = APIRouter(prefix='/mail/composer')

# ── PIN-Session-Cache ──────────────────────────────────────────────────────────
# {account_id: (key_bytes, expires_at)}
_sessions: dict[str, tuple[bytes, datetime]] = {}
_SESSION_TTL = 30  # Minuten


def _set_session(account_id: str, key: bytes):
    _sessions[account_id] = (key, datetime.utcnow() + timedelta(minutes=_SESSION_TTL))


def _get_session(account_id: str) -> Optional[bytes]:
    entry = _sessions.get(account_id)
    if not entry:
        return None
    key, exp = entry
    if datetime.utcnow() > exp:
        del _sessions[account_id]
        return None
    return key


# ── Crypto ─────────────────────────────────────────────────────────────────────
# Ein Salt pro Account – gilt für SMTP- und IMAP-Passwort gleichermaßen.

def _derive_key(pin: str, salt_hex: str) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt_hex), 600_000)


def _make_account_key(pin: str) -> tuple[bytes, str]:
    """Neuen Salt + Key erzeugen (beim Anlegen/Ändern des Kontos)."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 600_000)
    return key, salt.hex()


def _encrypt_with_key(key: bytes, plaintext: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _decrypt_with_key(key: bytes, enc: str) -> str:
    data = base64.b64decode(enc)
    return AESGCM(key).decrypt(data[:12], data[12:], None).decode()


# ── DB-Hilfsfunktion ───────────────────────────────────────────────────────────

def _get_account(account_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM mail_composer_accounts WHERE id=?', (account_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, 'Konto nicht gefunden')
    return dict(row)


# ── Models ─────────────────────────────────────────────────────────────────────

class AccountIn(BaseModel):
    name: str
    from_name: str = ''
    from_address: str
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    use_starttls: bool = True
    imap_host: str
    imap_port: int = 993
    imap_user: str
    imap_pass: str
    sent_folder: str = 'Sent'
    is_default: bool = False
    pin: str


class SendIn(BaseModel):
    account_id: str
    to: str
    cc: str = ''
    bcc: str = ''
    subject: str
    body: str
    html_body: Optional[str] = None
    attachment_ids: list[str] = []
    pin: Optional[str] = None


class UnlockIn(BaseModel):
    account_id: str
    pin: str


# ── Routen ─────────────────────────────────────────────────────────────────────

@router.get('/accounts')
def list_accounts(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, name, from_name, from_address, smtp_host, smtp_port, smtp_user, '
            'use_starttls, imap_host, imap_port, imap_user, sent_folder, is_default '
            'FROM mail_composer_accounts ORDER BY name'
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['unlocked'] = _get_session(d['id']) is not None
        result.append(d)
    return result


@router.post('/accounts', status_code=201)
def create_account(body: AccountIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_admin(user_id, conn)
    pin = body.pin.strip()
    if len(pin) < 4:
        raise HTTPException(400, 'PIN mindestens 4 Zeichen')
    key, salt_hex = _make_account_key(pin)
    smtp_enc = _encrypt_with_key(key, body.smtp_pass)
    imap_enc = _encrypt_with_key(key, body.imap_pass)
    aid = uid()
    with get_conn() as conn:
        if body.is_default:
            conn.execute('UPDATE mail_composer_accounts SET is_default=0')
        conn.execute(
            'INSERT INTO mail_composer_accounts '
            '(id, name, from_name, from_address, smtp_host, smtp_port, smtp_user, '
            'smtp_pass_enc, smtp_pass_salt, use_starttls, '
            'imap_host, imap_port, imap_user, imap_pass_enc, imap_pass_salt, '
            'sent_folder, is_default) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (aid, body.name.strip(), body.from_name.strip(), body.from_address.strip(),
             body.smtp_host.strip(), body.smtp_port, body.smtp_user.strip(),
             smtp_enc, salt_hex, 1 if body.use_starttls else 0,
             body.imap_host.strip(), body.imap_port, body.imap_user.strip(),
             imap_enc, salt_hex, body.sent_folder.strip(),
             1 if body.is_default else 0),
        )
    return {'id': aid, 'name': body.name}


class AccountPatch(BaseModel):
    name: Optional[str] = None
    from_name: Optional[str] = None
    from_address: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    use_starttls: Optional[bool] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_user: Optional[str] = None
    imap_pass: Optional[str] = None
    sent_folder: Optional[str] = None
    is_default: Optional[bool] = None
    pin: Optional[str] = None       # neue PIN (Pflicht wenn Passwörter geändert werden)
    old_pin: Optional[str] = None   # alte PIN zur Verifikation


@router.patch('/accounts/{account_id}')
def update_account(account_id: str, body: AccountPatch, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_admin(user_id, conn)
    acc = _get_account(account_id)

    updates: list[tuple] = []

    # Felder ohne Crypto
    for field, col in [
        ('name', 'name'), ('from_name', 'from_name'), ('from_address', 'from_address'),
        ('smtp_host', 'smtp_host'), ('smtp_port', 'smtp_port'), ('smtp_user', 'smtp_user'),
        ('use_starttls', 'use_starttls'), ('imap_host', 'imap_host'), ('imap_port', 'imap_port'),
        ('imap_user', 'imap_user'), ('sent_folder', 'sent_folder'), ('is_default', 'is_default'),
    ]:
        val = getattr(body, field)
        if val is not None:
            if field == 'use_starttls':
                val = 1 if val else 0
            elif field == 'is_default':
                val = 1 if val else 0
            updates.append((col, val))

    # Passwörter neu verschlüsseln (neue PIN erforderlich)
    if body.smtp_pass is not None or body.imap_pass is not None or body.pin is not None:
        new_pin = (body.pin or '').strip()
        if len(new_pin) < 4:
            raise HTTPException(400, 'Neue PIN mindestens 4 Zeichen')
        # Alte Passwörter laden, falls nur eines geändert wird
        old_key = None
        if body.old_pin:
            try:
                old_key = _derive_key(body.old_pin.strip(), acc['smtp_pass_salt'])
                _decrypt_with_key(old_key, acc['smtp_pass_enc'])
            except Exception:
                raise HTTPException(403, 'Alte PIN falsch')
        elif body.smtp_pass is None or body.imap_pass is None:
            # Ohne alte PIN können wir bestehende Passwörter nicht umverschlüsseln
            raise HTTPException(400, 'Alte PIN erforderlich um bestehende Passwörter umzuschlüsseln')

        new_key, salt_hex = _make_account_key(new_pin)

        smtp_plain = body.smtp_pass if body.smtp_pass is not None else _decrypt_with_key(old_key, acc['smtp_pass_enc'])
        imap_plain = body.imap_pass if body.imap_pass is not None else _decrypt_with_key(old_key, acc['imap_pass_enc'])

        updates += [
            ('smtp_pass_enc', _encrypt_with_key(new_key, smtp_plain)),
            ('smtp_pass_salt', salt_hex),
            ('imap_pass_enc', _encrypt_with_key(new_key, imap_plain)),
            ('imap_pass_salt', salt_hex),
        ]
        _sessions.pop(account_id, None)  # Session invalidieren

    if not updates:
        return {'ok': True}

    set_clause = ', '.join(f'{col}=?' for col, _ in updates)
    vals = [v for _, v in updates]

    with get_conn() as conn:
        if any(col == 'is_default' for col, _ in updates):
            conn.execute('UPDATE mail_composer_accounts SET is_default=0')
        conn.execute(
            f'UPDATE mail_composer_accounts SET {set_clause} WHERE id=?',
            (*vals, account_id)
        )
    return {'ok': True}


@router.delete('/accounts/{account_id}', status_code=204)
def delete_account(account_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_admin(user_id, conn)
        conn.execute('DELETE FROM mail_composer_accounts WHERE id=?', (account_id,))
    _sessions.pop(account_id, None)


@router.post('/unlock')
def unlock(body: UnlockIn, user_id: str = Depends(current_user_id)):
    acc = _get_account(body.account_id)
    try:
        key = _derive_key(body.pin.strip(), acc['smtp_pass_salt'])
        _decrypt_with_key(key, acc['smtp_pass_enc'])  # PIN verifizieren
    except Exception:
        raise HTTPException(403, 'Falsche PIN')
    _set_session(body.account_id, key)
    return {'ok': True, 'expires_in': _SESSION_TTL * 60}


@router.post('/send')
def send_mail(body: SendIn, user_id: str = Depends(current_user_id)):
    acc = _get_account(body.account_id)

    # Schlüssel aus Session oder PIN
    key = _get_session(body.account_id)
    if not key:
        if not body.pin:
            raise HTTPException(403, 'PIN erforderlich')
        try:
            key = _derive_key(body.pin.strip(), acc['smtp_pass_salt'])
            _decrypt_with_key(key, acc['smtp_pass_enc'])  # verifizieren
            _set_session(body.account_id, key)
        except Exception:
            raise HTTPException(403, 'Falsche PIN')

    smtp_pass = _decrypt_with_key(key, acc['smtp_pass_enc'])
    imap_pass = _decrypt_with_key(key, acc['imap_pass_enc'])

    # Anhänge aus DB laden
    att_files = []
    if body.attachment_ids:
        with get_conn() as conn:
            for att_id in body.attachment_ids:
                row = conn.execute(
                    'SELECT filename, path, mime FROM attachments WHERE id=?', (att_id,)
                ).fetchone()
                if row and os.path.exists(row['path']):
                    att_files.append(dict(row))

    # MIME-Struktur: mixed wenn Anhänge, sonst alternative
    if att_files:
        msg = MIMEMultipart('mixed')
    else:
        msg = MIMEMultipart('alternative')

    from_str = f"{acc['from_name']} <{acc['from_address']}>" if acc['from_name'] else acc['from_address']
    msg['From'] = from_str
    msg['To'] = body.to
    if body.cc:
        msg['Cc'] = body.cc
    if body.bcc:
        msg['Bcc'] = body.bcc
    msg['Subject'] = str(Header(body.subject, 'utf-8'))

    # Text/HTML als alternative Sub-Part (bei mixed) oder direkt (bei alternative)
    if att_files:
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body.body, 'plain', 'utf-8'))
        if body.html_body:
            full_html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<style>body{font-family:sans-serif;font-size:14px;line-height:1.6;color:#222}'
                'a{color:#6366f1}pre,code{background:#f4f4f4;padding:2px 4px;border-radius:3px}'
                'blockquote{border-left:3px solid #ccc;margin:0;padding-left:1em;color:#666}</style>'
                f'</head><body>{body.html_body}</body></html>'
            )
            alt.attach(MIMEText(full_html, 'html', 'utf-8'))
        msg.attach(alt)
        for af in att_files:
            mime_type = af['mime'] or mimetypes.guess_type(af['filename'])[0] or 'application/octet-stream'
            main_type, sub_type = mime_type.split('/', 1)
            with open(af['path'], 'rb') as f:
                part = MIMEBase(main_type, sub_type)
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment',
                            filename=Header(af['filename'], 'utf-8').encode())
            msg.attach(part)
    else:
        msg.attach(MIMEText(body.body, 'plain', 'utf-8'))
        if body.html_body:
            full_html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<style>body{font-family:sans-serif;font-size:14px;line-height:1.6;color:#222}'
                'a{color:#6366f1}pre,code{background:#f4f4f4;padding:2px 4px;border-radius:3px}'
                'blockquote{border-left:3px solid #ccc;margin:0;padding-left:1em;color:#666}</style>'
                f'</head><body>{body.html_body}</body></html>'
            )
            msg.attach(MIMEText(full_html, 'html', 'utf-8'))

    recipients = [
        r.strip()
        for part in [body.to, body.cc, body.bcc]
        for r in part.split(',')
        if r.strip()
    ]

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(acc['smtp_host'], acc['smtp_port'], timeout=20) as smtp:
            smtp.ehlo()
            if acc['use_starttls']:
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(acc['smtp_user'], smtp_pass)
            smtp.sendmail(acc['from_address'], recipients, msg.as_bytes())
    except Exception as e:
        raise HTTPException(502, f'SMTP-Fehler: {e}')

    # Sent-Kopie via IMAP
    sent_error = None
    try:
        with imaplib.IMAP4_SSL(acc['imap_host'], acc['imap_port']) as M:
            M.login(acc['imap_user'], imap_pass)
            folder = acc['sent_folder'] or 'Sent'
            ok, data = M.select(f'"{folder}"')
            print(f'[IMAP] select "{folder}" -> {ok} {data}', flush=True)
            if ok != 'OK':
                # Ordner auflisten für Diagnose
                _, folders = M.list()
                print(f'[IMAP] available folders: {folders}', flush=True)
                for alt in ('Sent', 'Sent Items', 'Gesendet', 'INBOX.Sent', 'Sent Messages'):
                    ok, data = M.select(f'"{alt}"')
                    print(f'[IMAP] try "{alt}" -> {ok}', flush=True)
                    if ok == 'OK':
                        folder = alt
                        break
            res = M.append(folder, r'(\Seen)', None, msg.as_bytes())
            print(f'[IMAP] append -> {res}', flush=True)
    except Exception as e:
        sent_error = str(e)
        print(f'[IMAP] exception: {e}', flush=True)

    return {'ok': True, 'sent_copy': sent_error is None, 'sent_error': sent_error}


@router.get('/inbox/{account_id}')
def get_inbox(account_id: str, n: int = 20, user_id: str = Depends(current_user_id)):
    acc = _get_account(account_id)
    key = _get_session(account_id)
    if not key:
        raise HTTPException(403, 'PIN erforderlich')
    imap_pass = _decrypt_with_key(key, acc['imap_pass_enc'])

    def _dh(val: str) -> str:
        parts = decode_header(val or '')
        out = []
        for b, enc in parts:
            if isinstance(b, bytes):
                out.append(b.decode(enc or 'utf-8', errors='replace'))
            else:
                out.append(b)
        return ''.join(out)

    try:
        with imaplib.IMAP4_SSL(acc['imap_host'], acc['imap_port']) as M:
            M.login(acc['imap_user'], imap_pass)
            M.select('INBOX')
            _, data = M.search(None, 'ALL')
            ids = data[0].split()
            ids = ids[-n:] if len(ids) > n else ids
            headers = []
            for num in reversed(ids):
                _, msg_data = M.fetch(num, '(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])')
                h = message_from_bytes(msg_data[0][1])
                headers.append({
                    'from': _dh(h.get('From', '')),
                    'subject': _dh(h.get('Subject', '')),
                    'date': h.get('Date', ''),
                })
        return headers
    except Exception as e:
        raise HTTPException(502, f'IMAP-Fehler: {e}')
