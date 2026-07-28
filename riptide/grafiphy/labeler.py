#!/usr/bin/env python3
"""
grafiphy/labeler.py — Template-based label generator.

Uses graphify data structure to generate consistent labels.
No LLM needed — guarantees format compliance.

Format: "Does X via `functionName()`"
Edge: "sends `payload` to `target()`"
"""
import json
import os
import re
from pathlib import Path
from typing import Optional


def generate_labels(diff: list[dict], graph_data: dict) -> dict:
    """
    Generate labels using templates based on graphify data structure.
    
    Args:
        diff: List of file dicts from GitHub API
        graph_data: Dict with 'nodes', 'edges', 'communities', 'god_nodes'
    
    Returns:
        Dict with 'nodes' (name → label) and 'edges' (source|target → label)
    """
    nodes = {}
    edges = {}
    
    # Label nodes based on their type and community
    for node in graph_data.get("nodes", []):
        name = node.get("name", "")
        community = node.get("community", "")
        source = node.get("source", "")
        
        if not name:
            continue
        
        # Determine the action based on the node name and community
        label = _generate_node_label(name, community, source)
        nodes[name] = label
    
    # Label god nodes
    for god in graph_data.get("god_nodes", []):
        name = god.get("name", "")
        edges_count = god.get("edges", 0)
        if name:
            nodes[name] = f"Coordinates {edges_count} connections via `{name}`"
    
    # Label edges based on relationship type
    for fname, affected in graph_data.get("blast_radius", {}).items():
        for edge in affected.get("edges", []):
            target = edge.get("target", "")
            relation = edge.get("relation", "related")
            key = f"{fname}|{target}"
            edges[key] = _generate_edge_label(fname, target, relation)
    
    # Label community edges
    comm_list = list(graph_data.get("communities", {}).keys())
    for i in range(len(comm_list) - 1):
        src = comm_list[i]
        dst = comm_list[i + 1]
        key = f"{src}|{dst}"
        edges[key] = f"triggers `{dst}`"
    
    return {"nodes": nodes, "edges": edges}


def _generate_node_label(name: str, community: str, source: str) -> str:
    """Generate a label for a node based on its properties."""
    # Clean up the name for display
    display_name = name
    
    # Determine action based on name patterns
    if "()" in name:
        # It's a function
        func_name = name.replace("()", "")
        
        # Common function patterns
        if func_name.startswith("get") or func_name.startswith("load") or func_name.startswith("read"):
            return f"Reads data via `{name}`"
        elif func_name.startswith("set") or func_name.startswith("save") or func_name.startswith("write"):
            return f"Writes data via `{name}`"
        elif func_name.startswith("render") or func_name.startswith("build") or func_name.startswith("create"):
            return f"Creates UI via `{name}`"
        elif func_name.startswith("init") or func_name.startswith("setup") or func_name.startswith("start"):
            return f"Initializes via `{name}`"
        elif func_name.startswith("check") or func_name.startswith("validate") or func_name.startswith("is"):
            return f"Validates via `{name}`"
        elif func_name.startswith("handle") or func_name.startswith("on"):
            return f"Handles events via `{name}`"
        elif func_name.startswith("inject") or func_name.startswith("add") or func_name.startswith("insert"):
            return f"Injects via `{name}`"
        elif func_name.startswith("open") or func_name.startswith("show") or func_name.startswith("display"):
            return f"Opens via `{name}`"
        elif func_name.startswith("close") or func_name.startswith("hide") or func_name.startswith("remove"):
            return f"Closes via `{name}`"
        elif func_name.startswith("sync") or func_name.startswith("update") or func_name.startswith("refresh"):
            return f"Synchronizes via `{name}`"
        elif func_name.startswith("coord") or func_name.startswith("manage"):
            return f"Coordinates via `{name}`"
        else:
            return f"Executes `{name}`"
    else:
        # It's a file or component
        if ".js" in name or ".ts" in name:
            return f"Implements logic via `{name}`"
        elif ".css" in name or ".scss" in name:
            return f"Styles via `{name}`"
        elif ".json" in name:
            return f"Configures via `{name}`"
        elif ".md" in name:
            return f"Documents via `{name}`"
        elif ".py" in name:
            return f"Processes via `{name}`"
        else:
            return f"Provides `{name}`"


def _generate_edge_label(source: str, target: str, relation: str) -> str:
    """Generate a label for an edge based on relationship type."""
    # Clean up names
    src = source.split("/")[-1] if "/" in source else source
    tgt = target.split("/")[-1] if "/" in target else target
    
    # Determine payload based on relation type
    if relation in ("calls", "invokes"):
        return f"triggers `{tgt}`"
    elif relation in ("imports", "requires"):
        return f"imports `{tgt}`"
    elif relation in ("references", "uses"):
        return f"uses `{tgt}`"
    elif relation in ("extends", "inherits"):
        return f"extends `{tgt}`"
    elif relation in ("contains", "has"):
        return f"contains `{tgt}`"
    elif relation in ("sends", "emits"):
        return f"sends data to `{tgt}`"
    else:
        return f"triggers `{tgt}`"
