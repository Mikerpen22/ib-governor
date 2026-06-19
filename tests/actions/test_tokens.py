# tests/actions/test_tokens.py
import datetime as dt

from governor.actions.tokens import ConfirmTokenGate

T0 = dt.datetime(2026, 6, 17, 11, 0, tzinfo=dt.timezone.utc)


def _gate():
    # deterministic token factory for tests (no randomness in the pure core)
    seq = iter(["AAAA", "BBBB", "CCCC"])
    return ConfirmTokenGate(ttl_seconds=300, token_factory=lambda: next(seq))


def test_issue_then_verify_consumes_single_use():
    g = _gate()
    tok = g.issue(payload="lockout", now=T0)
    assert tok == "AAAA"
    p = g.verify("confirm AAAA", now=T0 + dt.timedelta(seconds=10))
    assert p is not None and p.payload == "lockout"
    # single-use: second verify of the same token fails
    assert g.verify("confirm AAAA", now=T0 + dt.timedelta(seconds=11)) is None


def test_expired_token_rejected():
    g = _gate()
    g.issue(payload="x", now=T0)
    assert g.verify("AAAA", now=T0 + dt.timedelta(seconds=301)) is None


def test_reply_must_contain_token():
    g = _gate()
    g.issue(payload="x", now=T0)
    assert g.verify("yes please", now=T0) is None
    assert g.verify("AAAA", now=T0) is not None  # bare token ok, case-insensitive


def test_case_insensitive_match():
    g = _gate()
    g.issue(payload="x", now=T0)
    assert g.verify("Confirm aaaa", now=T0) is not None


def test_expired_token_is_consumed_not_retryable():
    g = _gate()
    g.issue(payload="x", now=T0)
    assert g.verify("AAAA", now=T0 + dt.timedelta(seconds=301)) is None  # expired
    assert g.verify("AAAA", now=T0) is None  # also gone — consumed on the expired attempt


def test_dedup_key_invalidates_prior_token():
    seq = iter(["AAAA", "BBBB"])
    g = ConfirmTokenGate(ttl_seconds=300, token_factory=lambda: next(seq))
    g.issue(payload="trim-1", now=T0, dedup_key="trim")
    g.issue(payload="trim-2", now=T0, dedup_key="trim")   # invalidates AAAA
    assert g.verify("AAAA", now=T0) is None                # prior token dead
    p = g.verify("BBBB", now=T0)
    assert p is not None and p.payload == "trim-2"         # only the latest works


def test_no_dedup_key_keeps_both():
    seq = iter(["AAAA", "BBBB"])
    g = ConfirmTokenGate(ttl_seconds=300, token_factory=lambda: next(seq))
    g.issue(payload="x", now=T0)
    g.issue(payload="y", now=T0)
    assert g.verify("AAAA", now=T0) is not None            # both valid without a dedup key
    assert g.verify("BBBB", now=T0) is not None
