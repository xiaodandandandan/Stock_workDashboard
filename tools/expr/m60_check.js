(async () => {
  Drawer.open({ code: '920007', name: '酉立智能' }, 'm60');
  await new Promise(z => setTimeout(z, 5000));
  const card = document.getElementById('dwCard');
  return {
    meta: document.getElementById('dwMeta').innerText.replace(/\s+/g, ' ').slice(0, 90),
    cards: card.querySelectorAll('.wi').length,
    marks: (LAST && LAST.hist || []).length,
    heads: [...card.querySelectorAll('.why-h')].map(x => x.textContent.trim()).join(' / '),
    foot: (card.querySelector('.wi-f') || {}).innerText || '',
  };
})()
