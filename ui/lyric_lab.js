(function () {
  "use strict";

  const $ = id => document.getElementById(id);
  const stage = $("stage");
  const lyrics = $("lyrics");
  const accents = $("accents");
  const canvas = $("ambient");
  const ctx = canvas.getContext("2d");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const SAMPLE_LINES = [
    "きみの声が 夜を照らしていく",
    "まだ見たことのない景色へ",
    "この瞬間を 忘れないで",
    "心拍数は もう止められない",
    "せーので世界を塗り替えよう",
    "朝焼けの向こうで また会おう"
  ];

  const PRESETS = [
    { id: "soft", icon: "淡", name: "ソフトフォーカス", desc: "静かな語り・バラード", energy: 1, exit: "dissolve", tech: "blur / opacity" },
    { id: "editorial", icon: "組", name: "エディトリアル", desc: "余白を活かす誌面構成", energy: 2, exit: "wipe", tech: "grid / clip-path" },
    { id: "type", icon: "字", name: "タイプカスケード", desc: "一文字ずつ小気味よく", energy: 2, exit: "scatter", tech: "stagger / WAAPI" },
    { id: "vertical", icon: "縦", name: "縦書きレイヤー", desc: "和風・エモーショナル", energy: 2, exit: "lift", tech: "writing-mode / mask" },
    { id: "ribbon", icon: "帯", name: "リボンワイプ", desc: "歌詞を帯で切り取る", energy: 3, exit: "sweep", tech: "clip-path / transform" },
    { id: "diagonal", icon: "斜", name: "ダイアゴナルラッシュ", desc: "疾走感・テンポ重視", energy: 3, exit: "rush", tech: "skew / stagger" },
    { id: "split", icon: "分", name: "スプリットスクリーン", desc: "掛け合い・対比する歌詞", energy: 3, exit: "part", tech: "layout / wipe" },
    { id: "orbit", icon: "軌", name: "オービット", desc: "言葉が中心を周回する", energy: 3, exit: "implode", tech: "motion path / transform" },
    { id: "echo", icon: "残", name: "エコートレイル", desc: "残像が重なる浮遊感", energy: 3, exit: "drift", tech: "clone / blend" },
    { id: "karaoke", icon: "歌", name: "グラデーション走査", desc: "歌唱の進行を強調", energy: 3, exit: "wipe", tech: "background-clip / mask" },
    { id: "glitch", icon: "乱", name: "グリッチカット", desc: "デジタル・強いアクセント", energy: 4, exit: "cut", tech: "filter / blend / slice" },
    { id: "impact", icon: "爆", name: "コーラスインパクト", desc: "サビ・決め台詞を最大化", energy: 4, exit: "burst", tech: "scale / flash / canvas" }
  ];

  const state = {
    preset: 0,
    mode: "manual",
    cursor: 0,
    phrase: 0,
    timer: 0,
    seed: 724,
    rng: null,
    ambientMode: "calm",
    particles: [],
    raf: 0,
    token: 0
  };

  function mulberry32(seed) {
    return function () {
      let t = seed += 0x6D2B79F5;
      t = Math.imul(t ^ t >>> 15, t | 1);
      t ^= t + Math.imul(t ^ t >>> 7, t | 61);
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  function resetRng() {
    const n = parseInt($("seed").value, 10);
    state.seed = Number.isFinite(n) ? n : 724;
    state.rng = mulberry32(state.seed);
  }

  function rand(min = 0, max = 1) { return min + state.rng() * (max - min); }
  function pick(items) { return items[Math.floor(rand(0, items.length))]; }
  function tempo(ms) { return Math.max(80, ms * Number($("tempo").value || 1) * (reducedMotion ? .35 : 1)); }

  function animate(el, keyframes, options) {
    const adjusted = Object.assign({}, options, { duration: tempo(options.duration || 500) });
    if (!el.animate) {
      Object.assign(el.style, keyframes[keyframes.length - 1]);
      return { finished: Promise.resolve(), cancel() {} };
    }
    return el.animate(keyframes, adjusted);
  }

  function addUnit(text, className = "") {
    const el = document.createElement("div");
    el.className = `lyric-unit ${className}`;
    el.textContent = text;
    lyrics.appendChild(el);
    return el;
  }

  function splitChars(el, text) {
    el.textContent = "";
    return Array.from(text).map(ch => {
      const span = document.createElement("span");
      span.className = "char";
      span.textContent = ch;
      el.appendChild(span);
      return span;
    });
  }

  function splitWords(el, text) {
    el.textContent = "";
    const parts = text.split(/(\s+)/);
    return parts.map(part => {
      const span = document.createElement("span");
      span.className = "word";
      span.textContent = part;
      el.appendChild(span);
      return span;
    });
  }

  function accent(className, style = {}) {
    const el = document.createElement("div");
    el.className = className;
    Object.assign(el.style, style);
    accents.appendChild(el);
    return el;
  }

  function scene() {
    const el = document.createElement("div");
    el.className = "lyric-scene";
    lyrics.appendChild(el);
    return el;
  }

  function exitFrames(kind, index, currentTransform) {
    const origin = currentTransform && currentTransform !== "none" ? currentTransform : "translate(0,0)";
    const base = { opacity: 1, transform: origin, filter: "blur(0)", clipPath: "inset(0 0 0 0)" };
    const exits = {
      dissolve: { opacity: 0, transform: `${origin} translateY(-12px) scale(1.04)`, filter: "blur(16px)", clipPath: "inset(0)" },
      wipe: { opacity: 0, transform: `${origin} translateX(12px)`, filter: "blur(0)", clipPath: "inset(0 0 0 100%)" },
      scatter: { opacity: 0, transform: `${origin} translate(${rand(-90, 90)}px,${rand(-70, 70)}px) rotate(${rand(-14, 14)}deg) scale(.72)`, filter: "blur(5px)", clipPath: "inset(0)" },
      lift: { opacity: 0, transform: `${origin} translateY(-80px)`, filter: "blur(4px)", clipPath: "inset(0 0 100% 0)" },
      sweep: { opacity: 0, transform: `${origin} translateX(120px) skewX(-14deg)`, filter: "blur(3px)", clipPath: "inset(0 0 0 100%)" },
      rush: { opacity: 0, transform: `${origin} translate(${index % 2 ? 180 : -180}px,${index % 2 ? -80 : 80}px) skewX(-18deg)`, filter: "blur(9px)", clipPath: "inset(0)" },
      part: { opacity: 0, transform: `${origin} translateX(${index % 2 ? 150 : -150}px) scaleX(.8)`, filter: "blur(2px)", clipPath: "inset(0)" },
      implode: { opacity: 0, transform: `${origin} scale(.05) rotate(${index % 2 ? 35 : -35}deg)`, filter: "blur(10px)", clipPath: "inset(0)" },
      drift: { opacity: 0, transform: `${origin} translate(${28 + index * 9}px,${-18 - index * 5}px) scale(1.08)`, filter: "blur(12px)", clipPath: "inset(0)" },
      cut: { opacity: 0, transform: `${origin} translateX(${index % 2 ? 95 : -95}px) scaleX(1.35)`, filter: "blur(2px) hue-rotate(90deg)", clipPath: index % 2 ? "inset(0 0 55% 0)" : "inset(55% 0 0 0)" },
      burst: { opacity: 0, transform: `${origin} scale(1.8)`, filter: "blur(14px)", clipPath: "inset(0)" }
    };
    return [base, exits[kind] || exits.dissolve];
  }

  function clearStage(fast = false) {
    state.token++;
    const nodes = [...lyrics.children];
    if (fast) {
      nodes.forEach(n => n.remove());
      [...accents.children].forEach((n, i) => { if (i) n.remove(); });
      return;
    }
    const exitKind = PRESETS[state.preset]?.exit || "dissolve";
    nodes.forEach((n, i) => {
      const a = animate(n, exitFrames(exitKind, i, getComputedStyle(n).transform), { duration: exitKind === "cut" ? 220 : 430, delay: i * 18, easing: exitKind === "cut" ? "steps(4,end)" : "cubic-bezier(.55,0,1,.45)", fill: "forwards" });
      a.finished.catch(() => {}).finally(() => n.remove());
    });
    [...accents.children].forEach((n, i) => {
      if (!i) return;
      animate(n, [{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: "forwards" }).finished.catch(() => {}).finally(() => n.remove());
    });
  }

  function layoutText(text) {
    const cut = Math.max(3, Math.min(text.length - 2, Math.round(text.length * .54)));
    const breakAt = text.lastIndexOf(" ", cut);
    const pos = breakAt > 2 ? breakAt : cut;
    return [text.slice(0, pos).trim(), text.slice(pos).trim()].filter(Boolean);
  }

  const effects = {
    soft(text) {
      state.ambientMode = "calm";
      const lines = layoutText(text);
      lines.forEach((line, i) => {
        const el = addUnit(line, i ? "gradient-text" : "");
        Object.assign(el.style, { left: i ? "17%" : "12%", top: `${34 + i * 18}%`, fontSize: i ? "6.2cqw" : "4.2cqw", fontWeight: i ? 900 : 650 });
        animate(el, [
          { opacity: 0, filter: "blur(18px)", transform: "translateY(22px) scale(1.06)", letterSpacing: ".12em" },
          { opacity: 1, filter: "blur(0)", transform: "translateY(0) scale(1)", letterSpacing: ".015em" }
        ], { duration: 1150, delay: tempo(i * 220), easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
      const line = accent("accent-line", { left: "12%", top: "66%", width: "23%" });
      animate(line, [{ transform: "scaleX(0)", opacity: 0 }, { transform: "scaleX(1)", opacity: .85 }], { duration: 900, delay: tempo(420), easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
    },

    editorial(text) {
      state.ambientMode = "grid";
      const lines = layoutText(text);
      const big = addUnit(lines[0]);
      Object.assign(big.style, { left: "9%", top: "19%", fontSize: "7.2cqw", maxWidth: "80%" });
      animate(big, [
        { opacity: 0, clipPath: "inset(0 100% 0 0)", transform: "translateX(-24px)" },
        { opacity: 1, clipPath: "inset(0 0 0 0)", transform: "translateX(0)" }
      ], { duration: 760, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      if (lines[1]) {
        const small = addUnit(lines[1], "outline");
        Object.assign(small.style, { right: "9%", top: "56%", fontSize: "5.1cqw", textAlign: "right" });
        animate(small, [{ opacity: 0, transform: "translateX(35px)" }, { opacity: 1, transform: "translateX(0)" }], { duration: 720, delay: tempo(250), easing: "ease-out", fill: "both" });
      }
      const box = accent("accent-box", { left: "6%", top: "13%", width: "58%", height: "35%" });
      animate(box, [{ opacity: 0, transform: "scale(.92)", clipPath: "inset(0 100% 100% 0)" }, { opacity: 1, transform: "scale(1)", clipPath: "inset(0 0 0 0)" }], { duration: 900, easing: "ease-out", fill: "both" });
      const index = accent("lyric-unit", { right: "7%", top: "10%", fontSize: "1.5cqw", opacity: ".55", letterSpacing: ".22em" });
      index.textContent = `LYRIC / ${String(state.cursor + 1).padStart(2, "0")}`;
    },

    type(text) {
      state.ambientMode = "calm";
      const el = addUnit(text);
      Object.assign(el.style, { left: "50%", top: "48%", fontSize: "5.3cqw", transform: "translate(-50%,-50%)" });
      const chars = splitChars(el, text);
      chars.forEach((ch, i) => animate(ch, [
        { opacity: 0, transform: `translate(${rand(-9, 9)}px, 28px) rotate(${rand(-7, 7)}deg) scale(.65)`, filter: "blur(5px)" },
        { opacity: 1, transform: "translate(0,0) rotate(0) scale(1.08)", filter: "blur(0)", offset: .72 },
        { opacity: 1, transform: "translate(0,0) rotate(0) scale(1)", filter: "blur(0)" }
      ], { duration: 520, delay: tempo(i * 42), easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" }));
      const line = accent("accent-line", { left: "19%", right: "19%", top: "62%", height: "1px" });
      animate(line, [{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: 820, delay: tempo(chars.length * 26), easing: "ease-out", fill: "both" });
    },

    vertical(text) {
      state.ambientMode = "calm";
      const lines = layoutText(text);
      lines.forEach((line, i) => {
        const el = addUnit(line, `vertical ${i ? "outline" : ""}`);
        Object.assign(el.style, { right: `${24 + i * 20}%`, top: i ? "13%" : "22%", fontSize: i ? "4.4cqw" : "5.6cqw", letterSpacing: ".12em" });
        animate(el, [
          { opacity: 0, clipPath: "inset(0 0 100% 0)", transform: "translateY(-30px)" },
          { opacity: 1, clipPath: "inset(0 0 0 0)", transform: "translateY(0)" }
        ], { duration: 980, delay: tempo(i * 260), easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
      });
      for (let i = 0; i < 3; i++) {
        const ring = accent("accent-ring", { left: `${9 + i * 7}%`, bottom: `${12 + i * 5}%`, width: `${9 + i * 4}%`, aspectRatio: "1" });
        animate(ring, [{ opacity: 0, transform: "scale(.25)" }, { opacity: .6 - i * .12, transform: "scale(1)" }], { duration: 1050, delay: tempo(i * 140), easing: "ease-out", fill: "both" });
      }
    },

    ribbon(text) {
      state.ambientMode = "grid";
      const band = accent("accent-box", { left: "-4%", top: "39%", width: "108%", height: "23%", background: "linear-gradient(90deg, rgba(99,73,255,.74), rgba(21,202,184,.68))", border: "none", transform: "skewY(-3deg)" });
      animate(band, [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0 0 0)" }], { duration: 700, easing: "cubic-bezier(.5,0,.15,1)", fill: "both" });
      const el = addUnit(text);
      Object.assign(el.style, { left: "50%", top: "49%", fontSize: "5.2cqw", transform: "translate(-50%,-50%) rotate(-3deg)", zIndex: 2 });
      animate(el, [
        { opacity: 0, clipPath: "inset(0 0 100% 0)", transform: "translate(-50%,-35%) rotate(-3deg)" },
        { opacity: 1, clipPath: "inset(0 0 0 0)", transform: "translate(-50%,-50%) rotate(-3deg)" }
      ], { duration: 600, delay: tempo(210), easing: "ease-out", fill: "both" });
    },

    diagonal(text) {
      state.ambientMode = "rush";
      const el = addUnit(text, "gradient-text");
      Object.assign(el.style, { left: "50%", top: "48%", fontSize: "5.8cqw", transform: "translate(-50%,-50%) skewX(-9deg) rotate(-5deg)" });
      const words = splitWords(el, text);
      words.forEach((word, i) => animate(word, [
        { opacity: 0, transform: `translate(${i % 2 ? 110 : -110}px, ${i % 2 ? -50 : 50}px) scaleX(1.5)`, filter: "blur(7px)" },
        { opacity: 1, transform: "translate(0,0) scaleX(1)", filter: "blur(0)" }
      ], { duration: 620, delay: tempo(i * 85), easing: "cubic-bezier(.1,.8,.2,1)", fill: "both" }));
      for (let i = 0; i < 4; i++) {
        const line = accent("accent-line", { left: `${-4 + i * 20}%`, top: `${76 - i * 17}%`, width: "34%", transform: "rotate(-24deg)", opacity: ".3" });
        animate(line, [{ transform: "translateX(-140%) rotate(-24deg)" }, { transform: "translateX(420%) rotate(-24deg)" }], { duration: 1100, delay: tempo(i * 80), easing: "cubic-bezier(.2,.7,.2,1)", fill: "both" });
      }
    },

    split(text) {
      state.ambientMode = "grid";
      const lines = layoutText(text);
      const leftPanel = accent("accent-box", { left: 0, top: 0, width: "50%", height: "100%", border: "none", background: "rgba(119,91,255,.28)" });
      const rightPanel = accent("accent-box", { right: 0, top: 0, width: "50%", height: "100%", border: "none", background: "rgba(18,220,194,.12)" });
      animate(leftPanel, [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0)" }], { duration: 560, easing: "ease-out", fill: "both" });
      animate(rightPanel, [{ clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0)" }], { duration: 560, easing: "ease-out", fill: "both" });
      lines.forEach((line, i) => {
        const el = addUnit(line, i ? "outline" : "");
        Object.assign(el.style, { left: i ? "54%" : "46%", top: i ? "54%" : "34%", fontSize: i ? "4.8cqw" : "6.4cqw", transform: i ? "none" : "translateX(-100%)", textAlign: i ? "left" : "right" });
        animate(el, [
          { opacity: 0, transform: `${i ? "translateX(80px)" : "translateX(calc(-100% - 80px))"}` },
          { opacity: 1, transform: i ? "translateX(0)" : "translateX(-100%)" }
        ], { duration: 700, delay: tempo(i * 180), easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
    },

    orbit(text) {
      state.ambientMode = "orbit";
      const lines = layoutText(text);
      const core = addUnit(lines[0], "gradient-text");
      Object.assign(core.style, { left: "50%", top: "50%", fontSize: "6.5cqw", transform: "translate(-50%,-50%)" });
      animate(core, [{ opacity: 0, transform: "translate(-50%,-50%) scale(.3) rotate(-8deg)" }, { opacity: 1, transform: "translate(-50%,-50%) scale(1) rotate(0)" }], { duration: 820, easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      const orbitText = lines[1] || lines[0];
      Array.from(orbitText).slice(0, 12).forEach((char, i, arr) => {
        const el = addUnit(char, "outline");
        Object.assign(el.style, { left: "50%", top: "50%", fontSize: "2.5cqw" });
        const angle = i / arr.length * Math.PI * 2;
        const x = Math.cos(angle) * 28;
        const y = Math.sin(angle) * 25;
        animate(el, [
          { opacity: 0, transform: `translate(-50%,-50%) translate(${x * .2}cqw,${y * .2}cqh) rotate(${angle}rad)` },
          { opacity: .9, transform: `translate(-50%,-50%) translate(${x}cqw,${y}cqh) rotate(${angle + Math.PI / 2}rad)` }
        ], { duration: 920, delay: tempo(i * 45), easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
      });
      const ring = accent("accent-ring", { left: "18%", top: "16%", width: "64%", aspectRatio: "1.78", borderRadius: "50%" });
      animate(ring, [{ opacity: 0, transform: "scale(.4) rotate(-20deg)" }, { opacity: .7, transform: "scale(1) rotate(0)" }], { duration: 1000, easing: "ease-out", fill: "both" });
    },

    echo(text) {
      state.ambientMode = "calm";
      const positions = [
        { x: 50, y: 48, s: 6.1, o: 1, z: 4 },
        { x: 48, y: 45, s: 6.1, o: .3, z: 3 },
        { x: 46, y: 42, s: 6.1, o: .16, z: 2 },
        { x: 44, y: 39, s: 6.1, o: .08, z: 1 }
      ];
      positions.reverse().forEach((p, i) => {
        const el = addUnit(text, i === positions.length - 1 ? "gradient-text" : "outline");
        Object.assign(el.style, { left: `${p.x}%`, top: `${p.y}%`, fontSize: `${p.s}cqw`, opacity: p.o, zIndex: p.z, transform: "translate(-50%,-50%)", mixBlendMode: i < 3 ? "screen" : "normal" });
        animate(el, [
          { opacity: 0, transform: `translate(-50%,-50%) translate(${rand(-120, 120)}px,${rand(-60, 60)}px) scale(1.18)`, filter: "blur(12px)" },
          { opacity: p.o, transform: "translate(-50%,-50%) translate(0,0) scale(1)", filter: "blur(0)" }
        ], { duration: 950, delay: tempo(i * 105), easing: "cubic-bezier(.16,1,.3,1)", fill: "both" });
      });
    },

    karaoke(text) {
      state.ambientMode = "calm";
      const base = addUnit(text, "outline");
      const fill = addUnit(text, "gradient-text");
      [base, fill].forEach(el => Object.assign(el.style, { left: "50%", top: "47%", fontSize: "5.8cqw", transform: "translate(-50%,-50%)" }));
      base.style.opacity = ".42";
      animate(base, [{ opacity: 0, transform: "translate(-50%,-50%) translateY(20px)" }, { opacity: .42, transform: "translate(-50%,-50%) translateY(0)" }], { duration: 500, fill: "both" });
      animate(fill, [
        { clipPath: "inset(0 100% 0 0)", backgroundPosition: "100% 0" },
        { clipPath: "inset(0 0 0 0)", backgroundPosition: "0% 0" }
      ], { duration: 2100, delay: tempo(220), easing: "linear", fill: "both" });
      const scan = accent("scanline", { top: "34%", left: "8%", width: "1px", height: "30%" });
      animate(scan, [{ opacity: 0, transform: "translateX(0)" }, { opacity: .85, offset: .08 }, { opacity: .85, offset: .9 }, { opacity: 0, transform: "translateX(84cqw)" }], { duration: 2100, delay: tempo(220), easing: "linear", fill: "both" });
    },

    glitch(text) {
      state.ambientMode = "rush";
      const base = addUnit(text);
      Object.assign(base.style, { left: "50%", top: "48%", fontSize: "6.2cqw", transform: "translate(-50%,-50%)", zIndex: 3 });
      const colors = ["#59f8e5", "#ff4e8b"];
      colors.forEach((color, i) => {
        const clone = addUnit(text);
        Object.assign(clone.style, { left: "50%", top: "48%", fontSize: "6.2cqw", transform: "translate(-50%,-50%)", color, mixBlendMode: "screen", opacity: .66, zIndex: 2, clipPath: i ? "inset(16% 0 47% 0)" : "inset(55% 0 12% 0)" });
        animate(clone, [
          { opacity: 0, transform: `translate(-50%,-50%) translateX(${i ? 90 : -90}px) skewX(${i ? 18 : -18}deg)` },
          { opacity: .8, transform: `translate(-50%,-50%) translateX(${i ? -9 : 9}px)`, offset: .45 },
          { opacity: .2, transform: "translate(-50%,-50%) translateX(0)" }
        ], { duration: 620, delay: tempo(i * 40), easing: "steps(5,end)", fill: "both" });
      });
      animate(base, [
        { opacity: 0, filter: "blur(10px)", transform: "translate(-50%,-50%) scaleX(1.6)" },
        { opacity: 1, filter: "blur(0)", transform: "translate(-50%,-50%) scaleX(.94)", offset: .55 },
        { opacity: 1, transform: "translate(-50%,-50%) scaleX(1)" }
      ], { duration: 680, easing: "steps(6,end)", fill: "both" });
      const flash = accent("screen-flash");
      animate(flash, [{ opacity: 0 }, { opacity: .7, offset: .2 }, { opacity: 0 }], { duration: 220, fill: "both" });
    },

    impact(text) {
      state.ambientMode = "impact";
      burstParticles(36);
      const lines = layoutText(text);
      lines.forEach((line, i) => {
        const el = addUnit(line, i ? "gradient-text" : "");
        Object.assign(el.style, { left: "50%", top: `${39 + i * 18}%`, fontSize: i ? "6.6cqw" : "8.2cqw", transform: "translate(-50%,-50%)", zIndex: 3 });
        animate(el, [
          { opacity: 0, transform: "translate(-50%,-50%) scale(2.6)", filter: "blur(18px)", letterSpacing: "-.08em" },
          { opacity: 1, transform: "translate(-50%,-50%) scale(.88)", filter: "blur(0)", letterSpacing: ".02em", offset: .68 },
          { opacity: 1, transform: "translate(-50%,-50%) scale(1)", letterSpacing: ".015em" }
        ], { duration: 720, delay: tempo(i * 130), easing: "cubic-bezier(.12,.75,.2,1)", fill: "both" });
      });
      for (let i = 0; i < 3; i++) {
        const ring = accent("accent-ring", { left: "50%", top: "50%", width: "8%", aspectRatio: "1", transform: "translate(-50%,-50%)", borderWidth: `${3 - i}px` });
        animate(ring, [{ opacity: .9, transform: "translate(-50%,-50%) scale(.2)" }, { opacity: 0, transform: `translate(-50%,-50%) scale(${8 + i * 3})` }], { duration: 850, delay: tempo(i * 100), easing: "ease-out", fill: "both" });
      }
      const flash = accent("screen-flash");
      animate(flash, [{ opacity: 0 }, { opacity: .75, offset: .18 }, { opacity: 0 }], { duration: 280, fill: "both" });
    }
  };

  function choosePreset() {
    if (state.mode === "manual") return state.preset;
    if (state.mode === "random") {
      let next = Math.floor(rand(0, PRESETS.length));
      if (next === state.preset) next = (next + 1) % PRESETS.length;
      return next;
    }
    if (state.mode === "arc") {
      const arc = [0, 1, 2, 3, 4, 5, 9, 8, 10, 11];
      return arc[state.cursor % arc.length];
    }
    const alternate = [0, 10, 1, 6, 3, 11, 4, 8, 2, 9];
    return alternate[state.cursor % alternate.length];
  }

  function play(text, advance = true) {
    const value = (text || $("lyricText").value).trim() || SAMPLE_LINES[0];
    clearStage(false);
    const index = choosePreset();
    state.preset = index;
    renderPresetList();
    const preset = PRESETS[index];
    $("nowName").textContent = preset.name;
    $("hudTech").textContent = preset.tech;
    window.setTimeout(() => effects[preset.id](value), 130);
    if (advance) state.cursor++;
  }

  function next() {
    if (state.mode === "manual") state.preset = (state.preset + 1) % PRESETS.length;
    $("lyricText").value = SAMPLE_LINES[state.phrase++ % SAMPLE_LINES.length];
    play();
  }

  function setMode(mode) {
    state.mode = mode;
    state.cursor = 0;
    resetRng();
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
    const names = { manual: "手動", random: "ランダム", arc: "静 → サビ", alternate: "緩急" };
    $("hudMode").textContent = names[mode];
  }

  function renderPresetList() {
    const root = $("presetList");
    root.textContent = "";
    PRESETS.forEach((p, i) => {
      const button = document.createElement("button");
      button.className = `preset${state.preset === i ? " active" : ""}`;
      button.innerHTML = `<span class="preset-icon">${p.icon}</span><span><span class="preset-name">${p.name}</span><span class="preset-desc">${p.desc}</span></span><span class="energy"><i style="width:${p.energy * 25}%"></i></span>`;
      button.addEventListener("click", () => {
        state.preset = i;
        setMode("manual");
        renderPresetList();
        play($("lyricText").value, false);
      });
      root.appendChild(button);
    });
    $("presetCount").textContent = `${PRESETS.length} patterns`;
  }

  function setBackground(name) {
    const backgrounds = {
      aurora: "radial-gradient(circle at 70% 22%, rgba(110,86,255,.34), transparent 33%), radial-gradient(circle at 22% 80%, rgba(0,229,204,.14), transparent 36%), linear-gradient(145deg,#090a15,#111426 52%,#070810)",
      midnight: "radial-gradient(circle at 50% 120%, rgba(51,91,180,.32), transparent 44%), linear-gradient(160deg,#03050c,#0d1529 62%,#020308)",
      warm: "radial-gradient(circle at 28% 70%, rgba(255,132,85,.31), transparent 38%), radial-gradient(circle at 77% 27%, rgba(172,77,208,.25), transparent 32%), linear-gradient(150deg,#160c18,#251229 55%,#090811)",
      transparent: "repeating-conic-gradient(#252735 0 25%,#171925 0 50%) 50% / 24px 24px"
    };
    stage.style.background = backgrounds[name] || backgrounds.aurora;
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function burstParticles(count) {
    const rect = canvas.getBoundingClientRect();
    for (let i = 0; i < count; i++) {
      const angle = rand(0, Math.PI * 2);
      const speed = rand(45, 210);
      state.particles.push({ x: rect.width / 2, y: rect.height / 2, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, life: rand(.55, 1.15), age: 0, size: rand(1, 4), hue: rand(155, 275) });
    }
  }

  function ambientFrame(time) {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    const seconds = time / 1000;
    ctx.save();
    if (state.ambientMode === "grid") {
      ctx.strokeStyle = "rgba(160,170,255,.075)";
      ctx.lineWidth = 1;
      const gap = Math.max(35, rect.width / 16);
      for (let x = (seconds * 8) % gap; x < rect.width; x += gap) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke(); }
      for (let y = 0; y < rect.height; y += gap) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke(); }
    } else if (state.ambientMode === "rush") {
      ctx.strokeStyle = "rgba(120,235,220,.13)";
      for (let i = 0; i < 13; i++) {
        const x = ((i * 137 + seconds * 330) % (rect.width + 300)) - 150;
        ctx.beginPath(); ctx.moveTo(x, rect.height); ctx.lineTo(x + 260, 0); ctx.stroke();
      }
    } else if (state.ambientMode === "orbit") {
      ctx.strokeStyle = "rgba(144,125,255,.11)";
      for (let i = 0; i < 3; i++) {
        ctx.beginPath();
        ctx.ellipse(rect.width / 2, rect.height / 2, rect.width * (.21 + i * .08), rect.height * (.18 + i * .06), seconds * .04, 0, Math.PI * 2);
        ctx.stroke();
      }
    } else {
      const g = ctx.createRadialGradient(rect.width * (.5 + Math.sin(seconds * .25) * .08), rect.height * .5, 0, rect.width * .5, rect.height * .5, rect.width * .42);
      g.addColorStop(0, "rgba(134,111,255,.07)"); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, rect.width, rect.height);
    }
    ctx.restore();

    const dt = 1 / 60;
    state.particles = state.particles.filter(p => {
      p.age += dt;
      if (p.age >= p.life) return false;
      p.x += p.vx * dt; p.y += p.vy * dt; p.vx *= .985; p.vy *= .985;
      const alpha = 1 - p.age / p.life;
      ctx.fillStyle = `hsla(${p.hue},90%,70%,${alpha})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.size * alpha, 0, Math.PI * 2); ctx.fill();
      return true;
    });
    state.raf = requestAnimationFrame(ambientFrame);
  }

  function updateAutoplay() {
    clearInterval(state.timer);
    state.timer = 0;
    if (!$("autoplay").checked) return;
    state.timer = window.setInterval(next, Number($("interval").value));
  }

  document.querySelectorAll(".mode-btn").forEach(button => button.addEventListener("click", () => {
    setMode(button.dataset.mode);
    next();
  }));
  $("playBtn").addEventListener("click", () => play());
  $("nextBtn").addEventListener("click", next);
  $("clearBtn").addEventListener("click", () => clearStage(false));
  $("lyricText").addEventListener("keydown", e => { if (e.key === "Enter") play(); });
  $("seed").addEventListener("change", () => { state.cursor = 0; resetRng(); });
  $("background").addEventListener("change", e => setBackground(e.target.value));
  $("autoplay").addEventListener("change", updateAutoplay);
  $("interval").addEventListener("change", updateAutoplay);
  $("showSafe").addEventListener("change", e => stage.classList.toggle("show-safe", e.target.checked));
  window.addEventListener("resize", resizeCanvas);
  window.addEventListener("beforeunload", () => { cancelAnimationFrame(state.raf); clearInterval(state.timer); });

  resetRng();
  renderPresetList();
  resizeCanvas();
  state.raf = requestAnimationFrame(ambientFrame);
  window.setTimeout(() => play($("lyricText").value, false), 200);
})();
