#!/usr/bin/env python3
"""
grafiphy/renderers/mermaid.py — Mermaid diagram renderer for code relationship sequences.

Shows: Actual code flow between functions/modules using graphify query results.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


def render_mermaid(graph_data: dict, output_path: str, title: str = None) -> str:
    """
    Render Mermaid diagram from graphify graph data.
    
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
    
    # Build Mermaid flowchart showing real code relationships
    lines = ["flowchart TD"]
    
    # Add subgraphs for communities
    for i, (community, members) in enumerate(list(communities.items())[:4]):
        safe_name = community.replace("/", "_").replace(" ", "_").replace("-", "_")
        lines.append(f"    subgraph {safe_name}[{community}]")
        for member in members[:5]:
            safe_member = member.replace("(", "_").replace(")", "_").replace(".", "_")
            lines.append(f"        {safe_member}[{member}]")
        lines.append("    end")
    
    # Add god nodes as styled nodes
    for god in god_nodes[:3]:
        safe_name = god["name"].replace("(", "_").replace(")", "_")
        lines.append(f"    {safe_name}[{god['name']}]")
        lines.append(f"    style {safe_name} fill:#fbbf24,stroke:#fbbf24,color:#000")
    
    # Add connections between communities
    comm_list = list(communities.keys())
    for i in range(len(comm_list) - 1):
        safe_src = comm_list[i].replace("/", "_").replace(" ", "_").replace("-", "_")
        safe_dst = comm_list[i + 1].replace("/", "_").replace(" ", "_").replace("-", "_")
        lines.append(f"    {safe_src} --> {safe_dst}")
    
    mermaid_spec = "\n".join(lines)
    
    # Create HTML with Mermaid.js
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{
            background: transparent;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .mermaid {{
            background: transparent;
        }}
        .title {{
            position: absolute;
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
            color: #020617;
            font-family: monospace;
            font-size: 14px;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    {f'<div class="title">{title}</div>' if title else ''}
    <div class="mermaid">
{mermaid_spec}
    </div>
    <script>
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'default',
            securityLevel: 'loose',
            flowchart: {{ useMaxWidth: true, htmlLabels: true, curve: 'basis' }},
            themeVariables: {{
                primaryTextColor: '#000',
                background: '#ffffff',
                mainBkg: '#ffffff'
            }}
        }});
    </script>
</body>
</html>"""
    
    # Write temp HTML
    temp_html = Path(output_path).with_suffix('.html')
    temp_html.write_text(html)
    
    # Screenshot with Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto(f"file://{temp_html.absolute()}")
        page.wait_for_timeout(2000)
        page.screenshot(path=output_path, omit_background=True)
        browser.close()
    
    # Cleanup temp HTML
    temp_html.unlink()
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render Mermaid diagram from graphify data")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file")
    parser.add_argument("--output", "-o", required=True, help="Output PNG path")
    parser.add_argument("--title", "-t", help="Diagram title")
    args = parser.parse_args()
    
    graph_data = json.loads(Path(args.input).read_text())
    result = render_mermaid(graph_data, args.output, args.title)
    print(f"OK: {result}")


if __name__ == "__main__":
    main()
