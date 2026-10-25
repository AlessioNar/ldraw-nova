"""docs/plans/done/loop-perf/live_check.py against the real app served by uvicorn, with a mock Anthropic behind it."""
import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest
import uvicorn

import llm_config
import settings
from mock_providers import MockAnthropic, reply

SCRIPT = Path(__file__).resolve().parents[4] / "docs" / "plans" / "loop-perf" / "live_check.py"
if not SCRIPT.is_file():                 # inside the container the tests are not next to the repo root
    pytest.skip("live_check.py is only reachable from a checkout layout", allow_module_level=True)
spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
live_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_check)


@pytest.fixture
def app_url():
    from main import app
    os.chmod(settings.DATA_DIR.parent, 0o755)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    thread.join(10)


def configure(port):
    for old in llm_config.list_entries()[0]:
        llm_config.delete(old["id"])                # earlier tests' agents point at dead mock ports
    return llm_config.create({"model_name": "Mock Claude", "capabilities": {"tools": True, "vision": True},
                              "litellm_params": {"model": "anthropic/claude-sonnet-5", "api_key": "sk-dummy",
                                                 "api_base": f"http://127.0.0.1:{port}"}})


def test_live_check_prints_the_per_step_table(app_url, capsys):
    steps = [reply("", [("toolu_1", "report_progress", {"summary": "check 1"})], usage=dict(input=100, cache_write=18000)),
             reply("", [("toolu_2", "report_progress", {"summary": "check 2"})], usage=dict(input=15, cache_read=18000, cache_write=60)),
             reply("done", usage=dict(input=12, cache_read=18060, cache_write=40))]
    with MockAnthropic(steps) as mock:
        configure(mock.port)
        code = live_check.main(["--model", "Mock Claude", "--base-url", app_url, "--steps", "2", "--timeout", "60"])
    out = capsys.readouterr().out
    assert code == 0, out
    rows = [line.split() for line in out.splitlines() if line.strip()[:1].isdigit()]
    assert rows == [["1", "18100", "5", "0", "18000", "0"],
                    ["2", "18075", "5", "18000", "60", "100"],
                    ["3", "18112", "5", "18060", "40", "100"]]
    assert "Caching works" in out
    assert "exactly 2 separate calls" in mock.bodies()[0]["messages"][0]["content"][0]["text"]


def test_live_check_flags_a_provider_that_never_reads_the_cache(app_url, capsys):
    steps = [reply("", [("toolu_1", "report_progress", {"summary": "check 1"})], usage=dict(input=100, cache_write=500)),
             reply("done", usage=dict(input=600))]
    with MockAnthropic(steps) as mock:
        configure(mock.port)
        code = live_check.main(["--model", "anthropic/claude-sonnet-5", "--base-url", app_url, "--timeout", "60"])
    assert code == 2 and "no reads" in capsys.readouterr().out


def test_live_check_reports_a_failed_turn(app_url, capsys):
    with MockAnthropic([]) as mock:                 # script exhausted: the mock answers 500
        configure(mock.port)
        code = live_check.main(["--model", "anthropic/claude-sonnet-5", "--base-url", app_url, "--timeout", "60"])
    assert code == 1 and "turn failed" in capsys.readouterr().out


def test_unknown_model_lists_what_is_configured(app_url):
    with pytest.raises(SystemExit, match="no configured agent matches"):
        live_check.main(["--model", "nope", "--base-url", app_url])


def test_table_and_verdict_edge_cases():
    assert live_check.verdict([])[1] == 1
    assert live_check.verdict([{"prompt": 5, "completion": 1, "cache_read": 0, "cache_write": 0}])[1] == 2
    assert "-" in live_check.table([{"prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0}]).splitlines()[2]
