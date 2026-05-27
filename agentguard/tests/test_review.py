"""Tests for human review tickets."""

import threading
from agentguard.review.tickets import InMemoryApprovalBridge
from agentguard.review.api import ApprovalConsole


def test_enqueue_and_resolve():
    bridge = InMemoryApprovalBridge()
    ticket = bridge.enqueue({"tool": "shell.exec"}, {"action": "human_check"})
    assert ticket.status == "pending"

    assert bridge.resolve(ticket.ticket_id, "approve")
    assert ticket.status == "approved"


def test_pending():
    bridge = InMemoryApprovalBridge()
    bridge.enqueue({"tool": "a"}, {"action": "human_check"})
    bridge.enqueue({"tool": "b"}, {"action": "human_check"})
    assert len(bridge.pending()) == 2


def test_wait_resolves():
    bridge = InMemoryApprovalBridge()
    ticket = bridge.enqueue({"tool": "x"}, {"action": "human_check"})

    def resolver():
        import time
        time.sleep(0.1)
        bridge.resolve(ticket.ticket_id, "deny", "too risky")

    t = threading.Thread(target=resolver)
    t.start()
    result = bridge.wait(ticket.ticket_id, timeout_s=5.0)
    t.join()
    assert result.status == "denied"
    assert result.note == "too risky"


def test_console():
    bridge = InMemoryApprovalBridge()
    console = ApprovalConsole(bridge)
    bridge.enqueue({"tool": "shell.exec"}, {"action": "human_check"})
    pending = console.list_pending()
    assert len(pending) == 1
    console.approve(pending[0]["ticket_id"])
    assert len(console.list_pending()) == 0
