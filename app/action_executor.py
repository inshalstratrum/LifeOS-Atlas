from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
ROLLBACK_DIR = APP_ROOT / "data" / "rollback_bundles"


def _inside_atlas(path: Path) -> bool:
    try:
        path.resolve().relative_to(APP_ROOT.resolve())
        return True
    except ValueError:
        return False


def create_rollback_bundle(paths: list[Path], action_plan_id: int | None = None, test_fixture: bool = False) -> dict:
    if not test_fixture:
        for path in paths:
            if not _inside_atlas(path):
                raise PermissionError("Rollback bundle creation for source roots requires an approved action plan.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle = ROLLBACK_DIR / f"rollback_{stamp}_{hashlib.sha1(str(paths).encode()).hexdigest()[:8]}"
    bundle.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "files": [], "test_fixture": test_fixture}
    for path in paths:
        if path.is_file():
            target = bundle / path.name
            shutil.copy2(path, target)
            manifest["files"].append({"source": str(path), "bundle_copy": str(target)})
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"bundle_path": str(bundle), "manifest": manifest, "action_plan_id": action_plan_id}


def create_test_fixture_bundle() -> dict:
    fixture = APP_ROOT / "data" / "rollback_test_fixture.txt"
    fixture.write_text("LifeOS Atlas rollback fixture. No source root touched.\n", encoding="utf-8")
    return create_rollback_bundle([fixture], test_fixture=True)

