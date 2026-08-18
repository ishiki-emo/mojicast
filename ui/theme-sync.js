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
    });
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
