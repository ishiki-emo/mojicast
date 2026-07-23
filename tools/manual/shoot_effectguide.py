"""エフェクトガイド用スクリーンショット撮影（開発ツール・配布物ではない）

docs/EFFECT_GUIDE.md の実例を、実サーバー＋ヘッドレスChromeで撮影する。
shoot_styleguide.py と同じ仕組み。docs/images/effectguide_*.jpg|png へ書き出す。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot_effectguide.py

前提: pip install Pillow websocket-client（開発環境のみ）/ Google Chrome
注意: ポート8765を使うため Mojicast 本体は閉じてから実行すること。
"""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(ROOT, "docs", "images")
TMP = os.path.join(tempfile.gettempdir(), "mojicast_effectguide_shoot")
DATA = os.path.join(TMP, "data")
PROFILE = os.path.join(TMP, "chrome_profile")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DBG = 9335

os.makedirs(OUT_DIR, exist_ok=True)
shutil.rmtree(DATA, ignore_errors=True)
shutil.rmtree(PROFILE, ignore_errors=True)

# --- サーバー（実データに触れないよう data を一時フォルダへ差し替え）---
sys.path.insert(0, ROOT)
import wordstore
wordstore.DATA = DATA
wordstore.PROFILES_DIR = os.path.join(DATA, "profiles")
import app_server
app_server.start(port=PORT)


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(),
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


B = app_server.broadcast

# --- ガイド実例のエフェクト単語 ---
post("/api/effects", {"effects": [
    {"word": "ありがとう", "color": "#ffd97a", "scale": 1.15,
     "anim": "shine", "font": "", "particle": "spark"},
    {"word": "かわいい", "color": "#ff9ec6", "scale": 1.15,
     "anim": "heartbeat", "font": "", "particle": "heart"},
    {"word": "ナイス", "color": "#ffd400", "scale": 1.3,
     "anim": "rainbow", "font": "", "particle": "confetti"},
    {"word": "初見", "color": "#00e5ff", "scale": 1.15,
     "anim": "neon", "font": "", "particle": "none"},
]})

# --- Chrome (headless + CDP) ---
chrome = subprocess.Popen([
    CHROME, "--headless=new", f"--remote-debugging-port={DBG}",
    "--remote-allow-origins=*",
    f"--user-data-dir={PROFILE}", "--window-size=1920,1080",
    "--hide-scrollbars", "--disable-gpu", "--force-device-scale-factor=1",
    "about:blank"])
for _ in range(50):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DBG}/json/version",
                               timeout=2)
        break
    except OSError:
        time.sleep(0.3)


class Tab:
    def __init__(self, url):
        req = urllib.request.Request(
            f"http://127.0.0.1:{DBG}/json/new", method="PUT")
        info = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.id = info["id"]
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"],
                                              timeout=30)
        self._mid = 0
        self.cmd("Page.enable")
        self.cmd("Page.navigate", url=url)
        time.sleep(0.5)
        self.wait_ready()
        assert self.js("location.href").startswith("http"), "navigation failed"

    def cmd(self, method, **params):
        self._mid += 1
        mid = self._mid
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def wait_ready(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.js("document.readyState") == "complete":
                return
            time.sleep(0.2)
        raise TimeoutError("page not ready")

    def size(self, w, h):
        self.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h,
                 deviceScaleFactor=1, mobile=False)
        time.sleep(0.3)

    def shot(self, name, width=1280, fmt="jpg"):
        data = self.cmd("Page.captureScreenshot", format="png")["data"]
        img = Image.open(io.BytesIO(base64.b64decode(data)))
        if fmt == "jpg":
            img = img.convert("RGB")
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)),
                             Image.LANCZOS)
        path = os.path.join(OUT_DIR, name + "." + fmt)
        if fmt == "jpg":
            img.save(path, "JPEG", quality=88, optimize=True)
        else:
            img.save(path, "PNG", optimize=True)
        print("shot:", name, os.path.getsize(path) // 1024, "KB")

    def close(self):
        try:
            self.ws.close()
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{DBG}/json/close/{self.id}"), timeout=5)
        except OSError:
            pass


try:
    # ============ 1) オーバーレイ実例（エフェクト発動中＋パーティクル） ============
    post("/api/config", {"collab": False, "preset": "standard", "box": "none"})
    t = Tab(BASE + "/")
    t.size(1920, 1080)
    time.sleep(1.0)
    t.js("document.body.style.background="
         "'linear-gradient(135deg,#1c2a45 0%,#3a2a4d 55%,#1d3a3a 100%)'")
    B({"type": "final", "text": "そのアイコン、めっちゃかわいいね",
       "id": 301, "speaker": ""})
    time.sleep(0.6)
    B({"type": "final", "text": "今のプレイ、我ながらナイスだった！",
       "id": 302, "speaker": ""})
    time.sleep(0.6)
    # 最終行は上向きに舞うキラキラ付き（下向きの紙吹雪は画面外に落ちて写らない）
    B({"type": "final", "text": "わっ、初見さんだ！来てくれてありがとう！",
       "id": 303, "speaker": ""})
    time.sleep(0.4)
    # パーティクルが舞っている瞬間で全アニメーションをフリーズして確実に写す
    t.js("document.getAnimations().forEach(a => { a.pause(); a.currentTime = 300; })")
    time.sleep(0.2)
    t.shot("effectguide_overlay")
    t.close()

    # ============ 2) 単語スタジオ（✨強調する単語タブ・ライト） ============
    post("/api/config", {"theme": "light"})
    t = Tab(BASE + "/ui/words?theme=light")
    t.size(1120, 720)
    time.sleep(1.5)
    t.js("showTab && showTab('fx')")
    time.sleep(0.8)
    t.shot("effectguide_words_fx", width=1120, fmt="png")
    t.close()

    print("ALL SHOTS DONE →", OUT_DIR)
finally:
    chrome.terminate()
