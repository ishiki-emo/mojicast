// Loaded before page scripts. Credentials never appear in an HTTP page URL or HTML response.
(() => {
  const key = "mojicast.ui.session";
  const fragment = new URLSearchParams(location.hash.slice(1));
  const incoming = fragment.get("mojicast_session");
  if (incoming) {
    sessionStorage.setItem(key, incoming);
    history.replaceState(null, "", location.pathname + location.search);
  }
  const token = () => sessionStorage.getItem(key) || "";
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/"))
      return originalFetch(input, init);
    const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
    if (token()) headers.set("Authorization", "Bearer " + token());
    return originalFetch(input, {...init, headers, redirect: "error"});
  };
  const NativeEventSource = window.EventSource;
  window.EventSource = class extends NativeEventSource {
    constructor(input, options) {
      const url = new URL(input, location.href);
      if (url.origin === location.origin && url.pathname === "/events" && token())
        url.searchParams.set("ui_session", token());
      super(url.href, options);
    }
  };
})();
