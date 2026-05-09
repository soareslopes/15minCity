# step9_analysis.py
# Post-processing: diminishing returns, scatter plots, inequality distributions.
# Called by main.py or standalone: python step9_analysis.py output/results_final.csv

import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_intervals(df):
    """Return sorted list of time intervals found in mean_variety_{t}min columns."""
    intervals = []
    for col in df.columns:
        m = re.match(r"mean_variety_(\d+)min", col)
        if m:
            intervals.append(int(m.group(1)))
    return sorted(intervals)


def _exp_decay_up(x, k):
    """f(x) = 10 * (1 - k^x)  — exponential growth towards asymptote 10."""
    return 10 * (1 - np.power(np.clip(k, 1e-9, 1 - 1e-9), x))


def _fit_exp(x, y):
    try:
        popt, _ = curve_fit(_exp_decay_up, x, y, p0=[0.95], bounds=(0, 1), maxfev=5000)
        return popt[0]
    except Exception:
        return None


def _scatter_with_fit(ax, x, y, xlabel, ylabel, title, color="#2196F3"):
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) == 0:
        ax.set_title(title)
        return
    ax.scatter(x, y, alpha=0.6, s=40, color=color, edgecolors="white", linewidths=0.4)
    k = _fit_exp(x, y)
    if k is not None:
        x_line = np.linspace(x.min(), x.max(), 300)
        ax.plot(x_line, _exp_decay_up(x_line, k), color="crimson", linewidth=1.8,
                label=f"fit: 10·(1 − {k:.4f}^x)")
        ax.legend(fontsize=9)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.set_title(title, fontsize=11)


# ---------------------------------------------------------------------------
# Plot 1: Diminishing returns across time intervals (box plots)
# ---------------------------------------------------------------------------

def _plot_diminishing_returns(df, intervals, fig_dir):
    """
    For each metric (variety, total_dest, entropy), box plot of city means
    at each time interval. Shows how the distribution shifts as time grows.
    """
    metrics = [
        ("variety",    "Mean Variety",    "#4CAF50"),
        ("total_dest", "Mean Total Dest", "#2196F3"),
        ("entropy",    "Mean Entropy",    "#FF9800"),
    ]
    present = [(m, l, c) for m, l, c in metrics
               if any(f"mean_{m}_{t}min" in df.columns for t in intervals)]
    if not present or not intervals:
        return

    fig, axes = plt.subplots(1, len(present), figsize=(5 * len(present), 5))
    if len(present) == 1:
        axes = [axes]

    for ax, (metric, label, color) in zip(axes, present):
        data = []
        labels = []
        for t in intervals:
            col = f"mean_{metric}_{t}min"
            if col in df.columns:
                data.append(df[col].dropna().values)
                labels.append(f"{t} min")
        if not data:
            continue
        bp = ax.boxplot(data, labels=labels, patch_artist=True,
                        medianprops=dict(color="crimson", linewidth=2))
        for patch in bp["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.6)
        ax.set_xlabel("Time threshold", fontsize=10)
        ax.set_ylabel(label, fontsize=10)
        ax.set_title(f"Diminishing returns — {label}", fontsize=11)

    plt.tight_layout()
    fig.savefig(fig_dir / "diminishing_returns.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved diminishing_returns.png")


# ---------------------------------------------------------------------------
# Plot 2: Pop density vs accessibility scatter (reference interval)
# ---------------------------------------------------------------------------

def _plot_density_scatter(df, ref_interval, fig_dir):
    metrics = [
        (f"mean_variety_{ref_interval}min",    f"Mean Variety ({ref_interval} min)",    "#4CAF50"),
        (f"mean_total_dest_{ref_interval}min", f"Mean Total Dest ({ref_interval} min)", "#2196F3"),
        (f"mean_entropy_{ref_interval}min",    f"Mean Entropy ({ref_interval} min)",    "#FF9800"),
    ]
    present = [(c, l, col) for c, l, col in metrics if c in df.columns]
    if not present or "pop_density_ha" not in df.columns:
        return

    fig, axes = plt.subplots(1, len(present), figsize=(5 * len(present), 5))
    if len(present) == 1:
        axes = [axes]

    for ax, (col, label, color) in zip(axes, present):
        _scatter_with_fit(
            ax,
            df["pop_density_ha"].values.astype(float),
            df[col].values.astype(float),
            "Population density (inh/ha)",
            label,
            f"Pop. density vs {label}",
            color=color,
        )
        for _, row in df.iterrows():
            if pd.notna(row.get("pop_density_ha")) and pd.notna(row.get(col)):
                ax.annotate(
                    str(row["city_name"]).split(",")[0],
                    (row["pop_density_ha"], row[col]),
                    fontsize=6, alpha=0.7,
                )

    plt.suptitle(f"Pop. Density vs Accessibility ({ref_interval} min threshold)", fontsize=13)
    plt.tight_layout()
    fig.savefig(fig_dir / "density_vs_accessibility.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved density_vs_accessibility.png")


# ---------------------------------------------------------------------------
# Plot 3: Inequality distributions (reference interval)
# ---------------------------------------------------------------------------

def _plot_inequity_distributions(df, ref_interval, fig_dir):
    measures = [
        ("gini",   "Gini"),
        ("palma",  "Palma ratio"),
        ("theil",  "Theil T"),
        ("cv",     "CV"),
    ]
    metrics = [
        (f"variety_{ref_interval}min",    f"Variety {ref_interval}min"),
        (f"total_dest_{ref_interval}min", f"Total Dest {ref_interval}min"),
    ]

    fig, axes = plt.subplots(len(measures), len(metrics),
                             figsize=(5 * len(metrics), 4 * len(measures)))
    if len(metrics) == 1:
        axes = axes.reshape(-1, 1)
    if len(measures) == 1:
        axes = axes.reshape(1, -1)

    for j, (met_key, met_label) in enumerate(metrics):
        for i, (meas_key, meas_label) in enumerate(measures):
            col = f"{meas_key}_{met_key}"
            ax = axes[i][j]
            if col in df.columns:
                vals = df[col].dropna()
                if len(vals):
                    ax.hist(vals, bins=20, color="#42A5F5", edgecolor="white")
                    ax.axvline(vals.median(), color="crimson", linestyle="--", linewidth=1.2,
                               label=f"median={vals.median():.3f}")
                    ax.legend(fontsize=8)
            ax.set_title(f"{meas_label} — {met_label}", fontsize=10)
            ax.set_xlabel(meas_label, fontsize=9)

    plt.tight_layout()
    fig.savefig(fig_dir / "inequity_distributions.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved inequity_distributions.png")


# ---------------------------------------------------------------------------
# Plot 4: Gini vs pop density (reference interval)
# ---------------------------------------------------------------------------

def _plot_gini_vs_density(df, ref_interval, fig_dir):
    pairs = [
        (f"gini_variety_{ref_interval}min",    f"Gini Variety {ref_interval}min"),
        (f"gini_total_dest_{ref_interval}min", f"Gini Total Dest {ref_interval}min"),
    ]
    present = [(c, l) for c, l in pairs if c in df.columns]
    if not present or "pop_density_ha" not in df.columns:
        return

    fig, axes = plt.subplots(1, len(present), figsize=(6 * len(present), 5))
    if len(present) == 1:
        axes = [axes]

    for ax, (y_col, y_label) in zip(axes, present):
        ax.scatter(df["pop_density_ha"], df[y_col], alpha=0.7, s=50,
                   color="#9C27B0", edgecolors="white")
        for _, row in df.iterrows():
            if pd.notna(row.get("pop_density_ha")) and pd.notna(row.get(y_col)):
                ax.annotate(str(row["city_name"]).split(",")[0],
                            (row["pop_density_ha"], row[y_col]), fontsize=6, alpha=0.7)
        ax.set_xlabel("Population density (inh/ha)")
        ax.set_ylabel(y_label)
        ax.set_title(f"Pop density vs {y_label}")

    plt.tight_layout()
    fig.savefig(fig_dir / "gini_vs_density.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved gini_vs_density.png")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_analysis(results_csv, output_dir):
    results_path = Path(results_csv)
    fig_dir = Path(output_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(results_path)
    df = df[df["status"] == "success"].copy()
    if df.empty:
        print("No successful cities in results — skipping analysis.")
        return

    intervals = _detect_intervals(df)
    ref = intervals[0] if intervals else None
    print(f"Running analysis on {len(df)} cities, intervals={intervals}, ref={ref} min…")

    _plot_diminishing_returns(df, intervals, fig_dir)
    if ref is not None:
        _plot_density_scatter(df, ref, fig_dir)
        _plot_inequity_distributions(df, ref, fig_dir)
        _plot_gini_vs_density(df, ref, fig_dir)

    print(f"Figures saved → {fig_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analysis.py <results_final.csv> [output_dir]")
        sys.exit(1)
    csv_path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(csv_path).parent)
    run_analysis(csv_path, out)
