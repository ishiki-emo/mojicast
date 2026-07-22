/* Development-only server: feeds sample final captions to the real overlay. */
const fs = require("fs");
const http = require("http");
const path = require("path");

const root = path.resolve(__dirname, "..");
const port = Number(process.argv[2] || 8766);
const style = {
  font: "'Yu Gothic UI','Meiryo',sans-serif",
  weight: 800,
  size: 54,
  color: "#ffffff",
  outlineColor: "#050611",
  outlineWidth: 3,
  glow: "#6455cc",
  glowSize: 9,
  shadow: true,
  hotColor: "#66f2dd",
  letterSpacing: 0,
};
const box = {
  mode: "lyric",
  x: 5, y: 8, w: 90, h: 82,
  bg: "", bgOpacity: 0, radius: 0, padding: 0, borderWidth: 0,
  lyricMood: "auto",
  lyricScale: 1.25,
  lyricMaxScenes: 2,
  lifeSec: 3.5,
  lyricPartial: false,
};
const lines = [
  "こんばんは、今日も配信始めます！",
  "今日はちょっと面白い話があってね",
  "それはさすがにびっくりした",
  "待って、そんなことある？",
  "コメントありがとう！",
  "この瞬間を忘れないで",
  "明日もきっと楽しくなるよ",
  "最高！",
];
const patterns = [
  "soft", "editorial", "type", "vertical", "ribbon", "diagonal",
  "split", "orbit", "echo", "karaoke", "glitch", "impact",
];
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const json = value => {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(value));
  };
  if (url.pathname.startsWith("/api/") && req.method !== "GET") {
    json({ ok: true });
    return;
  }
  if (url.pathname === "/api/presets") {
    json(JSON.parse(fs.readFileSync(path.join(root, "defaults", "presets.json"), "utf8")));
    return;
  }
  if (url.pathname === "/api/boxes") {
    json(JSON.parse(fs.readFileSync(path.join(root, "defaults", "boxes.json"), "utf8")));
    return;
  }
  if (url.pathname === "/api/effects") { json({ effects: [] }); return; }
  if (url.pathname === "/api/hotwords") { json({ entries: [] }); return; }
  if (url.pathname === "/api/fonts") { json({ fonts: [] }); return; }
  if (url.pathname === "/api/config") { json({ preset: "standard", box: "lyric", theme: "dark" }); return; }
  if (url.pathname === "/events") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write(`data: ${JSON.stringify({ type: "init", style, box, effects: [], hotwords: [] })}\n\n`);
    let index = 0;
    const timer = setInterval(() => {
      const nextBox = { ...box, lyricPattern: patterns[index % patterns.length] };
      res.write(`data: ${JSON.stringify({ type: "style", style, box: nextBox })}\n\n`);
      res.write(`data: ${JSON.stringify({ type: "final", text: lines[index % lines.length], id: index })}\n\n`);
      index += 1;
    }, 1250);
    req.on("close", () => clearInterval(timer));
    return;
  }

  const relative = url.pathname === "/"
    ? "overlay.html"
    : decodeURIComponent(url.pathname).replace(/^\/+/, "");
  const file = path.resolve(root, relative);
  if (!file.startsWith(root)) {
    res.writeHead(403).end();
    return;
  }
  fs.readFile(file, (error, data) => {
    if (error) {
      res.writeHead(404).end("not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": mime[path.extname(file)] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    res.end(data);
  });
}).listen(port, "127.0.0.1", () => {
  console.log(`Lyric overlay demo: http://127.0.0.1:${port}/`);
});
