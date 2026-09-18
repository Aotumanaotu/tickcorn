"""ctypes binding for the CTP MdApi shim (_mdshim.so).

The DepthMarketData ctypes structure is transcribed from
third_party/ctp/v6.7.13_linux64/include/ThostFtdcUserApiStruct.h and is
VERIFIED at load time against offsetof()/sizeof() values exported by the
compiled shim (which is built against the very same header), so a field
transcription error cannot silently corrupt data.
"""

from __future__ import annotations

import ctypes
import threading
from pathlib import Path
from typing import Callable, Optional

from app.common.exceptions import CollectorError
from app.common.logging import get_logger
from app.common.schema import CTP_FIELD_TO_COLUMN

logger = get_logger("collector.ctp_binding")

NATIVE_DIR = Path(__file__).resolve().parent / "native"
SHIM_PATH = NATIVE_DIR / "_mdshim.so"

# ---------------------------------------------------------------------
# Events (must match md_shim.cpp)
# ---------------------------------------------------------------------
MD_EVT_FRONT_CONNECTED = 1
MD_EVT_FRONT_DISCONNECTED = 2
MD_EVT_HEARTBEAT_WARNING = 3
MD_EVT_RSP_USER_LOGIN = 4
MD_EVT_RSP_USER_LOGOUT = 5
MD_EVT_RSP_ERROR = 6
MD_EVT_RSP_SUB_MARKET_DATA = 7
MD_EVT_RSP_UNSUB_MARKET_DATA = 8
MD_EVT_RTN_DEPTH_MARKET_DATA = 9
MD_EVT_RTN_FOR_QUOTE_RSP = 10


# ---------------------------------------------------------------------
# Structs
# ---------------------------------------------------------------------

def _char(n: int):
    return ctypes.c_char * n


class CTPDepthMarketData(ctypes.Structure):
    """CThostFtdcDepthMarketDataField (CTP v6.7.13, linux64)."""

    _fields_ = [
        ("TradingDay", _char(9)),
        ("reserve1", _char(31)),
        ("ExchangeID", _char(9)),
        ("reserve2", _char(31)),
        ("LastPrice", ctypes.c_double),
        ("PreSettlementPrice", ctypes.c_double),
        ("PreClosePrice", ctypes.c_double),
        ("PreOpenInterest", ctypes.c_double),
        ("OpenPrice", ctypes.c_double),
        ("HighestPrice", ctypes.c_double),
        ("LowestPrice", ctypes.c_double),
        ("Volume", ctypes.c_int),
        ("Turnover", ctypes.c_double),
        ("OpenInterest", ctypes.c_double),
        ("ClosePrice", ctypes.c_double),
        ("SettlementPrice", ctypes.c_double),
        ("UpperLimitPrice", ctypes.c_double),
        ("LowerLimitPrice", ctypes.c_double),
        ("PreDelta", ctypes.c_double),
        ("CurrDelta", ctypes.c_double),
        ("UpdateTime", _char(9)),
        ("UpdateMillisec", ctypes.c_int),
        ("BidPrice1", ctypes.c_double),
        ("BidVolume1", ctypes.c_int),
        ("AskPrice1", ctypes.c_double),
        ("AskVolume1", ctypes.c_int),
        ("BidPrice2", ctypes.c_double),
        ("BidVolume2", ctypes.c_int),
        ("AskPrice2", ctypes.c_double),
        ("AskVolume2", ctypes.c_int),
        ("BidPrice3", ctypes.c_double),
        ("BidVolume3", ctypes.c_int),
        ("AskPrice3", ctypes.c_double),
        ("AskVolume3", ctypes.c_int),
        ("BidPrice4", ctypes.c_double),
        ("BidVolume4", ctypes.c_int),
        ("AskPrice4", ctypes.c_double),
        ("AskVolume4", ctypes.c_int),
        ("BidPrice5", ctypes.c_double),
        ("BidVolume5", ctypes.c_int),
        ("AskPrice5", ctypes.c_double),
        ("AskVolume5", ctypes.c_int),
        ("AveragePrice", ctypes.c_double),
        ("ActionDay", _char(9)),
        ("InstrumentID", _char(81)),
        ("ExchangeInstID", _char(81)),
        ("BandingUpperPrice", ctypes.c_double),
        ("BandingLowerPrice", ctypes.c_double),
    ]


class CTPRspUserLogin(ctypes.Structure):
    """Prefix of CThostFtdcRspUserLoginField (only fields we read)."""

    _fields_ = [
        ("TradingDay", _char(9)),
        ("LoginTime", _char(9)),
        ("BrokerID", _char(11)),
        ("UserID", _char(16)),
    ]


class CTPRspInfo(ctypes.Structure):
    _fields_ = [
        ("ErrorID", ctypes.c_int),
        ("ErrorMsg", _char(81)),
    ]


class CTPSpecificInstrument(ctypes.Structure):
    _fields_ = [
        ("reserve1", _char(31)),
        ("InstrumentID", _char(81)),
    ]


MD_CALLBACK = ctypes.CFUNCTYPE(
    None, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int64, ctypes.c_int64)


# ---------------------------------------------------------------------
# Struct -> dict conversion (fast path used per snapshot)
# ---------------------------------------------------------------------

def _dec(raw: bytes) -> str:
    if not raw:
        return ""
    return raw.split(b"\x00", 1)[0].decode("gbk", errors="replace").strip()


_CHAR_FIELDS = {
    "TradingDay", "ExchangeID", "UpdateTime", "ActionDay", "InstrumentID",
    "ExchangeInstID",
}
_INT_FIELDS = {
    "Volume", "UpdateMillisec", "BidVolume1", "AskVolume1", "BidVolume2",
    "AskVolume2", "BidVolume3", "AskVolume3", "BidVolume4", "AskVolume4",
    "BidVolume5", "AskVolume5",
}


def depth_struct_to_fields(p: ctypes.POINTER(CTPDepthMarketData)) -> dict:
    """CTP struct pointer -> {CTPFieldName: python value}."""
    s = p.contents
    out: dict = {}
    for field, _t, _ in CTPDepthMarketData._fields_:
        if field.startswith("reserve"):
            continue
        v = getattr(s, field)
        if field in _CHAR_FIELDS:
            out[field] = _dec(v)
        elif field in _INT_FIELDS:
            out[field] = int(v)
        else:
            out[field] = float(v)
    return out


def depth_fields_to_row(fields: dict, sequence_id: int,
                        local_receive_time_ns: int, batch_id: str,
                        trading_session: str, raw_source: str) -> dict:
    """CTP fields -> canonical raw snapshot row (schema.py naming)."""
    row: dict = {
        "sequence_id": sequence_id,
        "local_receive_time_ns": local_receive_time_ns,
        "batch_id": batch_id,
        "trading_session": trading_session,
        "raw_source": raw_source,
    }
    for ctp_name, column in CTP_FIELD_TO_COLUMN.items():
        row[column] = fields.get(ctp_name)
    return row


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------

class CtpMdClient:
    """Lifecycle wrapper around the shimmed CTP MdApi.

    The user supplies a handler object with (a subset of) methods:
        on_front_connected()
        on_front_disconnected(reason: int)
        on_heartbeat_warning(seconds: int)
        on_rsp_user_login(trading_day, broker, user, error_id, error_msg)
        on_rsp_sub_market_data(instrument, error_id, error_msg)
        on_rsp_error(error_id, error_msg)
        on_depth_market_data(fields: dict)   # already converted
        on_rsp_user_logout(error_id, error_msg)
    All handler calls happen on CTP worker threads -- keep them fast.
    """

    def __init__(self, handler, flow_dir: Path, use_udp: bool = False,
                 use_multicast: bool = False, production_mode: bool = True,
                 shim_path: Optional[Path] = None):
        self.handler = handler
        self.flow_dir = Path(flow_dir)
        self.flow_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._created = False
        self._released = False
        self._subscribed: list[str] = []
        self._credentials: Optional[tuple[str, str, str]] = None

        shim_path = Path(shim_path) if shim_path else SHIM_PATH
        if not shim_path.exists():
            raise CollectorError(
                f"CTP shim not found at {shim_path}. "
                f"Run: bash scripts/build_ctp_shim.sh")
        self._lib = ctypes.CDLL(str(shim_path))
        self._configure_signatures()
        verify_struct_layout(self._lib)

        self._cb = MD_CALLBACK(self._on_event)
        self._lib.md_set_callback(self._cb)

        rc = self._lib.md_create(
            str(self.flow_dir).encode(), int(use_udp), int(use_multicast),
            int(production_mode))
        if rc != 0:
            raise CollectorError(f"md_create failed with code {rc}")
        self._created = True
        self.api_version = self._lib.md_api_version().decode()
        logger.info("CTP MdApi created (version %s)", self.api_version)

    # ------------------------------------------------------------------
    def _configure_signatures(self) -> None:
        lib = self._lib
        lib.md_api_version.restype = ctypes.c_char_p
        lib.md_get_trading_day.restype = ctypes.c_char_p
        lib.md_depth_offsets.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int]
        lib.md_create.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int]
        lib.md_register_front.argtypes = [ctypes.c_char_p]
        lib.md_login.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.md_subscribe.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]
        lib.md_unsubscribe.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]

    # ------------------------------------------------------------------
    # Event dispatch (runs on CTP threads; GIL held by ctypes)
    # ------------------------------------------------------------------
    def _on_event(self, evt: int, p1, p2, a1: int, a2: int) -> None:
        h = self.handler
        try:
            if evt == MD_EVT_FRONT_CONNECTED:
                h.on_front_connected()
            elif evt == MD_EVT_FRONT_DISCONNECTED:
                h.on_front_disconnected(int(a1))
            elif evt == MD_EVT_HEARTBEAT_WARNING:
                h.on_heartbeat_warning(int(a1))
            elif evt == MD_EVT_RSP_USER_LOGIN:
                login = ctypes.cast(p1, ctypes.POINTER(CTPRspUserLogin)).contents
                info = ctypes.cast(p2, ctypes.POINTER(CTPRspInfo)).contents
                h.on_rsp_user_login(
                    trading_day=_dec(login.TradingDay),
                    broker=_dec(login.BrokerID),
                    user=_dec(login.UserID),
                    error_id=int(info.ErrorID),
                    error_msg=_dec(info.ErrorMsg))
            elif evt == MD_EVT_RSP_USER_LOGOUT:
                info = ctypes.cast(p2, ctypes.POINTER(CTPRspInfo)).contents
                h.on_rsp_user_logout(int(info.ErrorID), _dec(info.ErrorMsg))
            elif evt == MD_EVT_RSP_ERROR:
                info = ctypes.cast(p1, ctypes.POINTER(CTPRspInfo)).contents
                h.on_rsp_error(int(info.ErrorID), _dec(info.ErrorMsg))
            elif evt in (MD_EVT_RSP_SUB_MARKET_DATA, MD_EVT_RSP_UNSUB_MARKET_DATA):
                spec = ctypes.cast(p1, ctypes.POINTER(CTPSpecificInstrument)).contents
                info = ctypes.cast(p2, ctypes.POINTER(CTPRspInfo)).contents
                instrument = _dec(spec.InstrumentID)
                error_id, error_msg = int(info.ErrorID), _dec(info.ErrorMsg)
                if evt == MD_EVT_RSP_SUB_MARKET_DATA:
                    h.on_rsp_sub_market_data(instrument, error_id, error_msg)
                else:
                    h.on_rsp_unsub_market_data(instrument, error_id, error_msg)
            elif evt == MD_EVT_RTN_DEPTH_MARKET_DATA:
                fields = depth_struct_to_fields(
                    ctypes.cast(p1, ctypes.POINTER(CTPDepthMarketData)))
                h.on_depth_market_data(fields)
            elif evt == MD_EVT_RTN_FOR_QUOTE_RSP:
                pass  # not used in Phase 1
        except Exception:  # pragma: no cover - defensive
            logger.exception("error in CTP event handler (evt=%s)", evt)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def register_front(self, address: str) -> None:
        self._check_created()
        self._lib.md_register_front(address.encode())

    def init(self) -> None:
        self._check_created()
        self._lib.md_init()

    def join(self) -> int:
        self._check_created()
        return int(self._lib.md_join())

    def login(self, broker: str, user: str, password: str) -> int:
        """Store credentials and send login. Credentials are re-used on
        reconnect (front-connected events)."""
        self._credentials = (broker, user, password)
        return self._send_login()

    def _send_login(self) -> int:
        if self._credentials is None:
            raise CollectorError("login() called without credentials")
        b, u, p = self._credentials
        return int(self._lib.md_login(b.encode(), u.encode(), p.encode()))

    def resubscribe(self) -> int:
        """Re-issue subscriptions (e.g., after reconnect+login)."""
        if self._subscribed:
            return self.subscribe(self._subscribed)
        return 0

    def subscribe(self, instruments: list[str]) -> int:
        self._check_created()
        arr = (ctypes.c_char_p * len(instruments))(
            *[i.encode() for i in instruments])
        rc = int(self._lib.md_subscribe(arr, len(instruments)))
        if rc == 0:
            for i in instruments:
                if i not in self._subscribed:
                    self._subscribed.append(i)
        return rc

    def unsubscribe(self, instruments: list[str]) -> int:
        self._check_created()
        arr = (ctypes.c_char_p * len(instruments))(
            *[i.encode() for i in instruments])
        return int(self._lib.md_unsubscribe(arr, len(instruments)))

    def get_trading_day(self) -> str:
        self._check_created()
        return self._lib.md_get_trading_day().decode()

    def release(self) -> None:
        with self._lock:
            if self._created and not self._released:
                self._lib.md_release()
                self._released = True
                logger.info("CTP MdApi released")

    def _check_created(self) -> None:
        if not self._created:
            raise CollectorError("MdApi not created")
        if self._released:
            raise CollectorError("MdApi already released")


# ---------------------------------------------------------------------
# Layout verification
# ---------------------------------------------------------------------

def verify_struct_layout(lib) -> None:
    """Assert the Python ctypes structure matches the C++ layout exactly."""
    shim_size = int(lib.md_depth_size())
    py_size = ctypes.sizeof(CTPDepthMarketData)
    if shim_size != py_size:
        raise CollectorError(
            f"DepthMarketData size mismatch: shim={shim_size} python={py_size}. "
            f"Rebuild the shim or fix the ctypes structure.")

    n_fields = len(CTPDepthMarketData._fields_)
    offsets = (ctypes.c_int64 * n_fields)()
    written = int(lib.md_depth_offsets(offsets, n_fields))
    if written != n_fields:
        raise CollectorError(
            f"DepthMarketData field count mismatch: shim={written} python={n_fields}")

    for i, (name, _t) in enumerate(CTPDepthMarketData._fields_):
        py_off = getattr(CTPDepthMarketData, name).offset
        if int(offsets[i]) != py_off:
            raise CollectorError(
                f"DepthMarketData offset mismatch for '{name}': "
                f"shim={offsets[i]} python={py_off}")
    logger.debug("CTP DepthMarketData layout verified (%d fields, %d bytes)",
                 n_fields, py_size)
