import os
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SECRET = os.environ.get('SECRET_KEY', 'change-me-in-production-please')
ALGO = 'HS256'
EXPIRE_DAYS = 7
EXPIRE_DAYS_REMEMBER = 30

_bearer = HTTPBearer(auto_error=False)


def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_pw(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, remember: bool = False) -> str:
    days = EXPIRE_DAYS_REMEMBER if remember else EXPIRE_DAYS
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    return jwt.encode({'sub': user_id, 'exp': exp}, SECRET, algorithm=ALGO)


def _decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
        user_id = payload.get('sub')
        if not user_id:
            raise HTTPException(401, 'Invalid token')
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, 'Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(401, 'Invalid token')


def current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    token: str | None = None,
) -> str:
    # Accept token as query param (needed for <img src> and other browser-native requests)
    if creds:
        return _decode_token(creds.credentials)
    if token:
        return _decode_token(token)
    raise HTTPException(401, 'Not authenticated')


def current_user_id_from_query(token: str) -> str:
    """For SSE endpoint where Authorization header is not available."""
    if not token:
        raise HTTPException(401, 'Not authenticated')
    return _decode_token(token)


def require_board_access(board_id: str, user_id: str, conn, min_role: str = 'editor'):
    """Raises 403 if user does not have sufficient access to the board."""
    roles = {'viewer': 0, 'editor': 1, 'owner': 2}
    row = conn.execute(
        "SELECT role FROM board_members WHERE board_id=? AND user_id=?",
        (board_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(403, 'No access to this board')
    if roles.get(row['role'], 0) < roles.get(min_role, 0):
        raise HTTPException(403, 'Insufficient permissions')
    return row


def get_col_scope(user_id: str, board_id: str, conn) -> str | None:
    """Returns the col_id restriction for a board member, or None for full access."""
    row = conn.execute(
        'SELECT col_id, role FROM board_members WHERE board_id=? AND user_id=?',
        (board_id, user_id)
    ).fetchone()
    if not row or row['role'] == 'owner':
        return None
    return row['col_id'] or None


def get_global_role(user_id: str, conn) -> str:
    row = conn.execute('SELECT global_role FROM users WHERE id=?', (user_id,)).fetchone()
    return row['global_role'] if row else 'user'


def require_admin(user_id: str, conn):
    if get_global_role(user_id, conn) != 'admin':
        raise HTTPException(403, 'Admin-Rechte erforderlich')


def promote_admin_on_startup():
    """Set ADMIN_EMAIL user to global_role='admin' if not already."""
    email = os.environ.get('ADMIN_EMAIL', '').lower().strip()
    if not email:
        return
    from db import get_conn as _get_conn
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET global_role='admin' WHERE email=? AND global_role!='admin'",
            (email,),
        )
