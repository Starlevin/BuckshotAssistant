import unittest

from core import Counter, CountError, EventGate, LoadGate, parse_load


class ParsingTests(unittest.TestCase):
    def test_numeric_tokens_do_not_merge_across_whitespace(self):
        self.assertEqual(parse_load("1 3 实弹 2 空弹"), (3, 2))
        self.assertEqual(parse_load("1\n3 LIVE ROUNDS 2 BLANK ROUNDS"), (3, 2))
        self.assertEqual(parse_load("3 实弹 2 空弹 剩余 1 发"), (3, 2))
        for text in ("16 实弹 0 空弹", "8 实弹 8 空弹", "9 实弹 1 空弹", "十三实弹 两空弹"):
            with self.subTest(text=text):
                self.assertIsNone(parse_load(text))

    def test_supported_languages_and_formats(self):
        samples = {
            "3 实弹 2 空弹": (3, 2),
            "实 弹：３ 空 弹：２": (3, 2),
            "3 LIVE ROUNDS. 2 BLANKS.": (3, 2),
            "LIVE: 3 BLANK: 2": (3, 2),
            "2 blank rounds\n3 live rounds": (3, 2),
            "三发实弹、两发空包弹": (3, 2),
            "3實彈，2空彈": (3, 2),
            "0 live rounds, 4 blank rounds": (0, 4),
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(parse_load(text), expected)

    def test_incomplete_conflicting_or_invalid_text(self):
        for text in ("LIVE", "3 LIVE ROUNDS", "magnifying glass: BLANK", "0 实弹 0 空弹", "17 实弹 0 空弹", "-3 实弹 2 空弹", "3.5 实弹 2 空弹", "3 实弹 2 空弹 4 实弹", "LIVE 3 BLANK 2 LIVE 2 BLANK 3", "133 实弹 2 空弹"):
            with self.subTest(text=text):
                self.assertIsNone(parse_load(text))


class CounterTests(unittest.TestCase):
    def test_capacity_eight_and_invalid_load_preserves_state(self):
        c = Counter()
        for pair in ((8, 0), (0, 8), (4, 4)):
            c.load(*pair)
            self.assertEqual(c.state.total, 8)
        before = (c.state.live, c.state.blank, c.state.round)
        for pair in ((9, 0), (4, 5), (8, 8)):
            with self.assertRaises(CountError):
                c.load(*pair)
            self.assertEqual((c.state.live, c.state.blank, c.state.round), before)

    def test_conflict_undo_keeps_last_legal_consumption(self):
        c = Counter()
        c.load(1, 2)
        c.consume("live")
        with self.assertRaises(CountError):
            c.consume("live")
        self.assertIsNone(c.state.probability)
        event = c.undo()
        self.assertEqual(event['action'], 'clear_conflict')
        self.assertEqual((c.state.live, c.state.blank), (0, 2))
        self.assertFalse(c.state.uncertain)
        c.undo()
        self.assertEqual((c.state.live, c.state.blank), (1, 2))

    def test_conflict_undo_does_not_remove_load_or_cross_round(self):
        c = Counter()
        c.load(0, 2)
        with self.assertRaises(CountError):
            c.consume('live')
        c.undo()
        self.assertTrue(c.state.loaded)
        self.assertEqual(c.state.total, 2)
        c.load(3, 2)
        with self.assertRaises(CountError):
            c.undo()
        self.assertEqual((c.state.live, c.state.blank, c.state.round), (3, 2, 2))

    def test_round_consumption_and_undo(self):
        c = Counter()
        c.load(2, 2)
        c.consume("live", "auto_shot")
        c.consume("blank", "auto_eject")
        self.assertEqual((c.state.live, c.state.blank, c.state.total), (1, 1, 2))
        c.undo()
        self.assertEqual((c.state.live, c.state.blank), (1, 2))
        c.consume("live")
        c.consume("blank")
        c.consume("blank")
        self.assertIsNone(c.state.probability)
        c.load(3, 1)
        self.assertEqual(c.state.round, 2)

    def test_conflicts_stop_without_negative_counts(self):
        c = Counter()
        c.load(0, 2)
        with self.assertRaises(CountError):
            c.consume("live", "auto_shot")
        self.assertTrue(c.state.uncertain)
        self.assertEqual((c.state.live, c.state.blank), (0, 2))
        with self.assertRaises(CountError):
            c.consume("blank")
        c.load(1, 2)
        self.assertFalse(c.state.uncertain)

    def test_inverter_changes_composition_but_not_total(self):
        c = Counter()
        c.load(1, 2)
        c.invert("live")
        self.assertEqual((c.state.live, c.state.blank, c.state.total), (0, 3, 3))
        c.consume("blank")
        c.undo()
        c.undo()
        self.assertEqual((c.state.live, c.state.blank), (1, 2))
        with self.assertRaises(CountError):
            c.invert("unknown")

    def test_requires_valid_load(self):
        c = Counter()
        with self.assertRaises(CountError):
            c.consume("live")
        for pair in ((0, 0), (-1, 2), (9, 9), (True, 3), (1.5, 2)):
            with self.assertRaises(CountError):
                c.load(*pair)


class GateTests(unittest.TestCase):
    def test_cooldown_result_is_discarded_not_delayed(self):
        g = EventGate()
        g.observe('idle', 0.)
        g.observe('idle', .5)
        g.observe('shot_live', 1.)
        self.assertEqual(g.observe('shot_live', 1.2), 'shot_live')
        g.observe('idle', 1.3)
        g.observe('idle', 1.8)
        self.assertTrue(g.armed)
        g.observe('eject_live', 1.9)
        self.assertFalse(g.armed)
        self.assertIsNone(g.observe('eject_live', 2.2))
        self.assertIsNone(g.observe('eject_live', 3.))
        g.observe('idle', 4.)
        g.observe('idle', 4.5)
        g.observe('shot_blank', 5.)
        self.assertEqual(g.observe('shot_blank', 5.2), 'shot_blank')

    def test_exact_idle_threshold_and_single_idle_frame(self):
        g = EventGate()
        g.observe('idle', 1.3)
        g.observe('idle', 1.7)
        self.assertTrue(g.armed)
        g.reset()
        g.observe('idle', 1.3)
        g.observe('shot_live', 1.7)
        self.assertFalse(g.armed)
        self.assertIsNone(g.observe('shot_live', 2.))

    def test_load_stability_and_reappearance(self):
        g = LoadGate()
        self.assertIsNone(g.observe((3, 2), 0))
        self.assertIsNone(g.observe((3, 2), .2))
        self.assertEqual(g.observe((3, 2), .5), (3, 2))
        self.assertIsNone(g.observe((3, 2), 1))
        g.observe(None, 2)
        g.observe(None, 3)
        self.assertIsNone(g.observe((3, 2), 4))
        self.assertEqual(g.observe((3, 2), 4.5), (3, 2))

    def test_no_consumption_before_idle(self):
        g = EventGate()
        self.assertIsNone(g.observe("shot_live", 0))
        self.assertIsNone(g.observe("shot_live", 1))
        self.assertFalse(g.armed)

    def test_persistent_shell_and_unknown_scene_do_not_rearm(self):
        g = EventGate()
        g.observe("idle", 0)
        g.observe("idle", .5)
        g.observe("shot_live", .7)
        self.assertEqual(g.observe("shot_live", .9), "shot_live")
        for t in (1, 2, 3, 4):
            self.assertIsNone(g.observe("shot_live", t))
        g.observe(None, 5)
        g.observe(None, 6)
        g.observe("shot_blank", 7)
        self.assertIsNone(g.observe("shot_blank", 8))
        g.observe("idle", 9)
        g.observe("idle", 9.5)
        g.observe("shot_live", 10)
        self.assertEqual(g.observe("shot_live", 10.2), "shot_live")

    def test_reset_on_focus_change_requires_new_idle(self):
        g = EventGate()
        g.observe("idle", 0)
        g.observe("idle", .5)
        g.reset()
        g.observe("eject_blank", 1)
        self.assertIsNone(g.observe("eject_blank", 1.3))


if __name__ == "__main__":
    unittest.main()
