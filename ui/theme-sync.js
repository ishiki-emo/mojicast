(function () {
  "use strict";
  const embedded = new URLSearchParams(location.search).get("embed") === "1";

  function applyTheme(theme) {
    const light = theme === "light";
    if (light) document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("mojicast-theme", light ? "light" : "dark"); } catch (e) {}

    // アプリ設定を開いたままコックピット側で切り替えた場合も、選択値を合わせる。
    const select = document.getElementById("theme");
    if (select) select.value = light ? "light" : "dark";
    window.dispatchEvent(new CustomEvent("mojicast-theme-changed", {
      detail: { theme: light ? "light" : "dark" }
    }));
  }

  // UI言語（i18n.js があれば委譲）。theme と同じ経路で適用・中継する。
  function applyLang(lang) {
    if (lang && window.MojicastI18n) window.MojicastI18n.applyLang(lang);
  }

  // GUI窓の拡大率。URLの ?s= は窓を開いた時点の値なので、起動後に設定を
  // 変えた場合はここで上書きする（窓自体のピクセルサイズは次回起動から）。
  function applyScale(scale) {
    // 埋め込みiframeは親スタジオのzoomで一緒に拡縮される（二重適用の防止）
    if (embedded) return;
    const s = parseFloat(scale);
    if (!(s > 0)) return;
    // 等倍は zoom を外す。拡大／縮小からの戻りで指定が残らないようにする。
    document.documentElement.style.zoom = s === 1 ? "" : s;
    // 100vh は zoom に連動しないため、高さ補正用の変数も必ず揃えて更新する。
    // ここを忘れると calc(100vh / var(--ui-zoom)) が古い倍率のまま残り、
    // フッターの下に余白が出る（cockpit の applyScale と同じ処置）
    if (s === 1) document.documentElement.style.removeProperty("--ui-zoom");
    else document.documentElement.style.setProperty("--ui-zoom", s);
    // 埋め込み iframe（スタジオの設定画面）に倍率変更を知らせ、高さ補正を再計算させる
    document.querySelectorAll("iframe").forEach(frame => {
      try {
        if (frame.contentWindow)
          frame.contentWindow.postMessage({ mojiScaleChanged: true }, location.origin);
      } catch (e) {}
    });
  }

  // 埋め込み iframe の高さ補正（Mac 対策）。
  // WebKit（Mac の WKWebView）は、zoom を掛けた親ページの iframe の中で
  // 100vh が「iframe の高さ × 親の倍率」になる（innerHeight だけが正しい。倍率を
  // 動的に変えた後は clientHeight まで別の値になる）。放置すると倍率>1 で本文が
  // iframe より背が高くなり、フッターの保存ボタンが画面外へ出る（倍率<1 では下に余白）。
  // Chromium（WebView2）は 100vh = innerHeight なので何も起きない。
  // 100vh を実測して innerHeight との比を --ui-zoom に立て、各ページの
  // calc(100vh / var(--ui-zoom)) で打ち消す（＝本文の高さが常に innerHeight になる）。
  // エンジン判別はしない（計測した比がそのまま補正量になる）。
  function measureVh() {
    const probe = document.createElement("div");
    probe.style.cssText = "position:absolute;top:0;left:0;width:0;height:100vh;" +
                          "visibility:hidden;pointer-events:none";
    document.documentElement.appendChild(probe);
    const h = probe.getBoundingClientRect().height;
    probe.remove();
    return h;
  }
  function fixEmbeddedViewport() {
    if (!embedded || !document.documentElement) return;
    const inner = window.innerHeight;
    if (!(inner > 0)) return;
    const ratio = measureVh() / inner;
    if (ratio > 0 && Math.abs(ratio - 1) > 0.01)
      document.documentElement.style.setProperty("--ui-zoom", ratio.toFixed(4));
    else document.documentElement.style.removeProperty("--ui-zoom");
  }
  // 倍率変更やリサイズの直後はレイアウトが追いついていないことがあるので、
  // その場・次フレーム・少し後の3回計測する（冪等なので重複しても害はない）
  function refixEmbeddedViewport() {
    fixEmbeddedViewport();
    window.requestAnimationFrame(fixEmbeddedViewport);
    window.setTimeout(fixEmbeddedViewport, 150);
  }

  function connect() {
    const events = new EventSource("/events");
    events.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if ((message.type === "theme" || message.type === "init") && message.theme)
          applyTheme(message.theme);
        if ((message.type === "ui_lang" || message.type === "init") && message.ui_lang)
          applyLang(message.ui_lang);
        if ((message.type === "ui_scale" || message.type === "init") && message.ui_scale)
          applyScale(message.ui_scale);
      } catch (e) {}
    };
    events.onerror = () => {
      events.close();
      window.setTimeout(connect, 1000);
    };
  }

  // iframe内の設定画面は親スタジオがテーマを中継する。各iframeがSSEを
  // 常時1本ずつ占有すると、設定窓を併用した際に通常APIが待たされるため。
  if (embedded) {
    window.addEventListener("message", event => {
      const message = event.data || {};
      if (message.mojiTheme) applyTheme(message.mojiTheme);
      if (message.mojiLang) applyLang(message.mojiLang);
      if (message.mojiScaleChanged) refixEmbeddedViewport();
    });
    refixEmbeddedViewport();
    window.addEventListener("load", fixEmbeddedViewport);
    window.addEventListener("resize", refixEmbeddedViewport);
  }

  window.MojicastThemeSync = { applyTheme };
  // SSE接続前にも保存値を取得する。別WebViewでlocalStorageの反映が遅い場合や、
  // 設定窓をテーマ変更直後に開いた場合の初期色ずれを防ぐ。
  fetch("/api/config", { cache: "no-store" })
    .then(response => response.json())
    .then(config => {
      applyTheme(config.theme || "light");
      applyLang(config.ui_lang || "ja");
      // URLの ?s= は窓を開いた時点の値。起動後に倍率を変えていた場合はここで揃う。
      applyScale(config.ui_scale_resolved);
    })
    .catch(() => {})
    .finally(() => { if (!embedded) connect(); });
})();
