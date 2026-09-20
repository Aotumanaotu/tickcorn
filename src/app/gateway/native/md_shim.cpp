/*
 * md_shim.cpp
 * ------------
 * C-ABI wrapper around CTP CThostFtdcMdApi (v6.7.13) for Python ctypes.
 *
 * Rationale: CTP exposes a C++ virtual SPI interface which cannot be
 * implemented directly from Python. This shim:
 *   1. implements CThostFtdcMdSpi and forwards every event to a single
 *      C function pointer  (event_code, p1, p2, i1, i2),
 *   2. owns the MdApi lifecycle (create / register front / init / release),
 *   3. fills ReqUserLoginField server-side so Python never needs its layout,
 *   4. exposes sizeof/offsetof of CThostFtdcDepthMarketDataField so the
 *      Python side can verify its ctypes.Structure transcription at runtime.
 *
 * The callback is invoked from CTP worker threads. ctypes CFUNCTYPE
 * callbacks acquire the GIL automatically, so the Python handler is safe,
 * but it must be fast.
 *
 * Build: scripts/build_ctp_shim.sh
 */
#include <cstdint>
#include <cstring>
#include <cstddef>
#include "ThostFtdcMdApi.h"

/* Export shim symbols even when compiled with -fvisibility=hidden. */
#define MD_EXPORT extern "C" __attribute__((visibility("default")))

typedef void (*md_callback_t)(int event, void *p1, void *p2,
                              int64_t a1, int64_t a2);

enum MdEventType {
    MD_EVT_FRONT_CONNECTED     = 1,
    MD_EVT_FRONT_DISCONNECTED  = 2,  /* a1 = reason                          */
    MD_EVT_HEARTBEAT_WARNING   = 3,  /* a1 = time lapse (s)                  */
    MD_EVT_RSP_USER_LOGIN      = 4,  /* p1=RspUserLoginField* p2=RspInfo*    */
    MD_EVT_RSP_USER_LOGOUT     = 5,  /* p1=UserLogoutField*   p2=RspInfo*    */
    MD_EVT_RSP_ERROR           = 6,  /* p1=RspInfoField*                     */
    MD_EVT_RSP_SUB_MARKET_DATA = 7,  /* p1=SpecificInstrument* p2=RspInfo*   */
    MD_EVT_RSP_UNSUB_MARKET_DATA = 8,
    MD_EVT_RTN_DEPTH_MARKET_DATA = 9, /* p1=DepthMarketDataField*           */
    MD_EVT_RTN_FOR_QUOTE_RSP   = 10  /* p1=ForQuoteRspField*                 */
};

static md_callback_t g_cb = nullptr;
static CThostFtdcMdApi *g_api = nullptr;
static CThostFtdcMdSpi *g_spi = nullptr;

class ShimSpi : public CThostFtdcMdSpi {
public:
    void OnFrontConnected() override {
        if (g_cb) g_cb(MD_EVT_FRONT_CONNECTED, nullptr, nullptr, 0, 0);
    }
    void OnFrontDisconnected(int nReason) override {
        if (g_cb) g_cb(MD_EVT_FRONT_DISCONNECTED, nullptr, nullptr, nReason, 0);
    }
    void OnHeartBeatWarning(int nTimeLapse) override {
        if (g_cb) g_cb(MD_EVT_HEARTBEAT_WARNING, nullptr, nullptr, nTimeLapse, 0);
    }
    void OnRspUserLogin(CThostFtdcRspUserLoginField *pRspUserLogin,
                        CThostFtdcRspInfoField *pRspInfo, int nRequestID,
                        bool bIsLast) override {
        if (g_cb) g_cb(MD_EVT_RSP_USER_LOGIN, (void *)pRspUserLogin,
                       (void *)pRspInfo, nRequestID, (int64_t)bIsLast);
    }
    void OnRspUserLogout(CThostFtdcUserLogoutField *pUserLogout,
                         CThostFtdcRspInfoField *pRspInfo, int nRequestID,
                         bool bIsLast) override {
        if (g_cb) g_cb(MD_EVT_RSP_USER_LOGOUT, (void *)pUserLogout,
                       (void *)pRspInfo, nRequestID, (int64_t)bIsLast);
    }
    void OnRspError(CThostFtdcRspInfoField *pRspInfo, int nRequestID,
                    bool bIsLast) override {
        if (g_cb) g_cb(MD_EVT_RSP_ERROR, (void *)pRspInfo, nullptr,
                       nRequestID, (int64_t)bIsLast);
    }
    void OnRspSubMarketData(CThostFtdcSpecificInstrumentField *pSpecific,
                            CThostFtdcRspInfoField *pRspInfo, int nRequestID,
                            bool bIsLast) override {
        if (g_cb) g_cb(MD_EVT_RSP_SUB_MARKET_DATA, (void *)pSpecific,
                       (void *)pRspInfo, nRequestID, (int64_t)bIsLast);
    }
    void OnRspUnSubMarketData(CThostFtdcSpecificInstrumentField *pSpecific,
                              CThostFtdcRspInfoField *pRspInfo, int nRequestID,
                              bool bIsLast) override {
        if (g_cb) g_cb(MD_EVT_RSP_UNSUB_MARKET_DATA, (void *)pSpecific,
                       (void *)pRspInfo, nRequestID, (int64_t)bIsLast);
    }
    void OnRtnDepthMarketData(CThostFtdcDepthMarketDataField *pDepth) override {
        if (g_cb) g_cb(MD_EVT_RTN_DEPTH_MARKET_DATA, (void *)pDepth, nullptr, 0, 0);
    }
    void OnRtnForQuoteRsp(CThostFtdcForQuoteRspField *pForQuote) override {
        if (g_cb) g_cb(MD_EVT_RTN_FOR_QUOTE_RSP, (void *)pForQuote, nullptr, 0, 0);
    }
};

/* Register the single event callback. Pass NULL to clear. */
MD_EXPORT int md_set_callback(md_callback_t cb) { g_cb = cb; return 0; }

MD_EXPORT const char *md_api_version() { return CThostFtdcMdApi::GetApiVersion(); }

MD_EXPORT int md_create(const char *flow_path, int use_udp, int use_multicast,
              int production_mode) {
    if (g_api != nullptr) return -1;
    g_api = CThostFtdcMdApi::CreateFtdcMdApi(
        flow_path ? flow_path : "", use_udp != 0, use_multicast != 0,
        production_mode != 0);
    if (g_api == nullptr) return -2;
    g_spi = new ShimSpi();
    g_api->RegisterSpi(g_spi);
    return 0;
}

MD_EXPORT int md_register_front(const char *addr) {
    if (g_api == nullptr || addr == nullptr) return -1;
    g_api->RegisterFront(const_cast<char *>(addr));
    return 0;
}

MD_EXPORT int md_init() {
    if (g_api == nullptr) return -1;
    g_api->Init();
    return 0;
}

MD_EXPORT int md_join() {
    if (g_api == nullptr) return -1;
    return g_api->Join();
}

MD_EXPORT void md_release() {
    if (g_api != nullptr) {
        g_api->Release();   /* deletes the api object */
        g_api = nullptr;
    }
    if (g_spi != nullptr) {
        delete g_spi;
        g_spi = nullptr;
    }
}

MD_EXPORT const char *md_get_trading_day() {
    if (g_api == nullptr) return "";
    return g_api->GetTradingDay();
}

MD_EXPORT int md_subscribe(char **instruments, int n) {
    if (g_api == nullptr || instruments == nullptr) return -1;
    return g_api->SubscribeMarketData(instruments, n);
}

MD_EXPORT int md_unsubscribe(char **instruments, int n) {
    if (g_api == nullptr || instruments == nullptr) return -1;
    return g_api->UnSubscribeMarketData(instruments, n);
}

MD_EXPORT int md_login(const char *broker, const char *user, const char *password) {
    if (g_api == nullptr) return -1;
    CThostFtdcReqUserLoginField req;
    std::memset(&req, 0, sizeof(req));
    if (broker)   std::strncpy(req.BrokerID, broker, sizeof(req.BrokerID) - 1);
    if (user)     std::strncpy(req.UserID, user, sizeof(req.UserID) - 1);
    if (password) std::strncpy(req.Password, password, sizeof(req.Password) - 1);
    return g_api->ReqUserLogin(&req, 1);
}

MD_EXPORT int md_logout() {
    if (g_api == nullptr) return -1;
    CThostFtdcUserLogoutField req;
    std::memset(&req, 0, sizeof(req));
    return g_api->ReqUserLogout(&req, 2);
}

/* ---- struct layout introspection ------------------------------------- */

MD_EXPORT size_t md_depth_size() {
    return sizeof(CThostFtdcDepthMarketDataField);
}

MD_EXPORT size_t md_rsp_login_size() { return sizeof(CThostFtdcRspUserLoginField); }
MD_EXPORT size_t md_rsp_info_size()  { return sizeof(CThostFtdcRspInfoField); }
MD_EXPORT size_t md_specific_inst_size() {
    return sizeof(CThostFtdcSpecificInstrumentField);
}

/*
 * Fill `out` with offsetof() of DepthMarketDataField members, in the exact
 * order of DEPTH_FIELD_ORDER in app/collector/ctp_binding.py.
 * Returns the number of fields written (== n when n is large enough).
 */
MD_EXPORT int md_depth_offsets(int64_t *out, int n) {
#define F(name) { if (written < n) out[written] = \
    (int64_t)offsetof(CThostFtdcDepthMarketDataField, name); ++written; }
    int written = 0;
    F(TradingDay) F(reserve1) F(ExchangeID) F(reserve2)
    F(LastPrice) F(PreSettlementPrice) F(PreClosePrice) F(PreOpenInterest)
    F(OpenPrice) F(HighestPrice) F(LowestPrice) F(Volume) F(Turnover)
    F(OpenInterest) F(ClosePrice) F(SettlementPrice) F(UpperLimitPrice)
    F(LowerLimitPrice) F(PreDelta) F(CurrDelta) F(UpdateTime) F(UpdateMillisec)
    F(BidPrice1) F(BidVolume1) F(AskPrice1) F(AskVolume1)
    F(BidPrice2) F(BidVolume2) F(AskPrice2) F(AskVolume2)
    F(BidPrice3) F(BidVolume3) F(AskPrice3) F(AskVolume3)
    F(BidPrice4) F(BidVolume4) F(AskPrice4) F(AskVolume4)
    F(BidPrice5) F(BidVolume5) F(AskPrice5) F(AskVolume5)
    F(AveragePrice) F(ActionDay) F(InstrumentID) F(ExchangeInstID)
    F(BandingUpperPrice) F(BandingLowerPrice)
#undef F
    return written;
}


