import fs from "node:fs";
import path from "node:path";

function asBool(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function asInt(value, fallback) {
  const n = Number.parseInt(value ?? "", 10);
  return Number.isFinite(n) ? n : fallback;
}

function asList(value) {
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function loadConfig(env = process.env) {
  const dataDir = env.DATA_DIR || "/data";
  return {
    // Feishu bot app credentials (official SDK)
    feishuAppId: env.FEISHU_APP_ID || "",
    feishuAppSecret: env.FEISHU_APP_SECRET || "",
    feishuReceiveId: env.FEISHU_RECEIVE_ID || "",
    feishuReceiveIdType: env.FEISHU_RECEIVE_ID_TYPE || "chat_id",
    feishuDomain: (env.FEISHU_DOMAIN || "feishu").toLowerCase(),

    // Scheduling
    enabled: asBool(env.MONITOR_ENABLED, true),
    reportTimes: asList(env.REPORT_TIMES || "08:00,12:30,21:30"),
    alertOnly: asBool(env.ALERT_ONLY, false),
    sendOnStart: asBool(env.SEND_ON_START, false),
    dryRun: asBool(env.DRY_RUN, false),
    testRequest: 0,

    // Target service
    dashboardUrl: (env.DASHBOARD_URL || "http://127.0.0.1:8800").replace(/\/+$/, ""),
    dashboardToken: env.DASHBOARD_TOKEN || "",
    dashboardTokenFile:
      env.DASHBOARD_TOKEN_FILE || `${dataDir}/dashboard-token.local.json`,
    dataDir,
    instruments: asList(env.INSTRUMENTS),
    httpTimeoutMs: asInt(env.HTTP_TIMEOUT_MS, 8000),
    diskWarnPercent: asInt(env.DISK_WARN_PERCENT, 85),

    // Presentation
    title: env.REPORT_TITLE || "玉米快照采集",
    serviceName: env.SERVICE_NAME || "corn-tick",
    tz: env.TZ || "Asia/Shanghai",

    settingsFile:
      env.MONITOR_SETTINGS_FILE || path.join(dataDir, "monitor-settings.local.json"),
  };
}

function pick(value, current) {
  if (value === undefined || value === null || value === "") return current;
  return value;
}

export function applyMonitorSettingsFile(cfg, env = process.env) {
  const file = env.MONITOR_SETTINGS_FILE || cfg.settingsFile;
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return cfg;
  }
  if (typeof raw.enabled === "boolean") cfg.enabled = raw.enabled;
  cfg.feishuAppId = pick(raw.feishu_app_id, cfg.feishuAppId);
  cfg.feishuAppSecret = pick(raw.feishu_app_secret, cfg.feishuAppSecret);
  cfg.feishuReceiveId = pick(raw.feishu_receive_id, cfg.feishuReceiveId);
  cfg.feishuReceiveIdType = pick(raw.feishu_receive_id_type, cfg.feishuReceiveIdType);
  if (Array.isArray(raw.report_times) && raw.report_times.length) {
    cfg.reportTimes = raw.report_times.map((t) => String(t).trim()).filter(Boolean);
  }
  if (typeof raw.alert_only === "boolean") cfg.alertOnly = raw.alert_only;
  if (typeof raw.title === "string" && raw.title) cfg.title = raw.title;
  if (typeof raw.tz === "string" && raw.tz) cfg.tz = raw.tz;
  if (Number.isFinite(raw.test_request)) cfg.testRequest = Number(raw.test_request);
  return cfg;
}

export function loadEffectiveConfig(env = process.env) {
  return applyMonitorSettingsFile(loadConfig(env), env);
}
