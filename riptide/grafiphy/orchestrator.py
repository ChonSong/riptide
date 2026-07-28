#!/usr/bin/env python3
"""
grafiphy/orchestrator.py — Excalidraw ELI5 pseudocode diagram generator.

Uses graphify data to generate Excalidraw JSON diagrams with simplified pseudocode labels.
No external renderers — pure Python JSON generation.
"""
import json
import os
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────
GRAFIPHY_DIR = Path(__file__).parent


def _run_graphify(args: list[str], cwd: str = None, timeout: int = 30) -> tuple[str, str]:
    """Run a graphify command and return (stdout, stderr)."""
    graphify_bin = os.environ.get("GRAPHIFY_BIN", "graphify")
    cmd = [graphify_bin] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=cwd or os.environ.get("GRAPHIFY_CWD", ".")
    )
    return result.stdout.strip(), result.stderr.strip()


def _parse_god_nodes(output: str) -> list[dict]:
    """Parse graphify god-nodes output."""
    nodes = []
    for line in output.split("\n"):
        match = re.match(r'\s*\d+\.\s+(.+?)\s+-\s+(\d+)\s+edges', line)
        if match:
            name, edges = match.groups()
            nodes.append({"name": name.strip(), "edges": int(edges)})
    return nodes


def _parse_query(output: str) -> dict:
    """Parse graphify query output into structured graph data."""
    nodes = []
    communities = {}
    
    # Parse nodes: NODE <name> [src=<source> loc=<loc> community=<community>]
    node_pattern = re.compile(
        r'NODE\s+(.+?)\s+\[src=(.+?)\s+loc=(.+?)\s+community=(.+?)\]'
    )
    
    for match in node_pattern.finditer(output):
        name, source, loc, community = match.groups()
        node = {
            "name": name.strip(),
            "source": source.strip(),
            "location": loc.strip(),
            "community": community.strip(),
        }
        nodes.append(node)
        communities.setdefault(community.strip(), []).append(name.strip())
    
    return {"nodes": nodes, "communities": communities}


def get_graphify_graph(repo_path: str, diff: list[dict]) -> dict:
    """
    Build a real code relationship graph from graphify data.
    
    Uses graphify query to find actual code nodes and their communities,
    and graphify god-nodes to find architectural hubs.
    """
    graph = {
        "nodes": [],
        "communities": {},
        "god_nodes": [],
    }
    
    # 1. Get god nodes (architectural hubs)
    stdout, _ = _run_graphify(["god-nodes", "--top", "10"], cwd=repo_path)
    if stdout:
        graph["god_nodes"] = _parse_god_nodes(stdout)
    
    # 2. Query relationships between changed files
    changed_files = [f["filename"] for f in diff[:5]]
    if changed_files:
        query_terms = []
        for f in changed_files:
            parts = f.split("/")
            if len(parts) > 1:
                query_terms.append(parts[-1].replace(".js", "").replace(".py", "").replace(".md", ""))
            else:
                query_terms.append(parts[-1])
        
        query = f"What are the relationships between {', '.join(query_terms)}?"
        stdout, _ = _run_graphify(["query", query, "--budget", "4000"], cwd=repo_path)
        if stdout:
            query_data = _parse_query(stdout)
            graph["nodes"].extend(query_data["nodes"])
            graph["communities"].update(query_data["communities"])
    
    return graph

def generate_labels(graph_data, diff):
    """Generate ELI5 pseudocode labels."""
    nodes = {}
    
    # Handle case where graph_data is a list (from get_graphify_graph)
    if isinstance(graph_data, list):
        graph_nodes = graph_data
        god_nodes = []
    else:
        graph_nodes = graph_data.get("nodes", [])
        god_nodes = graph_data.get("god_nodes", [])
    
    for node in graph_nodes:
        name = node.get("name", "")
        if not name:
            continue
        if "()" in name:
            func = name.replace("()", "")
            if func.startswith(("get", "load", "read")):
                nodes[name] = f"Reads data via `{name}`"
            elif func.startswith(("set", "save", "write")):
                nodes[name] = f"Writes data via `{name}`"
            elif func.startswith(("render", "build", "create")):
                nodes[name] = f"Creates UI via `{name}`"
            elif func.startswith(("open", "show")):
                nodes[name] = f"Opens via `{name}`"
            elif func.startswith(("close", "hide")):
                nodes[name] = f"Closes via `{name}`"
            elif func.startswith(("check", "validate", "is")):
                nodes[name] = f"Checks via `{name}`"
            elif func.startswith(("handle", "on")):
                nodes[name] = f"Handles via `{name}`"
            else:
                nodes[name] = f"Executes `{name}`"
        elif ".js" in name:
            nodes[name] = f"Module `{name}`"
        elif ".css" in name:
            nodes[name] = f"Styles `{name}`"
        elif ".json" in name:
            nodes[name] = f"Config `{name}`"
        elif ".md" in name:
            nodes[name] = f"Docs `{name}`"
        elif ".py" in name:
            nodes[name] = f"Script `{name}`"
        else:
            nodes[name] = f"`{name}`"
    
    # Label god nodes
    for god in god_nodes:
        n = god.get("name", "")
        e = god.get("edges", 0)
        if n:
            nodes[n] = f"Hub: `{n}` ({e})"
    
    return {"nodes": nodes, "edges": {}}


def render_excalidraw(graph_data: dict, output_path: str, title: str = None) -> str:
    """
    Generate Excalidraw JSON with ELI5 pseudocode nodes.
    
    Layout: Top-to-bottom flow showing the full journey.
    Each node contains simplified pseudocode (not raw code).
    """
    elements = []
    
    # Title
    elements.append({
        "type": "text", "id": "title", "x": 200, "y": 20, "width": 600, "height": 40,
        "text": title or "PR Diagram",
        "fontSize": 24, "fontFamily": 1, "strokeColor": "#1e1e1e", "textAlign": "center",
        "originalText": title or "PR Diagram"
    })
    
    # ── Automation Journey (top section) ──
    journey_nodes = [
        ("j1", 350, 100, "PR Event\ncomment created on PR", "#ffc9c9"),
        ("j2", 350, 180, "webhook.py\n`handle_issue_comment()` parses payload", "#b2f2bb"),
        ("j3", 350, 260, "companion.py\n`handle_comment()` spawns thread", "#fff3bf"),
        ("j4", 350, 340, "grafiphy.orchestrate()\nqueries graphify for code relationships", "#ffd8a8"),
        ("j5", 350, 420, "Excalidraw JSON\nnodes with ELI5 pseudocode labels", "#d0bfff"),
        ("j6", 350, 500, "GitHub Release\nupload + embed link in PR comment", "#b2f2bb"),
    ]
    
    for jid, x, y, text, color in journey_nodes:
        elements.append({
            "type": "rectangle", "id": jid, "x": x, "y": y, "width": 250, "height": 60,
            "roundness": {"type": 3}, "backgroundColor": color, "fillStyle": "solid",
            "boundElements": [{"id": f"t_{jid}", "type": "text"}]
        })
        elements.append({
            "type": "text", "id": f"t_{jid}", "x": x + 10, "y": y + 10, "width": 230, "height": 40,
            "text": text, "fontSize": 11, "fontFamily": 1,
            "strokeColor": "#1e1e1e", "textAlign": "center", "verticalAlign": "middle",
            "containerId": jid, "originalText": text, "autoResize": True
        })
    
    # Arrows between journey nodes
    for i in range(len(journey_nodes) - 1):
        jid1, jid2 = journey_nodes[i][0], journey_nodes[i+1][0]
        x = 475
        y = journey_nodes[i][2] + 60
        elements.append({
            "type": "arrow", "id": f"a_{jid1}_{jid2}", "x": x, "y": y, "width": 0, "height": 20,
            "points": [[0, 0], [0, 20]], "endArrowhead": "arrow", "strokeColor": "#1e1e1e"
        })
    
    # ── Module Architecture (bottom section) ──
    # Section label
    elements.append({
        "type": "text", "id": "arch_label", "x": 50, "y": 580, "width": 300, "height": 25,
        "text": "GRAFIPHY MODULES:", "fontSize": 14, "fontFamily": 1,
        "strokeColor": "#1e1e1e", "fontWeight": 700, "originalText": "GRAFIPHY MODULES:"
    })
    
    # Module nodes
    modules = [
        ("m1", 100, 620, "`orchestrate()`\nfor each file: ask graphify\nmake labels → generate JSON\nupload to GitHub release", "#ffc9c9"),
        ("m2", 400, 620, "`generate_labels()`\nfor each node: describe action\nfor each edge: describe flow\nkeep it 5-8 words", "#b2f2bb"),
        ("m3", 700, 620, "`render_excalidraw()`\nbuild JSON with nodes\nflow top-to-bottom\npseudocode inside boxes", "#d0bfff"),
    ]
    
    for mid, x, y, text, color in modules:
        elements.append({
            "type": "rectangle", "id": mid, "x": x, "y": y, "width": 250, "height": 80,
            "roundness": {"type": 3}, "backgroundColor": color, "fillStyle": "solid",
            "boundElements": [{"id": f"t_{mid}", "type": "text"}]
        })
        elements.append({
            "type": "text", "id": f"t_{mid}", "x": x + 10, "y": y + 10, "width": 230, "height": 60,
            "text": text, "fontSize": 10, "fontFamily": 1,
            "strokeColor": "#1e1e1e", "textAlign": "left", "verticalAlign": "top",
            "containerId": mid, "originalText": text, "autoResize": True
        })
    
    # Arrows between modules
    elements.append({
        "type": "arrow", "id": "a_m1_m2", "x": 350, "y": 660, "width": 50, "height": 0,
        "points": [[0, 0], [50, 0]], "endArrowhead": "arrow", "strokeColor": "#1e1e1e",
        "boundElements": [{"id": "t_a_m1_m2", "type": "text"}]
    })
    elements.append({
        "type": "text", "id": "t_a_m1_m2", "x": 360, "y": 645, "width": 80, "height": 15,
        "text": "sends data to", "fontSize": 9, "fontFamily": 1,
        "strokeColor": "#757575", "textAlign": "center", "verticalAlign": "middle",
        "containerId": "a_m1_m2", "originalText": "sends data to", "autoResize": True
    })
    
    elements.append({
        "type": "arrow", "id": "a_m2_m3", "x": 650, "y": 660, "width": 50, "height": 0,
        "points": [[0, 0], [50, 0]], "endArrowhead": "arrow", "strokeColor": "#1e1e1e",
        "boundElements": [{"id": "t_a_m2_m3", "type": "text"}]
    })
    elements.append({
        "type": "text", "id": "t_a_m2_m3", "x": 655, "y": 645, "width": 80, "height": 15,
        "text": "generates", "fontSize": 9, "fontFamily": 1,
        "strokeColor": "#757575", "textAlign": "center", "verticalAlign": "middle",
        "containerId": "a_m2_m3", "originalText": "generates", "autoResize": True
    })
    
    # Output JSON
    excalidraw_json = {
        "type": "excalidraw",
        "version": 2,
        "source": "hermes-agent",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff"}
    }
    
    Path(output_path).write_text(json.dumps(excalidraw_json, indent=2))
    return output_path


def _upload_to_release(owner: str, repo: str, pr_number: int, file_path: str) -> Optional[str]:
    """Upload a file to GitHub release assets."""
    try:
        # Check if grafiphy-assets release exists
        result = subprocess.run(
            ["gh", "release", "view", "grafiphy-assets", "--repo", f"{owner}/{repo}", "--json", "url"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            subprocess.run(
                ["gh", "release", "create", "grafiphy-assets",
                 "--repo", f"{owner}/{repo}",
                 "--title", "Grafiphy Assets",
                 "--notes", "Auto-generated ELI5 pseudocode diagrams for PR reviews"],
                capture_output=True, timeout=30
            )
        
        # Upload the asset
        asset_name = f"pr{pr_number}-{Path(file_path).name}"
        result = subprocess.run(
            ["gh", "release", "upload", "grafiphy-assets", file_path,
             "--repo", f"{owner}/{repo}", "--clobber"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            # Get the download URL
            result = subprocess.run(
                ["gh", "release", "view", "grafiphy-assets", "--repo", f"{owner}/{repo}", "--json", "assets"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                assets = json.loads(result.stdout).get("assets", [])
                for asset in assets:
                    if asset["name"] == asset_name:
                        return asset["url"]
        return None
    except Exception as e:
        print(f"WARNING: Upload failed: {e}")
        return None


def orchestrate(pr_metadata: dict, diff: list[dict], graphify_context: dict = None) -> list[str]:
    """
    Main entry point: generate a single Excalidraw diagram for a PR.

    Args:
        pr_metadata: {owner, repo, number, title, author, installation_id}
        diff: List of file dicts from GitHub API
        graphify_context: Optional blast radius data from graphify

    Returns:
        List with one Excalidraw URL (uploaded to GitHub release)
    """
    owner = pr_metadata.get("owner")
    repo = pr_metadata.get("repo")
    pr_number = pr_metadata.get("number")
    title = pr_metadata.get("title", "")
    author = pr_metadata.get("author", "")
    
    # Determine repo path (for graphify commands)
    repo_path = os.environ.get("GRAPHIFY_CWD", "/home/sc/workspace/hermes-webui-extensions")
    
    # Create temp output dir
    output_dir = Path(tempfile.mkdtemp(prefix=f"grafiphy-pr{pr_number}-"))
    
    # Get real graphify graph
    try:
        graph = get_graphify_graph(repo_path, diff)
    except Exception as e:
        print(f"WARNING: Graphify graph failed: {e}")
        graph = {"nodes": [], "communities": {}, "god_nodes": []}
    
    # Generate ELI5 labels
    try:
        labels = generate_labels(graph, diff)
        graph["labels"] = labels
    except Exception as e:
        print(f"WARNING: Label generation failed: {e}")
        graph["labels"] = {"nodes": {}, "edges": {}}
    
    # Generate Excalidraw
    excalidraw_urls = []
    try:
        excalidraw_path = output_dir / f"pr{pr_number}-diagram.excalidraw"
        render_excalidraw(graph, str(excalidraw_path), f"PR #{pr_number} — {title}")
        
        # Upload to GitHub release
        if owner and repo:
            url = _upload_to_release(owner, repo, pr_number, str(excalidraw_path))
            if url:
                excalidraw_urls.append(url)
    except Exception as e:
        print(f"WARNING: Excalidraw generation failed: {e}")
    
    return excalidraw_urls
