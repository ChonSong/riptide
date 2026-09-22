#!/usr/bin/env python3
"""Bot 3 capture-target resolution and the login-gate guard.

Bot 3 used to fall back to `http://localhost:8788` in three places, so any repo
with a UI change silently captured whatever sat on the developer's dev port -- a
login page, or another app -- and posted it as that PR's evidence. These tests pin
the two replacements: a target must be declared, and the captured page must be the
app rather than a gate.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from riptide.proofshotter import (
    CaptureTargetError,
    _assert_capture_is_app_shell,
    _resolve_capture_target,
    handle_manual_command,
)


class _Page:
    """Minimal stand-in for a Playwright page."""

    def __init__(self, url="http://localhost:8790/", login_selector=None):
        self.url = url
        self._login_selector = login_selector

    def query_selector(self, selector):
        return object() if selector == self._login_selector else None


class TestResolveCaptureTarget:
    def test_config_url_wins_over_env(self):
        with patch.dict(os.environ, {"RIPTIDE_PROOFSHOT_URL": "http://env:1/"}):
            assert _resolve_capture_target({"url": "http://config:2/"}) == "http://config:2/"

    def test_env_used_when_config_declares_no_url(self):
        with patch.dict(os.environ, {"RIPTIDE_PROOFSHOT_URL": "http://env:1/"}):
            assert _resolve_capture_target({"captures": []}) == "http://env:1/"

    def test_no_target_when_nothing_is_declared(self):
        """The old code returned localhost:8788 here and captured the wrong app."""
        with patch.dict(os.environ, {}, clear=True):
            assert _resolve_capture_target({}) is None
            assert _resolve_capture_target(None) is None


class TestAppShellGuard:
    def test_login_form_is_refused(self):
        page = _Page(login_selector="input[type=password]")
        with pytest.raises(CaptureTargetError) as exc:
            _assert_capture_is_app_shell(page, "http://localhost:8790/")
        assert "login form" in str(exc.value)

    def test_app_shell_passes(self):
        _assert_capture_is_app_shell(_Page(), "http://localhost:8790/")

    def test_cross_origin_redirect_is_refused(self):
        page = _Page(url="http://localhost:8788/")
        with pytest.raises(CaptureTargetError) as exc:
            _assert_capture_is_app_shell(page, "http://localhost:8790/")
        assert "redirected off" in str(exc.value)


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _fake_gh(*args, **kwargs):
    """Answer the two `gh` calls handle_manual_command makes."""
    cmd = args[0] if args else kwargs.get("args", [])
    if "view" in cmd:
        return _FakeCompleted(
            json.dumps(
                {
                    "headRefOid": "a" * 40,
                    "title": "t",
                    "state": "OPEN",
                    "isDraft": False,
                }
            )
        )
    return _FakeCompleted("src/app.css\n")


class TestManualCommandHonesty:
    def test_undeclared_target_is_refused_not_guessed(self):
        with patch("riptide.proofshotter.subprocess.run", _fake_gh), patch(
            "riptide.proofshotter._checkout_pr", return_value=Path("/tmp/wt")
        ), patch("riptide.proofshotter._check_proofshot_config", return_value=None), patch.dict(
            os.environ, {"RIPTIDE_PROOFSHOT_URL": ""}
        ):
            reply = handle_manual_command(1, "ChonSong", "riptide", 1, "ChonSong")
        assert "No capture target declared" in reply

    def test_failed_comment_post_is_not_reported_as_complete(self):
        with patch("riptide.proofshotter.subprocess.run", _fake_gh), patch(
            "riptide.proofshotter._checkout_pr", return_value=Path("/tmp/wt")
        ), patch(
            "riptide.proofshotter._check_proofshot_config",
            return_value={"url": "http://localhost:8790/"},
        ), patch(
            "riptide.proofshotter._run_proofshot",
            return_value={"gif": "/tmp/p.gif", "screenshots": []},
        ), patch(
            "riptide.proofshotter._upload_gif", return_value="http://img/x.gif"
        ), patch(
            "riptide.proofshotter._post_proofshot_comment", return_value=False
        ), patch(
            "riptide.proofshotter.mark_visualized"
        ):
            reply = handle_manual_command(1, "ChonSong", "riptide", 1, "ChonSong")
        assert "posting the evidence comment failed" in reply
        assert "ProofShot complete" not in reply
