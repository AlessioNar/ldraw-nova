"""Conversation mode: quick changes in between don't end an agent conversation."""
import chat_routing


def test_a_quick_change_mid_conversation_keeps_the_ai_mode():
    asking = {"role": "assistant", "content": "Step 3 helped. Should I go ahead with step 4?"}
    quick = [{"role": "user", "content": "make the roof blue", "_fast_path": {"by": "person"}},
             {"role": "assistant", "content": "[Quick change] roof colour: dark red -> blue", "_fast_path": {"route": "edit"}}]
    assert chat_routing.state([{"role": "user", "content": "build a castle"}, asking])["mode"] == "ai"
    assert chat_routing.state([{"role": "user", "content": "build a castle"}, asking, *quick])["mode"] == "ai"
    assert chat_routing.state(quick)["mode"] == "quick"
