import json
import os
import uuid
import xml.etree.ElementTree as ET
import urllib.request
from typing import Literal

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from auth import current_user_id, require_board_access
from db import get_conn

router = APIRouter()

_OO_INTERNAL = 'http://onlyoffice'
_OO_MKAN_BASE = os.environ.get('OO_MKAN_BASE', 'http://mkan:8000')
_OO_SECRET = os.environ.get('OO_SECRET', '')

# In-memory one-time HTML store (OO fetches and we pop it)
_TEMP: dict[str, str] = {}

_MIME = {
    'pdf':  'application/pdf',
    'odt':  'application/vnd.oasis.opendocument.text',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}


@router.get('/convert/temp/{token}')
def serve_temp(token: str):
    """OO fetches the HTML to convert from here (internal Docker network, no auth)."""
    html = _TEMP.pop(token, None)
    if html is None:
        raise HTTPException(404, 'Nicht gefunden oder bereits abgerufen')
    return Response(html, media_type='text/html; charset=utf-8')


class ConvertIn(BaseModel):
    html: str
    outputtype: Literal['pdf', 'odt', 'docx'] = 'pdf'


def _oo_convert(html: str, outputtype: str) -> bytes:
    token = uuid.uuid4().hex
    _TEMP[token] = html
    src_url = f'{_OO_MKAN_BASE}/convert/temp/{token}'

    payload: dict = {
        'async': False,
        'filetype': 'html',
        'outputtype': outputtype,
        'key': uuid.uuid4().hex[:20],
        'url': src_url,
    }
    if _OO_SECRET:
        payload['token'] = pyjwt.encode(payload.copy(), _OO_SECRET, algorithm='HS256')

    req = urllib.request.Request(
        f'{_OO_INTERNAL}/ConvertService.ashx',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        xml_data = resp.read()
    except Exception as e:
        _TEMP.pop(token, None)
        raise HTTPException(502, f'OnlyOffice nicht erreichbar: {e}')

    try:
        root = ET.fromstring(xml_data)
        err = root.findtext('Error')
        if err and err.strip() not in ('0', ''):
            raise HTTPException(502, f'OnlyOffice Fehler {err.strip()}')
        file_url = root.findtext('FileUrl')
        if not file_url:
            raise HTTPException(502, 'Keine Datei-URL in OO-Antwort')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'OO-Antwort nicht parsebar: {e}')

    try:
        return urllib.request.urlopen(file_url, timeout=30).read()
    except Exception as e:
        raise HTTPException(502, f'Fehler beim Laden des konvertierten Dokuments: {e}')


@router.post('/boards/{board_id}/convert')
def convert_doc(board_id: str, body: ConvertIn, user_id: str = Depends(current_user_id)):
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')
    if not body.html.strip():
        raise HTTPException(400, 'Kein HTML-Inhalt')
    data = _oo_convert(body.html, body.outputtype)
    mime = _MIME.get(body.outputtype, 'application/octet-stream')
    return Response(
        content=data,
        media_type=mime,
        headers={'Content-Disposition': f'attachment; filename="serie.{body.outputtype}"'},
    )
