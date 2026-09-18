"""Public SimNow presets supplied from the official environment listing.

These are editable starting points, not credentials or a live service directory.
Operational overrides stay in the private settings file.
"""

PRESETS = [
    {"id": f"standard-{group}", "label": f"第一套 · 标准仿真 · 第{group}组",
     "source_kind": "simnow_standard", "broker_id": "9999",
     "fronts": [f"tcp://182.254.243.31:{30010 + group}"],
     "hours": "与实际生产环境一致；休市时不保证有新行情。"}
    for group in (1, 2, 3)
] + [
    {"id": "test", "label": "第二套 · API 测试（7×24）",
     "source_kind": "simnow_test", "broker_id": "9999",
     "fronts": ["tcp://182.254.243.31:40011"],
     "hours": "交易日 16:00～次日 09:00；非交易日 16:00～次日 12:00（北京时间）。仅供联调。"},
]


def validate_source(fronts, source_kind):
    """Prevent known endpoints being labeled as a different environment."""
    from app.common.exceptions import AppError
    from urllib.parse import urlsplit
    known = {urlsplit(front).netloc: p["source_kind"]
             for p in PRESETS for front in p["fronts"]}
    for front in fronts:
        endpoint = urlsplit(front).netloc
        if endpoint in known and known[endpoint] != source_kind:
            raise AppError("行情地址与数据来源不一致，请重新选择环境或修改地址")
        if endpoint in {"182.254.243.31:" + str(p) for p in (30001, 30002, 30003, 40001)}:
            raise AppError("填写的是交易前置，请使用 Market Front 行情前置")
