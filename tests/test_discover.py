"""Tests for td_mcp.discover (multi-instance discovery)."""

from td_mcp.discover import discover_instances, pick_instance


def _fake_probe(ok_host, ok_port):
    def probe(host, port, timeout):
        if host == ok_host and port == ok_port:
            return {"name": "project1", "version": "2023.10000"}
        return None
    return probe


def test_discovers_reachable():
    inst = discover_instances(probe=_fake_probe("127.0.0.1", 9980))
    assert len(inst) == 1
    assert inst[0]["port"] == 9980
    assert inst[0]["info"]["name"] == "project1"


def test_no_instances_when_none_reachable():
    inst = discover_instances(probe=lambda h, p, t: None)
    assert inst == []


def test_pick_prefers_port():
    inst = discover_instances(probe=_fake_probe("127.0.0.1", 9988))
    chosen = pick_instance(inst, prefer_port=9988)
    assert chosen["port"] == 9988


def test_pick_none_when_empty():
    assert pick_instance([]) is None
