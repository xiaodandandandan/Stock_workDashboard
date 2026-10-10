/* 后端调用的唯一出口。
 *
 * 为什么要单独一层：端口、超时、错误文案、要加的鉴权/埋点，都属于「基础设施」，
 * 散在各个组件里迟早不一致。新组件只准从这里发请求。
 */
const API = {
  base: '',                       // 同源部署（server.py 既托管页面也提供接口）

  async get(path, params) {
    const u = new URL(path, location.origin);
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null) u.searchParams.set(k, v);
    });
    const r = await fetch(u, { cache: 'no-store' });
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { msg = (await r.json()).error || msg; } catch (e) { /* 非 JSON 错误体 */ }
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return r.json();
  },

  health() { return this.get('/api/health'); },
  scan() { return this.get('/api/scan'); },
  scanStatus() { return this.get('/api/scan/status'); },
  stock(code, freq, opts) {
    return this.get('/api/stock', Object.assign({ code, freq }, opts || {}));
  },

  async runScan(body) {
    const r = await fetch(this.base + '/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  },
};
