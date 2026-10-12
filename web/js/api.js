// @ts-check
/* Typed boundary; classic scripts and Python-only deployment are retained. */
class ApiError extends Error {
  /** @param {string} message @param {number} status */
  constructor(message, status) { super(message); this.status = status; }
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }

/** @param {unknown} value @returns {StockPayload} */
function readStockPayload(value) {
  if (!isRecord(value) || typeof value.symbol !== 'string' || typeof value.freq !== 'string' ||
      !isRecord(value.chart) || !isRecord(value.chart.pack) || !Array.isArray(value.chart.dates) ||
      !Array.isArray(value.chart.hist) || !isRecord(value.signals) ||
      !Array.isArray(value.signals.buy) || !Array.isArray(value.signals.sell)) {
    throw new Error('个股接口返回数据不完整');
  }
  const pack = value.chart.pack;
  if (typeof pack.n !== 'number' || !Number.isInteger(pack.n) || pack.n <= 0 ||
      value.chart.dates.length < pack.n ||
      ['o', 'h', 'l', 'c', 'v'].some(k => !Array.isArray(pack[k]) || pack[k].length !== pack.n ||
        !pack[k].every(x => typeof x === 'number' && Number.isFinite(x)))) {
    throw new Error('K 线数组长度或数值不正确');
  }
  return /** @type {StockPayload} */ (/** @type {unknown} */ (value));
}

const API = {
  base: '',

  /** @param {string} path
   * @param {Record<string, string | number | boolean | undefined>} [params]
   * @param {RequestInit} [init]
   * @returns {Promise<unknown>}
   */
  async request(path, params = {}, init = {}) {
    const u = new URL(this.base + path, location.origin);
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined) u.searchParams.set(k, String(v)); });
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (init.signal?.aborted) abort();
    else init.signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, 90000);
    try {
      const r = await fetch(u, { ...init, cache: 'no-store', signal: controller.signal });
      /** @type {unknown} */
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new ApiError(isRecord(body) && typeof body.error === 'string' ? body.error : 'HTTP ' + r.status, r.status);
      if (body === null) throw new Error('接口没有返回 JSON 数据');
      return body;
    } finally {
      clearTimeout(timer);
      init.signal?.removeEventListener('abort', abort);
    }
  },

  health() { return this.request('/api/health'); },
  /** @param {string} [snapshot] @param {AbortSignal} [signal] @returns {Promise<ScanPayload>} */
  async scan(snapshot = '', signal) {
    const body = await this.request('/api/scan', { snapshot: snapshot || undefined }, { signal });
    if (!isRecord(body) || !Array.isArray(body.rows)) throw new Error('榜单数据不完整');
    return /** @type {ScanPayload} */ (/** @type {unknown} */ (body));
  },
  /** @returns {Promise<{snapshots: ScanSnapshot[]}>} */
  async scans() {
    const body = await this.request('/api/scans');
    if (!isRecord(body) || !Array.isArray(body.snapshots)) throw new Error('扫描历史数据不完整');
    return /** @type {{snapshots: ScanSnapshot[]}} */ (/** @type {unknown} */ (body));
  },
  /** @param {string} snapshot @param {string[]} codes @param {boolean} [refresh]
   * @param {AbortSignal} [signal] @returns {Promise<TrackingPayload>} */
  async tracking(snapshot, codes, refresh = false, signal) {
    const body = await this.request('/api/tracking', { snapshot: snapshot || undefined,
      codes: codes.join(','), refresh: refresh ? 1 : 0 }, { signal });
    if (!isRecord(body) || !isRecord(body.quotes)) throw new Error('跟踪行情数据不完整');
    return /** @type {TrackingPayload} */ (/** @type {unknown} */ (body));
  },
  /** @returns {Promise<ScanStatus>} */
  async scanStatus() { return /** @type {ScanStatus} */ (await this.request('/api/scan/status')); },
  /** @param {string} code @param {Frequency} freq
   * @param {{lookback?: number, report?: 0 | 1}} [opts]
   * @param {AbortSignal} [signal] @returns {Promise<StockPayload>}
   */
  async stock(code, freq, opts = {}, signal) {
    const symbol = normalizeSymbol(code);
    const data = readStockPayload(await this.request('/api/stock', { ...opts, code: symbol, freq }, { signal }));
    if (data.symbol !== symbol || data.freq !== freq) throw new Error('返回的股票或周期与请求不一致');
    return data;
  },
  /** @param {string} code @param {boolean} [refresh] @param {AbortSignal} [signal]
   * @returns {Promise<FundamentalPayload>} */
  async fundamentals(code, refresh = false, signal) {
    const symbol = normalizeSymbol(code);
    const body = await this.request('/api/fundamentals', { code: symbol, refresh: refresh ? 1 : 0 }, { signal });
    if (!isRecord(body) || body.schema !== 2 || body.symbol !== symbol || !isRecord(body.sections) || !isRecord(body.report) ||
        !Array.isArray(body.report.kpis) || !Array.isArray(body.report.conclusions) ||
        !Array.isArray(body.report.risk_exposure) || !Array.isArray(body.report.regulatory_checks) ||
        !Array.isArray(body.report.cash_quality) || !Array.isArray(body.report.scenarios) ||
        !Array.isArray(body.report.checklist) || !Array.isArray(body.report.valuation_notes) ||
        !isRecord(body.report.verification) || !Array.isArray(body.report.verification.links) ||
        !Array.isArray(body.summary) || !Array.isArray(body.sources) ||
        ['profile', 'financials', 'valuation', 'news'].some(key => {
          const section = /** @type {Record<string, unknown>} */ (body.sections)[key];
          return !isRecord(section) || !['ok', 'empty', 'failed'].includes(String(section.status)) || !isRecord(section.data);
        })) throw new Error('基本面接口返回的股票或资料格式不正确');
    return /** @type {FundamentalPayload} */ (/** @type {unknown} */ (body));
  },
  /** @param {ScanOptions} [body] @returns {Promise<{started: boolean, reason: string}>} */
  async runScan(body = {}) {
    return /** @type {{started: boolean, reason: string}} */ (await this.request('/api/scan', {}, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }));
  },
};
