import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user_id, require_admin, get_global_role, hash_pw
from db import get_conn, uid, now
from routers.mail_utils import send_mail, _MKAN_BASE, DEFAULTS, TEMPLATE_VARS

router = APIRouter(prefix='/admin')

_SMTP_HOST = os.environ.get('SMTP_HOST', '')


def _send_invite_mail(to_email: str, to_name: str, link: str, expires_days: int = 7) -> None:
    send_mail(to_email, 'invite_token', {
        'name_part': (' ' + to_name) if to_name else '',
        'link': link, 'expires_days': expires_days,
    })


def _check_admin(user_id: str):
    with get_conn() as conn:
        require_admin(user_id, conn)


# ── Users ─────────────────────────────────────────────────────────────────────

class CreateUserIn(BaseModel):
    name: str
    email: str
    password: str
    global_role: str = 'user'


@router.post('/users', status_code=201)
def admin_create_user(body: CreateUserIn, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    if len(body.password) < 8:
        raise HTTPException(400, 'Passwort zu kurz')
    email = body.email.lower().strip()
    name = body.name.strip()
    new_id = uid()
    with get_conn() as conn:
        if conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            raise HTTPException(409, 'E-Mail bereits vergeben')
        conn.execute(
            'INSERT INTO users (id, name, email, pw_hash, global_role, created_at) VALUES (?,?,?,?,?,?)',
            (new_id, name, email, hash_pw(body.password), body.global_role, now()),
        )
    mail_sent = False
    try:
        send_mail(email, 'welcome_admin', {'name': name, 'email': email, 'password': body.password})
        send_mail(email, 'url_reminder', {'name': name, 'url': _MKAN_BASE})
        mail_sent = True
    except Exception:
        pass
    return {'user_id': new_id, 'name': name, 'mail_sent': mail_sent}


@router.get('/users')
def list_users(user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, name, email, global_role, display_initials, badge_color, created_at FROM users ORDER BY name'
        ).fetchall()
    return [dict(r) for r in rows]


class SetRoleIn(BaseModel):
    global_role: str


@router.patch('/users/{target_id}/role')
def set_user_role(target_id: str, body: SetRoleIn, user_id: str = Depends(current_user_id)):
    if body.global_role not in ('admin', 'user', 'viewer'):
        raise HTTPException(400, 'Ungültige Rolle')
    with get_conn() as conn:
        require_admin(user_id, conn)
        if not conn.execute('SELECT id FROM users WHERE id=?', (target_id,)).fetchone():
            raise HTTPException(404, 'Nutzer nicht gefunden')
        conn.execute('UPDATE users SET global_role=? WHERE id=?', (body.global_role, target_id))
    return {'ok': True}


class SetBadgeColorIn(BaseModel):
    badge_color: str


@router.patch('/users/{target_id}/badge-color')
def set_badge_color(target_id: str, body: SetBadgeColorIn, user_id: str = Depends(current_user_id)):
    if not re.match(r'^#[0-9a-fA-F]{6}$', body.badge_color or ''):
        raise HTTPException(400, 'Ungültige Farbe')
    with get_conn() as conn:
        require_admin(user_id, conn)
        if not conn.execute('SELECT id FROM users WHERE id=?', (target_id,)).fetchone():
            raise HTTPException(404, 'Nutzer nicht gefunden')
        conn.execute('UPDATE users SET badge_color=? WHERE id=?', (body.badge_color, target_id))
    return {'badge_color': body.badge_color}


class SetInitialsIn(BaseModel):
    display_initials: str


@router.patch('/users/{target_id}/initials')
def set_user_initials(target_id: str, body: SetInitialsIn, user_id: str = Depends(current_user_id)):
    val = body.display_initials.strip()[:2].upper() if body.display_initials.strip() else None
    with get_conn() as conn:
        require_admin(user_id, conn)
        if not conn.execute('SELECT id FROM users WHERE id=?', (target_id,)).fetchone():
            raise HTTPException(404, 'Nutzer nicht gefunden')
        conn.execute('UPDATE users SET display_initials=? WHERE id=?', (val, target_id))
    return {'display_initials': val}


# ── Boards overview ───────────────────────────────────────────────────────────

@router.get('/boards')
def list_all_boards(user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        rows = conn.execute(
            '''SELECT b.id, b.title, b.created_at, u.name AS owner_name, u.email AS owner_email
               FROM boards b JOIN users u ON b.owner_id = u.id
               ORDER BY b.created_at DESC'''
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete('/boards/{board_id}')
def admin_delete_board(board_id: str, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        if not conn.execute('SELECT id FROM boards WHERE id=?', (board_id,)).fetchone():
            raise HTTPException(404, 'Board nicht gefunden')
        conn.execute('DELETE FROM boards WHERE id=?', (board_id,))
    return {'ok': True}


@router.delete('/users/{target_id}')
def admin_delete_user(target_id: str, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    if target_id == user_id:
        raise HTTPException(400, 'Eigenen Account nicht löschbar')
    with get_conn() as conn:
        u = conn.execute('SELECT id, name FROM users WHERE id=?', (target_id,)).fetchone()
        if not u:
            raise HTTPException(404, 'Nutzer nicht gefunden')
        # Delete all boards owned by this user (cascades to cards, columns, etc.)
        conn.execute('DELETE FROM boards WHERE owner_id=?', (target_id,))
        # Remove from all board memberships
        conn.execute('DELETE FROM board_members WHERE user_id=?', (target_id,))
        # Delete the user account
        conn.execute('DELETE FROM users WHERE id=?', (target_id,))
    return {'ok': True, 'name': u['name']}


# ── Board invite tokens ───────────────────────────────────────────────────────

class InviteTokenIn(BaseModel):
    send_to_email: Optional[str] = None
    send_to_name: Optional[str] = None


@router.post('/invite-token')
def create_invite_token(body: InviteTokenIn = InviteTokenIn(), user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    token = uuid.uuid4().hex
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO board_invite_tokens (token, created_by, expires_at) VALUES (?,?,?)',
            (token, user_id, expires),
        )
    link = f'{_MKAN_BASE}/?board_invite={token}'
    mail_sent = False
    if body.send_to_email and _SMTP_HOST:
        try:
            _send_invite_mail(body.send_to_email, body.send_to_name or '', link)
            mail_sent = True
        except Exception as e:
            pass
    return {
        'token': token,
        'expires_at': expires,
        'link': link,
        'mail_sent': mail_sent,
    }


@router.get('/invite-tokens')
def list_invite_tokens(user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        rows = conn.execute(
            '''SELECT t.token, t.expires_at, u.name AS created_by_name
               FROM board_invite_tokens t JOIN users u ON t.created_by = u.id
               ORDER BY t.expires_at DESC'''
        ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    result = []
    for r in rows:
        d = dict(r)
        d['expired'] = d['expires_at'] < now
        d['link'] = f'{_MKAN_BASE}/?board_invite={d["token"]}'
        result.append(d)
    return result


@router.delete('/invite-tokens/{token}')
def delete_invite_token(token: str, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        conn.execute('DELETE FROM board_invite_tokens WHERE token=?', (token,))
    return {'ok': True}


# ── Email Templates ───────────────────────────────────────────────────────────

@router.get('/email-templates')
def list_email_templates(user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    result = []
    for key, default in DEFAULTS.items():
        with get_conn() as conn:
            row = conn.execute('SELECT subject, body FROM email_templates WHERE key=?', (key,)).fetchone()
        entry = {
            'key': key,
            'subject': row['subject'] if row else default['subject'],
            'body': row['body'] if row else default['body'],
            'customized': bool(row),
            'vars': TEMPLATE_VARS.get(key, ''),
        }
        result.append(entry)
    return result


class TemplatePatch(BaseModel):
    subject: str
    body: str


@router.patch('/email-templates/{key}')
def save_email_template(key: str, body: TemplatePatch, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    if key not in DEFAULTS:
        raise HTTPException(404, 'Unbekanntes Template')
    with get_conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO email_templates (key, subject, body) VALUES (?,?,?)',
            (key, body.subject.strip(), body.body.strip()),
        )
    return {'ok': True}


@router.delete('/email-templates/{key}')
def reset_email_template(key: str, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    with get_conn() as conn:
        conn.execute('DELETE FROM email_templates WHERE key=?', (key,))
    return {'ok': True}


class TestMailIn(BaseModel):
    key: str
    to_email: str


@router.post('/email-templates/test')
def test_email_template(body: TestMailIn, user_id: str = Depends(current_user_id)):
    _check_admin(user_id)
    dummy = {
        'name': 'Testperson', 'email': body.to_email, 'password': 'Geheim123',
        'board_title': 'Mein Testboard', 'url': _MKAN_BASE,
        'link': _MKAN_BASE + '/?reset_token=TESTTOKEN', 'expires_days': 7,
        'name_part': ' Testperson',
    }
    try:
        send_mail(body.to_email, body.key, dummy)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {'ok': True}
