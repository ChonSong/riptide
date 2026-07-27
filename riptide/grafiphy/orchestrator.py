#!/usr/bin/env python3
"""
grafiphy/orchestrator.py — Main orchestrator for grafiphy visual evidence engine.

Uses REAL graphify data: query, affected, god-nodes, explain, path.
Renders actual code relationships, not directory groupings.
"""
import json
import os
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Optional

# ── Constants ──
GRAFIPHY_DIR = Path(__file__).parent
RENDERERS_DIR = GRAFIPHY_DIR / "renderers"


def _run_graphify(args: list[str], cwd: str = None, timeout: int = 30) -> tuple[str, str]:
    """Run a graphify command and return (stdout, stderr)."""
    graphify_bin = os.environ.get("GRAPHIFY_BIN", "graphify")
    cmd = [graphify_bin] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=cwd or os.environ.get("GRAPHIFY_CWD", ".")
    )
    return result.stdout.strip(), result.stderr.strip()


def _parse_graphify_query(output: str) -> dict:
    """Parse graphify query output into structured graph data."""
    nodes = []
    edges = []
    
    # Parse nodes: NODE <name> [src=<source> loc=<loc> community=<community>]
    node_pattern = re.compile(
        r'NODE\s+(.+?)\s+\[src=(.+?)\s+loc=(.+?)\s+community=(.+?)\]'
    )
    
    for match in node_pattern.finditer(output):
        name, source, loc, community = match.groups()
        nodes.append({
            "name": name.strip(),
            "source": source.strip(),
            "location": loc.strip(),
            "community": community.strip(),
        })
    
    return {"nodes": nodes, "edges": edges}


def _parse_graphify_affected(output: str) -> dict:
    """Parse graphify affected output into structured data."""
    nodes = []
    edges = []
    
    lines = output.split("\n")
    current_file = None
    
    for line in lines:
        line = line.strip()
        if line.startswith("Affected nodes for"):
            current_file = line.replace("Affected nodes for", "").strip()
        elif line.startswith("- "):
            # - <node> [source=... community=...] or - <node> → <relation> [source=...]
            node_match = re.match(r'-\s+(.+?)\s+\[(.+)\]', line)
            if node_match:
                node_name, attrs = node_match.groups()
                node_data = {"name": node_name.strip(), "attributes": {}}
                
                # Parse attributes
                for attr_match in re.finditer(r'(\w+)=([^\s]+)', attrs):
                    key, val = attr_match.groups()
                    node_data["attributes"][key] = val
                
                nodes.append(node_data)
                
                # Create edge from current file to this node
                if current_file:
                    edges.append({
                        "source": current_file,
                        "target": node_name.strip(),
                        "relation": node_data["attributes"].get("relation", "related"),
                    })
    
    return {"nodes": nodes, "edges": edges}


def _parse_god_nodes(output: str) -> list[dict]:
    """Parse graphify god-nodes output."""
    nodes = []
    for line in output.split("\n"):
        match = re.match(r'\s*\d+\.\s+(.+?)\s+-\s+(\d+)\s+edges', line)
        if match:
            name, edges = match.groups()
            nodes.append({"name": name.strip(), "edges": int(edges)})
    return nodes


def _parse_explain(output: str) -> dict:
    """Parse graphify explain output."""
    result = {"node": "", "connections": []}
    
    node_match = re.search(r'Node:\s+(.+)', output)
    if node_match:
        result["node"] = node_match.group(1).strip()
    
    # Parse connections: --> <name> [relation] [source] or <-- <name> [relation] [source]
    for match in re.finditer(r'(--|<)\s+(.+?)\s+\[(.+?)\]\s+\[(.+?)\]', output):
        direction, name, relation, source = match.groups()
        result["connections"].append({
            "direction": "out" if "--" in direction else "in",
            "name": name.strip(),
            "relation": relation.strip(),
            "source": source.strip(),
        })
    
    return result


def get_graphify_graph(repo_path: str, diff: list[dict], graphify_context: dict = None) -> dict:
    """
    Build a real code relationship graph from graphify data.
    
    Uses graphify query to find actual code nodes and their communities,
    and graphify god-nodes to find architectural hubs.
    """
    graph = {
        "nodes": [],
        "edges": [],
        "communities": {},
        "god_nodes": [],
        "blast_radius": {},
    }
    
    # 1. Get god nodes (architectural hubs)
    stdout, _ = _run_graphify(["god-nodes", "--top", "10"], cwd=repo_path)
    if stdout:
        graph["god_nodes"] = _parse_god_nodes(stdout)
    
    # 2. Query relationships between changed files
    changed_files = [f["filename"] for f in diff[:5]]
    if changed_files:
        # Use just the file/dir names for the query
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
            query_data = _parse_graphify_query(stdout)
            graph["nodes"].extend(query_data["nodes"])
    
    # 3. Build community map from query results
    for node in graph["nodes"]:
        community = node.get("community", "unknown")
        graph["communities"].setdefault(community, []).append(node["name"])
    
    # 4. Add god nodes to communities
    for god in graph["god_nodes"]:
        # Find which community this god belongs to
        god_community = None
        for node in graph["nodes"]:
            if node["name"] == god["name"]:
                god_community = node.get("community")
                break
        
        if god_community:
            graph["communities"].setdefault(god_community, []).append(god["name"])
    
    return graph


def orchestrate(pr_metadata: dict, diff: list[dict], graphify_context: dict = None) -> list[str]:
    """
    Main entry point: generate all relevant diagrams for a PR using REAL graphify data.
    
    Args:
        pr_metadata: {owner, repo, number, title, author, installation_id}
        diff: List of file dicts from GitHub API
        graphify_context: Optional blast radius data from graphify
    
    Returns:
        List of PNG URLs (uploaded to GitHub release)
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
        graph = get_graphify_graph(repo_path, diff, graphify_context)
    except Exception as e:
        print(f"WARNING: Graphify graph failed: {e}")
        graph = {"nodes": [], "edges": [], "communities": {}, "god_nodes": [], "blast_radius": {}}
    
    # Generate LLM labels for nodes and edges
    try:
        from grafiphy.labeler import generate_labels
        labels = generate_labels(diff, graph)
        graph["labels"] = labels
    except Exception as e:
        print(f"WARNING: Label generation failed: {e}")
        graph["labels"] = {"nodes": {}, "edges": {}}
    
    png_paths = []
    
    # 1. Sankey diagram — code relationship flow (real graphify data)
    try:
        sankey_path = output_dir / "blast-radius.png"
        _run_renderer("sankey", graph, str(sankey_path), f"Blast Radius — {title}")
        png_paths.append(sankey_path)
    except Exception as e:
        print(f"WARNING: Sankey render failed: {e}")
    
    # 2. Mermaid diagram — code relationships (real graphify data)
    try:
        mermaid_path = output_dir / "call-flow.png"
        _run_renderer("mermaid", graph, str(mermaid_path), f"Call Flow — {title}")
        png_paths.append(mermaid_path)
    except Exception as e:
        print(f"WARNING: Mermaid render failed: {e}")
    
    # 3. Architecture diagram — community topology (real graphify data)
    try:
        arch_path = output_dir / "system-map.png"
        _run_renderer("architecture", graph, str(arch_path), f"System Map — {title}")
        png_paths.append(arch_path)
    except Exception as e:
        print(f"WARNING: Architecture render failed: {e}")
    
    # Upload all PNGs to GitHub release
    png_urls = []
    for png_path in png_paths:
        if not Path(png_path).exists():
            continue
        url = _upload_to_release(owner, repo, pr_number, str(png_path))
        if url:
            png_urls.append(url)
    
    return png_urls


def _build_sankey_from_graph(graph: dict) -> dict:
    """Build Sankey data from real graphify graph."""
    nodes = []
    links = []
    
    # Add god nodes as primary nodes
    for god in graph.get("god_nodes", [])[:5]:
        nodes.append({
            "name": god["name"],
            "color": _community_color("god"),
        })
    
    # Add community nodes
    for community, members in graph.get("communities", {}).items():
        if members:
            nodes.append({
                "name": community.split("/")[-1] if "/" in community else community,
                "color": _community_color(community),
            })
    
    # Add blast radius edges
    for fname, affected in graph.get("blast_radius", {}).items():
        source = fname.split("/")[-1]
        for edge in affected.get("edges", []):
            links.append({
                "source": source,
                "target": edge["target"],
                "value": 1,
            })
    
    # Add god node connections
    god_names = {g["name"] for g in graph.get("god_nodes", [])}
    for edge in graph.get("edges", []):
        if edge["source"] in god_names or edge["target"] in god_names:
            links.append({
                "source": edge["source"],
                "target": edge["target"],
                "value": 2,
            })
    
    return {"nodes": nodes, "links": links}


def _build_mermaid_from_graph(graph: dict) -> str:
    """Build Mermaid sequence diagram from real graphify graph."""
    lines = ["sequenceDiagram"]
    
    # Add participants from communities
    participants = set()
    for community, members in graph.get("communities", {}).items():
        if members:
            name = community.split("/")[-1] if "/" in community else community
            name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            participants.add(name)
            lines.append(f"    participant {name}")
    
    # Add blast radius interactions
    for fname, affected in graph.get("blast_radius", {}).items():
        source = fname.split("/")[-1]
        for edge in affected.get("edges", [])[:3]:
            target = edge["target"].split("/")[-1] if "/" in edge["target"] else edge["target"]
            lines.append(f"    {source}->>target: {edge.get('relation', 'calls')}")
    
    return "\n".join(lines)


def _build_topology_from_graph(graph: dict) -> dict:
    """Build architecture topology from real graphify graph."""
    components = []
    connections = []
    
    # Add god nodes as central components
    for i, god in enumerate(graph.get("god_nodes", [])[:5]):
        components.append({
            "name": god["name"],
            "type": "service",
            "x": 20 + i * 15,
            "y": 30,
            "width": 14,
            "height": 8,
        })
    
    # Add community components
    y_pos = 60
    for i, (community, members) in enumerate(graph.get("communities", {}).items()):
        if members:
            name = community.split("/")[-1] if "/" in community else community
            components.append({
                "name": name,
                "type": "frontend" if "ext" in community else "service",
                "x": 20 + (i % 3) * 25,
                "y": y_pos + (i // 3) * 20,
                "width": 14,
                "height": 8,
            })
    
    # Add connections from blast radius
    for fname, affected in graph.get("blast_radius", {}).items():
        source = fname.split("/")[-1]
        for edge in affected.get("edges", [])[:2]:
            target = edge["target"].split("/")[-1] if "/" in edge["target"] else edge["target"]
            connections.append({
                "from": source,
                "to": target,
                "label": edge.get("relation", "calls"),
            })
    
    return {"components": components, "connections": connections}


def _community_color(community: str) -> str:
    """Get color for a community."""
    colors = [
        "#22d3ee", "#34d399", "#a78bfa", "#fb7185", "#fb923c",
        "#fbbf24", "#38bdf8", "#f472b6", "#a78bfa", "#555",
    ]
    # Hash community name to get consistent color
    idx = hash(community) % len(colors)
    return colors[idx]


def _run_renderer(renderer_name: str, data: dict, output_path: str, title: str = None):
    """Run a renderer with JSON input."""
    data_path = Path(output_path).with_suffix('.json')
    data_path.write_text(json.dumps(data, indent=2))
    
    cmd = ["python3", str(RENDERERS_DIR / f"{renderer_name}.py"),
           "--input", str(data_path), "--output", output_path]
    if title:
        cmd.extend(["--title", title])
    
    env = os.environ.copy()
    grafiphy_parent = str(GRAFIPHY_DIR.parent)
    env["PYTHONPATH"] = grafiphy_parent + ":" + env.get("PYTHONPATH", "")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    
    if result.returncode != 0:
        raise RuntimeError(f"Renderer {renderer_name} failed: {result.stderr}")
    
    data_path.unlink()


def _run_mermaid_renderer(mermaid_spec: str, output_path: str, title: str = None):
    """Run mermaid renderer with spec string."""
    spec_path = Path(output_path).with_suffix('.mmd')
    spec_path.write_text(mermaid_spec)
    
    cmd = ["python3", str(RENDERERS_DIR / "mermaid.py"),
           "--input", str(spec_path), "--output", output_path]
    if title:
        cmd.extend(["--title", title])
    
    env = os.environ.copy()
    grafiphy_parent = str(GRAFIPHY_DIR.parent)
    env["PYTHONPATH"] = grafiphy_parent + ":" + env.get("PYTHONPATH", "")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    
    if result.returncode != 0:
        raise RuntimeError(f"Mermaid renderer failed: {result.stderr}")
    
    spec_path.unlink()


def _upload_to_release(owner: str, repo: str, pr_number: int, file_path: str) -> Optional[str]:
    """Upload a file to GitHub release assets."""
    try:
        # Check if grafiphy-assets release exists
        result = subprocess.run(
            ["gh", "release", "view", "grafiphy-assets", "--repo", f"{owner}/{repo}", "--json", "url"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Create the release
            subprocess.run(
                ["gh", "release", "create", "grafiphy-assets",
                 "--repo", f"{owner}/{repo}",
                 "--title", "Grafiphy Assets",
                 "--notes", "Auto-generated visual evidence for PR reviews"],
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
