from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "atlas.db"


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {cursor.description[index][0]: row[index] for index in range(len(row))}


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  source_count INTEGER DEFAULT 0,
  files_seen INTEGER DEFAULT 0,
  files_indexed INTEGER DEFAULT 0,
  excluded_count INTEGER DEFAULT 0,
  unsupported_count INTEGER DEFAULT 0,
  notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  root_label TEXT NOT NULL,
  root_path TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  full_path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  extension TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  modified_at TEXT,
  created_at TEXT,
  sha256 TEXT NOT NULL,
  content_supported INTEGER NOT NULL DEFAULT 0,
  content_status TEXT NOT NULL,
  purpose TEXT NOT NULL,
  department TEXT NOT NULL,
  phase TEXT NOT NULL,
  authority TEXT NOT NULL,
  lifecycle TEXT NOT NULL,
  priority TEXT NOT NULL,
  canonical_status TEXT NOT NULL DEFAULT 'candidate',
  classification_basis TEXT NOT NULL,
  finding_basis TEXT NOT NULL DEFAULT 'local metadata and content fingerprints',
  headings_json TEXT NOT NULL DEFAULT '[]',
  yaml_json TEXT NOT NULL DEFAULT '{}',
  links_json TEXT NOT NULL DEFAULT '[]',
  created_in_atlas INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(run_id) REFERENCES audit_runs(id)
);

CREATE TABLE IF NOT EXISTS content_blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  block_index INTEGER NOT NULL,
  block_type TEXT NOT NULL,
  heading TEXT DEFAULT '',
  line_start INTEGER,
  line_end INTEGER,
  fingerprint TEXT NOT NULL,
  snippet TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  FOREIGN KEY(source_id) REFERENCES source_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_key TEXT UNIQUE,
  group_type TEXT NOT NULL,
  label TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence REAL NOT NULL,
  finding_basis TEXT NOT NULL,
  member_count INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_resolutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  group_key TEXT UNIQUE,
  resolution_type TEXT NOT NULL,
  source_id INTEGER,
  output_path TEXT,
  reason TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE CASCADE,
  FOREIGN KEY(source_id) REFERENCES source_records(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS duplicate_members (
  group_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  proposed_role TEXT NOT NULL DEFAULT 'supporting',
  rationale TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(group_id, source_id),
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE CASCADE,
  FOREIGN KEY(source_id) REFERENCES source_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comparisons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER,
  left_source_id INTEGER NOT NULL,
  right_source_id INTEGER NOT NULL,
  similarity REAL NOT NULL,
  duplicate_blocks INTEGER NOT NULL,
  unique_left_blocks INTEGER NOT NULL,
  unique_right_blocks INTEGER NOT NULL,
  conflict_blocks INTEGER NOT NULL,
  finding_basis TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS canonical_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER,
  canonical_source_id INTEGER,
  proposed_destination TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'owner_decision_needed',
  created_at TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE SET NULL,
  FOREIGN KEY(canonical_source_id) REFERENCES source_records(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER,
  group_id INTEGER,
  decision_type TEXT NOT NULL,
  exact_reference TEXT NOT NULL,
  reason TEXT NOT NULL,
  uncertainty TEXT NOT NULL,
  recommended_option TEXT NOT NULL,
  alternative_options_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_id) REFERENCES source_records(id) ON DELETE SET NULL,
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS action_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER,
  plan_type TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  requires_owner_approval INTEGER NOT NULL DEFAULT 1,
  destructive INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'preview_only',
  created_at TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES duplicate_groups(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS rollback_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bundle_path TEXT NOT NULL,
  action_plan_id INTEGER,
  source_paths_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  test_fixture INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(action_plan_id) REFERENCES action_plans(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ai_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL DEFAULT 'Local only',
  provider TEXT NOT NULL DEFAULT 'Local only',
  model TEXT NOT NULL DEFAULT '',
  selected_files_json TEXT NOT NULL DEFAULT '[]',
  purpose TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  consent_required INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_records_sha ON source_records(sha256);
CREATE INDEX IF NOT EXISTS idx_source_records_ext ON source_records(extension);
CREATE INDEX IF NOT EXISTS idx_content_blocks_fp ON content_blocks(fingerprint);
"""


OPERATIONAL_TABLES = [
    "rollback_records",
    "action_plans",
    "decisions",
    "canonical_proposals",
    "comparisons",
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO schema_state(key, value) VALUES('schema_version', '1')"
    )
    # Migration: safely add group_key
    try:
        conn.execute("ALTER TABLE duplicate_groups ADD COLUMN group_key TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE duplicate_resolutions ADD COLUMN group_key TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_duplicate_groups_group_key ON duplicate_groups(group_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_duplicate_resolutions_group_key ON duplicate_resolutions(group_key)")
    conn.commit()


def fetch_all(conn: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return list(conn.execute(query, tuple(params)).fetchall())


def fetch_one(conn: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    return conn.execute(query, tuple(params)).fetchone()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default

