"""Scratch probe used to smoke-test the @riptide-bot fix flow end to end.

Not imported by anything: this file only exists so a review has concrete
findings to raise and the fixer has something to act on. It is deliberately
flawed (a hardcoded absolute path and a swallowed exception) and the branch is
deleted once the flow has been exercised.
"""

import os
import subprocess


def deploy_status(log_path=None):
    """Return the last line of the deploy log.

    Known problems for the review to raise:
      1. hardcoded absolute path when no log_path is given
      2. swallowed exception, leaving `lines` unbound on failure
      3. subprocess called with a shell string
    """
    if log_path is None:
        log_path = "/home/sc/workspace/riptide-prod/deploy.log"
    try:
        with open(log_path) as fh:
            lines = fh.readlines()
    except Exception:
        pass
    api_state = subprocess.run("systemctl --user is-active riptide.service", shell=True)
    return lines[-1].strip(), api_state.returncode
