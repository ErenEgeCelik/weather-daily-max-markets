"""Plot paired Brier differences recomputed by the historical research audit."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research_audit import run_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="output/research-scores.png")
    args = parser.parse_args()
    report = run_audit()
    panels = [
        ("source_substituted", "Historical replay with substituted forecast source"),
        ("production_journal", "Recorded production-journal diagnostics"),
        ("pre_metar_all", "Before a METAR report: all eligible states"),
        ("pre_metar_consistent", "Before a METAR report: consistency-filtered states"),
    ]
    fig, axes = plt.subplots(4, 1, figsize=(10, 10), layout="constrained")
    fig.suptitle("Weather forecast scores against the market", fontsize=16, weight="bold")
    for ax, (key, title) in zip(axes, panels):
        rows = report["score_rechecks"][key]
        for i, row in enumerate(rows):
            value = row["model_minus_market_brier_mean"]
            lo, hi = row["difference_ci95_cluster_bootstrap"]
            ax.scatter(value, i, color="#1f5b83", zorder=3, s=45)
            if lo is not None and hi is not None:
                ax.hlines(i, lo, hi, color="#1f5b83", linewidth=2)
                ax.plot([lo, hi], [i, i], "|", color="#1f5b83", markersize=9)
            ax.annotate(f"  {value:+.4f}", (value, i), xytext=(4, 9),
                        textcoords="offset points", fontsize=9)
        ax.set_yticks(range(len(rows)), [
            f"{r['model']}\n{r['n_snapshots']:,} snapshots / {r['clusters']} city-days" for r in rows
        ], fontsize=9)
        ax.set_ylim(-0.6, max(0, len(rows) - 1) + 0.8)
        ax.invert_yaxis()
        ax.axvline(0, color="#777777", linewidth=1, linestyle="--")
        ax.set_title(title, loc="left", fontsize=11, pad=12)
        ax.set_xlabel("Model Brier minus market Brier  |  negative favors model", fontsize=9)
        ax.grid(axis="x", alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.margins(x=0.25)
    fig.supxlabel("Archived predictions; snapshot-weighted means; 95% city-day bootstrap intervals.\n"
                  "Quoted-support scoring. Separate studies and model versions; no trading-return estimate.",
                  fontsize=9)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
