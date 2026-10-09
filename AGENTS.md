# Atlas Governance

LifeOS Atlas is a local control layer for the LifeOS file estate. It must remain an offline, dependency-free application built from HTML, CSS, vanilla JavaScript, inline SVG, Canvas 2D, JSON, and Windows PowerShell 5.1.

## Boundaries

- Work only inside `D:\LifeOS\LifeOS_Atlas`.
- Do not modify, move, rename, deduplicate, archive, or rewrite any external LifeOS source file.
- Authorize local file copying (retained copies), manual merge outputs, and local AI workspace staging (future session-only Gemini/OpenRouter staging) to owner-approved destination folders outside of the protected source roots.
- Do not inspect external source roots during dashboard work unless the owner explicitly asks for a scanner run.
- Banned: external source edits, packages, frameworks, CDNs, fonts, telemetry, automatic external actions, deployment configuration, and provider calls during this task.
- Allowed extensions: Atlas's approved local SQLite database, local HTTP server on `127.0.0.1`, and future session-only Gemini/OpenRouter staging (via env vars).
- `.kilo` is out of scope for Atlas edits.

## Authority Zones

1. `D:\LifeOS\LifeOS_Codex_Audit` - Control Plane + Historical Authority. Never silently active canonical.
2. `D:\LifeOS\Shali_LifeOS_Vault` - Active LifeOS Vault. Controlled canonical updates only.
3. `C:\LifeOS\LifeOS_Backups\Shali_LifeOS_Vault_Mirror` - Backup Mirror. Read-only recovery context, never canonical.
4. `D:\LifeOS\Kilo_ReadOnly_Audit_Workspace` - Sanitised Audit Sandbox. Read-only audit evidence, never canonical.

## Classification Policy

Atlas derives classifications only from metadata: root, role, relative path, folder chain, filename, extension, size, dates, attributes, explicit rules, and manual overrides. It never claims document-body understanding. Inferred results must be labelled as deterministically derived, and uncertain records remain `Needs Review`.

## Protected Material

Protected material is count-only. Protected records must never expose filename, relative path, folder chain, extension, safe full path, or Copy Path.

## Update Policy

One logical document equals one canonical path. Atlas may flag possible duplicates, versions, mirror gaps, and governance risks, but it must never act automatically. Financial, external, source-root, destructive, or canonical-changing actions require explicit human approval.

## Validation

Before reporting completion, verify local dashboard loading, data wrapper order, search, filters, virtual tree, Inspector Drawer, raw/visual tabs, related-record navigation, lifecycle and governance timelines, duplicate/version honesty, backup integrity honesty, Canvas landscape, responsive layout, keyboard/focus behavior, reduced-motion compatibility, and absence of network/dependency additions.
