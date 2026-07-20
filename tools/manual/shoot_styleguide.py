"""スタイルガイド用スクリーンショット撮影（開発ツール・配布物ではない）

docs/STYLE_GUIDE.md のシーン別レシピ実例を、実サーバー＋ヘッドレスChromeで
オーバーレイに字幕をSSE注入して撮影する。shoot.py と同じ仕組みだが、
本番比率を保つため 1920×1080 で撮り、docs/images/styleguide_*.jpg へ
1280幅へ縮小して直接書き出す（マニュアルのビルドとは独立）。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot_styleguide.py

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
TMP = os.path.join(tempfile.gettempdir(), "mojicast_styleguide_shoot")
DATA = os.path.join(TMP, "data")
PROFILE = os.path.join(TMP, "chrome_profile")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DBG = 9334

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


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=10).read())


B = app_server.broadcast

# --- レシピ用の追加ボックス（一時データなので本物の設定には残らない） ---
boxes = get("/api/boxes")["boxes"]
boxes.append({
    "id": "sg-lyric", "name": "リリック（ガイド用）", "desc": "",
    "x": 2, "y": 4, "w": 96, "h": 92, "bg": "#000000", "bgOpacity": 0,
    "radius": 0, "padding": 20, "borderColor": "#000000", "borderWidth": 0,
    "align": "left", "maxLines": 4, "smooth": True, "smoothMs": 250,
    "mode": "lyric", "lyricSplit": "phrase", "lyricScale": 1.3,
    "lifeSec": 9, "maxChunks": 8, "vertRate": 25, "rotate": 7,
    "sizeJitter": 0.4, "stagger": 140, "lyricPartial": False,
})
boxes.append({
    "id": "sg-vert", "name": "縦書き（ガイド用）", "desc": "",
    "x": 62, "y": 6, "w": 34, "h": 88, "bg": "#10131a", "bgOpacity": 0.35,
    "radius": 14, "padding": 28, "borderColor": "#000000", "borderWidth": 0,
    "align": "left", "maxLines": 10, "smooth": True, "smoothMs": 250,
    "mode": "vertical",
})
post("/api/boxes", {"boxes": boxes})

# --- 強調・エフェクト単語（実例に彩りを出す） ---
post("/api/hotwords", {"entries": [{"surface": "Mojicast", "reading": "もじきゃすと",
                                    "score": ""}]})
post("/api/effects", {"effects": [
    {"word": "ありがとう", "color": "#ffd97a", "scale": 1.05,
     "anim": "shine", "font": "", "particle": "spark"},
    {"word": "ボス戦", "color": "#ff5c5c", "scale": 1.1,
     "anim": "shake", "font": "", "particle": "none"},
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

    def shot(self, name):
        data = self.cmd("Page.captureScreenshot", format="png")["data"]
        img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        img = img.resize((1280, round(img.height * 1280 / img.width)),
                         Image.LANCZOS)
        path = os.path.join(OUT_DIR, name + ".jpg")
        img.save(path, "JPEG", quality=88, optimize=True)
        print("shot:", name, os.path.getsize(path) // 1024, "KB")

    def close(self):
        try:
            self.ws.close()
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{DBG}/json/close/{self.id}"), timeout=5)
        except OSError:
            pass


def bg(tab, colors):
    """配信画面の見立て（グラデーション背景）"""
    tab.js("document.body.style.background="
           f"'linear-gradient(135deg,{colors})'")


def scene(tab, preset, box, colors):
    """スタイル・レイアウトを切り替えて画面をリセット"""
    post("/api/config", {"collab": False, "preset": preset, "box": box})
    time.sleep(0.9)           # SSE style 反映待ち
    B({"type": "clear"})
    bg(tab, colors)
    time.sleep(0.3)


def finals(*texts, gap=0.5, speaker=""):
    for i, text in enumerate(texts):
        B({"type": "final", "text": text, "id": 100 + i, "speaker": speaker})
        time.sleep(gap)


try:
    t = Tab(BASE + "/")
    t.size(1920, 1080)
    time.sleep(1.0)

    # ============ 1) スタンダード × フリー（基本形） ============
    scene(t, "standard", "none", "#1c2a45 0%,#3a2a4d 55%,#1d3a3a 100%")
    finals("皆様こんばんは！今日もまったり配信していきます",
           "Mojicastの字幕、こんな感じで出ます。来てくれてありがとう")
    B({"type": "partial", "text": "はなしているとちゅうは、うすいもじでひょうじされ",
       "speaker": ""})
    time.sleep(1.0)
    t.shot("styleguide_standard_free")

    # ============ 2) 極太テロップ × 下部バー（ゲーム実況） ============
    scene(t, "telop", "lower", "#0f2027 0%,#203a43 55%,#2c5364 100%")
    finals("回復アイテム、持ってきてよかった…",
           "うわっ、ここでボス戦！？聞いてないよ！", gap=0.7)
    time.sleep(0.9)
    t.shot("styleguide_telop_lower")

    # ============ 3) エレガント × 角丸カード（落ち着いた雑談） ============
    scene(t, "elegant", "card", "#3a2c3a 0%,#241f2e 55%,#1c2733 100%")
    finals("今日も来てくれてありがとう、ゆっくりしていってね",
           "お茶でも飲みながら、のんびりおしゃべりしましょう", gap=0.7)
    time.sleep(0.9)
    t.shot("styleguide_elegant_card")

    # ============ 4) サイバー × サイドログ（テック系・作業配信） ============
    scene(t, "cyber", "side", "#0f0c29 0%,#302b63 55%,#24243e 100%")
    finals("ビルド通ったので今日はUI周りを直していきます",
           "このバグ、再現条件がわかったかも",
           "コメントの質問はあとでまとめて答えますね",
           "メモリ使用量もいい感じに収まってる",
           "よし、次のタスクいきましょう", gap=0.45)
    time.sleep(0.9)
    t.shot("styleguide_cyber_side")

    # ============ 5) キュート × リリック（歌枠） ============
    scene(t, "cute", "sg-lyric", "#4a2a5a 0%,#6a3a6e 50%,#2a3a6e 100%")
    finals("きらきらひかる、よぞらのむこう",
           "ふたりでうたう、よるのメロディー",
           "とどけこのこえ、ほしのかなたへ", gap=0.9)
    time.sleep(1.2)
    t.shot("styleguide_cute_lyric")

    # ============ 6) エレガント × 縦書き（和風・朗読） ============
    scene(t, "elegant", "sg-vert", "#232526 0%,#2c3338 55%,#414345 100%")
    finals("静かな夜にお届けする朗読配信",
           "今宵の物語は、雪の降る町のお話です",
           "どうぞ、ごゆっくり", gap=0.7)
    time.sleep(0.9)
    t.shot("styleguide_elegant_vertical")

    # ============ 7) コラボ（左右ハーフ） ============
    post("/api/config", {
        "collab": True, "collab_source": "process",
        "collab_process": "Discord.exe",
        "self_name": "自分", "guest_name": "ゲスト",
        "preset": "standard", "box": "half-right",
        "guest_preset": "collab", "guest_box": "half-left"})
    time.sleep(1.2)
    B({"type": "clear"})
    bg(t, "#1a2440 0%,#2a1a40 55%,#40233a 100%")
    time.sleep(0.3)
    B({"type": "final", "text": "今日はコラボ配信です、よろしくね！",
       "id": 201, "speaker": "自分"})
    time.sleep(0.5)
    B({"type": "final", "text": "呼んでくれてありがとう！",
       "id": 202, "speaker": "ゲスト"})
    time.sleep(0.5)
    B({"type": "partial", "text": "さっそくはじめよっか", "speaker": "自分"})
    time.sleep(1.0)
    t.shot("styleguide_collab_half")
    t.close()

    print("ALL SHOTS DONE →", OUT_DIR)
finally:
    chrome.terminate()
