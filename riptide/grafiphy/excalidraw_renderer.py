#!/usr/bin/env python3
"""
excalidraw_renderer.py — Polished Excalidraw review diagram generator.

Produces sectioned, graphify-informed Excalidraw JSON with severity colors,
code chunks, god nodes, communities, and connections — all with WHY: annotations.

Uses the Excalidraw skill's official color palette (see excalidraw skill
references/colors.md).

Usage:
    from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw
    url = render_review(pr_data, findings, graph_data=..., code_chunks=...)
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

# ── Excalidraw Skill Official Palette ──────────────────────────────
# Pastel fills
LIGHT_BLUE = "#a5d8ff"
LIGHT_GREEN = "#b2f2bb"
LIGHT_ORANGE = "#ffd8a8"
LIGHT_PURPLE = "#d0bfff"
LIGHT_RED = "#ffc9c9"
LIGHT_YELLOW = "#fff3bf"
LIGHT_TEAL = "#c3fae8"
LIGHT_PINK = "#eebefa"

# Primary strokes
BLUE = "#4a9eed"
AMBER = "#f59e0b"
GREEN = "#22c55e"
RED = "#ef4444"
PURPLE = "#8b5cf6"
PINK = "#ec4899"
CYAN = "#06b6d4"
LIME = "#84cc16"

# Dark text variants (for readability on light fills)
DARK_RED = "#b91c1c"
DARK_GREEN = "#15803d"
DARK_BLUE = "#1e3a5f"
DARK_AMBER = "#9a5030"
DARK_PURPLE = "#5b21b6"

# Background zones (opacity ~30 when used as full-section backdrops)
ZONE_BLUE = "#dbe4ff"
ZONE_PURPLE = "#e5dbff"
ZONE_GREEN = "#d3f9d8"

# ── Severity Map ───────────────────────────────────────────────────
SEVERITY = {
    "critical":   (LIGHT_RED,    RED,    DARK_RED),
    "warning":    (LIGHT_ORANGE, AMBER,  DARK_AMBER),
    "suggestion": (LIGHT_PURPLE, PURPLE, DARK_PURPLE),
    "approved":   (LIGHT_GREEN,  GREEN,  DARK_GREEN),
    "info":       (LIGHT_BLUE,   BLUE,   DARK_BLUE),
}

SEVERITY_ICON = {
    "critical":   "!!",
    "warning":    "!",
    "suggestion": "?",
    "approved":   "OK",
    "info":       "i",
}


# ── Element Helpers ─────────────────────────────────────────────────

def make_rect(eid: str, x: int, y: int, w: int, h: int,
              bg: str, stroke: str = "#1e1e1e",
              opacity: int = 100, roundness: bool = True,
              text_id: str = None) -> dict:
    el = {
        "type": "rectangle", "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "backgroundColor": bg, "fillStyle": "solid",
        "strokeColor": stroke, "strokeWidth": 2,
    }
    if roundness:
        el["roundness"] = {"type": 3}
    if text_id:
        el["boundElements"] = [{"id": text_id, "type": "text"}]
    if opacity < 100:
        el["opacity"] = opacity
    return el


def make_text(eid: str, x: int, y: int, w: int, h: int,
              text: str, font_size: int = 12,
              color: str = "#1e1e1e", align: str = "left",
              valign: str = "top", container_id: str = None) -> dict:
    el = {
        "type": "text", "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "text": text, "fontSize": font_size, "fontFamily": 1,
        "strokeColor": color, "textAlign": align,
        "verticalAlign": valign,
        "originalText": text, "autoResize": True,
    }
    if container_id:
        el["containerId"] = container_id
    return el


def make_arrow(eid: str, x: int, y: int, w: int, h: int,
               color: str = "#1e1e1e",
               dashed: bool = False,
               label: str = None) -> list[dict]:
    els = [{
        "type": "arrow", "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "points": [[0, 0], [w, h]],
        "endArrowhead": "arrow",
        "strokeColor": color, "strokeWidth": 2,
    }]
    if dashed:
        els[0]["strokeStyle"] = "dashed"
    if label:
        tid = f"t_{eid}"
        els[0]["boundElements"] = [{"id": tid, "type": "text"}]
        els.append({
            "type": "text", "id": tid,
            "x": x + 4, "y": y - 16,
            "width": max(len(label) * 7, 80), "height": 15,
            "text": label, "fontSize": 9, "fontFamily": 1,
            "strokeColor": "#757575", "textAlign": "left",
            "verticalAlign": "middle",
            "containerId": eid, "originalText": label, "autoResize": True,
        })
    return els


def _section_title(text: str) -> dict:
    return {
        "type": "text",
        "id": f"sec_{text[:12].lower().replace(' ', '_')}",
        "x": 50, "y": 0, "width": 600, "height": 22,
        "text": text,
        "fontSize": 14, "fontFamily": 1,
        "strokeColor": "#1e1e1e",
        "originalText": text, "autoResize": True,
    }


def _chunk_text(text: str, max_w: int, font_size: int) -> list[str]:
    """Roughly estimate line breaks for a given pixel width."""
    avg_char_w = font_size * 0.6
    max_chars = max(int(max_w / avg_char_w), 20)
    lines = []
    for line in text.split("\n"):
        while len(line) > max_chars:
            break_idx = line.rfind(" ", 0, max_chars)
            if break_idx < max_chars // 2:
                break_idx = max_chars
            lines.append(line[:break_idx])
            line = line[break_idx:].lstrip()
        lines.append(line)
    return lines


# ── Main Renderer ──────────────────────────────────────────────────

def render_review(
    pr_data: dict,
    findings: list[dict] = None,
    graph_data: dict = None,
    code_chunks: list[dict] = None,
    connections: list[dict] = None,
    flow_steps: list[tuple] = None,
    file_tree: str = None,
    frontend_components: list[dict] = None,
    output_path: str = "/tmp/review.excalidraw",
) -> str:
    """
    Generate a polished, graphify-informed Excalidraw review diagram.

    Args:
        pr_data: {title, number, repo, author, loc, status}
        findings: [{severity, title, detail, file?, line?}]
        graph_data: {god_nodes: [{name, edges, why}],
                     communities: [{name, members, why}],
                     blast_radius: {file: [affected]}}
        code_chunks: [{code, why, file?}]
        connections: [{source, target, relation, why}]
        flow_steps: [(label, detail, color)]
        file_tree: str (multi-line)
        frontend_components: [{name, desc, file, why}]
        output_path: where to save .excalidraw file

    Returns:
        Local file path (call upload_excalidraw() for shareable link)
    """
    elements = []
    y_cursor = 20

    pr_data = pr_data or {}
    findings = findings or []
    graph_data = graph_data or {}
    code_chunks = code_chunks or []
    connections = connections or []
    flow_steps = flow_steps or []
    frontend_components = frontend_components or []

    god_nodes = graph_data.get("god_nodes", [])
    communities = graph_data.get("communities", [])
    blast_radius = graph_data.get("blast_radius", {})

    # ── 1. Title ───────────────────────────────────────────────────
    title = pr_data.get("title", f"PR #{pr_data.get('number', '?')} Review")
    subtitle_parts = []
    if pr_data.get("repo"):
        subtitle_parts.append(pr_data["repo"])
    if pr_data.get("loc"):
        subtitle_parts.append(f"{pr_data['loc']} LOC")
    if pr_data.get("status"):
        subtitle_parts.append(pr_data["status"])
    if pr_data.get("author"):
        subtitle_parts.append(f"by {pr_data['author']}")

    elements.append(make_text(
        "title", 200, y_cursor, 800, 35,
        title, font_size=24, align="center",
    ))
    y_cursor += 35
    if subtitle_parts:
        elements.append(make_text(
            "subtitle", 200, y_cursor, 800, 18,
            "  •  ".join(subtitle_parts), font_size=12, color="#757575",
            align="center",
        ))
        y_cursor += 25
    y_cursor += 10

    # ── 2. Scope / File Tree ───────────────────────────────────────
    if file_tree:
        tree_lines = file_tree.strip().split("\n")
        tree_h = max(60, min(280, len(tree_lines) * 16 + 40))
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_tree", 50, y_cursor, 700, tree_h,
            ZONE_BLUE, BLUE, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("SCOPE / FILES CHANGED")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28
        elements.append(make_text(
            "file_tree_txt", 60, y_cursor, 680, tree_h - 50,
            file_tree, font_size=10, color="#1e1e1e",
        ))
        y_cursor = zone_y + tree_h + 5

    # ── 3. Flow Diagram ────────────────────────────────────────────
    if flow_steps:
        f_cnt = len(flow_steps)
        box_w = min(140, int(600 / max(f_cnt, 1)))
        box_h = 50
        gap = 12
        total_w = f_cnt * box_w + (f_cnt - 1) * gap
        start_x = 50 + (700 - total_w) // 2 if total_w < 700 else 50

        zone_y = y_cursor
        zone_h = 100
        elements.append(make_rect(
            "bg_flow", 50, y_cursor, 700, zone_h,
            ZONE_GREEN, GREEN, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("FLOW")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 32

        for i, (label, detail, color) in enumerate(flow_steps):
            bx = start_x + i * (box_w + gap)
            bid = f"f{i}"
            tid = f"tf{i}"
            elements.append(make_rect(
                bid, bx, y_cursor, box_w, box_h,
                color, stroke="#1e1e1e",
                text_id=tid,
            ))
            elements.append(make_text(
                tid, bx + 3, y_cursor + 4, box_w - 6, box_h - 8,
                f"{label}\n{detail}", font_size=9,
                align="center", valign="middle", container_id=bid,
            ))
            if i < f_cnt - 1:
                ax = bx + box_w
                ay = y_cursor + box_h // 2
                elements.extend(make_arrow(f"fa{i}", ax, ay, gap, 0, color="#666"))

        y_cursor = zone_y + zone_h + 5

    # ── 4. Code Chunks with WHY annotations ────────────────────────
    if code_chunks:
        chunk_h = sum(max(50, len(_chunk_text(
            f"{c.get('code','')[:200]}", 620, 10)) * 14 + 40)
            for c in code_chunks) + 35
        chunk_h = min(chunk_h, 350)

        zone_y = y_cursor
        elements.append(make_rect(
            "bg_code", 50, y_cursor, 700, chunk_h,
            LIGHT_TEAL, CYAN, opacity=25,
        ))
        y_cursor += 8
        t = _section_title("CODE CHUNKS  // WHY:")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for ci, chunk in enumerate(code_chunks):
            code = chunk.get("code", "")
            why = chunk.get("why", "")
            cfile = chunk.get("file", "")
            cid = f"code{ci}"
            ctid = f"tcode{ci}"

            label = f"// {cfile}\n{code[:200]}"
            if why:
                label += f"\n// WHY: {why[:150]}"

            el_h = max(40, min(80, len(label.split("\n")) * 14 + 10))
            elements.append(make_rect(
                cid, 65, y_cursor, 670, el_h,
                LIGHT_YELLOW, "#1e1e1e",
            ))
            elements.append(make_text(
                ctid, 70, y_cursor + 3, 660, el_h - 6,
                label, font_size=9,
                align="left", valign="top", container_id=cid,
            ))
            y_cursor += el_h + 6

        y_cursor = zone_y + chunk_h + 5

    # ── 5. God Nodes ───────────────────────────────────────────────
    if god_nodes:
        gn_cnt = min(len(god_nodes), 8)
        gn_h = max(60, gn_cnt * 32 + 35)
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_god", 50, y_cursor, 700, gn_h,
            ZONE_PURPLE, PURPLE, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("GOD NODES  (graphify architectural hubs)")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for gi, gn in enumerate(god_nodes[:gn_cnt]):
            name = gn.get("name", "?")
            edges = gn.get("edges", 0)
            why = gn.get("why", "")
            gid = f"god{gi}"
            gtid = f"tgod{gi}"
            label = f"`{name}` — {edges} connections"
            if why:
                label += f"\n  WHY: {why[:100]}"

            elements.append(make_rect(
                gid, 65, y_cursor, 670, 28,
                LIGHT_PURPLE, PURPLE,
            ))
            elements.append(make_text(
                gtid, 70, y_cursor + 3, 660, 22,
                label, font_size=9,
                align="left", valign="middle", container_id=gid,
            ))
            y_cursor += 30

        y_cursor = zone_y + gn_h + 5

    # ── 6. Communities ─────────────────────────────────────────────
    if communities:
        cm_cnt = min(len(communities), 6)
        cm_h = max(60, cm_cnt * 50 + 35)
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_comm", 50, y_cursor, 700, cm_h,
            ZONE_BLUE, BLUE, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("COMMUNITIES  (graphify code communities)")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for ci, cm in enumerate(communities[:cm_cnt]):
            name = cm.get("name", "?")
            members = cm.get("members", [])
            why = cm.get("why", "")
            cid = f"comm{ci}"
            ctid = f"tcomm{ci}"

            member_list = ", ".join(f"`{m}`" for m in members[:5])
            if len(members) > 5:
                member_list += f" +{len(members)-5} more"
            label = f"Community: {name}\n  Members: {member_list}"
            if why:
                label += f"\n  WHY: {why[:100]}"

            el_h = max(40, min(60, len(label.split("\n")) * 14 + 10))
            elements.append(make_rect(
                cid, 65, y_cursor, 670, el_h,
                LIGHT_BLUE, BLUE,
            ))
            elements.append(make_text(
                ctid, 70, y_cursor + 3, 660, el_h - 6,
                label, font_size=9,
                align="left", valign="top", container_id=cid,
            ))
            y_cursor += el_h + 4

        y_cursor = zone_y + cm_h + 5

    # ── 7. Connections with WHY ────────────────────────────────────
    if connections:
        cn_cnt = min(len(connections), 10)
        cn_h = max(60, cn_cnt * 32 + 35)
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_conn", 50, y_cursor, 700, cn_h,
            ZONE_GREEN, GREEN, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("CROSS-MODULE CONNECTIONS")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for ci, conn in enumerate(connections[:cn_cnt]):
            src = conn.get("source", "?")
            tgt = conn.get("target", "?")
            rel = conn.get("relation", "related")
            why = conn.get("why", "")
            cnid = f"conn{ci}"
            cntid = f"tconn{ci}"

            label = f"`{src}` {rel} `{tgt}`"
            if why:
                label += f"\n  WHY: {why[:100]}"

            elements.append(make_rect(
                cnid, 65, y_cursor, 670, 28,
                LIGHT_GREEN, GREEN,
            ))
            elements.append(make_text(
                cntid, 70, y_cursor + 3, 660, 22,
                label, font_size=9,
                align="left", valign="middle", container_id=cnid,
            ))
            y_cursor += 30

        y_cursor = zone_y + cn_h + 5

    # ── 8. Frontend Components ─────────────────────────────────────
    if frontend_components:
        fc_cnt = min(len(frontend_components), 8)
        fc_h = max(60, fc_cnt * 40 + 35)
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_fe", 50, y_cursor, 700, fc_h,
            ZONE_BLUE, BLUE, opacity=30,
        ))
        y_cursor += 8
        t = _section_title("FRONTEND COMPONENTS")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for fi, fc in enumerate(frontend_components[:fc_cnt]):
            name = fc.get("name", "?")
            desc = fc.get("desc", "")
            ffile = fc.get("file", "")
            why = fc.get("why", "")
            fid = f"fe{fi}"
            ftid = f"tfe{fi}"

            label = f"{name}"
            if ffile:
                label += f"  ({ffile})"
            if desc:
                label += f"\n  {desc[:120]}"
            if why:
                label += f"\n  WHY: {why[:80]}"

            el_h = max(36, min(55, len(label.split("\n")) * 14 + 10))
            elements.append(make_rect(
                fid, 65, y_cursor, 670, el_h,
                LIGHT_BLUE, BLUE,
            ))
            elements.append(make_text(
                ftid, 70, y_cursor + 3, 660, el_h - 6,
                label, font_size=9,
                align="left", valign="top", container_id=fid,
            ))
            y_cursor += el_h + 4

        y_cursor = zone_y + fc_h + 5

    # ── 9. Findings ────────────────────────────────────────────────
    if findings:
        fh = max(50, len(findings) * 52 + 35)
        zone_y = y_cursor
        elements.append(make_rect(
            "bg_findings", 50, y_cursor, 700, fh,
            LIGHT_YELLOW, AMBER, opacity=25,
        ))
        y_cursor += 8
        t = _section_title("FINDINGS")
        t["y"] = y_cursor
        elements.append(t)
        y_cursor += 28

        for fi, finding in enumerate(findings):
            sev = finding.get("severity", "info").lower()
            fill, stroke, text_c = SEVERITY.get(sev, SEVERITY["info"])
            title_t = finding.get("title", "")
            detail = finding.get("detail", "")
            ffile = finding.get("file", "")
            fline = finding.get("line", "")

            fid = f"find{fi}"
            ftid = f"tfind{fi}"
            icon = SEVERITY_ICON.get(sev, " ")
            loc = f"  {ffile}:{fline}" if ffile else ""

            label = f"[{icon}] {title_t}{loc}\n  {detail[:150]}"
            if fline:
                label += f" (line {fline})"

            elements.append(make_rect(
                fid, 65, y_cursor, 670, 46,
                fill, stroke,
            ))
            elements.append(make_text(
                ftid, 70, y_cursor + 3, 660, 40,
                label, font_size=10,
                align="left", valign="top", container_id=fid,
                color=text_c,
            ))
            y_cursor += 48

        y_cursor = zone_y + fh + 5

    # ── 10. Legend ─────────────────────────────────────────────────
    legend_data = [
        (LIGHT_RED,    RED,    "Critical"),
        (LIGHT_ORANGE, AMBER,  "Warning"),
        (LIGHT_PURPLE, PURPLE, "Suggestion"),
        (LIGHT_GREEN,  GREEN,  "Approved"),
        (LIGHT_BLUE,   BLUE,   "Info"),
    ]
    legend_y = y_cursor + 8
    elements.append(make_text(
        "leg_title", 50, legend_y, 200, 18,
        "Legend:", font_size=12,
    ))
    legend_y += 22

    for i, (fill, stroke, label) in enumerate(legend_data):
        lx = 110 + i * 115
        lid = f"leg{i}"
        ltid = f"tleg{i}"
        elements.append(make_rect(lid, lx, legend_y, 95, 26, fill, stroke))
        elements.append(make_text(
            ltid, lx + 3, legend_y + 3, 89, 20,
            label, font_size=10, align="center", valign="middle",
            container_id=lid,
        ))

    # ── Assemble JSON ──────────────────────────────────────────────
    data = {
        "type": "excalidraw",
        "version": 2,
        "source": "riptide-review",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff"},
    }

    Path(output_path).write_text(json.dumps(data, indent=2))
    return output_path


# ── Upload ──────────────────────────────────────────────────────────

def upload_excalidraw(file_path: str) -> Optional[str]:
    """Upload .excalidraw file and return shareable excalidraw.com link."""
    upload_script = (
        Path.home()
        / ".hermes" / "hermes-agent" / "skills"
        / "creative" / "excalidraw" / "scripts" / "upload.py"
    )
    fallback = (
        Path.home()
        / "workspace" / "riptide" / "scripts" / "upload_excalidraw.py"
    )

    for script in [upload_script, fallback]:
        if script.exists():
            result = subprocess.run(
                ["python3", str(script), str(file_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    return None


# ── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Test mode: generate a sample diagram
    output = render_review(
        pr_data={"title": "feat: sample PR", "number": 42, "repo": "test/repo",
                 "author": "test", "loc": 250, "status": "OPEN"},
        findings=[
            {"severity": "critical", "title": "Bad API call",
             "detail": "Calls deprecated endpoint without auth", "file": "api.py", "line": 42},
            {"severity": "warning", "title": "Missing error handling",
             "detail": "No try/except around network call", "file": "client.py", "line": 88},
            {"severity": "suggestion", "title": "Use f-strings",
             "detail": "String concatenation can be simplified", "file": "utils.py", "line": 12},
        ],
        graph_data={
            "god_nodes": [
                {"name": "routes.py", "edges": 42, "why": "Central routing hub"},
                {"name": "Session", "edges": 38, "why": "Core domain object"},
            ],
            "communities": [
                {"name": "API Layer", "members": ["routes.py", "auth.py", "helpers.py"],
                 "why": "HTTP request handling path"},
                {"name": "Persistence", "members": ["models.py", "store.py", "mcp_server.py"],
                 "why": "Session read/write lifecycle"},
            ],
            "blast_radius": {},
        },
        code_chunks=[
            {"code": "session_data.update(draft)", "why": "Leaks keys to top level",
             "file": "draft_optimization.py:63"},
            {"code": "except Exception: pass", "why": "Silently hides ImportError",
             "file": "models.py:1421"},
        ],
        connections=[
            {"source": "draft_optimization.py", "target": "models.py",
             "relation": "imported by", "why": "Per-session draft overlay on Session.load()"},
            {"source": "routes.py", "target": "draft_optimization.py",
             "relation": "calls", "why": "Saves draft before session rewrite"},
        ],
        flow_steps=[
            ("Webhook", "PR event", LIGHT_BLUE),
            ("Graphify", "code analysis", LIGHT_PURPLE),
            ("DeepThink", "review loop", LIGHT_ORANGE),
            ("Excalidraw", "diagram out", LIGHT_GREEN),
        ],
        file_tree="api/draft_optimization.py  (83 LOC)\napi/models.py (+7 LOC)\napi/routes.py (+6 / -6)\napi/recovery_manager.py  (177 LOC, dead code)",
        frontend_components=[
            {"name": "Draft Overlay", "desc": "Applies per-session draft on load",
             "file": "models.py", "why": "Reduces large-session latency"},
        ],
        output_path=sys.argv[1] if len(sys.argv) > 1 else "/tmp/review.excalidraw",
    )
    print(f"Diagram: {output}")
