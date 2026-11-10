/* ガンビットの部品（カウンター）をオーバーレイに重ねる（GAMBIT_DESIGN.md §6.4）
 *
 * soundfx.js と同じ形の独立モジュール。全面に重ねた層の上に、比率（0〜1）で
 * 置いた部品を描く。配置と現在値は style 一式の widgets で、値の変化は
 * widget イベントで届く。
 *
 *   WIDGETS.setConfig(list)        style / init が届いたとき（現在値も含む）
 *   WIDGETS.update(id, value, delta)  widget イベント
 *   WIDGETS.mount(el)              設定画面のプレビュー枠の中に描く
 *
 * 接続が切れても描いたものは消さない（最後の値を出し続ける）。
 */
(function () {
  const W = (window.WIDGETS = {});
  let list = [], host = null, layer = null;
  const els = new Map();   // id → { root, value }

  // 増えたときの動き。ワード演出の動きの名前を受けて、近いものを出す
  const ANIM = { pop: "pop", bounce: "bounce", shake: "shake", heartbeat: "beat",
                 glowpulse: "beat", spin: "spin", flash: "flash", glitch: "shake",
                 wave: "bounce", float: "bounce", rainbow: "pop", shine: "pop",
                 neon: "pop", none: "" };

  (function css() {
    const st = document.createElement("style");
    st.textContent = `
      .wg { position:absolute; transform:translate(-50%,-50%); white-space:nowrap;
            line-height:1.1; display:flex; align-items:baseline;
            gap:.35em; will-change:transform; }
      .wg .lb, .wg .v { paint-order:stroke fill; }
      .wg .v { display:inline-block; font-variant-numeric:tabular-nums; }
      .wg-pop .v    { animation: wg-pop .45s ease-out; }
      .wg-bounce .v { animation: wg-bounce .6s ease-out; }
      .wg-shake .v  { animation: wg-shake .45s linear; }
      .wg-beat .v   { animation: wg-beat .6s ease-out; }
      .wg-spin .v   { animation: wg-spin .6s ease-out; }
      .wg-flash .v  { animation: wg-flash .5s linear; }
      @keyframes wg-pop    { 0%{transform:scale(1)} 35%{transform:scale(1.45)} 100%{transform:scale(1)} }
      @keyframes wg-bounce { 0%,100%{transform:translateY(0)} 30%{transform:translateY(-.45em)} 60%{transform:translateY(.08em)} }
      @keyframes wg-shake  { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-.12em)} 40%{transform:translateX(.12em)} 60%{transform:translateX(-.08em)} 80%{transform:translateX(.08em)} }
      @keyframes wg-beat   { 0%,100%{transform:scale(1)} 25%{transform:scale(1.3)} 50%{transform:scale(1)} 75%{transform:scale(1.18)} }
      @keyframes wg-spin   { 0%{transform:rotate(0)} 100%{transform:rotate(360deg)} }
      @keyframes wg-flash  { 0%,100%{opacity:1} 25%,75%{opacity:.2} 50%{opacity:1} }
    `;
    document.head.appendChild(st);
  })();

  function getLayer() {
    if (!layer) {
      layer = document.createElement("div");
      layer.style.cssText = host
        ? "position:absolute;inset:0;pointer-events:none;overflow:hidden;"
        : "position:fixed;inset:0;pointer-events:none;z-index:8999;overflow:hidden;";
      (host || document.body).appendChild(layer);
    }
    return layer;
  }

  function width() { return (host || document.documentElement).clientWidth || 1920; }

  /** 文字1かたまりに字幕デザインの見た目（look）を px の大きさで当てる。
   *  設定画面のプレビューも同じ関数で描く（見た目をずらさないため） */
  W.styleText = function (el, k, px) {
    k = k || {};
    el.style.fontSize = px + "px";
    el.style.fontFamily = k.font || "'Yu Gothic UI','Meiryo',sans-serif";
    el.style.fontWeight = k.weight || 800;
    el.style.color = k.color || "#ffffff";
    const ow = k.outlineWidth ?? 2;
    // 文字の大きさに合わせて縁取りも太らせる（字幕の size 40px 基準）
    const scale = px / 40;
    el.style.webkitTextStroke = ow > 0
      ? `${ow * 2 * scale}px ${k.outlineColor || "#000000"}` : "";
    const glow = k.glow && k.glowSize > 0 ? `0 0 ${k.glowSize * scale}px ${k.glow}` : "";
    const shadow = k.shadow ? `0 ${2 * scale}px ${4 * scale}px rgba(0,0,0,.6)` : "";
    el.style.textShadow = [glow, shadow].filter(Boolean).join(",");
  };

  /** 数字の大きさ（px）。size は画面幅に対する比率 */
  W.valuePx = (w, areaWidth) => Math.max(8, (w.size || 0.12) * 0.3 * areaWidth);
  W.labelPx = (w, areaWidth) => Math.max(6, W.valuePx(w, areaWidth) * (w.label_scale || 0.55));

  function applyLook(e, w) {
    const el = e.root;
    el.style.left = (w.pos?.x ?? 0.9) * 100 + "%";
    el.style.top = (w.pos?.y ?? 0.1) * 100 + "%";
    W.styleText(e.v, w.look, W.valuePx(w, width()));
    W.styleText(e.lb, w.label_look || w.look, W.labelPx(w, width()));
    // 増えたときの動き（em 単位）は数字の大きさを基準にする
    el.style.fontSize = W.valuePx(w, width()) + "px";
    el.style.display = w.visible === false ? "none" : "";
  }

  function render() {
    const lay = getLayer();
    const seen = new Set();
    for (const w of list) {
      seen.add(w.id);
      let e = els.get(w.id);
      if (!e) {
        const root = document.createElement("div");
        root.className = "wg";
        const lb = document.createElement("span"); lb.className = "lb";
        const v = document.createElement("span"); v.className = "v";
        root.append(lb, v);
        lay.appendChild(root);
        e = { root, lb, v };
        els.set(w.id, e);
      }
      e.w = w;
      e.lb.textContent = w.label === false ? "" : (w.name || "");
      e.lb.style.display = w.label === false ? "none" : "";
      e.v.textContent = String(w.value ?? 0);
      applyLook(e, w);
    }
    for (const [id, e] of els) {
      if (!seen.has(id)) { e.root.remove(); els.delete(id); }
    }
  }

  W.setConfig = function (widgets) {
    list = Array.isArray(widgets) ? widgets : [];
    render();
  };

  W.update = function (id, value, delta) {
    const w = list.find(x => x.id === id);
    if (w) w.value = value;
    const e = els.get(id);
    if (!e) return;
    e.v.textContent = String(value);
    const a = delta > 0 ? ANIM[e.w?.anim || "pop"] : "";
    if (a === undefined || !a) return;
    // 同じ動きを続けて出せるよう、クラスを外して描き直してから付け直す
    e.root.classList.remove("wg-" + a);
    void e.root.offsetWidth;
    e.root.classList.add("wg-" + a);
  };

  W.mount = function (el) {
    host = el;
    if (layer) { layer.remove(); layer = null; els.clear(); }
    render();
  };

  W.refresh = render;
  window.addEventListener("resize", () => {
    for (const e of els.values()) if (e.w) applyLook(e, e.w);
  });
})();
