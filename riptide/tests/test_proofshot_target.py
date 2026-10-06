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


class _Element:
    """Stand-in for a Playwright element handle."""

    def __init__(self, visible):
        self._visible = visible

    def is_visible(self):
        return self._visible


class _Page:
    """Minimal stand-in for a Playwright page."""

    def __init__(self, url="http://localhost:8790/", login_selector=None, login_visible=True):
        self.url = url
        self._login_selector = login_selector
        self._login_visible = login_visible

    def query_selector(self, selector):
        if selector == self._login_selector:
            return _Element(self._login_visible)
        return None


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

    def test_hidden_password_field_is_not_a_gate(self):
        """The app shell carries HIDDEN password fields in its settings forms.

        Measured on a live :8790 instance (title "Hermes", HTTP 200): two
        `input[type=password]` elements, neither rendered. Presence alone refused
        that capture, which would have made Bot 3 silently refuse every capture.
        """
        page = _Page(login_selector="input[type=password]", login_visible=False)
        _assert_capture_is_app_shell(page, "http://localhost:8790/")

    def test_visible_password_field_is_a_gate(self):
        """The real gate: one visible `#pw` inside `#login-form`."""
        page = _Page(login_selector="input[type=password]", login_visible=True)
        with pytest.raises(CaptureTargetError) as exc:
            _assert_capture_is_app_shell(page, "http://localhost:8790/")
        assert "login form" in str(exc.value)

    def test_login_redirect_path_is_refused(self):
        """A gate redirects to /login on the SAME host.

        The cross-origin check cannot catch that, so the path is checked too.
        """
        page = _Page(url="http://localhost:8790/login?next=/")
        with pytest.raises(CaptureTargetError) as exc:
            _assert_capture_is_app_shell(page, "http://localhost:8790/")
        assert "/login" in str(exc.value)


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


class TestShippedConfigs:
    def test_this_repo_declares_no_capture_target(self):
        """`ChonSong/riptide` serves no capturable UI, so it declares no target.

        The shipped config used to declare `:8790` — the Hermes WebUI suite's
        instance, a different application whose skip-onboarding boot leaves no login
        gate for the guard to catch. A Riptide PR touching a stylesheet would have
        posted that app's shell as its evidence. Declaring nothing makes Bot 3 skip
        the PR instead of capturing a stranger's app.
        """
        shipped = Path(__file__).resolve().parents[2] / "proofshot.config.json"
        config = json.loads(shipped.read_text(encoding="utf-8"))

        assert "url" not in config, "this repo must not declare a capture target"
        assert isinstance(config.get("captures", []), list)

    def test_no_shipped_config_documents_the_removed_default(self):
        """Nothing in the repo may still teach `localhost:8788`."""
        root = Path(__file__).resolve().parents[2]

        for name in ("proofshot.config.json", "proofshot.config.example.json"):
            text = (root / name).read_text(encoding="utf-8")
            assert "8788" not in text, f"{name} still documents the removed default"

    def test_the_schema_file_still_documents_the_fields(self):
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "proofshot.config.example.json").read_text(encoding="utf-8"))

        assert set(schema["fields"]) == {"url", "seed", "captures"}
        assert schema["fields"]["url"]["required"] is True
        assert "default" not in schema["fields"]["url"]
