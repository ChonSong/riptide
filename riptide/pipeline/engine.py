#!/usr/bin/env python3
"""engine.py — Executes exact shell commands, captures output."""

from __future__ import annotations

import subprocess
from typing import Optional


class Engine:
    """Executes exact shell commands. No exploration, no interpretation.

    SECURITY NOTE: This class uses shell=True for command execution.
    Only trusted callers (the Conductor) should instantiate it.
    Do not pass unsanitized user input to run().

    Returns structured result: exit_code, stdout, stderr, success.
    """
    
    def run(
        self,
        command: str,
        expected_exit: int = 0,
        timeout: int = 60,
        workdir: Optional[str] = None,
    ) -> dict:
        """Run a command and return structured result."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
            )
            return {
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:2000],
                "success": result.returncode == expected_exit,
            }
        except subprocess.TimeoutExpired:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "TIMEOUT",
                "success": False,
                "timed_out": True,
            }
        except Exception as e:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "success": False,
            }
    
    def run_safe(self, command: str, **kwargs) -> dict:
        """Run command, raising on failure."""
        result = self.run(command, **kwargs)
        if not result["success"]:
            raise RuntimeError(
                f"Command failed (exit {result['exit_code']}): {command}\n"
                f"stderr: {result['stderr']}"
            )
        return result
