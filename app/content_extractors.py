from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".html", ".htm", ".yaml", ".yml"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".docx"}


@dataclass
class ExtractedBlock:
    block_type: str
    heading: str
    line_start: int
    line_end: int
    text: str
    fingerprint: str


@dataclass
class ExtractionResult:
    supported: bool
    status: str
    text: str
    headings: list[dict[str, Any]]
    yaml_fields: dict[str, str]
    links: list[dict[str, Any]]
    blocks: list[ExtractedBlock]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value.strip().lower())
    value = re.sub(r"[^a-z0-9\u0600-\u06ff ]+", "", value)
    return value


def block_fingerprint(value: str) -> str:
    return hashlib.sha1(normalize_text(value).encode("utf-8", errors="ignore")).hexdigest()


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
    root = ET.fromstring(document)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
        if parts:
            paragraphs.append("".join(parts))
    return "\n\n".join(paragraphs)


def extract_yaml_frontmatter(lines: list[str]) -> dict[str, str]:
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields


def extract_links(line: str, line_no: int) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for target in re.findall(r"\[\[([^\]]+)\]\]", line):
        links.append({"type": "wiki", "target": target.strip(), "line": line_no})
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", line):
        links.append({"type": "markdown", "label": label.strip(), "target": target.strip(), "line": line_no})
    return links


def extract_structured_blocks(text: str, extension: str) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], list[ExtractedBlock]]:
    lines = text.splitlines()
    yaml_fields = extract_yaml_frontmatter(lines)
    headings: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    blocks: list[ExtractedBlock] = []
    current_heading = ""
    pending: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal pending, start_line
        chunk = "\n".join(pending).strip()
        if len(normalize_text(chunk)) >= 24:
            blocks.append(
                ExtractedBlock(
                    "paragraph",
                    current_heading,
                    start_line,
                    end_line,
                    chunk,
                    block_fingerprint(chunk),
                )
            )
        pending = []
        start_line = end_line + 1

    for index, line in enumerate(lines, 1):
        links.extend(extract_links(line, index))
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        html_heading = re.search(r"<h([1-6])[^>]*>(.*?)</h\1>", line, re.IGNORECASE)
        if heading_match or html_heading:
            flush(index - 1)
            level = len(heading_match.group(1)) if heading_match else int(html_heading.group(1))
            title = heading_match.group(2).strip() if heading_match else re.sub("<[^>]+>", "", html_heading.group(2)).strip()
            current_heading = title
            headings.append({"level": level, "text": title, "line": index})
            blocks.append(
                ExtractedBlock("heading", title, index, index, title, block_fingerprint(f"heading {title}"))
            )
            start_line = index + 1
            continue
        if not line.strip():
            flush(index - 1)
            continue
        if not pending:
            start_line = index
        pending.append(line)
    flush(len(lines))

    if extension == ".json":
        try:
            data = json.loads(text)
            keys = list(data.keys()) if isinstance(data, dict) else []
            for key in keys[:30]:
                headings.append({"level": 2, "text": str(key), "line": 1})
        except json.JSONDecodeError:
            pass

    if extension == ".csv":
        try:
            reader = csv.reader(text.splitlines())
            header = next(reader, [])
            for name in header[:30]:
                headings.append({"level": 2, "text": f"CSV field: {name}", "line": 1})
        except csv.Error:
            pass

    return headings, yaml_fields, links, blocks


def extract_file(path: Path) -> ExtractionResult:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return ExtractionResult(False, f"content comparison unavailable for {extension or 'extensionless'}", "", [], {}, [], [])
    try:
        text = read_docx(path) if extension == ".docx" else read_text_file(path)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        return ExtractionResult(False, f"extract_failed: {exc}", "", [], {}, [], [])
    headings, yaml_fields, links, blocks = extract_structured_blocks(text, extension)
    return ExtractionResult(True, "content indexed locally", text, headings, yaml_fields, links, blocks)

