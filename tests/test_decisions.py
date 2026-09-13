import math
import unittest

from weather_research.decisions import (
    Action, Bucket, BucketBook, MarketState, OneStepDecisionEngine, Position,
    apply_action, conditional_playbook, contract_probability, exit_values,
    multi_position_utility, payoff, reaction_mean, total_probability_residual,
)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.exact = Bucket("24", 24, 24)
        self.higher = Bucket("24+", 24, None)
        self.below = Bucket("23-", None, 23)

    def test_range_and_tail_payoffs_and_probability(self):
        probabilities = {22: 0.1, 23: 0.2, 24: 0.3, 25: 0.4}
        for bucket, expected in ((self.exact, 0.3), (self.higher, 0.7), (self.below, 0.3),
                                 (Bucket("23-24", 23, 24), 0.5)):
            with self.subTest(bucket=bucket):
                self.assertAlmostEqual(contract_probability(probabilities, bucket), expected)
                self.assertAlmostEqual(contract_probability(probabilities, bucket, "NO"), 1 - expected)
                for outcome in probabilities:
                    self.assertEqual(payoff(bucket, "YES", outcome) + payoff(bucket, "NO", outcome), 1)

    def test_joint_payoff_enumeration_retains_covariance(self):
        yes = Position(self.exact, "YES", 0.45, 10)
        no = Position(self.exact, "NO", 0.40, 10)
        probabilities = {24: 0.5, 25: 0.5}
        total = multi_position_utility(probabilities, (yes, no))
        self.assertAlmostEqual(total.mean, 1.5)
        self.assertAlmostEqual(total.variance, 0.0)
        self.assertAlmostEqual(multi_position_utility(probabilities, (yes,)).variance, 25.0)

    def test_fee_accounting_buy_resolution_and_sell(self):
        state = MarketState({24: 0.7, 25: 0.3}, fee_rate=0.02)
        bought, realized = apply_action(state, Action("BUY", self.exact, "YES", 0.50, 10))
        self.assertEqual(realized, 0)
        self.assertAlmostEqual(bought[0].entry_price, 0.51)
        self.assertAlmostEqual(multi_position_utility(state.P_Y, bought, lam=0).mean, 1.9)
        after = MarketState(state.P_Y, positions=bought, fee_rate=0.02)
        remaining, realized = apply_action(after, Action("SELL", self.exact, "YES", 0.60, 10))
        self.assertEqual(remaining, ())
        self.assertAlmostEqual(realized, (0.60 * 0.98 - 0.51) * 10)

    def test_partial_fifo_sale_preserves_settlement_bounds_and_state(self):
        positions = (Position(self.higher, "YES", 0.3, 2), Position(self.higher, "YES", 0.4, 4))
        state = MarketState({24: 0.5, 25: 0.5}, positions=positions)
        after, realized = apply_action(state, Action("SELL", self.higher, "YES", 0.5, 3))
        self.assertAlmostEqual(realized, 0.5)
        self.assertEqual(after, (Position(self.higher, "YES", 0.4, 3),))
        self.assertEqual(state.positions, positions)
        self.assertAlmostEqual(after[0].pnl(25), 1.8)
        with self.assertRaises(ValueError):
            apply_action(state, Action("SELL", self.higher, "YES", 0.5, 7))

    def test_buy_and_hold_selection(self):
        book = BucketBook(self.exact, yes_bid=0.40, yes_ask=0.42, yes_ask_depth=10)
        engine = OneStepDecisionEngine(lam=0, default_qty=2)
        good = engine.choose(MarketState({24: 0.9, 25: 0.1}, (book,)))
        self.assertEqual(good.action.kind, "BUY")
        self.assertEqual(good.outcome, "candidate")
        bad = engine.choose(MarketState({24: 0.3, 25: 0.7}, (book,)))
        self.assertEqual(bad.action.kind, "HOLD")

    def test_selling_at_a_loss_can_improve_terminal_utility(self):
        position = Position(self.exact, "YES", 0.60, 3)
        book = BucketBook(self.exact, yes_bid=0.30, yes_bid_depth=3)
        decision = OneStepDecisionEngine(lam=0).choose(
            MarketState({24: 0.1, 25: 0.9}, (book,), (position,)))
        self.assertEqual(decision.action.kind, "SELL")
        self.assertEqual(decision.outcome, "candidate")
        self.assertAlmostEqual(decision.selected.edge_per_share, -0.30)
        self.assertAlmostEqual(decision.selected.improvement, 0.60)

    def test_constraints_bound_buy_size_and_sell_depth(self):
        book = BucketBook(self.exact, yes_bid=0.4, yes_ask=0.42, yes_ask_depth=8, yes_bid_depth=1)
        position = Position(self.exact, "YES", 0.4, 4)
        engine = OneStepDecisionEngine(lam=0, default_qty=10, max_position_per_contract=5)
        actions = engine.candidates(MarketState({24: 0.9, 25: 0.1}, (book,), (position,), cash=100))
        self.assertEqual([action.qty for action in actions if action.kind == "BUY"], [1])
        self.assertEqual([action.qty for action in actions if action.kind == "SELL"], [1])
        actions = engine.candidates(MarketState({24: 0.9, 25: 0.1}, (book,), cash=0.21))
        self.assertAlmostEqual(next(a.qty for a in actions if a.kind == "BUY"), 0.5)
        self.assertEqual(engine.choose(MarketState({24: 0.9, 25: 0.1}, (book,), cash=0)).action.kind, "HOLD")

    def test_risk_penalty_and_liquidity_screen_can_reject_positive_mean_buy(self):
        book = BucketBook(self.exact, yes_bid=0.38, yes_ask=0.40, yes_ask_depth=10)
        state = MarketState({24: 0.6, 25: 0.4}, (book,))
        self.assertEqual(OneStepDecisionEngine(lam=10).choose(state).action.kind, "HOLD")
        self.assertEqual(OneStepDecisionEngine(lam=0, liquidity_blend_alpha=0).choose(state).action.kind, "HOLD")
        self.assertEqual(OneStepDecisionEngine(lam=0).choose(state).outcome, "review")
        wide = BucketBook(self.exact, yes_bid=0.2, yes_ask=0.4, yes_ask_depth=10)
        self.assertEqual(OneStepDecisionEngine(lam=0).choose(MarketState(state.P_Y, (wide,))).action.kind, "HOLD")

    def test_no_contract_reaction_is_not_complemented_twice(self):
        # These are NO prices throughout: current 0.20, target P(NO)=0.80.
        self.assertAlmostEqual(reaction_mean(0.2, 0.8, 0.1, 0), 0.2)
        self.assertAlmostEqual(reaction_mean(0.2, 0.8, 0.1, 1000), 0.8)
        position = Position(self.exact, "NO", 0.2, 1)
        result = exit_values(position, {24: 0.2, 25: 0.8}, 0.3, event_contract_price=0.7)
        self.assertAlmostEqual(result["pnl_per_share"]["event"], 0.5)
        self.assertAlmostEqual(result["pnl_per_share"]["resolution"], 0.6)

    def test_exit_comparison_allows_loss_reduction_and_requires_bid(self):
        position = Position(self.exact, "YES", 0.6, 1)
        result = exit_values(position, {24: 0.1, 25: 0.9}, 0.3)
        self.assertTrue(result["should_exit"])
        self.assertEqual(result["horizon"], "now")
        self.assertFalse(exit_values(position, {24: 0.1, 25: 0.9}, None,
                                     stop_loss_per_share=0.1)["should_exit"])

    def test_same_target_reaction_screen_does_not_create_positive_event_edge(self):
        for fair in (0.1, 0.3, 0.5, 0.9):
            for gamma in (0.0, 0.01, 0.1):
                for side in ("YES", "NO"):
                    book = BucketBook(self.exact, yes_bid=0.38, yes_ask=0.4,
                                      no_bid=0.38, no_ask=0.4, yes_ask_depth=5, no_ask_depth=5)
                    probability_yes = fair if side == "YES" else 1 - fair
                    state = MarketState({24: probability_yes, 25: 1 - probability_yes}, (book,), fee_rate=0.02)
                    decision = OneStepDecisionEngine(lam=0, reaction_gamma=gamma).choose(state)
                    score = next(s for s in decision.alternatives if s.action.kind == "BUY" and s.action.side == side)
                    if score.edge_per_share > 0:
                        self.assertEqual(score.gate_horizon, "resolution_gate")

    def test_conditional_playbook_keeps_original_weights_and_floor(self):
        Q = {23: 0.4, 24: 0.6}
        D = {23: {24: 0.8, 25: 0.2}, 24: {24: 0.5, 25: 0.5}}
        state = MarketState({24: 0.62, 25: 0.38}, M_floor=24)
        plans = conditional_playbook(state, Q, D, OneStepDecisionEngine(), top_k=1)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].metar_value, 24)
        self.assertEqual(plans[0].probability, 0.6)
        self.assertEqual(plans[0].confirmed_floor, 24)
        self.assertAlmostEqual(total_probability_residual(state.P_Y, Q, D), 0)
        self.assertEqual(state.P_Y, {24: 0.62, 25: 0.38})

    def test_missing_or_impossible_conditional_branch_is_rejected(self):
        state = MarketState({24: 0.5, 25: 0.5}, M_floor=24)
        with self.assertRaises(ValueError):
            conditional_playbook(state, {25: 1.0}, {}, OneStepDecisionEngine())
        with self.assertRaises(ValueError):
            conditional_playbook(state, {25: 1.0}, {25: {24: 0.5, 25: 0.5}}, OneStepDecisionEngine())
        with self.assertRaises(ValueError):
            total_probability_residual(state.P_Y, {25: 1.0}, {})

    def test_zero_confirmed_floor_is_not_treated_as_missing(self):
        state = MarketState({0: 0.5, 1: 0.5}, M_floor=0)
        plans = conditional_playbook(state, {-1: 1.0}, {-1: state.P_Y}, OneStepDecisionEngine())
        self.assertEqual(plans[0].confirmed_floor, 0)

    def test_invalid_inputs_cannot_produce_scores(self):
        for probabilities in ({}, {24: 0.9}, {24: math.nan}, {24: -0.2, 25: 1.2}):
            with self.subTest(probabilities=probabilities), self.assertRaises(ValueError):
                multi_position_utility(probabilities, ())
        with self.assertRaises(ValueError):
            MarketState({24: 0.5, 25: 0.5}, M_floor=25)
        with self.assertRaises(ValueError):
            BucketBook(self.exact, yes_bid=0.7, yes_ask=0.6)


if __name__ == "__main__":
    unittest.main()
