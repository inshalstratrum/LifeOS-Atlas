# 🗺️ LifeOS Atlas: Semantic Deduplication & Diff Engine

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Architecture: Zero Dependency](https://img.shields.io/badge/Dependencies-Standard%20Library%20Only-success.svg)](https://docs.python.org/3/)
[![UI: Vanilla Web Dashboard](https://img.shields.io/badge/UI-Vanilla%20JS%20%2B%20Canvas%202D-orange.svg)](dashboard/)
[![Database: SQLite WAL](https://img.shields.io/badge/Storage-SQLite3%20WAL-blue.svg)](app/database.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**LifeOS Atlas** is a zero-dependency, local loopback-only control system for **semantic document deduplication, multi-variant diffing, structural comparison, and controlled merge planning**.

It inspects large document repositories (Markdown, Text, HTML, CSV, JSON, DOCX), detects exact duplicates and fuzzy content variants, visualizes differences side-by-side in a responsive browser canvas, and prepares rollback-protected merge proposals.

---

## 🌟 Key Capabilities

### 1. 🔍 Semantic Deduplication & Variant Grouping
* **Exact Hash Duplicate Detection**: Bit-level SHA-256 fingerprinting.
* **Structural Block Similarity**: Breaks documents into semantic blocks and computes repeated local fingerprint distributions.
* **Version Progression Tracking**: Detects version chains (`v1.0`, `v1.1`, `_draft`, `_final`, `_merged`) and maps lineage graphs.

### 2. 🔀 Multi-Variant Document Diff Engine
* **Side-by-Side Visual Diffing**: Compares multiple variations of the same document across different folders and dates.
* **Normalized Diff Scoring**: Computes line-by-line delta percentages, additions, subtractions, and structural modifications.
* **Block-Level Alignment**: Matches corresponding sections and headings across documents even when rearranged.

### 3. 🛡️ Safe Merge Planning & Rollback Protection
* **Preview-Only Workspaces**: Merges generate interactive diff previews before touching source files.
* **Immutable Rollback Bundles**: Every executed action creates a self-contained rollback bundle (`manifest.json` + snapshot) for instant reversal.
* **Strict Consent Ladder**: No destructive move, delete, or overwrite occurs silently.

### 4. 🚀 Zero-Dependency Architecture
* Runs on **100% Python standard library** (`ThreadingHTTPServer`, `sqlite3`, `pathlib`, `hashlib`, `xml.etree`).
* Frontend built with vanilla JavaScript, CSS3, and Canvas 2D without Node.js, npm build steps, CDNs, or external tracking.

---

## 📂 Repository Structure

```
LifeOS-Atlas/
├── app/
│   ├── server.py              # Loopback HTTP server & REST JSON API (127.0.0.1)
│   ├── atlas_engine.py        # Read-only audit orchestration & graph indexer
│   ├── comparison_engine.py   # Multi-variant side-by-side diff engine
│   ├── duplicate_engine.py    # Exact hash, version lineage & similarity grouping
│   ├── merge_engine.py        # Non-destructive merge planner & conflict resolver
│   ├── content_extractors.py  # Local parser for MD, TXT, JSON, CSV, HTML, YAML, DOCX
│   ├── action_executor.py     # Rollback bundle manager
│   ├── scope_reader.py        # Scope crawler with strict exclusion gates
│   ├── database.py            # SQLite WAL relational index
│   └── tests/                 # Automated test suite
├── dashboard/                 # Single-page web dashboard (Vanilla JS + CSS)
│   ├── index.html
│   └── assets/
│       ├── app.js             # Visual diff canvas & interactive inspector
│       └── styles.css
├── config/                    # Scope rules, exclusion lists & classification rules
├── data/                      # Local database & rollback bundle storage (ignored)
├── Run_LifeOS_Atlas.bat       # 1-Click Windows launcher
├── requirements.txt
├── .gitignore
├── LICENSE                    # MIT License
└── README.md
```

---

## 🚀 Quick Start Guide

### 1. Requirements
* **Python 3.10, 3.11, or 3.12** installed and on system PATH.
* Any modern web browser (Chrome, Firefox, Edge, Safari).

### 2. Launch the Application
Double-click `Run_LifeOS_Atlas.bat`, or run from terminal:

```bash
python app/server.py --open --audit
```

The server automatically starts on `http://127.0.0.1:8765` and opens your browser.

### 3. Run Automated Tests
```bash
python -m unittest discover app/tests
```

---

## 🔒 Safety & Privacy Principles

* **Loopback Only**: Server binds strictly to `127.0.0.1`.
* **Zero Telemetry**: No network requests or tracking analytics.
* **Isolated Data**: Personal databases, proposals, and vault contents are strictly excluded by `.gitignore`.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
