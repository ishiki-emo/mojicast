"""マニュアル用スクリーンショット撮影（開発ツール・配布物ではない）

実サーバー＋ヘッドレスChrome(CDP)で各画面を撮る。ユーザーデータには触れない
（wordstore の data を一時フォルダへ差し替えて撮影する）。
SSE注入で「認識中」「コラボ中」の画面を演出する。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot.py
    → tools\\manual\\shots\\*.png が生成される。次に build_manual.py を実行。

前提: pip install Pillow websocket-client（開発環境のみ）/ Google Chrome
注意: 本体（8765）とぶつからないよう 8766 で動かし、画面に写るOBS用URLだけ
      既定の http://127.0.0.1:8765/ に書き換えて撮る（本体は起動したままでよい）。
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
PORT = 8766
SHOWN_URL = "http://127.0.0.1:8765/"   # 画面に写すOBS用URL（既定ポート）
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


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=10).read())


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

    def show_default_url(self):
        """撮影用ポートのURLを、利用者の画面と同じ既定ポートの表記にする"""
        self.js("document.querySelectorAll('#obsUrl').forEach(e => e.value = %s)"
                % json.dumps(SHOWN_URL))

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
    # 新しい場所への初回起動扱いになる（引き継ぎ案内が出る）ので消しておく
    post("/api/config", {"migrate_offer": "", "setup_suggested": True, "theme": "light",
                         "monitor_mode": "preview"})

    # ============ 0) コックピット（初回起動・はじめの3ステップ） ============
    post("/api/config", {"collab": False, "translate": False, "guide_dismissed": False})
    t = Tab(BASE + "/ui/cockpit?theme=light")
    t.size(1280, 800)
    time.sleep(1.8)          # loadAll完了待ち
    t.js("showGuideIfNew(true)")   # 案内は本当の初回起動（提案前）だけ出るので明示して出す
    time.sleep(0.3)
    t.show_default_url()
    t.shot("cockpit_first")

    # ============ 1) コックピット（ソロ・認識中・配信プレビュー） ============
    t.js("dismissGuide()")
    post("/api/config", {"box": "lower", "preset": "standard"})
    time.sleep(1.0)
    running_state()
    time.sleep(0.2)
    B({"type": "final", "text": "こんばんは、今日も配信はじめていきます", "id": 1,
       "speaker": ""})
    time.sleep(0.3)
    B({"type": "final", "text": "字幕はこんな感じでリアルタイムに出ます", "id": 2,
       "speaker": ""})
    time.sleep(0.3)
    B({"type": "partial", "text": "話している途中は薄い文字で", "speaker": ""})
    time.sleep(1.2)
    t.show_default_url()
    t.shot("cockpit_solo")

    # ============ 2) コックピット（コラボON・2段表示） ============
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
    time.sleep(1.2)
    t.js("$('restartHint').style.display = 'none'")   # 設定を変えた直後の「再起動で反映」の帯は写さない
    t.show_default_url()
    t.shot("cockpit_collab")
    t.close()

    # ============ 3) アプリ設定・声の聞き取り ============
    post("/api/config", {"collab": False, "asr_model": "k2-ja",
                         "asr_lang": "auto", "translate_lang": "en",
                         "box": "lower", "guest_box": ""})
    t = Tab(BASE + "/ui/settings?section=hearing&theme=light")
    t.size(1040, 760)
    time.sleep(1.2)
    t.js("switchSection && switchSection('hearing')")
    time.sleep(0.4)
    t.shot("settings_hearing")
    t.close()

    # ============ 4) アプリ設定・1対1コラボ ============
    post("/api/config", {"collab": True, "collab_source": "process",
                         "collab_process": "Discord.exe",
                         "self_name": "自分", "guest_name": "ゲスト"})
    t = Tab(BASE + "/ui/settings?section=collab&theme=light")
    t.size(1040, 760)
    time.sleep(1.2)
    t.js("switchSection && switchSection('collab')")
    time.sleep(0.4)
    t.shot("settings_collab")
    t.close()
    post("/api/config", {"collab": False})

    # ============ 5) 字幕スタジオ（入口 / 単語） ============
    t = Tab(BASE + "/ui/studio?theme=light")
    t.size(1200, 800)
    time.sleep(1.8)
    t.shot("studio_style")
    t.close()
    t = Tab(BASE + "/ui/studio?tab=words&theme=light")
    t.size(1200, 800)
    time.sleep(1.8)
    t.shot("studio_words")
    t.close()

    # ============ 6) レイアウトの編集（フォームとプレビューが横並び） ============
    def edit_layout(box_id, name, after=""):
        t = Tab(BASE + f"/ui/studio?detail=layout&box={box_id}&theme=light")
        t.size(1280, 820)
        time.sleep(2.2)
        f = "document.getElementById('fStyle').contentWindow"
        t.js(f + ".beginBoxEdit()")
        time.sleep(0.6)
        if after:
            t.js(after.replace("W.", f + "."))
            time.sleep(1.5)
        t.shot(name)
        t.close()

    edit_layout("lower", "studio_layout")
    # 吹き出し: 3つ積み重ねた状態を、吹き出しの項目が見える位置で撮る
    # （ポイントに散らすと番号の印が文字に重なって読みにくいので、積み重ねで見せる）
    boxes = get("/api/boxes")["boxes"]
    for b in boxes:
        if b["id"] == "bubble":
            b.update(bubbleArrange="stack", bubbleHistory=3)
    post("/api/boxes", {"boxes": boxes})
    edit_layout("bubble", "studio_bubble",
                "(() => { const d = W.document;"
                " const h = [...d.querySelectorAll('*')].find(e => e.children.length <= 2 &&"
                " /💬 吹き出し/.test(e.textContent) && e.offsetParent);"
                " if (h) h.scrollIntoView({block: 'start'});"
                " const add = [...d.querySelectorAll('button')].find(b => /行を追加/.test(b.textContent));"
                " if (add) { add.click(); add.click(); add.click(); }"
                " return true; })()")

    print("ALL SHOTS DONE →", SHOTS)
finally:
    chrome.terminate()
