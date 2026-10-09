from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from . import content_extractors, scope_reader
from .database import fetch_all, fetch_one, json_dumps


APP_ROOT = Path(__file__).resolve().parents[1]
PROPOSALS_DIR = APP_ROOT / "data" / "proposals"
MERGEABLE_EXTENSIONS = {".md", ".markdown", ".txt"}


def create_merge_preview(conn, canonical_id: int, source_ids: list[int]) -> dict:
    canonical = fetch_one(conn, "SELECT * FROM source_records WHERE id=?", (canonical_id,))
    if not canonical:
        raise ValueError("Canonical source not found.")
    candidates = fetch_all(
        conn,
        f"SELECT * FROM source_records WHERE id IN ({','.join('?' for _ in source_ids)})",
        source_ids,
    ) if source_ids else []
    canonical_blocks = fetch_all(conn, "SELECT * FROM content_blocks WHERE source_id=? ORDER BY block_index", (canonical_id,))
    existing = {block["fingerprint"] for block in canonical_blocks}
    additions = []
    conflicts = []
    for candidate in candidates:
        if candidate["id"] == canonical_id:
            continue
        if candidate["extension"].lower() not in (".md", ".markdown", ".txt"):
            conflicts.append({
                "source_id": candidate["id"],
                "file": candidate["filename"],
                "reason": "Automatic semantic merge unavailable for this file type.",
            })
            continue
        blocks = fetch_all(conn, "SELECT * FROM content_blocks WHERE source_id=? ORDER BY block_index", (candidate["id"],))
        for block in blocks:
            if block["block_type"] not in ("heading", "paragraph"):
                continue
            if block["fingerprint"] not in existing and block["char_count"] >= 40:
                additions.append({
                    "source_id": candidate["id"],
                    "file": candidate["filename"],
                    "line_start": block["line_start"],
                    "line_end": block["line_end"],
                    "heading": block["heading"],
                    "snippet": block["snippet"],
                    "fingerprint": block["fingerprint"],
                    "default_include": True,
                })
                existing.add(block["fingerprint"])
    preview = {
        "canonical": canonical,
        "sources": candidates,
        "additions": additions[:400],
        "conflicts": conflicts,
        "impact": {
            "source_files_changed": [],
            "canonical_reviewed": canonical["full_path"],
            "review_copy_created": [],
            "files_archived_or_deleted": [],
            "links_affected": "preview required before approved link updates",
            "rollback": "No source rollback is needed. Rejecting the review copy only removes an Atlas-owned proposal.",
        },
        "safety": "Preview only. No source file is modified by this operation.",
    }
    cursor = conn.execute(
        """
        INSERT INTO action_plans(plan_type, preview_json, requires_owner_approval, destructive, status, created_at)
        VALUES('merge_preview', ?, 1, 0, 'preview_only', ?)
        """,
        (json_dumps(preview), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    preview["action_plan_id"] = cursor.lastrowid
    return preview


def list_proposal_folders() -> list[str]:
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    return ["."] + sorted(
        str(path.relative_to(PROPOSALS_DIR)).replace("\\", "/")
        for path in PROPOSALS_DIR.rglob("*")
        if path.is_dir()
    )


def _safe_proposal_path(destination_folder: str, filename: str, canonical_extension: str) -> Path:
    folder = (destination_folder or ".").strip().replace("\\", "/")
    folder_path = Path(folder)
    
    filename = filename.strip()
    if not filename or filename != Path(filename).name or re.search(r'[<>:"/\\|?*]', filename):
        raise ValueError("Enter a valid filename without path separators.")
    suffix = Path(filename).suffix.lower()
    if not suffix:
        suffix = canonical_extension if canonical_extension in MERGEABLE_EXTENSIONS else ".md"
        filename += suffix
    if suffix not in MERGEABLE_EXTENSIONS:
        raise ValueError("Merged review copies must use .md, .markdown, or .txt.")
        
    if folder_path.is_absolute():
        target = (folder_path / filename).resolve()
    else:
        target = (PROPOSALS_DIR / folder_path / filename).resolve()
        try:
            target.relative_to(PROPOSALS_DIR.resolve())
        except ValueError:
            raise ValueError("Relative paths cannot escape the proposals directory.")
            
    # Ensure it's not inside an authorised source root
    if scope_reader.is_path_authorised(str(target)):
        raise PermissionError("Output path cannot be inside an authorised read-only source root.")
        
    return target


def _validated_source_path(row: dict) -> Path:
    path = Path(row["full_path"])
    if row.get("created_in_atlas"):
        try:
            path.resolve().relative_to(APP_ROOT.resolve())
        except ValueError as exc:
            raise PermissionError("Atlas fixtures must stay inside the Atlas workspace.") from exc
    elif not scope_reader.is_path_authorised(str(path)):
        raise PermissionError("Source path is outside the authorised cleanup scope.")
    if not path.is_file():
        raise FileNotFoundError(f"Source file is unavailable: {path.name}")
    return path


def _read_source_lines(row: dict, start: int, end: int) -> str:
    text = content_extractors.read_text_file(_validated_source_path(row))
    lines = text.splitlines()
    return "\n".join(lines[max(0, start - 1):max(start, end)])


def create_merged_review_copy(
    conn,
    canonical_id: int,
    source_ids: list[int],
    selected_fingerprints: list[str],
    destination_folder: str,
    filename: str,
) -> dict:
    canonical = fetch_one(conn, "SELECT * FROM source_records WHERE id=?", (canonical_id,))
    if not canonical:
        raise ValueError("Canonical source not found.")
    if canonical["extension"].lower() not in MERGEABLE_EXTENSIONS:
        raise ValueError("Only Markdown and text files can create merged review copies.")
    canonical_path = _validated_source_path(canonical)
    output_path = _safe_proposal_path(destination_folder, filename, canonical["extension"].lower())
    if output_path.exists():
        raise FileExistsError("A proposal with this path already exists. Choose a new filename.")
    preview = create_merge_preview(conn, canonical_id, source_ids)
    selected = set(selected_fingerprints)
    additions = [item for item in preview["additions"] if item["fingerprint"] in selected]
    source_by_id = {row["id"]: row for row in preview["sources"]}
    sections = [content_extractors.read_text_file(canonical_path).rstrip()]
    if additions:
        sections.append("\n\n# Atlas review additions\n")
    for item in additions:
        source = source_by_id.get(item["source_id"])
        if not source:
            continue
        exact_text = _read_source_lines(source, int(item["line_start"]), int(item["line_end"])).strip()
        sections.append(
            f"\n## Review addition from {source['filename']} (lines {item['line_start']}-{item['line_end']})\n\n{exact_text}\n"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    
    try:
        relative_output_path = str(output_path.relative_to(PROPOSALS_DIR)).replace("\\", "/")
    except ValueError:
        relative_output_path = str(output_path).replace("\\", "/")
        
    result = {
        "ok": True,
        "output_path": str(output_path),
        "relative_output_path": relative_output_path,
        "canonical_id": canonical_id,
        "selected_additions": len(additions),
        "source_files_changed": [],
        "source_files_archived_or_deleted": [],
        "safety": "Created an Atlas-owned review copy only. Source files are unchanged.",
    }
    conn.execute(
        """
        INSERT INTO action_plans(plan_type, preview_json, requires_owner_approval, destructive, status, created_at)
        VALUES('merged_review_copy', ?, 1, 0, 'review_copy_created', ?)
        """,
        (json_dumps(result), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return result
