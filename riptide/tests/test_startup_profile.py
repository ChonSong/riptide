"""Regression test: start.sh must export HERMES_PROFILE=riptide.

The riptide systemd unit (and any dev launcher) runs `server.py` via start.sh.
Without an `HERMES_PROFILE` default, `hermes cron create` calls inside the
running server inherit the *default* profile (~2.6KB SOUL). With the default
in place, cron jobs spawned via `riptide.deepthink._spawn_deepthink` are
created under the lean riptide profile (~667B SOUL) — saving ~1.8k input
tokens per review run.

The export is `${HERMES_PROFILE:-riptide}` so an operator who deliberately
sets HERMES_PROFILE elsewhere (e.g. in the systemd drop-in or .env) keeps
their override.

Verified at https://github.com/ChonSong/riptide/blob/main/riptide/deepthink.py
that `_spawn_deepthink` shells out to `hermes cron create`, and at
`hermes_cli/profiles.py:get_active_profile_name()` that the active profile is
resolved from `HERMES_PROFILE_NAME`/`HERMES_PROFILE` env vars before falling
back to the persisted `active_profile` file.
"""

from pathlib import Path


def _start_sh_path() -> Path:
    # tests/ lives at <repo>/riptide/tests/. start.sh is at <repo>/start.sh.
    return Path(__file__).resolve().parents[2] / "start.sh"


class TestStartupProfile:
    """Pin HERMES_PROFILE=riptide in start.sh so cron-spawn inherits the lean SOUL."""

    def test_start_sh_exists(self):
        assert _start_sh_path().is_file(), "start.sh missing at repo root"

    def test_start_sh_exports_riptide_profile(self):
        text = _start_sh_path().read_text()
        assert "export HERMES_PROFILE=\"${HERMES_PROFILE:-riptide}\"" in text, (
            "start.sh must export HERMES_PROFILE with `riptide` as the default, "
            "honoring any operator-supplied override via `${VAR:-default}`."
        )

    def test_export_precedes_exec(self):
        """The export must happen BEFORE the python3 server.py exec line."""
        text = _start_sh_path().read_text()
        export_idx = text.find("export HERMES_PROFILE=\"${HERMES_PROFILE:-riptide}\"")
        exec_idx = text.find("server.py --prod")
        assert export_idx != -1, "start.sh missing `export HERMES_PROFILE=...riptide`"
        assert exec_idx != -1, "start.sh missing the server exec line"
        assert export_idx < exec_idx, (
            "HERMES_PROFILE=riptide must be exported *before* the server exec "
            "so the python process inherits the env var."
        )

    def test_default_is_riptide(self):
        """The shell-default branch must be `riptide`, not something else."""
        text = _start_sh_path().read_text()
        assert "${HERMES_PROFILE:-riptide}" in text, (
            "Shell-default must be `riptide` — that's the lean profile we "
            "want riptide-spawned cron jobs to inherit by default."
        )