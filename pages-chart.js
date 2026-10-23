/* Offline daily charts. No quote source or server API is requested. */
(() => {
  const dialog = document.getElementById('detail');
  const reason = document.getElementById('reason');
  const box = document.createElement('div');
  dialog.insertBefore(box, reason);
  let ticket = 0, repaint = null;
  const originalClick = document.getElementById('rows').onclick;
  document.getElementById('rows').onclick = async event => {
    const row = event.target.closest('tr[data-row]');
    if (!row) return;
    originalClick(event);
    const code = shown[row.dataset.row].code;
    const current = ++ticket;
    reason.hidden = true;
    box.replaceChildren();
    const status = document.createElement('p');
    status.className = 'hint'; status.textContent = '正在加载已导出的日K…'; box.append(status);
    const why = document.createElement('button');
    why.textContent = '展开所选扫描批次的信号判据';
    why.onclick = () => { reason.hidden = !reason.hidden; };
    box.append(why);
    try {
      const response = await fetch(new URL('charts/' + encodeURIComponent(code) + '.json', location.href));
      if (response.status === 404) throw Error('本次导出没有该股的缓存日线，仍可展开查看扫描判据。');
      if (!response.ok) throw Error('日K读取失败，请稍后重试。');
      const saved = await response.json();
      if (current !== ticket || !dialog.open) return;
      status.textContent = `日K截止 ${saved.date} · 最近 ${saved.chart.pack.n} 根 · 非实时行情。图表来自导出时的日线缓存，可能与所选历史扫描批次不同；图中买卖点按该日线重新计算。`;
      const controls = document.createElement('div'); controls.className = 'chart-controls'; box.append(controls);
      const scroll = document.createElement('div'); scroll.className = 'chart-scroll'; box.append(scroll);
      const inner = document.createElement('div'); scroll.append(inner);
      const canvas = document.createElement('canvas'); inner.append(canvas);
      const tip = document.createElement('div'); tip.className = 'chart-tip';tip.style.display = 'none';box.append(tip);
      let width = 7;
      repaint = () => {
        if (!dialog.open || current !== ticket) return;
        inner.style.width = Math.max(scroll.clientWidth, saved.chart.pack.n * width) + 'px';
        draw(setupCanvas(canvas, 430), saved.chart.pack, saved.chart.dates, saved.chart.opt, saved.chart.hist);
      };
      for (const [key, label] of [['bi','笔'],['fx','分型'],['zs','中枢'],['buy','买点'],['sell','卖点'],['ma','均线'],['macd','MACD']]) {
        LAYERS[key] = 1;
        const item = document.createElement('label'), checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.checked = true;
        checkbox.onchange = () => { LAYERS[key] = checkbox.checked ? 1 : 0; repaint(); };
        item.append(checkbox, document.createTextNode(label));controls.append(item);
      }
      for (const [text, value] of [['缩小',3],['默认',7],['放大',12]]) {
        const button = document.createElement('button');button.textContent = text;
        button.onclick = () => { width = value;repaint();scroll.scrollLeft = scroll.scrollWidth; };controls.append(button);
      }
      const instruction = document.createElement('p');instruction.className = 'hint';
      instruction.textContent = '左右滑动查看历史K线；点按K线查看价格，使用上方按钮缩放。';box.append(instruction);
      repaint();scroll.scrollLeft = scroll.scrollWidth;
      bindTip(canvas, saved.chart.pack, saved.chart.dates, saved.chart.hist, tip);
      canvas.style.touchAction = 'pan-x pan-y';
      const positionTip = event => {
        if (tip.style.display === 'none') return;
        const point = event.touches ? event.touches[0] : event;
        if (!point) return;
        tip.style.left = Math.max(4, Math.min(point.clientX + 12, window.innerWidth - tip.offsetWidth - 4)) + 'px';
        tip.style.top = Math.max(4, Math.min(point.clientY - tip.offsetHeight - 14, window.innerHeight - tip.offsetHeight - 4)) + 'px';
      };
      for (const type of ['pointerdown', 'pointermove', 'mousemove', 'touchstart', 'touchmove']) {
        canvas.addEventListener(type, positionTip, {passive:true});
      }
      scroll.addEventListener('scroll', () => { tip.style.display = 'none'; }, {passive:true});
    } catch (error) {
      if (current === ticket && dialog.open) status.textContent = error.message;
    }
  };
  dialog.addEventListener('close', () => { ticket++;repaint = null; });
  window.addEventListener('resize', () => { if (repaint) repaint(); });
})();
