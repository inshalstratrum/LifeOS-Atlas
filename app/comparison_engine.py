from __future__ import annotations

from .database import fetch_all, fetch_one, json_loads


def comparison_for_group(conn, group_id: int) -> dict:
    group = fetch_one(conn, "SELECT * FROM duplicate_groups WHERE id=?", (group_id,))
    members = fetch_all(
        conn,
        """
        SELECT s.*, dm.proposed_role, dm.rationale
        FROM duplicate_members dm
        JOIN source_records s ON s.id = dm.source_id
        WHERE dm.group_id=?
        ORDER BY dm.proposed_role DESC, s.filename
        """,
        (group_id,),
    )
    comparisons = fetch_all(conn, "SELECT * FROM comparisons WHERE group_id=? ORDER BY similarity DESC", (group_id,))
    for comparison in comparisons:
        comparison["comparison"] = json_loads(comparison.get("comparison_json"), {})
    return {"group": group, "members": members, "comparisons": comparisons}


def relationship_map(conn, source_id: int) -> dict:
    source = fetch_one(conn, "SELECT * FROM source_records WHERE id=?", (source_id,))
    if not source:
        return {"source": None, "nodes": [], "edges": []}
    links = json_loads(source.get("links_json"), [])
    nodes = [{"id": f"source:{source_id}", "label": source["filename"], "type": "file"}]
    edges = []
    for index, link in enumerate(links[:40]):
        node_id = f"link:{index}"
        nodes.append({"id": node_id, "label": link.get("target") or link.get("label"), "type": link.get("type", "link")})
        edges.append({"from": f"source:{source_id}", "to": node_id, "label": f"line {link.get('line', '?')}"})
    related = fetch_all(
        conn,
        """
        SELECT DISTINCT s.*, dg.id AS group_id, dg.group_type, dg.label AS group_label
        FROM duplicate_members dm
        JOIN duplicate_groups dg ON dg.id = dm.group_id
        JOIN duplicate_members peer ON peer.group_id = dm.group_id AND peer.source_id != dm.source_id
        JOIN source_records s ON s.id = peer.source_id
        WHERE dm.source_id=?
        LIMIT 20
        """,
        (source_id,),
    )
    for row in related:
        node_id = f"file:{row['id']}"
        nodes.append({"id": node_id, "label": row["filename"], "type": row["group_type"]})
        edges.append({"from": f"source:{source_id}", "to": node_id, "label": row["group_type"]})

    incoming = []
    source_names = {source["filename"].lower(), source["filename"].rsplit(".", 1)[0].lower()}
    for candidate in fetch_all(conn, "SELECT id, filename, links_json FROM source_records WHERE id != ?", (source_id,)):
        for link in json_loads(candidate.get("links_json"), []):
            target = str(link.get("target") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
            target_names = {target, target.rsplit(".", 1)[0]}
            if source_names & target_names:
                incoming.append({
                    "source_id": candidate["id"],
                    "filename": candidate["filename"],
                    "line": link.get("line"),
                    "relation": "incoming markdown/wiki reference",
                })
                break
    return {"source": source, "nodes": nodes, "edges": edges, "links": links, "related_files": related, "incoming": incoming[:40]}
