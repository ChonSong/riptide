#!/usr/bin/env python3
"""
grafiphy/renderers/sankey.py — Sankey diagram renderer for code relationship flow.

Shows: Changed Files → Affected Code Nodes → Communities
Uses REAL graphify data: query results, god-nodes, community structure.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.patheffects as pe


# ── Palette ──
BG = "#020617"
GRID = "#1e293b"
CYAN = "#22d3ee"
EMERALD = "#34d399"
VIOLET = "#a78bfa"
AMBER = "#fbbf24"
ROSE = "#fb7185"
ORANGE = "#fb923c"
SLATE = "#94a3b8"
RED = "#ef4444"
DIM = "#475569"

COMMUNITY_COLORS = [CYAN, EMERALD, VIOLET, AMBER, ROSE, ORANGE, "#38bdf8", "#f472b6"]


def render_sankey(graph_data: dict, output_path: str, title: str = None) -> str:
    """
    Render Sankey diagram showing code relationship flow.
    
    Args:
        graph_data: Dict with 'nodes', 'edges', 'communities', 'god_nodes', 'blast_radius', 'labels'
        output_path: Path to save PNG
        title: Optional title
    
    Returns:
        Path to saved PNG
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    communities = graph_data.get("communities", {})
    god_nodes = graph_data.get("god_nodes", [])
    blast_radius = graph_data.get("blast_radius", {})
    labels = graph_data.get("labels", {})
    
    # ── Figure setup ──
    fig, ax = plt.subplots(figsize=(18, 11), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    
    # ── Grid ──
    for x in range(0, 101, 4):
        ax.axvline(x, color=GRID, linewidth=0.3, alpha=0.3)
    for y in range(0, 101, 4):
        ax.axhline(y, color=GRID, linewidth=0.3, alpha=0.3)
    
    # ── Title ──
    if title:
        fig.text(0.5, 0.97, title, ha="center", va="top", fontsize=16, fontweight="bold",
                 color="white", fontfamily="monospace",
                 path_effects=[pe.withStroke(linewidth=2, foreground=BG)])
    
    # ── Layout: 3 columns ──
    # Column 1: Changed Files (left)
    # Column 2: Code Nodes (middle)  
    # Column 3: Communities (right)
    
    col1_x = 10
    col2_x = 45
    col3_x = 80
    
    # Column 1: Changed files
    changed_files = list(blast_radius.keys()) if blast_radius else []
    if not changed_files:
        # Use nodes from query
        changed_files = [n["name"] for n in nodes[:5]]
    
    file_positions = {}
    for i, fname in enumerate(changed_files[:6]):
        y = 20 + i * 12
        file_positions[fname] = (col1_x, y)
        
        # Draw node
        color = CYAN
        ax.add_patch(FancyBboxPatch((col1_x - 8, y - 3), 16, 6,
                                    boxstyle="round,pad=0.3",
                                    facecolor=color, alpha=0.25,
                                    edgecolor=color, linewidth=1.5))
        # Use LLM label if available, else truncate filename
        label = labels.get("nodes", {}).get(fname, fname.split("/")[-1] if "/" in fname else fname)
        if len(label) > 15:
            label = label[:12] + "..."
        ax.text(col1_x, y, label, fontsize=7, fontweight="bold", color="white",
                fontfamily="monospace", ha="center", va="center")
    
    # Column 2: Code nodes (from query)
    code_nodes = [n for n in nodes if n.get("community") and n["name"] not in file_positions]
    node_positions = {}
    for i, node in enumerate(code_nodes[:8]):
        y = 15 + i * 10
        node_positions[node["name"]] = (col2_x, y)
        
        # Color by community
        community = node.get("community", "unknown")
        color = COMMUNITY_COLORS[hash(community) % len(COMMUNITY_COLORS)]
        
        ax.add_patch(FancyBboxPatch((col2_x - 10, y - 3), 20, 6,
                                    boxstyle="round,pad=0.3",
                                    facecolor=color, alpha=0.25,
                                    edgecolor=color, linewidth=1.5))
        # Use LLM label if available
        label = labels.get("nodes", {}).get(node["name"], node["name"])
        if len(label) > 18:
            label = label[:15] + "..."
        ax.text(col2_x, y, label, fontsize=6.5, fontweight="bold", color="white",
                fontfamily="monospace", ha="center", va="center")
    
    # Column 3: Communities
    comm_positions = {}
    for i, (community, members) in enumerate(list(communities.items())[:6]):
        y = 20 + i * 12
        comm_positions[community] = (col3_x, y)
        
        color = COMMUNITY_COLORS[i % len(COMMUNITY_COLORS)]
        ax.add_patch(FancyBboxPatch((col3_x - 10, y - 3), 20, 6,
                                    boxstyle="round,pad=0.3",
                                    facecolor=color, alpha=0.25,
                                    edgecolor=color, linewidth=1.5))
        # Use LLM label if available
        label = labels.get("nodes", {}).get(community, community)
        if len(label) > 18:
            label = label[:15] + "..."
        ax.text(col3_x, y, label, fontsize=7, fontweight="bold", color="white",
                fontfamily="monospace", ha="center", va="center")
    
    # ── Draw links with labels ──
    # Changed files → code nodes
    for fname, affected in blast_radius.items():
        if fname not in file_positions:
            continue
        x1, y1 = file_positions[fname]
        for edge in affected.get("edges", [])[:3]:
            target = edge["target"]
            if target in node_positions:
                x2, y2 = node_positions[target]
                ax.annotate("", xy=(x2 - 10, y2), xytext=(x1 + 8, y1),
                            arrowprops=dict(arrowstyle="-|>", color=CYAN,
                                            linewidth=1, alpha=0.5,
                                            connectionstyle="arc3,rad=0.1"))
                # Draw edge label
                edge_key = f"{fname}|{target}"
                edge_label = labels.get("edges", {}).get(edge_key, edge.get("relation", ""))
                if edge_label:
                    mid_x = (x1 + 8 + x2 - 10) / 2
                    mid_y = (y1 + y2) / 2
                    ax.text(mid_x, mid_y + 1, edge_label, fontsize=5, color=CYAN,
                            fontfamily="monospace", ha="center", va="center", alpha=0.7)
    
    # Code nodes → communities
    for node in nodes:
        name = node["name"]
        community = node.get("community")
        if name in node_positions and community in comm_positions:
            x1, y1 = node_positions[name]
            x2, y2 = comm_positions[community]
            color = COMMUNITY_COLORS[list(communities.keys()).index(community) % len(COMMUNITY_COLORS)]
            ax.annotate("", xy=(x2 - 10, y2), xytext=(x1 + 10, y1),
                        arrowprops=dict(arrowstyle="-|>", color=color,
                                        linewidth=0.8, alpha=0.4,
                                        connectionstyle="arc3,rad=0.1"))
            # Draw edge label
            edge_key = f"{name}|{community}"
            edge_label = labels.get("edges", {}).get(edge_key, "")
            if edge_label:
                mid_x = (x1 + 10 + x2 - 10) / 2
                mid_y = (y1 + y2) / 2
                ax.text(mid_x, mid_y + 1, edge_label, fontsize=5, color=color,
                        fontfamily="monospace", ha="center", va="center", alpha=0.6)
    
    # ── God nodes annotation ──
    if god_nodes:
        god_text = "God Nodes: " + ", ".join([f"{g['name']} ({g['edges']})" for g in god_nodes[:3]])
        fig.text(0.5, 0.02, god_text, ha="center", va="bottom", fontsize=9, color=AMBER,
                 fontfamily="monospace")
    
    # ── Footer ──
    fig.text(0.5, 0.005,
             f"grafiphy · sankey · {len(nodes)} code nodes · {len(communities)} communities · {len(god_nodes)} god nodes",
             ha="center", va="bottom", fontsize=8, color=DIM, fontfamily="monospace")
    
    plt.tight_layout(rect=[0, 0.03, 1, 0.92])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close()
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render Sankey diagram from graphify data")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file")
    parser.add_argument("--output", "-o", required=True, help="Output PNG path")
    parser.add_argument("--title", "-t", help="Diagram title")
    args = parser.parse_args()
    
    graph_data = json.loads(Path(args.input).read_text())
    result = render_sankey(graph_data, args.output, args.title)
    print(f"OK: {result}")


if __name__ == "__main__":
    main()
