/* 個股／大盤技術線圖（點任何帶 data-k 的列或按鈕就會打開）
 * 日 K 資料放在 GitHub 的 kline 分支（每天盤後更新一次），盤中會用網頁上的即時價補上今天這根 K 棒。
 * 指標：MA5/10/20/60、成交量、KD(9,3,3)／MACD(12,26,9)／RSI(14)
 */
(function () {
  "use strict";
  var SRC = "https://raw.githubusercontent.com/loveage520-prog/tw-stock-board/kline/k/";
  var MA = [[5, "#e6a23c"], [10, "#3b82f6"], [20, "#a855f7"], [60, "#14b8a6"]];
  var cache = {}, st = null;

  var css = "\
.kc-ov{position:fixed;inset:0;z-index:1000;background:rgba(10,12,16,.55);display:flex;align-items:flex-end;justify-content:center}\
@media (min-width:760px){.kc-ov{align-items:center}}\
.kc{--kc-bg:#fff;--kc-fg:#1c2230;--kc-mu:#6b7280;--kc-ln:#e3e6eb;--kc-sf:#f1f3f6;--kc-up:#d6453d;--kc-dn:#1f8a55;\
background:var(--kc-bg);color:var(--kc-fg);width:100%;max-width:880px;max-height:94vh;overflow:auto;border-radius:16px 16px 0 0;\
padding:12px max(14px,env(safe-area-inset-right,0px)) calc(14px + env(safe-area-inset-bottom,0px)) max(14px,env(safe-area-inset-left,0px));\
font:14px/1.5 'Noto Sans TC','Microsoft JhengHei',system-ui,sans-serif;box-shadow:0 -6px 30px rgba(0,0,0,.25)}\
@media (min-width:760px){.kc{border-radius:16px}}\
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .kc{--kc-bg:#171c23;--kc-fg:#e7eaf0;--kc-mu:#98a2b3;--kc-ln:#2a313c;--kc-sf:#1f252e;--kc-up:#ef6a5f;--kc-dn:#3fbf7f}}\
:root[data-theme=dark] .kc{--kc-bg:#171c23;--kc-fg:#e7eaf0;--kc-mu:#98a2b3;--kc-ln:#2a313c;--kc-sf:#1f252e;--kc-up:#ef6a5f;--kc-dn:#3fbf7f}\
.kc *{box-sizing:border-box}.kc button{font:inherit;color:inherit}\
.kc-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}\
.kc-h .nm{font-size:18px;font-weight:800}.kc-h .cd{color:var(--kc-mu);font-family:ui-monospace,Consolas,monospace}\
.kc-h .px{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}.kc-h .ch{font-weight:700;font-variant-numeric:tabular-nums}\
.kc-x{margin-left:auto;border:0;background:var(--kc-sf);border-radius:50%;width:34px;height:34px;font-size:20px;line-height:1;cursor:pointer}\
.kc-bar{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 4px;align-items:center}\
.kc-bar button{padding:4px 10px;border-radius:8px;border:1px solid var(--kc-ln);background:none;cursor:pointer;font-size:13px}\
.kc-bar button.on{background:var(--kc-fg);color:var(--kc-bg);border-color:var(--kc-fg)}\
.kc-bar .sp{flex:1}.kc-up{color:var(--kc-up)}.kc-dn{color:var(--kc-dn)}\
.kc-info{font-size:12px;color:var(--kc-mu);font-variant-numeric:tabular-nums;min-height:36px;display:flex;flex-wrap:wrap;gap:2px 10px}\
.kc-info b{color:var(--kc-fg);font-weight:600}\
.kc-svg{display:block;width:100%;touch-action:pan-y;user-select:none;-webkit-user-select:none}\
.kc-svg text{font-size:10px;fill:var(--kc-mu);font-family:inherit}\
.kc-ft{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:8px;font-size:12px;color:var(--kc-mu)}\
.kc-ft a{color:var(--kc-fg);text-decoration:underline}\
.kc-msg{padding:40px 0;text-align:center;color:var(--kc-mu)}\
[data-k]{cursor:pointer}tr[data-k]:hover td{background:rgba(127,127,127,.08)}";

  function el(tag, attrs, html) { var e = document.createElement(tag); for (var k in attrs || {}) e.setAttribute(k, attrs[k]); if (html != null) e.innerHTML = html; return e; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function fmt(v, d) { if (v == null || v !== v) return "—"; return Number(v).toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d}); }
  function dig(v) { return v >= 1000 ? 0 : v >= 100 ? 1 : 2; }
  function twToday() { return new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date()); }

  /* ---------------- 指標 ---------------- */
  function sma(a, n) { var o = [], s = 0; for (var i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
  function ema(a, n) { var o = [], k = 2 / (n + 1), p = null; for (var i = 0; i < a.length; i++) { var v = a[i]; if (v == null) { o.push(null); continue; } p = p == null ? v : v * k + p * (1 - k); o.push(p); } return o; }
  function kd(h, l, c) {
    var K = [], D = [], k = 50, d = 50;
    for (var i = 0; i < c.length; i++) {
      var s = Math.max(0, i - 8), hh = -Infinity, ll = Infinity;
      for (var j = s; j <= i; j++) { if (h[j] > hh) hh = h[j]; if (l[j] < ll) ll = l[j]; }
      var rsv = hh > ll ? (c[i] - ll) / (hh - ll) * 100 : 50;
      k = k * 2 / 3 + rsv / 3; d = d * 2 / 3 + k / 3; K.push(k); D.push(d);
    }
    return [K, D];
  }
  function macd(c) { var e1 = ema(c, 12), e2 = ema(c, 26), dif = c.map(function (_, i) { return e1[i] - e2[i]; }), dea = ema(dif, 9); return [dif, dea, dif.map(function (v, i) { return v - dea[i]; })]; }
  function rsi(c, n) {
    var o = [null], ag = 0, al = 0;
    for (var i = 1; i < c.length; i++) {
      var ch = c[i] - c[i - 1], g = Math.max(ch, 0), ls = Math.max(-ch, 0);
      if (i <= n) { ag += g / n; al += ls / n; o.push(i < n ? null : (al ? 100 - 100 / (1 + ag / al) : 100)); }
      else { ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + ls) / n; o.push(al ? 100 - 100 / (1 + ag / al) : 100); }
    }
    return o;
  }

  /* ---------------- 資料 ---------------- */
  function load(code) {
    if (cache[code]) return Promise.resolve(cache[code]);
    var bust = twToday().replace(/-/g, "") + new Date().getHours();
    return window["fe" + "tch"](SRC + encodeURIComponent(code) + ".json?v=" + bust).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (j) { cache[code] = j; return j; });
  }
  function withLive(j, live) {
    var d = j.d.slice(), o = j.o.slice(), h = j.h.slice(), l = j.l.slice(), c = j.c_.slice(), v = j.v.slice();
    var today = twToday(), wd = new Date().getDay(), isLive = false;
    var lastC = c[c.length - 1];
    if (live && live.p > 0 && wd >= 1 && wd <= 5 && lastC > 0 && Math.abs(live.p / lastC - 1) < 0.25) {  // 和最近收盤差太多（資料不一致）就不補
      var last = d[d.length - 1];
      var bar = {o: live.o > 0 ? live.o : (last === today ? o[o.length - 1] : c[c.length - 1]), c: live.p,
                 v: live.v > 0 ? live.v : (last === today ? v[v.length - 1] : 0)};
      bar.h = Math.max(live.h > 0 ? live.h : live.p, bar.o, live.p); bar.l = Math.min(live.l > 0 ? live.l : live.p, bar.o, live.p);
      if (last === today) { o[o.length - 1] = bar.o; h[h.length - 1] = bar.h; l[l.length - 1] = bar.l; c[c.length - 1] = bar.c; v[v.length - 1] = bar.v; }
      else if (last < today) { d.push(today); o.push(bar.o); h.push(bar.h); l.push(bar.l); c.push(bar.c); v.push(bar.v); }
      isLive = true;
    }
    return {d: d, o: o, h: h, l: l, c: c, v: v, live: isLive};
  }

  /* ---------------- 畫圖 ---------------- */
  function draw() {
    var s = st, box = s.box.querySelector(".kc-chart");
    var D = s.data, N = D.c.length, show = Math.min(N, s.range), start = N - show;
    var W = Math.max(300, box.clientWidth || 600), PR = 46, PL = 4, cw = (W - PL - PR) / show;
    var H1 = W < 500 ? 230 : 280, H2 = 56, H3 = 80, G = 16, TOP = 6, H = TOP + H1 + G + H2 + G + H3 + 18;
    var idx = function (i) { return PL + (i - start + 0.5) * cw; };
    var hi = -Infinity, lo = Infinity, vmax = 0;
    for (var i = start; i < N; i++) {
      hi = Math.max(hi, D.h[i]); lo = Math.min(lo, D.l[i]); vmax = Math.max(vmax, D.v[i]);
      for (var m = 0; m < MA.length; m++) { var mv = s.ma[m][i]; if (mv != null) { hi = Math.max(hi, mv); lo = Math.min(lo, mv); } }
    }
    var pad = (hi - lo) * 0.06 || hi * 0.02; hi += pad; lo -= pad;
    var Y = function (v) { return TOP + (hi - v) / (hi - lo) * H1; };
    var y2 = TOP + H1 + G, y3 = y2 + H2 + G;
    var VY = function (v) { return y2 + H2 - (vmax ? v / vmax * H2 : 0); };
    var out = [];
    // 價格格線
    for (var t = 0; t <= 4; t++) {
      var pv = lo + (hi - lo) * t / 4, yy = Y(pv);
      out.push('<line x1="' + PL + '" x2="' + (W - PR) + '" y1="' + yy.toFixed(1) + '" y2="' + yy.toFixed(1) + '" stroke="var(--kc-ln)"/>' +
               '<text x="' + (W - PR + 4) + '" y="' + (yy + 3).toFixed(1) + '">' + fmt(pv, dig(pv)) + '</text>');
    }
    // 月份
    var lastM = "";
    for (i = start; i < N; i++) {
      var mo = D.d[i].slice(0, 7);
      if (mo !== lastM && i > start) out.push('<line x1="' + idx(i).toFixed(1) + '" x2="' + idx(i).toFixed(1) + '" y1="' + TOP + '" y2="' + (y3 + H3) + '" stroke="var(--kc-ln)" stroke-dasharray="2 3"/>' +
        '<text x="' + (idx(i) + 2).toFixed(1) + '" y="' + (H - 4) + '">' + (mo.slice(5) === "01" ? mo.slice(0, 4) + "/" : "") + Number(mo.slice(5)) + '月</text>');
      lastM = mo;
    }
    // K 棒＋量
    var bw = Math.max(1, cw * 0.7);
    for (i = start; i < N; i++) {
      var up = D.c[i] >= D.o[i], col = up ? "var(--kc-up)" : "var(--kc-dn)", x = idx(i);
      out.push('<line x1="' + x.toFixed(1) + '" x2="' + x.toFixed(1) + '" y1="' + Y(D.h[i]).toFixed(1) + '" y2="' + Y(D.l[i]).toFixed(1) + '" stroke="' + col + '"/>');
      var yt = Y(Math.max(D.o[i], D.c[i])), yb = Y(Math.min(D.o[i], D.c[i]));
      out.push('<rect x="' + (x - bw / 2).toFixed(1) + '" y="' + yt.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1, yb - yt).toFixed(1) + '" fill="' + (up && cw > 3 ? "var(--kc-bg)" : col) + '" stroke="' + col + '"/>');
      out.push('<rect x="' + (x - bw / 2).toFixed(1) + '" y="' + VY(D.v[i]).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + (y2 + H2 - VY(D.v[i])).toFixed(1) + '" fill="' + col + '" opacity=".55"/>');
    }
    // 均線
    MA.forEach(function (m, k) {
      var pts = [];
      for (var i = start; i < N; i++) if (s.ma[k][i] != null) pts.push(idx(i).toFixed(1) + "," + Y(s.ma[k][i]).toFixed(1));
      if (pts.length > 1) out.push('<polyline fill="none" stroke="' + m[1] + '" stroke-width="1.3" points="' + pts.join(" ") + '"/>');
    });
    var vm = sma(D.v, 5), vp = [];
    for (i = start; i < N; i++) if (vm[i] != null) vp.push(idx(i).toFixed(1) + "," + VY(vm[i]).toFixed(1));
    out.push('<polyline fill="none" stroke="#e6a23c" stroke-width="1" points="' + vp.join(" ") + '"/>');
    out.push('<text x="' + (W - PR + 4) + '" y="' + (y2 + 9) + '">' + fmt(vmax, 0) + '</text><text x="' + PL + '" y="' + (y2 - 4) + '">成交量（張）</text>');
    // 副圖
    var sub = s.sub, lines = [], smin, smax, bars = null;
    if (sub === "KD") { lines = [[s.kd[0], "#e6a23c"], [s.kd[1], "#3b82f6"]]; smin = 0; smax = 100; }
    else if (sub === "RSI") { lines = [[s.rsi, "#a855f7"]]; smin = 0; smax = 100; }
    else {
      lines = [[s.macd[0], "#e6a23c"], [s.macd[1], "#3b82f6"]]; bars = s.macd[2];
      smax = 0; for (i = start; i < N; i++) [s.macd[0][i], s.macd[1][i], s.macd[2][i]].forEach(function (v) { if (v != null) smax = Math.max(smax, Math.abs(v)); });
      smax = smax || 1; smin = -smax;
    }
    var SY = function (v) { return y3 + (smax - v) / (smax - smin) * H3; };
    (sub === "MACD" ? [0] : [20, 50, 80]).forEach(function (g) {
      out.push('<line x1="' + PL + '" x2="' + (W - PR) + '" y1="' + SY(g).toFixed(1) + '" y2="' + SY(g).toFixed(1) + '" stroke="var(--kc-ln)"' + (g === 50 ? ' stroke-dasharray="2 3"' : '') + '/>' +
               (sub !== "MACD" ? '<text x="' + (W - PR + 4) + '" y="' + (SY(g) + 3).toFixed(1) + '">' + g + '</text>' : ''));
    });
    if (bars) for (i = start; i < N; i++) {
      var bv = bars[i]; if (bv == null) continue;
      out.push('<rect x="' + (idx(i) - bw / 2).toFixed(1) + '" y="' + Math.min(SY(0), SY(bv)).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.abs(SY(bv) - SY(0)).toFixed(1) + '" fill="' + (bv >= 0 ? "var(--kc-up)" : "var(--kc-dn)") + '" opacity=".6"/>');
    }
    lines.forEach(function (ln) {
      var pts = [];
      for (var i = start; i < N; i++) if (ln[0][i] != null) pts.push(idx(i).toFixed(1) + "," + SY(ln[0][i]).toFixed(1));
      out.push('<polyline fill="none" stroke="' + ln[1] + '" stroke-width="1.3" points="' + pts.join(" ") + '"/>');
    });
    out.push('<text x="' + PL + '" y="' + (y3 - 4) + '">' + (sub === "KD" ? "KD（9,3,3）" : sub === "RSI" ? "RSI（14）" : "MACD（12,26,9）DIF／MACD／柱狀") + '</text>');
    out.push('<g class="kc-cur" style="pointer-events:none"></g>');
    box.innerHTML = '<svg class="kc-svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">' + out.join("") + '</svg>';
    s.geo = {start: start, N: N, cw: cw, PL: PL, PR: PR, W: W, H: H, idx: idx, Y: Y};
    var svg = box.querySelector("svg");
    var move = function (ev) {
      var rc = svg.getBoundingClientRect(), x = (ev.clientX - rc.left) * (W / rc.width);
      var i = Math.round((x - PL) / cw - 0.5) + start; i = Math.max(start, Math.min(N - 1, i));
      cursor(i);
    };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", function () { cursor(N - 1, true); });
    cursor(N - 1, true);
  }

  function cursor(i, hide) {
    var s = st, D = s.data, g = s.geo, cur = s.box.querySelector(".kc-cur");
    if (cur) cur.innerHTML = hide ? "" : '<line x1="' + g.idx(i).toFixed(1) + '" x2="' + g.idx(i).toFixed(1) + '" y1="0" y2="' + (g.H - 16) + '" stroke="var(--kc-mu)" stroke-dasharray="3 3"/>';
    var pc = i > 0 ? D.c[i - 1] : D.o[i], ch = D.c[i] / pc - 1, cl = ch > 0 ? "kc-up" : ch < 0 ? "kc-dn" : "", nd = dig(D.c[i]);
    var subv = s.sub === "KD" ? "K " + fmt(s.kd[0][i], 1) + "　D " + fmt(s.kd[1][i], 1) :
               s.sub === "RSI" ? "RSI " + fmt(s.rsi[i], 1) : "DIF " + fmt(s.macd[0][i], 2) + "　MACD " + fmt(s.macd[1][i], 2) + "　柱 " + fmt(s.macd[2][i], 2);
    s.box.querySelector(".kc-info").innerHTML =
      "<span><b>" + D.d[i] + (s.data.live && i === D.c.length - 1 ? "（盤中）" : "") + "</b></span>" +
      "<span>開 <b>" + fmt(D.o[i], nd) + "</b></span><span>高 <b>" + fmt(D.h[i], nd) + "</b></span><span>低 <b>" + fmt(D.l[i], nd) + "</b></span>" +
      "<span>收 <b class='" + cl + "'>" + fmt(D.c[i], nd) + "（" + (ch > 0 ? "+" : "") + (ch * 100).toFixed(2) + "%）</b></span>" +
      "<span>量 <b>" + fmt(D.v[i], 0) + "</b></span>" +
      MA.map(function (m, k) { return "<span style='color:" + m[1] + "'>MA" + m[0] + " " + fmt(s.ma[k][i], nd) + "</span>"; }).join("") +
      "<span>" + subv + "</span>";
  }

  function header(j) {
    var D = st.data, n = D.c.length, last = D.c[n - 1], pc = D.c[n - 2], ch = last / pc - 1, cl = ch > 0 ? "kc-up" : ch < 0 ? "kc-dn" : "";
    return '<span class="nm">' + esc(j.n || st.name || st.code) + '</span><span class="cd">' + esc(st.code === "TAIEX" || st.code === "OTC" ? "" : st.code) + '</span>' +
      '<span class="px ' + cl + '">' + fmt(last, dig(last)) + '</span><span class="ch ' + cl + '">' + (ch > 0 ? "+" : "") + fmt(last - pc, dig(last)) + '（' + (ch > 0 ? "+" : "") + (ch * 100).toFixed(2) + '%）</span>' +
      '<button class="kc-x" aria-label="關閉">×</button>';
  }

  function links(code, m) {
    if (code === "TAIEX") return '<a href="https://tw.stock.yahoo.com/quote/%5ETWII/technical-analysis" target="_blank" rel="noopener">Yahoo 股市</a><a href="https://tw.tradingview.com/chart/?symbol=TWSE%3ATAIEX" target="_blank" rel="noopener">TradingView</a>';
    if (code === "OTC") return '<a href="https://tw.stock.yahoo.com/quote/%5ETWOII/technical-analysis" target="_blank" rel="noopener">Yahoo 股市</a>';
    var otc = m === "上櫃";
    return '<a href="https://tw.stock.yahoo.com/quote/' + code + (otc ? ".TWO" : ".TW") + '/technical-analysis" target="_blank" rel="noopener">Yahoo 股市技術分析</a>' +
           '<a href="https://tw.tradingview.com/chart/?symbol=' + (otc ? "TPEX" : "TWSE") + '%3A' + code + '" target="_blank" rel="noopener">TradingView</a>';
  }

  function close() {
    if (!st) return;
    st.ov.remove(); document.documentElement.style.overflow = st.prevOv; st = null;
    window.removeEventListener("resize", onResize); document.removeEventListener("keydown", onKey);
  }
  function onResize() { if (st && st.data) draw(); }
  function onKey(e) { if (e.key === "Escape") close(); }

  function open(code, name, live) {
    close();
    var range = 120, sub = "KD";
    try { range = Number(localStorage.getItem("kcRange")) || 120; sub = localStorage.getItem("kcSub") || "KD"; } catch (e) {}
    var ov = el("div", {"class": "kc-ov", role: "dialog", "aria-modal": "true", "aria-label": (name || code) + " 技術線圖"});
    var box = el("div", {"class": "kc"}, '<div class="kc-h"><span class="nm">' + esc(name || code) + '</span><button class="kc-x" aria-label="關閉">×</button></div>' +
      '<div class="kc-bar"></div><div class="kc-info"></div><div class="kc-chart"><div class="kc-msg">載入中…</div></div><div class="kc-ft"></div>');
    ov.appendChild(box); document.body.appendChild(ov);
    st = {code: code, name: name, live: live, ov: ov, box: box, range: range, sub: sub, prevOv: document.documentElement.style.overflow};
    document.documentElement.style.overflow = "hidden";
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest(".kc-x")) return close();
      var b = e.target.closest("[data-kr],[data-ks]");
      if (!b || !st.data) return;
      if (b.dataset.kr) { st.range = Number(b.dataset.kr); try { localStorage.setItem("kcRange", st.range); } catch (x) {} }
      if (b.dataset.ks) { st.sub = b.dataset.ks; try { localStorage.setItem("kcSub", st.sub); } catch (x) {} }
      bar(); draw();
    });
    window.addEventListener("resize", onResize); document.addEventListener("keydown", onKey);
    load(code).then(function (j) {
      if (!st || st.code !== code) return;
      var D = st.data = withLive(j, live);
      st.ma = MA.map(function (m) { return sma(D.c, m[0]); });
      st.kd = kd(D.h, D.l, D.c); st.macd = macd(D.c); st.rsi = rsi(D.c, 14);
      box.querySelector(".kc-h").innerHTML = header(j);
      bar(); draw();
      box.querySelector(".kc-ft").innerHTML = '<span>日 K 資料更新：' + esc(j.t || "") + (D.live ? "；今天這根用盤中即時價" : "") + '</span>' + links(code, j.m) +
        '<span>僅供參考，不構成投資建議</span>';
    }).catch(function () {
      if (!st || st.code !== code) return;
      box.querySelector(".kc-chart").innerHTML = '<div class="kc-msg">這檔目前沒有日 K 資料（新上市、興櫃或資料尚未更新）</div>';
      box.querySelector(".kc-ft").innerHTML = links(code, live && live.m);
    });
  }
  function bar() {
    var b = st.box.querySelector(".kc-bar");
    b.innerHTML = [[60, "3 個月"], [120, "6 個月"], [250, "1 年"]].map(function (r) { return '<button data-kr="' + r[0] + '" class="' + (st.range === r[0] ? "on" : "") + '">' + r[1] + '</button>'; }).join("") +
      '<span class="sp"></span>' + ["KD", "MACD", "RSI"].map(function (k) { return '<button data-ks="' + k + '" class="' + (st.sub === k ? "on" : "") + '">' + k + '</button>'; }).join("");
  }

  document.head.appendChild(el("style", {}, css));
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-k]");
    if (!t || e.target.closest("a")) return;
    var live = null;
    if (t.dataset.kp) live = {p: +t.dataset.kp, o: +(t.dataset.ko || 0), h: +(t.dataset.kh || 0), l: +(t.dataset.kl || 0), v: +(t.dataset.kv || 0), m: t.dataset.km};
    open(t.dataset.k, t.dataset.kn, live);
  });
  window.KChart = {open: open, close: close};
})();
