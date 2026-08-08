#!/usr/bin/env python3
"""artisan.py — Creates/modifies files with exact content."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


class Artisan:
    """Creates and modifies files deterministically.
    
    No exploration — exact file paths, exact content, exact patches.
    """
    
    def create_file(self, path: str, content: str) -> dict:
        """Create a file with exact content."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return {"path": path, "bytes": len(content), "created": True}
    
    def patch_file(self, path: str, old_string: str, new_string: str) -> dict:
        """Replace old_string with new_string in file."""
        with open(path, 'r') as f:
            content = f.read()
        
        if old_string not in content:
            return {"path": path, "patched": False, "error": "old_string not found"}
        
        content = content.replace(old_string, new_string)
        with open(path, 'w') as f:
            f.write(content)
        
        return {"path": path, "patched": True}
    
    def delete_file(self, path: str) -> dict:
        """Delete a file."""
        p = Path(path)
        if p.exists():
            p.unlink()
            return {"path": path, "deleted": True}
        return {"path": path, "deleted": False, "error": "file not found"}
    
    def move_file(self, src: str, dst: str) -> dict:
        """Move/rename a file."""
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(src).rename(dst)
        return {"src": src, "dst": dst, "moved": True}
    
    def create_excalidraw_diagram(
        self,
        pr_data: dict,
        findings: list[dict],
        graphify_data: dict,
        output_path: str = "/tmp/review.excalidraw"
    ) -> dict:
        """Generate an Excalidraw review diagram."""
        from riptide.graphify_ingest.excalidraw_renderer import render_review
        
        render_review(
            pr_data=pr_data,
            findings=findings,
            graph_data=graphify_data,
            output_path=output_path,
        )
        
        return {"path": output_path, "created": Path(output_path).exists()}
