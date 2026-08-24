import unittest

from core.models import OrderIntent, RiskLimits, Signal
from core.order_executor import PaperExecutor
from factor_zoo.technical import deterministic_signal, get_technical_context
from backtest.validation import rolling_windows, summarize_returns


class PaperCoreTests(unittest.TestCase):
    def test_hold_is_not_executed(self):
        executor = PaperExecutor()
        decision = executor.execute(OrderIntent("BTC/USDT", Signal.HOLD, 100.0, 2.0, "test"))
        self.assertFalse(decision.approved)
        self.assertEqual(executor.positions, {})

    def test_position_is_capped_and_has_stop_target(self):
        executor = PaperExecutor(RiskLimits(starting_capital=10_000, max_position_notional_pct=0.25))
        decision = executor.execute(OrderIntent("BTC/USDT", Signal.BUY, 100.0, 1.0, "test"))
        self.assertTrue(decision.approved)
        self.assertLessEqual(decision.notional, 2500.0)
        position = executor.positions["BTC/USDT"]
        self.assertLess(position.stop_loss, position.entry_price)
        self.assertGreater(position.take_profit, position.entry_price)

    def test_stop_closes_once_and_includes_costs(self):
        executor = PaperExecutor(RiskLimits(starting_capital=10_000, fee_rate=0.001, slippage_bps=10))
        executor.execute(OrderIntent("BTC/USDT", Signal.BUY, 100.0, 1.0, "test"))
        position = executor.positions["BTC/USDT"]
        closed = executor.update_price("BTC/USDT", position.stop_loss)
        self.assertEqual(len(closed), 1)
        self.assertNotIn("BTC/USDT", executor.positions)
        self.assertLess(closed[0]["pnl"], 0)
        self.assertEqual(executor.update_price("BTC/USDT", position.stop_loss), [])

    def test_invalid_context_fails_closed(self):
        context = get_technical_context(None)
        self.assertFalse(context["ready"])
        self.assertEqual(deterministic_signal(context), Signal.HOLD)

    def test_portfolio_exposure_gate(self):
        limits = RiskLimits(starting_capital=10_000, max_position_notional_pct=0.25, max_total_exposure_pct=0.25)
        executor = PaperExecutor(limits)
        first = executor.execute(OrderIntent("BTC/USDT", Signal.BUY, 100.0, 1.0, "test"))
        second = executor.execute(OrderIntent("ETH/USDT", Signal.BUY, 100.0, 1.0, "test"))
        self.assertTrue(first.approved)
        self.assertFalse(second.approved)

    def test_walk_forward_windows_are_forward_only(self):
        windows = rolling_windows(1000, 500, 100)
        self.assertEqual(windows[0].train_end, windows[0].test_start)
        self.assertLess(windows[0].test_start, windows[0].test_end)
        summary = summarize_returns([{"return_pct": 2, "max_drawdown_pct": 4}, {"return_pct": -1, "max_drawdown_pct": 8}])
        self.assertEqual(summary["profitable_windows"], 1)
        self.assertEqual(summary["worst_return_pct"], -1.0)


if __name__ == "__main__":
    unittest.main()
