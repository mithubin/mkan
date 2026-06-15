import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from auth import current_user_id
from db import DB_PATH, get_conn

router = APIRouter()

SQLITE_MAGIC = b'SQLite format 3\x00'


def _db_abspath() -> str:
    return os.path.abspath(DB_PATH)


@router.get('/db/export')
def export_db(user_id: str = Depends(current_user_id)):
    db_path = _db_abspath()
    if not os.path.exists(db_path):
        raise HTTPException(404, 'DB not found')
    # WAL checkpoint so the export is consistent
    with get_conn() as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    ts = datetime.utcnow().strftime('%Y%m%d-%H%M')
    fname = f'kanban-backup-{ts}.sqlite'
    return FileResponse(db_path, media_type='application/octet-stream',
                        filename=fname)


@router.post('/db/import')
async def import_db(file: UploadFile = File(...),
                    user_id: str = Depends(current_user_id)):
    data = await file.read()
    if not data.startswith(SQLITE_MAGIC):
        raise HTTPException(400, 'Keine gültige SQLite-Datei')
    # Quick schema sanity check
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite') as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        test_conn = sqlite3.connect(tmp_path)
        tables = {r[0] for r in test_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        test_conn.close()
        required = {'boards', 'cards', 'users', 'columns', 'swimlanes'}
        if not required.issubset(tables):
            os.unlink(tmp_path)
            raise HTTPException(400, 'SQLite-Datei enthält kein gültiges mkan-Schema')
    except HTTPException:
        raise
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(400, f'Fehler beim Lesen der Datei: {e}')

    db_path = _db_abspath()
    bak_path = db_path + '.bak'
    shutil.copy2(db_path, bak_path)
    shutil.move(tmp_path, db_path)
    # Remove WAL/SHM from old DB to avoid confusion
    for ext in ('-wal', '-shm'):
        old = db_path + ext
        if os.path.exists(old):
            os.unlink(old)
    return {'ok': True, 'backup': bak_path}
