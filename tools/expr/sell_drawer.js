(async () => {
  const r = D.rows.find(x => (x.sell || []).some(h => h.type === '一类卖点' && h.fresh <= 1))
         || D.rows.find(x => (x.sell || []).length);
  Drawer.open({ code: r.code, name: r.name });
  await new Promise(z => setTimeout(z, 3000));
  return {
    name: r.name, code: r.code,
    marks: (LAST.hist || []).map(h => h.side + h.n + '@' + h.i),
    cards: [...document.querySelectorAll('#dwCard .si')].length,
  };
})()
