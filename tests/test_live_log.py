import json
from pathlib import Path
import tempfile
import unittest
from live_log import read_snapshot


class SnapshotTests(unittest.TestCase):
    def check(self, **changes):
        data = dict(schema=1, time=100., valid=True, live=3, blank=2, total=5, pid=1)
        data.update(changes)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'snapshot.json'
            p.write_text(json.dumps(data))
            return read_snapshot(p, now=101., check_process=False)

    def test_fresh_consistent(self):
        self.assertEqual(self.check()['total'], 5)

    def test_stale_and_future_rejected(self):
        self.assertIsNone(self.check(time=98.))
        self.assertIsNone(self.check(time=102.))

    def test_waiting_and_inconsistent_rejected(self):
        self.assertIsNone(self.check(valid=False))
        self.assertIsNone(self.check(total=4))
        self.assertIsNone(self.check(live=True))

    def test_empty_is_known(self):
        self.assertEqual(self.check(live=0, blank=0, total=0)['total'], 0)


if __name__ == '__main__':
    unittest.main()
