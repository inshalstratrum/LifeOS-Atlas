from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import action_executor, ai_connectors, atlas_engine, comparison_engine, merge_engine, scope_reader
    from app.database import connect, fetch_all, fetch_one, init_db, json_loads
else:
    from . import action_executor, ai_connectors, atlas_engine, comparison_engine, merge_engine, scope_reader
    from .database import connect, fetch_all, fetch_one, init_db, json_loads


APP_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = APP_ROOT / "dashboard"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def authorised_paths() -> set[str]:
    return {entry.full_path.lower() for entry in scope_reader.parse_scope_tree()}


def response_json(handler: BaseHTTPRequestHandler, payload, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class AtlasHandler(BaseHTTPRequestHandler):
    server_version = "LifeOSAtlas/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            response_json(self, {"error": "not_found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            body = {}
        self.handle_api_post(parsed, body)

    def log_message(self, format: str, *args) -> None:
        print(f"[Atlas] {self.address_string()} - {format % args}")

    def handle_api_get(self, parsed) -> None:
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        conn = connect()
        init_db(conn)
        try:
            if path == "/api/health":
                response_json(self, {"ok": True, "host": HOST, "network": "loopback-only"})
            elif path == "/api/summary":
                response_json(self, atlas_engine.summary(conn))
            elif path == "/api/files":
                q = (query.get("q") or [""])[0].lower()
                rows = fetch_all(conn, "SELECT * FROM source_records ORDER BY priority, filename LIMIT 1500")
                allowed = authorised_paths()
                rows = [row for row in rows if row["full_path"].lower() in allowed]
                for row in rows:
                    if row["department"] == "Empty / Placeholder Audit":
                        cands = fetch_all(conn, "SELECT id, full_path, size_bytes, modified_at, root_label FROM source_records WHERE filename=? AND id!=? AND size_bytes>0", (row["filename"], row["id"]))
                        row["historical_candidates"] = [c for c in cands if c["full_path"].lower() in allowed]
                if q:
                    rows = [row for row in rows if q in " ".join(str(row.get(k, "")) for k in ("filename", "relative_path", "purpose", "department")).lower()]
                response_json(self, {"files": rows})
            elif path.startswith("/api/file/"):
                source_id = int(path.rsplit("/", 1)[-1])
                row = fetch_one(conn, "SELECT * FROM source_records WHERE id=?", (source_id,))
                if row and row["full_path"].lower() not in authorised_paths():
                    row = None
                if row:
                    row["headings"] = json_loads(row.get("headings_json"), [])
                    row["yaml"] = json_loads(row.get("yaml_json"), {})
                    row["links"] = json_loads(row.get("links_json"), [])
                response_json(self, {"file": row})
            elif path == "/api/duplicates":
                show_resolved = query.get("show_resolved", ["false"])[0].lower() == "true"
                groups = fetch_all(conn, "SELECT * FROM duplicate_groups ORDER BY confidence DESC, member_count DESC")
                
                # Fetch resolutions to filter or annotate
                resolutions = {row["group_id"]: row for row in fetch_all(conn, "SELECT * FROM duplicate_resolutions")}
                
                allowed = authorised_paths()
                final_groups = []
                for group in groups:
                    res = resolutions.get(group["id"])
                    if res and not show_resolved:
                        continue
                    group["resolution"] = res
                    
                    members = fetch_all(
                        conn,
                        """
                        SELECT s.id, s.filename, s.full_path, s.extension, s.priority, s.department,
                               s.root_label, s.modified_at, dm.proposed_role
                        FROM duplicate_members dm
                        JOIN source_records s ON s.id=dm.source_id
                        WHERE dm.group_id=?
                        ORDER BY dm.proposed_role DESC, s.filename
                        """,
                        (group["id"],),
                    )
                    members = [member for member in members if member["full_path"].lower() in allowed]
                    group["members"] = members
                    group["member_count"] = len(members)
                    group["extensions"] = sorted({row["extension"] for row in members})
                    group["priorities"] = sorted({row["priority"] for row in members})
                    group["departments"] = sorted({row["department"] for row in members})
                    group["canonical"] = next((row for row in members if row["proposed_role"] == "proposed_canonical"), members[0] if members else None)
                    if group["member_count"] >= 2 or res:
                        final_groups.append(group)
                response_json(self, {"groups": final_groups})
            elif path.startswith("/api/comparison/"):
                group_id = int(path.rsplit("/", 1)[-1])
                comparison = comparison_engine.comparison_for_group(conn, group_id)
                allowed = authorised_paths()
                comparison["members"] = [row for row in comparison["members"] if row["full_path"].lower() in allowed]
                member_ids = {row["id"] for row in comparison["members"]}
                comparison["comparisons"] = [
                    row for row in comparison["comparisons"]
                    if row["left_source_id"] in member_ids and row["right_source_id"] in member_ids
                ]
                if len(comparison["members"]) < 2:
                    comparison["group"] = None
                response_json(self, comparison)
            elif path.startswith("/api/relationships/"):
                source_id = int(path.rsplit("/", 1)[-1])
                source = fetch_one(conn, "SELECT full_path FROM source_records WHERE id=?", (source_id,))
                if not source or source["full_path"].lower() not in authorised_paths():
                    response_json(self, {"source": None, "nodes": [], "edges": [], "links": [], "related_files": [], "incoming": []}, 404)
                else:
                    response_json(self, comparison_engine.relationship_map(conn, source_id))
            elif path == "/api/canonical":
                rows = fetch_all(
                    conn,
                    """
                    SELECT cp.*, dg.label, sr.filename, sr.full_path, sr.modified_at AS canonical_modified_at
                    FROM canonical_proposals cp
                    LEFT JOIN duplicate_groups dg ON dg.id=cp.group_id
                    LEFT JOIN source_records sr ON sr.id=cp.canonical_source_id
                    ORDER BY cp.confidence DESC
                    """,
                )
                allowed = authorised_paths()
                rows = [row for row in rows if row.get("full_path", "").lower() in allowed]
                for row in rows:
                    members = fetch_all(
                        conn,
                        """
                        SELECT s.id, s.filename, s.full_path, s.root_label, s.modified_at, s.extension,
                               dm.proposed_role
                        FROM duplicate_members dm
                        JOIN source_records s ON s.id=dm.source_id
                        WHERE dm.group_id=?
                        ORDER BY dm.proposed_role DESC, s.modified_at DESC, s.filename
                        """,
                        (row.get("group_id"),),
                    )
                    members = [member for member in members if member["full_path"].lower() in allowed]
                    row["supporting_files"] = [member for member in members if member["id"] != row.get("canonical_source_id")]
                    canonical_modified = row.get("canonical_modified_at") or ""
                    row["older_versions"] = [
                        member for member in members
                        if member["id"] != row.get("canonical_source_id") and member.get("modified_at", "") < canonical_modified
                    ][:8]
                    row["conflicting_files"] = []
                    row["linked_indexes"] = [
                        member for member in members
                        if "index" in member.get("filename", "").lower()
                    ][:8]
                    row["final_canonical_path"] = "" if row.get("status") != "owner_confirmed" else row.get("proposed_destination", "")
                response_json(self, {"proposals": rows})
            elif path == "/api/decisions":
                rows = fetch_all(
                    conn,
                    """
                    SELECT d.*, s.filename AS linked_filename, s.full_path AS linked_full_path,
                           dg.label AS group_label, dg.group_type
                    FROM decisions d
                    LEFT JOIN source_records s ON s.id=d.source_id
                    LEFT JOIN duplicate_groups dg ON dg.id=d.group_id
                    ORDER BY d.decision_type, d.confidence DESC, d.id DESC
                    """,
                )
                allowed = authorised_paths()
                rows = [row for row in rows if not row.get("linked_full_path") or row["linked_full_path"].lower() in allowed]
                response_json(self, {"decisions": rows})
            elif path == "/api/ai/status":
                response_json(self, ai_connectors.provider_status())
            elif path == "/api/proposals/folders":
                response_json(self, {"root": str(merge_engine.PROPOSALS_DIR), "folders": merge_engine.list_proposal_folders()})
            else:
                response_json(self, {"error": "not_found"}, 404)
        finally:
            conn.close()

    def handle_api_post(self, parsed, body: dict) -> None:
        conn = connect()
        init_db(conn)
        try:
            if parsed.path == "/api/audit/run":
                limit = body.get("limit")
                response_json(self, atlas_engine.run_audit(limit=limit))
            elif parsed.path == "/api/decisions/build":
                atlas_engine.create_decision_cards(conn)
                response_json(self, {"ok": True})
            elif parsed.path == "/api/merge/preview":
                preview = merge_engine.create_merge_preview(
                    conn,
                    int(body.get("canonical_id")),
                    [int(item) for item in body.get("source_ids", [])],
                )
                response_json(self, preview)
            elif parsed.path == "/api/merge/review-copy":
                response_json(
                    self,
                    merge_engine.create_merged_review_copy(
                        conn,
                        int(body.get("canonical_id")),
                        [int(item) for item in body.get("source_ids", [])],
                        [str(item) for item in body.get("selected_fingerprints", [])],
                        str(body.get("destination_folder", ".")),
                        str(body.get("filename", "")),
                    ),
                    201,
                )
            elif parsed.path == "/api/empty/resolve":
                source_id = int(body.get("source_id"))
                decision = body.get("decision")
                reason = body.get("reason", "")
                
                if decision == "Restore from source":
                    candidate_id = body.get("candidate_id")
                    output_path = body.get("output_path")
                    if not candidate_id or not output_path:
                        response_json(self, {"error": "Candidate ID and Output Path required for Restore"}, 400)
                        return
                    cand = fetch_one(conn, "SELECT full_path FROM source_records WHERE id=?", (candidate_id,))
                    if not cand:
                        response_json(self, {"error": "Candidate not found"}, 400)
                        return
                    out_path = Path(output_path)
                    try:
                        target_path = merge_engine._safe_proposal_path(str(out_path.parent), out_path.name, out_path.suffix or ".md")
                    except Exception as e:
                        response_json(self, {"error": str(e)}, 400)
                        return
                    if target_path.exists():
                        response_json(self, {"error": "File already exists."}, 400)
                        return
                    import shutil
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(cand["full_path"], target_path)
                    exact_ref = str(target_path)
                else:
                    exact_ref = ""
                    
                from datetime import datetime, timezone
                conn.execute(
                    """
                    INSERT INTO decisions(source_id, decision_type, exact_reference, reason, uncertainty, recommended_option, alternative_options_json, confidence, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_id, decision, exact_ref, reason, "none", decision, "[]", 1.0, datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                response_json(self, {"ok": True})
            elif parsed.path == "/api/ai/draft":
                mode = body.get("mode", "Local only")
                model = body.get("model", "")
                purpose = body.get("purpose", "")
                files = body.get("files", [])
                content = body.get("content", "")
                owner_confirmed = body.get("owner_confirmed_external_transfer", False)
                req = ai_connectors.AIRequest(mode, model, purpose, files, content, owner_confirmed)
                try:
                    result = ai_connectors.call_provider(req)
                    response_json(self, {"draft": result})
                except Exception as e:
                    response_json(self, {"error": str(e)}, 400)
            elif parsed.path == "/api/files/open-folder":
                source_id = int(body.get("source_id"))
                row = fetch_one(conn, "SELECT * FROM source_records WHERE id=?", (source_id,))
                if not row:
                    raise ValueError("Source record not found.")
                if not scope_reader.is_path_authorised(row["full_path"]):
                    raise PermissionError("Source path is outside the authorised cleanup scope.")
                source_path = Path(row["full_path"])
                folder = source_path.parent.resolve()
                if not folder.is_dir():
                    raise FileNotFoundError("Containing folder is unavailable.")
                if os.name != "nt" or not hasattr(os, "startfile"):
                    raise RuntimeError("Open folder is available only on Windows.")
                os.startfile(str(folder))
                response_json(self, {"ok": True, "folder": str(folder)})
            elif parsed.path == "/api/rollback/test-fixture":
                result = action_executor.create_test_fixture_bundle()
                conn.execute(
                    """
                    INSERT INTO rollback_records(bundle_path, source_paths_json, created_at, test_fixture)
                    VALUES(?, ?, datetime('now'), 1)
                    """,
                    (result["bundle_path"], json.dumps(result["manifest"]["files"])),
                )
                conn.commit()
                response_json(self, result)
            elif parsed.path == "/api/choose-folder":
                import tkinter as tk
                from tkinter import filedialog
                
                def _ask_dir():
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    folder = filedialog.askdirectory(parent=root, title="Select Destination Folder")
                    root.destroy()
                    return folder

                if os.name != "nt":
                    response_json(self, {"error": "Folder picker available only on Windows."}, 400)
                    return
                # Must run in main thread or carefully in separate thread. 
                # Tkinter can be picky about threads. 
                # Let's try running it in the current thread since HTTP handler is a thread.
                # Actually, tkinter requires main thread on mac, but works fine on windows in any thread.
                folder_path = _ask_dir()
                if folder_path:
                    response_json(self, {"ok": True, "folder": folder_path.replace("\\", "/")})
                else:
                    response_json(self, {"ok": False, "reason": "cancelled"})
            elif parsed.path.startswith("/api/group/") and parsed.path.endswith("/resolve"):
                group_id = int(parsed.path.split("/")[3])
                resolution_type = body.get("resolution_type")
                source_id = body.get("source_id")
                output_path = body.get("output_path", "")
                reason = body.get("reason", "")
                content = body.get("content", "")
                create_folder = body.get("create_folder", False)

                if resolution_type in ["retained", "manual", "ai"]:
                    if not output_path:
                        response_json(self, {"error": "Output path is required"}, 400)
                        return
                    out_path = Path(output_path)
                    try:
                        target_path = merge_engine._safe_proposal_path(str(out_path.parent), out_path.name, ".md")
                    except Exception as e:
                        response_json(self, {"error": str(e)}, 400)
                        return
                        
                    if target_path.exists():
                        response_json(self, {"error": "File already exists. Choose a new name."}, 400)
                        return
                        
                    if not target_path.parent.exists():
                        if not create_folder:
                            response_json(self, {"error": "folder_missing"}, 400)
                            return
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        
                    source = fetch_one(conn, "SELECT full_path FROM source_records WHERE id=?", (source_id,))
                    if not source:
                        response_json(self, {"error": "Source record not found"}, 400)
                        return
                        
                    if resolution_type == "retained":
                        import shutil
                        shutil.copy2(source["full_path"], target_path)
                    elif resolution_type == "manual":
                        target_path.write_text(content, encoding="utf-8")
                    elif resolution_type == "ai":
                        # For AI draft, we don't write to file yet, just save it to ai_jobs
                        conn.execute(
                            "INSERT INTO ai_jobs(purpose, selected_files_json) VALUES(?, ?)",
                            (f"Draft for group {group_id}", json.dumps([source["full_path"]]))
                        )

                from datetime import datetime, timezone
                group_row = fetch_one(conn, "SELECT group_key FROM duplicate_groups WHERE id=?", (group_id,))
                if not group_row:
                    response_json(self, {"error": "Group not found"}, 400)
                    return
                group_key = group_row["group_key"]

                conn.execute(
                    """
                    INSERT INTO duplicate_resolutions(group_id, group_key, resolution_type, source_id, output_path, reason, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_key) DO UPDATE SET
                        group_id=excluded.group_id,
                        resolution_type=excluded.resolution_type,
                        source_id=excluded.source_id,
                        output_path=excluded.output_path,
                        reason=excluded.reason,
                        created_at=excluded.created_at
                    """,
                    (group_id, group_key, resolution_type, source_id, output_path, reason, datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                response_json(self, {"ok": True})
            else:
                response_json(self, {"error": "not_found"}, 404)
        except Exception as exc:
            response_json(self, {"error": str(exc)}, 400)
        finally:
            conn.close()

    def serve_static(self, request_path: str) -> None:
        rel = request_path.lstrip("/") or "index.html"
        target = (DASHBOARD_ROOT / rel).resolve()
        try:
            target.relative_to(DASHBOARD_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.exists() or not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def migration_rows(conn) -> list[dict]:
    files = fetch_all(conn, "SELECT * FROM source_records ORDER BY priority, filename LIMIT 500")
    rows = []
    for row in files:
        destination = destination_for(row)
        rows.append(
            {
                "current_path": row["full_path"],
                "proposed_destination": destination,
                "proposed_action": "canonical / merge / archive decision required",
                "reason": f"{row['department']} | {row['phase']} | {row['purpose']}",
                "approval_status": "not approved",
            }
        )
    return rows


def destination_for(row: dict) -> str:
    dept = row["department"].lower()
    if "architecture" in dept or "memory" in dept:
        book = "Architecture and Memory Book"
    elif "research" in dept:
        book = "Research and Evidence Library"
    elif "technical" in dept:
        book = "Technical Development Library"
    elif "design" in dept:
        book = "Design / UI / UX Library"
    elif "operation" in dept:
        book = "Operations and Automation Library"
    elif "archive" in dept or "backup" in dept:
        book = "Controlled Reference Archive"
    else:
        book = "Executive Governance Book"
    return f"<proposal only>\\{book}\\{row['filename']}"


def run_server(port: int, open_browser: bool = False, audit: bool = False) -> None:
    if audit:
        print("Running read-only authorised-scope audit before launch...")
        atlas_engine.run_audit()
        with connect() as conn:
            atlas_engine.create_decision_cards(conn)
            
    def poll_audit():
        import time
        while True:
            time.sleep(5)
            try:
                atlas_engine.run_audit(background=True)
            except Exception as e:
                print(f"Background audit error: {e}")
                
    threading.Thread(target=poll_audit, daemon=True).start()
    
    server = ThreadingHTTPServer((HOST, port), AtlasHandler)
    url = f"http://{HOST}:{port}/"
    print(f"LifeOS Atlas running at {url}")
    print("Bound to 127.0.0.1 only. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("LIFEOS_ATLAS_PORT", DEFAULT_PORT)))
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    run_server(args.port, args.open, args.audit)


if __name__ == "__main__":
    main()
