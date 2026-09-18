import fs from "node:fs";
import path from "node:path";

export async function httpGetJson(url, headers, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const started = Date.now();
  try {
    const res = await fetch(url, {
      headers: headers || {},
      signal: controller.signal,
      cache: "no-store",
    });
    const ms = Date.now() - started;
    let body = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    return { ok: res.ok, status: res.status, ms, body };
  } catch (err) {
    const ms = Date.now() - started;
    const message = err?.name === "AbortError" ? "timeout" : String(err?.message || err);
    return { ok: false, status: 0, ms, error: message };
  } finally {
    clearTimeout(timer);
  }
}

export function readDashboardToken(file, fallback) {
  if (fallback) return { token: fallback, source: "env" };
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (raw && typeof raw.token === "string" && raw.token.length >= 32) {
      return { token: raw.token, source: file };
    }
    return { token: null, error: `令牌文件格式异常: ${file}` };
  } catch (err) {
    if (err?.code === "ENOENT") return { token: null, error: `令牌文件不存在: ${file}` };
    return { token: null, error: `读取令牌失败: ${err?.message || err}` };
  }
}

export async function checkHealth(baseUrl, timeoutMs) {
  return httpGetJson(`${baseUrl}/healthz`, {}, timeoutMs);
}

export async function checkState(baseUrl, token, timeoutMs) {
  if (!token) return { ok: false, status: 0, error: "缺少面板令牌" };
  return httpGetJson(
    `${baseUrl}/api/state`,
    { "X-Auth-Token": token },
    timeoutMs,
  );
}

export async function checkDisk(dir, warnPercent) {
  try {
    const stats = await fs.promises.statfs(dir);
    const total = stats.blocks * stats.bsize;
    const free = stats.bavail * stats.bsize;
    const usedPercent = total ? Math.round((1 - free / total) * 100) : null;
    return {
      ok: true,
      total,
      free,
      usedPercent,
      warn: usedPercent != null && usedPercent >= warnPercent,
    };
  } catch (err) {
    return { ok: false, error: String(err?.message || err) };
  }
}

function normalizeDay(value) {
  return String(value || "").replace(/-/g, "");
}

export function scanRawData(dataDir, wantedDays, instruments) {
  const root = path.join(dataDir, "raw");
  const result = {
    root,
    exists: fs.existsSync(root),
    instruments: {},
    totalFiles: 0,
    totalBytes: 0,
    hasStaging: false,
  };
  if (!result.exists) return result;

  const wanted = new Set((wantedDays || []).filter(Boolean).map(normalizeDay));
  const filter = new Set(instruments || []);

  let instEntries = [];
  try {
    instEntries = fs.readdirSync(root, { withFileTypes: true });
  } catch (err) {
    result.error = String(err?.message || err);
    return result;
  }

  for (const instDir of instEntries) {
    if (!instDir.isDirectory() || !instDir.name.startsWith("instrument=")) continue;
    const inst = instDir.name.slice("instrument=".length);
    if (filter.size && !filter.has(inst)) continue;

    const rec = {
      files: 0,
      bytes: 0,
      days: [],
      matchedDays: [],
      latestMtime: null,
      staging: false,
    };
    const instPath = path.join(root, instDir.name);

    let dayEntries = [];
    try {
      dayEntries = fs.readdirSync(instPath, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const dayDir of dayEntries) {
      if (!dayDir.isDirectory()) continue;
      if (dayDir.name.includes("_staging")) {
        rec.staging = true;
        continue;
      }
      if (!dayDir.name.startsWith("trading_day=")) continue;

      const day = dayDir.name.slice("trading_day=".length);
      rec.days.push(day);
      if (wanted.has(normalizeDay(day))) rec.matchedDays.push(day);

      const dayPath = path.join(instPath, dayDir.name);
      let files = [];
      try {
        files = fs.readdirSync(dayPath, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const file of files) {
        if (!file.isFile()) {
          if (file.isDirectory() && file.name.includes("_staging")) {
            rec.staging = true;
            result.hasStaging = true;
          }
          continue;
        }
        const full = path.join(dayPath, file.name);
        let st;
        try {
          st = fs.statSync(full);
        } catch {
          continue;
        }
        rec.files += 1;
        rec.bytes += st.size;
        result.totalFiles += 1;
        result.totalBytes += st.size;
        if (!rec.latestMtime || st.mtimeMs > rec.latestMtime) {
          rec.latestMtime = st.mtimeMs;
        }
      }
    }

    if (rec.staging) result.hasStaging = true;
    result.instruments[inst] = rec;
  }

  return result;
}
