import unittest
from unittest.mock import patch
from solver_adapter import StrategyState, advise


def position(**kw):
    fields = dict(player_hp=4, enemy_hp=1, max_hp=4,
                  player_items=(0,) * 9, enemy_items=(0,) * 9,
                  player_turn=True, dealer_cuff_state=0, player_cuff_state=0,
                  saw_active=False, mode='endless', dealer_memory_clear=True, confirmed=True)
    fields.update(kw)
    return StrategyState(**fields)


class SolverAdapterTests(unittest.TestCase):
    def test_counts_only_never_invent_strategy(self):
        with patch('solver_adapter.load_engine', side_effect=AssertionError('must not load')):
            result = advise(3, 2)
        self.assertEqual(result['status'], 'needs_context')
        self.assertEqual(result['live_probability'], .6)
        self.assertEqual(result['steps'], [])

    def test_actual_upstream_confirmed_live_immediate_kill(self):
        result = advise(1, 0, position())
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['steps'][0].startswith('Shoot DEALER'))

    def test_actual_upstream_confirmed_blank_shoots_self(self):
        result = advise(0, 1, position(enemy_hp=4))
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['steps'][0].startswith('Shoot YOURSELF'))

    def test_actual_upstream_inverter_combo(self):
        items = [0] * 9
        items[8] = 1
        result = advise(0, 1, position(player_items=tuple(items)))
        self.assertTrue(any('INVERTER' in step for step in result['steps']))
        self.assertTrue(any('Shoot DEALER' in step for step in result['steps']))

    def test_unsupported_context_rejected_without_solving(self):
        for changes in (dict(mode='normal'), dict(player_turn=False), dict(dealer_memory_clear=False), dict(player_cuff_state=1)):
            with self.subTest(changes=changes):
                self.assertEqual(advise(1, 1, position(**changes))['status'], 'unsupported_context')

    def test_complete_but_unconfirmed_context_not_solved(self):
        with patch('solver_adapter.load_engine', side_effect=AssertionError('must not load')):
            self.assertEqual(advise(1, 1, position(confirmed=False))['status'], 'needs_confirmation')

    def test_invalid_counts_inventory_and_shell_knowledge(self):
        for live, blank in ((True, 1), (9, 0), (-1, 2)):
            with self.assertRaises(ValueError):
                advise(live, blank)
        with self.assertRaises(ValueError):
            advise(1, 1, position(player_items=(0,)))
        with self.assertRaises(ValueError):
            advise(1, 1, position(known_shells=(1, 1)))


if __name__ == '__main__':
    unittest.main()
