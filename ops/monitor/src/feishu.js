let cachedModule = null;

async function loadSdk() {
  if (!cachedModule) {
    const mod = await import("@larksuiteoapi/node-sdk");
    cachedModule = mod.default ?? mod;
  }
  return cachedModule;
}

export function validateFeishuConfig(cfg) {
  const missing = [];
  if (!cfg.feishuAppId) missing.push("FEISHU_APP_ID");
  if (!cfg.feishuAppSecret) missing.push("FEISHU_APP_SECRET");
  if (!cfg.feishuReceiveId) missing.push("FEISHU_RECEIVE_ID");
  return missing;
}

function feishuErrorDetail(err) {
  const candidates = [];
  const respData = err?.response?.data;
  if (Array.isArray(respData)) candidates.push(...respData);
  else if (respData) candidates.push(respData);
  if (respData && typeof respData === "object" && respData.data) {
    candidates.push(respData.data);
  }
  if (err?.data) candidates.push(err.data);
  for (const c of candidates) {
    if (c && typeof c === "object" && (c.code != null || c.msg)) return c;
  }
  return null;
}

function formatFeishuError(detail) {
  const code = detail.code ?? "?";
  const msg = detail.msg || detail.message || "";
  const violations = Array.isArray(detail.permission_violations)
    ? detail.permission_violations
        .map((v) => v?.scope_name || v?.scope || v)
        .filter(Boolean)
    : [];
  const scopes = [...new Set(violations)];
  const scopeHint = scopes.length
    ? `；请在飞书开放平台添加权限: ${scopes.join(", ")}`
    : "";
  return `飞书发送失败 code=${code} msg=${msg}${scopeHint}`;
}

export async function sendFeishu(cfg, text, log = console) {
  const missing = validateFeishuConfig(cfg);
  if (missing.length) {
    throw new Error(`飞书配置缺失: ${missing.join(", ")}`);
  }
  if (cfg.dryRun) {
    log.info?.("[dry-run] 不发送，消息内容如下:\n" + text);
    return { dryRun: true };
  }

  const lark = await loadSdk();
  const client = new lark.Client({
    appId: cfg.feishuAppId,
    appSecret: cfg.feishuAppSecret,
    appType: lark.AppType?.SelfBuild,
    domain: cfg.feishuDomain === "lark" ? lark.Domain?.Lark : lark.Domain?.Feishu,
  });

  let res;
  try {
    res = await client.im.v1.message.create({
      params: { receive_id_type: cfg.feishuReceiveIdType },
      data: {
        receive_id: cfg.feishuReceiveId,
        msg_type: "text",
        content: JSON.stringify({ text }),
      },
    });
  } catch (err) {
    const detail = feishuErrorDetail(err);
    if (detail) throw new Error(formatFeishuError(detail));
    throw new Error(`飞书发送失败: ${err?.message || err}`);
  }

  const code = res?.code ?? res?.data?.code;
  if (code !== undefined && code !== 0) {
    throw new Error(formatFeishuError(res?.data ? { ...res.data, code } : res));
  }
  return res;
}
