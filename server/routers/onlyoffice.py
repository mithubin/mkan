import os, re, hashlib, urllib.request, uuid, logging
import jwt as pyjwt
logger = logging.getLogger('onlyoffice')
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import FileResponse

from db import get_conn, now
from auth import current_user_id, require_board_access
from routers.events import broadcast

router = APIRouter()

# Keys deren Callback keinen Save auslösen soll (Verwerfen-Modus)
_discard_keys: set = set()

OO_URL       = os.environ.get('OO_URL',       'http://localhost:80')
OO_MKAN_BASE = os.environ.get('OO_MKAN_BASE', 'http://localhost:7878')
OO_SECRET    = os.environ.get('OO_SECRET',    '')
OO_BACKEND   = os.environ.get('OO_BACKEND',   '')  # wenn gesetzt → Proxy aktiv, ooUrl = '/oo'

MIME_MAP = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ('word', 'docx'),
    'application/msword':                                                        ('word', 'doc'),
    'application/vnd.oasis.opendocument.text':                                  ('word', 'odt'),
    'text/plain':                                                                ('word', 'txt'),
    'application/rtf':                                                           ('word', 'rtf'),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':        ('cell', 'xlsx'),
    'application/vnd.ms-excel':                                                  ('cell', 'xls'),
    'application/vnd.oasis.opendocument.spreadsheet':                           ('cell', 'ods'),
    'text/csv':                                                                  ('cell', 'csv'),
    'application/vnd.openxmlformats-officedocument.presentationml.presentation':('slide','pptx'),
    'application/vnd.ms-powerpoint':                                             ('slide','ppt'),
    'application/vnd.oasis.opendocument.presentation':                          ('slide','odp'),
}
EXT_MAP = {
    'docx':('word','docx'),'doc':('word','doc'),'odt':('word','odt'),
    'txt':('word','txt'),'rtf':('word','rtf'),
    'xlsx':('cell','xlsx'),'xls':('cell','xls'),'ods':('cell','ods'),'csv':('cell','csv'),
    'pptx':('slide','pptx'),'ppt':('slide','ppt'),'odp':('slide','odp'),
}


@router.get('/attachments/{att_id}/onlyoffice-config')
def oo_config(att_id: str, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        att = conn.execute(
            'SELECT a.*, c.board_id FROM attachments a JOIN cards c ON a.card_id=c.id WHERE a.id=?',
            (att_id,)
        ).fetchone()
        if not att:
            raise HTTPException(404, 'Attachment not found')
        require_board_access(att['board_id'], user_id, conn, min_role='editor')
        user_row = conn.execute('SELECT name FROM users WHERE id=?', (user_id,)).fetchone()

    mime = att['mime'] or ''
    ext  = (att['filename'] or '').rsplit('.', 1)[-1].lower()
    doc_type, file_type = MIME_MAP.get(mime) or EXT_MAP.get(ext) or (None, None)
    if not doc_type:
        raise HTTPException(400, 'Dateityp wird von OnlyOffice nicht unterstützt')

    is_pdf = (doc_type == 'pdf')
    key = uuid.uuid4().hex[:20]  # immer frisch, kein OO-Cache
    cb  = f"{OO_MKAN_BASE}/attachments/{att_id}/onlyoffice-callback"
    params = []
    if OO_SECRET:
        params.append(f"secret={OO_SECRET}")
    if is_pdf:
        params.append('copy=1')
    if params:
        cb += '?' + '&'.join(params)

    doc_section: dict = {
        'fileType': file_type,
        'key': key,
        'title': att['filename'],
        'url': f"{OO_MKAN_BASE}/attachments/{att_id}/onlyoffice-download",
    }
    if is_pdf:
        doc_section['permissions'] = {
            'edit': True, 'comment': True, 'fillForms': True,
            'review': True, 'download': True, 'print': True,
        }

    config_dict = {
        'document': doc_section,
        'documentType': doc_type,
        'editorConfig': {
            'callbackUrl': cb,
            'mode': 'edit',
            'user': {'id': user_id, 'name': user_row['name'] if user_row else 'Nutzer'},
            'lang': 'de',
            'customization': {
                'autosave': True,
                'forcesave': False,
                'logo': {'visible': False},
            },
        },
    }

    result = {'ooUrl': '/oo' if OO_BACKEND else OO_URL, 'config': config_dict}
    if OO_SECRET:
        result['token'] = pyjwt.encode(config_dict, OO_SECRET, algorithm='HS256')
    return result


@router.get('/attachments/{att_id}/onlyoffice-download')
def oo_download(att_id: str, request: Request):
    logger.warning(f"OO download request from {request.client.host} for {att_id}")
    with get_conn() as conn:
        att = conn.execute('SELECT * FROM attachments WHERE id=?', (att_id,)).fetchone()
    if not att or not os.path.exists(att['path']):
        logger.warning(f"OO download: not found {att_id}")
        raise HTTPException(404)
    logger.warning(f"OO download: serving {att['filename']} ({att['mime']})")
    return FileResponse(att['path'], filename=att['filename'],
                        media_type=att['mime'] or 'application/octet-stream')


@router.post('/attachments/{att_id}/oo-discard')
async def oo_discard(att_id: str, request: Request, user_id: str = Depends(current_user_id)):
    body = await request.json()
    key = body.get('key', '')
    if key:
        _discard_keys.add(key)
    return {'ok': True}


@router.post('/attachments/{att_id}/onlyoffice-callback')
async def oo_callback(att_id: str, request: Request, secret: str = '', copy: int = 0):
    if OO_SECRET and secret != OO_SECRET:
        raise HTTPException(403, 'Invalid secret')

    body = await request.json()
    status = body.get('status')

    # 2 = bereit zum Speichern, 6 = Force-Save
    if status in (2, 6):
        doc_key = body.get('key', '')
        if doc_key in _discard_keys:
            _discard_keys.discard(doc_key)
            return {'error': 0}  # Session schließen, kein Save
        dl_url = body.get('url')
        if not dl_url:
            return {'error': 0}
        with get_conn() as conn:
            att = conn.execute(
                'SELECT a.*, c.board_id, c.id AS card_id FROM attachments a JOIN cards c ON a.card_id=c.id WHERE a.id=?',
                (att_id,)
            ).fetchone()
        if not att:
            return {'error': 1}
        try:
            with urllib.request.urlopen(dl_url, timeout=30) as resp:
                content = resp.read()
            if copy:
                # Annotierte Kopie anlegen, Original unverändert lassen
                base, ext = os.path.splitext(att['filename'])
                new_filename = f"{base}_annotiert{ext}"
                safe_name = re.sub(r'[^\w.\-]', '_', new_filename)[:100]
                new_id = str(uuid.uuid4())
                new_path = os.path.join(os.path.dirname(att['path']), f"{new_id}_{safe_name}")
                with open(new_path, 'wb') as f:
                    f.write(content)
                with get_conn() as conn:
                    row = conn.execute(
                        'SELECT COALESCE(MAX(position),0) AS mp FROM attachments WHERE card_id=?',
                        (att['card_id'],)
                    ).fetchone()
                    conn.execute(
                        'INSERT INTO attachments (id,card_id,filename,path,size,mime,created_at,position) '
                        'VALUES (?,?,?,?,?,?,?,?)',
                        (new_id, att['card_id'], new_filename, new_path,
                         len(content), att['mime'], now(), row['mp'] + 1)
                    )
            else:
                with open(att['path'], 'wb') as f:
                    f.write(content)
                with get_conn() as conn:
                    conn.execute('UPDATE attachments SET size=? WHERE id=?', (len(content), att_id))
            broadcast(att['board_id'], {
                'type': 'card_updated', 'boardId': att['board_id'], 'id': att['card_id']
            })
        except Exception as exc:
            logger.warning(f"OO callback error: {exc}")
            return {'error': 1}

    return {'error': 0}  # OO erwartet {"error": 0}
