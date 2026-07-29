#!/usr/bin/env python3
"""
grafiphy/orchestrator.py — Entry point for companion diagram generation.

Uses graphify data and delegates output to excalidraw_renderer for polished,
graphify-informed Excalidraw diagrams with connected narrative flow.

Adds:
    - repo_graph: full module list from graphify (codebase landscape)
    - suggestions: inline review suggestions from Bot 2
"""
import json
import os
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Optional

from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw

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


def get_repo_graph(repo_path: str) -> list[dict]:
    """
    Collect ALL modules from graphify for the codebase landscape view.
    Returns list of {name, type, file, why}.
    """
    try:
        stdout, _ = _run_graphify(["list", "--all"], cwd=repo_path, timeout=30)
        if not stdout:
            return []

        modules = []
        # Graphify list format: "name  type  path"
        for line in stdout.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) >= 2:
                name = parts[0]
                rg_type = parts[1] if len(parts) > 1 else "module"
                file_path = parts[2] if len(parts) > 2 else ""
                modules.append({
                    "name": name,
                    "type": rg_type,
                    "file": file_path,
                    "why": f"Part of the {rg_type} layer" if rg_type else "Codebase module",
                })
        return modules
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []


def get_graphify_graph(repo_path: str, diff: list[dict]) -> dict:
    """Build a code relationship graph from graphify data."""
    graph = {
        "nodes": [],
        "communities": {},
        "god_nodes": [],
    }

    stdout, _ = _run_graphify(["god-nodes", "--top", "10"], cwd=repo_path)
    if stdout:
        raw = _parse_god_nodes(stdout)
        graph["god_nodes"] = [
            {"name": g["name"], "edges": g["edges"],
             "why": "Top architectural hub from graphify analysis"}
            for g in raw
        ]

    changed_files = [f["filename"] for f in diff[:5]]
    if changed_files:
        query_terms = []
        for f in changed_files:
            parts = f.split("/")
            name = parts[-1]
            for ext in (".js", ".py", ".md", ".ts", ".css", ".json"):
                name = name.replace(ext, "")
            query_terms.append(name)

        query = f"What are the relationships between {', '.join(query_terms)}?"
        stdout, _ = _run_graphify(["query", query, "--budget", "4000"], cwd=repo_path)
        if stdout:
            query_data = _parse_query(stdout)
            graph["nodes"].extend(query_data["nodes"])
            graph["communities"] = {
                k: [{"name": m, "why": f"Member of {k} community"}
                    for m in v]
                for k, v in query_data["communities"].items()
            }

    return graph


def build_distance_map(repo_path: str, changed_files: list[str]) -> dict:
    """
    Build a distance map from PR changed files using graphify.

    For each changed file:
    - 0 hops: the file itself (epicenter)
    - 1 hop: affected nodes from `graphify affected`
    - 2+ hops: community-based distance from graph.json

    Returns {name: {hops, relation, community, degree}}
    """
    distance_map = {}

    if not changed_files:
        return distance_map

    # Step 1: Epicenter (0 hops) — PR changed files
    for f in changed_files:
        fname = Path(f).name
        distance_map[fname] = {
            "hops": 0,
            "relation": "epicenter",
            "community": "",
            "degree": 0,
        }

    # Step 2: 1 hop — query graphify affected for each changed file
    affected_seen = set()
    for f in changed_files:
        try:
            stdout, _ = _run_graphify(["affected", f], cwd=repo_path, timeout=15)
            if stdout:
                for line in stdout.split("\n"):
                    line = line.strip()
                    if not line or line in distance_map or line in affected_seen:
                        continue
                    affected_seen.add(line)
                    distance_map[line] = {
                        "hops": 1,
                        "relation": f"affected by {Path(f).name}",
                        "community": "",
                        "degree": 0,
                    }
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

    # Step 3: Enrich with community and degree info from graph.json if available
    graph_json = Path(repo_path) / "graphify-out" / "graph.json"
    if graph_json.exists():
        try:
            gdata = json.loads(graph_json.read_text())
            node_lookup = {}
            for node in gdata.get("nodes", []):
                nname = node.get("name", "")
                node_lookup[nname] = node

            # Enrich existing entries
            for name in list(distance_map.keys()):
                node = node_lookup.get(name)
                if node:
                    community = node.get("community", "")
                    degree = len(node.get("incoming", [])) + len(node.get("outgoing", []))
                    distance_map[name]["community"] = community
                    distance_map[name]["degree"] = degree

            # Step 4: Assign 2-hop for same-community nodes not yet mapped
            epicenter_communities = set()
            for name, info in distance_map.items():
                if info["hops"] == 0 and info.get("community"):
                    epicenter_communities.add(info["community"])

            for community in epicenter_communities:
                for node in gdata.get("nodes", []):
                    nname = node.get("name", "")
                    if nname not in distance_map and node.get("community") == community:
                        degree = len(node.get("incoming", [])) + len(node.get("outgoing", []))
                        distance_map[nname] = {
                            "hops": 2,
                            "relation": f"community: {community}",
                            "community": community,
                            "degree": degree,
                        }

            # Step 5: Cross-community edges for 3-hop (adjacent communities)
            community_nodes = {}
            for node in gdata.get("nodes", []):
                c = node.get("community", "")
                if c:
                    community_nodes.setdefault(c, []).append(node.get("name", ""))

            for edge in gdata.get("edges", []):
                src_name = edge.get("source", "")
                tgt_name = edge.get("target", "")
                src_node = node_lookup.get(src_name, {})
                tgt_node = node_lookup.get(tgt_name, {})
                src_comm = src_node.get("community", "")
                tgt_comm = tgt_node.get("community", "")

                # If an edge connects an epicenter community to another
                if src_comm in epicenter_communities and tgt_comm not in epicenter_communities:
                    for nn in community_nodes.get(tgt_comm, []):
                        if nn not in distance_map:
                            distance_map[nn] = {
                                "hops": 3,
                                "relation": f"adjacent via {src_comm}",
                                "community": tgt_comm,
                                "degree": 0,
                            }

        except (json.JSONDecodeError, OSError, Exception):
            pass

    # Limit size to prevent diagram overload
    max_nodes = 30
    if len(distance_map) > max_nodes:
        # Keep epicenter (0), then nearest hops, truncate by hops then by name
        hop0 = {k: v for k, v in distance_map.items() if v["hops"] == 0}
        remainder = {k: v for k, v in distance_map.items() if v["hops"] != 0}
        sorted_remainder = sorted(remainder.items(), key=lambda x: (x[1]["hops"], x[0]))
        truncated = dict(sorted_remainder[:max_nodes - len(hop0)])
        distance_map = {**hop0, **truncated}

    return distance_map


def orchestrate(pr_metadata: dict, diff: list[dict],
                graphify_context: dict = None,
                suggestions: list[dict] = None) -> list[str]:
    """
    Generate a graphify-informed Excalidraw diagram for a PR.

    Args:
        pr_metadata: {owner, repo, number, title, author, installation_id}
        diff: List of file dicts from GitHub API [{filename, additions, deletions, status}]
        graphify_context: Optional pre-computed graph data
        suggestions: [{file, line, old_code, new_code, severity, reasoning}] from Bot 2

    Returns:
        List with one Excalidraw URL (uploaded to excalidraw.com)
    """
    owner = pr_metadata.get("owner")
    repo = pr_metadata.get("repo")
    pr_number = pr_metadata.get("number")
    title = pr_metadata.get("title", "")
    author = pr_metadata.get("author", "")

    total_loc = sum(f.get("additions", 0) + f.get("deletions", 0) for f in diff)

    repo_path = os.environ.get("GRAPHIFY_CWD",
                               f"/home/sc/workspace/{repo or 'hermes-webui-extensions'}")

    output_dir = Path(tempfile.mkdtemp(prefix=f"grafiphy-pr{pr_number}-"))

    # Get graphify graph (use pre-computed context if available)
    try:
        if graphify_context:
            graph = graphify_context
        else:
            graph = get_graphify_graph(repo_path, diff)
    except Exception as e:
        print(f"WARNING: Graphify graph failed: {e}")
        graph = {"nodes": [], "communities": {}, "god_nodes": []}

    # Get full repo graph for codebase landscape view
    repo_graph = get_repo_graph(repo_path)

    # Build file tree string
    file_tree_lines = []
    changed_files_set = set()
    for f in diff:
        fn = f.get("filename", "?")
        add = f.get("additions", 0)
        del_ = f.get("deletions", 0)
        status = f.get("status", "modified")
        if status == "added":
            file_tree_lines.append(f"  + {fn} ({add} LOC)")
        elif status == "removed":
            file_tree_lines.append(f"  - {fn} ({del_} LOC)")
        else:
            file_tree_lines.append(f"  M {fn} (+{add}/-{del_})")
        changed_files_set.add(fn)
    file_tree = "\n".join(file_tree_lines) if file_tree_lines else None

    # Build flow steps from companion pipeline
    flow_steps = None
    if diff:
        flow_steps = [
            ("Webhook", "receives PR event", "#a5d8ff"),
            ("Graphify", "code relationship analysis", "#d0bfff"),
            ("Labels", f"{len(diff)} files mapped", "#b2f2bb"),
            ("Excalidraw", "review diagram", "#fff3bf"),
        ]

    # Build god_nodes list from graph data
    god_nodes = graph.get("god_nodes", [])
    communities_list = []
    for name, members in graph.get("communities", {}).items():
        if isinstance(members, list):
            member_names = [m["name"] if isinstance(m, dict) else m for m in members]
            communities_list.append({
                "name": name,
                "members": member_names,
                "why": f"Code community: {len(member_names)} related files",
            })

    graph_data = {
        "god_nodes": god_nodes[:8],
        "communities": communities_list[:6],
    }

    # Auto-generate human-readable narrative
    narr_parts = []
    if title:
        narr_parts.append(f"PR: {title}")
    if total_loc:
        narr_parts.append(f"Changes: {total_loc} LOC in {len(diff)} files.")
    if communities_list:
        narr_parts.append(
            f"Graphify found {len(communities_list)} code communities "
            f"and {len(god_nodes)} architectural hubs."
        )
    human_narrative = " ".join(narr_parts) if narr_parts else None

    # Build distance map from graphify
    changed_file_names = [f.get("filename", "") for f in diff if f.get("filename")]
    distance_map = {}
    try:
        distance_map = build_distance_map(repo_path, changed_file_names)
    except Exception as e:
        print(f"WARNING: Distance map failed: {e}")

    # Generate diagram
    excalidraw_urls = []
    try:
        excalidraw_path = output_dir / f"pr{pr_number}-diagram.excalidraw"
        render_review(
            pr_data={
                "number": pr_number,
                "title": title or f"PR #{pr_number}",
                "repo": f"{owner}/{repo}" if owner and repo else "",
                "author": author,
                "loc": total_loc,
            },
            graph_data=graph_data,
            flow_steps=flow_steps,
            file_tree=file_tree,
            repo_graph=repo_graph if repo_graph else None,
            suggestions=suggestions or None,
            distance_map=distance_map or None,
            human_narrative=human_narrative,
            output_path=str(excalidraw_path),
        )

        # Upload to excalidraw.com for shareable link
        url = upload_excalidraw(str(excalidraw_path))
        if url:
            excalidraw_urls.append(url)
    except Exception as e:
        print(f"WARNING: Excalidraw generation failed: {e}")

    return excalidraw_urls
