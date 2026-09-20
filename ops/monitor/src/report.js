import { zonedParts, zonedString } from "./time.js";

const GOOD_CONNECTION = new Set(["logged_in", "streaming"]);
const WARN_CONNECTION = new Set([
  "connected",
  "waiting_front",
  "replaying",
  "starting",
  "login_failed",
  "disconnected",
  "failed",
  "replay_failed",
]);

export function formatBytes(bytes) {
  if (bytes == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function buildReport(data, cfg, now = new Date()) {
  const notes = [];
  let severity = "ok";
  const bump = (level, note) => {
    notes.push(note);
    const order = { ok: 0, warn: 1, critical: 2 };
    if (order[level] > order[severity]) severity = level;
  };

  const state = data.state?.body || null;
  const conn = state?.connection || {};
  const manager = state?.manager || {};
  const instruments = state?.instruments || {};

  if (!data.health?.ok) {
    bump("critical", `面板 HTTP 不可达 (${data.health?.error || "status " + data.health?.status})`);
  }
  if (data.token?.error && !data.token?.token) {
    bump("warn", data.token.error);
  }
  if (data.state && !data.state.ok) {
    if (data.state.status === 401) bump("critical", "面板令牌无效或被拒绝 (401)");
    else bump("critical", `读取采集状态失败 (${data.state.error || "status " + data.state.status})`);
  }
  if (state && !manager.running) {
    bump("warn", "采集未在运行（容器重启后需从网页重新开始）");
  }
  if (conn.status && !GOOD_CONNECTION.has(conn.status)) {
    const level = WARN_CONNECTION.has(conn.status) ? "warn" : "critical";
    bump(level, `行情连接状态: ${conn.status}${conn.detail ? " · " + conn.detail : ""}`);
  }
  if (data.disk?.warn) {
    bump("warn", `磁盘空间紧张: /data 已用 ${data.disk.usedPercent}%`);
  }
  if (data.data?.hasStaging && !manager.running) {
    bump("warn", "存在未 finalize 的 staging 分片");
  }
  if (manager.running && data.expectedDays?.length && data.data?.exists) {
    const hit = Object.values(data.data.instruments).some(
      (rec) => rec.matchedDays && rec.matchedDays.length > 0,
    );
    if (!hit) {
      bump("warn", `今日 (${data.expectedDays.join(" / ")}) 暂无落盘数据`);
    }
  }

  const icon = severity === "critical" ? "❌" : severity === "warn" ? "⚠️" : "✅";
  const statusText =
    severity === "critical" ? "异常" : severity === "warn" ? "需要关注" : "正常";

  const lines = [];
  lines.push(`【${cfg.title}】状态简报 ${zonedParts(cfg.tz, now).hhmm}`);
  lines.push("");
  if (state?.simulate) lines.push("数据来源：本地模拟（仅联调，不作为市场样本）");
  if (state?.source) lines.push(`采集来源：${state.source}`);
  if (state?.metrics_scope) lines.push(`实时统计范围：${state.metrics_scope}`);
  lines.push(`总体: ${icon} ${statusText}`);
  lines.push(
    data.health?.ok
      ? `服务: ✅ HTTP ok (${data.health.ms}ms)`
      : `服务: ❌ HTTP 异常`,
  );
  if (state) {
    lines.push(
      manager.running
        ? `采集: 🟢 运行中 · batch ${manager.batch_id || state.batch_id || "–"}`
        : `采集: ⚪ 未运行${manager.error ? " · " + manager.error : ""}`,
    );
    lines.push(
      conn.status
        ? `连接: ${GOOD_CONNECTION.has(conn.status) ? "🟢" : "🟠"} ${conn.status}${
            conn.detail ? " · " + conn.detail : ""
          }`
        : "连接: ⚪ 未知",
    );
    if (state.ctp_trading_day) lines.push(`CTP交易日: ${state.ctp_trading_day}`);
  } else {
    lines.push("采集: ⚪ 无法获取状态");
  }

  const instIds = Object.keys(instruments);
  if (instIds.length) {
    lines.push("");
    lines.push("合约:");
    for (const id of instIds) {
      const s = instruments[id] || {};
      const q = s.last || {};
      const last = q.last_price == null ? "–" : q.last_price;
      const bid = q.bid_price1 == null ? "–" : q.bid_price1;
      const ask = q.ask_price1 == null ? "–" : q.ask_price1;
      const stale = q.stale_s == null ? "–" : `${q.stale_s}s`;
      const bounce =
        s.bounce_ratio == null ? "–" : `${(100 * s.bounce_ratio).toFixed(1)}%`;
      const genuine =
        s.genuine_move_ratio == null ? "–" : `${(100 * s.genuine_move_ratio).toFixed(1)}%`;
      lines.push(`  • ${id}  last=${last}  bid/ask=${bid}/${ask}`);
      lines.push(
        `     更新 ${q.update_time || "–"}  延迟 ${stale}  ${s.rate_per_min ?? "–"} msg/min  快照 ${s.msg_count ?? "–"}`,
      );
      lines.push(`     反弹比 ${bounce} / 真实移动比 ${genuine}`);
    }
  } else {
    lines.push("");
    lines.push(
      "行情: ⚪ 尚未收到任何快照（非交易时段属正常；交易时段请检查订阅）",
    );
    const subs = Object.entries(state?.subscriptions || {});
    if (subs.length) {
      lines.push(
        "订阅: " +
          subs
            .map(([inst, st]) =>
              st === "accepted" ? `${inst} ✅已接受` : `${inst} ❌${st}`,
            )
            .join("  "),
      );
    } else if (state) {
      lines.push("订阅: ⚪ 尚未收到订阅响应");
    }
  }

  lines.push("");
  if (data.expectedDays?.length) {
    lines.push(`今日数据 (交易日 ${data.expectedDays.join(" / ")}):`);
  } else {
    lines.push("今日数据:");
  }
  if (!data.data?.exists) {
    lines.push("  (数据目录不存在)");
  } else {
    const recs = Object.entries(data.data.instruments);
    if (!recs.length) {
      lines.push("  (暂无已落盘分片)");
    } else {
      for (const [inst, rec] of recs) {
        const latest = rec.latestMtime
          ? zonedString(cfg.tz, new Date(rec.latestMtime))
          : "–";
        const marker = rec.matchedDays?.length ? "✅" : "◻️";
        lines.push(
          `  ${marker} ${inst}  ${rec.files} 文件 · ${formatBytes(rec.bytes)} · 最新 ${latest}`,
        );
      }
    }
  }

  if (data.disk?.ok) {
    lines.push(
      `磁盘: ${data.disk.warn ? "⚠️" : "✅"} /data 剩余 ${formatBytes(data.disk.free)} / ${formatBytes(
        data.disk.total,
      )} (已用 ${data.disk.usedPercent}%)`,
    );
  } else if (data.disk) {
    lines.push(`磁盘: ⚠️ 无法读取 (${data.disk.error})`);
  }

  if (notes.length) {
    lines.push("");
    lines.push("告警:");
    for (const n of notes) lines.push(`  • ${n}`);
  }

  lines.push("");
  lines.push(`时间: ${zonedString(cfg.tz, now)} (${cfg.tz})`);
  lines.push(`来源: ${cfg.serviceName} · ${data.dashboardUrl}`);

  return { text: lines.join("\n"), severity, notes };
}
