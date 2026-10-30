"""Live UI state transitions without opening a desktop window."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import tempfile
from pathlib import Path
import threading
from app import App
from core import Counter


class LiveDisplayTests(TestCase):
    def make_app(self):
        app = App.__new__(App)
        app.config = {"realtime": True}
        app.counter = Counter()
        app.last_live_poll = 0
        app.window = SimpleNamespace(pid=123)
        app.status = SimpleNamespace(set=lambda value: None)
        app.update_counts = lambda: None
        return app

    def test_disconnect_clears_previous_count(self):
        app = self.make_app()
        snapshot = {"live": 3, "blank": 2, "total": 5, "pid": 123}
        with patch("app.read_snapshot", return_value=snapshot):
            app.poll_live(force=True)
        self.assertTrue(app.counter.state.loaded)
        self.assertEqual(app.counter.state.total, 5)
        with patch("app.read_snapshot", return_value=None):
            app.poll_live(force=True)
        self.assertFalse(app.counter.state.loaded)
        self.assertIsNone(app.live_source)

    def test_empty_is_known_zero_and_updates_do_not_accumulate_history(self):
        app = self.make_app()
        with patch("app.read_snapshot", return_value={"live": 0, "blank": 0, "total": 0, "pid": 123}):
            app.poll_live(force=True)
        self.assertTrue(app.counter.state.loaded)
        self.assertEqual(app.counter.state.total, 0)
        self.assertEqual(app.counter.history, [])

    def test_realtime_blocks_manual_changes(self):
        app = self.make_app()
        self.assertFalse(app.manual_allowed())
        app.config["realtime"] = False
        self.assertTrue(app.manual_allowed())

    def test_tick_failure_clears_count_logs_and_schedules_next_tick(self):
        app = self.make_app()
        app.stop = threading.Event()
        app.counter.load(2, 1)
        scheduled = []
        app.root = SimpleNamespace(after=lambda delay, callback: scheduled.append((delay, callback)))
        app._tick = lambda: (_ for _ in ()).throw(RuntimeError("injected tick failure"))
        with tempfile.TemporaryDirectory() as directory:
            app.data = Path(directory)
            app.tick()
            self.assertIn("injected tick failure", (app.data / "errors.log").read_text(encoding="utf-8"))
        self.assertFalse(app.counter.state.loaded)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], 80)
