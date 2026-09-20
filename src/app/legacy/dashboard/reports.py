"""Private, asynchronous report generation and explicitly scoped downloads."""

from datetime import date, datetime
import json
from pathlib import Path
import re
import shutil
import threading
import uuid
import zipfile
from zoneinfo import ZoneInfo

from app.common.exceptions import AppError
from app.common.logging import get_logger
from app.common.private_files import write_private_json
from app.storage.parquet_store import RawParquetStore

logger = get_logger("dashboard.reports")
SOURCES = {"simnow_test": "SimNow API 测试环境", "simnow_standard": "SimNow 标准仿真环境"}
DOWNLOADS = {"html": ("summary.html", "text/html; charset=utf-8"),
             "md": ("summary.md", "text/markdown; charset=utf-8"),
             "zip": ("report.zip", "application/zip")}


def partitions(config):
    store = RawParquetStore(config.paths.raw_dir)
    return [{"instrument": k.instrument_id, "day": k.trading_day,
             "staging": any(store.staging_dir(k).glob("part-*.parquet"))}
            for k in store.list_partitions()]


def briefing(manager):
    """Build from an allowlist; never serialize settings, live login or errors."""
    status = manager.status()
    live = manager.get_live_state()
    state = live.to_json() if live else {}
    disk = shutil.disk_usage(manager.config.paths.data_dir)
    parts = partitions(manager.config)
    pending = sum(p["staging"] for p in parts)
    lines = ["玉米快照采集 · 运行简报",
             "生成时间：" + datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
             "采集状态：" + ("运行中" if status["running"] else "正在停止" if status["stopping"] else "已停止"),
             "以下实时计数属于最近一次采集批次，不代表全部历史数据。",
             f"磁盘使用：{100 * disk.used / disk.total:.1f}%；可用 {disk.free / 2**30:.2f} GiB",
             f"数据分区：{len(parts)}；待整理分区：{pending}"]
    for inst, snap in state.get("instruments", {}).items():
        ratio = snap.get("bounce_ratio")
        lines.append(f"{inst}：{snap.get('msg_count', 0)} 条；"
                     f"{snap.get('rate_per_min', 0)} 条/分钟；"
                     f"1-tick 变化 {snap.get('one_tick_last_changes', 0)} 次；"
                     + (f"反弹占比 {ratio:.1%}" if ratio is not None else "反弹占比暂无"))
    if pending:
        lines.append("采集中的 staging 是正常缓冲落盘；停止后仍有残留时请检查归档状态。"
                     if status["running"] or status["stopping"] else
                     "存在尚未归档的数据，请检查停止结果；必要时执行 finalize。")
    if status["error"]:
        lines.append("最近采集出现错误，请在面板检查状态。")
    lines.append("历史统计请停止采集后，选择合约、交易日和数据来源生成分析报告。")
    return {"text": "\n".join(lines)}


class ReportStore:
    def __init__(self, config):
        self.config = config
        self.root = config.paths.reports_dir
        self._lock = threading.RLock()
        self._thread = None
        self._current = None

    @property
    def busy(self):
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def list(self):
        jobs = []
        with self._lock:
            for path in sorted(self.root.glob("web-*/job.json")):
                if path.is_symlink() or path.parent.is_symlink():
                    continue
                try:
                    job = json.loads(path.read_text())
                    if job["id"] != path.parent.name:
                        continue
                    if job["status"] == "running" and job["id"] != self._current:
                        job["status"] = "interrupted"
                        job["error"] = "生成过程中服务已重启，请重新生成"
                    jobs.append(job)
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:50]

    def start(self, payload):
        inst, day, source = (payload.get(k) for k in ("instrument", "day", "source"))
        if not isinstance(inst, str) or not re.fullmatch(r"[A-Z]{1,8}[0-9]{3,4}", inst):
            raise AppError("请选择已有数据的合约")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise AppError("请选择 YYYY-MM-DD 格式的交易日")
        try:
            date.fromisoformat(day)
        except ValueError:
            raise AppError("交易日无效") from None
        if not isinstance(source, str) or source not in SOURCES:
            raise AppError("请选择明确的数据来源")
        chosen = next((p for p in partitions(self.config)
                       if p["instrument"] == inst and p["day"] == day), None)
        if chosen is None:
            raise AppError("所选合约和交易日没有数据")
        if chosen["staging"]:
            raise AppError("仍有待归档分片，请等待停止保存完成；必要时先执行 finalize")
        with self._lock:
            if self.busy:
                raise AppError("已有报告正在生成，请稍候")
            job = {"id": "web-" + uuid.uuid4().hex, "status": "running",
                   "instrument": inst, "day": day, "source": source,
                   "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")}
            directory = self.root / job["id"]
            directory.mkdir(parents=True, mode=0o700)
            write_private_json(directory / "job.json", job)
            self._current = job["id"]
            self._thread = threading.Thread(target=self._generate, args=(job, directory),
                                            name="report-generator", daemon=True)
            self._thread.start()
            return dict(job)

    def _generate(self, job, directory):
        try:
            artifacts = directory / "artifacts"
            from app.analysis.session_report import AnalysisOptions, run_analysis
            options = AnalysisOptions(
                raw_source="ctp:" + job["source"],
                mid_tolerance_ticks=self.config.classifier.mid_tolerance_ticks,
                lookbacks=tuple(self.config.analysis.lookbacks),
                horizons=tuple(self.config.analysis.horizons))
            result = run_analysis(self.config, job["instrument"], [job["day"]],
                                  options, output_dir=artifacts)
            # Only report artifacts are exported; no runtime config, logs or raw data.
            with zipfile.ZipFile(artifacts / "report.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                files = [artifacts / n for n in ("summary.html", "summary.md", "run_metadata.json")]
                files += list(artifacts.glob("*.csv")) + list((artifacts / "figures").glob("*.png"))
                for path in files:
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, path.relative_to(artifacts))
            job.update(status="ready", snapshots=int(result.summary["total_snapshots"]))
        except Exception as exc:
            logger.error("report generation failed (%s)", type(exc).__name__)
            job.update(status="failed", error="生成失败：请确认所选来源有有效行情、数据已归档且磁盘和内存充足")
        finally:
            with self._lock:
                try:
                    write_private_json(directory / "job.json", job)
                finally:
                    self._current = None

    def download(self, job_id, kind):
        if not re.fullmatch(r"web-[a-f0-9]{32}", job_id) or kind not in DOWNLOADS:
            raise AppError("报告或下载格式无效")
        directory = self.root / job_id
        if directory.is_symlink() or not (directory / "job.json").is_file():
            raise AppError("报告不存在")
        manifest = directory / "job.json"
        if manifest.is_symlink():
            raise AppError("报告不存在")
        job = json.loads(manifest.read_text())
        if job.get("status") != "ready":
            raise AppError("报告尚未生成完成")
        name, mime = DOWNLOADS[kind]
        path = directory / "artifacts" / name
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise AppError("报告文件不存在")
        return path, mime
