import multiprocessing
import unittest
from solver_adapter import StrategyState
from strategy_panel import _solve_worker


class StrategyWorkerTests(unittest.TestCase):
    def test_spawned_worker_executes_real_engine_and_exits(self):
        ctx = multiprocessing.get_context('spawn')
        receiver, sender = ctx.Pipe(duplex=False)
        state = StrategyState(player_hp=4, enemy_hp=1, max_hp=4,
                              player_items=(0,) * 9, enemy_items=(0,) * 9,
                              player_turn=True, dealer_cuff_state=0,
                              player_cuff_state=0, saw_active=False,
                              mode='endless', dealer_memory_clear=True,
                              confirmed=True)
        worker = ctx.Process(target=_solve_worker, args=(sender, (1, 0), state))
        worker.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(10), 'worker timed out')
            result = receiver.recv()
            self.assertEqual(result['status'], 'ready')
            self.assertTrue(result['steps'][0].startswith('Shoot DEALER'))
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker.exitcode, 0)
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(2)
            receiver.close()
            worker.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    unittest.main()
