(async () => {
  const r = D.rows.find(x => (x.buy || []).length) || D.rows[0];
  Drawer.open({ code: r.code, name: r.name });
  await new Promise(z => setTimeout(z, 3200));
  document.getElementById('mcv').scrollIntoView({ block: 'center' });
  await new Promise(z => setTimeout(z, 400));
  return { name: r.name, code: r.code, marks: (LAST.hist || []).length };
})()
