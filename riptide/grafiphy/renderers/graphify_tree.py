#!/usr/bin/env python3
"""
grafiphy/renderers/graphify_tree.py — Graphify tree screenshot renderer.

Input: Path to graphify-out/ directory
Output: PNG screenshot of interactive tree (full-page)

Usage:
    python -m grafiphy.renderers.graphify_tree --input graphify-out/ --output tree.png
    python -m grafiphy.renderers.graphify_tree --input graphify-out/ --output tree.png --title "Dependency Tree"
"""
import argparse
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


def render_graphify_tree(graphify_out_path: str, output_path: str, title: str = None) -> str:
    """
    Render the graphify tree interactive HTML to PNG.
    
    Args:
        graphify_out_path: Path to graphify-out/ directory
        output_path: Path to save PNG
        title: Optional title
    
    Returns:
        Path to saved PNG
    """
    graphify_out = Path(graphify_out_path)
    html_file = graphify_out / "graph.html"
    
    if not html_file.exists():
        raise FileNotFoundError(f"graph.html not found in {graphify_out}")
    
    # Screenshot with Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"file://{html_file.absolute()}")
        page.wait_for_timeout(3000)  # Wait for D3 to render
        
        # Get full page dimensions
        dimensions = page.evaluate("""
            () => ({
                width: document.documentElement.scrollWidth,
                height: document.documentElement.scrollHeight
            })
        """)
        
        # Resize to full page
        page.set_viewport_size({
            "width": min(dimensions["width"], 4000),
            "height": min(dimensions["height"], 4000)
        })
        page.wait_for_timeout(1000)
        
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render graphify tree to PNG")
    parser.add_argument("--input", "-i", required=True, help="Path to graphify-out/ directory")
    parser.add_argument("--output", "-o", required=True, help="Output PNG path")
    parser.add_argument("--title", "-t", help="Diagram title")
    args = parser.parse_args()
    
    result = render_graphify_tree(args.input, args.output, args.title)
    print(f"OK: {result}")


if __name__ == "__main__":
    main()
