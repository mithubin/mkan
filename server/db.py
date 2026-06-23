import sqlite3
import uuid
import os
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get('DB_PATH', './data/db/kanban.sqlite')


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db():
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate_db()


def _migrate_db():
    alters = [
        "ALTER TABLE boards ADD COLUMN start_date TEXT",
        "ALTER TABLE boards ADD COLUMN end_date TEXT",
        "ALTER TABLE cards ADD COLUMN person_id TEXT REFERENCES persons(id) ON DELETE SET NULL",
        "ALTER TABLE klassenbuch ADD COLUMN snapshot_id TEXT REFERENCES snapshots(id) ON DELETE SET NULL",
        "ALTER TABLE cards ADD COLUMN card_type TEXT NOT NULL DEFAULT 'card'",
        "ALTER TABLE cards ADD COLUMN parent_card_id TEXT REFERENCES cards(id) ON DELETE CASCADE",
        "ALTER TABLE users ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE cards ADD COLUMN card_mode TEXT NOT NULL DEFAULT 'org'",
        "ALTER TABLE cards ADD COLUMN due_date TEXT",
        "ALTER TABLE cards ADD COLUMN time_spent INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE cards ADD COLUMN attendance_n INTEGER",
        "ALTER TABLE cards ADD COLUMN attendance_data TEXT",
        "ALTER TABLE snapshot_cards ADD COLUMN card_mode TEXT",
        "ALTER TABLE snapshot_cards ADD COLUMN due_date TEXT",
        "ALTER TABLE snapshot_cards ADD COLUMN time_spent INTEGER",
        "ALTER TABLE snapshot_cards ADD COLUMN attendance_n INTEGER",
        "ALTER TABLE snapshot_cards ADD COLUMN attendance_data TEXT",
        "ALTER TABLE cards ADD COLUMN cover_pos TEXT",
        "ALTER TABLE snapshot_cards ADD COLUMN assignee_names TEXT",
        "ALTER TABLE columns ADD COLUMN col_type TEXT NOT NULL DEFAULT 'normal'",
        "ALTER TABLE cards ADD COLUMN card_settings TEXT",
        "ALTER TABLE users ADD COLUMN global_role TEXT NOT NULL DEFAULT 'user'",
        """CREATE TABLE IF NOT EXISTS board_invite_tokens (
          token      TEXT PRIMARY KEY,
          created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL
        )""",
        "ALTER TABLE users ADD COLUMN display_initials TEXT",
        "ALTER TABLE attachments ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS pw_reset_tokens (
          token      TEXT PRIMARY KEY,
          user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS email_accounts (
          id           TEXT PRIMARY KEY,
          name         TEXT NOT NULL,
          smtp_host    TEXT NOT NULL,
          smtp_port    INTEGER NOT NULL DEFAULT 587,
          smtp_user    TEXT NOT NULL,
          smtp_pass    TEXT NOT NULL,
          use_starttls INTEGER NOT NULL DEFAULT 1,
          from_name    TEXT NOT NULL DEFAULT '',
          from_address TEXT NOT NULL,
          created_at   TEXT DEFAULT (datetime('now'))
        )""",
        "ALTER TABLE columns ADD COLUMN bg_color TEXT",
        """CREATE TABLE IF NOT EXISTS planner_items (
          id          TEXT PRIMARY KEY,
          user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title       TEXT NOT NULL DEFAULT '',
          date        TEXT NOT NULL,
          time_start  TEXT NOT NULL,
          time_end    TEXT NOT NULL,
          color       TEXT,
          notes       TEXT,
          cover_path  TEXT,
          updated_by  TEXT REFERENCES users(id),
          created_at  TEXT DEFAULT (datetime('now')),
          updated_at  TEXT DEFAULT (datetime('now'))
        )""",
        "ALTER TABLE planner_items ADD COLUMN cover_path TEXT",
        "ALTER TABLE planner_items ADD COLUMN bg_color TEXT",
        "ALTER TABLE planner_items ADD COLUMN is_freiraum INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE planner_items ADD COLUMN cover_pos TEXT",
        """CREATE TABLE IF NOT EXISTS email_templates (
          key     TEXT PRIMARY KEY,
          subject TEXT NOT NULL,
          body    TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS inter_board_links (
          card_id        TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
          target_card_id TEXT NOT NULL,
          target_board_id TEXT NOT NULL,
          PRIMARY KEY (card_id, target_card_id)
        )""",
        "ALTER TABLE board_members ADD COLUMN col_id TEXT",
        "ALTER TABLE cards ADD COLUMN created_by TEXT",
        """CREATE TABLE IF NOT EXISTS mail_composer_accounts (
          id             TEXT PRIMARY KEY,
          name           TEXT NOT NULL,
          from_name      TEXT NOT NULL DEFAULT '',
          from_address   TEXT NOT NULL,
          smtp_host      TEXT NOT NULL,
          smtp_port      INTEGER NOT NULL DEFAULT 587,
          smtp_user      TEXT NOT NULL,
          smtp_pass_enc  TEXT NOT NULL,
          smtp_pass_salt TEXT NOT NULL,
          use_starttls   INTEGER NOT NULL DEFAULT 1,
          imap_host      TEXT NOT NULL,
          imap_port      INTEGER NOT NULL DEFAULT 993,
          imap_user      TEXT NOT NULL,
          imap_pass_enc  TEXT NOT NULL,
          imap_pass_salt TEXT NOT NULL,
          sent_folder    TEXT NOT NULL DEFAULT 'Sent',
          is_default     INTEGER NOT NULL DEFAULT 0,
          created_at     TEXT DEFAULT (datetime('now'))
        )""",
        "ALTER TABLE users ADD COLUMN badge_color TEXT",
        "ALTER TABLE cards ADD COLUMN dv_shared INTEGER NOT NULL DEFAULT 0",
    ]
    with get_conn() as conn:
        for sql in alters:
            try:
                conn.execute(sql)
            except Exception:
                pass


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT UNIQUE NOT NULL,
  pw_hash    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boards (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  owner_id   TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_members (
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  user_id  TEXT NOT NULL REFERENCES users(id),
  role     TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
  PRIMARY KEY (board_id, user_id)
);

CREATE TABLE IF NOT EXISTS columns (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  title    TEXT NOT NULL,
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS swimlanes (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  title    TEXT NOT NULL,
  note     TEXT,
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
  id         TEXT PRIMARY KEY,
  board_id   TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  col_id     TEXT NOT NULL REFERENCES columns(id),
  lane_id    TEXT NOT NULL REFERENCES swimlanes(id),
  position   INTEGER NOT NULL DEFAULT 0,
  title      TEXT NOT NULL,
  notes      TEXT,
  color      TEXT,
  bg_color   TEXT,
  cover_path TEXT,
  points     INTEGER NOT NULL DEFAULT 0,
  points_max INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subtasks (
  id       TEXT PRIMARY KEY,
  card_id  TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  text     TEXT NOT NULL,
  done     INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  text     TEXT NOT NULL,
  color    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_labels (
  card_id  TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  label_id TEXT NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
  PRIMARY KEY (card_id, label_id)
);

CREATE TABLE IF NOT EXISTS attachments (
  id         TEXT PRIMARY KEY,
  card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  filename   TEXT NOT NULL,
  path       TEXT NOT NULL,
  size       INTEGER NOT NULL,
  mime       TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_links (
  card_id_a TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  card_id_b TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  PRIMARY KEY (card_id_a, card_id_b)
);

CREATE TABLE IF NOT EXISTS card_assignees (
  card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (card_id, user_id)
);

CREATE TABLE IF NOT EXISTS persons (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  code     TEXT NOT NULL,
  name     TEXT,
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  ts       TEXT NOT NULL,
  note     TEXT
);

CREATE TABLE IF NOT EXISTS snapshot_cards (
  id             TEXT PRIMARY KEY,
  snapshot_id    TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  card_id        TEXT,
  person_id      TEXT,
  person_code    TEXT,
  title          TEXT NOT NULL,
  col_id         TEXT,
  col_title      TEXT,
  lane_id        TEXT,
  lane_title     TEXT,
  color          TEXT,
  bg_color       TEXT,
  label_ids      TEXT,
  points         INTEGER NOT NULL DEFAULT 0,
  points_max     INTEGER,
  subtasks_done  INTEGER NOT NULL DEFAULT 0,
  subtasks_total INTEGER NOT NULL DEFAULT 0,
  subtasks       TEXT,
  attachments    TEXT,
  card_notes     TEXT
);

CREATE TABLE IF NOT EXISTS klassenbuch (
  id       TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  date     TEXT NOT NULL,
  title    TEXT NOT NULL,
  note     TEXT,
  stunden  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_tables (
  id         TEXT PRIMARY KEY,
  card_id    TEXT NOT NULL UNIQUE REFERENCES cards(id) ON DELETE CASCADE,
  title      TEXT,
  mode       TEXT NOT NULL DEFAULT 'table',
  col_labels TEXT NOT NULL DEFAULT '[]',
  row_labels TEXT NOT NULL DEFAULT '[]',
  cells      TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_board_settings (
  user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  settings TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (user_id, board_id)
);

CREATE TABLE IF NOT EXISTS fonts (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  mimetype   TEXT NOT NULL DEFAULT 'font/ttf',
  data       BLOB NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
"""
