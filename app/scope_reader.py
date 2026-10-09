from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
SCOPE_TREE = APP_ROOT / "docs" / "1st_cleanup_scope_tree.md"
AUTHORISED_SCOPE_JSON = APP_ROOT / "config" / "authorised_scope.json"
EXCLUSIONS_JSON = APP_ROOT / "config" / "exclusions.json"


FILE_EXT_RE = re.compile(
    r"\.(md|markdown|txt|json|csv|html?|ya?ml|docx|pdf|zip|png|jpe?g|gif|webp|mp3|mp4|wav|xlsx?|pptx?)$",
    re.IGNORECASE,
)

AUTHORIZED_ROOTS = [
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\01_CODEX_OUTPUT", "Codex Output"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\03_MASTER_REBUILD_PLAN", "Master Rebuild Plan"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\04_REBUILT_MASTER_FILES", "Rebuilt Master Files"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\05_MASTER_ARCHITECTURE_BLUEPRINT", "Architecture Blueprint"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\App-01_Authority_Architecture_Pack", "Authority Architecture Pack"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\LifeOS_Memory_Database_Architecture_Decision_Pack", "Memory Database Architecture"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\LifeOS_Memory_Database_Gate_A_Reconciliation_Draft", "Gate A Reconciliation"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\Obsidian _vault_Shali_LifeOS_Vault\\00_PANEL_MEMORY_BRIDGE", "Panel Memory Bridge"),
    ("D:\\LifeOS\\LifeOS_Codex_Audit\\Shali_LifeOS_Vault", "Codex Shali Vault"),
    ("D:\\LifeOS\\Shali_LifeOS_Vault", "Shali Vault"),
]

DEFAULT_EXCLUSIONS = [
    ".obsidian",
    "01_conversation_archive",
    "raw dumps",
    "raw_dump",
    "raw conversations",
    "raw_conversation",
    "cognitive abyss",
    "session dumps",
    "source_cards",
    "source-card",
    "source cards",
    "99_admin_backup",
    "80_attachments_media",
    "08_mirror_manifests",
    "09_panel_memory_bridge_reference_mirror",
    "source_and_mirror_manifests",
    "kilo_readonly_audit_workspace",
    "backup mirror",
    "lifeos_backups",
]


@dataclass(frozen=True)
class ScopeEntry:
    full_path: str
    root_label: str
    root_path: str
    relative_path: str
    declared_line: int


def _normal_path(value: str) -> str:
    value = value.replace("/", "\\")
    value = re.sub(r"\\{2,}", r"\\", value)
    return value.strip()


def _root_for(path: str) -> tuple[str, str]:
    lower = path.lower()
    best = ("External authorised", "")
    for root, label in AUTHORIZED_ROOTS:
        if lower.startswith(root.lower()):
            if len(root) > len(best[1]):
                best = (label, root)
    if best[1]:
        return best
    drive = re.match(r"^[A-Z]:\\[^\\]+\\[^\\]+", path, re.IGNORECASE)
    root_path = drive.group(0) if drive else str(Path(path).anchor)
    return best[0], root_path


def _is_excluded(path: str, exclusions: list[str]) -> bool:
    low = path.lower().replace("/", "\\")
    low_spaces = low.replace("\\", " ")
    return any(term.lower() in low or term.lower() in low_spaces for term in exclusions)


def _unescape_tree_text(value: str) -> str:
    value = value.strip().rstrip()
    value = value.replace("\\_", "_").replace("\\+", "+").replace("\\-", "-")
    return value.replace("\\\\", "\\")


def _tree_depth(prefix: str) -> int:
    depth = 0
    while len(prefix) >= 4 and prefix[:4] in ("|   ", "    "):
        depth += 1
        prefix = prefix[4:]
    return depth


def _parse_declared_paths(scope_path: Path, exclusions: list[str]) -> tuple[list[ScopeEntry], list[tuple[str, str]]]:
    entries: dict[str, ScopeEntry] = {}
    excluded: list[tuple[str, str]] = []
    current_root = ""
    folder_stack: list[str] = []

    for line_no, raw_line in enumerate(scope_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        heading = re.search(r"\*\*([A-Za-z]:\\\\.+?)\*\*", raw_line)
        if heading:
            current_root = _normal_path(_unescape_tree_text(heading.group(1)))
            folder_stack = []
            continue
        if not current_root:
            continue

        line = raw_line.rstrip()
        marker = re.search(r"(?:\\?[+\\]---)(.+?)\s*$", line)
        if marker:
            prefix = line[:marker.start()]
            depth = _tree_depth(prefix)
            folder_name = _unescape_tree_text(marker.group(1))
            folder_stack = folder_stack[:depth]
            folder_stack.append(folder_name)
            continue

        unescaped = _unescape_tree_text(line)
        match = FILE_EXT_RE.search(unescaped)
        if not match:
            continue
        filename = unescaped[:match.end()].split("|")[-1].strip()
        if not filename or filename.startswith("D:\\"):
            continue
        prefix = line[: line.lower().rfind(match.group(0).lower().replace("_", "\\_"))]
        depth = _tree_depth(prefix)
        parents = folder_stack[:depth] if folder_stack else []
        full_path = _normal_path(str(Path(current_root, *parents, filename)))
        if _is_excluded(full_path, exclusions):
            excluded.append((full_path, "excluded_by_term"))
            continue
        root_label, root_path = _root_for(full_path)
        relative_path = full_path[len(root_path):].lstrip("\\") if root_path else filename
        entries[full_path.lower()] = ScopeEntry(full_path, root_label, root_path, relative_path, line_no)

    return sorted(entries.values(), key=lambda item: item.full_path.lower()), excluded


def scan_recursive(root_path: str, exclusions: list[str]) -> tuple[list[str], int, list[tuple[str, str]]]:
    root = Path(root_path)
    if not root.exists():
        return [], 0, []
    files = []
    count_before = 0
    excluded_paths = []
    for item in root.rglob("*"):
        count_before += 1
        if item.is_file() and FILE_EXT_RE.search(item.name):
            full = _normal_path(str(item))
            if _is_excluded(full, exclusions):
                excluded_paths.append((full, "excluded_by_term"))
            else:
                files.append(full)
    return files, count_before, excluded_paths


def parse_scope_tree(scope_path: Path | None = None, exclusions: list[str] | None = None) -> list[ScopeEntry]:
    exclusions = exclusions or DEFAULT_EXCLUSIONS
    entries, _ = _parse_declared_paths(scope_path or SCOPE_TREE, exclusions)
    return entries


def scan_roots_with_counts(exclusions: list[str] | None = None) -> tuple[list[ScopeEntry], int, int, list[tuple[str, str]], list[tuple[str, str]]]:
    exclusions = exclusions or DEFAULT_EXCLUSIONS
    entries, excluded = _parse_declared_paths(SCOPE_TREE, exclusions)
    missing_roots = [(root, "missing_root") for root, _ in AUTHORIZED_ROOTS if not Path(root).exists()]
    return entries, len(entries), len(entries) + len(excluded), excluded, missing_roots


def build_scope_config() -> dict:
    entries, unique_count, total_before, excluded, missing = scan_roots_with_counts()
    roots: dict[str, dict] = {}
    for entry in entries:
        root = roots.setdefault(
            entry.root_path,
            {"label": entry.root_label, "path": entry.root_path, "file_count": 0},
        )
        root["file_count"] += 1
    return {
        "schema_version": 1,
        "source": "docs/1st_cleanup_scope_tree.md",
        "policy": "Only explicitly declared files are eligible; excluded paths never index.",
        "roots": list(roots.values()),
        "files": [entry.__dict__ for entry in entries],
    }


def write_scope_configs() -> None:
    AUTHORIZED = build_scope_config()
    AUTHORISED_SCOPE_JSON.write_text(json.dumps(AUTHORIZED, indent=2), encoding="utf-8")
    EXCLUSIONS_JSON.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "excluded_terms": DEFAULT_EXCLUSIONS,
                "policy": "Excluded paths cannot be scanned even if a similar path appears in the scope tree.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def is_path_authorised(path: str, entries: list[ScopeEntry] | None = None) -> bool:
    entries = entries or parse_scope_tree()
    target = _normal_path(path).lower()
    return any(entry.full_path.lower() == target for entry in entries)
