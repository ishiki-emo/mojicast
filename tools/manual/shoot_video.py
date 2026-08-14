"""マニュアル用の操作動画撮影（開発ツール・配布物ではない）

shoot.py と同じ隔離環境（実サーバー＋ヘッドレスChrome/CDP・dataは一時フォルダ）で、
擬似カーソル・クリック・テロップを演出しながら Page.startScreencast で録画し、
ffmpeg で章ごとの mp4 と通し版を生成する。マイクの代わりに SSE 注入で字幕を流す。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot_video.py
    → tools\\manual\\videos\\*.mp4 が生成される

前提: pip install websocket-client（開発環境のみ）/ Google Chrome / ffmpeg
注意: Mojicast本体は閉じてから実行すること（ポート8765を使用）。
"""
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
VIDEOS = os.path.join(HERE, "videos")
TMP = os.path.join(tempfile.gettempdir(), "mojicast_manual_video")
DATA = os.path.join(TMP, "data")
PROFILE = os.path.join(TMP, "chrome_profile")
FRAMES = os.path.join(TMP, "frames")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
FFMPEG = shutil.which("ffmpeg") or r"C:\FFmpeg\bin\ffmpeg.exe"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DBG = 9333
W, H = 1280, 800

os.makedirs(VIDEOS, exist_ok=True)
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(FRAMES, exist_ok=True)

# --- サーバー（実データに触れないよう data を一時フォルダへ差し替え）---
sys.path.insert(0, ROOT)
import wordstore
wordstore.DATA = DATA
wordstore.PROFILES_DIR = os.path.join(DATA, "profiles")
import app_server
app_server.start(port=PORT)
B = app_server.broadcast


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(),
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# --- Chrome (headless + CDP) ---
chrome = subprocess.Popen([
    CHROME, "--headless=new", f"--remote-debugging-port={DBG}",
    "--remote-allow-origins=*",
    f"--user-data-dir={PROFILE}", f"--window-size={W},{H}",
    "--hide-scrollbars", "--disable-gpu", "--force-device-scale-factor=1",
    "about:blank"])
for _ in range(50):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DBG}/json/version", timeout=2)
        break
    except OSError:
        time.sleep(0.3)


class Tab:
    """CDPタブ。コマンド応答は受信スレッドで捌き、screencastフレームを蓄積する"""

    def __init__(self, url):
        req = urllib.request.Request(f"http://127.0.0.1:{DBG}/json/new",
                                     method="PUT")
        info = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.id = info["id"]
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"],
                                              timeout=30)
        self._mid = 0
        self._pending = {}
        self._alive = True
        self.frames = []          # (time, jpeg bytes)
        self._recording = False
        threading.Thread(target=self._reader, daemon=True).start()
        self.cmd("Page.enable")
        self.cmd("Emulation.setDeviceMetricsOverride", width=W, height=H,
                 deviceScaleFactor=1, mobile=False)
        self.cmd("Page.navigate", url=url)
        time.sleep(0.5)
        self.wait_ready()

    def _send(self, method, wait, **params):
        self._mid += 1
        mid = self._mid
        q = queue.Queue() if wait else None
        if wait:
            self._pending[mid] = q
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params}))
        if not wait:
            return None
        r = q.get(timeout=30)
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r.get("result", {})

    def cmd(self, method, **params):
        return self._send(method, True, **params)

    def _reader(self):
        while self._alive:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                return
            if "id" in msg:
                q = self._pending.pop(msg["id"], None)
                if q:
                    q.put(msg)
            elif msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                if self._recording:
                    self.frames.append((time.time(),
                                        base64.b64decode(p["data"])))
                self._send("Page.screencastFrameAck", False,
                           sessionId=p["sessionId"])

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                     awaitPromise=True)
        return r.get("result", {}).get("value")

    def wait_ready(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.js("document.readyState") == "complete":
                return
            time.sleep(0.2)
        raise TimeoutError("page not ready")

    def start_rec(self):
        self.frames = []
        self._recording = True
        self.cmd("Page.startScreencast", format="jpeg", quality=85,
                 everyNthFrame=1)

    def stop_rec(self):
        self.cmd("Page.stopScreencast")
        self._recording = False
        return list(self.frames)

    def close(self):
        self._alive = False
        try:
            self.ws.close()
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{DBG}/json/close/{self.id}"), timeout=5)
        except OSError:
            pass


# --- 演出ヘルパー（擬似カーソル・テロップ・クリック） ---

DEMO_JS = """
(function(){
  if (window.__demo) return;
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;' +
    'border-radius:50%;background:rgba(255,80,160,.85);border:2px solid #fff;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.4);z-index:2147483647;' +
    'pointer-events:none;transform:translate(-60px,-60px);' +
    'transition:transform .55s cubic-bezier(.3,.7,.3,1)';
  document.body.appendChild(c);
  const t = document.createElement('div');
  t.style.cssText = 'position:fixed;left:0;right:0;bottom:0;padding:16px 26px;' +
    "font:700 21px 'Yu Gothic UI',sans-serif;background:rgba(8,14,26,.9);" +
    'color:#fff;z-index:2147483646;pointer-events:none;opacity:0;' +
    'transition:opacity .3s;line-height:1.45';
  document.body.appendChild(t);
  const st = document.createElement('style');
  st.textContent = '@keyframes __rip{from{transform:scale(.5);opacity:1}' +
                   'to{transform:scale(1.7);opacity:0}}';
  document.head.appendChild(st);
  window.__demo = {
    move(x, y) { c.style.transform = `translate(${x-11}px,${y-11}px)`; },
    telop(s) { t.innerHTML = s || ''; t.style.opacity = s ? 1 : 0; },
    ripple(x, y) {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${x-22}px;top:${y-22}px;` +
        'width:44px;height:44px;border-radius:50%;border:3px solid #ff50a0;' +
        'z-index:2147483647;pointer-events:none;' +
        'animation:__rip .5s ease-out forwards';
      document.body.appendChild(r);
      setTimeout(() => r.remove(), 600);
    },
  };
})();
"""


def demo(tab):
    tab.js(DEMO_JS)


def center(tab, sel):
    return tab.js(
        f"(() => {{ const e = document.querySelector({json.dumps(sel)});"
        f" if (!e) return null; const r = e.getBoundingClientRect();"
        f" return [r.left + r.width / 2, r.top + r.height / 2]; }})()")


def point(tab, sel, pause=0.9):
    xy = center(tab, sel)
    if not xy:
        print("  [warn] not found:", sel)
        return None
    tab.js(f"__demo.move({xy[0]},{xy[1]})")
    time.sleep(pause)
    return xy


def click(tab, sel, pause=0.9):
    xy = point(tab, sel, pause=0.7)
    if not xy:
        return
    tab.js(f"__demo.ripple({xy[0]},{xy[1]})")
    for typ in ("mousePressed", "mouseReleased"):
        tab.cmd("Input.dispatchMouseEvent", type=typ, x=xy[0], y=xy[1],
                button="left", clickCount=1)
    time.sleep(pause)


def telop(tab, text, hold=3.2):
    tab.js(f"__demo.telop({json.dumps(text)})")
    time.sleep(hold)


def running_state():
    B({"type": "state", "state": "running", "detail": "認識中"})
    B({"type": "level", "value": 0.42})


def say(text, fid, wait=1.6, speaker=""):
    """発話1回ぶんを partial → final で再現する"""
    for i in (8, 16):
        B({"type": "partial", "text": text[:i], "speaker": speaker})
        time.sleep(0.35)
    B({"type": "final", "text": text, "id": fid, "speaker": speaker})
    time.sleep(wait)


# --- 動画の書き出し ---

def encode(name, frames):
    """screencastフレーム列（実時刻つき）を実尺どおりのmp4へ"""
    if len(frames) < 2:
        print("  [warn] no frames:", name)
        return
    d = os.path.join(FRAMES, name)
    os.makedirs(d, exist_ok=True)
    lines = []
    for i, (t, data) in enumerate(frames):
        p = os.path.join(d, f"f{i:05d}.jpg")
        with open(p, "wb") as f:
            f.write(data)
        dur = (frames[i + 1][0] - t) if i + 1 < len(frames) else 1.0
        lines.append(f"file '{p}'\nduration {max(dur, 0.02):.3f}")
    lines.append(f"file '{os.path.join(d, f'f{len(frames)-1:05d}.jpg')}'")
    lst = os.path.join(d, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).replace("\\", "/"))
    out = os.path.join(VIDEOS, name + ".mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", lst,
                    "-vf", "scale=1280:-2,format=yuv420p",
                    "-r", "24", "-c:v", "libx264", "-crf", "22", out],
                   check=True)
    print("video:", out, os.path.getsize(out) // 1024, "KB",
          f"({frames[-1][0]-frames[0][0]:.1f}s)")


def concat_all(names, out_name):
    lst = os.path.join(FRAMES, "all.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for n in names:
            f.write(f"file '{os.path.join(VIDEOS, n + '.mp4')}'\n".replace("\\", "/"))
    out = os.path.join(VIDEOS, out_name + ".mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
    print("video:", out, os.path.getsize(out) // 1024, "KB")


# ================================================================
chapters = []
try:
    post("/api/config", {"collab": False, "translate": False,
                         "theme": "light", "preset": "standard",
                         "box": "card"})

    # ============ 1) コックピットの画面構成 ============
    t = Tab(BASE + "/ui/cockpit?theme=light")
    time.sleep(1.6)
    t.js("dismissGuide && dismissGuide()")
    demo(t)
    t.start_rec()
    telop(t, "Mojicastを起動すると、この「コックピット」が開きます", 3.0)
    point(t, "#btnEngine")
    telop(t, "右上の「▶ 開始」で音声認識がスタートします", 3.0)
    running_state()
    time.sleep(0.4)
    say("こんばんは、今日も配信はじめていきます", 101)
    telop(t, "話すと薄い文字で途中経過が出て、ひと呼吸おくと確定します", 1.0)
    say("字幕はこんな感じでリアルタイムに出ます", 102)
    time.sleep(1.0)
    telop(t, "", 0.3)
    point(t, ".quick-today")
    telop(t, "「字幕の見た目」で シーン・デザイン・レイアウト を切り替えます", 3.2)
    point(t, "#translateOn")
    telop(t, "右カラムでは 翻訳・VRChat送信・コラボ などをオン/オフできます", 3.2)
    point(t, ".studio-nav")
    telop(t, "細かい調整は下の「🎨 字幕スタジオ」「⚙ アプリ設定」から", 3.0)
    telop(t, "", 0.4)
    encode("01_cockpit", t.stop_rec())
    chapters.append("01_cockpit")

    # ============ 2) 字幕とエフェクト（配信プレビュー） ============
    t.start_rec()
    click(t, "#modePreview")
    telop(t, "「配信プレビュー」に切り替えると、OBSに映る実際の見た目を確認できます", 3.0)
    B({"type": "clear"})
    say("単語を登録しておくと、エフェクトも発動します", 103)
    say("お祝いの言葉でスピンさせたり", 104)
    say("ぷるぷるさせたりできます", 105)
    telop(t, "エフェクト単語は「字幕スタジオ → ワード演出」で登録します", 3.2)
    telop(t, "", 0.4)
    encode("02_captions", t.stop_rec())
    chapters.append("02_captions")

    # ============ 3) デザイン・シーンの切り替え ============
    t.start_rec()
    telop(t, "配信中でも、字幕の見た目はワンタップで変えられます", 2.6)
    point(t, "#sceneSel")
    t.js("(() => { const s = document.querySelector('#sceneSel');"
         " if (!s) return; s.value = 'game';"
         " s.dispatchEvent(new Event('change')); })()")
    time.sleep(1.4)
    B({"type": "clear"})
    say("シーンを「ゲーム」にすると、上部の極太テロップになります", 106)
    telop(t, "シーン ＝ デザインとレイアウトのセット。歌枠・雑談なども保存できます", 3.2)
    t.js("(() => { const s = document.querySelector('#sceneSel');"
         " if (!s) return; s.value = 'talk';"
         " s.dispatchEvent(new Event('change')); })()")
    time.sleep(1.4)
    say("雑談シーンに戻しました", 107)
    telop(t, "ボイスコマンドを設定すれば「ステラ、歌枠」のように声でも切り替えられます", 3.4)
    telop(t, "", 0.4)
    encode("03_scenes", t.stop_rec())
    chapters.append("03_scenes")

    # ============ 4) 翻訳と「翻訳のみ表示」 ============
    t.start_rec()
    point(t, "#translateOn")
    click(t, "#translateOn")
    telop(t, "「英訳（翻訳）」をオンにすると、字幕の下に訳文が併記されます"
             "（反映は ■停止→▶開始 の後）", 3.6)
    B({"type": "style", **app_server.resolve_style(app_server.load_config()),
       "translate": True})
    B({"type": "clear"})
    say("海外のリスナーさんも、これで安心です", 108, wait=0.6)
    B({"type": "translation", "id": 108,
       "text": "Overseas listeners can enjoy the stream too."})
    time.sleep(2.2)
    telop(t, "さらに v0.8.0 からは「翻訳のみ表示」が使えます", 2.8)
    cfg = app_server.load_config()
    style_ev = app_server.resolve_style(cfg)
    style_ev["style"] = dict(style_ev["style"], displayMode="en")
    B({"type": "style", **style_ev, "translate": True})
    B({"type": "clear"})
    time.sleep(0.6)
    say("日本語を出さずに、訳文だけを大きく表示できます", 109, wait=0.7)
    B({"type": "translation", "id": 109,
       "text": "Show only the translation, nice and big."})
    time.sleep(2.4)
    telop(t, "設定は「字幕スタジオ → 翻訳字幕の見た目 → 表示モード」から", 3.4)
    telop(t, "", 0.4)
    encode("04_translate", t.stop_rec())
    chapters.append("04_translate")
    t.close()

    # ============ 5) OBSに貼る ============
    post("/api/config", {"translate": False, "preset": "standard",
                         "box": "card"})
    t = Tab(BASE + "/ui/settings?section=obs&theme=light")
    time.sleep(1.4)
    t.js("switchSection && switchSection('obs')")
    demo(t)
    t.start_rec()
    telop(t, "OBSへの表示は「アプリ設定 → OBS接続」から", 2.8)
    point(t, "#obsUrl")
    telop(t, "この表示用URLをコピーして…", 2.4)
    click(t, "#copyObs")
    telop(t, "OBSで「ソース追加 → ブラウザ」を選び、URL欄に貼り付けます", 3.4)
    telop(t, "幅と高さは配信解像度と同じ（例: 1920×1080）にすれば完成です", 3.4)
    telop(t, "", 0.4)
    encode("05_obs", t.stop_rec())
    chapters.append("05_obs")
    t.close()

    concat_all(chapters, "mojicast_basic_guide")
    print("ALL DONE →", VIDEOS)
finally:
    chrome.terminate()
