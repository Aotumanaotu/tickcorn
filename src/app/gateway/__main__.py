"""Gateway process entry point.

Examples:
    python -m app.gateway --simulate -i C2611 --data-dir /tmp/gw-smoke
    python -m app.gateway --config-dir config --data-dir data
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.common.config import load_config
from app.common.logging import setup_logging
from app.common.runtime import load_web_settings
from app.gateway.process import run_gateway


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.gateway", description="CTP 行情网关独立进程")
    parser.add_argument("--simulate", action="store_true",
                        help="使用内置模拟行情源（无需 CTP SDK）")
    parser.add_argument("-i", "--instrument", action="append", default=None,
                        metavar="ID",
                        help="simulate 模式合约（可重复指定，默认 C2611）")
    parser.add_argument("--simulate-rate", type=float, default=2.0,
                        metavar="HZ", help="模拟行情频率 Hz（默认 2.0）")
    parser.add_argument("--simulate-seed", type=int, default=7, metavar="N",
                        help="模拟行情随机种子（默认 7）")
    parser.add_argument("--config-dir", type=Path, default=None,
                        help="配置目录（默认自动探测项目 config/）")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="数据目录（默认取 settings.yaml paths.data_dir）")
    parser.add_argument("--log-level", default="INFO", metavar="LEVEL",
                        help="日志级别（默认 INFO）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging(args.log_level.upper())
    config = load_config(config_dir=args.config_dir,
                         data_dir_override=args.data_dir)
    settings = load_web_settings(config)
    return asyncio.run(run_gateway(
        config, settings,
        simulate=args.simulate,
        simulate_instruments=args.instrument,
        simulate_rate_hz=args.simulate_rate,
        simulate_seed=args.simulate_seed))


if __name__ == "__main__":
    raise SystemExit(main())
