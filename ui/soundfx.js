/*
 * soundfx.js — 音イベント演出の描画（overlay.html / 設定UIのプレビューで共用）
 *
 * 笑い・拍手などの検出イベント（SSE type:"sound"）を受けて、画面の指定位置に
 * 画像・パーティクルを出す。字幕描画（fx.js の renderWords / burstLine）とは
 * 独立した系統で、fx.js からは公開API（spawnParticles）とキーフレームCSS
 * （fx-* クラス）だけを借りる。逆依存は作らない。
 *
 * 提供するもの:
 *   SFX.ENTER_ANIMS / SFX.LOOP_ANIMS   設定UIの選択肢（値, 表示名）
 *   SFX.setRules(rules)                グループ名→ルール を差し替え
 *   SFX.play(group, score, opts)       演出を1回出す（多重発火・レート制御込み）
 */
(function () {
  const SFX = {};

  // 登場アニメ（LINE_ANIMS の語彙を踏襲。実装はこのファイルのCSS）
  SFX.ENTER_ANIMS = [
    ["pop", "ポップ（弾ける）"], ["fade", "フェード"],
    ["drop", "ドロップ（上から）"], ["slide", "スライド（下から）"],
    ["none", "なし"],
  ];
  // 表示中アニメ。fx.js の WORD_ANIMS のうち transform/filter 系のみ流用
  // （rainbow/shine/wave はグラデ文字・文字分解が前提のテキスト専用なので除外）
  SFX.LOOP_ANIMS = [
    ["none", "なし"], ["heartbeat", "ドキドキ（鼓動）"],
    ["shake", "ぷるぷる"], ["float", "ふわふわ（浮遊）"],
    ["glowpulse", "グロー点滅"], ["bounce", "バウンス"],
    ["pop", "ポップ（弾ける）"], ["spin", "スピン"],
    ["flash", "フラッシュ"], ["glitch", "グリッチ（バグ風）"],
  ];

  // スコア→強弱の正規化範囲。実測の分布（発火スコア 0.3〜1.5 前後）に合わせる
  const SCORE_LO = 0.3, SCORE_HI = 1.5;
  // レート上限: 同グループ 10 秒に 3 回まで（連続爆笑で画面が埋まる事故防止）
  const RATE_WINDOW_MS = 10000, RATE_MAX = 3;

  let rules = {};
  const active = new Map();     // group → {el, hideTimer, killTimer}
  const fireLog = new Map();    // group → [発火時刻ms, ...]
  const preloaded = new Map();  // 画像名 → Image（発火時のロード待ちを無くす）

  SFX.setRules = function (r) {
    rules = r || {};
    // ルールで使う画像を先読みしておく。発火してからロードを始めると、
    // 特に複数画像のランダム表示で「出るまで一拍遅れる」のが見えてしまう
    for (const rule of Object.values(rules)) {
      for (const v of rule.variants || []) {
        if (!preloaded.has(v.image)) {
          const im = new Image();
          im.src = "/soundfx/" + encodeURIComponent(v.image);
          preloaded.set(v.image, im);
        }
      }
    }
  };

  // ---------------- レイヤー・CSS ----------------

  let layer = null;
  function getLayer() {
    if (!layer || !layer.isConnected) {
      layer = document.createElement("div");
      layer.id = "sfx-layer";
      // 字幕より前・パーティクル(9999)より後ろ
      layer.style.cssText =
        "position:fixed;inset:0;pointer-events:none;z-index:9000;overflow:hidden;";
      document.body.appendChild(layer);
    }
    return layer;
  }

  const CSS = `
  .sfx-item { position: fixed; transform: translate(-50%,-50%); will-change: transform, opacity; }
  .sfx-item img { width: 100%; height: auto; display: block; }
  .sfx-enter-pop   { animation: sfxpop .45s cubic-bezier(.2,1.6,.4,1) both; }
  .sfx-enter-fade  { animation: sfxfade .4s ease-out both; }
  .sfx-enter-drop  { animation: sfxdrop .5s cubic-bezier(.3,1.4,.5,1) both; }
  .sfx-enter-slide { animation: sfxslide .45s ease-out both; }
  .sfx-leave       { animation: sfxfadeout .25s ease-in both; }
  @keyframes sfxpop   { from { transform:translate(-50%,-50%) scale(.2); opacity:0; }
                        to   { transform:translate(-50%,-50%) scale(1);  opacity:1; } }
  @keyframes sfxfade  { from { opacity:0; } to { opacity:1; } }
  @keyframes sfxdrop  { from { transform:translate(-50%,-90%) scale(.9); opacity:0; }
                        to   { transform:translate(-50%,-50%) scale(1);  opacity:1; } }
  @keyframes sfxslide { from { transform:translate(-50%,-10%); opacity:0; }
                        to   { transform:translate(-50%,-50%); opacity:1; } }
  @keyframes sfxfadeout { from { opacity:1; } to { opacity:0; } }`;
  if (typeof document !== "undefined") {
    const st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ---------------- 発火制御 ----------------

  function rateLimited(group) {
    const now = Date.now();
    const log = (fireLog.get(group) || []).filter(t => now - t < RATE_WINDOW_MS);
    if (log.length >= RATE_MAX) { fireLog.set(group, log); return true; }
    log.push(now);
    fireLog.set(group, log);
    return false;
  }

  function scoreK(score) {
    const k = ((+score || 0) - SCORE_LO) / (SCORE_HI - SCORE_LO);
    return Math.min(1, Math.max(0, k));
  }

  // ---------------- 本体 ----------------

  /** 演出を出す。表示中の同グループ再発火は「延長」（リスタートで点滅させない） */
  SFX.play = function (group, score, opts) {
    const rule = rules[group];
    if (!rule || !rule.on) return;
    if (rateLimited(group)) return;

    const cur = active.get(group);
    if (cur) {                     // 表示中 → 表示時間を延長するだけ
      clearTimeout(cur.hideTimer);
      cur.hideTimer = setTimeout(() => hide(group), rule.duration || 1500);
      return;
    }

    const k = rule.scale_by_score !== false ? scoreK(score) : 0;
    const el = document.createElement("div");
    el.className = "sfx-item";
    if (rule.enter && rule.enter !== "none")
      el.classList.add("sfx-enter-" + rule.enter);

    // ランダム表示はバリアント（画像・位置・大きさのセット）単位。
    // バリアント無し（パーティクルのみ）はルール既定の pos/size を使う
    const variants = rule.variants || [];
    const v = variants.length
      ? variants[Math.floor(Math.random() * variants.length)] : null;
    const pos = v?.pos ?? rule.pos;
    const size = v?.size ?? rule.size;

    // 位置: 画面比率＋ジッター。サイズ: 画面幅比×スコア強化(〜1.3倍)
    const jx = (Math.random() - 0.5) * 2 * (rule.jitter || 0);
    const jy = (Math.random() - 0.5) * 2 * (rule.jitter || 0);
    const x = Math.min(1, Math.max(0, (pos?.x ?? 0.5) + jx));
    const y = Math.min(1, Math.max(0, (pos?.y ?? 0.3) + jy));
    const wpx = innerWidth * (size || 0.2) * (1 + 0.3 * k);
    el.style.left = (x * 100) + "%";
    el.style.top = (y * 100) + "%";
    el.style.width = wpx + "px";
    // パーティクルの粒サイズの基準（spawnParticles が fontSize を見る）
    el.style.fontSize = Math.max(24, wpx * 0.22) + "px";

    if (v) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = "/soundfx/" + encodeURIComponent(v.image);
      if (rule.anim && rule.anim !== "none") {
        // 表示中アニメは登場アニメが終わってから（transformの競合を避ける）
        const start = () => img.classList.add("fx", "fx-" + rule.anim);
        el.classList.contains("sfx-item") && rule.enter !== "none"
          ? setTimeout(start, 500) : start();
      }
      // 回転は専用ラッパに持たせる。外側(el)は登場アニメ・内側(img)は
      // 表示中アニメが transform を使うため、そのどちらにも書けない
      if (v.rot) {
        const rotor = document.createElement("div");
        rotor.style.transform = `rotate(${v.rot}deg)`;
        rotor.appendChild(img);
        el.appendChild(rotor);
      } else {
        el.appendChild(img);
      }
    } else {
      // 画像なし（パーティクルだけ）でも発射位置は寸法どおり確保する
      el.style.height = wpx * 0.6 + "px";
    }
    getLayer().appendChild(el);

    if (rule.particle && rule.particle !== "none" && window.FX) {
      FX.spawnParticles(el, rule.particle, null);
      if (k > 0.6) setTimeout(() => {       // 大きな笑いは2連発で盛る
        if (el.isConnected) FX.spawnParticles(el, rule.particle, null);
      }, 180);
    }

    const entry = {
      el,
      hideTimer: setTimeout(() => hide(group), rule.duration || 1500),
      killTimer: setTimeout(() => kill(group), 30000),  // 万一の消し忘れ保険
    };
    active.set(group, entry);
  };

  function hide(group) {
    const cur = active.get(group);
    if (!cur) return;
    cur.el.classList.remove("sfx-enter-pop", "sfx-enter-fade",
                            "sfx-enter-drop", "sfx-enter-slide");
    cur.el.classList.add("sfx-leave");
    setTimeout(() => kill(group), 260);
  }

  function kill(group) {
    const cur = active.get(group);
    if (!cur) return;
    clearTimeout(cur.hideTimer);
    clearTimeout(cur.killTimer);
    cur.el.remove();
    active.delete(group);
  }

  window.SFX = SFX;
})();
