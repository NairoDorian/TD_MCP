"""Tests for td_mcp.compat (version compat + error cache)."""

from td_mcp.compat import ErrorCache, check_compat, parse_version


def test_parse_version():
    assert parse_version("2023.10000") == (2023, 10000, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_major_mismatch_is_error():
    r = check_compat("2023.10000", "2024.10000")
    assert r["level"] == "error"
    assert r["compatible"] is False


def test_minor_drift_is_warning():
    r = check_compat("2023.10000", "2023.20000")
    assert r["level"] == "warning"
    assert r["compatible"] is True


def test_patch_is_ok():
    # True semver: only the patch differs -> compatible (ok).
    r = check_compat("1.2.3", "1.2.4")
    assert r["level"] == "ok"


def test_td_build_drift_is_warning():
    # TD build numbers have no patch level; a same-year build drift is tolerated.
    r = check_compat("2023.10000", "2023.10001")
    assert r["level"] == "warning"
    assert r["compatible"] is True


def test_error_cache_ttl():
    c = ErrorCache(ttl=0.05)
    c.set("127.0.0.1:9980", "ECONNREFUSED")
    assert c.cached("127.0.0.1:9980") is True
    import time
    time.sleep(0.08)
    assert c.cached("127.0.0.1:9980") is False


def test_error_cache_miss():
    c = ErrorCache()
    assert c.get("nope") is None
