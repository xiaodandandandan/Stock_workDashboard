const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  function element() { return {style:{}, children:[], handlers:{}, clientWidth:360, scrollWidth:1750,
    append(...nodes){this.children.push(...nodes)}, insertBefore(node){this.children.push(node)},
    replaceChildren(){this.children=[]}, addEventListener(type,fn){this.handlers[type]=fn}}; }
  const nodes={detail:element(),reason:element(),rows:element()};
  nodes.rows.onclick=()=>{nodes.detail.open=true};
  const requests=[]; let draws=0;
  const context={document:{getElementById:id=>nodes[id],createElement:element,createTextNode:s=>s},
    window:{addEventListener(){},innerWidth:390,innerHeight:844}, location:{href:'https://example.com/site/'},
    shown:[{code:'000001'},{code:'600000'}],URL,LAYERS:{},
    fetch:url=>new Promise(resolve=>requests.push({url,resolve})),
    setupCanvas:()=>({}),draw:()=>draws++,bindTip:()=>{}};
  vm.runInNewContext(fs.readFileSync('deploy/pages-chart.js','utf8'),context);
  const click=i=>nodes.rows.onclick({target:{closest:()=>({dataset:{row:i}})}});
  const payload={date:'20261023',chart:{pack:{n:250},dates:[],opt:{},hist:[]}};
  return {nodes,requests,click,payload,get draws(){return draws}};
}

test('static charts ignore a late response for another stock and enable touch scrolling', async()=>{
  const h=harness(),a=h.click(0),b=h.click(1);
  h.requests[1].resolve({ok:true,json:async()=>h.payload}); await b;
  assert.equal(h.draws,1);
  h.requests[0].resolve({ok:true,json:async()=>h.payload}); await a;
  assert.equal(h.draws,1);
  const box=h.nodes.detail.children[0],scroll=box.children[3],canvas=scroll.children[0].children[0];
  assert.equal(canvas.style.touchAction,'pan-x pan-y');
  assert.match(box.children[0].textContent,/20261023/);
});

test('missing cached chart preserves access to the selected scan explanation',async()=>{
  const h=harness(),pending=h.click(0);
  h.requests[0].resolve({status:404}); await pending;
  const box=h.nodes.detail.children[0];
  assert.match(box.children[0].textContent,/缓存日线/);
  box.children[1].onclick(); assert.equal(h.nodes.reason.hidden,false);
  assert.equal(h.draws,0);
});
