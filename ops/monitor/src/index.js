import { loadEffectiveConfig, loadConfig } from "./config.js";
import {
  checkDisk,
  checkHealth,
  checkState,
  readDashboardToken,
  scanRawData,
} from "./checks.js";
import { buildReport } from "./report.js";
import { sendFeishu, validateFeishuConfig } from "./feishu.js";
import { zonedParts } from "./time.js";

const LOG_PREFIX = "[monitor]";
const log = {
  info: (...a) => console.log(new Date().toISOString(), LOG_PREFIX, ...a),
  warn: (...a) => console.warn(new Date().toISOString(), LOG_PREFIX, ...a),
  error: (...a) => console.error(new Date().toISOString(), LOG_PREFIX, ...a),
};

async function runChecks(cfg, now = new Date()) {
  const token = readDashboardToken(cfg.dashboardTokenFile, cfg.dashboardToken);
  const [health, disk] = await Promise.all([
    checkHealth(cfg.dashboardUrl, cfg.httpTimeoutMs),
    checkDisk(cfg.dataDir, cfg.diskWarnPercent),
  ]);

  let state = null;
  if (token.token) {
    state = await checkState(cfg.dashboardUrl, token.token, cfg.httpTimeoutMs);
  } else {
    state = { ok: false, status: 0, error: token.error || "缺少面板令牌" };
  }

  const ctpDay = state?.body?.ctp_trading_day || null;
  const localDay = zonedParts(cfg.tz, now).date;
  const expectedDays = [...new Set([ctpDay, localDay].filter(Boolean))];
  const data = scanRawData(cfg.dataDir, expectedDays, cfg.instruments);

  return {
    dashboardUrl: cfg.dashboardUrl,
    token,
    health,
    state,
    disk,
    data,
    expectedDays,
  };
}

async function runAndReport(cfg, now = new Date()) {
  const checks = await runChecks(cfg, now);
  const report = buildReport(checks, cfg, now);
  return { checks, report };
}

async function deliver(cfg, report) {
  const { text, severity, notes } = report;
  if (cfg.alertOnly && severity === "ok") {
    log.info(`状态正常 (alertOnly)，跳过发送。告警 ${notes.length} 条。`);
    return { skipped: true };
  }
  await sendFeishu(cfg, text, log);
  log.info(`已发送简报 (severity=${severity}, ${notes.length} 条告警)`);
  return { sent: true };
}

function parseArgs(argv) {
  const args = { check: false, once: false, help: false, dryRun: false };
  for (const a of argv.slice(2)) {
    if (a === "--check") args.check = true;
    else if (a === "--once" || a === "--test") args.once = true;
    else if (a === "--dry-run") args.dryRun = true;
    else if (a === "--help" || a === "-h") args.help = true;
  }
  return args;
}

function printHelp() {
  console.log(
    [
      "玉米快照采集监控 — 每日简报",
      "",
      "用法: node src/index.js [选项]",
      "",
      "  (无选项)      按 REPORT_TIMES / 面板设置定时发送",
      "  --once        立即检查并发送一次",
      "  --check       仅检查并在终端打印报告，不发送",
      "  --dry-run     不实际发送飞书消息（打印内容）",
      "  --help        显示帮助",
      "",
      "配置：优先读 /data/monitor-settings.local.json（网页面板写入），",
      "缺失项回退到环境变量（.env.example）。",
    ].join("\n"),
  );
}

function startScheduler(env) {
  const fired = new Set();
  const intervalMs = 20_000;
  let lastTestRequest = -1;
  let cfg = loadEffectiveConfig(env);
  if (lastTestRequest < 0) lastTestRequest = cfg.testRequest;

  const tick = async () => {
    cfg = loadEffectiveConfig(env);           // reload so panel edits apply
    const now = new Date();
    const { hhmm, date } = zonedParts(cfg.tz, now);

    if (cfg.testRequest > lastTestRequest) {
      lastTestRequest = cfg.testRequest;
      if (!cfg.enabled) {
        log.info("收到测试请求（简报未启用，仍发送测试消息）");
      }
      try {
        const { report } = await runAndReport(cfg, now);
        report.text = `[测试消息]\n${report.text}`;
        await deliver(cfg, report);
      } catch (err) {
        log.error(`测试简报失败: ${err?.message || err}`);
      }
      return;
    }

    if (!cfg.enabled) return;
    if (!cfg.reportTimes.includes(hhmm)) return;
    const key = `${date} ${hhmm}`;
    if (fired.has(key)) return;
    fired.add(key);
    try {
      const { report } = await runAndReport(cfg, now);
      log.info(`定时触发 ${hhmm}: ${report.severity}`);
      await deliver(cfg, report);
    } catch (err) {
      log.error(`定时简报失败: ${err?.message || err}`);
    }
  };

  log.info(`调度已启动，发送时间: ${cfg.reportTimes.join(", ")} (${cfg.tz})`);
  if (!cfg.enabled) log.info("当前未启用（enabled=false），仅响应测试请求");
  const timer = setInterval(tick, intervalMs);
  return { timer, tick };
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    printHelp();
    return;
  }

  const env = process.env;
  let cfg = loadEffectiveConfig(env);
  if (args.dryRun) cfg.dryRun = true;

  const missing = validateFeishuConfig(cfg);
  if (missing.length) {
    log.warn(`飞书配置缺失: ${missing.join(", ")}（--check 仍可用）`);
  }

  if (args.check) {
    const { report } = await runAndReport(cfg);
    console.log(report.text);
    console.log(`\n(severity=${report.severity}, enabled=${cfg.enabled})`);
    return;
  }

  if (args.once) {
    const { report } = await runAndReport(cfg);
    console.log(report.text);
    await deliver(cfg, report);
    return;
  }

  if (cfg.sendOnStart) {
    try {
      const { report } = await runAndReport(cfg);
      await deliver(cfg, report);
    } catch (err) {
      log.error(`启动简报失败: ${err?.message || err}`);
    }
  }

  const { timer } = startScheduler(env);
  const shutdown = () => {
    clearInterval(timer);
    log.info("已停止");
    process.exit(0);
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

main().catch((err) => {
  log.error(`致命错误: ${err?.stack || err}`);
  process.exit(1);
});
