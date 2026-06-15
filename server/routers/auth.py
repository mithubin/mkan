import json
import os
import smtplib
import ssl
import uuid
from datetime import datetime, timezone, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any

from db import get_conn, uid, now
from auth import hash_pw, verify_pw, create_token, current_user_id

_SMTP_HOST = os.environ.get('SMTP_HOST', '')
_SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
_SMTP_USER = os.environ.get('SMTP_USER', '')
_SMTP_PASS = os.environ.get('SMTP_PASS', '')
_SMTP_FROM = os.environ.get('SMTP_FROM', _SMTP_USER)
_MKAN_BASE = os.environ.get('MKAN_BASE_URL', 'https://mkan.milan.how')


def _send_reset_mail(to_email: str, token: str) -> None:
    link = f'{_MKAN_BASE}/?reset_token={token}'
    body_text = f'Passwort zurücksetzen:\n{link}\n\nDer Link ist 1 Stunde gültig.'
    body_html = (
        f'<p>Passwort zurücksetzen:</p>'
        f'<p><a href="{link}">{link}</a></p>'
        f'<p>Der Link ist 1 Stunde gültig.</p>'
    )
    msg = MIMEMultipart('alternative')
    msg['From'] = _SMTP_FROM
    msg['To'] = to_email
    msg['Subject'] = str(Header('mkan – Passwort zurücksetzen', 'utf-8'))
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        smtp.login(_SMTP_USER, _SMTP_PASS)
        smtp.sendmail(_SMTP_FROM, [to_email], msg.as_string())

router = APIRouter()


class RegisterIn(BaseModel):
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str
    remember: bool = False


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


@router.post('/register', status_code=201)
def register(body: RegisterIn):
    if len(body.password) < 8:
        raise HTTPException(400, 'Password must be at least 8 characters')
    with get_conn() as conn:
        if conn.execute('SELECT id FROM users WHERE email=?', (body.email.lower().strip(),)).fetchone():
            raise HTTPException(409, 'Email already registered')
        user_id = uid()
        conn.execute(
            'INSERT INTO users (id, name, email, pw_hash, created_at) VALUES (?,?,?,?,?)',
            (user_id, body.name.strip(), body.email.lower().strip(), hash_pw(body.password), now()),
        )
    return {'token': create_token(user_id), 'user_id': user_id, 'name': body.name.strip()}


@router.post('/login')
def login(body: LoginIn):
    with get_conn() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE email=?', (body.email.lower().strip(),)
        ).fetchone()
    if not user or not verify_pw(body.password, user['pw_hash']):
        raise HTTPException(401, 'Invalid credentials')
    return {'token': create_token(user['id'], remember=body.remember), 'user_id': user['id'], 'name': user['name']}


@router.post('/change-password')
def change_password(body: PasswordChangeIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user or not verify_pw(body.old_password, user['pw_hash']):
            raise HTTPException(401, 'Wrong current password')
        if len(body.new_password) < 8:
            raise HTTPException(400, 'New password must be at least 8 characters')
        conn.execute(
            'UPDATE users SET pw_hash=? WHERE id=?',
            (hash_pw(body.new_password), user_id),
        )
    return {'ok': True}


@router.get('/me')
def me(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        user = conn.execute('SELECT id, name, email, global_role, display_initials FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, 'User not found')
    return dict(user)


class InitialsIn(BaseModel):
    display_initials: str


@router.patch('/me/initials')
def set_my_initials(body: InitialsIn, user_id: str = Depends(current_user_id)):
    val = body.display_initials.strip()[:2].upper() if body.display_initials.strip() else None
    with get_conn() as conn:
        conn.execute('UPDATE users SET display_initials=? WHERE id=?', (val, user_id))
    return {'display_initials': val}


@router.get('/users')
def list_users_public(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        rows = conn.execute('SELECT id, name, email FROM users ORDER BY name').fetchall()
    return [dict(r) for r in rows]


@router.get('/me/prefs')
def get_prefs(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        row = conn.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()
    try:
        return json.loads(row['settings'] or '{}')
    except Exception:
        return {}


@router.put('/me/prefs')
def put_prefs(body: dict[str, Any], user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        conn.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(body), user_id))
    return {}


class ResetRequestIn(BaseModel):
    email: str


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


@router.post('/request-reset')
def request_reset(body: ResetRequestIn):
    if not _SMTP_HOST:
        raise HTTPException(503, 'E-Mail-Versand nicht konfiguriert')
    with get_conn() as conn:
        user = conn.execute(
            'SELECT id, email FROM users WHERE email=?', (body.email.lower().strip(),)
        ).fetchone()
        if user:
            token = uuid.uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            conn.execute('DELETE FROM pw_reset_tokens WHERE user_id=?', (user['id'],))
            conn.execute(
                'INSERT INTO pw_reset_tokens (token, user_id, expires_at) VALUES (?,?,?)',
                (token, user['id'], expires),
            )
            try:
                _send_reset_mail(user['email'], token)
            except Exception:
                pass
    return {'ok': True}


@router.post('/reset-password')
def reset_password(body: ResetPasswordIn):
    if len(body.new_password) < 8:
        raise HTTPException(400, 'Passwort muss mindestens 8 Zeichen haben')
    with get_conn() as conn:
        row = conn.execute(
            'SELECT user_id, expires_at FROM pw_reset_tokens WHERE token=?', (body.token,)
        ).fetchone()
        if not row:
            raise HTTPException(400, 'Ungültiger oder abgelaufener Link')
        expires = datetime.fromisoformat(row['expires_at'])
        if datetime.now(timezone.utc) > expires:
            conn.execute('DELETE FROM pw_reset_tokens WHERE token=?', (body.token,))
            raise HTTPException(400, 'Link abgelaufen')
        conn.execute(
            'UPDATE users SET pw_hash=? WHERE id=?',
            (hash_pw(body.new_password), row['user_id']),
        )
        conn.execute('DELETE FROM pw_reset_tokens WHERE token=?', (body.token,))
    return {'ok': True}
