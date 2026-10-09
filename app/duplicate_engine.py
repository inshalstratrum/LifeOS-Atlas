from __future__ import annotations

import itertools
import re
from datetime import datetime, timezone
from typing import Any
import hashlib
import html

from .database import fetch_all, fetch_one, json_dumps


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(name: str) -> str:
    value = name.lower()
    value = re.sub(r"\.[^.]+$", "", value)
    value = re.sub(r"\b(v|version|rev|revision|r)[-_ .]*\d+(\.\d+)*\b", "", value)
    value = re.sub(r"\b(19|20)\d{2}[-_. ]?\d{0,2}[-_. ]?\d{0,2}\b", "", value)
    value = re.sub(r"\b(final|draft|copy|old|latest|backup|archive|frozen|report)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _insert_group(conn, group_type: str, label: str, reason: str, confidence: float, basis: str, members: list[dict[str, Any]]) -> tuple[int, bool]:
    hashes = sorted({m["sha256"] for m in members})
    ids = sorted({str(m["id"]) for m in members})
    key_str = f"{group_type}:" + ",".join(hashes) + ":" + ",".join(ids)
    group_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    existing = fetch_one(conn, "SELECT id FROM duplicate_groups WHERE group_key=?", (group_key,))
    if existing:
        return existing["id"], False

    cursor = conn.execute(
        """
        INSERT INTO duplicate_groups(group_key, group_type, label, reason, confidence, finding_basis, member_count, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (group_key, group_type, label, reason, confidence, basis, len(members), _now()),
    )
    group_id = int(cursor.lastrowid)
    canonical = choose_canonical(members)
    for member in members:
        role = "proposed_canonical" if member["id"] == canonical["id"] else "supporting"
        conn.execute(
            "INSERT INTO duplicate_members(group_id, source_id, proposed_role, rationale) VALUES(?, ?, ?, ?)",
            (group_id, member["id"], role, "Newest/larger supported text candidate chosen for review."),
        )
    conn.execute(
        """
        INSERT INTO canonical_proposals(group_id, canonical_source_id, proposed_destination, action, reason, confidence, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group_id,
            canonical["id"],
            canonical["full_path"],
            "owner_review_keep_canonical_then_merge_or_archive",
            reason,
            confidence,
            _now(),
        ),
    )
    return group_id, True


def choose_canonical(members: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        members,
        key=lambda row: (row.get("content_supported", 0), row.get("size_bytes", 0), row.get("modified_at") or ""),
        reverse=True,
    )[0]


def build_duplicate_groups(conn) -> None:
    conn.execute("DELETE FROM canonical_proposals WHERE group_id NOT IN (SELECT group_id FROM duplicate_resolutions)")
    conn.execute("DELETE FROM comparisons WHERE group_id NOT IN (SELECT group_id FROM duplicate_resolutions)")
    conn.execute("DELETE FROM duplicate_members WHERE group_id NOT IN (SELECT group_id FROM duplicate_resolutions)")
    conn.execute("DELETE FROM duplicate_groups WHERE id NOT IN (SELECT group_id FROM duplicate_resolutions)")

    resolved_sources = {row["source_id"] for row in fetch_all(conn, "SELECT source_id FROM duplicate_members WHERE group_id IN (SELECT group_id FROM duplicate_resolutions)")}
    records = [r for r in fetch_all(conn, "SELECT * FROM source_records") if r["id"] not in resolved_sources and r.get("department") != "Missing"]
    by_hash: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_hash.setdefault(record["sha256"], []).append(record)
        key = _norm_name(record["filename"])
        if key:
            by_name.setdefault(key, []).append(record)

    for sha, members in by_hash.items():
        if len(members) > 1:
            group_id, is_new = _insert_group(
                conn,
                "identical_hash_candidate",
                f"Identical files — canonical decision needed: {members[0]['filename']}",
                "SHA-256 hash match. Canonical decision required.",
                1.0,
                "exact hash match",
                members,
            )
            if is_new:
                for left, right in itertools.combinations(members, 2):
                    insert_comparison(conn, group_id, left["id"], right["id"], 1.0, "exact hash duplicate")

    for key, members in by_name.items():
        # Differentiate Hubs (e.g. LifeOS Platform Hub, Client Workspace Hub)
        # by treating them as distinct if their folder paths contain different Hub names
        def _hub_key(row):
            path_str = row["full_path"].lower()
            if "platform hub" in path_str: return "platform_hub"
            if "workspace hub" in path_str: return "workspace_hub"
            if "project hub" in path_str: return "project_hub"
            return ""
            
        by_hub = {}
        for m in members:
            by_hub.setdefault(_hub_key(m), []).append(m)
            
        for hkey, hmembers in by_hub.items():
            unique_hashes = {member["sha256"] for member in hmembers}
            if len(hmembers) > 1 and len(unique_hashes) > 1:
                group_id, is_new = _insert_group(
                    conn,
                    "near_duplicate",
                    f"Local filename/section-similarity candidates: {key}",
                    "Filename normalisation suggests old/new/final/frozen variants.",
                    0.64,
                    "local structural similarity",
                    hmembers,
                )
                if is_new:
                    for left, right in itertools.combinations(hmembers[:8], 2):
                        sim = block_similarity(conn, left["id"], right["id"])
                        insert_comparison(conn, group_id, left["id"], right["id"], sim, "likely version")

    block_cache = preload_block_sets(conn)
    candidate_pairs = near_duplicate_candidates(block_cache)
    for left_id, right_id in candidate_pairs[:1200]:
        left = next((record for record in records if record["id"] == left_id), None)
        right = next((record for record in records if record["id"] == right_id), None)
        if not left or not right or left["sha256"] == right["sha256"]:
            continue
        sim = block_similarity_sets(block_cache.get(left_id, set()), block_cache.get(right_id, set()))
        if sim >= 0.42:
            group_id, is_new = _insert_group(
                conn,
                "near_duplicate",
                f"Local filename/section-similarity candidates: {left['filename']} / {right['filename']}",
                "Supported text files share meaningful non-boilerplate local content blocks.",
                min(0.95, sim),
                "local structural similarity",
                [left, right],
            )
            if is_new:
                insert_comparison(conn, group_id, left["id"], right["id"], sim, "local structural similarity")
    conn.commit()


def _is_boilerplate(text: str) -> bool:
    norm = re.sub(r"[^a-z0-9 ]+", " ", html.unescape(text).strip().lower())
    return norm in {
        "status", "purpose", "authority", "next phase", "phase b1",
        "heading status", "heading purpose", "heading authority", "heading next phase", "heading phase b1"
    }

def block_sets(conn, source_id: int) -> set[str]:
    rows = fetch_all(
        conn,
        "SELECT fingerprint, snippet FROM content_blocks WHERE source_id=? AND block_type IN ('paragraph', 'heading')",
        (source_id,),
    )
    return {row["fingerprint"] for row in rows if not _is_boilerplate(row.get("snippet", ""))}


def preload_block_sets(conn) -> dict[int, set[str]]:
    rows = fetch_all(
        conn,
        "SELECT source_id, fingerprint, snippet FROM content_blocks WHERE block_type IN ('paragraph', 'heading') AND char_count >= 32",
    )
    cache: dict[int, set[str]] = {}
    for row in rows:
        if not _is_boilerplate(row.get("snippet", "")):
            cache.setdefault(row["source_id"], set()).add(row["fingerprint"])
    return {source_id: fps for source_id, fps in cache.items() if len(fps) >= 2}


def near_duplicate_candidates(block_cache: dict[int, set[str]]) -> list[tuple[int, int]]:
    inverted: dict[str, list[int]] = {}
    for source_id, fingerprints in block_cache.items():
        for fingerprint in fingerprints:
            bucket = inverted.setdefault(fingerprint, [])
            if len(bucket) < 80:
                bucket.append(source_id)
    scores: dict[tuple[int, int], int] = {}
    for ids in inverted.values():
        if len(ids) < 2:
            continue
        for left, right in itertools.combinations(sorted(ids), 2):
            key = (left, right)
            scores[key] = scores.get(key, 0) + 1
    return [pair for pair, overlap in sorted(scores.items(), key=lambda item: item[1], reverse=True) if overlap >= 2]


def block_similarity_sets(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def block_similarity(conn, left_id: int, right_id: int) -> float:
    left = block_sets(conn, left_id)
    right = block_sets(conn, right_id)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def insert_comparison(conn, group_id: int | None, left_id: int, right_id: int, similarity: float, basis: str) -> None:
    left_blocks = fetch_all(conn, "SELECT * FROM content_blocks WHERE source_id=? ORDER BY block_index", (left_id,))
    right_blocks = fetch_all(conn, "SELECT * FROM content_blocks WHERE source_id=? ORDER BY block_index", (right_id,))
    right_fp = {row["fingerprint"] for row in right_blocks}
    left_fp = {row["fingerprint"] for row in left_blocks}
    duplicate = len(left_fp & right_fp)
    payload = {
        "left": [
            {**block, "state": "duplicate" if block["fingerprint"] in right_fp else "unique"}
            for block in left_blocks[:300]
        ],
        "right": [
            {**block, "state": "duplicate" if block["fingerprint"] in left_fp else "unique"}
            for block in right_blocks[:300]
        ],
    }
    conn.execute(
        """
        INSERT INTO comparisons(group_id, left_source_id, right_source_id, similarity, duplicate_blocks,
          unique_left_blocks, unique_right_blocks, conflict_blocks, finding_basis, comparison_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group_id,
            left_id,
            right_id,
            similarity,
            duplicate,
            max(0, len(left_fp - right_fp)),
            max(0, len(right_fp - left_fp)),
            0,
            basis,
            json_dumps(payload),
        ),
    )
