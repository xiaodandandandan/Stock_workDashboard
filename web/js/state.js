// @ts-check

/** One owner per async view. Changing selection invalidates cached and pending work alike. */
class RequestGate {
  constructor() {
    this.generation = 0;
    /** @type {AbortController | null} */
    this.controller = null;
  }
  /** @returns {RequestTicket} */
  begin() {
    this.cancel();
    this.controller = new AbortController();
    return { id: this.generation, signal: this.controller.signal };
  }
  /** @param {RequestTicket} ticket */
  isCurrent(ticket) {
    return ticket.id === this.generation && !ticket.signal.aborted;
  }
  cancel() {
    this.generation++;
    if (this.controller) this.controller.abort();
    this.controller = null;
  }
}

/** @param {string} value @returns {SymbolId} */
function normalizeSymbol(value) {
  const c = String(value).trim().toLowerCase();
  if (/^(sh|sz|bj)[0-9]{6}$|^hk[0-9]{5}$/.test(c)) return /** @type {SymbolId} */ (c);
  if (/^[0-9]{5}$/.test(c)) return `hk${c}`;
  if (!/^[0-9]{6}$/.test(c)) throw new Error('股票代码格式不正确');
  return `${/^[65]/.test(c) ? 'sh' : /^[031]/.test(c) ? 'sz' : 'bj'}${c}`;
}

/** Keep the immediate predecessor/successor even when many bars separate endpoints.
 * @param {StrokePoint[]} points @param {number} a @param {number} b
 * @returns {StrokePoint[]}
 */
function sliceStrokePoints(points, a, b) {
  /** @type {StrokePoint[]} */
  const selected = [];
  /** @type {StrokePoint | null} */
  let before = null;
  for (const p of points) {
    if (p[0] < a) { before = p; continue; }
    selected.push(p);
    if (p[0] >= b) break;
  }
  if (before) selected.unshift(before);
  return selected.map(p => [p[0] - a, p[1], p[2]]);
}
