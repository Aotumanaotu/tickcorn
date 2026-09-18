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

  const res = await client.im.v1.message.create({
    params: { receive_id_type: cfg.feishuReceiveIdType },
    data: {
      receive_id: cfg.feishuReceiveId,
      msg_type: "text",
      content: JSON.stringify({ text }),
    },
  });

  const code = res?.code ?? res?.data?.code;
  if (code !== undefined && code !== 0) {
    throw new Error(`飞书发送失败 code=${code} msg=${res?.msg || res?.data?.msg || ""}`);
  }
  return res;
}
