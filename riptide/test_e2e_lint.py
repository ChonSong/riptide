"""Test module with deliberate lint issues for E2E pipeline testing."""

import os  # unused import - should trigger lint
import sys  # unused import - should trigger lint


def test_function():
    """A test function."""
    unused_variable = "this is unused"  # noqa: F841
    return True


class TestClass:
    """A test class."""

    def __init__(self):
        self.value = 42

    def method(self):
        """A method."""
        return self.value
