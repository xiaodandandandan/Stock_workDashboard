(async () => {
  const G = ms => new Promise(z => setTimeout(z, ms));
  const el = id => document.getElementById(id);
  const range = () => el('zmRange').textContent;
  // 画布上不透明像素数：用来确认「真的画上去了」——
  // canvas 停在 300×150 或整片空白时，画面看着像"这只票没结构"，而实际是渲染挂了。
  // ★ 不能用 "RGB<245" 统计：draw() 先 clearRect，未绘制区域是 (0,0,0,0) 的透明黑，
  //   按 RGB 统计会把整个背景算成"有内容"（实测 99.7%），其实等于没测。
  const ink = () => {
    const cv = el('zmcv');
    const ctx = cv.getContext('2d');
    const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
    return { n: n, w: cv.width, h: cv.height, pct: +(n / (cv.width * cv.height) * 100).toFixed(1) };
  };
  const point = (type, x, y, extra) => {
    const cv = el('zmcv');
    cv.dispatchEvent(new PointerEvent(type, Object.assign({
      bubbles: true, cancelable: true, clientX: x, clientY: y, pointerId: 1,
      pointerType: 'mouse', isPrimary: true, buttons: type === 'pointermove' ? 1 : 0,
    }, extra || {})));
  };

  const r = D.rows.find(x => (x.buy || []).length) || D.rows[0];
  Drawer.open({ code: r.code, name: r.name });
  await G(3200);

  const out = { name: r.name, code: r.code };
  out.drawerOnly = { drawer: el('drawer').classList.contains('on'), zoom: el('zoom').classList.contains('on') };

  el('dwZoom').click();
  await G(500);
  const cv = el('zmcv');
  const R = cv.getBoundingClientRect();
  const cx = R.left + R.width / 2, cy = R.top + R.height / 2;

  out.opened = {
    zoomOn: el('zoom').classList.contains('on'),
    title: el('zmTitle').textContent,
    range: range(),
    ink: ink(),
    layers: el('zmLayers').querySelectorAll('input').length,
    presets: [...el('zmPresets').querySelectorAll('button')].map(b => b.textContent).join(' | '),
    moreBtn: el('zmMore').style.display === 'none' ? '(隐藏)' : el('zmMore').textContent,
  };

  // ① 滚轮放大：锚在指针处，可见根数应当明显变少
  el('zmcv').parentNode.dispatchEvent(new WheelEvent('wheel',
    { bubbles: true, cancelable: true, clientX: cx, clientY: cy, deltaY: -120 }));
  await G(200);
  out.wheelIn = { range: range(), ink: ink() };

  // ② 拖动平移：根数不变、窗口左移
  point('pointerdown', cx, cy);
  point('pointermove', cx + 140, cy);
  point('pointerup', cx + 140, cy);
  await G(220);
  out.pan = { range: range(), ink: ink() };

  // ③ 预设「近 60 根」
  const p60 = [...el('zmPresets').querySelectorAll('button')].find(b => b.textContent.indexOf('60') >= 0);
  if (p60) { p60.click(); await G(220); }
  out.preset60 = { range: range(), on: p60 ? p60.classList.contains('on') : null, ink: ink() };

  // ④ 图层开关：放大视图里关掉均线，抽屉的勾选框与画面要一起变
  const maBox = el('zmLayers').querySelector('input[data-k="ma"]');
  maBox.checked = false;
  maBox.dispatchEvent(new Event('change', { bubbles: true }));
  await G(200);
  out.layer = {
    LAYERS_ma: LAYERS.ma,
    drawerBoxChecked: el('dwLayers').querySelector('input[data-k="ma"]').checked,
    ink: ink().n,
  };
  maBox.checked = true;
  maBox.dispatchEvent(new Event('change', { bubbles: true }));
  await G(150);

  // ⑤ 「加载更长历史」（日线缓存 800 根，抽屉窗口 250 根）
  if (el('zmMore').style.display !== 'none') {
    el('zmMore').click();
    await G(2500);
    out.more = { range: range(), ink: ink(), btn: el('zmMore').style.display };
  } else out.more = '(无按钮)';

  // ⑥ Esc：只关放大视图，抽屉要留着
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await G(300);
  out.afterEsc = {
    zoom: el('zoom').classList.contains('on'),
    drawer: el('drawer').classList.contains('on'),
  };

  // ⑦ 再打开一次（截图要看放大视图本身），顺手缩到近 60 根
  el('dwZoom').click();
  await G(400);
  const p2 = [...el('zmPresets').querySelectorAll('button')].find(b => b.textContent.indexOf('60') >= 0);
  if (p2) { p2.click(); await G(300); }
  // 悬停到中间那根，让 tooltip 也进画面
  const R2 = el('zmcv').getBoundingClientRect();
  el('zmcv').dispatchEvent(new PointerEvent('pointermove', {
    bubbles: true, clientX: R2.left + R2.width * 0.62, clientY: R2.top + R2.height * 0.4,
    pointerId: 2, pointerType: 'mouse', isPrimary: true,
  }));
  await G(250);
  out.final = { range: range(), tip: el('zmTip').textContent.replace(/\n/g, ' | ') };
  return out;
})()
