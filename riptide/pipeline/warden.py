#!/usr/bin/env python3
"""warden.py — Verifies outputs meet acceptance criteria."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class Warden:
    """Acceptance gate. Checks artifacts against deterministic criteria.
    
    No interpretation — pass/fail with exact reasons.
    """
    
    def check_file_exists(self, path: str) -> dict:
        exists = Path(path).exists()
        return {"check": "file_exists", "path": path, "pass": exists}
    
    def check_file_size(self, path: str, max_bytes: int) -> dict:
        p = Path(path)
        if not p.exists():
            return {"check": "file_size", "path": path, "pass": False, "error": "file not found"}
        size = p.stat().st_size
        return {"check": "file_size", "path": path, "pass": size <= max_bytes, "size": size, "max": max_bytes}
    
    def check_json_valid(self, path: str) -> dict:
        try:
            with open(path) as f:
                json.load(f)
            return {"check": "json_valid", "path": path, "pass": True}
        except Exception as e:
            return {"check": "json_valid", "path": path, "pass": False, "error": str(e)}
    
    def check_json_schema(self, path: str, schema: dict) -> dict:
        """Validate JSON file against schema."""
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            return {"check": "json_schema", "path": path, "pass": False, "error": f"Invalid JSON: {e}"}
        
        errors = []
        required = schema.get("required", [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")
        
        properties = schema.get("properties", {})
        for field, field_schema in properties.items():
            if field in data:
                expected_type = field_schema.get("type")
                if expected_type == "array" and not isinstance(data[field], list):
                    errors.append(f"{field}: expected array, got {type(data[field]).__name__}")
                elif expected_type == "object" and not isinstance(data[field], dict):
                    errors.append(f"{field}: expected object, got {type(data[field]).__name__}")
                elif expected_type == "string" and not isinstance(data[field], str):
                    errors.append(f"{field}: expected string, got {type(data[field]).__name__}")
        
        return {"check": "json_schema", "path": path, "pass": len(errors) == 0, "errors": errors}
    
    def check_syntax(self, path: str) -> dict:
        """Check Python file syntax."""
        import py_compile
        try:
            py_compile.compile(path, doraise=True)
            return {"check": "syntax", "path": path, "pass": True}
        except py_compile.PyCompileError as e:
            return {"check": "syntax", "path": path, "pass": False, "error": str(e)}
    
    def check_contains(self, path: str, text: str) -> dict:
        """Check file contains specific text."""
        try:
            content = Path(path).read_text()
            found = text in content
            return {"check": "contains", "path": path, "text": text, "pass": found}
        except Exception as e:
            return {"check": "contains", "path": path, "pass": False, "error": str(e)}
    
    def verify_all(self, checks: list[dict]) -> dict:
        """Run multiple checks and return overall pass/fail."""
        results = []
        for check in checks:
            method = getattr(self, check.get("method", "check_file_exists"), None)
            if method:
                result = method(**check.get("args", {}))
            else:
                result = {"check": "unknown", "pass": False, "error": f"Unknown check method: {check.get('method')}"}
            results.append(result)
        
        all_pass = all(r.get("pass", False) for r in results)
        return {"pass": all_pass, "checks": results}
