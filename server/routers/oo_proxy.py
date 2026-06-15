import os, asyncio, logging
import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger('oo_proxy')
router = APIRouter()

OO_BACKEND    = os.environ.get('OO_BACKEND',    '')  # e.g. http://localhost:8088
OO_PUBLIC_URL = os.environ.get('OO_PUBLIC_URL', '')  # URL die der Browser für OO-Ressourcen nutzt, z.B. http://localhost:7879/oo

if not OO_BACKEND:
    logger.warning('OO_BACKEND not set – /oo proxy inactive')

_ANALYTICS_STUB = b"if(window.Common===undefined)window.Common={};Common.component=Common.component||{};Common.Analytics=Common.component.Analytics={initialize:function(){},trackEvent:function(){}};"
_ANALYTICS_STUB_TAG = b'<head><script>' + _ANALYTICS_STUB + b'</script>'


def _oo_ws_backend():
    return OO_BACKEND.replace('https://', 'wss://').replace('http://', 'ws://')


def _rewrite_location(loc: str) -> str:
    if loc.startswith(OO_BACKEND):
        return '/oo' + loc[len(OO_BACKEND):]
    if loc.startswith('/') and not loc.startswith('/oo'):
        return '/oo' + loc
    return loc


async def _proxy_http(oo_path: str, request: Request) -> Response:
    if not OO_BACKEND:
        return Response('OO_BACKEND not configured', status_code=503)
    qs = request.url.query
    url = f"{OO_BACKEND}/{oo_path}" + (f"?{qs}" if qs else "")
    skip = {'host', 'content-length', 'transfer-encoding', 'referer'}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
        body = await request.body()
        resp = await client.request(request.method, url, headers=headers, content=body)
    out_headers = {}
    for k, v in resp.headers.items():
        kl = k.lower()
        if kl in ('content-encoding', 'transfer-encoding', 'content-length'):
            continue
        if kl == 'location':
            out_headers[k] = _rewrite_location(v)
            continue
        out_headers[k] = v
    content = resp.content
    if resp.status_code == 200 and 'index.html' in oo_path and b'<head>' in content:
        content = content.replace(b'<head>', _ANALYTICS_STUB_TAG, 1)
    return Response(content=content, status_code=resp.status_code, headers=out_headers)


async def _proxy_ws(oo_path: str, ws_client: WebSocket):
    if not OO_BACKEND:
        await ws_client.close(code=1011)
        return
    qs = ws_client.url.query
    target = f"{_oo_ws_backend()}/{oo_path}" + (f"?{qs}" if qs else "")
    await ws_client.accept(subprotocol=ws_client.headers.get('sec-websocket-protocol'))
    try:
        extra = {k: v for k, v in ws_client.headers.items()
                 if k.lower() not in ('host', 'upgrade', 'connection',
                                      'sec-websocket-key', 'sec-websocket-version',
                                      'sec-websocket-extensions', 'sec-websocket-protocol')}
        async with websockets.connect(target, extra_headers=extra) as ws_server:
            async def up():
                try:
                    while True:
                        msg = await ws_client.receive()
                        if msg['type'] == 'websocket.disconnect':
                            break
                        if 'bytes' in msg and msg['bytes'] is not None:
                            await ws_server.send(msg['bytes'])
                        elif 'text' in msg and msg['text'] is not None:
                            await ws_server.send(msg['text'])
                except Exception:
                    pass
                await ws_server.close()

            async def down():
                try:
                    async for msg in ws_server:
                        if isinstance(msg, bytes):
                            await ws_client.send_bytes(msg)
                        else:
                            if OO_BACKEND and OO_PUBLIC_URL:
                                msg = msg.replace(OO_BACKEND, OO_PUBLIC_URL)
                            await ws_client.send_text(msg)
                except Exception:
                    pass

            await asyncio.gather(up(), down())
    except Exception as e:
        logger.warning(f'WS proxy error [{oo_path}]: {e}')
        try:
            await ws_client.close()
        except Exception:
            pass


# Statische OO-Dateien und API
@router.api_route('/oo/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS', 'PATCH'])
async def proxy_oo_http(path: str, request: Request):
    return await _proxy_http(path, request)

@router.websocket('/oo/{path:path}')
async def proxy_oo_ws(path: str, ws_client: WebSocket):
    await _proxy_ws(path, ws_client)

# Socket.IO / Docservice-Verbindungen (kein /oo/-Präfix im Editor)
@router.api_route('/doc/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS', 'PATCH'])
async def proxy_doc_http(path: str, request: Request):
    return await _proxy_http('doc/' + path, request)

@router.websocket('/doc/{path:path}')
async def proxy_doc_ws(path: str, ws_client: WebSocket):
    await _proxy_ws('doc/' + path, ws_client)
