#!/usr/bin/env python3
"""
labeler.py — Riptide labeling engine.

Deterministic label classification from PR/issue metadata using the canonical
label taxonomy in resources/label-definitions.json.

Usage:
    from riptide.labeler import Labeler
    engine = Labeler()
    labels = engine.classify_pr(pr_details, files_changed)
"""
import json
import re
import os
import logging
import requests as http_requests
from pathlib import Path
from typing import Optional

log = logging.getLogger("riptide.labeler")

# Canonical taxonomy path
LABEL_DEFINITIONS_PATH = Path(__file__).parent / "resources" / "label-definitions.json"


class LLMVoter:
    """Fallback LLM for ambiguous classifications."""

    def __init__(self, endpoint: str = "http://localhost:43311/api/generate",
                 model: str = "qwen2.5-coder:7b"):
        self.endpoint = endpoint
        self.model = model

    def classify(self, title: str, body: str, files: list, labels_available: list) -> list[str]:
        """Ask LLM to classify. Returns list of label names."""
        prompt = f"""Classify this GitHub PR/issue. Return ONLY a JSON array of label names from the available set.

Available labels: {', '.join(labels_available)}

Title: {title}
Body: {body or '(none)'}
Files changed: {', '.join(files[:20])}

Response format: ["type/bug", "priority/high"]
"""
        try:
            resp = http_requests.post(
                self.endpoint,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get("response", "")
            # Extract JSON array from response
            match = re.search(r'\[.*?\]', text)
            if match:
                return json.loads(match.group(0))
        except Exception as e:
            log.warning(f"LLM fallback failed: {e}")
        return []


class Labeler:
    """
    Deterministic label classifier.

    Loads canonical taxonomy and applies rules in order.
    First match wins per dimension (except area which can have multiple).
    """

    def __init__(self, definitions_path: Optional[Path] = None):
        self.definitions_path = definitions_path or LABEL_DEFINITIONS_PATH
        self.definitions = self._load_definitions()
        self._llm = None

    def _load_definitions(self) -> dict:
        """Load label definitions JSON."""
        with open(self.definitions_path) as f:
            return json.load(f)

    @property
    def llm(self) -> Optional[LLMVoter]:
        """Lazy-load LLM voter."""
        if self._llm is None:
            ai_config = self.definitions.get("ai_fallback_config", {})
            self._llm = LLMVoter(
                endpoint=ai_config.get("endpoint", "http://localhost:43311/api/generate"),
                model=ai_config.get("model", "qwen2.5-coder:7b"),
            )
        return self._llm

    def _match_pattern(self, pattern: str, text: str) -> bool:
        """Match a regex pattern against text (case-insensitive)."""
        return bool(re.search(pattern, text, re.IGNORECASE))

    def _match_paths(self, paths: list[str], files: list[str]) -> bool:
        """Check if any file matches any glob pattern."""
        from fnmatch import fnmatch
        for f in files:
            for pattern in paths:
                if fnmatch(f, pattern):
                    return True
        return False

    def _evaluate_condition(self, condition: str, context: dict) -> bool:
        """Evaluate a simple condition expression."""
        if not condition:
            return True
        loc = context.get("loc", 0)
        if "loc" in condition:
            try:
                return eval(condition, {"loc": loc, "__builtins__": {}})
            except:
                return False
        if condition == "no_repro_steps":
            return not context.get("has_repro_steps", False)
        if condition == "touches_core_path":
            return context.get("touches_core_path", False)
        if condition == "confidence < 0.7":
            return context.get("confidence", 1.0) < 0.7
        if condition == "pr.draft == true":
            return context.get("is_draft", False)
        if condition == "inactive_days >= 30":
            return context.get("inactive_days", 0) >= 30
        return False

    def classify_pr(self, pr_details: dict, files: list[dict], repo: str = "") -> list[str]:
        """
        Classify a PR and return list of label names.

        Args:
            pr_details: PR metadata dict (title, body, draft, etc.)
            files: List of file dicts from GitHub API (with 'filename' key)
            repo: Optional repo full_name for comp/* classification

        Returns:
            List of label names to apply
        """
        title = pr_details.get("title", "")
        body = pr_details.get("body", "") or ""
        is_draft = pr_details.get("draft", False)
        total_loc = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files)
        filenames = [f.get("filename", "") for f in files]

        # Check for reproduction steps in body
        has_repro_steps = bool(re.search(
            r"steps\s+to\s+reproduce|reproduction\s+steps|reproduce\s+the\s+bug",
            body, re.IGNORECASE
        ))

        # Determine if touching core paths (heuristic: files in src/, main module, etc.)
        core_patterns = ("server.py", "webhook.py", "orchestrator.py", "deepthink.py", "companion.py")
        touches_core = any(
            any(p in fn for p in core_patterns)
            for fn in filenames
        )

        context = {
            "loc": total_loc,
            "has_repro_steps": has_repro_steps,
            "touches_core_path": touches_core,
            "is_draft": is_draft,
            "confidence": 1.0,
            "inactive_days": 0,
        }

        labels = []
        rules = self.definitions.get("classification_rules", {})

        # ── Type classification (first match wins) ────────────────────────
        type_matched = False
        for rule in rules.get("type", []):
            if self._match_pattern(rule["pattern"], title + " " + body):
                # Check path constraint if present
                if "paths" in rule and not self._match_paths(rule["paths"], filenames):
                    continue
                labels.append(rule["label"])
                type_matched = True
                break

        if not type_matched:
            # All-files-same-type heuristic (only if files present)
            if filenames:
                all_docs = all(
                    fn.endswith((".md", ".rst")) or "docs/" in fn for fn in filenames
                )
                all_test = all(
                    "test" in fn
                    or fn.endswith(("_test.py", ".test.js", "_spec.ts"))
                    or "_spec." in fn
                    for fn in filenames
                )
                if all_docs:
                    labels.append("type/docs")
                    type_matched = True
                elif all_test:
                    labels.append("type/test")
                    type_matched = True

            if not type_matched:
                type_matched = self._apply_title_heuristics(title, labels)

        if not type_matched:
            # Needs triage for unknown types
            labels.append("status/needs-triage")
            context["confidence"] = 0.5

        # Apply security label if detected (can coexist with type/bug etc.)
        if self._match_pattern(r"\b(security|vuln|cve|xss|injection|exploit)\b", title + " " + body):
            if "type/security" not in labels:
                labels.append("type/security")

        # ── Priority ───────────────────────────────────────────────────────
        for rule in rules.get("priority", []):
            if "pattern" in rule and not self._match_pattern(rule["pattern"], title + " " + body):
                continue
            if "condition" in rule and not self._evaluate_condition(rule["condition"], context):
                continue
            labels.append(rule["label"])
            break

        # ── Scope (deterministic from LOC) ─────────────────────────────────
        for rule in rules.get("scope", []):
            if self._evaluate_condition(rule["condition"], context):
                labels.append(rule["label"])
                break

        # ── Status ─────────────────────────────────────────────────────────
        if is_draft:
            labels.append("status/draft")
        if context["confidence"] < 0.7:
            labels.append("status/needs-triage")
        if "type/bug" in labels and not has_repro_steps:
            labels.append("status/needs-repro")
        if re.search(r"\b(blocked on|waiting for|depends on)\b", body, re.IGNORECASE):
            labels.append("status/blocked")

        # ── Area (can have multiple) ───────────────────────────────────────
        for rule in rules.get("area", []):
            if "pattern" in rule and self._match_pattern(rule["pattern"], title + " " + body):
                labels.append(rule["label"])

        # ── Sweeper (blast radius) ─────────────────────────────────────────
        for rule in rules.get("sweeper", []):
            if "pattern" in rule and not self._match_pattern(rule["pattern"], title + " " + body):
                continue
            if "condition" in rule and not self._evaluate_condition(rule["condition"], context):
                continue
            labels.append(rule["label"])
            break

        # ── Component (per-repo path matching) ─────────────────────────────
        if repo:
            repo_components = (
                self.definitions.get("repo_components", {})
                .get("repos", {})
                .get(repo, {})
            )
            for comp_label, comp_info in repo_components.items():
                if self._match_paths(comp_info.get("paths", []), filenames):
                    labels.append(comp_label)

        # ── Bot marker ─────────────────────────────────────────────────────
        labels.append("bot/labeled")

        return list(set(labels))  # dedupe

    def _apply_title_heuristics(self, title: str, labels: list[str]) -> bool:
        """Apply title-based heuristics when no rule matched. Returns True if matched."""
        if title.startswith("fix:") or title.startswith("fix("):
            labels.append("type/bug")
            return True
        if title.startswith("feat:") or title.startswith("feat("):
            labels.append("type/feature")
            return True
        return False

    def classify_issue(self, issue_details: dict, repo: str = "") -> list[str]:
        """Classify an issue (no files, just title+body)."""
        return self.classify_pr(issue_details, [], repo)

    def setup_labels_on_repo(self, installation_id: int, owner: str, repo: str,
                              github_client) -> bool:
        """
        Ensure all canonical labels exist on the repo.
        Creates missing labels. Does NOT delete existing ones (safe to re-run).
        """
        full_name = f"{owner}/{repo}"
        dimensions = self.definitions.get("shared_labels", {}).get("dimensions", {})

        # Collect all labels for this repo
        all_labels = {}
        for dim in dimensions.values():
            for name, info in dim.get("labels", {}).items():
                all_labels[name] = info

        # Add comp/* for this repo
        repo_components = (
            self.definitions.get("repo_components", {})
            .get("repos", {})
            .get(full_name, {})
        )
        for name, info in repo_components.items():
            all_labels[name] = info

        # Create labels
        for name, info in all_labels.items():
            try:
                github_client.ensure_label(
                    installation_id, owner, repo, name,
                    info["description"], info["color"]
                )
            except Exception as e:
                log.warning(f"Failed to create label {name}: {e}")

        return True
