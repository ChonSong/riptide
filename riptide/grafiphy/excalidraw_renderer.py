#!/usr/bin/env python3
"""
excalidraw_renderer.py — Polished Excalidraw review diagram generator.

Produces a flowing narrative diagram from codebase landscape → PR changes →
graphify analysis → code chunks with WHY → findings → suggestions.

Eight connected sections, all linked by arrows, using the Excalidraw skill's
official color palette.

Usage:
    from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw
    url = upload_excalidraw(render_review(pr_data, findings, ...))
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

# Background zones
ZONE_BLUE = "#dbe4ff"
ZONE_PURPLE = "#e5dbff"
ZONE_GREEN = "#d3f9d8"
ZONE_YELLOW = "#fff9db"
ZONE_PINK = "#fce4ec"
ZONE_TEAL = "#c3fae8"

# Highlight for PR-changed files in landscape
HIGHLIGHT_STROKE = "#ef4444"
HIGHLIGHT_FILL = "#ffe0e0"
HIGHLIGHT_GLOW = "#ff6b6b"

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

# Canvas constants
CANVAS_W = 900
MARGIN = 40
CONTENT_W = CANVAS_W - 2 * MARGIN  # 820


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
    """Standalone arrow (no bindings). Returns [arrow_elem, optional_label_elem]."""
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


def make_routed_arrow(eid: str,
                     src_rect: dict, tgt_rect: dict,
                     src_side: str = "bottom", tgt_side: str = "top",
                     color: str = "#757575",
                     dashed: bool = False,
                     label: str = None,
                     gap: int = 15) -> list[dict]:
    """
    Draw an arrow with rounded corners routing between two element rects.

    Computes a multi-point path (3 or 4 segments) that routes around elements
    rather than cutting through them. Excalidraw renders multi-point arrows
    with automatically rounded corners.

    Args:
        eid: unique arrow id
        src_rect: {x, y, w, h} of source element
        tgt_rect: {x, y, w, h} of target element
        src_side: "bottom" | "right" | "left" | "top"
        tgt_side: "top" | "left" | "right" | "bottom"
        color: stroke color
        dashed: if True, use dashed stroke
        label: optional text label at midpoint
        gap: px gap from element edges

    Returns:
        list of dicts (arrow elements + optional label text)
    """
    sx, sy, sw, sh = src_rect["x"], src_rect["y"], src_rect["w"], src_rect["h"]
    tx, ty, tw, th = tgt_rect["x"], tgt_rect["y"], tgt_rect["w"], tgt_rect["h"]

    # Compute source exit point
    if src_side == "bottom":
        p1x = sx + sw // 2
        p1y = sy + sh + gap
    elif src_side == "right":
        p1x = sx + sw + gap
        p1y = sy + sh // 2
    elif src_side == "left":
        p1x = sx - gap
        p1y = sy + sh // 2
    else:  # top
        p1x = sx + sw // 2
        p1y = sy - gap

    # Compute target entry point
    if tgt_side == "top":
        p3x = tx + tw // 2
        p3y = ty - gap
    elif tgt_side == "bottom":
        p3x = tx + tw // 2
        p3y = ty + th + gap
    elif tgt_side == "left":
        p3x = tx - gap
        p3y = ty + th // 2
    else:  # right
        p3x = tx + tw + gap
        p3y = ty + th // 2

    # Compute corner point(s) for routing
    # Simple 3-point: right-angle L-shape
    dx = p3x - p1x
    dy = p3y - p1y

    if abs(dx) > abs(dy):
        # Horizontal dominant: go horizontal first, then vertical
        corner_x = p3x
        corner_y = p1y
    else:
        # Vertical dominant: go vertical first, then horizontal
        corner_x = p1x
        corner_y = p3y

    points = [[p1x, p1y], [corner_x, corner_y], [p3x, p3y]]

    min_x = min(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_x = max(p[0] for p in points)
    max_y = max(p[1] for p in points)

    # Normalize points to be relative to (min_x, min_y)
    rel_points = [[p[0] - min_x, p[1] - min_y] for p in points]

    w = max(max_x - min_x, 1)
    h = max(max_y - min_y, 1)

    els = [{
        "type": "arrow", "id": eid,
        "x": min_x, "y": min_y, "width": w, "height": h,
        "points": rel_points,
        "endArrowhead": "arrow",
        "strokeColor": color, "strokeWidth": 2,
        "startBinding": {
            "elementId": None, "focus": 0, "gap": gap,
        },
        "endBinding": {
            "elementId": None, "focus": 0, "gap": gap,
        },
    }]
    if dashed:
        els[0]["strokeStyle"] = "dashed"

    if label:
        tid = f"tl_{eid}"
        mid_x = (p1x + p3x) // 2
        mid_y = (p1y + p3y) // 2
        els[0]["boundElements"] = [{"id": tid, "type": "text"}]
        els.append({
            "type": "text", "id": tid,
            "x": mid_x - 30, "y": mid_y - 10,
            "width": max(len(label) * 6, 60), "height": 14,
            "text": label, "fontSize": 9, "fontFamily": 1,
            "strokeColor": color, "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": eid, "originalText": label, "autoResize": True,
        })

    return els


def make_zone(eid: str, x: int, y: int, w: int, h: int,
              bg: str, stroke: str, opacity: int = 25) -> dict:
    """Background section zone with rounded rect and low opacity."""
    return make_rect(eid, x, y, w, h, bg, stroke, opacity=opacity)


def _section_title(eid: str, text: str, x: int, y: int) -> dict:
    return {
        "type": "text",
        "id": eid,
        "x": x, "y": y, "width": 600, "height": 24,
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


def _compute_text_h(text: str, max_w: int, font_size: int, line_h: int = 14) -> int:
    """Estimate height for a block of text at given font size."""
    lines = _chunk_text(text, max_w, font_size)
    return max(len(lines) * line_h, 20)


# ── Main Renderer ──────────────────────────────────────────────────

def render_review(
    pr_data: dict = None,
    findings: list[dict] = None,
    graph_data: dict = None,
    code_chunks: list[dict] = None,
    connections: list[dict] = None,
    flow_steps: list[tuple] = None,
    file_tree: str = None,
    frontend_components: list[dict] = None,
    repo_graph: list[dict] = None,
    repo_tree: str = None,
    suggestions: list[dict] = None,
    human_narrative: str = None,
    output_path: str = "/tmp/review.excalidraw",
) -> str:
    """
    Generate a flowing narrative Excalidraw review diagram.

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
        repo_graph: [{name, type, file, why}] — all modules in repo (new)
        suggestions: [{file, line, old_code, new_code, severity, reasoning}] (new)
        human_narrative: str — plain-English summary (optional, auto-generated if not given)
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
    repo_graph = repo_graph or []
    suggestions = suggestions or []

    god_nodes = graph_data.get("god_nodes", [])
    communities = graph_data.get("communities", [])
    blast_radius = graph_data.get("blast_radius", {})

    # Determine PR file paths for highlighting
    pr_changed_files = set()
    if file_tree:
        for line in file_tree.split("\n"):
            line = line.strip()
            if line and line[0] in ("+", "M", "-"):
                parts = line.split()
                if len(parts) >= 2:
                    fname = parts[1] if parts[0] in ("+", "M", "-") else parts[1]
                    pr_changed_files.add(fname)
    # Also check code_chunks for file references
    for cc in code_chunks:
        cf = cc.get("file", "")
        if cf and ":" in cf:
            pr_changed_files.add(cf.split(":")[0])

    # Collect all file paths from repo_graph
    repo_files = set()
    for rg in repo_graph:
        f = rg.get("file", "")
        if f:
            repo_files.add(f)

    # Map file -> bool (is_changed)
    def is_pr_file(path: str) -> bool:
        for pf in pr_changed_files:
            if pf.endswith(path) or path.endswith(pf):
                return True
        return False

    # ================================================================
    # SECTION 1: Title
    # ================================================================
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
        "title", 250, y_cursor, 600, 35,
        title, font_size=24, align="center",
    ))
    y_cursor += 35
    if subtitle_parts:
        elements.append(make_text(
            "subtitle", 250, y_cursor, 600, 18,
            "  .  ".join(subtitle_parts), font_size=12, color="#757575",
            align="center",
        ))
        y_cursor += 25
    y_cursor += 10

    # ================================================================
    # SECTION 2: Codebase Directory Tree
    # ================================================================
    landscape_top = y_cursor
    zone_id = "zone_tree"

    # Parse the repo_tree string
    parsed_lines = []
    has_tree = bool(repo_tree)
    if repo_tree:
        for line in repo_tree.strip().split("\n"):
            raw = line.rstrip()
            # Count leading spaces to determine depth (every 4 spaces = 1 level)
            stripped = raw.lstrip()
            indent = (len(raw) - len(stripped)) // 4
            # Remove tree-drawing chars (├── └── │)
            clean = stripped.lstrip("├└│").lstrip("──").strip()
            # Split off annotation after #
            annotation = ""
            if "#" in clean:
                parts = clean.split("#", 1)
                clean = parts[0].strip()
                annotation = parts[1].strip()
            is_dir = clean.endswith("/")
            if is_dir:
                clean = clean.rstrip("/")
            parsed_lines.append({
                "indent": indent,
                "name": clean,
                "annotation": annotation,
                "is_dir": is_dir,
                "raw": raw,
            })

    tree_h = 0
    if has_tree:
        # Estimate height: ~20px per line, capped
        n_lines = len(parsed_lines)
        tree_h = max(60, min(500, n_lines * 20 + 50))
    else:
        tree_h = 60

    elements.append(make_zone(
        zone_id, MARGIN, y_cursor, CONTENT_W, tree_h,
        ZONE_TEAL, CYAN, opacity=20,
    ))
    y_cursor += 10

    st = _section_title("sec_tree", "CODEBASE DIRECTORY TREE  (PR files highlighted in red)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if has_tree:
        for ti, pl in enumerate(parsed_lines):
            indent_px = pl["indent"] * 16 + MARGIN + 10
            line_w = CONTENT_W - (indent_px - MARGIN) - 20
            name = pl["name"]
            annotation = pl["annotation"]
            is_dir = pl["is_dir"]

            # Check if this file path matches a PR-changed file
            is_pr = is_pr_file(name)

            tid = f"tree{ti}"
            if is_dir:
                label = f"{name}/"
                if annotation:
                    label += f"  # {annotation}"
                el_h = 18
                fill = ZONE_TEAL
                stroke = CYAN
                stroke_w = 1
            else:
                label = name
                if annotation:
                    label += f"  # {annotation}"
                el_h = 18
                if is_pr:
                    fill = HIGHLIGHT_FILL
                    stroke = HIGHLIGHT_STROKE
                    stroke_w = 3
                else:
                    fill = LIGHT_TEAL
                    stroke = CYAN
                    stroke_w = 1

            rid = f"r_tree{ti}"
            elements.append(make_rect(
                rid, indent_px, y_cursor, line_w, el_h,
                fill, stroke, roundness=True,
            ))
            # Set stroke width on the element
            elements[-1]["strokeWidth"] = stroke_w
            elements.append(make_text(
                tid, indent_px + 3, y_cursor + 1, line_w - 6, el_h - 2,
                label, font_size=9,
                align="left", valign="middle", container_id=rid,
                color=DARK_BLUE if is_dir else "#1e1e1e",
            ))
            y_cursor += 20

        actual_h = max(tree_h, y_cursor - landscape_top + 10)
        for el in elements:
            if el.get("id") == zone_id:
                el["height"] = actual_h
                break
        y_cursor = landscape_top + actual_h + 5
    else:
        elements.append(make_text(
            "tree_empty", MARGIN + 10, y_cursor, CONTENT_W - 20, 30,
            "(No repo_tree provided — pass repo_tree for a directory tree overview)",
            font_size=11, color="#757575",
        ))
        y_cursor = landscape_top + tree_h + 5
    pr_scope_top = y_cursor + 25

    # ================================================================
    # SECTION 3: PR Scope
    # ================================================================
    scope_top = y_cursor

    if file_tree:
        tree_lines = file_tree.strip().split("\n")
        tree_h = max(50, min(280, len(tree_lines) * 18 + 50))
    else:
        tree_h = 50

    scope_h = tree_h
    elements.append(make_zone(
        "zone_scope", MARGIN, y_cursor, CONTENT_W, scope_h,
        ZONE_BLUE, BLUE, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_scope", "PR SCOPE  (files changed, LOC, status)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if file_tree:
        elements.append(make_text(
            "file_tree_txt", MARGIN + 10, y_cursor, CONTENT_W - 20, tree_h - 40,
            file_tree, font_size=11, color="#1e1e1e",
        ))

    y_cursor = scope_top + scope_h + 5

    # Arrow from scope to graphify analysis
    # ================================================================
    # SECTION 4: Graphify Analysis (god nodes + communities)
    # ================================================================
    graph_top = y_cursor

    # Estimate height
    gn_cnt = min(len(god_nodes), 6)
    cm_cnt = min(len(communities), 4)
    graph_h = max(80, (gn_cnt + cm_cnt) * 36 + 60)

    elements.append(make_zone(
        "zone_graph", MARGIN, y_cursor, CONTENT_W, graph_h,
        ZONE_PURPLE, PURPLE, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_graph", "GRAPHIFY ANALYSIS  (architectural hubs and code communities)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    # God nodes section inside zone
    if god_nodes:
        elements.append(make_text(
            "god_label", MARGIN + 10, y_cursor, 200, 16,
            "God Nodes (architectural hubs):", font_size=11,
            color=DARK_PURPLE,
        ))
        y_cursor += 18
        for gi, gn in enumerate(god_nodes[:gn_cnt]):
            name = gn.get("name", "?")
            edges = gn.get("edges", 0)
            why = gn.get("why", "")
            gid = f"god{gi}"
            gtid = f"tgod{gi}"
            label = f"{name}  ({edges} connections)"
            if why:
                label += f"\nWHY: {why}"

            h = max(28, _compute_text_h(label, CONTENT_W - 40, 9, 13) + 6)
            elements.append(make_rect(
                gid, MARGIN + 15, y_cursor, CONTENT_W - 30, h,
                LIGHT_PURPLE, PURPLE,
            ))
            elements.append(make_text(
                gtid, MARGIN + 20, y_cursor + 3, CONTENT_W - 40, h - 6,
                label, font_size=9,
                align="left", valign="top", container_id=gid,
            ))
            y_cursor += h + 4

        y_cursor += 4

    # Communities section inside zone
    if communities:
        elements.append(make_text(
            "comm_label", MARGIN + 10, y_cursor, 200, 16,
            "Communities (code groups):", font_size=11,
            color=DARK_BLUE,
        ))
        y_cursor += 18
        for ci, cm in enumerate(communities[:cm_cnt]):
            name = cm.get("name", "?")
            members = cm.get("members", [])
            why = cm.get("why", "")
            cid = f"comm{ci}"
            ctid = f"tcomm{ci}"

            member_list = ", ".join(f"'{m}'" for m in members[:5])
            if len(members) > 5:
                member_list += f" +{len(members) - 5} more"
            label = f"{name}: {member_list}"
            if why:
                label += f"\nWHY: {why}"

            h = max(28, _compute_text_h(label, CONTENT_W - 40, 9, 13) + 6)
            elements.append(make_rect(
                cid, MARGIN + 15, y_cursor, CONTENT_W - 30, h,
                LIGHT_BLUE, BLUE,
            ))
            elements.append(make_text(
                ctid, MARGIN + 20, y_cursor + 3, CONTENT_W - 40, h - 6,
                label, font_size=9,
                align="left", valign="top", container_id=cid,
            ))
            y_cursor += h + 4

    actual_graph_h = y_cursor - graph_top + 5
    # Update zone height
    for el in elements:
        if el.get("id") == "zone_graph":
            el["height"] = actual_graph_h
            break

    # ================================================================
    # SECTION 5: Code Chunks with WHY
    # ================================================================
    code_top = y_cursor

    if code_chunks:
        chunk_h = sum(
            max(50, _compute_text_h(
                cc.get("code", "") + "\nWHY: " + cc.get("why", ""),
                CONTENT_W - 40, 10, 14,
            ) + 16)
            for cc in code_chunks
        ) + 40
        chunk_h = min(chunk_h, 450)
    else:
        chunk_h = 60

    elements.append(make_zone(
        "zone_code", MARGIN, y_cursor, CONTENT_W, chunk_h,
        ZONE_YELLOW, AMBER, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_code", "CODE CHUNKS  //  WHY: architectural reasoning",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if code_chunks:
        for ci, chunk in enumerate(code_chunks):
            code = chunk.get("code", "")
            why = chunk.get("why", "")
            cfile = chunk.get("file", "")
            cid = f"code{ci}"
            ctid = f"tcode{ci}"

            label = f"// File: {cfile}\n{code}"
            if why:
                label += f"\n\n// WHY: {why}"

            el_h = max(40, _compute_text_h(label, CONTENT_W - 40, 10, 14) + 12)
            elements.append(make_rect(
                cid, MARGIN + 10, y_cursor, CONTENT_W - 20, el_h,
                LIGHT_YELLOW, "#1e1e1e",
            ))
            elements.append(make_text(
                ctid, MARGIN + 15, y_cursor + 4, CONTENT_W - 30, el_h - 8,
                label, font_size=10,
                align="left", valign="top", container_id=cid,
            ))
            y_cursor += el_h + 6

    actual_code_h = y_cursor - code_top + 5
    for el in elements:
        if el.get("id") == "zone_code":
            el["height"] = actual_code_h
            break

    # ================================================================
    # SECTION 6: Human-Readable Narrative
    # ================================================================
    narr_top = y_cursor

    if not human_narrative and (code_chunks or findings):
        # Auto-generate a simple narrative
        parts = []
        title_t = pr_data.get("title", "")
        if title_t:
            parts.append(title_t)
        if code_chunks:
            files = ", ".join(set(c.get("file", "").split(":")[0] for c in code_chunks if c.get("file")))
            parts.append(f"Changes affect {files}.")
        if findings:
            sev_counts = {}
            for f in findings:
                s = f.get("severity", "info")
                sev_counts[s] = sev_counts.get(s, 0) + 1
            sev_str = ", ".join(f"{n} {s}" for s, n in sev_counts.items())
            parts.append(f"Review found: {sev_str}.")
        human_narrative = " ".join(parts)

    if human_narrative:
        narr_h = max(60, _compute_text_h(human_narrative, CONTENT_W - 40, 11, 15) + 40)
    else:
        narr_h = 60

    elements.append(make_zone(
        "zone_narr", MARGIN, y_cursor, CONTENT_W, narr_h,
        ZONE_GREEN, GREEN, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_narr", "HUMAN-READABLE NARRATIVE  (what this PR does and why)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if human_narrative:
        elements.append(make_text(
            "narrative_txt", MARGIN + 12, y_cursor, CONTENT_W - 24, narr_h - 40,
            human_narrative, font_size=11, color="#1e1e1e",
        ))

    y_cursor = narr_top + narr_h + 5

    # ================================================================
    # SECTION 7: Findings with Severity
    # ================================================================
    find_top = y_cursor

    if findings:
        find_h = sum(
            max(46, _compute_text_h(
                SEVERITY_ICON.get(f.get("severity", "info"), " ") + " " +
                f.get("title", "") + " " + f.get("detail", ""),
                CONTENT_W - 40, 10, 14,
            ) + 12)
            for f in findings
        ) + 40
        find_h = min(find_h, 400)
    else:
        find_h = 60

    elements.append(make_zone(
        "zone_find", MARGIN, y_cursor, CONTENT_W, find_h,
        ZONE_PINK, RED, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_find", "FINDINGS  (severity-coded issues)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if findings:
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
            loc = f"  [{ffile}:{fline}]" if ffile else ""

            label = f"[{icon}] {title_t}{loc}\n{detail}"

            el_h = max(40, _compute_text_h(label, CONTENT_W - 40, 10, 14) + 10)
            elements.append(make_rect(
                fid, MARGIN + 10, y_cursor, CONTENT_W - 20, el_h,
                fill, stroke,
            ))
            elements.append(make_text(
                ftid, MARGIN + 15, y_cursor + 3, CONTENT_W - 30, el_h - 6,
                label, font_size=10,
                align="left", valign="top", container_id=fid,
                color=text_c,
            ))
            y_cursor += el_h + 6

    actual_find_h = y_cursor - find_top + 5
    for el in elements:
        if el.get("id") == "zone_find":
            el["height"] = actual_find_h
            break

    # ================================================================
    # SECTION 8: Suggested Changes
    # ================================================================
    sug_top = y_cursor

    if suggestions:
        sug_h = sum(
            max(50, _compute_text_h(
                s.get("reasoning", "") + "\n---\nOLD: " + s.get("old_code", "") +
                "\nNEW: " + s.get("new_code", ""),
                CONTENT_W - 40, 10, 14,
            ) + 16)
            for s in suggestions
        ) + 40
        sug_h = min(sug_h, 400)
    else:
        sug_h = 60

    elements.append(make_zone(
        "zone_sug", MARGIN, y_cursor, CONTENT_W, sug_h,
        ZONE_BLUE, BLUE, opacity=20,
    ))
    y_cursor += 10
    st = _section_title("sec_sug", "SUGGESTED CHANGES  (actionable code diffs from Bot 2 review)",
                        MARGIN + 8, y_cursor)
    elements.append(st)
    y_cursor += 28

    if suggestions:
        for si, sug in enumerate(suggestions):
            sfile = sug.get("file", "")
            sline = sug.get("line", "")
            old_code = sug.get("old_code", "")
            new_code = sug.get("new_code", "")
            reasoning = sug.get("reasoning", "")
            sev = sug.get("severity", "suggestion").lower()
            fill, stroke, text_c = SEVERITY.get(sev, SEVERITY["suggestion"])

            sid = f"sug{si}"
            stid = f"tsug{si}"

            label = f"[{sfile}:{sline}] {reasoning}"
            if old_code:
                label += f"\nOLD: {old_code}"
            if new_code:
                label += f"\nNEW: {new_code}"

            el_h = max(50, _compute_text_h(label, CONTENT_W - 40, 10, 14) + 12)
            elements.append(make_rect(
                sid, MARGIN + 10, y_cursor, CONTENT_W - 20, el_h,
                fill, stroke,
            ))
            elements.append(make_text(
                stid, MARGIN + 15, y_cursor + 4, CONTENT_W - 30, el_h - 8,
                label, font_size=10,
                align="left", valign="top", container_id=sid,
                color=text_c,
            ))
            y_cursor += el_h + 6

    actual_sug_h = y_cursor - sug_top + 5
    for el in elements:
        if el.get("id") == "zone_sug":
            el["height"] = actual_sug_h
            break

    # ================================================================
    # SECTION 9: Legend
    # ================================================================
    legend_y = y_cursor + 10
    elements.append(make_text(
        "leg_title", MARGIN, legend_y, 200, 18,
        "Legend:", font_size=12,
    ))
    legend_y += 22

    legend_data = [
        (LIGHT_RED,    RED,    "Critical"),
        (LIGHT_ORANGE, AMBER,  "Warning"),
        (LIGHT_PURPLE, PURPLE, "Suggestion"),
        (LIGHT_GREEN,  GREEN,  "Approved"),
        (LIGHT_BLUE,   BLUE,   "Info"),
    ]
    # Also add landscape highlight legend
    legend_extras = [
        (HIGHLIGHT_FILL, HIGHLIGHT_STROKE, "PR-changed file"),
        (ZONE_TEAL, CYAN, "Unchanged module"),
    ]
    all_legend = legend_data + legend_extras

    for i, (fill, stroke, label) in enumerate(all_legend):
        lx = MARGIN + 10 + i * 115
        lid = f"leg{i}"
        ltid = f"tleg{i}"
        elements.append(make_rect(lid, lx, legend_y, 100, 26, fill, stroke))
        elements.append(make_text(
            ltid, lx + 3, legend_y + 3, 94, 20,
            label, font_size=10, align="center", valign="middle",
            container_id=lid,
        ))

    # ================================================================
    # CONNECTORS: Cross-section arrows
    # ================================================================
    # We add connectors AFTER all regular elements so we know final positions.
    conn_elements = []

    # --- Vertical section-to-section arrows (routed down) ---
    section_flow = [
        ("zone_tree",  "zone_scope", "conn_tree_scope",  CYAN),
        ("zone_scope", "zone_graph", "conn_scope_graph", PURPLE),
        ("zone_graph", "zone_code",  "conn_graph_code",  AMBER),
        ("zone_code",  "zone_narr",  "conn_code_narr",   GREEN),
        ("zone_narr",  "zone_find",  "conn_narr_find",   RED),
        ("zone_find",  "zone_sug",   "conn_find_sug",    BLUE),
    ]
    for src_zone_id, tgt_zone_id, conn_id, conn_color in section_flow:
        src_el = _find_elem(elements, src_zone_id)
        tgt_el = _find_elem(elements, tgt_zone_id)
        if src_el and tgt_el:
            conn_elements.extend(make_routed_arrow(
                conn_id,
                src_rect={"x": src_el["x"], "y": src_el["y"],
                          "w": src_el["width"], "h": src_el["height"]},
                tgt_rect={"x": tgt_el["x"], "y": tgt_el["y"],
                          "w": tgt_el["width"], "h": tgt_el["height"]},
                src_side="bottom", tgt_side="top",
                color=conn_color, gap=8,
            ))

    # Arrow: PR-changed tree nodes -> God nodes
    if repo_tree and god_nodes:
        pr_tree_ids = [f"r_tree{ti}" for ti, pl in enumerate(parsed_lines)
                       if is_pr_file(pl["name"]) and not pl["is_dir"]]
        if pr_tree_ids and god_nodes:
            tgt_id = "god0"
            src_el = _find_elem(elements, pr_tree_ids[0])
            god_el = _find_elem(elements, tgt_id)
            if src_el and god_el:
                conn_elements.extend(make_routed_arrow(
                    "conn_tree_god",
                    src_rect={"x": src_el["x"], "y": src_el["y"],
                              "w": src_el["width"], "h": src_el["height"]},
                    tgt_rect={"x": god_el["x"], "y": god_el["y"],
                              "w": god_el["width"], "h": god_el["height"]},
                    src_side="right", tgt_side="left",
                    color=PURPLE, dashed=True,
                    label="graphify links",
                ))

    # Arrow: Findings -> Code Chunks
    if findings and code_chunks:
        for fi, finding in enumerate(findings[:3]):
            fid = f"find{fi}"
            cid = f"code{fi}" if fi < len(code_chunks) else f"code{len(code_chunks) - 1}"
            f_el = _find_elem(elements, fid)
            c_el = _find_elem(elements, cid)
            if f_el and c_el:
                conn_elements.extend(make_routed_arrow(
                    f"conn_find_code{fi}",
                    src_rect={"x": f_el["x"], "y": f_el["y"],
                              "w": f_el["width"], "h": f_el["height"]},
                    tgt_rect={"x": c_el["x"], "y": c_el["y"],
                              "w": c_el["width"], "h": c_el["height"]},
                    src_side="left", tgt_side="right",
                    color=AMBER, dashed=True,
                    label="begins at",
                ))

    # Arrow: Suggestions -> Findings
    if suggestions and findings:
        for si, sug in enumerate(suggestions[:3]):
            sid = f"sug{si}"
            fid = f"find{si}" if si < len(findings) else f"find{len(findings) - 1}"
            s_el = _find_elem(elements, sid)
            f_el = _find_elem(elements, fid)
            if s_el and f_el:
                conn_elements.extend(make_routed_arrow(
                    f"conn_sug_find{si}",
                    src_rect={"x": s_el["x"], "y": s_el["y"],
                              "w": s_el["width"], "h": s_el["height"]},
                    tgt_rect={"x": f_el["x"], "y": f_el["y"],
                              "w": f_el["width"], "h": f_el["height"]},
                    src_side="left", tgt_side="right",
                    color=GREEN, dashed=True,
                    label="addresses",
                ))

    # Prepend connector elements so they render behind section content
    elements[0:0] = conn_elements

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


def _find_elem(elements: list[dict], eid: str) -> Optional[dict]:
    """Find an element by ID from the elements list."""
    for el in elements:
        if el.get("id") == eid:
            return el
    return None


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
    # Test mode: generate a sample diagram with all new features
    output = render_review(
        pr_data={"title": "feat: sample PR with full connected narrative",
                 "number": 42, "repo": "test/repo",
                 "author": "test", "loc": 250, "status": "OPEN"},
        repo_graph=[
            {"name": "routes.py", "type": "module", "file": "api/routes.py",
             "why": "HTTP routing and middleware"},
            {"name": "models.py", "type": "module", "file": "api/models.py",
             "why": "Core data models and ORM mappings"},
            {"name": "auth.py", "type": "module", "file": "api/auth.py",
             "why": "Authentication and authorization"},
            {"name": "helpers.py", "type": "module", "file": "api/helpers.py",
             "why": "Utility functions used across modules"},
            {"name": "draft_optimization.py", "type": "module",
             "file": "api/draft_optimization.py",
             "why": "Per-session draft overlay optimization"},
            {"name": "recovery_manager.py", "type": "module",
             "file": "api/recovery_manager.py",
             "why": "Session recovery and rollback"},
            {"name": "mcp_server.py", "type": "module", "file": "api/mcp_server.py",
             "why": "MCP protocol server for tool integration"},
            {"name": "store.py", "type": "module", "file": "api/store.py",
             "why": "Session read/write persistence layer"},
            {"name": "Session", "type": "class", "file": "api/models.py",
             "why": "Core domain object for user sessions"},
            {"name": "DraftOverlay", "type": "class", "file": "api/models.py",
             "why": "Per-session draft state management"},
        ],
        findings=[
            {"severity": "critical", "title": "Bad API call",
             "detail": "Calls deprecated endpoint without authentication. "
                       "This will fail once the legacy endpoint is removed in the next release.",
             "file": "api/draft_optimization.py", "line": 42},
            {"severity": "warning", "title": "Missing error handling",
             "detail": "No try/except around network call in draft save path. "
                       "A network failure would silently lose the draft.",
             "file": "api/models.py", "line": 88},
            {"severity": "suggestion", "title": "Use f-strings for clarity",
             "detail": "String concatenation with + operator makes the log messages "
                       "harder to read. Use f-strings for consistency with rest of codebase.",
             "file": "api/helpers.py", "line": 12},
        ],
        graph_data={
            "god_nodes": [
                {"name": "routes.py", "edges": 42,
                 "why": "Central routing hub that dispatches all HTTP requests to handlers"},
                {"name": "Session", "edges": 38,
                 "why": "Core domain object referenced by persistence, auth, and overlay modules"},
                {"name": "models.py", "edges": 29,
                 "why": "ORM definitions that every data-access path depends on"},
            ],
            "communities": [
                {"name": "API Layer",
                 "members": ["routes.py", "auth.py", "helpers.py"],
                 "why": "HTTP request handling path: routes dispatch through auth middleware"},
                {"name": "Persistence",
                 "members": ["models.py", "store.py", "mcp_server.py"],
                 "why": "Session read/write lifecycle: models define the schema"},
                {"name": "Draft System",
                 "members": ["draft_optimization.py", "recovery_manager.py", "models.py"],
                 "why": "Draft overlay on session load; recovery ensures rollback consistency"},
            ],
            "blast_radius": {},
        },
        code_chunks=[
            {"code": "session_data.update(draft)\n# This merges draft keys into session top-level\n"
                     "return session_data",
             "why": "This line merges draft overlay data into the session dictionary at the top "
                    "level, which can leak draft-specific keys into the main session namespace. "
                    "A safer approach would use a separate 'draft' sub-key to avoid key collision "
                    "with existing session fields like 'user_id' or 'expires_at'.",
             "file": "api/draft_optimization.py:63"},
            {"code": "@app.route('/session/<id>', methods=['GET'])\n"
                     "def get_session(id):\n"
                     "    session = Session.query.get(id)\n"
                     "    if not session:\n"
                     "        abort(404)\n"
                     "    return jsonify(session.to_dict())",
             "why": "This single route handles both web and API consumers of session data. "
                    "As the system grows, separating these concerns into versioned API endpoints "
                    "would prevent breaking web consumers when the session schema evolves.",
             "file": "api/routes.py:15"},
        ],
        connections=[
            {"source": "draft_optimization.py", "target": "models.py",
             "relation": "imported by", "why": "Per-session draft overlay on Session.load()"},
            {"source": "routes.py", "target": "draft_optimization.py",
             "relation": "calls", "why": "Saves draft before session rewrite"},
        ],
        suggestions=[
            {"file": "api/draft_optimization.py", "line": 63,
             "old_code": "session_data.update(draft)",
             "new_code": "session_data['draft'] = draft",
             "severity": "critical",
             "reasoning": "Using update() leaks draft keys into the session namespace. "
                         "Store draft under a dedicated 'draft' sub-key to isolate it. "
                         "This prevents key collision with fields like 'user_id'."},
            {"file": "api/models.py", "line": 88,
             "old_code": "response = requests.post(url, json=data)",
             "new_code": "try:\n    response = requests.post(url, json=data, timeout=5)\n"
                         "except requests.RequestException as e:\n    log.error('save failed: %s', e)\n    raise",
             "severity": "warning",
             "reasoning": "The network call is unprotected. A transient failure would silently "
                         "swallow the exception and the caller would never know the save failed."},
        ],
        human_narrative=(
            "This PR introduces a draft overlay feature for user sessions. "
            "The draft_optimization.py module caches in-progress draft data and applies it on "
            "session load, reducing latency for large sessions. "
            "Changes affect the API routing layer (routes.py), the persistence layer (models.py, "
            "store.py), and the new draft module. "
            "The main concern is that draft keys currently merge into the top-level session dict, "
            "which risks collision with existing fields. "
            "Reviewers should focus on the merge strategy in draft_optimization.py and verify "
            "that the error handling in models.py covers network failures during save."
        ),
        file_tree=(
            "  M api/draft_optimization.py  (+83 / -12)\n"
            "  M api/models.py  (+7 / -3)\n"
            "  M api/routes.py  (+6 / -6)\n"
            "  M api/store.py  (+4 / -0)\n"
            "  + api/draft.py  (+47 / -0)"
        ),
        flow_steps=[
            ("Webhook", "PR event", LIGHT_BLUE),
            ("Graphify", "code analysis", LIGHT_PURPLE),
            ("DeepThink", "review loop", LIGHT_ORANGE),
            ("Excalidraw", "diagram out", LIGHT_GREEN),
        ],
        output_path=sys.argv[1] if len(sys.argv) > 1 else "/tmp/review.excalidraw",
    )
    print(f"Diagram: {output}")
