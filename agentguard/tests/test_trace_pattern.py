"""Unit tests for the trace-pattern matcher."""

from __future__ import annotations

import pytest

from agentguard.policy.dsl.trace_pattern import (
    TracePatternError,
    compile_trace_pattern,
    match_trace,
)


class TestAdjacent:
    """``A -> B`` — A immediately followed by B."""

    def test_match_adjacent(self):
        assert match_trace("a -> b", ["a", "b"])

    def test_match_with_prefix(self):
        assert match_trace("a -> b", ["x", "a", "b"])

    def test_match_with_suffix(self):
        assert match_trace("a -> b", ["a", "b", "y"])

    def test_no_match_when_gap(self):
        assert not match_trace("a -> b", ["a", "x", "b"])

    def test_no_match_when_only_a(self):
        assert not match_trace("a -> b", ["a"])

    def test_no_match_when_reversed(self):
        assert not match_trace("a -> b", ["b", "a"])


class TestExactlyOne:
    """``A -> * -> B`` — exactly one event between."""

    def test_match_one_between(self):
        assert match_trace("a -> * -> b", ["a", "x", "b"])

    def test_no_match_when_adjacent(self):
        assert not match_trace("a -> * -> b", ["a", "b"])

    def test_no_match_when_two_between(self):
        assert not match_trace("a -> * -> b", ["a", "x", "y", "b"])


class TestNonEmptyGap:
    """``A -> ... -> B`` — at least one event between (non-empty path)."""

    def test_match_one_between(self):
        assert match_trace("a -> ... -> b", ["a", "x", "b"])

    def test_match_many_between(self):
        assert match_trace("a -> ... -> b", ["a", "x", "y", "z", "b"])

    def test_no_match_when_adjacent(self):
        assert not match_trace("a -> ... -> b", ["a", "b"])

    def test_no_match_when_only_a(self):
        assert not match_trace("a -> ... -> b", ["a"])


class TestOptionalGap:
    """``A -> ...? -> B`` — zero or more events between."""

    def test_match_when_adjacent(self):
        assert match_trace("a -> ...? -> b", ["a", "b"])

    def test_match_when_one_between(self):
        assert match_trace("a -> ...? -> b", ["a", "x", "b"])

    def test_match_when_many_between(self):
        assert match_trace("a -> ...? -> b", ["a", "x", "y", "z", "b"])

    def test_no_match_when_only_a(self):
        assert not match_trace("a -> ...? -> b", ["a"])

    def test_no_match_when_reversed(self):
        assert not match_trace("a -> ...? -> b", ["b", "a"])


class TestRealisticToolNames:
    """Tool names with dots (``db.query``) must match literally, not as regex."""

    def test_dotted_names_adjacent(self):
        assert match_trace("db.query -> http.post", ["db.query", "http.post"])

    def test_dotted_names_no_false_positive_on_dot(self):
        # 'db.query' must NOT match 'dbXquery'
        assert not match_trace("db.query -> http.post", ["dbXquery", "http.post"])

    def test_chain_three_steps(self):
        assert match_trace(
            "db.query -> ... -> file.write -> http.post",
            ["db.query", "transform", "file.write", "http.post"],
        )

    def test_chain_three_steps_missing_middle(self):
        assert not match_trace(
            "db.query -> ... -> file.write -> http.post",
            ["db.query", "http.post"],
        )


class TestBoundary:
    """Boundary cases: empty sequences / single steps / errors."""

    def test_single_step_matches_when_present(self):
        assert match_trace("a", ["a"])

    def test_single_step_matches_in_longer_seq(self):
        assert match_trace("a", ["x", "a", "y"])

    def test_single_step_no_match_when_absent(self):
        assert not match_trace("a", ["x", "y"])

    def test_empty_sequence(self):
        assert not match_trace("a -> b", [])

    def test_empty_pattern_raises(self):
        with pytest.raises(TracePatternError):
            compile_trace_pattern("")

    def test_trailing_separator_raises(self):
        with pytest.raises(TracePatternError):
            compile_trace_pattern("a ->")

    def test_double_separator_raises(self):
        with pytest.raises(TracePatternError):
            compile_trace_pattern("a -> -> b")


class TestCacheReuse:
    """Compiled matchers should be cached (lru_cache)."""

    def test_same_pattern_returns_same_matcher(self):
        m1 = compile_trace_pattern("a -> b")
        m2 = compile_trace_pattern("a -> b")
        assert m1 is m2
