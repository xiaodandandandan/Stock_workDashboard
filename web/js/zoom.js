/* K 线放大视图 —— 把抽屉里那张图放大到整个窗口，可缩放 / 平移看细节。
 *
 * 为什么单独做一个视图，而不是"把抽屉的画布画大一点"：
 *   · 抽屉宽度被榜单挤着（最多 60vw），250 根挤在 1000px 里，一根不到 4px，
 *     笔的折点、中枢边界、背驰段的两块面积全糊在一起；
 *   · 放大之后真正需要的是**能缩能移** —— 看某一笔的内部结构、看 A2/A1 两段
 *     的面积对比、看信号报出那几根的量价变化。固定窗给不了这件事。
 *
 * 三条实现约束（改这个文件时别绕开）：
 *   1. 数据只来自抽屉已经拿到的那份 pack（dates 是全量日期轴），本模块做的是
 *      **窗口切片**，不重新取数 —— 唯一的例外是「加载更长历史」按钮，它显式请求
 *      lookback = 全部可用根数，服务端结果按天缓存，代价接近 0。
 *   2. 切片时把窗口 [a,b) 里所有索引整体减 a：笔 / 分型 / 中枢 / 买卖点标记 / 图上
 *      编号一个都不能漏，漏一处就是"K 线对了、中枢画在别处"。
 *   3. 笔端点在切片时**多留左右各一个窗口外的邻接端点**：缩到很小一段时窗口内
 *      可能一个笔端点都没有，不补的话图上会完全没有笔（看着像"这段没有笔"）。
 *      这些窗口外端点只参与画线，纵向取值域跳过它们（见 kline_canvas.draw）。
 */
const Zoom = (() => {
  const MIN_BARS = 24;        // 最少可见根数：再少就只剩几根柱子，笔和中枢无从谈起
  const INITIAL_BARS = 120;
  const SCALE = 1.35;         // 每档缩放倍率
  const PRESETS = [250, 120, 60];

  let S = null;               // 视图状态，见 open()
  let cv = null, tipApi = null, bound = false, raf = 0;
  const historyGate = new RequestGate();
  const pts = new Map();
  let drag = null, pinch = null;

  const el = id => document.getElementById(id);
  const isOn = () => el('zoom').classList.contains('on');

  /* ---------------- 时间轴标签 ---------------- */
  function ts(s) {
    s = String(s || '');
    if (s.length >= 12) return s.slice(4, 6) + '/' + s.slice(6, 8) + ' ' + s.slice(8, 10) + ':' + s.slice(10, 12);
    if (s.length >= 8) return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8);
    return s;
  }

  /* ---------------- 窗口 ---------------- */
  const NB = () => S.fd.n;                 // pack 里一共有多少根
  const LEN = () => S.b - S.a;             // 当前显示多少根

  function updateCursor() {
    if (!cv) return;
    cv.style.cursor = !S || LEN() === NB() ? 'crosshair' : (pts.size ? 'grabbing' : 'grab');
  }
  function resetGesture() {
    const captured = [...pts.keys()];
    pts.clear();
    drag = pinch = null;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    for (const id of captured) {
      try { if (cv?.hasPointerCapture(id)) cv.releasePointerCapture(id); } catch (err) { /* already released */ }
    }
    updateCursor();
  }

  function clampLen(len) {
    const n = NB();
    return Math.round(Math.min(n, Math.max(Math.min(MIN_BARS, n), len)));
  }
  function setWin(a, len) {
    const n = NB();
    len = clampLen(len);
    a = Math.round(Math.max(0, Math.min(n - len, a)));
    S.a = a;
    S.b = a + len;
  }
  // 以 cx（pack 下标，可以是小数）为锚点缩放到 len 根：锚点那根在屏幕上的位置不动
  function zoomAround(cx, len) {
    len = clampLen(len);
    const rel = (cx - S.a) / LEN();
    setWin(cx - rel * len, len);
  }
  // 贴右边缘（最新）取 len 根
  const latest = len => setWin(NB() - clampLen(len), len);
  function resetWindow() {
    resetGesture();
    latest(Math.min(INITIAL_BARS, NB()));
  }

  /* ---------------- 切片 ---------------- */
  function slice(a, b) {
    const fd = S.fd, n = b - a;
    const d = {
      n: n, d0: S.fdates[S.base + a],
      o: fd.o.slice(a, b), h: fd.h.slice(a, b), l: fd.l.slice(a, b),
      c: fd.c.slice(a, b), v: fd.v.slice(a, b),
    };
    if (fd.md) d.md = fd.md.slice(a, b);
    d.fx = (fd.fx || []).filter(p => p[0] >= a && p[0] < b).map(p => [p[0] - a, p[1], p[2]]);
    // ★ 多留一个窗口外的邻接端点（a-1 / b），见文件头第 3 条
    d.bi = sliceStrokePoints(fd.bi || [], a, b);
    if (fd.bi && fd.bi.length) d.biLast = fd.bi[fd.bi.length - 1][0] - a;
    d.zs = [];
    (fd.zs || []).forEach(z => {                       // 中枢带按窗口裁切，跨窗口的截到边上
      const i0 = Math.max(z[0], a), i1 = Math.min(z[1], b - 1);
      if (i1 > i0) d.zs.push([i0 - a, i1 - a, z[2], z[3]]);
    });
    if (fd.ar) d.ar = fd.ar.map(g => {                 // A2/A1 背驰框同理；整段在窗外就置空
      const s0 = Math.max(g[0], a), s1 = Math.min(g[1], b - 1);
      return s1 > s0 ? [s0 - a, s1 - a] : [-1, -1];
    });
    // 图上 1/2/3 编号 + tooltip 的历史信号：与抽屉同口径，只画落在窗口内的段
    const hist = (S.fhist || []).filter(h => h.i >= a && h.i < b)
      .map(h => Object.assign({}, h, { i: h.i - a }));
    // opt.buyIdx / sellIdx 是窗口相对下标，必须一起平移；移出窗口就删掉
    const opt = Object.assign({}, S.fopt);
    ['buyIdx', 'sellIdx'].forEach(k => {
      if (opt[k] == null) return;
      if (opt[k] >= a && opt[k] < b) opt[k] -= a; else delete opt[k];
    });
    return { d: d, opt: opt, hist: hist };
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    if (!S) return;
    const box = el('zmCw');
    const h = Math.max(240, box.clientHeight - 2);
    const sl = slice(S.a, S.b);
    draw(setupCanvas(cv, h), sl.d, S.fdates, sl.opt, sl.hist);
    if (tipApi) tipApi.update(sl.d, S.fdates, sl.hist);   // 监听器不重挂，只换数据
    meta();
  }
  function schedule() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; render(); });
  }

  function meta() {
    const n = NB(), len = LEN();
    el('zmRange').textContent =
      `显示 ${len} / ${n} 根　${ts(S.fdates[S.base + S.a])} ~ ${ts(S.fdates[S.base + S.b - 1])}`;
    el('zmRange').title = len < n ? '向右拖动查看更早 K 线，向左拖动返回较新 K 线' :
      '当前显示全部 K 线；点击“＋ 放大”或选择较短区间后即可拖动';
    updateCursor();
    el('zmIn').disabled = len <= Math.min(MIN_BARS, n);
    el('zmOut').disabled = len >= n;
    el('zmPresets').querySelectorAll('button').forEach(b => {
      const v = +b.dataset.len, target = v === 0 ? n : Math.min(v, n);
      b.classList.toggle('on', len === target && (v === 0 || S.b >= n));
    });
  }

  // 命中列：屏幕 x → pack 下标（小数，便于锚点缩放）。必须用 draw() 的同一套边距
  function idxAt(clientX) {
    const r = cv.getBoundingClientRect(), L0 = layout(r.width, LEN());
    const off = Math.max(0, Math.min(LEN() - 1, (clientX - r.left - L0.PL) / L0.bw));
    return S.a + off;
  }

  function buildPresets() {
    const n = NB();
    const items = [[0, '全部']].concat(
      PRESETS.filter(v => v < n).map(v => [v, '近 ' + v + ' 根']));
    el('zmPresets').innerHTML = items.map(([v, t]) =>
      `<button data-len="${v}">${t}</button>`).join('');
  }

  function buildLayers() {
    const LBL = (Drawer && Drawer.LAYER_LABELS) || {};
    el('zmLayers').innerHTML = Object.keys(LBL).map(k =>
      `<label><input type="checkbox" data-k="${k}" ${LAYERS[k] ? 'checked' : ''}>${LBL[k]}</label>`
    ).join('');
  }

  /* ---------------- 打开 / 关闭 ---------------- */
  function open() {
    const snap = Drawer.snapshot && Drawer.snapshot();
    if (!snap || !snap.d) return;
    resetGesture();
    historyGate.cancel();
    S = { code: snap.symbol || snap.code, name: snap.name, freq: snap.freq, avail: snap.bars || 0,
          fd: snap.d, fdates: snap.dates, fopt: snap.opt || {}, fhist: snap.hist || [], base: 0 };
    S.base = baseOf(S.fd, S.fdates);
    resetWindow();

    el('zmTitle').textContent = (snap.name || snap.code) + ' · ' + (snap.unit || '');
    const more = S.avail > S.fd.n;
    el('zmMore').style.display = more ? '' : 'none';
    el('zmMore').disabled = false;
    if (more) el('zmMore').textContent = `加载更长历史（共 ${S.avail} 根）`;

    el('zoom').classList.add('on');
    el('zoom').setAttribute('aria-hidden', 'false');
    buildPresets();
    buildLayers();
    render();
    if (!tipApi) {
      const sl = slice(S.a, S.b);
      tipApi = bindTip(cv, sl.d, S.fdates, sl.hist, el('zmTip'));
      // bindTip 默认把 touch-action 设成 pan-y（给页面留翻页）；放大视图要吃掉纵向手势做平移
    }
    cv.style.touchAction = 'none';
  }

  function close() {
    historyGate.cancel();
    resetGesture();
    el('zoom').classList.remove('on');
    el('zoom').setAttribute('aria-hidden', 'true');
    if (tipApi && tipApi.hide) tipApi.hide();
    S = null;
    updateCursor();
  }

  // d0 → 在全量 dates 里的下标。★ 严格比较必须转字符串（历史坑，见 kline_canvas.dateStart）
  function baseOf(fd, dates) {
    const t = String(fd.d0);
    for (let i = 0; i < dates.length; i++) if (String(dates[i]) === t) return i;
    return Math.max(0, dates.length - (fd.n || 0));
  }

  /* ---------------- 加载更长历史 ---------------- */
  async function loadMore() {
    if (!S || S.avail <= NB()) return;
    const view = S, ticket = historyGate.begin();
    const btn = el('zmMore');
    btn.disabled = true;
    btn.textContent = '载入中…';
    try {
      const p = await API.stock(view.code, view.freq, { lookback: view.avail }, ticket.signal);
      if (!historyGate.isCurrent(ticket) || S !== view) return;
      if (!p || !p.chart || !p.chart.pack) throw new Error('返回数据不完整');
      const firstDate = String(S.fdates[S.base + S.a]), visible = LEN();
      resetGesture();
      S.fd = p.chart.pack;
      S.fdates = p.chart.dates;
      S.fopt = p.chart.opt || {};
      S.fhist = p.chart.hist || [];
      S.base = baseOf(S.fd, S.fdates);
      S.avail = p.bars || S.fd.n;
      const first = S.fdates.findIndex(date => String(date) === firstDate) - S.base;
      if (first >= 0) setWin(first, visible); else latest(visible);
      btn.style.display = 'none';
      buildPresets();
      render();
    } catch (e) {
      if (!historyGate.isCurrent(ticket) || S !== view) return;
      btn.textContent = '载入失败：' + e.message;
    } finally {
      if (historyGate.isCurrent(ticket) && S === view) btn.disabled = false;
    }
  }

  /* ---------------- 事件（只绑一次） ---------------- */
  function bindOnce() {
    if (bound) return;
    bound = true;

    cv = document.createElement('canvas');
    cv.id = 'zmcv';
    el('zmCw').appendChild(cv);

    el('zmClose').addEventListener('click', close);
    el('zoom').addEventListener('click', e => { if (e.target === el('zoom')) close(); });
    el('zmIn').addEventListener('click', () => { zoomAround((S.a + S.b) / 2, LEN() / SCALE); render(); });
    el('zmOut').addEventListener('click', () => { zoomAround((S.a + S.b) / 2, LEN() * SCALE); render(); });
    el('zmReset').addEventListener('click', () => { resetWindow(); render(); });
    el('zmLast').addEventListener('click', () => { latest(LEN()); render(); });
    el('zmMore').addEventListener('click', loadMore);

    el('zmPresets').addEventListener('click', e => {
      const b = e.target.closest('button[data-len]');
      if (!b) return;
      const v = +b.dataset.len;
      if (v === 0) setWin(0, NB()); else latest(v);
      render();
    });

    el('zmLayers').addEventListener('change', e => {
      const k = e.target.dataset.k;
      if (!k) return;
      LAYERS[k] = e.target.checked ? 1 : 0;
      // 图层是全局的：放大视图里改完，抽屉的勾选和画面都要跟着走，否则关掉再开对不上
      if (Drawer.syncLayers) Drawer.syncLayers();
      if (Drawer.paint) Drawer.paint();
      render();
    });

    // 滚轮缩放：锚在指针那一根上
    el('zmCw').addEventListener('wheel', e => {
      if (!S) return;
      e.preventDefault();
      if (!e.deltaY) return;
      zoomAround(idxAt(e.clientX), LEN() * (e.deltaY < 0 ? 1 / SCALE : SCALE));
      render();
    }, { passive: false });

    // 拖动平移 / 双指缩放。用 Pointer Events 一套吃鼠标 + 触摸 + 触控笔
    const pinchStart = () => {
      const p = [...pts.values()];
      const d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1;
      return { d: d, len: LEN(), idx: idxAt((p[0].x + p[1].x) / 2), a: S.a };
    };
    const pinchMove = () => {
      const p = [...pts.values()];
      const d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1;
      const rel = (pinch.idx - pinch.a) / pinch.len;
      const nl = clampLen(pinch.len * (pinch.d / d));
      setWin(pinch.idx - rel * nl, nl);
      schedule();
    };

    cv.addEventListener('pointerdown', e => {
      if (!isOn() || !S) return;
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      // ★ try 一下：指针在事件到达前已经抬起（或合成事件没有活跃指针）时，
      //   setPointerCapture 会抛 NotFoundError，整段平移就哑了 —— 抓不到就算了，
      //   后面的 pointermove 照样能收到。
      try { cv.setPointerCapture(e.pointerId); } catch (err) { /* 抓不到不致命 */ }
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 1) { drag = { x: e.clientX, a: S.a, len: LEN() }; pinch = null; }
      else if (pts.size === 2) { pinch = pinchStart(); drag = null; }
      updateCursor();
    });

    cv.addEventListener('pointermove', e => {
      if (!S || !pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size >= 2) { if (pinch) pinchMove(); return; }
      if (!drag) return;
      const bw = layout(cv.getBoundingClientRect().width, LEN()).bw;
      setWin(drag.a - (e.clientX - drag.x) / bw, drag.len);
      schedule();
    });

    const endPointer = e => {
      if (!pts.has(e.pointerId)) return;
      pts.delete(e.pointerId);
      if (!S || pts.size === 0) { drag = pinch = null; }
      else if (pts.size === 1) {
        const remaining = [...pts.values()][0];
        drag = { x: remaining.x, a: S.a, len: LEN() };
        pinch = null;
      } else { pinch = pinchStart(); drag = null; }
      updateCursor();
    };
    cv.addEventListener('pointerup', endPointer);
    cv.addEventListener('pointercancel', endPointer);
    cv.addEventListener('lostpointercapture', endPointer);

    // 双击：以点击处为中心放大一倍；已经放到最小了就复位 —— 一个手势覆盖「放大 / 退回」
    cv.addEventListener('dblclick', e => {
      if (!S) return;
      if (LEN() <= Math.min(MIN_BARS, NB())) resetWindow();
      else zoomAround(idxAt(e.clientX), LEN() / 2);
      render();
    });

    document.addEventListener('keydown', e => {
      if (!isOn() || !S) return;
      const mid = (S.a + S.b) / 2;
      if (e.key === 'Escape') close();
      else if (e.key === '+' || e.key === '=') { zoomAround(mid, LEN() / SCALE); render(); }
      else if (e.key === '-' || e.key === '_') { zoomAround(mid, LEN() * SCALE); render(); }
      else if (e.key === '0') { resetWindow(); render(); }
      else if (e.key === 'ArrowLeft') { setWin(S.a - Math.max(1, LEN() * 0.2), LEN()); render(); }
      else if (e.key === 'ArrowRight') { setWin(S.a + Math.max(1, LEN() * 0.2), LEN()); render(); }
      else return;
      e.preventDefault();
    });

    window.addEventListener('resize', () => { if (isOn()) render(); });
  }

  bindOnce();
  // ★ 必须挂到 window 上：classic script 里顶层 `const Zoom` 只是**词法全局**，
  //   不是 window 的属性 —— 别的模块（抽屉的 Esc 分流）用 window.Zoom 判断会拿到
  //   undefined，于是「放大视图开着时按 Esc」会连抽屉一起关掉。
  window.Zoom = { open, close, isOpen: isOn };
  return { open, close, isOpen: isOn };
})();
