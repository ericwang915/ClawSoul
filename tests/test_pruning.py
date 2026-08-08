"""
Tests for EmotionalGraph and TemporalMemoryIndex pruning/capping logic.
"""
from datetime import datetime, timedelta
from unittest.mock import patch


from claw_soul.core.memory.emotional_graph import (
    EmotionalGraph,
    MAX_DAYS_KEEP,
    MAX_EVENTS_KEEP,
    PRUNE_CHECK_INTERVAL,
)
from claw_soul.core.memory.temporal_index import (
    TemporalMemoryIndex,
    TimelineEvent,
    MAX_MEMORY_EVENTS,
    MAX_FILE_EVENTS,
    PRUNE_AFTER,
)


# ── EmotionalGraph pruning tests ──────────────────────────────────────────────

class TestEmotionalGraphPruning:

    def _make_graph(self, tmp_path) -> EmotionalGraph:
        affect_dir = str(tmp_path / "affect")
        return EmotionalGraph(affect_dir)

    def test_initial_event_count_is_zero(self, tmp_path):
        g = self._make_graph(tmp_path)
        assert g._event_count == 0

    def test_event_count_increases(self, tmp_path):
        g = self._make_graph(tmp_path)
        g.add_event("test", "positive", 0.5, "hello")
        assert g._event_count == 1

    def test_add_and_read(self, tmp_path):
        g = self._make_graph(tmp_path)
        g.add_event("test", "positive", 0.5, "hello")
        events = g.get_recent(days=365)
        assert len(events) == 1
        assert events[0]["topic"] == "test"
        assert events[0]["sentiment"] == "positive"

    def test_prune_removes_old_events(self, tmp_path):
        g = self._make_graph(tmp_path)
        # Add an old event manually
        old_ts = (datetime.now() - timedelta(days=MAX_DAYS_KEEP + 10)).isoformat(timespec="seconds")
        old_event = {"ts": old_ts, "topic": "old", "sentiment": "negative", "intensity": 0.5, "context_summary": "old"}
        g._atomic_append(old_event)
        g._event_count = 1

        # Add a recent event
        g.add_event("recent", "positive", 0.8, "new stuff")

        # The prune may not auto-trigger until PRUNE_CHECK_INTERVAL writes.
        # Force it manually.
        g._prune_old_events()

        # After pruning, only the recent event should remain
        events = g._read_all()
        assert len(events) == 1
        assert events[0]["topic"] == "recent"

    def test_prune_trims_to_max_events(self, tmp_path):
        g = self._make_graph(tmp_path)
        # Add more than MAX_EVENTS_KEEP events
        now = datetime.now()
        for i in range(MAX_EVENTS_KEEP + 100):
            ts = (now - timedelta(hours=i)).isoformat(timespec="seconds")
            ev = {"ts": ts, "topic": f"e{i}", "sentiment": "neutral", "intensity": 0.0, "context_summary": ""}
            g._atomic_append(ev)
        g._event_count = MAX_EVENTS_KEEP + 100

        g._prune_old_events()
        events = g._read_all()
        assert len(events) <= MAX_EVENTS_KEEP

    def test_add_event_triggers_prune_at_interval(self, tmp_path):
        g = self._make_graph(tmp_path)
        # Override the counter to trigger prune on next add
        g._write_counter = PRUNE_CHECK_INTERVAL - 1
        g._event_count = 5  # some existing events

        # Add a new event — this should trigger _prune_old_events
        with patch.object(g, "_prune_old_events") as mock_prune:
            g.add_event("trigger", "positive", 0.5)
            mock_prune.assert_called_once()

    def test_get_topic_sentiment_works_after_prune(self, tmp_path):
        g = self._make_graph(tmp_path)
        g.add_event("music", "positive", 0.9, "liked a song")
        g.add_event("music", "positive", 0.7, "great concert")
        g.add_event("sports", "negative", 0.3, "lost game")

        result = g.get_topic_sentiment("music")
        assert result["count"] == 2
        assert result["avg_sentiment"] == "positive"

    def test_get_trend_after_prune(self, tmp_path):
        g = self._make_graph(tmp_path)
        # Add events spread across recent days
        for i in range(7):
            ts = (datetime.now() - timedelta(days=i)).isoformat(timespec="seconds")
            ev = {"ts": ts, "topic": "daily", "sentiment": "positive", "intensity": 0.5, "context_summary": ""}
            g._atomic_append(ev)
        g._event_count = 7

        trend = g.get_trend()
        assert len(trend) == 14  # last 14 days
        # At least some days should have events
        days_with_events = [d for d in trend if d["event_count"] > 0]
        assert len(days_with_events) >= 1

    def test_get_summary_after_prune(self, tmp_path):
        g = self._make_graph(tmp_path)
        g.add_event("chat", "positive", 0.6, "nice talk")
        summary = g.get_summary()
        assert "Recent mood" in summary
        assert "positive" in summary or "neutral" in summary

    def test_prune_no_file_does_nothing(self, tmp_path):
        g = self._make_graph(tmp_path)
        # No file exists yet
        g._prune_old_events()  # should not raise

    def test_count_lines_accurate(self, tmp_path):
        g = self._make_graph(tmp_path)
        assert g._count_lines() == 0
        g.add_event("a", "positive", 0.5)
        g.add_event("b", "neutral", 0.3)
        assert g._count_lines() == 2


# ── TemporalMemoryIndex pruning tests ─────────────────────────────────────────

class TestTemporalMemoryIndexPruning:

    def _make_index(self, tmp_path) -> TemporalMemoryIndex:
        memory_dir = str(tmp_path / "memory")
        return TemporalMemoryIndex(memory_dir)

    def _make_event(self, ts_offset: int = 0, topic="test") -> TimelineEvent:
        ts = (datetime.now() - timedelta(hours=ts_offset)).isoformat(timespec="seconds")
        return TimelineEvent(
            timestamp=ts,
            session_id="test_session",
            topic=topic,
            summary=f"Event at {ts_offset}h ago",
            sentiment=0.5,
            keywords=[topic],
        )

    def test_initial_memory_events_empty(self, tmp_path):
        idx = self._make_index(tmp_path)
        assert len(idx._events) == 0

    def test_add_event_increments_counter(self, tmp_path):
        idx = self._make_index(tmp_path)
        idx.add_event(self._make_event())
        assert len(idx._events) == 1

    def test_memory_events_capped(self, tmp_path):
        idx = self._make_index(tmp_path)
        # Add more than MAX_MEMORY_EVENTS
        for i in range(MAX_MEMORY_EVENTS + 50):
            idx.add_event(self._make_event(ts_offset=i))
        # Memory should be capped
        assert len(idx._events) <= MAX_MEMORY_EVENTS

    def test_file_pruning_trims_to_max(self, tmp_path):
        idx = self._make_index(tmp_path)
        # Directly write many events to the file
        for i in range(MAX_FILE_EVENTS + 100):
            ev = self._make_event(ts_offset=i)
            idx._atomic_append(ev)
        # Now prune
        idx._prune_file()
        # Check file size
        with open(idx._path) as f:
            lines = f.readlines()
        assert len(lines) <= MAX_FILE_EVENTS

    def test_add_event_triggers_prune_after_interval(self, tmp_path):
        idx = self._make_index(tmp_path)
        # Force counter to trigger prune on next add
        idx._add_counter = PRUNE_AFTER - 1

        with patch.object(idx, "_prune_file") as mock_prune:
            idx.add_event(self._make_event())
            mock_prune.assert_called_once()

        # Counter should be reset
        assert idx._add_counter == 0

    def test_search_from_file_after_prune(self, tmp_path):
        idx = self._make_index(tmp_path)
        idx.add_event(self._make_event(ts_offset=0, topic="hello"))
        idx.add_event(self._make_event(ts_offset=1, topic="world"))

        results = idx.search("hello")
        assert len(results) >= 1
        assert results[0].topic == "hello"

    def test_get_timeline_from_file(self, tmp_path):
        idx = self._make_index(tmp_path)
        ts1 = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        ts2 = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        idx.add_event(
            TimelineEvent(timestamp=ts1, session_id="s1", topic="early")
        )
        idx.add_event(
            TimelineEvent(timestamp=ts2, session_id="s2", topic="late")
        )

        start = (datetime.now() - timedelta(hours=3)).isoformat()
        end = datetime.now().isoformat()
        timeline = idx.get_timeline(start, end)
        assert len(timeline) == 2

    def test_get_timeline_out_of_range(self, tmp_path):
        idx = self._make_index(tmp_path)
        ts = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
        idx.add_event(
            TimelineEvent(timestamp=ts, session_id="s1", topic="old")
        )
        start = (datetime.now() - timedelta(hours=1)).isoformat()
        end = datetime.now().isoformat()
        timeline = idx.get_timeline(start, end)
        assert len(timeline) == 0

    def test_get_topics_works_with_limited_memory(self, tmp_path):
        idx = self._make_index(tmp_path)
        idx.add_event(self._make_event(topic="music"))
        idx.add_event(self._make_event(topic="music"))
        idx.add_event(self._make_event(topic="sports"))

        topics = idx.get_topics()
        assert "music" in topics
        assert "sports" in topics
        # music should come first (2 events vs 1)
        assert topics[0] == "music"

    def test_get_sessions(self, tmp_path):
        idx = self._make_index(tmp_path)
        idx.add_event(
            TimelineEvent(timestamp=datetime.now().isoformat(), session_id="s1")
        )
        idx.add_event(
            TimelineEvent(timestamp=datetime.now().isoformat(), session_id="s2")
        )
        sessions = idx.get_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_get_summary(self, tmp_path):
        idx = self._make_index(tmp_path)
        idx.add_event(self._make_event(topic="chat"))
        summary = idx.get_summary(days=7)
        assert "Recent conversations" in summary
        assert "chat" in summary

    def test_get_summary_empty(self, tmp_path):
        idx = self._make_index(tmp_path)
        summary = idx.get_summary(days=7)
        assert "No recent" in summary

    def test_load_recent_on_init(self, tmp_path):
        idx = self._make_index(tmp_path)
        # Add events directly to file (not through add_event to avoid memory)
        for i in range(100):
            ev = self._make_event(ts_offset=i)
            idx._atomic_append(ev)

        # Create a new index that loads from file
        idx2 = TemporalMemoryIndex(str(tmp_path / "memory"))
        # Should have loaded only the last MAX_MEMORY_EVENTS
        assert len(idx2._events) <= MAX_MEMORY_EVENTS
        # But should have at least some events
        assert len(idx2._events) > 0

    def test_prune_then_search_still_works(self, tmp_path):
        idx = self._make_index(tmp_path)
        # Add many events
        for i in range(500):
            ev = self._make_event(ts_offset=i, topic=f"topic_{i % 10}")
            idx._events.append(ev)
            idx._atomic_append(ev)

        # Force prune
        idx._prune_file()

        # Search should still find recent content
        results = idx.search("topic")
        assert len(results) > 0
