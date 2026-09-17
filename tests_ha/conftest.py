"""Real Home Assistant fixtures isolated from the offline unittest processes."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Enable only locally supplied custom integrations."""
