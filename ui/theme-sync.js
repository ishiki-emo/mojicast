(function () {
  "use strict";

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

  function connect() {
    const events = new EventSource("/events");
    events.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "theme") applyTheme(message.theme);
      } catch (e) {}
    };
    events.onerror = () => {
      events.close();
      window.setTimeout(connect, 1000);
    };
  }

  window.MojicastThemeSync = { applyTheme };
  connect();
})();
