import hashlib
import os
import re
import time
import bcrypt

from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection
from wsgidav.dc.base_dc import BaseDomainController
from wsgidav.lock_man.lock_storage import LockStorageDict

from db import get_conn

UPLOAD_PATH = os.environ.get('UPLOAD_PATH', './data/uploads')


def _safe(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '_', s or 'untitled')


def _safe_dir(s: str) -> str:
    # No dots — directory names must not look like files to WebDAV clients/file managers
    return re.sub(r'[^A-Za-z0-9_-]', '_', s or 'untitled')


def _card_label(card) -> str:
    return f"{card['id'][:8]}_{_safe_dir(card['title'])}"


# ── Auth ────────────────────────────────────────────────────────────────────

_auth_cache: dict = {}  # {(email, sha256(pw)): (ok, expires)}
_AUTH_TTL = 300         # 5 min — bcrypt is slow, cache the result


def _cached_auth(email: str, password: str) -> bool:
    key = (email, hashlib.sha256(password.encode()).hexdigest())
    now = time.time()
    if key in _auth_cache:
        ok, expires = _auth_cache[key]
        if expires > now:
            return ok
    with get_conn() as conn:
        row = conn.execute('SELECT pw_hash FROM users WHERE email=?', (email,)).fetchone()
    ok = bool(row and bcrypt.checkpw(password.encode(), row['pw_hash'].encode()))
    _auth_cache[key] = (ok, now + _AUTH_TTL)
    if len(_auth_cache) > 200:
        _auth_cache.clear()
    return ok


class MkanDomainController(BaseDomainController):
    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)

    def get_domain_realm(self, path_info, environ):
        return 'mkan'

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        return _cached_auth(user_name, password)


# ── DB helpers ──────────────────────────────────────────────────────────────

def _user_id_for_email(email):
    with get_conn() as conn:
        row = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        return row['id'] if row else None


def _user_boards(user_id):
    with get_conn() as conn:
        return conn.execute(
            '''SELECT b.id, b.title FROM boards b
               JOIN board_members bm ON bm.board_id=b.id
               WHERE bm.user_id=?
               ORDER BY b.title''', (user_id,)
        ).fetchall()


def _cards_with_attachments(board_id):
    with get_conn() as conn:
        return conn.execute(
            '''SELECT DISTINCT c.id, c.title FROM cards c
               JOIN attachments a ON a.card_id=c.id
               WHERE c.board_id=?
               ORDER BY c.title''', (board_id,)
        ).fetchall()


def _attachments(card_id):
    with get_conn() as conn:
        return conn.execute(
            '''SELECT id, filename, path, size, mime, created_at
               FROM attachments WHERE card_id=? ORDER BY created_at''',
            (card_id,)
        ).fetchall()


# ── Virtual filesystem ───────────────────────────────────────────────────────

def _uid(environ):
    email = environ.get('wsgidav.auth.user_name')
    return _user_id_for_email(email) if email else None


DAV_PREFIX = '/dav'


class RootCollection(DAVCollection):
    def get_member_names(self):
        uid = _uid(self.environ)
        if not uid:
            return []
        return [_safe(b['title']) for b in _user_boards(uid)]

    def get_member(self, name):
        uid = _uid(self.environ)
        if not uid:
            return None
        for b in _user_boards(uid):
            if _safe(b['title']) == name:
                return BoardCollection(f'{DAV_PREFIX}/{name}/', self.environ, b['id'])
        return None


class BoardCollection(DAVCollection):
    def __init__(self, path, environ, board_id):
        super().__init__(path, environ)
        self._board_id = board_id

    def get_member_names(self):
        return [_card_label(c) for c in _cards_with_attachments(self._board_id)]

    def get_member(self, name):
        for c in _cards_with_attachments(self._board_id):
            if _card_label(c) == name:
                return CardCollection(f'{self.path}{name}/', self.environ, c['id'])
        return None


class CardCollection(DAVCollection):
    def __init__(self, path, environ, card_id):
        super().__init__(path, environ)
        self._card_id = card_id

    def get_member_names(self):
        return [a['filename'] for a in _attachments(self._card_id)]

    def get_member(self, name):
        for a in _attachments(self._card_id):
            if a['filename'] == name:
                return AttachmentResource(f'{self.path}{name}', self.environ, dict(a))
        return None


class AttachmentResource(DAVNonCollection):
    def __init__(self, path, environ, att):
        super().__init__(path, environ)
        self._att = att

    def get_content_length(self):
        try:
            return os.path.getsize(self._att['path'])
        except OSError:
            return 0

    def get_content_type(self):
        return self._att['mime'] or 'application/octet-stream'

    def get_last_modified(self):
        try:
            return os.path.getmtime(self._att['path'])
        except OSError:
            return None

    def support_etag(self):
        return True

    def get_etag(self):
        return self._att['id']

    def get_content(self):
        return open(self._att['path'], 'rb')

    def begin_write(self, content_type=None):
        return open(self._att['path'], 'wb')

    def end_write(self, with_errors):
        if with_errors:
            return
        new_size = 0
        try:
            new_size = os.path.getsize(self._att['path'])
        except OSError:
            pass
        with get_conn() as conn:
            conn.execute(
                'UPDATE attachments SET size=? WHERE id=?',
                (new_size, self._att['id'])
            )
            conn.execute(
                """UPDATE cards SET updated_at=datetime('now')
                   WHERE id=(SELECT card_id FROM attachments WHERE id=?)""",
                (self._att['id'],)
            )


class MkanProvider(DAVProvider):
    def get_resource_inst(self, path, environ):
        uid = _uid(environ)
        # path arrives stripped of /dav by Starlette mount (e.g. '/' or '/Board/card/')
        parts = [p for p in path.strip('/').split('/') if p]

        if not parts:
            return RootCollection(f'{DAV_PREFIX}/', environ)

        boards = _user_boards(uid) if uid else []
        board = next((b for b in boards if _safe(b['title']) == parts[0]), None)
        if not board:
            return None
        board_path = f'{DAV_PREFIX}/{parts[0]}/'

        if len(parts) == 1:
            return BoardCollection(board_path, environ, board['id'])

        cards = _cards_with_attachments(board['id'])
        card = next((c for c in cards if _card_label(c) == parts[1]), None)
        if not card:
            return None
        card_path = f'{board_path}{parts[1]}/'

        if len(parts) == 2:
            return CardCollection(card_path, environ, card['id'])

        atts = _attachments(card['id'])
        att = next((a for a in atts if a['filename'] == parts[2]), None)
        if not att:
            return None
        return AttachmentResource(f'{card_path}{parts[2]}', environ, dict(att))


# ── App factory ─────────────────────────────────────────────────────────────

def get_dav_app():
    config = {
        'provider_mapping': {'/': MkanProvider()},
        'http_authenticator': {
            'domain_controller': MkanDomainController,
            'accept_basic': True,
            'accept_digest': False,
            'default_to_digest': False,
        },
        'lock_storage': LockStorageDict(),
        'verbose': 0,
    }
    inner = WsgiDAVApp(config)

    def app(environ, start_response):
        environ['wsgi.url_scheme'] = 'https'
        environ['SERVER_NAME'] = 'mkan.milan.how'
        environ['SERVER_PORT'] = '443'
        return inner(environ, start_response)

    return app
