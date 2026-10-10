(async () => {
  const G = ms => new Promise(z => setTimeout(z, ms));
  const el = id => document.getElementById(id);
  const ink = () => {
    const cv = el('zmcv');
    const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
    return { n: n, pct: +(n / (cv.width * cv.height) * 100).toFixed(1) };
  };

  const r = D.rows.find(x => (x.buy || []).length) || D.rows[0];
  Drawer.open({ code: r.code, name: r.name });
  await G(3000);

  // 切 60 分钟：分钟线是现取现算的，放大视图必须也能用（日期轴是 12 位字符串）
  [...el('dwFreq').querySelectorAll('button[data-freq]')]
    .find(b => b.dataset.freq === 'm60').click();
  await G(4000);

  const out = { name: r.name, code: r.code, freqRange: el('dwMeta').textContent.slice(0, 60) };

  // 双击抽屉里的图 → 应当直接进放大视图
  const dv = el('mcv');
  dv.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  await G(600);
  out.afterDblclick = {
    zoomOn: el('zoom').classList.contains('on'),
    title: el('zmTitle').textContent,
    range: el('zmRange').textContent,
    ink: ink(),
  };

  // 悬停取一根，看时间轴是不是带时分
  const R = el('zmcv').getBoundingClientRect();
  el('zmcv').dispatchEvent(new PointerEvent('pointermove', {
    bubbles: true, clientX: R.left + R.width * 0.7, clientY: R.top + R.height * 0.35,
    pointerId: 3, pointerType: 'mouse', isPrimary: true,
  }));
  await G(250);
  out.hover = el('zmTip').textContent.replace(/\n/g, ' | ');

  // 缩一档看细节
  el('zmIn').click();
  el('zmIn').click();
  await G(250);
  out.afterZoomIn = { range: el('zmRange').textContent, ink: ink() };

  out.layers = [...el('zmLayers').querySelectorAll('input')].length;
  return out;
})()
