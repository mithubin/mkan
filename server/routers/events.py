"""
SSE-Broadcast — Phase 1: Stub mit Keep-Alive-Pings.
Phase 3 verdrahtet broadcast() in allen mutierenden Routen.
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db import get_conn
from auth import current_user_id_from_query, require_board_access

router = APIRouter()

# board_id → set of asyncio.Queue
_subscribers: dict[str, set[asyncio.Queue]] = {}


def broadcast(board_id: str, event: dict):
    """Sync-Wrapper, aufrufbar aus sync Route-Handlern (Phase 3)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for q in list(_subscribers.get(board_id, set())):
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except asyncio.QueueFull:
            pass


@router.get('/{board_id}/events')
async def board_events(board_id: str, token: str = ''):
    user_id = current_user_id_from_query(token)
    with get_conn() as conn:
        require_board_access(board_id, user_id, conn, min_role='viewer')

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.setdefault(board_id, set()).add(queue)

    async def stream():
        try:
            while True:
                try:
                    event = queue.get_nowait()
                    yield f'data: {json.dumps(event)}\n\n'
                except asyncio.QueueEmpty:
                    yield f'data: {json.dumps({"type": "ping"})}\n\n'
                    await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        finally:
            _subscribers.get(board_id, set()).discard(queue)

    return StreamingResponse(
        stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
