/* =============================================================================
 * 缠论 K 线 Canvas 绘图层 —— 零依赖，抽取自 chanlun 看板弹窗（实测一致）
 *
 * ★ 这是**项目内副本**（来源：~/.workbuddy/skills/chanlun-kline/scripts/kline_canvas.js）。
 *   有意分叉的几处差异，同步技能时不要覆盖掉：
 *     1. 新增 `sell` 图层（LAYERS.sell / opt.sellIdx / LAB.sellTag）—— 技能原版只有买点，
 *        而工作台有卖点 tab，点进去看不到卖点标记会和榜单自相矛盾；
 *     2. 顶部标签加了避让：买卖点挨得近时卖点标签下移一行，避免叠字；
 *     3. tooltip 里同一根 K 线上的历史信号**不再 break** —— 一根上可能同时压着买点和
 *        卖点（或一类 + 三类），只显示第一个会让人以为「这根只有一个信号」；
 *     4. 抽出 `layout(W,n)`（左右边距 / 每根宽度），draw / bindTip 共用 —— 供放大视图
 *        在任意窗口宽度下算同一条命中列；
 *     5. `bindTip` 支持自定义 tooltip 容器，并返回 { update } 换数据不重挂监听器；
 *     6. 纵向取值域跳过窗口外的笔端点（放大视图会故意多带邻接端点用于连线）。
 *     7. 跨窗口的笔按原坐标裁剪，保留斜率；仅原序列最后一笔标虚线。
 *
 * 用法：
 *   var cv = document.getElementById('cv');
 *   var ctx = setupCanvas(cv, 510);          // 返回已 scale(dpr) 的 ctx
 *   draw(ctx, d, dates, opt);                // d 来自 chanlun_core.pack()
 *   bindTip(cv, d, dates);                   // 悬停十字光标
 *
 * 数据约定（前端约定，务必与 pack() 对齐）：
 *   d.o/h/l/c  价格 ×100 的整数
 *   d.v        成交量（原值）
 *   d.md       MACD 柱 ×1000 的整数
 *   d.fx       [[i, t, p]]      分型  t=1 顶 / -1 底
 *   d.bi       [[i, t, p]]      笔端点
 *   d.zs       [[i0,i1,ZD,ZG]]  中枢（价格也是 ×100）
 *   d.ar       [[a2起,a2止],[a1起,a1止]]
 *   dates      全局交易日序列 ['20260101', ...]，用于把 d.d0 换算成横轴起点
 * ========================================================================== */

var dpr = Math.min(window.devicePixelRatio || 1, 2);

// 中国习惯：涨红跌绿
var COL = {up:'#dc2626', dn:'#16a34a', ma5:'#f59e0b', ma10:'#3b82f6', ma20:'#8b5cf6',
           grid:'#eef0f2', tx:'#8b95a1', zg:'#2563eb', stop:'#dc2626', buy:'#7c3aed',
           sell:'#0d9488'};   // 卖点用青绿 —— 和「跌绿」区分开，避免和 K 线颜色混

// 术语表。默认是股票分析语境的措辞；「纯技术分析」模式由页面注入 window.CL_LAB 覆盖。
// ★ 为什么要可覆盖：「买点 / 入场 / 止损 / 至今涨跌」在内容平台语境里等同于投顾建议
//   （小红书《社区金融生态公约》明列「明示或暗示个股买卖价位」为非法荐股），
//   换成纯几何描述后同一张图就是形态复盘，不是操作指令。
var LAB = {
  stop: '止损',            // 止损线标注
  buyTag: '◆买点',         // 当前信号的图上标签
  sellTag: '◆卖点',        // 卖点标签（项目内副本新增）
  hist: '历史',            // tooltip 里历史信号的定语
  typeSuffix: '类买点',     // 拼成「历史一类买点」
  anonType: 0,             // 1 = 用 Ⅰ/Ⅱ/Ⅲ 型结构 代替「X类买点」
  liveOn: '（引擎持续报出）',
  entry: '入场',           // 复盘价
  alive: '存活',           // 存活根数
  now: '至今',             // 至今涨跌
  hideNow: 0               // 1 = 不显示收益百分比（纯技术分析模式）
};
if (typeof window !== 'undefined' && window.CL_LAB) {
  for (var _lk in window.CL_LAB) { if (window.CL_LAB[_lk] !== undefined) LAB[_lk] = window.CL_LAB[_lk]; }
}

// 7 个图层开关，默认全开。关掉一个就重绘，不重新拉数据。
var LAYERS = {bi:1, fx:1, zs:1, buy:1, sell:1, histbuy:1, ma:1, macd:1};

var LAST = {d:null, opt:null};

function setupCanvas(cv, h) {
  h = h || 510;
  // ★ 宽度取父容器实测值；预览器 / 隐藏容器里父元素可能量到 0，退回视口宽度。
  //   再兜一个 240 下限：宽度为 0 会让 bw = 0，X() 全部塌到一点上（图变一条竖线）。
  var w = cv.parentElement ? cv.parentElement.clientWidth : 0;
  if (!w) w = window.innerWidth || 900;
  if (w < 240) w = 240;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  cv.style.width = '100%';       // 双保险：外部样式被覆盖时仍按容器宽显示
  cv.style.height = h + 'px';
  var ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);           // ★ 不 scale 的话高 DPI 屏上线条发虚
  return ctx;
}

function redraw() {
  if (!LAST.d) return;
  var cv = document.getElementById('mcv');
  draw(setupCanvas(cv, LAST.h || 510), LAST.d, LAST.dates, LAST.opt || {}, LAST.hist);
}

// d0 → 在 dates 里的起始下标。
// ★ 必须宽松比较（String()）：历史上 pack 输出的 d0 曾是 int，
//   而 dates 是字符串数组，严格 indexOf 必然 -1，导致日期轴整体错位。
// ★ 兜底只能是「取末尾 n 根」，绝不能退成 0 —— 退 0 会取到数据最前面那一段，
//   图看着完全正常但日期全错（不报错的错，最难查）。
function dateStart(dates, d, n) {
  if (!dates || !d) return 0;
  var t = String(d.d0);
  for (var i = 0; i < dates.length; i++) {
    if (String(dates[i]) === t) return i;
  }
  return Math.max(0, dates.length - (n || d.n || 0));
}

function ma(arr, n) {
  var r = [];
  for (var i = 0; i < arr.length; i++) {
    if (i < n - 1) { r.push(null); continue; }
    var s = 0;
    for (var j = 0; j < n; j++) s += arr[i - j];
    r.push(s / n);
  }
  return r;
}

// 窄屏（手机）判定阈值。手机宽度多在 320~430px，桌面/平板在 520px 以上。
// ★ 只按「画布宽度」判断，不用 UA —— 分屏窗口、iPad 竖屏同样是窄的。
function isNarrow(w) { return w < 520; }

// 横向布局：左右边距 + 每根宽度。
// ★ draw() / bindTip() / 放大视图三处必须共用这一个函数。以前是各写一份常量，
//   只要有一处忘了同步，tooltip 的命中列就整体偏移 —— 现象是「十字光标对不上 K 线」，
//   不报错、不当机，只让人觉得「这图很飘」。
function layout(W, n) {
  var nar = isNarrow(W);
  var PL = nar ? 40 : 52, PR = nar ? 10 : 14, cw = W - PL - PR;
  return { nar: nar, PL: PL, PR: PR, cw: cw, bw: cw / n };
}

// 右对齐文字标签 + 白底：压在 Y 轴刻度或 K 线上也看得清
// nar=true（窄屏）时缩小字号与内边距，否则标签会盖住半张图
function tag(ctx, txt, rightX, y, col, nar) {
  ctx.font = (nar ? 9.5 : 10.5) + 'px system-ui,sans-serif';
  ctx.textAlign = 'left';
  var w = ctx.measureText(txt).width, pol = nar ? 2 : 3, x = rightX - w - 2;
  ctx.fillStyle = 'rgba(255,255,255,.86)';
  ctx.fillRect(x - pol, y - 10, w + pol * 2, nar ? 12 : 13);
  ctx.fillStyle = col;
  ctx.fillText(txt, x, y);
}

function draw(ctx, d, dates, opt, hist) {
  opt = opt || {};
  var W = ctx.canvas.width / dpr, H = ctx.canvas.height / dpr;
  ctx.clearRect(0, 0, W, H);

  // ★ 窄屏自适应：手机上固定 PL=52 会吃掉 15% 画布宽度，K 线被压成一条窄带，
  //   副图也各占 46/64px 固定高度。按画布宽度收窄边距、字号与副图高度。
  var L0 = layout(W, d.n);
  var nar = L0.nar, PL = L0.PL, PR = L0.PR, cw = L0.cw;
  var PT = 12, PB = nar ? 18 : 20;
  var macdH = (LAYERS.macd && d.md && d.md.length) ? (nar ? 52 : 64) : 0;
  var volH = nar ? 36 : 46, gap = nar ? 12 : 16;
  var mainH = H - PT - PB - volH - gap - (macdH ? macdH + gap : 0);
  var volY = PT + mainH + gap, macdY = volY + volH + gap;
  var n = d.n;

  var start = dateStart(dates, d, n);
  var dts = dates.slice(start, start + n);
  var bw = L0.bw, bwd = Math.max(1, Math.min(bw * 0.66, 11));

  // ---- 纵轴范围：K 线 + ZG/ZD + 止损 + **笔端点**
  // ★ 笔端点必须纳进来，否则最后一笔的高/低点会被裁在框外
  // ★ 放大视图里 bi 会带上窗口外的邻接端点（让线段画到边框上、不出现断头）——
  //   那些点只参与画线，**不参与纵向取值域**，否则一放大就会被窗口外的远端笔端点撑扁。
  var hi = -1e9, lo = 1e9;
  for (var i = 0; i < n; i++) { if (d.h[i] > hi) hi = d.h[i]; if (d.l[i] < lo) lo = d.l[i]; }
  if (opt.zg) { hi = Math.max(hi, opt.zg * 100); lo = Math.min(lo, opt.zd * 100); }
  if (opt.stop) lo = Math.min(lo, opt.stop * 100);
  if (LAYERS.bi && d.bi) {
    for (var q = 0; q < d.bi.length; q++) {
      if (d.bi[q][0] < 0 || d.bi[q][0] > n - 1) continue;
      if (d.bi[q][2] > hi) hi = d.bi[q][2];
      if (d.bi[q][2] < lo) lo = d.bi[q][2];
    }
  }
  var pad = (hi - lo) * 0.06 || 1; hi += pad; lo -= pad;

  var Y = function (v) { return PT + (hi - v) / (hi - lo) * mainH; };
  var X = function (i) { return PL + (i < 0 ? 0 : (i > n - 1 ? n - 1 : i)) * bw + bw / 2; };

  // ---- 网格 + Y 轴
  ctx.strokeStyle = COL.grid; ctx.lineWidth = 1;
  ctx.font = (nar ? 9.5 : 11) + 'px system-ui,sans-serif'; ctx.textAlign = 'right';
  for (var g = 0; g <= 4; g++) {
    var v = lo + (hi - lo) * g / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke();
    ctx.fillStyle = COL.tx; ctx.fillText((v / 100).toFixed(2), PL - 5, y + 3.5);
  }
  // X 轴。★ 窄屏少放刻度：手机上一个日期标签约 30px 宽，7 个标签在 300px 里会互相盖住
  ctx.textAlign = 'center';
  ctx.font = (nar ? 9.5 : 11) + 'px system-ui,sans-serif';
  var step = Math.ceil(n / (nar ? 4 : 7));
  for (var i2 = 0; i2 < n; i2 += step) {
    var s = String(dts[i2] || ''), lb = s.slice(4, 6) + '/' + s.slice(6, 8);
    ctx.fillStyle = COL.tx; ctx.fillText(lb, X(i2), H - 6);
  }

  // ① 中枢 —— 只画最后 3 个。画多了区间重叠，糊成一片看不出个数
  if (LAYERS.zs && d.zs && d.zs.length) {
    d.zs.slice(-3).forEach(function (z) {
      var x0 = X(z[0]), x1 = X(z[1]), yG = Y(z[3]), yD = Y(z[2]);
      if (x1 <= x0 || yD <= yG) return;
      ctx.fillStyle = 'rgba(217,119,6,.09)';
      ctx.fillRect(x0, yG, x1 - x0, yD - yG);
      ctx.strokeStyle = 'rgba(217,119,6,.5)'; ctx.lineWidth = 0.8;
      ctx.setLineDash([3, 2]); ctx.strokeRect(x0, yG, x1 - x0, yD - yG); ctx.setLineDash([]);
    });
  }

  // ② 本信号的中枢带 ZG / ZD（贯穿全图，与本信号直接相关）
  if (opt.zg && opt.zd) {
    ctx.fillStyle = 'rgba(37,99,235,.07)';
    ctx.fillRect(PL, Y(opt.zg * 100), cw, Y(opt.zd * 100) - Y(opt.zg * 100));
    ctx.setLineDash([5, 3]); ctx.lineWidth = 1.2; ctx.strokeStyle = COL.zg;
    ctx.beginPath(); ctx.moveTo(PL, Y(opt.zg * 100)); ctx.lineTo(W - PR, Y(opt.zg * 100)); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(PL, Y(opt.zd * 100)); ctx.lineTo(W - PR, Y(opt.zd * 100)); ctx.stroke();
    ctx.setLineDash([]);
    tag(ctx, 'ZG ' + (opt.zg * 1).toFixed(2), W - PR, Y(opt.zg * 100) - 11, COL.zg, nar);
    tag(ctx, 'ZD ' + (opt.zd * 1).toFixed(2), W - PR, Y(opt.zd * 100) + 1, COL.zg, nar);
  }

  // 止损线
  if (opt.stop) {
    ctx.setLineDash([3, 3]); ctx.strokeStyle = COL.stop; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PL, Y(opt.stop * 100)); ctx.lineTo(W - PR, Y(opt.stop * 100));
    ctx.stroke(); ctx.setLineDash([]);
    // ★ 止损位与中枢下沿 ZD 重合时，两个标签会同坐标叠死 —— 错开一行
    var dyS = (opt.zd && Math.abs(opt.stop - opt.zd) < 0.5) ? 15 : 2;
    tag(ctx, LAB.stop + ' ' + (opt.stop * 1).toFixed(2), W - PR, Y(opt.stop * 100) + dyS, COL.stop, nar);
  }

  // K 线
  for (var i3 = 0; i3 < n; i3++) {
    var o = d.o[i3], h = d.h[i3], l = d.l[i3], c = d.c[i3];
    var up = c >= o, col = up ? COL.up : COL.dn, x = X(i3);
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, Y(h)); ctx.lineTo(x, Y(l)); ctx.stroke();
    var yo = Y(o), yc = Y(c), top = Math.min(yo, yc), hh = Math.max(Math.abs(yc - yo), 1);
    if (up) { ctx.fillStyle = col; ctx.fillRect(x - bwd / 2, top, bwd, hh); }
    else {
      ctx.fillStyle = '#fff'; ctx.fillRect(x - bwd / 2, top, bwd, hh);
      ctx.strokeStyle = col; ctx.strokeRect(x - bwd / 2, top, bwd, hh);
    }
  }

  // 均线
  if (LAYERS.ma) {
    [[5, COL.ma5], [10, COL.ma10], [20, COL.ma20]].forEach(function (p) {
      var a = ma(d.c, p[0]);
      ctx.strokeStyle = p[1]; ctx.lineWidth = 1.1; ctx.beginPath();
      var mv = false;
      for (var i = 0; i < n; i++) {
        if (a[i] == null) continue;
        var x = X(i), y = Y(a[i]);
        if (!mv) { ctx.moveTo(x, y); mv = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });
  }

  // 笔：上笔红 / 下笔绿；**最后一笔虚线**（尚未确认，可能还会变）
  if (LAYERS.bi && d.bi && d.bi.length > 1) {
    ctx.save();
    ctx.beginPath(); ctx.rect(PL, PT, cw, mainH); ctx.clip();
    // Outside endpoints keep their true x coordinate; clipping preserves the slope.
    var strokeX = function (i) { return PL + i * bw + bw / 2; };
    ctx.lineJoin = 'round'; ctx.lineWidth = 1.7; ctx.globalAlpha = .88;
    for (var i4 = 0; i4 < d.bi.length - 1; i4++) {
      var a2 = d.bi[i4], b2 = d.bi[i4 + 1];
      ctx.strokeStyle = (b2[2] > a2[2]) ? COL.up : COL.dn;
      var provisional = d.biLast == null ? i4 === d.bi.length - 2 : b2[0] === d.biLast;
      if (provisional) ctx.setLineDash([5, 3]); else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(strokeX(a2[0]), Y(a2[2])); ctx.lineTo(strokeX(b2[0]), Y(b2[2])); ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    ctx.restore();
  }

  // 分型：顶朝上三角、底朝下三角。**未成笔的分型也画** ——
  // 看得出哪些分型被采纳为笔端点、哪些被过滤掉了
  if (LAYERS.fx && d.fx && d.fx.length) {
    ctx.fillStyle = '#5b6470'; ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = 0.9;
    d.fx.forEach(function (f) {
      var x = X(f[0]), y = Y(f[2]), s = 3.8;
      ctx.beginPath();
      if (f[1] > 0) { ctx.moveTo(x - s, y - 3); ctx.lineTo(x + s, y - 3); ctx.lineTo(x, y - 3 - s * 1.7); }
      else { ctx.moveTo(x - s, y + 3); ctx.lineTo(x + s, y + 3); ctx.lineTo(x, y + 3 + s * 1.7); }
      ctx.closePath(); ctx.fill(); ctx.stroke();
    });
  }

  // 历史买点（复盘）：细竖线 + 顶部类型标（一/二/三）。
  // ★ 与下面的「买点」(opt.buyIdx) 是两回事：这里是**曾经成立过**的信号，
  //   多数早已失效（detect 只比最后两组下跌段，是个滑动窗口，信号会滑出去）。
  //   所以画细、画淡，**失效的用虚线**，避免与当前有效信号混淆。
  //   仍有效的（live=1）用实线 + 粗标签。
  // ★ 本项目分叉：买点用紫色、卖点用青色（原技能版只有买点）。编号 1/2/3 = 一/二/三类，
  //   与抽屉里「本票信号」的卡片一一对应 —— 图上看到 3，卡片里就找三类那张。
  if (LAYERS.histbuy && hist && hist.length) {
    ctx.textAlign = 'center';
    var lastLb = -99;                 // 上一个标签的 x，密集处跳过标签防叠字
    hist.forEach(function (hh) {
      var i = hh.i;
      if (i == null || i < 0 || i >= n || d.c[i] == null) return;
      var x = X(i), on = !!hh.live;
      var sc = hh.side === 's' ? [13, 148, 136] : [124, 58, 237];
      var rg = function (a) { return 'rgba(' + sc[0] + ',' + sc[1] + ',' + sc[2] + ',' + a + ')'; };
      ctx.strokeStyle = on ? rg(.62) : rg(.34);
      ctx.lineWidth = on ? 1.4 : 1;
      ctx.setLineDash(on ? [] : [3, 3]);
      ctx.beginPath(); ctx.moveTo(x, PT); ctx.lineTo(x, volY + volH); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = on ? rg(.9) : rg(.5);
      ctx.beginPath(); ctx.arc(x, Y(d.c[i]), 3, 0, 6.283); ctx.fill();
      // ★ 窄屏下信号更容易挤在一起：把标签间隔放宽，免得方块连成一条
      if (x - lastLb >= (nar ? 18 : 15)) {
        // 实心色底 + 白字。★ 必须用数字 1/2/3，不能用汉字「一/二/三」——
        //   汉字在小方块里就是几道横线，根本分不清是一类还是二类（实测）。
        var lb = hh.n || hh.t, f = (on ? 'bold ' : '') + (nar ? 10 : 11) + 'px system-ui,sans-serif';
        ctx.font = f;
        var tw = ctx.measureText(lb).width;
        ctx.fillStyle = on ? rg(.95) : rg(.62);
        ctx.fillRect(x - tw / 2 - 4.5, PT + 12, tw + 9, 14);
        ctx.fillStyle = '#fff';
        ctx.fillText(lb, x, PT + 23);        // 下一行，避开「◆买点」的标签
        lastLb = x;
      }
      // 存活期横条：首次报出 → 失效。★ 复盘最关键的一条视觉信息 ——
      //   横条越长说明这个信号越经得起后续走势检验，越短说明报出即被推翻。
      var x2 = X(Math.min(i + (hh.alive || 0), n - 1));
      if (x2 > x + 1) {
        ctx.fillStyle = on ? rg(.5) : rg(.26);
        ctx.fillRect(x, PT + 29, x2 - x, 2.5);
      }
    });
  }

  // 买点：竖线 + 当日收盘处圆点
  if (LAYERS.buy && opt.buyIdx != null && opt.buyIdx >= 0) {
    var x5 = X(opt.buyIdx);
    ctx.setLineDash([4, 3]); ctx.strokeStyle = COL.buy; ctx.lineWidth = 1.3;
    ctx.beginPath(); ctx.moveTo(x5, PT); ctx.lineTo(x5, volY + volH); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = COL.buy;
    ctx.beginPath(); ctx.arc(x5, Y(d.c[opt.buyIdx] || d.c[n - 1]), 4, 0, 6.283); ctx.fill();
    ctx.font = 'bold ' + (nar ? 10 : 10.5) + 'px system-ui,sans-serif'; ctx.textAlign = 'center';
    // ★ 标签比竖线宽，信号落在窗口边缘时会被画布裁掉半截 —— 夹进安全区
    ctx.fillText(LAB.buyTag, Math.max(PL + 28, Math.min(x5, W - PR - 28)), PT + 9);
  }

  // 卖点：买点的镜像图层（项目内副本新增）。竖线 + 收盘处圆点 + 顶部标签。
  //   ★ 与「历史买点」的区别同上：这里画的是**当前仍成立**的最新卖点信号。
  if (LAYERS.sell && opt.sellIdx != null && opt.sellIdx >= 0) {
    var x6 = X(opt.sellIdx);
    ctx.setLineDash([4, 3]); ctx.strokeStyle = COL.sell; ctx.lineWidth = 1.3;
    ctx.beginPath(); ctx.moveTo(x6, PT); ctx.lineTo(x6, volY + volH); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = COL.sell;
    ctx.beginPath(); ctx.arc(x6, Y(d.c[opt.sellIdx] || d.c[n - 1]), 4, 0, 6.283); ctx.fill();
    ctx.font = 'bold ' + (nar ? 10 : 10.5) + 'px system-ui,sans-serif'; ctx.textAlign = 'center';
    // ★ 同一段震荡里买卖点常常挨得很近，两个标签会叠成一团 —— 错开一行
    var sty = PT + 9;
    if (opt.buyIdx != null && opt.buyIdx >= 0 && Math.abs(x6 - X(opt.buyIdx)) < 62) sty = PT + 22;
    ctx.fillText(LAB.sellTag, Math.max(PL + 28, Math.min(x6, W - PR - 28)), sty);
  }

  // 成交量
  var vmax = 0;
  for (var i6 = 0; i6 < n; i6++) if (d.v[i6] > vmax) vmax = d.v[i6];
  ctx.textAlign = 'left'; ctx.fillStyle = COL.tx; ctx.font = (nar ? 9 : 10) + 'px system-ui,sans-serif';
  ctx.fillText('成交量', PL + 2, volY - 5);
  for (var i7 = 0; i7 < n; i7++) {
    ctx.fillStyle = d.c[i7] >= d.o[i7] ? COL.up : COL.dn;
    var vh = Math.max(1, d.v[i7] / vmax * volH);
    ctx.fillRect(X(i7) - bwd / 2, volY + volH - vh, bwd, vh);
  }
  ctx.strokeStyle = COL.grid;
  ctx.beginPath(); ctx.moveTo(PL, volY + volH); ctx.lineTo(W - PR, volY + volH); ctx.stroke();

  // MACD 副图 + A2/A1 背驰对比框
  if (macdH) {
    var md = d.md, mmax = 1;
    for (var i8 = 0; i8 < md.length; i8++) { var av = Math.abs(md[i8]); if (av > mmax) mmax = av; }
    var mid = macdY + macdH / 2, mw = Math.max(1, Math.min(bw * 0.6, 9));
    ctx.textAlign = 'left'; ctx.fillStyle = COL.tx; ctx.font = (nar ? 9 : 10) + 'px system-ui,sans-serif';
    ctx.fillText('MACD 柱', PL + 2, macdY - 4);
    // ★ 说明放左侧标题行，放右侧会压住 A1 框；窄屏放不下整句，只留 A2/A1 是什么
    ctx.fillStyle = '#185FA5';
    ctx.fillText(nar ? 'A2 前段 · A1 后段'
                     : 'A2 = 前一段下跌 · A1 = 最近一段下跌　　绿柱面积收缩即底背驰',
                 PL + (nar ? 48 : 60), macdY - 4);
    ctx.strokeStyle = COL.grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PL, mid); ctx.lineTo(W - PR, mid); ctx.stroke();
    for (var i9 = 0; i9 < md.length; i9++) {
      var v2 = md[i9], mhh = Math.abs(v2) / mmax * (macdH / 2 - 3);
      ctx.fillStyle = (v2 >= 0) ? COL.up : COL.dn;
      ctx.fillRect(X(i9) - mw / 2, (v2 >= 0) ? mid - mhh : mid, mw, Math.max(0.7, mhh));
    }
    if (d.ar) {
      // ★ 标签必须带「前段/后段」。裸的 A2/A1 在图上是从左到右排的，
      //   很容易被读成时间顺序，其实 A2 在前 —— 写清楚省掉反复解释
      var lb2 = ['A2 前段', 'A1 后段'], cl = ['rgba(37,99,235,.85)', 'rgba(8,145,178,.9)'];
      for (var k = 0; k < 2; k++) {
        var s0 = d.ar[k][0], s1 = d.ar[k][1]; if (s1 <= s0) continue;
        ctx.strokeStyle = cl[k]; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
        ctx.strokeRect(X(s0), macdY + 1, X(s1) - X(s0), macdH - 2); ctx.setLineDash([]);
        ctx.fillStyle = cl[k]; ctx.font = (nar ? 8.5 : 9.5) + 'px system-ui,sans-serif'; ctx.textAlign = 'center';
        ctx.fillText(lb2[k], (X(s0) + X(s1)) / 2, macdY + 11);
      }
    }
    ctx.strokeStyle = COL.grid;
    ctx.beginPath(); ctx.moveTo(PL, macdY + macdH); ctx.lineTo(W - PR, macdY + macdH); ctx.stroke();
  }
}

// 悬停 / 触摸 十字光标 tooltip
// ★ 手机上没有 hover：原来只绑 onmousemove，移动端整个交互是死的（点图没反应）。
//   这里改用 Pointer Events —— 一套代码同时吃鼠标、手指、触控笔；不支持的旧设备
//   回退到 mouse + touch。
//  hist 可选：命中历史买点报出点时，把复盘数据一并显示（比在图上找标记快得多）。
//  tipEl 可选：tooltip 容器。默认 #tip；放大视图有自己的容器，不能和抽屉抢同一个。
//
//  ★ 返回 { update(d, dates, hist) }：放大 / 缩小 / 平移后数据换了，但监听器不能重挂
//    （重挂会累积，切几次后一次移动触发 N 次）。所以把 d/dates/hist 收进一个可变引用，
//    由调用方 update 覆盖即可。抽屉侧忽略返回值不影响原行为。
function bindTip(cv, d, dates, hist, tipEl) {
  var tip = tipEl || document.getElementById('tip');
  if (!tip) return null;            // 模板未提供 tooltip 容器时静默跳过，不要抛异常

  var hideTimer = null;

  function place(e) {
    var r = cv.getBoundingClientRect(), W = r.width, n = d.n;
    // ★ 边距取 draw() 的同一套（layout），否则窄屏上命中列会整体偏移
    var L0 = layout(W, n), PL = L0.PL, bw = L0.bw;
    // 触摸事件把坐标放在 e.touches[0]，指针/鼠标事件就在 e 本身
    var pt = (e.touches && e.touches.length) ? e.touches[0] : e;
    if (!pt || pt.clientX == null) return;
    var lx = pt.clientX - r.left, ly = pt.clientY - r.top;
    var i = Math.floor((lx - PL) / bw);
    if (i < 0 || i >= n) { tip.style.display = 'none'; return; }
    var start = dateStart(dates, d, n);
    var s = String(dates[start + i] || '');
    var dt = s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8);
    if (s.length >= 12) dt += ' ' + s.slice(8, 10) + ':' + s.slice(10, 12);   // 分钟线补时分
    var pc = (d.c[i] / 100).toFixed(2), po = (d.o[i] / 100).toFixed(2),
        ph = (d.h[i] / 100).toFixed(2), pl = (d.l[i] / 100).toFixed(2);
    var chg = ((d.c[i] - d.c[i - 1]) / d.c[i - 1] * 100).toFixed(2);
    var txt = dt + '\n开 ' + po + '  高 ' + ph + '\n低 ' + pl + '  收 ' + pc
            + '\n涨跌 ' + (i > 0 ? chg : '—') + '%';
    if (hist) {
      // ★ 不 break：同一根 K 线上可能同时压着买点和卖点（或一类 + 三类），
      //   只显示第一个会让人以为「这根只有一个信号」。逐个列出。
      for (var k = 0; k < hist.length; k++) {
        if (hist[k].i === i) {
          var hh = hist[k];
          txt += '\n————\n' + LAB.hist
               + (LAB.anonType ? ((hh.rn || hh.n) + '型结构') : (hh.ty || hh.t))
               + (hh.live ? LAB.liveOn : '')
               + '\n' + LAB.entry + ' ' + hh.entry + '　' + LAB.stop + ' ' + (hh.stop == null ? '—' : hh.stop)
               + '\n' + LAB.alive + ' ' + hh.alive + ' 根';
          if (!LAB.hideNow) txt += '　' + LAB.now + ' ' + (hh.now > 0 ? '+' : '') + hh.now + '%';
        }
      }
    }
    tip.textContent = txt;
    tip.style.display = 'block';
    // ★ 右/下边界用 tip 实测尺寸算。原来写死的 168px 是按桌面估的，
    //   手机上 tip 几乎占满屏宽，写死值会把它顶出屏幕右侧。
    var tw = tip.offsetWidth || 168, th = tip.offsetHeight || 0;
    tip.style.left = Math.max(4, Math.min(lx + 14, W - tw - 4)) + 'px';
    // 手指点在下方时把 tip 翻到上方，避免被手指盖住
    var top = ly - th - 14;
    if (top < 4) top = ly + 18;
    tip.style.top = Math.max(4, Math.min(top, cv.clientHeight - th - 4)) + 'px';
  }

  function hide() { tip.style.display = 'none'; }

  if (window.PointerEvent) {
    // pan-y：手指竖滑仍可翻页，横划 / 按住则看图。设 none 会把页面滚动也吃掉
    cv.style.touchAction = 'pan-y';
    cv.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'mouse' || e.buttons) place(e);
    });
    cv.addEventListener('pointerdown', function (e) {
      if (hideTimer) clearTimeout(hideTimer);
      place(e);
    });
    cv.addEventListener('pointerup', function () {
      // ★ 松手后保留一会儿：手机上一抬手就消失的话根本来不及看
      hideTimer = setTimeout(hide, 2600);
    });
    cv.addEventListener('pointercancel', hide);
    cv.addEventListener('pointerleave', function (e) {
      if (e.pointerType === 'mouse') hide();
    });
  } else {
    cv.addEventListener('mousemove', place);
    cv.addEventListener('mouseleave', hide);
    cv.addEventListener('touchstart', function (e) {
      if (hideTimer) clearTimeout(hideTimer); place(e);
    }, { passive: true });
    cv.addEventListener('touchmove', place, { passive: true });
    cv.addEventListener('touchend', function () { hideTimer = setTimeout(hide, 2600); });
  }

  return {
    // 换数据（放大视图缩放 / 平移后调这个），监听器不动
    update: function (nd, ndates, nhist) { d = nd; dates = ndates; hist = nhist; },
    hide: hide
  };
}

// 尺寸变化重绘。
// ★ 原来没有：手机横竖屏切换、或从预览器小窗放大后，canvas 仍按旧宽度绘制 ——
//   图被拉伸糊掉，命中列也跟着错位（X() 用的是旧 bw）。
//   只绑一次：这份 JS 可能被同一页面多个图表实例复用。
if (typeof window !== 'undefined' && !window.__clResizeBound) {
  window.__clResizeBound = 1;
  var _clRzT = null;
  var _clRz = function () {
    if (_clRzT) clearTimeout(_clRzT);
    _clRzT = setTimeout(function () {
      if (typeof redraw === 'function' && typeof LAST !== 'undefined' && LAST && LAST.d) redraw();
    }, 160);
  };
  window.addEventListener('resize', _clRz);
  window.addEventListener('orientationchange', _clRz);
}
