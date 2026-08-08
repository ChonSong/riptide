#!/usr/bin/env python3
"""diagram_builder.py — Build review diagrams from structured pipeline data.

Unlike excalidraw_renderer.py (which requires full graphify traversal data),
this builder uses the data the pipeline actually has: context_bundle, diff_report,
and findings. Produces clean, readable diagrams without requiring LLM exploration.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


# ── Colors ─────────────────────────────────────────────────────────────────

BG_TITLE = "#1a1a2e"
BG_ZONE = "#f8f9fa"
BG_SECTION = "#e9ecef"
BG_FINDING = "#fff3cd"
BG_SUCCESS = "#d4edda"
BG_INFO = "#d1ecf1"

TEXT_DARK = "#1a1e21"
TEXT_MUTED = "#6c757d"
TEXT_LIGHT = "#f8f9fa"

BORDER = "#dee2e6"
BORDER_DARK = "#495057"

ARROW = "#6c757d"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _escape_excalidraw_text(text: str) -> str:
    """Escape text for Excalidraw JSON."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _rect(eid: str, x: int, y: int, w: int, h: int,
          bg: str = BG_ZONE, border: str = BORDER,
          opacity: int = 100) -> dict:
    return {
        "type": "rectangle",
        "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "backgroundColor": bg,
        "fillStyle": "solid",
        "strokeColor": border,
        "strokeWidth": 1,
        "opacity": opacity,
        "roundness": {"type": 3},
    }


def _text(eid: str, x: int, y: int, w: int, h: int,
           content: str, font_size: int = 12,
           color: str = TEXT_DARK, align: str = "left",
           bold: bool = False) -> dict:
    return {
        "type": "text",
        "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "text": _escape_excalidraw_text(content),
        "fontSize": font_size,
        "fontFamily": 1,
        "strokeColor": color,
        "textAlign": align,
        "verticalAlign": "middle",
        "originalText": content,
        "autoResize": True,
    }


def _arrow(eid: str, x1: int, y1: int, x2: int, y2: int,
           color: str = ARROW, dashed: bool = False) -> dict:
    return {
        "type": "arrow",
        "id": eid,
        "x": x1, "y": y1,
        "width": x2 - x1, "height": y2 - y1,
        "points": [[0, 0], [x2 - x1, y2 - y1]],
        "endArrowhead": "arrow",
        "strokeColor": color,
        "strokeWidth": 2,
        "strokeStyle": "dashed" if dashed else "solid",
    }


# ── Main Builder ────────────────────────────────────────────────────────────


def build_review_diagram(
    pr_data: dict,
    context_bundle: dict,
    findings: list[dict],
    output_path: str = "/tmp/review.excalidraw",
) -> str:
    """Build a review diagram from pipeline data.
    
    Args:
        pr_data: {title, number, repo, author, loc, status}
        context_bundle: output of context_bundle.build_context_bundle()
        findings: list of finding dicts [{severity, title, file, line}]
        output_path: where to save the .excalidraw file
    
    Returns:
        Path to the generated file
    """
    elements = []
    y = 20
    w = 800
    margin = 40
    content_w = w - 2 * margin

    # ── Title ──────────────────────────────────────────────────────────
    elements.append(_text(
        "title", margin, y, content_w, 30,
        pr_data.get("title", f"PR #{pr_data.get('number', '?')}"),
        font_size=20, color=TEXT_LIGHT, bold=True,
    ))
    elements.append(_rect(
        "title_bg", margin - 10, y - 5, content_w + 20, 40,
        bg=BG_TITLE, border=BG_TITLE,
    ))
    # Move title above background
    elements.append(elements.pop(0))  # title was added first, move to end
    
    y += 50

    # ── PR Info ────────────────────────────────────────────────────────
    subtitle = f"#{pr_data.get('number', '?')} · {pr_data.get('repo', 'unknown')} · by {pr_data.get('author', {}).get('login', 'unknown')}"
    elements.append(_text(
        "subtitle", margin, y, content_w, 20,
        subtitle, font_size=11, color=TEXT_MUTED,
    ))
    y += 30

    # ── Verdict Zone ───────────────────────────────────────────────────
    aggregate = context_bundle.get("aggregate", {})
    verdict = context_bundle.get("verdict", "pass")
    verdict_colors = {
        "pass": ("#d4edda", "#155724"),
        "review": ("#fff3cd", "#856404"),
        "block": ("#f8d7da", "#721c24"),
    }
    v_bg, v_text = verdict_colors.get(verdict, (BG_ZONE, TEXT_DARK))
    
    elements.append(_rect(
        "verdict_zone", margin, y, content_w, 60,
        bg=v_bg, border=BORDER,
    ))
    elements.append(_text(
        "verdict_label", margin + 10, y + 5, 100, 20,
        "VERDICT", font_size=10, color=v_text, bold=True,
    ))
    elements.append(_text(
        "verdict_value", margin + 10, y + 25, content_w - 20, 25,
        verdict.upper(), font_size=16, color=v_text, bold=True,
    ))
    
    # Key stats
    total_loc = aggregate.get("total_loc", 0)
    concepts = aggregate.get("concepts", [])
    files_count = aggregate.get("files_count", 0)
    stats_text = f"{files_count} files · {total_loc} LOC · touches: {', '.join(concepts) if concepts else 'none'}"
    elements.append(_text(
        "stats", margin + 10, y + 42, content_w - 20, 15,
        stats_text, font_size=9, color=v_text,
    ))
    
    y += 75

    # ── Files Changed ──────────────────────────────────────────────────
    concepts_list = context_bundle.get("concepts", [])
    if concepts_list:
        elements.append(_text(
            "files_header", margin, y, content_w, 20,
            "FILES CHANGED", font_size=11, color=TEXT_MUTED, bold=True,
        ))
        y += 22
        
        # Show up to 6 files
        for i, concept in enumerate(concepts_list[:6]):
            fname = concept.get("filename", "?")
            additions = concept.get("additions", 0)
            deletions = concept.get("deletions", 0)
            status = concept.get("status", "modified")
            concept_type = concept.get("concept", "core")
            
            elements.append(_rect(
                f"file_{i}", margin + 10, y, content_w - 20, 22,
                bg=BG_ZONE, border=BORDER,
            ))
            elements.append(_text(
                f"file_{i}_text", margin + 15, y + 3, content_w - 30, 16,
                f"{status:10} {fname}  (+{additions}/-{deletions})  [{concept_type}]",
                font_size=9, color=TEXT_DARK,
            ))
            y += 26
        
        if len(concepts_list) > 6:
            elements.append(_text(
                "files_more", margin + 15, y, content_w - 30, 15,
                f"... and {len(concepts_list) - 6} more files",
                font_size=9, color=TEXT_MUTED,
            ))
            y += 20
        
        y += 10

    # ── Findings ───────────────────────────────────────────────────────
    diff_report = context_bundle.get("report")
    diff_findings = []
    if hasattr(diff_report, "findings"):
        diff_findings = [
            {"severity": f.severity, "title": f.message, "file": f.file, "line": f.line_hint}
            for f in diff_report.findings
        ]
    
    all_findings = (findings or []) + diff_findings
    
    elements.append(_text(
        "findings_header", margin, y, content_w, 20,
        f"FINDINGS ({len(all_findings)})", font_size=11, color=TEXT_MUTED, bold=True,
    ))
    y += 22
    
    if all_findings:
        for i, finding in enumerate(all_findings[:5]):
            severity = finding.get("severity", "info")
            title = finding.get("title", finding.get("message", "?"))
            file_ref = finding.get("file", "")
            line_ref = finding.get("line", "")
            
            sev_colors = {
                "critical": ("#f8d7da", "#721c24"),
                "warning": ("#fff3cd", "#856404"),
                "suggestion": ("#e2d9f3", "#5b21b6"),
                "info": ("#d1ecf1", "#0c5460"),
            }
            f_bg, f_text = sev_colors.get(severity, (BG_ZONE, TEXT_DARK))
            
            loc = f"  ({file_ref}:{line_ref})" if file_ref else ""
            display_text = f"{severity.upper()}: {title}{loc}"
            
            elements.append(_rect(
                f"finding_{i}", margin + 10, y, content_w - 20, 22,
                bg=f_bg, border=BORDER,
            ))
            elements.append(_text(
                f"finding_{i}_text", margin + 15, y + 3, content_w - 30, 16,
                display_text, font_size=9, color=f_text,
            ))
            y += 26
    else:
        elements.append(_rect(
            "no_findings", margin + 10, y, content_w - 20, 30,
            bg=BG_SUCCESS, border=BORDER,
        ))
        elements.append(_text(
            "no_findings_text", margin + 15, y + 7, content_w - 30, 16,
            "✓ No issues found — clean PR", font_size=10, color="#155724",
        ))
        y += 35
    
    y += 15

    # ── Pipeline Steps ─────────────────────────────────────────────────
    elements.append(_text(
        "pipeline_header", margin, y, content_w, 20,
        "PIPELINE EXECUTION", font_size=11, color=TEXT_MUTED, bold=True,
    ))
    y += 22
    
    steps = [
        ("Probe", "Context gathered"),
        ("Judge", f"{len(all_findings)} findings"),
        ("Artisan", "Diagram generated"),
        ("Engine", "Diagram uploaded"),
        ("Scribe", "Review posted"),
    ]
    
    for i, (step_name, step_result) in enumerate(steps):
        x = margin + i * 155
        elements.append(_rect(
            f"step_{i}", x, y, 145, 35,
            bg=BG_SECTION, border=BORDER,
        ))
        elements.append(_text(
            f"step_{i}_name", x + 5, y + 3, 135, 14,
            step_name, font_size=9, color=TEXT_DARK, bold=True,
        ))
        elements.append(_text(
            f"step_{i}_result", x + 5, y + 18, 135, 14,
            step_result, font_size=8, color=TEXT_MUTED,
        ))
        
        # Arrow between steps
        if i < len(steps) - 1:
            elements.append(_arrow(
                f"step_arrow_{i}",
                x + 147, y + 17,
                x + 153, y + 17,
            ))
    
    y += 50

    # ── Legend ──────────────────────────────────────────────────────────
    elements.append(_text(
        "legend_title", margin, y, content_w, 15,
        "Legend:", font_size=9, color=TEXT_MUTED, bold=True,
    ))
    y += 16
    
    legend_items = [
        ("#d4edda", "Pass"),
        ("#fff3cd", "Warning"),
        ("#f8d7da", "Critical"),
        ("#e2d9f3", "Suggestion"),
        ("#d1ecf1", "Info"),
    ]
    
    for i, (color, label) in enumerate(legend_items):
        x = margin + i * 90
        elements.append(_rect(
            f"legend_{i}", x, y, 80, 16,
            bg=color, border=BORDER,
        ))
        elements.append(_text(
            f"legend_{i}_text", x + 3, y + 2, 74, 12,
            label, font_size=8, color=TEXT_DARK,
        ))

    # ── Assemble ───────────────────────────────────────────────────────
    data = {
        "type": "excalidraw",
        "version": 2,
        "source": "riptide-pipeline",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff"},
    }
    
    Path(output_path).write_text(json.dumps(data, indent=2))
    return output_path


def upload_diagram(file_path: str) -> Optional[str]:
    """Upload .excalidraw file and return shareable link."""
    upload_script = (
        Path.home()
        / ".hermes" / "hermes-agent" / "skills"
        / "creative" / "excalidraw" / "scripts" / "upload.py"
    )
    
    if upload_script.exists():
        result = subprocess.run(
            ["python3", str(upload_script), str(file_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    
    return None
