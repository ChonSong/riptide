#!/usr/bin/env python3
"""
grafiphy/renderers/architecture.py — Architecture/community topology renderer.

Shows: Communities as clusters, god nodes as hubs, code nodes as members.
Uses REAL graphify data: communities, god-nodes, query results.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import matplotlib.patheffects as pe
from math import cos, sin


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
DIM = "#475569"

COMMUNITY_COLORS = [CYAN, EMERALD, VIOLET, AMBER, ROSE, ORANGE, "#38bdf8", "#f472b6"]


def render_architecture(graph_data: dict, output_path: str, title: str = None) -> str:
    """
    Render architecture diagram from graphify graph data.
    
    Args:
        graph_data: Dict with 'nodes', 'edges', 'communities', 'god_nodes'
        output_path: Path to save PNG
        title: Optional title
    
    Returns:
        Path to saved PNG
    """
    nodes = graph_data.get("nodes", [])
    communities = graph_data.get("communities", {})
    god_nodes = graph_data.get("god_nodes", [])
    
    # ── Figure setup ──
    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=150)
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
        fig.text(0.5, 0.97, title, ha="center", va="top", fontsize=18, fontweight="bold",
                 color="white", fontfamily="monospace",
                 path_effects=[pe.withStroke(linewidth=2, foreground=BG)])
    
    # ── Layout communities as clusters ──
    # Place communities in a circle, god nodes in center
    center_x, center_y = 50, 50
    comm_positions = {}
    comm_list = list(communities.keys())
    
    for i, community in enumerate(comm_list[:6]):
        angle = (2 * 3.14159 * i) / min(len(comm_list), 6)
        radius = 25
        x = center_x + radius * cos(angle)
        y = center_y + radius * sin(angle)
        comm_positions[community] = (x, y)
        
        color = COMMUNITY_COLORS[i % len(COMMUNITY_COLORS)]
        members = communities[community]
        
        # Draw community cluster
        box_w = 18
        box_h = 12
        
        # Glow
        ax.add_patch(FancyBboxPatch((x - box_w/2 - 1, y - box_h/2 - 1), 
                                    box_w + 2, box_h + 2,
                                    boxstyle="round,pad=0.5",
                                    facecolor=color, alpha=0.15, edgecolor="none"))
        # Box
        ax.add_patch(FancyBboxPatch((x - box_w/2, y - box_h/2), box_w, box_h,
                                    boxstyle="round,pad=0.4",
                                    facecolor=color, alpha=0.25,
                                    edgecolor=color, linewidth=2))
        # Community name
        label = community
        if len(label) > 15:
            label = label[:12] + "..."
        ax.text(x, y + 2, label, fontsize=8, fontweight="bold", color="white",
                fontfamily="monospace", ha="center", va="center")
        # Member count
        ax.text(x, y - 2, f"{len(members)} nodes", fontsize=7, color=color,
                fontfamily="monospace", ha="center", va="center", alpha=0.8)
    
    # ── Draw god nodes in center ──
    god_positions = {}
    for i, god in enumerate(god_nodes[:5]):
        angle = (2 * 3.14159 * i) / min(len(god_nodes), 5)
        radius = 10
        x = center_x + radius * cos(angle)
        y = center_y + radius * sin(angle)
        god_positions[god["name"]] = (x, y)
        
        # Draw god node
        ax.add_patch(FancyBboxPatch((x - 6, y - 3), 12, 6,
                                    boxstyle="round,pad=0.3",
                                    facecolor=AMBER, alpha=0.3,
                                    edgecolor=AMBER, linewidth=2))
        label = god["name"]
        if len(label) > 10:
            label = label[:7] + "..."
        ax.text(x, y, label, fontsize=7, fontweight="bold", color=AMBER,
                fontfamily="monospace", ha="center", va="center")
    
    # ── Draw connections between communities ──
    for i in range(len(comm_list) - 1):
        if comm_list[i] in comm_positions and comm_list[i + 1] in comm_positions:
            x1, y1 = comm_positions[comm_list[i]]
            x2, y2 = comm_positions[comm_list[i + 1]]
            color = COMMUNITY_COLORS[i % len(COMMUNITY_COLORS)]
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                        arrowprops=dict(arrowstyle="-|>", color=color,
                                        linewidth=1.5, alpha=0.5,
                                        connectionstyle="arc3,rad=0.1"))
    
    # ── Draw connections from god nodes to communities ──
    for god in god_nodes[:5]:
        god_name = god["name"]
        if god_name not in god_positions:
            continue
        
        # Find which community this god belongs to
        god_community = None
        for node in nodes:
            if node["name"] == god_name:
                god_community = node.get("community")
                break
        
        if god_community and god_community in comm_positions:
            x1, y1 = god_positions[god_name]
            x2, y2 = comm_positions[god_community]
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                        arrowprops=dict(arrowstyle="-|>", color=AMBER,
                                        linewidth=1, alpha=0.4,
                                        connectionstyle="arc3,rad=0.1"))
    
    # ── Legend ──
    legend_y = 6
    legend_items = [(c, COMMUNITY_COLORS[i % len(COMMUNITY_COLORS)]) for i, c in enumerate(comm_list[:6])]
    for i, (label, color) in enumerate(legend_items):
        x = 4 + (i % 3) * 32
        y = legend_y - (i // 3) * 4
        ax.add_patch(FancyBboxPatch((x - 1, y - 1), 2, 2, boxstyle="round,pad=0.2",
                                    facecolor=color, edgecolor=color, alpha=0.9))
        display = label if len(label) < 20 else label[:17] + "..."
        ax.text(x + 2.5, y, display, fontsize=7, color=SLATE, fontfamily="monospace", va="center")
    
    # ── Footer ──
    fig.text(0.5, 0.01,
             f"grafiphy · architecture · {len(communities)} communities · {len(god_nodes)} god nodes · {len(nodes)} code nodes",
             ha="center", va="bottom", fontsize=9, color=DIM, fontfamily="monospace")
    
    plt.tight_layout(rect=[0, 0.03, 1, 0.92])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close()
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render architecture diagram from graphify data")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file")
    parser.add_argument("--output", "-o", required=True, help="Output PNG path")
    parser.add_argument("--title", "-t", help="Diagram title")
    args = parser.parse_args()
    
    graph_data = json.loads(Path(args.input).read_text())
    result = render_architecture(graph_data, args.output, args.title)
    print(f"OK: {result}")


if __name__ == "__main__":
    main()
