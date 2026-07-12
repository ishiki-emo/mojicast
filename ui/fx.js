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

  // ---------------- リリックビデオモード ----------------
  // 確定テキストをフレーズに割り、エリア内へランダム配置＋キネティック出現、
  // 寿命が来たらふわっと消える。box.mode === "lyric" のとき使用。

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

  // 出現キネティクス（origin は「にゅっ」と伸びる起点）
  const KINETICS = [
    { name: "lyrsquashx", origin: "left center", dur: 480 },   // 横ににゅっ
    { name: "lyrsquashy", origin: "center top",  dur: 480 },   // 縦ににゅっ
    { name: "lyrblur",    origin: "center",      dur: 550 },
    { name: "lyrslide",   origin: "center",      dur: 420 },
    { name: "lyrpop",     origin: "center",      dur: 420 },
    { name: "lyrwipe",    origin: "center",      dur: 500 },
  ];

  function shuffle9() {
    const z = [];
    for (let x = 0; x < 3; x++) for (let y = 0; y < 3; y++) z.push([x, y]);
    for (let i = z.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [z[i], z[j]] = [z[j], z[i]];
    }
    return z;
  }

  function exitChunk(el) {
    if (!el || !el.isConnected) return;
    clearTimeout(el._lyrTimer);
    el.animate([
      { opacity: 1, transform: el.style.transform },
      { opacity: 0, transform: el.style.transform + " translateY(-14px) scale(.97)" },
    ], { duration: 600, easing: "ease-in" }).onfinish = () => el.remove();
  }

  function spawnChunk(container, text, opts) {
    const { style, table, box, fontScale = 1 } = opts;
    if (!container.isConnected) return;
    const st = container._lyr ?? (container._lyr = { chunks: [], zones: shuffle9(), zi: 0 });

    const outer = document.createElement("div");
    outer.style.position = "absolute";
    // 縦書き＆軽い回転で日本語リリックの味を出す
    const vert = Math.random() * 100 < (box.vertRate ?? 25);
    if (vert) outer.style.writingMode = "vertical-rl";
    const rot = (Math.random() * 2 - 1) * (box.rotate ?? 6);
    outer.style.transform = `rotate(${rot.toFixed(1)}deg)`;

    const inner = document.createElement("div");
    const jit = box.sizeJitter ?? 0.35;
    const scale = (1 + (Math.random() * 2 - 1) * jit) * (box.lyricScale ?? 1);
    FX.applyLineStyle(inner, style,
      Math.max(12, Math.round((style.size || 40) * scale * fontScale)));
    inner.style.lineHeight = "1.2";
    inner.style.whiteSpace = "nowrap";
    FX.renderWords(inner, text, table, style);

    const kin = KINETICS[Math.floor(Math.random() * KINETICS.length)];
    inner.style.transformOrigin = kin.origin;
    inner.style.animation =
      `${kin.name} ${kin.dur}ms cubic-bezier(.2,1.4,.4,1) both`;
    outer.appendChild(inner);

    // 一旦不可視で置いてサイズ計測 → 3x3ゾーン巡回＋ジッタで配置（重なり軽減）
    outer.style.visibility = "hidden";
    container.appendChild(outer);
    const bw = container.clientWidth, bh = container.clientHeight;
    const cw = outer.offsetWidth, ch = outer.offsetHeight;
    const z = st.zones[st.zi++ % 9];
    if (st.zi % 9 === 0) st.zones = shuffle9();
    let x = z[0] / 3 * bw + Math.random() * Math.max(1, bw / 3 - cw);
    let y = z[1] / 3 * bh + Math.random() * Math.max(1, bh / 3 - ch);
    // 大きな文字はエリア端から3割まで「はみ出してOK」の緩いクランプ
    x = Math.min(Math.max(-cw * 0.3, x), Math.max(-cw * 0.3, bw - cw * 0.7));
    y = Math.min(Math.max(-ch * 0.3, y), Math.max(-ch * 0.3, bh - ch * 0.7));
    outer.style.left = x + "px";
    outer.style.top = y + "px";
    outer.style.visibility = "";
    FX.burstLine(inner);

    st.chunks.push(outer);
    while (st.chunks.length > (box.maxChunks ?? 8))
      exitChunk(st.chunks.shift());
    const life = (box.lifeSec ?? 6) * 1000 * (0.85 + Math.random() * 0.3);
    outer._lyrTimer = setTimeout(() => {
      const i = st.chunks.indexOf(outer);
      if (i >= 0) st.chunks.splice(i, 1);
      exitChunk(outer);
    }, life);
  }

  /** テキストを分割（フレーズ or 単語）し、時差でエリアに散らす */
  FX.lyricSpawn = function (container, text, opts) {
    const chunks = (opts.box.lyricSplit === "word")
      ? FX.splitLyricWords(text, opts.table)
      : FX.splitLyric(text);
    const stagger = opts.box.stagger ?? 130;
    chunks.forEach((t, i) =>
      setTimeout(() => spawnChunk(container, t, opts), i * stagger));
  };

  FX.lyricClear = function (container) {
    const st = container._lyr;
    if (!st) return;
    for (const c of st.chunks) { clearTimeout(c._lyrTimer); c.remove(); }
    st.chunks = [];
  };

  FX.injectCss();
  window.FX = FX;
})();
