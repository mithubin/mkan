import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user_id
from db import get_conn, uid

router = APIRouter()


class AccountCreate(BaseModel):
    name: str
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    use_starttls: bool = True
    from_name: str = ''
    from_address: str


class MessageIn(BaseModel):
    to: str
    subject: str
    body_text: str
    body_html: Optional[str] = None


class SendBatchIn(BaseModel):
    account_id: str
    messages: List[MessageIn]


@router.get('/mail/accounts')
def list_accounts(user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, name, smtp_host, smtp_port, smtp_user, use_starttls, from_name, from_address '
            'FROM email_accounts ORDER BY name'
        ).fetchall()
    return [dict(r) for r in rows]


@router.post('/mail/accounts', status_code=201)
def create_account(body: AccountCreate, user_id: str = Depends(current_user_id)):
    name = body.name.strip()
    if not name or not body.from_address.strip():
        raise HTTPException(400, 'Name und Absenderadresse erforderlich')
    with get_conn() as conn:
        aid = uid()
        conn.execute(
            'INSERT INTO email_accounts '
            '(id,name,smtp_host,smtp_port,smtp_user,smtp_pass,use_starttls,from_name,from_address) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (aid, name, body.smtp_host.strip(), body.smtp_port,
             body.smtp_user.strip(), body.smtp_pass,
             1 if body.use_starttls else 0,
             body.from_name.strip(), body.from_address.strip()),
        )
    return {'id': aid, 'name': name}


@router.delete('/mail/accounts/{account_id}', status_code=204)
def delete_account(account_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        conn.execute('DELETE FROM email_accounts WHERE id=?', (account_id,))


@router.post('/mail/test-account')
def test_account(body: dict, user_id: str = Depends(current_user_id)):
    account_id = body.get('account_id')
    with get_conn() as conn:
        acc = conn.execute('SELECT * FROM email_accounts WHERE id=?', (account_id,)).fetchone()
    if not acc:
        raise HTTPException(404, 'Konto nicht gefunden')
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(acc['smtp_host'], int(acc['smtp_port']), timeout=10) as smtp:
            smtp.ehlo()
            if acc['use_starttls']:
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(acc['smtp_user'], acc['smtp_pass'])
        return {'ok': True}
    except Exception as e:
        raise HTTPException(400, f'Verbindungsfehler: {e}')


@router.post('/mail/send-batch')
def send_batch(body: SendBatchIn, user_id: str = Depends(current_user_id)):
    if len(body.messages) > 500:
        raise HTTPException(400, 'Maximal 500 Nachrichten pro Batch')
    with get_conn() as conn:
        acc = conn.execute('SELECT * FROM email_accounts WHERE id=?', (body.account_id,)).fetchone()
    if not acc:
        raise HTTPException(404, 'Konto nicht gefunden')
    results = []
    for msg in body.messages:
        try:
            _send_one(acc, msg.to, msg.subject, msg.body_text, msg.body_html)
            results.append({'to': msg.to, 'ok': True})
        except Exception as e:
            results.append({'to': msg.to, 'ok': False, 'error': str(e)})
    return {'sent': sum(1 for r in results if r['ok']), 'total': len(results), 'results': results}


def _send_one(acc, to, subject, body_text, body_html=None):
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
