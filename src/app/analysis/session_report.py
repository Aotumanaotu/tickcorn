"""Research report generation: the `analyze` pipeline.

Pipeline (Phase 1):
    raw parquet -> clean df -> JumpEventClassifier -> event statistics
    -> pre-event feature analysis -> transition matrices -> OBI conditional
    tables -> figures (PNG) -> CSV exports -> summary.md + summary.html

Every run is registered in SQLite (analysis_runs + experiments) with the
full parameter set, code version, config hash and input file hashes, so
results are reproducible and auditable.
"""

from __future__ import annotations

import base64
import json
import html
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app import __version__
from app.core.features.derived import add_deltas
from app.core.classifier.jump_classifier import JumpEventClassifier
from app.common.config import AppConfig
from app.common.constants import STATE5_ORDER, STATE8_ORDER
from app.common.logging import get_logger
from app.common.schema import PartitionKey
from app.core.features.windows import add_window_features
from app.core.statistics.event_stats import (add_intraday_bucket, daily_summary,
                                        event_statistics, time_binned_stats)
from app.core.statistics.preevent import (compare_directions, headline_preevent_summary,
                                     preevent_feature_rows)
from app.core.statistics.transition import (direction_by_state, obi_conditional_table,
                                       state5_matrix, state8_matrix)
from app.storage.metadata_db import MetadataDB
from app.analysis.pipeline import load_clean_range
from app.storage.repo import StorageRepository
from app.visualization import plots

logger = get_logger("report.generator")


@dataclass
class AnalysisOptions:
    strict: bool = True
    mid_tolerance_ticks: float = 0.0
    lookbacks: tuple[int, ...] = (1, 2, 3, 5, 10)
    horizons: tuple[int, ...] = (1, 2, 3, 5)
    tick_size: Optional[float] = None
    title: str = ""
    raw_source: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "raw_source": self.raw_source,
            "strict_symmetric_quote_move": self.strict,
            "mid_tolerance_ticks": self.mid_tolerance_ticks,
            "lookbacks": list(self.lookbacks),
            "horizons": list(self.horizons),
            "tick_size": self.tick_size,
        }


@dataclass
class AnalysisResult:
    run_id: str
    output_dir: Path
    summary: dict = field(default_factory=dict)
    stats_session: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats_bucket: pd.DataFrame = field(default_factory=pd.DataFrame)
    transition5: pd.DataFrame = field(default_factory=pd.DataFrame)
    obi_tables: pd.DataFrame = field(default_factory=pd.DataFrame)
    preevent: pd.DataFrame = field(default_factory=pd.DataFrame)
    preevent_comp: pd.DataFrame = field(default_factory=pd.DataFrame)


# ---------------------------------------------------------------------

def run_analysis(config, instrument_id, days, options=None, db=None, repo=None, output_dir=None):
    from app.common.private_files import ProcessLock
    with ProcessLock(config.paths.data_dir / "collector.lock"):
        return _run_analysis(config, instrument_id, days, options, db, repo, output_dir)


def _run_analysis(config: AppConfig, instrument_id: str, days: list[str],
                 options: Optional[AnalysisOptions] = None,
                 db: Optional[MetadataDB] = None,
                 repo: Optional[StorageRepository] = None,
                 output_dir: Optional[Path] = None) -> AnalysisResult:
    options = options or AnalysisOptions()
    db = db or MetadataDB(config.paths.metadata_db)
    repo = repo or StorageRepository(config, db=db)

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    tick_size = options.tick_size or config.resolve_tick_size(instrument_id)
    clf = JumpEventClassifier(
        tick_size=tick_size,
        strict_symmetric_quote_move=options.strict,
        mid_tolerance_ticks=options.mid_tolerance_ticks,
        likely_bounce_requires_unit_move=(
            config.classifier.likely_bounce_requires_unit_move),
    )

    # ---------------- 1. load clean data --------------------------------
    keys = [PartitionKey(instrument_id, d) for d in days]
    existing = [k for k in keys if any(
        k2.trading_day == k.trading_day for k2 in repo.partitions(instrument_id))]
    if not existing:
        raise FileNotFoundError(
            f"No raw data found for {instrument_id} on {days}. "
            f"Run `python -m app collect --instrument {instrument_id}` first.")
    days = [k.trading_day for k in existing]

    if any(any(repo.store.staging_dir(k).glob("part-*.parquet")) for k in existing):
        raise ValueError("请先停止采集或执行 finalize，再分析已归档的数据")
    clean = load_clean_range(repo, instrument_id, days, tick_size=tick_size,
                                 raw_source=options.raw_source)
    df = clean.df
    logger.info("loaded %d clean snapshots for %s %s (dup=%d invalid=%d)",
                len(df), instrument_id, days, clean.dropped_duplicates,
                clean.dropped_invalid)

    if df.empty:
        raise ValueError("清洗后没有有效行情，无法生成报告")
    # Never infer an event across different days, collection batches or long gaps.
    boundary = df["exchange_ts_ns"].diff().gt(60_000_000_000)
    for col in ("trading_day", "batch_id", "trading_session", "instrument_id"):
        if col in df:
            boundary |= df[col].ne(df[col].shift())
    df["segment_id"] = boundary.cumsum()
    segments = []
    for _, segment in df.groupby("segment_id", sort=False):
        segment = segment.reset_index(drop=True)
        segment = add_deltas(segment)
        segment = pd.concat([segment, clf.classify_dataframe(segment)], axis=1)
        segments.append(add_window_features(segment, list(options.lookbacks), tick_size))
    df = pd.concat(segments, ignore_index=True)
    df = add_intraday_bucket(df, config)

    # ---------------- 4. event statistics ---------------------------------
    stats_day = event_statistics(df, instrument_id, ",".join(days), config,
                                 group_by="none")
    stats_session = event_statistics(df, instrument_id, ",".join(days), config,
                                     group_by="session")
    stats_bucket = event_statistics(df, instrument_id, ",".join(days), config,
                                    group_by="bucket")
    binned = time_binned_stats(df, config.analysis.intraday_bin_minutes)

    # ---------------- 5. pre-event analysis --------------------------------
    feats = preevent_feature_rows(df, list(options.lookbacks))
    comp = compare_directions(feats, config.analysis.min_samples_test)
    headline_pre = headline_preevent_summary(comp, list(options.lookbacks))

    # ---------------- 6. transition matrices -------------------------------
    t5 = state5_matrix(df)
    t8 = state8_matrix(df)
    dir_state_tables = [direction_by_state(df, h) for h in options.horizons]
    obi_tables = pd.concat(
        [obi_conditional_table(df, list(config.analysis.obi_bucket_edges), h)
         for h in options.horizons], ignore_index=True)

    # ---------------- 7. outputs -------------------------------------------
    day_label = days[0] if len(days) == 1 else f"{days[0]}..{days[-1]}"
    out_dir = Path(output_dir) if output_dir else (config.paths.reports_dir
               / f"{instrument_id}_{day_label}_{run_id}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("输出目录非空，请选择新目录")
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    figures = {}
    figures["price_and_quotes"] = plots.plot_price_and_quotes(df, fig_dir / "price_and_quotes.png")
    figures["bounce_ratio"] = plots.plot_bounce_ratio(binned, fig_dir / "bounce_ratio.png")
    figures["genuine_move_ratio"] = plots.plot_genuine_move_ratio(binned, fig_dir / "genuine_move_ratio.png")
    figures["obi_vs_direction"] = plots.plot_obi_vs_direction(feats, fig_dir / "obi_vs_direction.png")
    figures["microprice_vs_direction"] = plots.plot_microprice_vs_direction(feats, fig_dir / "microprice_vs_direction.png")
    figures["intraday_pattern"] = plots.plot_intraday_pattern(binned, fig_dir / "intraday_pattern.png")
    figures["obi_probability"] = plots.plot_obi_probability(obi_tables, fig_dir / "obi_probability.png")

    stats_day.to_csv(out_dir / "event_statistics.csv", index=False)
    stats_session.to_csv(out_dir / "event_statistics_by_session.csv", index=False)
    stats_bucket.to_csv(out_dir / "event_statistics_by_bucket.csv", index=False)
    binned.to_csv(out_dir / "intraday_time_bins.csv", index=False)
    t5.to_csv(out_dir / "transition_matrix.csv")
    t8.to_csv(out_dir / "transition_matrix_fine.csv")
    pd.concat(dir_state_tables, ignore_index=True).to_csv(
        out_dir / "next_move_by_state.csv", index=False)
    obi_tables.to_csv(out_dir / "obi_probability.csv", index=False)
    comp.to_csv(out_dir / "feature_statistics.csv", index=False)
    if not feats.empty:
        feats.to_csv(out_dir / "preevent_features.csv", index=False)

    summary = _build_summary(config, instrument_id, days, tick_size, options,
                             stats_day, comp, headline_pre, t5, obi_tables,
                             clean.stats, binned)

    summary["raw_sources"] = sorted(str(x) for x in df["raw_source"].dropna().unique())
    (out_dir / "summary.md").write_text(
        _render_markdown(config, summary, stats_session, t5, obi_tables, comp),
        encoding="utf-8")
    (out_dir / "summary.html").write_text(
        _render_html(config, summary, figures, stats_session, stats_bucket,
                     t5, obi_tables, comp),
        encoding="utf-8")
    (out_dir / "run_metadata.json").write_text(json.dumps(
        {
            "run_id": run_id, "app_version": __version__,
            "config_hash": config.config_hash,
            "instrument": instrument_id, "trading_days": days,
            "raw_sources": summary["raw_sources"],
            "tick_size": tick_size,
            "classifier": clf.__dict__ | options.to_dict(),
            "input_hashes": repo.input_hashes(existing),
            "clean_stats": clean.stats,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    _latest_symlink(config, out_dir)

    # ---------------- 8. register run ---------------------------------------
    params = {"instrument": instrument_id, "days": days,
              "tick_size": tick_size, **options.to_dict(),
              "config_hash": config.config_hash}
    exp_id = db.create_experiment("phase1-microstructure-analysis", params,
                                  code_version=__version__)
    db_run_id = db.start_analysis_run("analyze", params, instrument_id, days,
                                      repo.input_hashes(existing), exp_id)
    db.finish_analysis_run(db_run_id, str(out_dir), status="ok")

    logger.info("analysis complete: %s", out_dir)
    return AnalysisResult(run_id=run_id, output_dir=out_dir, summary=summary,
                          stats_session=stats_session, transition5=t5,
                          obi_tables=obi_tables, preevent=feats,
                          preevent_comp=comp)


def _latest_symlink(config: AppConfig, out_dir: Path) -> None:
    latest = config.paths.reports_dir / "latest"
    try:
        if latest.is_symlink():
            latest.unlink()
        latest.symlink_to(out_dir, target_is_directory=True)
    except OSError:
        pass


def _build_summary(config, instrument_id, days, tick_size, options, stats_day,
                   comp, headline_pre, t5, obi_tables, clean_stats,
                   binned) -> dict:
    row = stats_day.iloc[0] if len(stats_day) else {}
    one_tick = row.get("one_tick_last_change_count", 0)
    high = row.get("high_confidence_bounce_count", 0)
    likely = row.get("likely_bounce_count", 0)
    genuine = (row.get("genuine_quote_move_up_count", 0)
               + row.get("genuine_quote_move_down_count", 0))
    amb = row.get("ambiguous_count", 0)

    sig_features = []
    if not comp.empty:
        sig = comp[comp["significant"]] if "significant" in comp else pd.DataFrame()
        sig_features = sig["feature"].tolist() if len(sig) else []

    # strongest OBI edge (for the economic-value discussion)
    best_edge = float("nan")
    if not obi_tables.empty and "p_up_minus_p_down" in obi_tables:
        h_max = obi_tables["horizon"].max()
        sub = obi_tables[obi_tables["horizon"] == h_max]
        if len(sub) and sub["p_up_minus_p_down"].notna().any():
            best_edge = float(np.nanmax(np.abs(sub["p_up_minus_p_down"].values)))

    return {
        "instrument": instrument_id,
        "days": days,
        "tick_size": tick_size,
        "strict": options.strict,
        "total_snapshots": int(row.get("total_snapshots", 0)),
        "transitions": int(row.get("transitions", 0)),
        "one_tick_last_changes": int(one_tick),
        "high_confidence_bounce": int(high),
        "likely_bounce": int(likely),
        "genuine_moves": int(genuine),
        "ambiguous": int(amb),
        "one_tick_bounce": int(row.get("one_tick_bounce_count", 0)),
        "one_tick_genuine": int(row.get("one_tick_genuine_count", 0)),
        "one_tick_ambiguous": int(row.get("one_tick_ambiguous_count", 0)),
        "bounce_ratio": row.get("bounce_ratio", float("nan")),
        "genuine_move_ratio": row.get("genuine_move_ratio", float("nan")),
        "ambiguous_ratio": row.get("ambiguous_ratio", float("nan")),
        "preevent": headline_pre,
        "significant_preevent_features": sig_features,
        "best_obi_edge": best_edge,
        "clean_stats": clean_stats,
        "mean_spread_ticks": row.get("mean_spread_ticks", float("nan")),
    }


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------

def _fmt(x, pct=False, digits=4):
    if x is None or (isinstance(x, float) and (np.isnan(x))):
        return "n/a"
    if pct:
        return f"{100 * x:.1f}%"
    return f"{x:,.{digits}f}" if isinstance(x, (int, float)) else str(x)


def _answer_questions_md(s: dict) -> str:
    q1 = (
        f"在 {s['instrument']} 的 {', '.join(s['days'])} 交易日中，共观察到 "
        f"**{s['one_tick_last_changes']:,}** 次 1-Tick LastPrice 跳动。其中 "
        f"**{_fmt(s['bounce_ratio'], pct=True)}** 属于 Bid-Ask Bounce"
        f"（HIGH_CONFIDENCE_BOUNCE + LIKELY_BOUNCE；仅统计 1-Tick 变化子集，共 {s['one_tick_bounce']:,} 次），"
        f"{_fmt(s['genuine_move_ratio'], pct=True)} 为 Genuine Quote Move（真实报价移动），"
        f"{_fmt(s['ambiguous_ratio'], pct=True)} 无法确定（AMBIGUOUS，保守分类）。"
    )
    pre = s.get("preevent") or {}
    if pre:
        q2 = (
            "真实 Quote Move 之前的盘口特征（UP vs DOWN，t 检验 / Mann-Whitney U）：\n\n"
            f"- 上涨前平均 OBI1 = **{_fmt(pre.get('mean_obi_before_up'))}**，"
            f"下跌前平均 OBI1 = **{_fmt(pre.get('mean_obi_before_down'))}**"
            f"（p = {_fmt(pre.get('obi_p_value'), digits=6)}）\n"
            f"- 上涨前 Microprice 偏移 = **{_fmt(pre.get('mean_microprice_dev_before_up'))}** tick，"
            f"下跌前 = **{_fmt(pre.get('mean_microprice_dev_before_down'))}** tick"
            f"（p = {_fmt(pre.get('microprice_dev_p_value'), digits=6)}）\n"
        )
        if s.get("significant_preevent_features"):
            q2 += ("- 统计显著（p<0.05）的盘口特征: "
                   + ", ".join(s["significant_preevent_features"]) + "\n")
        else:
            q2 += "- 未发现统计显著的盘口前置特征\n"
    else:
        q2 = "- 样本中没有足够的 Genuine Quote Move 事件进行前置特征检验。\n"
    q3 = (
        "**本阶段不回答该问题。** 第一阶段仅度量统计可预测性 (Statistical "
        "Predictability)。经济价值 (Economic Profitability) 需要第三阶段的 "
        "ExecutionSimulator（含手续费、滑点、延迟、成交概率），并做敏感性分析后才能回答。"
        f"参考：OBI 条件概率表中最强的方向优势为 |P(UP)-P(DOWN)| = "
        f"{_fmt(s.get('best_obi_edge'))}。该概率差不能直接与货币交易成本比较，"
        "需要结合成交机制、价格变化幅度和手续费评估预期净收益。"
    )
    return q1, q2, q3


def _render_markdown(config, s, stats_session, t5, obi_tables, comp) -> str:
    q1, q2, q3 = _answer_questions_md(s)
    lines = [
        f"# {config.report.title_prefix}",
        "",
        f"- 合约: **{s['instrument']}**  交易日: **{', '.join(s['days'])}**",
        f"- TickSize: {s['tick_size']}（来自配置）  分类模式: "
        f"{'严格 (strict)' if s['strict'] else '宽松 (loose)'}",
        f"- 快照总数: {s['total_snapshots']:,}（清洗后；重复 {s['clean_stats'].get('dropped_duplicates', 0):,}，"
        f"无效 {s['clean_stats'].get('dropped_invalid', 0):,}）",
        "",
        f"数据来源（用户配置）：{', '.join(s.get('raw_sources', []))}。测试或合成数据仅供联调。",
        "",
        "## 三个核心问题",
        "",
        "### Q1: 1-Tick LastPrice 跳动中有多少未伴随真实报价移动？",
        q1,
        "",
        "### Q2: 真实 Quote Move 之前是否存在统计显著的盘口特征？",
        q2,
        "",
        "### Q3: 考虑交易成本后是否仍有经济价值？",
        q3,
        "",
        "## 分时段统计",
        "",
        stats_session.to_markdown(index=False) if len(stats_session) else "(无数据)",
        "",
        "## 状态转移矩阵 P(next | current)（5 态，AMBIGUOUS 已排除）",
        "",
        t5.to_markdown() if len(t5) else "(无数据)",
        "",
        "## OBI 条件概率表（最强视野）",
        "",
    ]
    if len(obi_tables):
        h_max = obi_tables["horizon"].max()
        lines.append(obi_tables[obi_tables["horizon"] == h_max].to_markdown(
            index=False))
    else:
        lines.append("(无数据)")
    lines += ["", "## 事件前特征对比（UP vs DOWN）", ""]
    if len(comp):
        show = comp[["feature", "n_up", "n_down", "mean_up", "mean_down",
                     "p_ttest", "p_mannwhitney", "cohens_d", "significant"]]
        lines.append(show.to_markdown(index=False))
    else:
        lines.append("(无数据)")
    return "\n".join(lines) + "\n"


def _png_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _render_html(config, s, figures, stats_session, stats_bucket, t5,
                 obi_tables, comp) -> str:
    q1, q2, q3 = _answer_questions_md(s)
    figs = "".join(
        f'<figure><img src="data:image/png;base64,{_png_b64(p)}" alt="{name}">'
        f"<figcaption>{name}</figcaption></figure>"
        for name, p in figures.items())

    def table(df, floatfmt="{:.4g}", max_rows=40):
        if df is None or not len(df):
            return "<p class='muted'>(无数据)</p>"
        d = df.head(max_rows)
        return d.to_html(index=False, border=0, classes="tbl",
                         float_format=lambda x: f"{x:.4g}")

    h_max = obi_tables["horizon"].max() if len(obi_tables) else None
    obi_show = (obi_tables[obi_tables["horizon"] == h_max]
                if h_max is not None else obi_tables)

    cards = "".join(
        f"<div class='card'><div class='card-v'>{v}</div>"
        f"<div class='card-k'>{k}</div></div>"
        for k, v in [
            ("1-tick LastPrice 跳动", f"{s['one_tick_last_changes']:,}"),
            ("Bounce Ratio", _fmt(s["bounce_ratio"], pct=True)),
            ("Genuine Move Ratio", _fmt(s["genuine_move_ratio"], pct=True)),
            ("Ambiguous Ratio", _fmt(s["ambiguous_ratio"], pct=True)),
            ("平均价差 (ticks)", _fmt(s["mean_spread_ticks"], digits=2)),
        ])

    comp_show = ""
    if len(comp):
        comp_show = comp[["feature", "n_up", "n_down", "mean_up", "mean_down",
                          "p_ttest", "p_mannwhitney", "cohens_d",
                          "significant"]].to_html(
            index=False, border=0, classes="tbl",
            float_format=lambda x: f"{x:.4g}")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{config.report.title_prefix} - {s['instrument']} {s['days'][0]}</title>
<style>
 body {{ font-family: -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif;
        margin: 0; background: #f6f7f9; color: #1c2733; }}
 header {{ background: #10263b; color: #fff; padding: 18px 28px; }}
 header h1 {{ margin: 0; font-size: 20px; }}
 header .meta {{ color: #9fb3c8; font-size: 12px; margin-top: 6px; }}
 main {{ padding: 20px 28px; max-width: 1180px; margin: 0 auto; }}
 .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 14px 0 22px; }}
 .card {{ background: #fff; border-radius: 10px; padding: 12px 18px;
         box-shadow: 0 1px 3px rgba(16,38,59,.12); min-width: 150px; }}
 .card-v {{ font-size: 22px; font-weight: 700; }}
 .card-k {{ font-size: 12px; color: #5b6b7b; }}
 section {{ background: #fff; border-radius: 10px; padding: 16px 20px;
           margin: 16px 0; box-shadow: 0 1px 3px rgba(16,38,59,.12); }}
 h2 {{ font-size: 15px; border-left: 4px solid #2b6cb0; padding-left: 8px; }}
 .q {{ font-weight: 600; color: #2b6cb0; }}
 .tbl {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
 .tbl th, .tbl td {{ padding: 4px 8px; border-bottom: 1px solid #e6ebf1;
                    text-align: right; }}
 .tbl th:first-child, .tbl td:first-child {{ text-align: left; }}
 figure {{ margin: 10px 0; }}
 figure img {{ max-width: 100%; border: 1px solid #e6ebf1; border-radius: 6px; }}
 figcaption {{ font-size: 11px; color: #5b6b7b; }}
 .muted {{ color: #5b6b7b; }}
 footer {{ color: #8aa0b5; font-size: 11px; padding: 16px 28px 40px; }}
</style>
</head>
<body>
<header>
 <h1>{config.report.title_prefix}</h1>
 <div class="meta">合约 {s['instrument']} · 交易日 {", ".join(s['days'])} ·
 TickSize {s['tick_size']} · 分类模式 {"严格" if s['strict'] else "宽松"} ·
 快照 {s['total_snapshots']:,} · 术语: CTP Market Snapshot（非逐笔订单流）</div>
</header>
<main>
<p>数据来源（用户配置）：{html.escape(", ".join(s.get("raw_sources", [])))}。测试或合成数据仅供联调。</p>
 <div class="cards">{cards}</div>

 <section>
  <h2>核心问题一：1-Tick LastPrice 跳动中有多少未伴随真实报价移动？</h2>
  <p>{q1}</p>
 </section>
 <section>
  <h2>核心问题二：真实 Quote Move 之前是否存在统计显著的盘口特征？</h2>
  <p>{q2.replace(chr(10), '<br>')}</p>
  {comp_show}
 </section>
 <section>
  <h2>核心问题三：考虑交易成本后是否仍有经济价值？</h2>
  <p>{q3}</p>
 </section>

 <section><h2>分时段事件统计</h2>{table(stats_session)}</section>
 <section><h2>开盘/收盘窗口统计</h2>{table(stats_bucket)}</section>
 <section><h2>状态转移矩阵 P(next | current)</h2>{table(t5.reset_index())}</section>
 <section><h2>OBI 条件概率（视野 h={h_max}）</h2>{table(obi_show)}</section>
 <section><h2>图表</h2>{figs}</section>
</main>
<footer>由 corn-tick-microstructure v{__version__} 自动生成 ·
原始数据不可变存储 (Parquet) · 参数与输入哈希见 run_metadata.json</footer>
</body>
</html>"""
