import asyncio

import pytest

import browser_auth


def test_switching_from_device_to_browser_cancels_old_login(monkeypatch):
    flows = []
    cancelled = []
    async def login(provider, state):
        flows.append(state["flow"])
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(state["flow"])
            raise
    monkeypatch.setattr(browser_auth, "_login", login)
    monkeypatch.setattr(browser_auth, "_sessions", {})
    monkeypatch.setattr(browser_auth, "_locks", {})
    async def run():
        await browser_auth.start("openai", "device")
        await asyncio.sleep(0)
        # An explicit retry after changing account settings must mint a fresh code.
        await browser_auth.start("openai", "device", restart=True)
        await asyncio.sleep(0)
        assert cancelled == ["device"] and flows == ["device", "device"]
        result = await browser_auth.start("openai")
        await asyncio.sleep(0)
        assert result == {"status": "starting", "flow": "browser"}
        assert cancelled == ["device", "device"] and flows == ["device", "device", "browser"]
        await browser_auth.shutdown()
    asyncio.run(run())


def test_device_flow_remains_explicit_and_does_not_change_claude():
    with pytest.raises(ValueError, match="Unsupported login flow"):
        asyncio.run(browser_auth.start("anthropic", "device"))
