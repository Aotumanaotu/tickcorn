/** Chinese display labels; API values stay unchanged for filtering and permissions. */
const roles: Record<string, string> = {
  ADMIN: '管理员', RESEARCHER: '研究员', TRADER: '交易员', VIEWER: '观察员',
}
const states: Record<string, string> = {
  idle: '待连接', connecting: '连接中', connected: '已连接', streaming: '行情推送中',
  disconnected: '已断开', failed: '连接失败', offline: '离线', unknown: '未知',
  running: '运行中', open: '已连接', reconnecting: '重连中', closed: '已关闭',
}
const modes: Record<string, string> = { live: '实时行情', replay: '行情回放', simulate: '模拟行情' }
const categories: Record<string, string> = {
  Agriculture: '农产品', Metal: '金属', Energy: '能源', Chemical: '化工',
  Financial: '金融', Other: '其他',
}
const exchanges: Record<string, string> = {
  DCE: '大商所', SHFE: '上期所', CZCE: '郑商所', CFFEX: '中金所',
  INE: '上海能源', GFEX: '广期所',
}
export const roleLabel = (value: string): string => roles[value] ?? value
export const stateLabel = (value: string): string => states[value] ?? value
export const modeLabel = (value: string): string => modes[value] ?? value
export const categoryLabel = (value: string): string => categories[value] ?? value
export const exchangeLabel = (value: string): string => exchanges[value] ?? value

/** Keep raw server details on ApiError.detail; present readable errors in the UI. */
export function apiErrorMessage(status: number, detail: string): string {
  if (/[\u3400-\u9fff]/.test(detail)) return detail
  const known: Record<string, string> = {
    'Session expired': '登录已过期，请重新登录。',
    'refresh failed': '登录状态刷新失败，请重新登录。',
    'Not authenticated': '请先登录。',
    'user not found or inactive': '用户不存在或已停用。',
    'watchlist not found': '自选列表不存在。',
  }
  if (known[detail]) return known[detail]
  if (detail.startsWith('unknown instrument: ')) return `未知合约：${detail.slice(20)}`
  const messages: Record<number, string> = {
    400: '请求内容有误，请检查后重试。', 401: '身份验证失败，请重新登录。',
    403: '权限不足，无法执行此操作。', 404: '请求的资源不存在。',
    409: '数据冲突，请检查是否已存在或刷新后重试。',
    422: '填写内容不符合要求，请检查后重试。', 429: '请求过于频繁，请稍后再试。',
    500: '服务器发生错误，请稍后重试。', 502: '服务暂时不可用，请稍后重试。',
    503: '服务尚未就绪，请稍后重试。', 504: '服务响应超时，请稍后重试。',
  }
  return messages[status] ?? `请求失败（状态码 ${status}），请稍后重试。`
}

const eventLabels: Record<string, string> = {
  NO_MOVE: '无价格变化', HIGH_CONFIDENCE_BOUNCE_UP: '高置信反弹 ↑', HIGH_CONFIDENCE_BOUNCE_DOWN: '高置信反弹 ↓',
  LIKELY_BOUNCE_UP: '可能反弹 ↑', LIKELY_BOUNCE_DOWN: '可能反弹 ↓',
  GENUINE_QUOTE_MOVE_UP: '真实报价上移', GENUINE_QUOTE_MOVE_DOWN: '真实报价下移', AMBIGUOUS: '不确定变化',
}
export const eventLabel = (value: unknown): string => eventLabels[String(value)] ?? String(value ?? '—')
export const percent = (value: number | null | undefined): string => value == null ? '—' : `${(value * 100).toFixed(1)}%`

const regimeLabels: Record<string, string> = {
  INSUFFICIENT_DATA: '数据不足',
  HIGH_BOUNCE: '高反弹市况',
  DIRECTIONAL: '趋势市况',
  MIXED: '混合市况',
}
export const regimeLabel = (value: unknown): string => regimeLabels[String(value)] ?? (value ? String(value) : '—')
export type RegimeTone = 'up' | 'warn' | 'info' | 'muted'
const regimeTones: Record<string, RegimeTone> = {
  HIGH_BOUNCE: 'warn',
  DIRECTIONAL: 'up',
  MIXED: 'info',
  INSUFFICIENT_DATA: 'muted',
}
export const regimeTone = (value: unknown): RegimeTone => regimeTones[String(value)] ?? 'muted'
