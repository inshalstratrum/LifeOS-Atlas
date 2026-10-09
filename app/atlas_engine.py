from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import content_extractors
from .database import connect, fetch_all, fetch_one, init_db, json_dumps
from .duplicate_engine import build_duplicate_groups
from .scope_reader import DEFAULT_EXCLUSIONS, scan_roots_with_counts, write_scope_configs
import threading

_audit_lock = threading.Lock()


APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = APP_ROOT / "config"
FOLDER_PROPOSAL = CONFIG_DIR / "folder_structure_proposal.json"
AI_SETTINGS = CONFIG_DIR / "ai_provider_settings.example.json"


PHASES = [
    "Discovery and Concept Development",
    "LifeOS Core Kernel",
    "Planning and Requirements",
    "Architecture, System Design and Blueprints",
    "Prototyping, Implementation and Coding",
    "Reference / Archive",
]


MASTER_BOOKS = [
    "Executive Governance Book",
    "Architecture and Memory Book",
    "Product / Apps Library",
    "Technical Development Library",
    "Design / UI / UX Library",
    "Research and Evidence Library",
    "Operations and Automation Library",
    "Navigation / Index / Metadata Library",
    "Controlled Reference Archive",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_rules() -> list[dict]:
    rules_path = CONFIG_DIR / "classification_rules.json"
    if not rules_path.exists():
        return []
    return json.loads(rules_path.read_text(encoding="utf-8")).get("rules", [])


def match_rule(path_text: str, root_label: str, extension: str) -> dict:
    haystack = path_text.replace("\\", " ")
    source = {
        "root": root_label,
        "relative_path": path_text,
        "folder_chain": path_text,
        "filename": Path(path_text).name,
        "extension": extension,
        "any": haystack,
    }
    for rule in sorted(load_rules(), key=lambda item: item.get("priority", 0), reverse=True):
        matches = rule.get("match", {})
        field_matched = True
        for key, patterns in matches.items():
            value = source.get(key, haystack)
            field_any_match = False
            for pattern in patterns:
                try:
                    if re.search(pattern, value, re.IGNORECASE):
                        field_any_match = True
                        break
                except re.error:
                    if pattern.lower() in value.lower():
                        field_any_match = True
                        break
            if not field_any_match:
                field_matched = False
                break
        if field_matched:
            return rule
    return {
        "id": "UNCLASSIFIED_LOCAL_REVIEW",
        "assign": {
            "department": "Needs Review",
            "phase": "Triage",
            "priority": "P2",
            "lifecycle": "Audit & Classification",
            "confidence": 0.35,
        },
        "reason": "No reliable rule matched all required fields.",
    }


def purpose_for(filename: str, headings: list[dict], extension: str) -> str:
    if headings:
        return f"Explains or controls: {headings[0]['text'][:120]}"
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    if extension.lower() in content_extractors.SUPPORTED_EXTENSIONS:
        return f"Text document about {stem}."
    return f"{extension or 'Unsupported'} file; metadata and hash only."


def write_static_configs() -> None:
    write_scope_configs()
    FOLDER_PROPOSAL.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "software_development_structure": PHASES,
                "master_library": MASTER_BOOKS,
                "policy": "Planner proposes destinations only; it never creates folders or moves source files.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    AI_SETTINGS.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_mode": "Local only",
                "modes": ["Local only", "Gemini", "OpenRouter"],
                "environment_variables": ["LIFEOS_GEMINI_API_KEY", "LIFEOS_OPENROUTER_API_KEY"],
                "policy": "No API key in HTML, JavaScript, SQLite, logs, or source code. Calls require explicit owner confirmation.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_audit(limit: int | None = None, background: bool = False) -> dict:
    if not _audit_lock.acquire(blocking=False):
        return {"status": "skipped_already_running"}
    try:
        write_static_configs()
        conn = connect()
        if background:
            # 1. Quick check for pending resolutions (resolved groups still in duplicate_groups)
            has_pending = conn.execute(
                "SELECT COUNT(*) AS c FROM duplicate_groups WHERE group_key IN (SELECT group_key FROM duplicate_resolutions)"
            ).fetchone()["c"] > 0
            if has_pending:
                return _run_audit_internal(limit, conn)

            # 2. Check if file counts or stats changed
            entries, _, _, _, _ = scan_roots_with_counts()
            if limit:
                entries = entries[:limit]
            
            # Non-missing records in DB
            db_records = {row["full_path"]: row for row in fetch_all(conn, "SELECT full_path, size_bytes, modified_at FROM source_records WHERE department != 'Missing'")}
            
            changed = False
            seen_count = 0
            for entry in entries:
                path = Path(entry.full_path)
                if path.exists() and path.is_file():
                    seen_count += 1
                    path_str = str(path)
                    existing = db_records.get(path_str)
                    if not existing:
                        changed = True
                        break
                    try:
                        stat = path.stat()
                        mtime_iso = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                        if existing["size_bytes"] != stat.st_size or existing["modified_at"] != mtime_iso:
                            changed = True
                            break
                    except OSError:
                        changed = True
                        break
            
            if not changed:
                # Also check if any files went missing (records count doesn't match seen_count)
                if len(db_records) != seen_count:
                    changed = True
            
            if not changed:
                return {"status": "no_changes"}

        return _run_audit_internal(limit, conn)
    finally:
        _audit_lock.release()

def _run_audit_internal(limit: int | None = None, conn=None) -> dict:
    init_db(conn)
    cursor = conn.execute(
        "INSERT INTO audit_runs(started_at, status, notes) VALUES(?, 'running', ?)",
        (now(), "Read-only authorised scope audit."),
    )
    run_id = int(cursor.lastrowid)
    entries, unique_count, total_before, excluded, missing_roots = scan_roots_with_counts()
    if limit:
        entries = entries[:limit]
    unsupported = 0
    missing = 0

    existing_records = {row["full_path"]: row for row in fetch_all(conn, "SELECT * FROM source_records")}
    existing_hashes = {row["sha256"]: row for row in existing_records.values()}
    seen_paths = {str(Path(entry.full_path)) for entry in entries if Path(entry.full_path).exists() and Path(entry.full_path).is_file()}
    missing_paths = set(existing_records.keys()) - seen_paths

    for entry in entries:
        path = Path(entry.full_path)
        path_str = str(path)
        if path_str not in seen_paths:
            missing += 1
            continue
        try:
            stat = path.stat()
            
            # Fast path: skip if unmodified
            existing = existing_records.get(path_str)
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            if existing and existing["size_bytes"] == stat.st_size and existing["modified_at"] == mtime_iso and existing.get("department") != "Missing":
                continue

            sha = content_extractors.file_sha256(path)
            extraction = content_extractors.extract_file(path)
        except OSError:
            continue
        if not extraction.supported:
            unsupported += 1
            
        extension = path.suffix.lower()
        rule = match_rule(entry.relative_path, entry.root_label, extension)
        assign = rule.get("assign", {})
        purpose = purpose_for(path.name, extraction.headings, extension)
        
        department = assign.get("department", "Needs Review")
        
        # Check for empty/placeholder logic (Step 8)
        is_empty = False
        if stat.st_size == 0 or (extraction.supported and not extraction.blocks):
            is_empty = True
        elif extraction.supported:
            from .duplicate_engine import _is_boilerplate
            if all(_is_boilerplate(b.text) for b in extraction.blocks):
                is_empty = True
                
        if is_empty:
            department = "Empty / Placeholder Audit"
            purpose = "Zero-byte, whitespace-only, metadata-only, or boilerplate-only file."
            
        source_id = None
        if path_str in existing_records:
            source_id = existing_records[path_str]["id"]
        elif sha in existing_hashes and existing_hashes[sha]["full_path"] in missing_paths:
            # Relocated!
            old_path = existing_hashes[sha]["full_path"]
            source_id = existing_hashes[sha]["id"]
            missing_paths.remove(old_path)
            department = "Relocated"
            
        if department == "Relocated":
            classification_basis = f"Relocated from {old_path}"
        else:
            classification_basis = f"deterministically derived from path/content structure; rule {rule.get('id')}: {rule.get('reason')}"

        if source_id is not None:
            conn.execute(
                """
                UPDATE source_records SET
                  run_id=?, root_label=?, root_path=?, relative_path=?, full_path=?, filename=?,
                  extension=?, size_bytes=?, modified_at=?, created_at=?, sha256=?, content_supported=?,
                  content_status=?, purpose=?, department=?, phase=?, authority=?, lifecycle=?, priority=?,
                  classification_basis=?, headings_json=?, yaml_json=?, links_json=?
                WHERE id=?
                """,
                (
                    run_id, entry.root_label, entry.root_path, entry.relative_path, path_str, path.name,
                    extension, stat.st_size, datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(), sha, 1 if extraction.supported else 0,
                    extraction.status, purpose, department, assign.get("phase", "Triage"),
                    "Active canonical candidate" if "Shali" in entry.root_label else "Historical/control authority",
                    assign.get("lifecycle", "Audit & Classification"), assign.get("priority", "P2"),
                    classification_basis,
                    json_dumps(extraction.headings), json_dumps(extraction.yaml_fields), json_dumps(extraction.links),
                    source_id,
                ),
            )
            # Clear old blocks
            conn.execute("DELETE FROM content_blocks WHERE source_id=?", (source_id,))
        else:
            cursor = conn.execute(
                """
                INSERT INTO source_records(run_id, root_label, root_path, relative_path, full_path, filename,
                  extension, size_bytes, modified_at, created_at, sha256, content_supported, content_status, purpose,
                  department, phase, authority, lifecycle, priority, classification_basis, headings_json, yaml_json, links_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, entry.root_label, entry.root_path, entry.relative_path, path_str, path.name,
                    extension, stat.st_size, datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(), sha, 1 if extraction.supported else 0,
                    extraction.status, purpose, department, assign.get("phase", "Triage"),
                    "Active canonical candidate" if "Shali" in entry.root_label else "Historical/control authority",
                    assign.get("lifecycle", "Audit & Classification"), assign.get("priority", "P2"),
                    classification_basis,
                    json_dumps(extraction.headings), json_dumps(extraction.yaml_fields), json_dumps(extraction.links),
                ),
            )
            source_id = int(cursor.lastrowid)
            
        for index, block in enumerate(extraction.blocks):
            conn.execute(
                """
                INSERT INTO content_blocks(source_id, block_index, block_type, heading, line_start, line_end, fingerprint, snippet, char_count)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, index, block.block_type, block.heading, block.line_start, block.line_end,
                    block.fingerprint, block.text[:420], len(block.text),
                ),
            )
            
    for missing_path in missing_paths:
        conn.execute("UPDATE source_records SET department='Missing' WHERE full_path=?", (missing_path,))

    unsupported = conn.execute("SELECT COUNT(*) AS c FROM source_records WHERE content_supported=0 AND department != 'Missing'").fetchone()["c"]
    conn.execute(
        """
        UPDATE audit_runs
        SET finished_at=?, status='completed', source_count=?, files_seen=?, files_indexed=?, excluded_count=?, unsupported_count=?, notes=?
        WHERE id=?
        """,
        (
            now(),
            len(entries),
            len(entries) - missing,
            len(entries) - missing,
            len(excluded),
            unsupported,
            f"Raw file count before exclusions: {total_before}. Excluded: {len(excluded)}. Missing roots: {len(missing_roots)}.",
            run_id,
        ),
    )
    conn.commit()
    build_duplicate_groups(conn)
    return summary(conn)


def summary(conn=None) -> dict:
    own = conn is None
    conn = conn or connect()
    run = fetch_one(conn, "SELECT * FROM audit_runs ORDER BY id DESC LIMIT 1")
    totals = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS files,
          SUM(CASE WHEN content_supported=1 THEN 1 ELSE 0 END) AS supported,
          SUM(size_bytes) AS bytes
        FROM source_records
        """,
    ) or {"files": 0, "supported": 0, "bytes": 0}
    groups = fetch_all(conn, "SELECT group_type, COUNT(*) AS count FROM duplicate_groups GROUP BY group_type")
    decisions = fetch_all(conn, "SELECT decision_type, COUNT(*) AS count FROM decisions GROUP BY decision_type")
    if own:
        conn.close()
    return {"run": run, "totals": totals, "groups": groups, "decisions": decisions}


def create_decision_cards(conn) -> None:
    conn.execute("DELETE FROM decisions")
    groups = fetch_all(conn, "SELECT * FROM duplicate_groups ORDER BY confidence DESC LIMIT 40")
    for group in groups:
        member = fetch_one(
            conn,
            """
            SELECT s.*
            FROM duplicate_members dm JOIN source_records s ON s.id=dm.source_id
            WHERE dm.group_id=?
            ORDER BY dm.proposed_role DESC LIMIT 1
            """,
            (group["id"],),
        )
        if not member:
            continue
        block = fetch_one(
            conn,
            """
            SELECT heading, line_start, line_end, snippet
            FROM content_blocks
            WHERE source_id=? AND block_type IN ('heading', 'paragraph')
            ORDER BY block_index LIMIT 1
            """,
            (member["id"],),
        ) or {}
        line_ref = (
            f"lines {block.get('line_start')}-{block.get('line_end')}"
            if block.get("line_start") is not None
            else "metadata record"
        )
        heading_ref = f" | heading: {block.get('heading')}" if block.get("heading") else ""
        snippet_ref = f" | block: {block.get('snippet', '')[:160]}" if block.get("snippet") else ""
        conn.execute(
            """
            INSERT INTO decisions(source_id, group_id, decision_type, exact_reference, reason, uncertainty,
              recommended_option, alternative_options_json, confidence, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member["id"],
                group["id"],
                "canonical_cleanup_batch",
                f"{member['full_path']} | {line_ref}{heading_ref}{snippet_ref} | duplicate group {group['id']}",
                group["reason"],
                "Owner must decide whether the proposed canonical file is the true logical home before any merge/archive/delete action.",
                "Keep proposed canonical, review unique blocks, then archive redundant copies after separate confirmation.",
                json_dumps(["Keep separately", "Merge selected unique blocks", "Archive only", "Delete after final confirmation"]),
                group["confidence"],
                now(),
            ),
        )
    conn.commit()


if __name__ == "__main__":
    result = run_audit()
    with connect() as connection:
        create_decision_cards(connection)
    print(json.dumps(result, indent=2))
