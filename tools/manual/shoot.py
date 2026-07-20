"""マニュアル用スクリーンショット撮影（開発ツール・配布物ではない）

実サーバー＋ヘッドレスChrome(CDP)で各画面を撮る。ユーザーデータには触れない
（wordstore の data を一時フォルダへ差し替えて撮影する）。
SSE注入で「認識中」「コラボ中」の画面を演出する。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot.py
    → tools\\manual\\shots\\*.png が生成される。次に build_manual.py を実行。

前提: pip install Pillow websocket-client（開発環境のみ）/ Google Chrome
注意: ポート8765を使う（スクショにOBS用URLが正しく写るようにするため）。
      Mojicast本体は閉じてから実行すること。
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SHOTS = os.path.join(HERE, "shots")
TMP = os.path.join(tempfile.gettempdir(), "mojicast_manual_shoot")
DATA = os.path.join(TMP, "data")
PROFILE = os.path.join(TMP, "chrome_profile")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DBG = 9333

os.makedirs(SHOTS, exist_ok=True)
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

# --- Chrome (headless + CDP) ---
chrome = subprocess.Popen([
    CHROME, "--headless=new", f"--remote-debugging-port={DBG}",
    "--remote-allow-origins=*",
    f"--user-data-dir={PROFILE}", "--window-size=1300,900",
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
        self.cmd("Page.navigate", url=url)   # /json/new のurlは無視されるため明示遷移
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

    def shot(self, name):
        data = self.cmd("Page.captureScreenshot", format="png")["data"]
        path = os.path.join(SHOTS, name + ".png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        print("shot:", name, os.path.getsize(path) // 1024, "KB")

    def close(self):
        try:
            self.ws.close()
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{DBG}/json/close/{self.id}"), timeout=5)
        except OSError:
            pass


def running_state():
    B({"type": "state", "state": "running", "detail": "認識中"})
    B({"type": "level", "value": 0.46})


try:
    # ============ 1) コックピット（初回ガイド表示） ============
    post("/api/config", {"collab": False, "translate": False})
    t = Tab(BASE + "/ui/cockpit")
    t.size(1160, 780)
    time.sleep(1.5)          # loadAll完了待ち
    t.shot("cockpit_guide")

    # ============ 2) コックピット（ソロ・認識中） ============
    t.js("dismissGuide()")
    running_state()
    time.sleep(0.2)
    B({"type": "final", "text": "こんばんは、今日も配信はじめていきます", "id": 1,
       "speaker": ""})
    time.sleep(0.3)
    B({"type": "final", "text": "字幕はこんな感じでリアルタイムに出ます", "id": 2,
       "speaker": ""})
    time.sleep(0.3)
    B({"type": "partial", "text": "エフェクト単語も光ったりし", "speaker": ""})
    time.sleep(0.8)
    t.shot("cockpit_solo")

    # ============ 3) コックピット（コラボON・2行表示） ============
    post("/api/config", {
        "collab": True, "collab_source": "process",
        "collab_process": "Discord.exe",
        "self_name": "自分", "guest_name": "ゲスト",
        "preset": "standard", "box": "half-right",
        "guest_preset": "collab", "guest_box": "half-left"})
    time.sleep(1.2)          # SSE style → refreshPresets 完了待ち
    running_state()
    B({"type": "clear"})
    time.sleep(0.2)
    B({"type": "final", "text": "今日はコラボ配信です、よろしくね", "id": 11,
       "speaker": "自分"})
    time.sleep(0.3)
    B({"type": "final", "text": "呼んでくれてありがとう、楽しみにしてたよ", "id": 12,
       "speaker": "ゲスト"})
    time.sleep(0.3)
    B({"type": "partial", "text": "さっそくゲームはじめよっか", "speaker": "自分"})
    time.sleep(0.8)
    t.shot("cockpit_collab")
    t.close()

    # ============ 4) コラボ設定窓 ============
    t = Tab(BASE + "/ui/collab")
    t.size(560, 640)
    time.sleep(1.2)
    t.shot("collab_window")
    t.close()

    # ============ 5) スタジオ（文字スタイル / 単語） ============
    t = Tab(BASE + "/ui/studio")
    t.size(1120, 800)
    time.sleep(1.8)
    t.shot("studio_style")
    t.close()
    t = Tab(BASE + "/ui/studio?tab=words")
    t.size(1120, 800)
    time.sleep(1.8)
    t.shot("studio_words")
    t.close()

    # ============ 6) オーバーレイ（コラボの左右2ボックス） ============
    t = Tab(BASE + "/")
    t.size(1280, 720)
    time.sleep(1.0)
    # 配信画面の代わりの背景（マニュアル用の見立て）
    t.js("document.body.style.background="
         "'linear-gradient(135deg,#1a2440 0%,#2a1a40 55%,#40233a 100%)'")
    B({"type": "final", "text": "今日はコラボ配信です、よろしくね！", "id": 21,
       "speaker": "自分"})
    time.sleep(0.4)
    B({"type": "final", "text": "呼んでくれてありがとう！", "id": 22,
       "speaker": "ゲスト"})
    time.sleep(0.4)
    B({"type": "final", "text": "このゲーム、実は初めてなんだ", "id": 23,
       "speaker": "ゲスト"})
    time.sleep(0.9)
    t.shot("overlay_collab")
    t.close()

    print("ALL SHOTS DONE →", SHOTS)
finally:
    chrome.terminate()
