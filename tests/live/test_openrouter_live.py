"""The one real OpenRouter call (opt-in). It costs well under $0.01.

Run it on your own computer, with your key set:
    PowerShell:  $env:OPENROUTER_API_KEY = "sk-or-..."; $env:DW_LIVE_TEST = "1"
                 python -m pytest -m live -s
Or simply:      python -m devils_workshop live-test
"""

import asyncio
import os

import pytest

from devils_workshop.llm.livecheck import live_check
from devils_workshop.llm.openrouter import OpenRouterClient
from devils_workshop.settings import get_api_key, load_settings, settings_dir

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("DW_LIVE_TEST") != "1", reason="set DW_LIVE_TEST=1 to make a real call")
def test_openrouter_live():
    key = get_api_key()
    assert key, "No API key: set OPENROUTER_API_KEY"
    report = asyncio.run(live_check(OpenRouterClient(key), load_settings(), settings_dir() / "logs", key))
    print("\n" + report.text())
    assert report.ok, report.text()
