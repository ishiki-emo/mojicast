/*
 * fx.js — 字幕描画の共有モジュール
 * overlay.html（OBS表示）/ words.html（プレビュー）/ style.html（プリセット編集）で共用。
 *
 * 提供するもの:
 *   FX.FONTS        フォント選択肢
 *   FX.LINE_ANIMS   行の登場アニメ一覧
 *   FX.WORD_ANIMS   エフェクト単語のアニメ一覧
 *   FX.PARTICLES    パーティクル種類一覧
 *   FX.applyLineStyle(el, style)          プリセットを要素へ適用
 *   FX.buildTable(hotwords, effects, style) 単語→装飾のテーブル
 *   FX.renderWords(el, text, table, style)  装飾スパン込みでテキスト描画
 *   FX.applyEntrance(el, animIn)          行の登場アニメ（typewriter対応）
 *   FX.spawnParticles(el, type, color)    要素の位置からパーティクル発射
 */
(function () {
  const FX = {};

  // ---------------- 選択肢定義 ----------------
  FX.FONTS = [
    { label: "游ゴシック",              css: "'Yu Gothic','Yu Gothic UI',sans-serif" },
    { label: "メイリオ",                css: "'Meiryo',sans-serif" },
    { label: "UD教科書体（まる文字風）", css: "'UD デジタル 教科書体 N-B','UD デジタル 教科書体 NP-B',sans-serif" },
    { label: "BIZ UDゴシック",          css: "'BIZ UDPGothic','BIZ UDGothic',sans-serif" },
    { label: "游明朝",                  css: "'Yu Mincho',serif" },
    { label: "MS ゴシック（レトロ）",    css: "'MS Gothic',monospace" },
    { label: "Impact（英字向け）",       css: "Impact,'Yu Gothic UI',sans-serif" },
  ];

  FX.LINE_ANIMS = [
    ["slide", "スライド（下から）"], ["fade", "フェード"],
    ["pop", "ポップ"], ["bounce", "バウンス"],
    ["drop", "ドロップ（上から）"], ["flip", "フリップ（回転）"],
    ["blur", "ブラー（にじみ）"], ["wipe", "ワイプ（左から）"],
    ["typewriter", "タイプライター"], ["none", "なし"],
  ];

  FX.WORD_ANIMS = [
    ["pop", "ポップ（弾ける）"], ["bounce", "バウンス"],
    ["rainbow", "レインボー"], ["shine", "シャイン（光が走る）"],
    ["neon", "ネオン（点滅点灯）"], ["wave", "ウェーブ（波打つ）"],
    ["heartbeat", "ドキドキ（鼓動）"], ["float", "ふわふわ（浮遊）"],
    ["glitch", "グリッチ（バグ風）"], ["glowpulse", "グロー点滅"],
    ["shake", "ぷるぷる"], ["spin", "スピン"],
    ["flash", "フラッシュ"], ["none", "なし"],
  ];

  FX.PARTICLES = [
    ["none", "なし"], ["spark", "✨ キラキラ"],
    ["confetti", "🎊 紙吹雪"], ["heart", "💖 ハート"],
    ["burst", "💥 光の粒"], ["sakura", "🌸 桜吹雪"],
    ["note", "🎵 音符"],
  ];

  // ---------------- 共通CSS（キーフレーム）を注入 ----------------
  const CSS = `
  .fx { display: inline-block; }
  .fx-pop      { animation: fxpop .5s ease-out; }
  .fx-bounce   { animation: fxbounce .8s ease-out; }
  .fx-shake    { animation: fxshake .4s ease-in-out infinite; }
  .fx-glowpulse{ animation: fxglow 1.2s ease-in-out infinite; }
  .fx-spin     { animation: fxspin .6s cubic-bezier(.34,1.56,.64,1); }
  .fx-flash    { animation: fxflash .9s ease-out; }
  /* レインボー/シャインは2層構造:
     本体 = 不透明文字（縁取りtext-shadowが効く）
     ::after = グラデーション文字を上に重ねる（clip-textはtext-shadowと共存できないため） */
  .fx-rainbow, .fx-shine { position: relative; }
  .fx-rainbow::after, .fx-shine::after {
    content: attr(data-text);
    position: absolute; inset: 0;
    background-image: var(--fx-grad);
    background-size: 300% 100%;
    -webkit-background-clip: text; background-clip: text;
    color: transparent;
    text-shadow: none;
    pointer-events: none;
  }
  .fx-rainbow {
    --fx-grad: linear-gradient(90deg,#ff4d9e,#ffa53d,#ffd93d,#4de07a,#3db5ff,#a570ff,#ff4d9e);
  }
  .fx-rainbow::after { animation: fxrainbow 2.4s linear infinite; }
  .fx-shine::after   { animation: fxrainbow 2.2s linear infinite; }
  @keyframes fxpop    { 0% { transform:scale(1.7) rotate(-4deg); filter:brightness(2); }
                        60% { transform:scale(.93); } 100% { transform:scale(1); } }
  @keyframes fxbounce { 0% { transform:translateY(0); } 25% { transform:translateY(-.35em); }
                        50% { transform:translateY(0); } 70% { transform:translateY(-.12em); }
                        100% { transform:translateY(0); } }
  @keyframes fxshake  { 0%,100% { transform:translateX(0) rotate(0); }
                        25% { transform:translateX(-.06em) rotate(-1.5deg); }
                        75% { transform:translateX(.06em) rotate(1.5deg); } }
  @keyframes fxglow   { 0%,100% { filter:brightness(1); } 50% { filter:brightness(1.9); } }
  @keyframes fxspin   { from { transform:rotateY(360deg) scale(.5); opacity:0; }
                        to { transform:rotateY(0) scale(1); opacity:1; } }
  @keyframes fxflash  { 0%,45%,90% { filter:brightness(1); }
                        15%,60% { filter:brightness(2.6); } }
  @keyframes fxrainbow{ from { background-position:0% 0; } to { background-position:300% 0; } }

  .fx-neon     { animation: fxneonin 1.1s both, fxglow 2.4s 1.1s ease-in-out infinite; }
  .fx-heartbeat{ animation: fxheart 1.1s ease-in-out infinite; }
  .fx-float    { animation: fxfloat 3s ease-in-out infinite; }
  .fx-glitch   { animation: fxglitch 2s steps(1) infinite; }
  .fx-wavechar { display: inline-block; animation: fxwavech 1.2s ease-in-out infinite; }
  @keyframes fxneonin { 0% { opacity:0; filter:brightness(3); }
                        8% { opacity:1; } 12% { opacity:.2; }
                        20% { opacity:1; filter:brightness(2.4); }
                        26% { opacity:.35; } 34% { opacity:1; }
                        100% { opacity:1; filter:brightness(1); } }
  @keyframes fxheart  { 0%,56%,100% { transform:scale(1); }
                        14% { transform:scale(1.18); } 28% { transform:scale(1); }
                        42% { transform:scale(1.12); } }
  @keyframes fxfloat  { 0%,100% { transform:translateY(0) rotate(-1.2deg); }
                        50% { transform:translateY(-.14em) rotate(1.2deg); } }
  @keyframes fxglitch { 0%,82%,100% { transform:translate(0,0) skewX(0); filter:none; }
                        84% { transform:translate(-.05em,.02em) skewX(-8deg); filter:brightness(1.6); }
                        86% { transform:translate(.04em,-.02em) skewX(6deg); }
                        88% { transform:translate(-.03em,0) skewX(-4deg); filter:brightness(2); }
                        90% { transform:translate(.02em,.01em) skewX(3deg); }
                        92% { transform:none; filter:none; } }
  @keyframes fxwavech { 0%,100% { transform:translateY(0); }
                        50% { transform:translateY(-.16em); } }

  .in-slide  { animation: linslide .18s ease-out; }
  .in-fade   { animation: linfade .45s ease-out; }
  .in-pop    { animation: linpop .22s cubic-bezier(.34,1.56,.64,1); }
  .in-bounce { animation: linbounce .5s cubic-bezier(.34,1.56,.64,1); }
  .in-drop   { animation: lindrop .45s ease-out; }
  .in-flip   { animation: linflip .4s ease-out; }
  .in-blur   { animation: linblur .5s ease-out; }
  .in-wipe   { animation: linwipe .5s ease-out; }
  @keyframes linslide  { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  @keyframes linfade   { from { opacity:0; } to { opacity:1; } }
  @keyframes linpop    { from { opacity:0; transform:scale(.6); } to { opacity:1; transform:scale(1); } }
  @keyframes linbounce { 0% { opacity:0; transform:translateY(18px) scale(.8); }
                         60% { opacity:1; transform:translateY(-4px) scale(1.05); }
                         100% { opacity:1; transform:none; } }
  @keyframes lindrop   { 0% { opacity:0; transform:translateY(-36px); }
                         60% { opacity:1; transform:translateY(5px); }
                         80% { transform:translateY(-3px); } 100% { opacity:1; transform:none; } }
  @keyframes linflip   { from { opacity:0; transform:perspective(400px) rotateX(85deg); }
                         to { opacity:1; transform:perspective(400px) rotateX(0); } }
  @keyframes linblur   { from { opacity:0; filter:blur(10px); } to { opacity:1; filter:blur(0); } }
  @keyframes linwipe   { from { clip-path:inset(0 100% 0 0); } to { clip-path:inset(0 0 0 0); } }
  @keyframes lintype   { from { opacity:0; } to { opacity:1; } }

  @keyframes lyrsquashx { from { opacity:0; transform:scaleX(0); } to { opacity:1; transform:scaleX(1); } }
  @keyframes lyrsquashy { from { opacity:0; transform:scaleY(0); } to { opacity:1; transform:scaleY(1); } }
  @keyframes lyrblur    { from { opacity:0; transform:scale(1.25); filter:blur(14px); }
                          to { opacity:1; transform:scale(1); filter:blur(0); } }
  @keyframes lyrslide   { from { opacity:0; transform:translateX(-46px); } to { opacity:1; transform:none; } }
  @keyframes lyrpop     { from { opacity:0; transform:scale(0) rotate(-8deg); } to { opacity:1; transform:none; } }
  @keyframes lyrwipe    { from { clip-path:inset(0 100% 0 0); } to { clip-path:inset(0 0 0 0); } }

  /* リリックビデオ風字幕: 1つの確定文を1シーンとして構成する */
  .lyr-scene { position:absolute; inset:0; pointer-events:none; transform-origin:center; }
  .lyr-unit {
    position:absolute; margin:0; line-height:1.08; white-space:nowrap;
    will-change:transform,opacity,filter,clip-path;
  }
  .lyr-char,.lyr-word { display:inline-block; white-space:pre; will-change:transform,opacity,filter; }
  .lyr-vertical { writing-mode:vertical-rl; text-orientation:upright; }
  .lyr-outline { color:transparent !important; -webkit-text-stroke:.035em var(--lyr-color,#fff); text-shadow:none !important; }
  .lyr-gradient {
    color:transparent !important;
    background:linear-gradient(100deg,var(--lyr-color,#fff) 5%,var(--lyr-accent,#69f0dd) 48%,var(--lyr-color,#fff) 94%);
    background-size:220% 100%; -webkit-background-clip:text; background-clip:text;
    text-shadow:none !important;
  }
  .lyr-accent { position:absolute; pointer-events:none; will-change:transform,opacity,clip-path; }
  .lyr-line { height:3px; border-radius:99px; transform-origin:left center;
    background:linear-gradient(90deg,var(--lyr-accent,#69f0dd),var(--lyr-color,#fff));
    box-shadow:0 0 18px rgba(105,240,221,.36);
    box-shadow:0 0 18px color-mix(in srgb,var(--lyr-accent,#69f0dd) 48%,transparent); }
  .lyr-box { border:1px solid rgba(255,255,255,.28); background:rgba(105,240,221,.08);
    border:1px solid color-mix(in srgb,var(--lyr-color,#fff) 28%,transparent);
    background:color-mix(in srgb,var(--lyr-accent,#69f0dd) 8%,transparent); }
  .lyr-ring { border-radius:50%; border:2px solid rgba(105,240,221,.42);
    border:2px solid color-mix(in srgb,var(--lyr-accent,#69f0dd) 45%,transparent); }
  .lyr-band { background:linear-gradient(90deg,color-mix(in srgb,var(--lyr-accent,#69f0dd) 70%,transparent),color-mix(in srgb,var(--lyr-color,#fff) 28%,transparent)); }
  .lyr-flash { inset:0; background:var(--lyr-color,#fff); mix-blend-mode:overlay; }
  `;

  FX.injectCss = function () {
    if (document.getElementById("fx-css")) return;
    const s = document.createElement("style");
    s.id = "fx-css";
    s.textContent = CSS;
    document.head.appendChild(s);
  };

  // ---------------- スタイル適用 ----------------
  FX.outlineShadow = function (w, color) {
    const s = [];
    w = Math.round(w || 0);
    for (let dx = -w; dx <= w; dx++)
      for (let dy = -w; dy <= w; dy++)
        if (dx || dy) s.push(`${dx}px ${dy}px 0 ${color}`);
    return s;
  };

  FX.buildTextShadow = function (style) {
    const shadows = FX.outlineShadow(style.outlineWidth ?? 2,
                                     style.outlineColor || "#000");
    if (style.glow) {
      const g = style.glowSize || 12;
      shadows.push(`0 0 ${g}px ${style.glow}`, `0 0 ${g * 2}px ${style.glow}`);
    }
    if (style.shadow) shadows.push("0 3px 8px rgba(0,0,0,.8)");
    return shadows.join(",");
  };

  FX.applyLineStyle = function (el, style, sizeOverride) {
    el.style.fontFamily = style.font;
    el.style.fontWeight = style.weight;
    el.style.fontSize = (sizeOverride || style.size) + "px";
    el.style.color = style.color;
    el.style.textShadow = FX.buildTextShadow(style);
    el.style.letterSpacing = (style.letterSpacing || 0) + "em";
  };

  // ---------------- エフェクト単語 ----------------
  FX.buildTable = function (hotwords, effects, style) {
    const map = new Map();
    for (const w of hotwords || [])
      map.set(w, { color: style.hotColor, scale: 1, anim: "pop",
                   font: "", particle: "none" });
    for (const e of effects || [])
      if (e.word) map.set(e.word, e);
    return [...map.entries()].sort((a, b) => b[0].length - a[0].length);
  };

  FX.renderWords = function (el, text, table, style) {
    el.textContent = "";
    let i = 0;
    text = text || "";
    while (i < text.length) {
      let hit = null;
      for (const [w, fx] of table)
        if (w && text.startsWith(w, i)) { hit = [w, fx]; break; }
      if (hit) {
        const [w, fx] = hit;
        const span = document.createElement("span");
        span.className = "fx" +
          (fx.anim && fx.anim !== "none" ? " fx-" + fx.anim : "");
        if (fx.anim === "wave") {
          // ウェーブは文字ごとに時差アニメ（親には変形アニメを付けない）
          span.className = "fx";
          [...w].forEach((ch, ci) => {
            const c = document.createElement("span");
            c.className = "fx-wavechar";
            c.textContent = ch;
            c.style.animationDelay = (ci * 90) + "ms";
            span.appendChild(c);
          });
        } else {
          span.textContent = w;
        }
        if (fx.anim === "shine") {
          // 単語色のベースに白い光が走るグラデーション（::afterに描く）
          const c = fx.color || "#ffd400";
          span.style.setProperty("--fx-grad",
            `linear-gradient(115deg, ${c} 42%, #ffffff 50%, ${c} 58%)`);
          if (!fx.color) span.style.color = c;
        }
        if (fx.anim === "shine" || fx.anim === "rainbow")
          span.dataset.text = w;   // ::after のグラデーション文字用
        if (fx.particle && fx.particle !== "none")
          span.dataset.particle = fx.particle;
        if (fx.color) span.style.color = fx.color;
        if (fx.color) span.dataset.color = fx.color;
        if (fx.font) span.style.fontFamily = fx.font;
        if (fx.scale && +fx.scale !== 1) span.style.fontSize = fx.scale + "em";
        span.style.fontWeight = 900;
        if (fx.color) {
          span.style.textShadow =
            FX.outlineShadow(style.outlineWidth ?? 2, style.outlineColor || "#000")
              .concat([`0 0 12px ${fx.color}`, `0 0 22px ${fx.color}`]).join(",");
        }
        el.appendChild(span);
        i += w.length;
      } else {
        let j = i + 1;
        outer: for (; j < text.length; j++)
          for (const [w] of table) if (w && text.startsWith(w, j)) break outer;
        el.appendChild(document.createTextNode(text.slice(i, j)));
        i = j;
      }
    }
  };

  // ---------------- 行の登場アニメ ----------------
  FX.applyEntrance = function (el, animIn) {
    if (!animIn || animIn === "none") return;
    if (animIn !== "typewriter") {
      el.classList.add("in-" + animIn);
      return;
    }
    // タイプライター: 文字単位（エフェクト単語はまとまりで）に遅延を振る
    const units = [];
    for (const node of [...el.childNodes]) {
      if (node.nodeType === Node.TEXT_NODE) {
        const frag = document.createDocumentFragment();
        for (const ch of node.textContent) {
          const s = document.createElement("span");
          s.textContent = ch;
          s.style.display = "inline-block";
          frag.appendChild(s);
          units.push(s);
        }
        el.replaceChild(frag, node);
      } else {
        units.push(node);
      }
    }
    const step = Math.min(45, 1400 / Math.max(1, units.length));
    units.forEach((u, i) => {
      u.style.animation = `lintype .12s both`;
      u.style.animationDelay = (i * step) + "ms";
    });
  };

  // ---------------- パーティクル ----------------
  let layer = null;
  function getLayer() {
    if (!layer || !layer.isConnected) {
      layer = document.createElement("div");
      layer.style.cssText =
        "position:fixed;inset:0;pointer-events:none;z-index:9999;overflow:hidden;";
      document.body.appendChild(layer);
    }
    return layer;
  }

  function shiftHue(hex, deg) {
    // #rrggbb → HSLで色相をずらして返す（パーティクルの彩り用）
    const n = parseInt(hex.slice(1), 16);
    let r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    let h = 0, s = 0, l = (mx + mn) / 2;
    if (mx !== mn) {
      const d = mx - mn;
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) :
          mx === g ? ((b - r) / d + 2) : ((r - g) / d + 4);
      h *= 60;
    }
    return `hsl(${(h + deg + 360) % 360},${Math.max(60, s * 100)}%,${Math.min(75, Math.max(55, l * 100))}%)`;
  }

  const PARTICLE_DEFS = {
    spark:    { chars: ["✦", "✧", "★"], n: 14, up: true,  spin: true },
    confetti: { rect: true,             n: 24, up: false, spin: true },
    heart:    { chars: ["♥"],           n: 10, up: true,  spin: false },
    burst:    { chars: ["●"],           n: 16, up: null,  spin: false },
    sakura:   { chars: ["🌸"],          n: 12, up: false, spin: true },
    note:     { chars: ["♪", "♫"],      n: 10, up: true,  spin: true },
  };

  FX.spawnParticles = function (target, type, color) {
    const def = PARTICLE_DEFS[type];
    if (!def) return;
    const rect = target.getBoundingClientRect();
    if (!rect.width) return;
    color = color || "#ffd400";
    const lay = getLayer();
    const fontPx = parseFloat(getComputedStyle(target).fontSize) || 32;

    for (let k = 0; k < def.n; k++) {
      const p = document.createElement("span");
      const colors = [color, shiftHue(color, 40), shiftHue(color, -40), "#ffffff"];
      const c = colors[Math.floor(Math.random() * colors.length)];
      // 単語の幅のどこかから発射
      const x = rect.left + Math.random() * rect.width;
      const y = rect.top + rect.height * (0.3 + Math.random() * 0.4);
      const size = fontPx * (0.18 + Math.random() * 0.22);

      if (def.rect) {
        p.style.cssText = `position:fixed;left:${x}px;top:${y}px;` +
          `width:${size * 0.8}px;height:${size * 0.5}px;background:${c};border-radius:1px;`;
      } else {
        p.textContent = def.chars[Math.floor(Math.random() * def.chars.length)];
        p.style.cssText = `position:fixed;left:${x}px;top:${y}px;` +
          `font-size:${size * 1.6}px;color:${c};line-height:1;` +
          `text-shadow:0 0 6px ${c};`;
      }
      lay.appendChild(p);

      // 飛散: burst=全方位 / up=上方向へ / confetti=舞い落ちる
      const ang = def.up === null ? Math.random() * Math.PI * 2 :
                  def.up ? (-Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 0.9) :
                           (-Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.4);
      const dist = fontPx * (1.2 + Math.random() * 2.2);
      const dx = Math.cos(ang) * dist;
      const dy = Math.sin(ang) * dist + (def.up === false ? fontPx * 2.2 : fontPx * 0.6);
      const rot = def.spin ? (Math.random() - 0.5) * 720 : 0;
      const dur = 650 + Math.random() * 500;

      p.animate([
        { transform: "translate(0,0) rotate(0) scale(1)", opacity: 1 },
        { transform: `translate(${dx * 0.7}px,${dy * 0.55}px) rotate(${rot * 0.6}deg) scale(1.05)`,
          opacity: 1, offset: 0.55 },
        { transform: `translate(${dx}px,${dy}px) rotate(${rot}deg) scale(.4)`, opacity: 0 },
      ], { duration: dur, easing: "cubic-bezier(.2,.6,.35,1)" })
       .onfinish = () => p.remove();
    }
  };

  /** 行の中の data-particle 付きスパン全てからパーティクル発射 */
  FX.burstLine = function (lineEl) {
    for (const span of lineEl.querySelectorAll("[data-particle]")) {
      FX.spawnParticles(span, span.dataset.particle, span.dataset.color);
    }
  };

  // ---------------- 字幕ボックス ----------------
  FX.hexToRgba = function (hex, a) {
    if (!hex) return "transparent";
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a ?? 1})`;
  };

  /**
   * ボックス定義を DOM に適用する
   *   boxEl:    枠（位置・背景・角丸）
   *   scrollEl: 中身（flow=下アンカー積み上げ / vertical=左アンカー縦書き）
   *   clipEl:   （任意）クリップ層。指定すると出口側にフェードマスクを敷き、
   *             見切れがハードカットでなく「すっと消える」見た目になる
   * ボックスは %指定なので親（画面 or プレビューステージ）サイズに追従する。
   */
  FX.applyBox = function (boxEl, scrollEl, box, clipEl) {
    const mode = box.mode || "flow";
    boxEl.style.position = "absolute";
    boxEl.style.left = (box.x ?? 0) + "%";
    boxEl.style.top = (box.y ?? 0) + "%";
    boxEl.style.width = (box.w ?? 100) + "%";
    boxEl.style.height = (box.h ?? 100) + "%";
    boxEl.style.background = box.bgOpacity > 0
      ? FX.hexToRgba(box.bg || "#000000", box.bgOpacity) : "transparent";
    boxEl.style.borderRadius = (box.radius ?? 0) + "px";
    boxEl.style.border = (box.borderWidth > 0 && box.borderColor)
      ? `${box.borderWidth}px solid ${box.borderColor}` : "none";
    boxEl.style.containerType = mode === "lyric" ? "size" : "";
    // クリップはclipEl（あれば）が担当。リリックははみ出し許可
    boxEl.style.overflow = (clipEl || mode === "lyric") ? "visible" : "hidden";

    if (clipEl) {
      clipEl.style.position = "absolute";
      clipEl.style.inset = "0";
      clipEl.style.borderRadius = (box.radius ?? 0) + "px";
      clipEl.style.overflow = mode === "lyric" ? "visible" : "hidden";
      // 出口エッジのフェード（flow=上端 / vertical=右端）
      const fade = box.fadePx ?? 36;
      let mask = "";
      if (mode === "vertical")
        mask = `linear-gradient(to left, transparent 0, #000 ${fade}px)`;
      else if (mode !== "lyric")
        mask = `linear-gradient(to bottom, transparent 0, #000 ${fade}px)`;
      clipEl.style.webkitMaskImage = mask;
      clipEl.style.maskImage = mask;
    }

    const pad = (box.padding ?? 16) + "px";
    scrollEl.style.position = "absolute";
    if (mode === "vertical") {
      // 縦書き: 左アンカー。新しい列が左端に入り、古い列が右へ流れて消える
      // （読み順は伝統どおり 右→左 = 古→新）
      scrollEl.style.writingMode = "vertical-rl";
      scrollEl.style.left = pad;
      scrollEl.style.top = pad;
      scrollEl.style.bottom = pad;
      scrollEl.style.right = "auto";
    } else {
      scrollEl.style.writingMode = "";
      scrollEl.style.left = pad;
      scrollEl.style.right = pad;
      scrollEl.style.bottom = pad;
      scrollEl.style.top = "auto";
    }
    scrollEl.style.textAlign = box.align || "left";
  };

  /**
   * 見切れ行の掃除: 一部でもクリップ境界を越えた行をフェードアウトして除去。
   * 静止状態で「文字が半分切れたまま」にならないようにする。
   * スクロールアニメ完了後（smoothMs+α）に呼ぶこと。最新行は消さない。
   */
  FX.pruneClipped = function (clipEl, linesEl, box) {
    if ((box.mode || "flow") === "lyric") return;
    const r = clipEl.getBoundingClientRect();
    const vert = box.mode === "vertical";
    const kids = [...linesEl.children];
    for (let i = 0; i < kids.length; i++) {
      const line = kids[i];
      if (i === kids.length - 1) continue;   // 最新行（読んでいる最中）は残す
      if (line._pruning) continue;
      const lr = line.getBoundingClientRect();
      const clipped = vert ? (lr.right > r.right + 1) : (lr.top < r.top - 1);
      if (clipped) {
        line._pruning = true;
        line.animate([{ opacity: 1 }, { opacity: 0 }],
          { duration: 350, easing: "ease-out", fill: "forwards" })
          .onfinish = () => line.remove();
      }
    }
  };

  /**
   * スムーズスクロール（FLIP方式・transformのみ＝GPU合成で軽量）
   * addFn() の中で行を追加すると、伸びた分だけ一瞬元の位置に戻してから
   * アニメで滑らかに流す。flow=上へ / vertical=右へ。box.smooth=false なら即時。
   */
  FX.smoothAppend = function (scrollEl, box, addFn) {
    const vert = box && box.mode === "vertical";
    const before = vert ? scrollEl.offsetWidth : scrollEl.offsetHeight;
    addFn();
    if (!box || !box.smooth) return;
    const delta = (vert ? scrollEl.offsetWidth : scrollEl.offsetHeight) - before;
    if (delta <= 0) return;
    scrollEl.style.transition = "none";
    scrollEl.style.transform =
      vert ? `translateX(${-delta}px)` : `translateY(${delta}px)`;
    requestAnimationFrame(() => {
      scrollEl.style.transition =
        `transform ${box.smoothMs || 250}ms cubic-bezier(.25,.6,.3,1)`;
      scrollEl.style.transform = "translate(0,0)";
    });
  };

  // ---------------- リリックビデオ風字幕 ----------------
  // 確定文を1つのシーンとして構成し、文章の長さや直前の演出を見て
  // 似合うプリセットをルール付きランダムで選ぶ。box.mode === "lyric" で使用。

  /** 句読点でフレーズ分割（。、は落とし、！？は残す。長すぎは強制分割） */
  FX.splitLyric = function (text) {
    const parts = (text || "").split(/[、。，．…]+/)
      .flatMap(p => p.split(/(?<=[！？!?])/));
    const out = [];
    for (let p of parts) {
      p = p.trim();
      while (p.length > 14) { out.push(p.slice(0, 12)); p = p.slice(12); }
      if (p) out.push(p);
    }
    return out;
  };

  /** 単語単位の分かち書き（Intl.Segmenter利用、無ければフレーズ分割へ） */
  function segmentWords(t) {
    t = t.replace(/[、。，．…]/g, " ");
    let words = [];
    if (typeof Intl !== "undefined" && Intl.Segmenter) {
      const seg = new Intl.Segmenter("ja", { granularity: "word" });
      for (const s of seg.segment(t)) {
        const w = s.segment.trim();
        if (w) words.push(w);
      }
    } else {
      return FX.splitLyric(t);
    }
    // 1文字ひらがな（助詞）や記号だけの断片は前の語へくっつける
    const out = [];
    for (const w of words) {
      const puncOnly = /^[！？!?ー〜…]+$/.test(w);
      const tinyKana = w.length === 1 && /[ぁ-ん]/.test(w);
      if (out.length && (puncOnly || tinyKana)) out[out.length - 1] += w;
      else out.push(w);
    }
    return out;
  }

  /** 単語分割。エフェクト/ホットワード（table）は分割せず丸ごと守る */
  FX.splitLyricWords = function (text, table) {
    text = text || "";
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { out.push(...segmentWords(buf)); buf = ""; } };
    let i = 0;
    while (i < text.length) {
      let hit = null;
      for (const [w] of table || [])
        if (w && text.startsWith(w, i)) { hit = w; break; }
      if (hit) { flush(); out.push(hit); i += hit.length; }
      else { buf += text[i]; i++; }
    }
    flush();
    // 保護単語の直後に残った1文字助詞などを前のチャンクへ結合
    // （ハイライトは部分一致なので「星野ひかりは」のような助詞付きでも効く）
    const merged = [];
    for (const w of out) {
      const puncOnly = /^[！？!?ー〜…]+$/.test(w);
      const tinyKana = w.length === 1 && /[ぁ-ん]/.test(w);
      if (merged.length && (puncOnly || tinyKana)) merged[merged.length - 1] += w;
      else merged.push(w);
    }
    return merged;
  };

  const LYRIC_PATTERNS = [
    { id: "soft",      label: "ソフトフォーカス", energy: 1, exit: "dissolve" },
    { id: "editorial", label: "エディトリアル",   energy: 2, exit: "wipe" },
    { id: "type",      label: "文字カスケード",    energy: 2, exit: "scatter" },
    { id: "vertical",  label: "縦書きレイヤー",    energy: 2, exit: "lift" },
    { id: "ribbon",    label: "リボンワイプ",      energy: 3, exit: "sweep" },
    { id: "diagonal",  label: "ダイアゴナル",      energy: 3, exit: "rush" },
    { id: "split",     label: "スプリット",        energy: 3, exit: "part" },
    { id: "orbit",     label: "オービット",        energy: 3, exit: "implode" },
    { id: "echo",      label: "エコートレイル",    energy: 3, exit: "drift" },
    { id: "karaoke",   label: "グラデーション走査", energy: 3, exit: "wipe" },
    { id: "glitch",    label: "グリッチカット",    energy: 4, exit: "cut" },
    { id: "impact",    label: "インパクト",        energy: 4, exit: "burst" },
  ];
  FX.LYRIC_PATTERNS = LYRIC_PATTERNS.map(p => ({ ...p }));

  const lyricReducedMotion = typeof matchMedia === "function" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lyricDuration = ms => lyricReducedMotion ? Math.max(80, ms * .35) : ms;

  function lyricAnimate(el, frames, options) {
    options = { ...options, duration: lyricDuration(options.duration || 500) };
    if (el.animate) return el.animate(frames, options);
    Object.assign(el.style, frames[frames.length - 1]);
    return { onfinish: null, cancel() {} };
  }

  function lyricScene(container, style, pattern) {
    const el = document.createElement("div");
    el.className = "lyr-scene";
    el.dataset.lyricPattern = pattern.id;
    el._lyrExit = pattern.exit;
    el.style.setProperty("--lyr-color", style.color || "#ffffff");
    el.style.setProperty("--lyr-accent", style.hotColor || style.glow || "#69f0dd");
    container.appendChild(el);
    return el;
  }

  function lyricUnit(scene, text, opts, size, className = "") {
    const el = document.createElement("div");
    el.className = `lyr-unit ${className}`;
    FX.applyLineStyle(el, opts.style, Math.max(14, Math.round(size)));
    FX.renderWords(el, text, opts.table, opts.style);
    scene.appendChild(el);
    return el;
  }

  function lyricPlainUnit(scene, text, opts, size, className = "") {
    const el = document.createElement("div");
    el.className = `lyr-unit ${className}`;
    FX.applyLineStyle(el, opts.style, Math.max(14, Math.round(size)));
    el.textContent = text;
    scene.appendChild(el);
    return el;
  }

  function lyricAccent(scene, className, styles) {
    const el = document.createElement("div");
    el.className = `lyr-accent ${className}`;
    Object.assign(el.style, styles || {});
    scene.appendChild(el);
    return el;
  }

  function lyricSplitLines(text) {
    text = String(text || "").trim();
    if (text.length <= 12) return [text];
    const center = Math.floor(text.length * .52);
    const marks = [" ", "、", "。", "！", "？", "!", "?"];
    let pos = -1;
    for (let d = 0; d < Math.min(8, text.length); d++) {
      for (const p of [center - d, center + d])
        if (p > 2 && p < text.length - 2 && marks.includes(text[p])) { pos = p + (text[p] === " " ? 0 : 1); break; }
      if (pos >= 0) break;
    }
    if (pos < 0) pos = center;
    return [text.slice(0, pos).trim(), text.slice(pos).trim()].filter(Boolean);
  }

  function lyricBaseSize(opts) {
    const length = opts.lyricTextLength || 0;
    const fit = length > 28 ? .64 : length > 20 ? .76 : length > 14 ? .88 : 1;
    return (opts.style.size || 40) * (opts.box.lyricScale ?? 1.35) * (opts.fontScale ?? 1) * fit;
  }

  const lyricBuilders = {
    soft(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      lines.forEach((line, i) => {
        const el = lyricUnit(scene, line, opts, base * (i ? 1.18 : .86), i ? "lyr-gradient" : "");
        Object.assign(el.style, { left: i ? "16%" : "10%", top: `${30 + i * 24}%` });
        lyricAnimate(el, [
          { opacity: 0, filter: "blur(15px)", transform: "translateY(18px) scale(1.05)", letterSpacing: ".1em" },
          { opacity: 1, filter: "blur(0)", transform: "none", letterSpacing: opts.style.letterSpacing ? `${opts.style.letterSpacing}em` : "normal" }
        ], { duration: 1050, delay: i * 180, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
      const line = lyricAccent(scene, "lyr-line", { left: "10%", top: "72%", width: "25%" });
      lyricAnimate(line, [{ opacity: 0, transform: "scaleX(0)" }, { opacity: .9, transform: "scaleX(1)" }], { duration: 760, delay: 340, easing: "ease-out", fill: "both" });
    },

    editorial(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      const first = lyricUnit(scene, lines[0], opts, base * 1.22);
      Object.assign(first.style, { left: "8%", top: "20%" });
      lyricAnimate(first, [{ opacity: 0, clipPath: "inset(0 100% 0 0)", transform: "translateX(-24px)" }, { opacity: 1, clipPath: "inset(0)", transform: "none" }], { duration: 720, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      if (lines[1]) {
        const second = lyricUnit(scene, lines[1], opts, base * .94, "lyr-outline");
        Object.assign(second.style, { right: "8%", top: "57%", textAlign: "right" });
        lyricAnimate(second, [{ opacity: 0, transform: "translateX(35px)" }, { opacity: 1, transform: "none" }], { duration: 680, delay: 220, easing: "ease-out", fill: "both" });
      }
      const box = lyricAccent(scene, "lyr-box", { left: "5%", top: "13%", width: "60%", height: "36%" });
      lyricAnimate(box, [{ opacity: 0, clipPath: "inset(0 100% 100% 0)" }, { opacity: 1, clipPath: "inset(0)" }], { duration: 850, easing: "ease-out", fill: "both" });
    },

    type(scene, text, opts) {
      const base = lyricBaseSize(opts);
      const el = lyricPlainUnit(scene, "", opts, base);
      Object.assign(el.style, { left: "50%", top: "47%", transform: "translate(-50%,-50%)" });
      Array.from(text).forEach((ch, i) => {
        const span = document.createElement("span");
        span.className = "lyr-char"; span.textContent = ch; el.appendChild(span);
        lyricAnimate(span, [
          { opacity: 0, transform: `translateY(24px) rotate(${(i % 3 - 1) * 4}deg) scale(.7)`, filter: "blur(4px)" },
          { opacity: 1, transform: "translateY(0) scale(1.08)", filter: "blur(0)", offset: .72 },
          { opacity: 1, transform: "none", filter: "blur(0)" }
        ], { duration: 470, delay: i * 34, easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
      });
    },

    vertical(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      lines.forEach((line, i) => {
        const el = lyricUnit(scene, line, opts, base * (i ? .78 : .95), `lyr-vertical ${i ? "lyr-outline" : ""}`);
        Object.assign(el.style, { right: `${22 + i * 24}%`, top: i ? "10%" : "20%" });
        lyricAnimate(el, [{ opacity: 0, clipPath: "inset(0 0 100% 0)", transform: "translateY(-25px)" }, { opacity: 1, clipPath: "inset(0)", transform: "none" }], { duration: 880, delay: i * 220, easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
      });
      for (let i = 0; i < 2; i++) {
        const ring = lyricAccent(scene, "lyr-ring", { left: `${9 + i * 8}%`, bottom: `${12 + i * 8}%`, width: `${11 + i * 5}%`, aspectRatio: "1" });
        lyricAnimate(ring, [{ opacity: 0, transform: "scale(.2)" }, { opacity: .55 - i * .15, transform: "scale(1)" }], { duration: 900, delay: i * 140, easing: "ease-out", fill: "both" });
      }
    },

    ribbon(scene, text, opts) {
      const base = lyricBaseSize(opts);
      const band = lyricAccent(scene, "lyr-band", { left: "-4%", top: "38%", width: "108%", height: "25%", transform: "skewY(-3deg)" });
      lyricAnimate(band, [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0)" }], { duration: 650, easing: "cubic-bezier(.5,0,.15,1)", fill: "both" });
      const el = lyricUnit(scene, text, opts, base * .98);
      Object.assign(el.style, { left: "50%", top: "49%", transform: "translate(-50%,-50%) rotate(-3deg)" });
      lyricAnimate(el, [{ opacity: 0, clipPath: "inset(0 0 100% 0)", transform: "translate(-50%,-32%) rotate(-3deg)" }, { opacity: 1, clipPath: "inset(0)", transform: "translate(-50%,-50%) rotate(-3deg)" }], { duration: 560, delay: 190, easing: "ease-out", fill: "both" });
    },

    diagonal(scene, text, opts) {
      const base = lyricBaseSize(opts);
      const el = lyricPlainUnit(scene, "", opts, base * 1.05, "lyr-gradient");
      Object.assign(el.style, { left: "50%", top: "47%", transform: "translate(-50%,-50%) skewX(-8deg) rotate(-4deg)" });
      text.split(/(\s+|[、。])/).filter(Boolean).forEach((part, i) => {
        const span = document.createElement("span"); span.className = "lyr-word"; span.textContent = part; el.appendChild(span);
        lyricAnimate(span, [{ opacity: 0, transform: `translate(${i % 2 ? 100 : -100}px,${i % 2 ? -40 : 40}px) scaleX(1.4)`, filter: "blur(6px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }], { duration: 570, delay: i * 70, easing: "cubic-bezier(.1,.8,.2,1)", fill: "both" });
      });
      for (let i = 0; i < 3; i++) {
        const line = lyricAccent(scene, "lyr-line", { left: `${-4 + i * 26}%`, top: `${78 - i * 22}%`, width: "32%", transform: "rotate(-24deg)", opacity: ".35" });
        lyricAnimate(line, [{ transform: "translateX(-140%) rotate(-24deg)" }, { transform: "translateX(390%) rotate(-24deg)" }], { duration: 980, delay: i * 90, easing: "ease-out", fill: "both" });
      }
    },

    split(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      const halves = lines.length > 1 ? lines : [text.slice(0, Math.ceil(text.length / 2)), text.slice(Math.ceil(text.length / 2))];
      lyricAccent(scene, "lyr-box", { left: 0, top: 0, width: "50%", height: "100%", border: "none" });
      lyricAccent(scene, "lyr-box", { right: 0, top: 0, width: "50%", height: "100%", border: "none", opacity: ".5" });
      halves.forEach((line, i) => {
        const el = lyricUnit(scene, line, opts, base * (i ? .86 : 1.05), i ? "lyr-outline" : "");
        Object.assign(el.style, { left: i ? "54%" : "46%", top: i ? "55%" : "34%", textAlign: i ? "left" : "right", transform: i ? "none" : "translateX(-100%)" });
        lyricAnimate(el, [{ opacity: 0, transform: i ? "translateX(75px)" : "translateX(calc(-100% - 75px))" }, { opacity: 1, transform: i ? "none" : "translateX(-100%)" }], { duration: 650, delay: i * 180, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
    },

    orbit(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      const core = lyricUnit(scene, lines[0], opts, base * 1.05, "lyr-gradient");
      Object.assign(core.style, { left: "50%", top: "50%", transform: "translate(-50%,-50%)" });
      lyricAnimate(core, [{ opacity: 0, transform: "translate(-50%,-50%) scale(.3) rotate(-8deg)" }, { opacity: 1, transform: "translate(-50%,-50%) scale(1)" }], { duration: 760, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      Array.from(lines[1] || lines[0]).slice(0, 10).forEach((ch, i, chars) => {
        const el = lyricPlainUnit(scene, ch, opts, base * .42, "lyr-outline");
        Object.assign(el.style, { left: "50%", top: "50%" });
        const angle = i / chars.length * Math.PI * 2, x = Math.cos(angle) * 34, y = Math.sin(angle) * 31;
        lyricAnimate(el, [{ opacity: 0, transform: "translate(-50%,-50%) scale(.2)" }, { opacity: .9, transform: `translate(-50%,-50%) translate(${x}cqw,${y}cqh) rotate(${angle + Math.PI / 2}rad)` }], { duration: 840, delay: i * 38, easing: "ease-out", fill: "both" });
      });
      const ring = lyricAccent(scene, "lyr-ring", { left: "18%", top: "15%", width: "64%", height: "70%" });
      lyricAnimate(ring, [{ opacity: 0, transform: "scale(.4) rotate(-20deg)" }, { opacity: .6, transform: "scale(1)" }], { duration: 900, easing: "ease-out", fill: "both" });
    },

    echo(scene, text, opts) {
      const base = lyricBaseSize(opts);
      [{ x: 50, y: 49, o: 1 }, { x: 48, y: 45, o: .32 }, { x: 46, y: 41, o: .16 }, { x: 44, y: 37, o: .08 }].reverse().forEach((p, i, arr) => {
        const el = lyricUnit(scene, text, opts, base, i === arr.length - 1 ? "lyr-gradient" : "lyr-outline");
        Object.assign(el.style, { left: `${p.x}%`, top: `${p.y}%`, opacity: p.o, transform: "translate(-50%,-50%)", mixBlendMode: i < 3 ? "screen" : "normal" });
        lyricAnimate(el, [{ opacity: 0, transform: `translate(-50%,-50%) translate(${(i - 2) * 35}px,${(2 - i) * 20}px) scale(1.15)`, filter: "blur(10px)" }, { opacity: p.o, transform: "translate(-50%,-50%)", filter: "blur(0)" }], { duration: 860, delay: i * 90, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
    },

    karaoke(scene, text, opts) {
      const base = lyricBaseSize(opts);
      const under = lyricUnit(scene, text, opts, base, "lyr-outline");
      const fill = lyricUnit(scene, text, opts, base, "lyr-gradient");
      [under, fill].forEach(el => Object.assign(el.style, { left: "50%", top: "48%", transform: "translate(-50%,-50%)" }));
      under.style.opacity = ".48";
      lyricAnimate(fill, [{ clipPath: "inset(0 100% 0 0)", backgroundPosition: "100% 0" }, { clipPath: "inset(0)", backgroundPosition: "0% 0" }], { duration: 1850, delay: 180, easing: "linear", fill: "both" });
      const scan = lyricAccent(scene, "lyr-line", { left: "8%", top: "63%", width: "1px", height: "2px" });
      lyricAnimate(scan, [{ opacity: 0, transform: "translateX(0)" }, { opacity: .9, offset: .1 }, { opacity: .9, offset: .9 }, { opacity: 0, transform: "translateX(76cqw)" }], { duration: 1850, delay: 180, easing: "linear", fill: "both" });
    },

    glitch(scene, text, opts) {
      const base = lyricBaseSize(opts);
      const main = lyricUnit(scene, text, opts, base * 1.08);
      Object.assign(main.style, { left: "50%", top: "48%", transform: "translate(-50%,-50%)", zIndex: 3 });
      ["var(--lyr-accent)", "#ff4e8b"].forEach((color, i) => {
        const clone = lyricPlainUnit(scene, text, opts, base * 1.08);
        Object.assign(clone.style, { left: "50%", top: "48%", transform: "translate(-50%,-50%)", color, mixBlendMode: "screen", opacity: .65, zIndex: 2, clipPath: i ? "inset(14% 0 52% 0)" : "inset(56% 0 10% 0)" });
        lyricAnimate(clone, [{ opacity: 0, transform: `translate(-50%,-50%) translateX(${i ? 85 : -85}px) skewX(${i ? 16 : -16}deg)` }, { opacity: .8, transform: `translate(-50%,-50%) translateX(${i ? -8 : 8}px)`, offset: .5 }, { opacity: .18, transform: "translate(-50%,-50%)" }], { duration: 580, delay: i * 35, easing: "steps(5,end)", fill: "both" });
      });
      lyricAnimate(main, [{ opacity: 0, filter: "blur(9px)", transform: "translate(-50%,-50%) scaleX(1.5)" }, { opacity: 1, filter: "blur(0)", transform: "translate(-50%,-50%) scaleX(.94)", offset: .6 }, { opacity: 1, transform: "translate(-50%,-50%)" }], { duration: 620, easing: "steps(6,end)", fill: "both" });
      const flash = lyricAccent(scene, "lyr-flash", {});
      lyricAnimate(flash, [{ opacity: 0 }, { opacity: .55, offset: .2 }, { opacity: 0 }], { duration: 200, fill: "both" });
    },

    impact(scene, text, opts) {
      const base = lyricBaseSize(opts), lines = lyricSplitLines(text);
      lines.forEach((line, i) => {
        const el = lyricUnit(scene, line, opts, base * (i ? 1.05 : 1.35), i ? "lyr-gradient" : "");
        Object.assign(el.style, { left: "50%", top: `${38 + i * 22}%`, transform: "translate(-50%,-50%)", zIndex: 3 });
        lyricAnimate(el, [{ opacity: 0, transform: "translate(-50%,-50%) scale(2.4)", filter: "blur(15px)", letterSpacing: "-.06em" }, { opacity: 1, transform: "translate(-50%,-50%) scale(.88)", filter: "blur(0)", offset: .7 }, { opacity: 1, transform: "translate(-50%,-50%) scale(1)" }], { duration: 660, delay: i * 110, easing: "cubic-bezier(.12,.75,.2,1)", fill: "both" });
      });
      for (let i = 0; i < 3; i++) {
        const ring = lyricAccent(scene, "lyr-ring", { left: "50%", top: "50%", width: "8%", aspectRatio: "1", transform: "translate(-50%,-50%)" });
        lyricAnimate(ring, [{ opacity: .8, transform: "translate(-50%,-50%) scale(.2)" }, { opacity: 0, transform: `translate(-50%,-50%) scale(${7 + i * 3})` }], { duration: 780, delay: i * 90, easing: "ease-out", fill: "both" });
      }
      for (let i = 0; i < 16; i++) {
        const dot = lyricAccent(scene, "", { left: "50%", top: "50%", width: `${2 + i % 3}px`, height: `${2 + i % 3}px`, borderRadius: "50%", background: "var(--lyr-accent)" });
        const a = i / 16 * Math.PI * 2, dist = 90 + i % 5 * 26;
        lyricAnimate(dot, [{ opacity: 1, transform: "translate(-50%,-50%)" }, { opacity: 0, transform: `translate(calc(-50% + ${Math.cos(a) * dist}px),calc(-50% + ${Math.sin(a) * dist}px))` }], { duration: 720, delay: i * 12, easing: "ease-out", fill: "both" });
      }
      const flash = lyricAccent(scene, "lyr-flash", {});
      lyricAnimate(flash, [{ opacity: 0 }, { opacity: .65, offset: .18 }, { opacity: 0 }], { duration: 240, fill: "both" });
    }
  };

  function lyricChoose(text, box, state) {
    const forced = box.lyricPattern && LYRIC_PATTERNS.find(p => p.id === box.lyricPattern);
    if (forced) {
      state.lastPattern = forced.id;
      return forced;
    }
    const mood = box.lyricMood || "auto";
    const length = Array.from(text).length;
    const hasBreak = /[ 、。！？!?]/.test(text);
    let candidates = LYRIC_PATTERNS.filter(p => {
      if (p.id === state.lastPattern) return false;
      if (mood === "calm" && p.energy > 2) return false;
      if (mood === "lively" && p.energy < 2) return false;
      if (state.strongCooldown > 0 && p.energy >= 4) return false;
      if (p.id === "orbit" && length > 16) return false;
      if (p.id === "impact" && length > 14) return false;
      if (p.id === "vertical" && length > 22) return false;
      if (p.id === "split" && length < 8) return false;
      return true;
    });
    if (!candidates.length) candidates = LYRIC_PATTERNS.filter(p => p.id !== state.lastPattern && p.energy <= 3);
    const weighted = [];
    for (const p of candidates) {
      let weight = 3;
      if (length <= 7 && ["type", "ribbon", "glitch", "impact"].includes(p.id)) weight += 4;
      if (length >= 18 && ["soft", "editorial", "split", "karaoke"].includes(p.id)) weight += 5;
      if (hasBreak && ["editorial", "split", "ribbon"].includes(p.id)) weight += 3;
      if (p.id === "vertical" || p.id === "orbit") weight = Math.max(1, weight - 2);
      if (p.energy >= 4 && mood !== "lively") weight = 1;
      for (let i = 0; i < weight; i++) weighted.push(p);
    }
    const chosen = weighted[Math.floor(Math.random() * weighted.length)] || candidates[0] || LYRIC_PATTERNS[0];
    state.strongCooldown = chosen.energy >= 4 ? 3 : Math.max(0, state.strongCooldown - 1);
    state.lastPattern = chosen.id;
    return chosen;
  }

  function lyricExit(scene, immediate = false) {
    if (!scene || !scene.isConnected || scene._lyrExiting) return;
    scene._lyrExiting = true;
    clearTimeout(scene._lyrTimer);
    if (immediate || !scene.animate) { scene.remove(); return; }
    const kind = scene._lyrExit || "dissolve";
    const exits = {
      dissolve: { opacity: 0, transform: "translateY(-12px) scale(1.04)", filter: "blur(14px)" },
      wipe: { opacity: 0, transform: "translateX(12px)", clipPath: "inset(0 0 0 100%)" },
      scatter: { opacity: 0, transform: `translate(${Math.random() * 90 - 45}px,${Math.random() * 70 - 35}px) rotate(${Math.random() * 18 - 9}deg) scale(.75)`, filter: "blur(5px)" },
      lift: { opacity: 0, transform: "translateY(-70px)", filter: "blur(4px)", clipPath: "inset(0 0 100% 0)" },
      sweep: { opacity: 0, transform: "translateX(120px) skewX(-14deg)", filter: "blur(3px)", clipPath: "inset(0 0 0 100%)" },
      rush: { opacity: 0, transform: "translate(150px,-70px) skewX(-18deg)", filter: "blur(8px)" },
      part: { opacity: 0, transform: "scaleX(.45)", filter: "blur(3px)" },
      implode: { opacity: 0, transform: "scale(.05) rotate(25deg)", filter: "blur(9px)" },
      drift: { opacity: 0, transform: "translate(55px,-30px) scale(1.08)", filter: "blur(11px)" },
      cut: { opacity: 0, transform: "translateX(90px) scaleX(1.3)", filter: "blur(2px) hue-rotate(90deg)", clipPath: "inset(52% 0 0 0)" },
      burst: { opacity: 0, transform: "scale(1.7)", filter: "blur(13px)" },
    };
    const anim = lyricAnimate(scene, [{ opacity: 1, transform: "none", filter: "blur(0)", clipPath: "inset(0)" }, exits[kind] || exits.dissolve], { duration: kind === "cut" ? 240 : 460, easing: kind === "cut" ? "steps(4,end)" : "cubic-bezier(.55,0,1,.45)", fill: "forwards" });
    anim.onfinish = () => scene.remove();
  }

  FX.lyricSpawn = function (container, text, opts) {
    text = String(text || "").trim();
    if (!text || !container.isConnected) return null;
    opts = { ...opts, lyricTextLength: Array.from(text).length };
    const state = container._lyr ?? (container._lyr = { scenes: [], lastPattern: "", strongCooldown: 0 });
    const pattern = lyricChoose(text, opts.box || {}, state);
    const scene = lyricScene(container, opts.style, pattern);
    lyricBuilders[pattern.id](scene, text, opts);
    state.scenes.push(scene);
    const maxScenes = Math.max(1, Math.min(3, opts.box.lyricMaxScenes ?? 2));
    while (state.scenes.length > maxScenes) lyricExit(state.scenes.shift());
    const life = Math.max(2, opts.box.lifeSec ?? 6) * 1000 * (.92 + Math.random() * .16);
    scene._lyrTimer = setTimeout(() => {
      const i = state.scenes.indexOf(scene);
      if (i >= 0) state.scenes.splice(i, 1);
      lyricExit(scene);
    }, life);
    FX.burstLine(scene.querySelector(".lyr-unit") || scene);
    return pattern.id;
  };

  FX.lyricClear = function (container) {
    const state = container._lyr;
    if (!state) return;
    for (const scene of state.scenes) lyricExit(scene, true);
    state.scenes = [];
    state.lastPattern = "";
    state.strongCooldown = 0;
  };

  FX.injectCss();
  window.FX = FX;
})();
