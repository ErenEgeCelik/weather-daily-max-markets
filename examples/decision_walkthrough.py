"""Synthetic observations -> actual Kalman P/Q/D -> action scores -> fake ARM/FIRE."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probability_walkthrough import build_synthetic_model, probability_state
from execution_walkthrough import SyntheticVenue
from weather_research.decisions import (
    Bucket, BucketBook, MarketState, OneStepDecisionEngine, Position,
    conditional_playbook, total_probability_residual,
)
from weather_research.execution import OrderIntent, PreparedExecutor
from weather_research.rules import build_triggers, known_yes_payoff


def describe(decision) -> dict:
    return {
        "action": asdict(decision.action), "outcome": decision.outcome,
        "contract_probability": decision.confidence,
        "selected_mean_pnl_dollars": decision.selected.value.mean,
        "selected_variance_dollars_squared": decision.selected.value.variance,
        "selected_utility_dollars": decision.selected.value.utility,
        "improvement_over_hold": decision.selected.improvement,
        "alternatives": [asdict(score) for score in decision.alternatives],
    }


async def preparation_bridge(decision) -> dict:
    """Map the BUY quantity to fixture spend without invoking an exchange."""
    if decision.action.kind != "BUY":
        return {"prepared": False, "reason": "the one-step action is not a BUY"}
    action = decision.action
    venue = SyntheticVenue()
    executor = PreparedExecutor("synthetic-decision-contract", venue, venue, venue,
                                ttl_s=30, clock=lambda: venue.now)
    await executor.warm_cache()
    intent = OrderIntent("synthetic-belief-decision", spend=action.qty * action.price,
                         price_cap=action.price)
    await executor.prepare(intent)
    before = venue.calls.copy()
    result = await executor.fire(intent.decision_id)
    return {"prepared": True, "contract": action.bucket.label + " " + action.side,
            "fixture_shares": action.qty, "fixture_spend_dollars": intent.spend,
            "fixture_price_cap": intent.price_cap,
            "fire_calls": dict(venue.calls - before), "reply_state": result.state.value,
            "confirmed_fill": result.confirmation is not None}


async def walkthrough() -> dict:
    model, observations = build_synthetic_model()
    belief = probability_state(model)
    probabilities = belief["P_now"]
    modal = max(probabilities, key=probabilities.get)
    target = Bucket(str(modal), modal, modal)
    held = Bucket(str(modal - 1), modal - 1, modal - 1)
    # Deliberately favorable example quotes show BUY/SELL/HOLD score differences.
    # They are not historical books or evidence of achievable mispricing.
    books = (
        BucketBook(target, yes_bid=0.20, yes_ask=0.22, yes_ask_depth=10, yes_bid_depth=10),
        BucketBook(held, yes_bid=0.40, yes_ask=0.42, yes_ask_depth=10, yes_bid_depth=10),
    )
    state = MarketState(probabilities, books, (Position(held, "YES", 0.60, 2),),
                        fee_rate=0.0, cash=10, M_floor=belief["confirmed_max_c"])
    engine = OneStepDecisionEngine(lam=0.02, default_qty=3, liquidity_blend_alpha=0.6)
    decision = engine.choose(state)
    plans = conditional_playbook(state, belief["Q_next"], belief["D_after"], engine)

    # Separate rule fixture: R1/R2 are maximum transitions; R3 uses book filters.
    b24, b25, b26, top = (Bucket("24", 24, 24), Bucket("25", 25, 25),
                         Bucket("26", 26, 26), Bucket("27+", 27, None))
    rule_books = (
        BucketBook(b24, yes_bid=0.25, yes_ask=0.3),
        BucketBook(b25, yes_bid=0.15, yes_ask=0.2),
        BucketBook(b26, yes_bid=0.1, yes_ask=0.15, yes_ask_depth=10),
        BucketBook(top, yes_bid=0.05, yes_ask=0.1),
    )
    return {
        "status": "synthetic observations/books/positions; no performance estimate",
        "model": "public KalmanWeatherModel; 10000 paths, seed 0 per sampling call",
        "assumptions": {"prices": "USD per share", "quantities": "shares",
                        "fee_rate": 0.0, "risk_penalty_lambda_per_dollar": 0.02,
                        "book_fills": "hypothetical best-level execution within displayed depth"},
        "observations": observations,
        "belief": {key: belief[key] for key in ("when", "posterior_mean_c", "posterior_sigma_c",
                                                "confirmed_max_c", "P_now", "Q_next", "D_after")},
        "total_probability_l1_residual": total_probability_residual(probabilities, belief["Q_next"], belief["D_after"]),
        "current_decision": describe(decision),
        "conditional_playbook": [{"metar_value": plan.metar_value,
                                  "branch_probability": plan.probability,
                                  "confirmed_floor": plan.confirmed_floor,
                                  "conditional_P_Y": plan.posterior,
                                  "decision": describe(plan.decision)} for plan in plans],
        "covered_branch_probability": sum(plan.probability for plan in plans),
        "fake_preparation_and_submission": await preparation_bridge(decision),
        "separate_rule_fixture": {"confirmed_max": 24,
                                  "candidates": [asdict(c) for c in build_triggers(rule_books, 24)],
                                  "24_yes_payoff_after_confirmed_25": known_yes_payoff(b24, 25)},
    }


def _action_label(action: dict) -> str:
    if action["kind"] == "HOLD":
        return "HOLD"
    return (f"{action['kind']} {action['bucket']['label']} {action['side']} "
            f"{action['qty']:g}sh @ ${action['price']:.2f}")


def _probability_summary(probabilities: dict, count: int = 4) -> str:
    ranked = sorted(probabilities.items(), key=lambda item: (-item[1], int(item[0])))
    shown = ", ".join(f"{value}: {probability:.1%}" for value, probability in ranked[:count])
    if len(ranked) > count:
        shown += f"; other: {sum(probability for _, probability in ranked[count:]):.1%}"
    return shown


def format_trace(result: dict) -> str:
    """A short computation trace; walkthrough() remains the complete data API."""
    belief, decision = result["belief"], result["current_decision"]
    lines = [
        "Weather observations -> probabilities -> decision -> fake submission",
        result["status"],
        result["model"],
        "",
        f"Input: {len(result['observations'])} synthetic observations; state at {belief['when']}",
        (f"Posterior: mean {belief['posterior_mean_c']:.3f} C, sigma {belief['posterior_sigma_c']:.3f} C; "
         f"confirmed maximum {belief['confirmed_max_c']} C"),
        "P(daily maximum): " + _probability_summary(belief["P_now"]),
        "Q(next METAR):    " + _probability_summary(belief["Q_next"]),
        "",
        "Hypothetical current-book fills; fee=0, lambda=0.02 per dollar.",
        "Portfolio scores include existing positions; variance includes covariance.",
        f"{'Action':<30} {'Mean $':>9} {'Var $^2':>9} {'Utility $':>10} {'Delta U $':>10}  Buy screen",
    ]
    for score in decision["alternatives"]:
        value = score["value"]
        gate = ("pass" if score["eligible"] else "fail") if score["action"]["kind"] == "BUY" else "-"
        lines.append(
            f"{_action_label(score['action']):<30} {value['mean']:>9.4f} "
            f"{value['variance']:>9.4f} {value['utility']:>10.4f} "
            f"{score['improvement']:>10.4f}  {gate}"
        )
    lines.extend([
        f"Selected: {_action_label(decision['action'])} [{decision['outcome']}]",
        "",
        "Conditional playbook (same hypothetical book, supplied D_after):",
    ])
    for branch in result["conditional_playbook"]:
        branch_decision = branch["decision"]
        lines.append(
            f"  METAR={branch['metar_value']}, Q={branch['branch_probability']:.1%}, "
            f"floor={branch['confirmed_floor']} -> {_action_label(branch_decision['action'])} "
            f"[{branch_decision['outcome']}], delta U=${branch_decision['improvement_over_hold']:.4f}"
        )
    lines.extend([
        f"Selected-branch probability coverage: {result['covered_branch_probability']:.2%}",
        f"P versus sum(Q*D) L1 residual: {result['total_probability_l1_residual']:.6f} (model diagnostic)",
        "",
    ])
    bridge = result["fake_preparation_and_submission"]
    if bridge["prepared"]:
        lines.extend([
            (f"Fake ARM: {bridge['contract']}, {bridge['fixture_shares']:g} shares, "
             f"spend ${bridge['fixture_spend_dollars']:.2f}, cap ${bridge['fixture_price_cap']:.2f}."),
            (f"Fake FIRE calls: {bridge['fire_calls']}; state={bridge['reply_state']}; "
             f"confirmed fill={bridge['confirmed_fill']}."),
        ])
    else:
        lines.append("Fake ARM/FIRE skipped: " + bridge["reason"])
    rule_fixture = result["separate_rule_fixture"]
    rules = sorted({candidate["rule"] for candidate in rule_fixture["candidates"]})
    lines.extend([
        (f"Separate rule fixture: {', '.join(rules)} conditions; confirmed 25 C makes "
         f"24 C YES payoff {rule_fixture['24_yes_payoff_after_confirmed_25']:g}."),
        "Full observations, probabilities, branch scores and rule conditions: add --json.",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the complete machine-readable fixture result")
    args = parser.parse_args()
    result = asyncio.run(walkthrough())
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_trace(result))
