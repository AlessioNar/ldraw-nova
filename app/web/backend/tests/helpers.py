"""Shared fakes for tests that stand in for litellm.acompletion."""
from types import SimpleNamespace


def ok_reply():
    """A non-streaming completion whose text is "OK"."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
