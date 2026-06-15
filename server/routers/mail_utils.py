"""Shared email template engine for mkan.

Templates stored in email_templates table; hardcoded defaults used as fallback.
Variables substituted with str.format_map(vars_dict).
HTML generated from plain text (newlines → <br>, blank lines → <p>).
"""
import os
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from db import get_conn

_SMTP_HOST = os.environ.get('SMTP_HOST', '')
_SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
_SMTP_USER = os.environ.get('SMTP_USER', '')
_SMTP_PASS = os.environ.get('SMTP_PASS', '')
_SMTP_FROM = os.environ.get('SMTP_FROM', _SMTP_USER)
_MKAN_BASE = os.environ.get('MKAN_BASE_URL', 'https://mkan.milan.how')

DEFAULTS = {
    'welcome_board': {
        'subject': 'mkan – Deine Zugangsdaten',
        'body': (
            'Hallo {name},\n\n'
            'dein Account auf mkan wurde eingerichtet — du wurdest zum Board „{board_title}" hinzugefügt.\n\n'
            'Deine Zugangsdaten (Anmeldeadresse erhältst du separat):\n'
            '  E-Mail:   {email}\n'
            '  Passwort: {password}\n\n'
            'Bitte ändere dein Passwort nach der ersten Anmeldung.\n\n'
            'Viele Grüße'
        ),
    },
    'welcome_admin': {
        'subject': 'mkan – Deine Zugangsdaten',
        'body': (
            'Hallo {name},\n\n'
            'dein Account auf mkan wurde eingerichtet.\n\n'
            'Deine Zugangsdaten (Anmeldeadresse erhältst du separat):\n'
            '  E-Mail:   {email}\n'
            '  Passwort: {password}\n\n'
            'Bitte ändere dein Passwort nach der ersten Anmeldung.\n\n'
            'Viele Grüße'
        ),
    },
    'board_added': {
        'subject': 'mkan – Du wurdest zu „{board_title}" hinzugefügt',
        'body': (
            'Hallo {name},\n\n'
            'du hast jetzt Zugang zum Board „{board_title}" auf mkan.\n\n'
            'Meld dich an und wechsle in der Tab-Leiste zum Board:\n'
            '{url}\n\n'
            'Viele Grüße'
        ),
    },
    'invite_token': {
        'subject': 'mkan – Einladung zum Board-Anlegen',
        'body': (
            'Hallo{name_part},\n\n'
            'du bist eingeladen, dein eigenes Board auf mkan anzulegen —\n'
            'einem kollaborativen Tool für Aufgaben, Dateien und Notizen.\n\n'
            'Klick einfach auf diesen Link:\n'
            '{link}\n\n'
            'Der Link ist {expires_days} Tage gültig und kann nur einmal verwendet werden.\n\n'
            'Viele Grüße'
        ),
    },
    'url_reminder': {
        'subject': 'mkan – Anmeldeadresse',
        'body': (
            'Hallo {name},\n\n'
            'hier ist die Adresse, unter der du dich bei mkan anmelden kannst:\n\n'
            '{url}\n\n'
            'Zugangsdaten (E-Mail + Passwort) hast du separat erhalten.\n\n'
            'Viele Grüße'
        ),
    },
    'reset_link': {
        'subject': 'mkan – Passwort zurücksetzen',
        'body': (
            'Hallo {name},\n\n'
            'hier ist dein Link zum Zurücksetzen des Passworts:\n\n'
            '{link}\n\n'
            'Der Link ist 1 Stunde gültig.\n\n'
            'Viele Grüße'
        ),
    },
}

TEMPLATE_VARS = {
    'welcome_board':  '{name}, {board_title}, {email}, {password}',
    'welcome_admin':  '{name}, {email}, {password}',
    'board_added':    '{name}, {board_title}, {url}',
    'invite_token':   '{name_part}, {link}, {expires_days}',
    'url_reminder':   '{name}, {url}',
    'reset_link':     '{name}, {link}',
}


def get_template(key: str) -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute(
                'SELECT subject, body FROM email_templates WHERE key=?', (key,)
            ).fetchone()
        if row:
            return {'subject': row['subject'], 'body': row['body']}
    except Exception:
        pass
    return dict(DEFAULTS.get(key, {'subject': key, 'body': ''}))


def _text_to_html(text: str) -> str:
    paragraphs = text.strip().split('\n\n')
    parts = []
    for p in paragraphs:
        lines = p.replace('\n', '<br>')
        parts.append(f'<p style="margin:0 0 10px">{lines}</p>')
    return (
        '<div style="font-family:sans-serif;font-size:14px;color:#333;max-width:520px">'
        + ''.join(parts)
        + '</div>'
    )


def send_mail(to_email: str, template_key: str, vars_dict: dict) -> None:
    if not _SMTP_HOST:
        raise RuntimeError('SMTP nicht konfiguriert')
    tpl = get_template(template_key)
    try:
        subject = tpl['subject'].format_map(vars_dict)
        body_text = tpl['body'].format_map(vars_dict)
    except KeyError as e:
        raise RuntimeError(f'Template-Variable fehlt: {e}')
    body_html = _text_to_html(body_text)
    msg = MIMEMultipart('alternative')
    msg['From'] = _SMTP_FROM
    msg['To'] = to_email
    msg['Subject'] = str(Header(subject, 'utf-8'))
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo(); smtp.starttls(context=ctx); smtp.ehlo()
        smtp.login(_SMTP_USER, _SMTP_PASS)
        smtp.sendmail(_SMTP_FROM, [to_email], msg.as_string())
