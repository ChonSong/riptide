"""Scratch probe used to smoke-test the @riptide-bot fix flow end to end.

Not imported by anything: this file only exists so a review has concrete
findings to raise and the fixer has something to act on. It was committed with
deliberate defects — a hardcoded absolute log path and a swallowed exception —
which the fixer has since repaired, and the branch is deleted once the flow has
been exercised.
"""

import os
import subprocess


def deploy_status(log_path=None):
    """Return ``(last_line, returncode)`` for the deploy log and the service.

    The log path may be passed explicitly; otherwise it comes from
    ``$RIPTIDE_DEPLOY_LOG``, falling back to
    ``~/workspace/riptide-prod/deploy.log``. A missing or unreadable log is a
    real error, so the underlying ``OSError`` propagates with the offending
    path rather than being swallowed.

    The returned ``returncode`` is the exit status of
    ``systemctl --user is-active riptide.service``: ``0`` means active,
    non-zero means inactive or unknown, and the caller must decide which of
    those is acceptable.
    """
    if log_path is None:
        log_path = os.environ.get(
            "RIPTIDE_DEPLOY_LOG",
            os.path.expanduser("~/workspace/riptide-prod/deploy.log"),
        )
    with open(log_path) as fh:
        lines = fh.readlines()
    api_state = subprocess.run(
        ["systemctl", "--user", "is-active", "riptide.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    return lines[-1].strip(), api_state.returncode
