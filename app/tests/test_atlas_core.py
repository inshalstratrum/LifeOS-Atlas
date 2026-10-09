from __future__ import annotations

import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

# Ensure application root is in python path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import action_executor, ai_connectors, atlas_engine, content_extractors, merge_engine
from app.database import init_db
from app.duplicate_engine import build_duplicate_groups
from app.scope_reader import is_path_authorised, parse_scope_tree


class AtlasCoreTests(unittest.TestCase):
    def test_scope_parser_blocks_excluded_paths(self) -> None:
        entries = parse_scope_tree()
        self.assertTrue(entries)
        self.assertFalse(any("99_ADMIN_BACKUP" in entry.full_path for entry in entries))
        self.assertFalse(any("80_ATTACHMENTS_MEDIA" in entry.full_path for entry in entries))
        self.assertFalse(any(".obsidian" in entry.full_path for entry in entries))
        self.assertFalse(any("source_cards" in entry.full_path.lower() for entry in entries))
        first = entries[0]
        self.assertTrue(is_path_authorised(first.full_path, entries))
        self.assertFalse(is_path_authorised(r"D:\LifeOS\Shali_LifeOS_Vault\.obsidian\config", entries))

    def test_extract_markdown_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("---\ntype: test\n---\n# Title\n\nA paragraph with [[Link]] and enough words for indexing.", encoding="utf-8")
            result = content_extractors.extract_file(path)
            self.assertTrue(result.supported)
            self.assertEqual(result.yaml_fields["type"], "test")
            self.assertEqual(result.headings[0]["text"], "Title")
            self.assertTrue(result.links)
            self.assertTrue(result.blocks)

    def test_duplicate_group_and_merge_preview(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = lambda cursor, row: {cursor.description[i][0]: row[i] for i in range(len(row))}
        init_db(conn)
        conn.execute("INSERT INTO audit_runs(started_at, status) VALUES(datetime('now'), 'test')")
        run_id = conn.execute("SELECT id FROM audit_runs").fetchone()["id"]
        common = "This shared governance paragraph is long enough to count as a local structural block."
        common_two = "This second shared approval paragraph creates a stronger near duplicate signal."
        files = [
            ("a.md", "aaa", f"{common}\n\n{common_two}", "same"),
            ("b.md", "bbb", f"{common}\n\n{common_two}\n\nUnique useful section for carry forward.", "same"),
        ]
        ids = []
        for name, sha, text, fp in files:
            cursor = conn.execute(
                """
                INSERT INTO source_records(run_id, root_label, root_path, relative_path, full_path, filename,
                  extension, size_bytes, sha256, content_supported, content_status, purpose, department,
                  phase, authority, lifecycle, priority, classification_basis)
                VALUES(?, 'test', 'D:\\x', ?, ?, ?, '.md', 10, ?, 1, 'ok', 'purpose',
                  'Needs Review', 'Triage', 'test', 'Audit', 'P2', 'test')
                """,
                (run_id, name, f"D:\\x\\{name}", name, sha),
            )
            source_id = cursor.lastrowid
            ids.append(source_id)
            for index, part in enumerate(text.split("\n\n")):
                fingerprint = f"same-{index}" if index < 2 else "unique"
                conn.execute(
                    """
                    INSERT INTO content_blocks(source_id, block_index, block_type, line_start, line_end, fingerprint, snippet, char_count)
                    VALUES(?, ?, 'paragraph', ?, ?, ?, ?, ?)
                    """,
                    (source_id, index, index + 1, index + 1, fingerprint, part, len(part)),
                )
        conn.commit()
        build_duplicate_groups(conn)
        groups = conn.execute("SELECT * FROM duplicate_groups").fetchall()
        self.assertTrue(groups)
        preview = merge_engine.create_merge_preview(conn, ids[0], [ids[1]])
        self.assertEqual(preview["safety"], "Preview only. No source file is modified by this operation.")
        self.assertTrue(preview["additions"])
        atlas_engine.create_decision_cards(conn)
        decision = conn.execute("SELECT exact_reference FROM decisions LIMIT 1").fetchone()
        self.assertIn("lines", decision["exact_reference"])
        self.assertIn("block:", decision["exact_reference"])

    def test_rollback_fixture_stays_inside_atlas(self) -> None:
        result = action_executor.create_test_fixture_bundle()
        bundle = Path(result["bundle_path"]).resolve()
        bundle.relative_to(action_executor.APP_ROOT.resolve())
        self.assertTrue((bundle / "manifest.json").exists())

    def test_review_copy_is_confined_and_sources_stay_unchanged(self) -> None:
        proposals = merge_engine.PROPOSALS_DIR
        proposals.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=proposals) as tmp:
            fixture_dir = Path(tmp)
            canonical_path = fixture_dir / "canonical.md"
            source_path = fixture_dir / "source.md"
            canonical_text = "# Canonical\n\nExisting canonical material."
            source_text = "# Source\n\nUnique useful material that should appear in the separate review copy."
            canonical_path.write_text(canonical_text, encoding="utf-8")
            source_path.write_text(source_text, encoding="utf-8")

            conn = sqlite3.connect(":memory:")
            conn.row_factory = lambda cursor, row: {cursor.description[i][0]: row[i] for i in range(len(row))}
            init_db(conn)
            conn.execute("INSERT INTO audit_runs(started_at, status) VALUES(datetime('now'), 'test')")
            run_id = conn.execute("SELECT id FROM audit_runs").fetchone()["id"]
            ids = []
            for path in (canonical_path, source_path):
                cursor = conn.execute(
                    """
                    INSERT INTO source_records(run_id, root_label, root_path, relative_path, full_path, filename,
                      extension, size_bytes, sha256, content_supported, content_status, purpose, department,
                      phase, authority, lifecycle, priority, classification_basis, created_in_atlas)
                    VALUES(?, 'test', ?, ?, ?, ?, '.md', 10, ?, 1, 'ok', 'purpose', 'Needs Review',
                      'Triage', 'test', 'Audit', 'P2', 'test', 1)
                    """,
                    (run_id, str(fixture_dir), path.name, str(path), path.name, path.name),
                )
                ids.append(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO content_blocks(source_id, block_index, block_type, line_start, line_end,
                  fingerprint, snippet, char_count)
                VALUES(?, 0, 'paragraph', 3, 3, 'unique-review-block', ?, 75)
                """,
                (ids[1], "Unique useful material that should appear in the separate review copy."),
            )
            conn.commit()

            relative_folder = fixture_dir.relative_to(proposals).as_posix()
            result = merge_engine.create_merged_review_copy(
                conn, ids[0], [ids[1]], ["unique-review-block"], relative_folder, "review.md"
            )
            output = Path(result["output_path"]).resolve()
            output.relative_to(proposals.resolve())
            self.assertEqual(canonical_path.read_text(encoding="utf-8"), canonical_text)
            self.assertEqual(source_path.read_text(encoding="utf-8"), source_text)
            self.assertIn("Unique useful material", output.read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                merge_engine._safe_proposal_path("../outside", "review.md", ".md")

    def test_ai_modes_exclude_chatgpt_and_network_stays_off(self) -> None:
        status = ai_connectors.provider_status()
        self.assertEqual(status["modes"], ["Local only", "Gemini", "OpenRouter"])
        self.assertFalse(status["network_enabled"])


if __name__ == "__main__":
    unittest.main()
