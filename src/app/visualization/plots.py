"""Matplotlib figures for the research report (PNG output)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402  (headless)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.common.logging import get_logger

logger = get_logger("visualization")

plt.rcParams.update({
    "figure.dpi": 110,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "font.size": 9,
    "axes.titlesize": 10,
})

_EVENT_COLORS = {
    "GENUINE_QUOTE_MOVE_UP": "#d62728",
    "GENUINE_QUOTE_MOVE_DOWN": "#1f77b4",
    "HIGH_CONFIDENCE_BOUNCE_UP": "#ff9896",
    "HIGH_CONFIDENCE_BOUNCE_DOWN": "#aec7e8",
    "LIKELY_BOUNCE_UP": "#c7c7c7",
    "LIKELY_BOUNCE_DOWN": "#c7c7c7",
}


def _time_axis(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["exchange_ts_ns"], unit="ns").dt.tz_localize(
        "UTC").dt.tz_convert("Asia/Shanghai")


def _downsample(df: pd.DataFrame, max_points: int = 6000) -> pd.DataFrame:
    if len(df) <= max_points:
        return df
    step = max(1, len(df) // max_points)
    return df.iloc[::step]


def plot_price_and_quotes(df: pd.DataFrame, out: Path,
                          max_points: int = 6000) -> Path:
    d = _downsample(df, max_points)
    t = _time_axis(d)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(t, d["bid_price1"], lw=0.8, color="#2ca02c", label="Bid1")
    ax.plot(t, d["ask_price1"], lw=0.8, color="#d62728", label="Ask1")
    ax.plot(t, d["last_price"], lw=0.8, color="#1f77b4", alpha=0.9,
            label="LastPrice")
    ax.plot(t, d["mid_price"], lw=0.7, color="#7f7f7f", ls="--", alpha=0.8,
            label="MidPrice")
    if "label" in d.columns:
        for lab, color in _EVENT_COLORS.items():
            m = d["label"] == lab
            if m.any():
                ax.scatter(t[m], d.loc[m, "mid_price"], s=6, color=color,
                           label=lab.replace("GENUINE_QUOTE_MOVE", "QUOTE")
                                    .replace("HIGH_CONFIDENCE_", "HIGH_"),
                           zorder=3)
    ax.set_ylabel("price")
    ax.set_title("Price and best quotes (raw snapshot view)")
    ax.legend(loc="upper left", fontsize=7, ncol=4)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_ratio_over_time(binned: pd.DataFrame, col: str, out: Path,
                         title: str, ylabel: str) -> Path:
    fig, ax = plt.subplots(figsize=(11, 3.6))
    if len(binned):
        x = binned["time_hhmm"]
        ax.plot(x, binned[col], marker="o", ms=3, lw=1, color="#1f77b4")
        denom = binned.get("one_tick_last_change_count")
        if denom is not None:
            scale = max(1, int(np.nan_to_num(denom.max())))
            ax2 = ax.twinx()
            ax2.bar(x, denom, alpha=0.15, color="#7f7f7f", width=0.8)
            ax2.set_ylabel("1-tick changes (count)", fontsize=8)
            ax2.set_ylim(0, scale * 4)
            ax2.grid(False)
    ax.set_ylim(0, 1)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.tick_params(axis="x", rotation=60, labelsize=7)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_bounce_ratio(binned: pd.DataFrame, out: Path) -> Path:
    return plot_ratio_over_time(
        binned, "bounce_ratio", out,
        "Bid-Ask Bounce Ratio over the day "
        "(bounce events / 1-tick LastPrice changes)", "bounce ratio")


def plot_genuine_move_ratio(binned: pd.DataFrame, out: Path) -> Path:
    return plot_ratio_over_time(
        binned, "genuine_move_ratio", out,
        "Genuine Quote Move Ratio over the day", "genuine move ratio")


def plot_obi_vs_direction(feats: pd.DataFrame, out: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    if feats.empty:
        for ax in axes:
            ax.text(0.5, 0.5, "no genuine quote moves in sample",
                    ha="center", va="center")
    else:
        up = feats[feats["direction"] == 1]["obi1"].dropna()
        down = feats[feats["direction"] == -1]["obi1"].dropna()
        axes[0].hist(up, bins=40, alpha=0.6, color="#d62728", label="before UP")
        axes[0].hist(down, bins=40, alpha=0.6, color="#1f77b4", label="before DOWN")
        axes[0].set_title("OBI1 distribution before genuine moves")
        axes[0].set_xlabel("OBI1 (t-1)")
        axes[0].legend(fontsize=8)
        groups = [(f"UP (n={len(up)})", up, "#d62728"),
                  (f"DOWN (n={len(down)})", down, "#1f77b4")]
        groups = [(lbl, d, c) for lbl, d, c in groups if len(d)]
        if groups:
            bp = axes[1].boxplot([d for _, d, _ in groups],
                                 tick_labels=[lbl for lbl, _, _ in groups],
                                 showmeans=True, patch_artist=True)
            for item, (_, _, color) in zip(bp["boxes"], groups):
                item.set_facecolor(color)
                item.set_alpha(0.5)
        axes[1].set_title("OBI1 before genuine moves")
        axes[1].set_ylabel("OBI1")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_microprice_vs_direction(feats: pd.DataFrame, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7, 3.6))
    if feats.empty:
        ax.text(0.5, 0.5, "no genuine quote moves in sample",
                ha="center", va="center")
    else:
        up = feats[feats["direction"] == 1]["microprice_dev_ticks"].dropna()
        down = feats[feats["direction"] == -1]["microprice_dev_ticks"].dropna()
        groups = [(f"UP (n={len(up)})", up, "#d62728"),
                  (f"DOWN (n={len(down)})", down, "#1f77b4")]
        groups = [(lbl, d, c) for lbl, d, c in groups if len(d)]
        if groups:
            bp = ax.boxplot([d for _, d, _ in groups],
                            tick_labels=[lbl for lbl, _, _ in groups],
                            showmeans=True, patch_artist=True)
            for item, (_, _, color) in zip(bp["boxes"], groups):
                item.set_facecolor(color)
                item.set_alpha(0.5)
        ax.axhline(0, color="k", lw=0.6)
        ax.set_ylabel("microprice - mid (ticks)")
        ax.set_title("Microprice deviation before genuine quote moves")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_intraday_pattern(binned: pd.DataFrame, out: Path) -> Path:
    fig, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    if len(binned):
        x = binned["time_hhmm"]
        axes[0].plot(x, binned["mean_spread_ticks"], marker="o", ms=3,
                     color="#2ca02c")
        axes[0].set_ylabel("mean spread (ticks)")
        axes[0].set_title("Intraday pattern: spread / OBI / snapshot rate")
        axes[1].plot(x, binned["mean_obi1"], marker="o", ms=3, color="#ff7f0e")
        axes[1].axhline(0, color="k", lw=0.6)
        axes[1].set_ylabel("mean OBI1")
        axes[2].plot(x, binned["snapshot_rate_per_min"], marker="o", ms=3,
                     color="#9467bd")
        axes[2].set_ylabel("snapshots / min")
        axes[2].tick_params(axis="x", rotation=60, labelsize=7)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_obi_probability(obi_tables: pd.DataFrame, out: Path) -> Path:
    """obi_tables: concatenated per-horizon conditional tables."""
    fig, ax = plt.subplots(figsize=(11, 4))
    if obi_tables.empty or "p_up" not in obi_tables:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
    else:
        horizons = sorted(obi_tables["horizon"].unique())
        width = 0.8 / max(1, len(horizons))
        buckets = obi_tables["obi_bucket"].unique()
        xs = np.arange(len(buckets))
        for j, h in enumerate(horizons):
            sub = obi_tables[obi_tables["horizon"] == h].set_index("obi_bucket")
            sub = sub.reindex(buckets)
            ax.bar(xs + j * width, sub["p_up"].fillna(0), width=width,
                   label=f"P(QUOTE_UP | OBI), h={h}")
        ax.set_xticks(xs + width * (len(horizons) - 1) / 2)
        ax.set_xticklabels(buckets, rotation=45, fontsize=8)
        ax.set_ylabel("P(next genuine move UP)")
        ax.set_title("Conditional probability of UP quote move given OBI1 bucket")
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out
