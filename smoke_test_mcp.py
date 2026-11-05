"""MCP サーバのリリース前スモークテスト

ビルド済みの dist\\Mojicast\\Mojicast.exe を起動し、同梱の mcp\\mojicast_mcp.py を
AI と同じ経路（uv で起動・stdio の JSON-RPC）で呼んで、全ツールの動作と副作用を確かめる。
tests\\test_mcp_server.py は MCP ライブラリを差し替えた単体テストなので、ここでは
本物の mcp ライブラリ・凍結版の本体・配布物の中のスクリプトの組み合わせを見る。

    .\\reazonspeech-env\\Scripts\\python.exe smoke_test_mcp.py

smoke_test.ps1 と同じく dist\\Mojicast の data\\ と logs\\ を作り直す。
本物の Mojicast が 8765 で動いているときは、その設定を触らないよう中止する。
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "dist", "Mojicast")
PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"
UV = shutil.which("uv") or os.path.expanduser(r"~\.local\bin\uv.exe")

EXPECTED_TOOLS = {
    "mojicast_status", "get_design", "update_design", "get_layout", "update_layout",
    "use_design", "use_layout", "duplicate", "list_fonts", "show_sample_caption",
    "clear_captions", "get_words", "set_word_effect", "remove_word_effect", "add_words",
    "remove_words", "read_recent_logs", "undo",
}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK' if ok else 'NG'}] {name}" + (f"  … {detail}" if detail and not ok else ""))


def http(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE_URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class Mcp:
    """MCP（stdio・改行区切り JSON-RPC）の最小クライアント"""

    def __init__(self, script):
        env = {**os.environ, "MOJICAST_PORT": str(PORT)}
        self.p = subprocess.Popen([UV, "run", "--script", script], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, text=True, encoding="utf-8")
        self.n = 0
        self.info = self.req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                            "clientInfo": {"name": "smoke", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, msg):
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def req(self, method, params):
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("MCP サーバが終了しました: " + self.p.stderr.read()[-2000:])
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def call(self, tool, **args):
        """(isError, 結果) を返す"""
        res = self.req("tools/call", {"name": tool, "arguments": args})
        text = res["content"][0]["text"] if res.get("content") else ""
        try:
            val = json.loads(text)
        except ValueError:
            val = text
        return bool(res.get("isError")), val

    def ok(self, tool, **args):
        err, val = self.call(tool, **args)
        if err:
            raise RuntimeError(f"{tool} が失敗: {val}")
        return val

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()


class Sse:
    """overlay と同じ /events?overlay=1 を購読して、届いたイベントをためる"""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            with urllib.request.urlopen(BASE_URL + "/events?overlay=1", timeout=300) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data:"):
                        with self.lock:
                            self.events.append(json.loads(line[5:]))
        except OSError:
            pass   # 終了時に本体を止めると切れる

    def mark(self):
        with self.lock:
            return len(self.events)

    def wait(self, since, pred, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                hit = [e for e in self.events[since:] if pred(e)]
            if hit:
                return hit[-1]
            time.sleep(0.05)
        return None


def server_up():
    try:
        http("/api/config")
        return True
    except (urllib.error.URLError, OSError):
        return False


def run(mcp, sse):
    print("ツール一覧・状態")
    tools = {t["name"] for t in mcp.req("tools/list", {})["tools"]}
    check("ツールが 18 個そろっている", tools == EXPECTED_TOOLS,
          f"不足 {EXPECTED_TOOLS - tools} / 余分 {tools - EXPECTED_TOOLS}")
    st = mcp.ok("mojicast_status")
    check("mojicast_status", st["designs"] and st["layouts"] and st["active_design"]
          and st["overlay_url"] == f"{BASE_URL}/", st)
    design0, layout0 = st["active_design"], st["active_layout"]

    print("デザイン")
    d = mcp.ok("get_design")["design"]
    size0 = d.get("size")
    m = sse.mark()
    r = mcp.ok("update_design", changes={"size": 61, "color": "#123456"})
    live = next(p for p in http("/api/presets")["presets"] if p["id"] == design0)
    check("update_design が保存される", live["size"] == 61 and live["color"] == "#123456", live)
    check("update_design が配信画面へ届く（style イベント）",
          sse.wait(m, lambda e: e.get("type") == "style" and (e.get("style") or {}).get("size") == 61), r)
    err, _ = mcp.call("update_design", changes={"size": "とても大きく"})
    check("update_design が不正な値を断る", err)
    mcp.ok("undo")
    live = next(p for p in http("/api/presets")["presets"] if p["id"] == design0)
    check("undo でデザインが戻る", live.get("size") == size0, live.get("size"))

    print("レイアウト")
    b = mcp.ok("get_layout")["layout"]
    err, val = mcp.call("update_layout", changes={"x": 60, "w": 60})
    check("update_layout が画面外へのはみ出しを断る", err and "100" in str(val), val)
    mcp.ok("update_layout", changes={"x": 3, "y": 56, "w": 64, "h": 40})
    live = next(x for x in http("/api/boxes")["boxes"] if x["id"] == layout0)
    check("update_layout が保存される", (live["x"], live["y"], live["w"], live["h"]) == (3, 56, 64, 40), live)
    mcp.ok("undo")
    live = next(x for x in http("/api/boxes")["boxes"] if x["id"] == layout0)
    check("undo でレイアウトが戻る", (live["x"], live["w"]) == (b["x"], b["w"]), live)

    print("切り替え・複製")
    other_d = next((x["id"] for x in st["designs"] if x["id"] != design0), None)
    other_l = next((x["id"] for x in st["layouts"] if x["id"] != layout0), None)
    if other_d:
        mcp.ok("use_design", design_id=other_d)
        check("use_design で切り替わる", http("/api/config").get("preset") == other_d)
        mcp.ok("undo")
        check("undo でデザインの選択が戻る", http("/api/config").get("preset") == design0)
    if other_l:
        mcp.ok("use_layout", layout_id=other_l)
        check("use_layout で切り替わる", http("/api/config").get("box") == other_l)
        mcp.ok("undo")
        check("undo でレイアウトの選択が戻る", http("/api/config").get("box") == layout0)
    err, _ = mcp.call("use_design", design_id="no-such-design")
    check("use_design が無い ID を断る", err)
    r = mcp.ok("duplicate", kind="design", name="スモーク複製")
    ids = [p["id"] for p in http("/api/presets")["presets"]]
    check("duplicate で複製されて使用中になる",
          r["id"] in ids and http("/api/config").get("preset") == r["id"], r)
    mcp.ok("undo")
    ids = [p["id"] for p in http("/api/presets")["presets"]]
    check("undo で複製が消えて選択も戻る",
          r["id"] not in ids and http("/api/config").get("preset") == design0)

    print("フォント・試し表示")
    f = mcp.ok("list_fonts")
    check("list_fonts が PC のフォントを返す", f["count"] > 10, f["count"])
    q = mcp.ok("list_fonts", query="gothic")
    check("list_fonts の絞り込み", all("gothic" in x.lower() for x in q["fonts"]), q["fonts"][:5])
    m = sse.mark()
    r = mcp.ok("show_sample_caption", text="スモークテストの試し表示", translation="smoke test")
    fin = sse.wait(m, lambda e: e.get("type") == "final" and e.get("sample")
                   and e.get("text") == "スモークテストの試し表示")
    check("show_sample_caption が字幕として届く", fin and fin.get("id") == r.get("id"), r)
    check("訳文も併記で届く", sse.wait(m, lambda e: e.get("type") == "translation"
                                    and e.get("id") == r.get("id") and e.get("text") == "smoke test"))
    m = sse.mark()
    mcp.ok("clear_captions")
    check("clear_captions で消える", sse.wait(m, lambda e: e.get("type") == "clear"))

    print("単語エフェクト")
    w = mcp.ok("get_words")
    check("get_words", all(k in w for k in ("recognition", "banned", "translation", "effects")), list(w))
    eff0 = http("/api/effects")["effects"]
    r = mcp.ok("set_word_effect", word="スモーク太郎", anim="rainbow", particle="spark", scale=1.5)
    e = next((x for x in http("/api/effects")["effects"] if x["word"] == "スモーク太郎"), None)
    check("set_word_effect が保存される",
          e and (e["anim"], e["particle"], e["scale"]) == ("rainbow", "spark", 1.5), e)
    err, _ = mcp.call("set_word_effect", word="スモーク太郎", anim="explode")
    check("set_word_effect が無い動きを断る", err)
    mcp.ok("remove_word_effect", word="スモーク太郎")
    check("remove_word_effect で外れる",
          all(x["word"] != "スモーク太郎" for x in http("/api/effects")["effects"]))
    mcp.ok("undo")
    check("undo で外す前に戻る",
          any(x["word"] == "スモーク太郎" for x in http("/api/effects")["effects"]))
    mcp.ok("undo")
    check("もう一度 undo で付ける前に戻る", http("/api/effects")["effects"] == eff0)

    print("辞書")
    err, val = mcp.call("add_words", kind="recognition", entries=[{"surface": "煙太郎"}])
    check("漢字の語は読みが無いと断る", err and "reading" in str(val), val)
    r = mcp.ok("add_words", kind="recognition",
               entries=[{"surface": "煙太郎", "reading": "けむたろう／ケム太郎"}])
    hw = {x["surface"]: x for x in http("/api/hotwords")["entries"]}
    check("認識辞書に登録される",
          r["added"] == ["煙太郎"] and hw.get("煙太郎", {}).get("reading") == "けむたろう／ケム太郎", r)
    check("認識を止めているので restart_needed は false", r["restart_needed"] is False, r)
    r = mcp.ok("add_words", kind="translation", entries=[{"ja": "煙太郎", "en": "Kemutaro"}])
    gl = {x["ja"]: x["en"] for x in http("/api/glossary")["entries"]}
    check("固定訳に登録される", gl.get("煙太郎") == "Kemutaro", r)
    mcp.ok("add_words", kind="banned", entries=["スモーク伏せ字"])
    check("伏せ字に登録される", "スモーク伏せ字" in http("/api/banned")["words"])
    err, val = mcp.call("remove_words", kind="banned", words=["スモーク伏せ字"])
    check("伏せ字は AI から外せない",
          err and "スモーク伏せ字" in http("/api/banned")["words"], val)
    r = mcp.ok("remove_words", kind="translation", words=["煙太郎"])
    check("remove_words で固定訳が外れる",
          r["removed"] == ["煙太郎"] and all(x["ja"] != "煙太郎" for x in http("/api/glossary")["entries"]), r)
    err, _ = mcp.call("get_words", profile="存在しないセット")
    check("無いプロファイルを断る", err)

    print("配信ログ")
    r = mcp.ok("read_recent_logs")
    check("ログが無いときは案内を返す", r["logs"] == [] and r.get("message"), r)
    day = os.path.join(APP, "logs", "2026-10-26")
    os.makedirs(day, exist_ok=True)
    with open(os.path.join(day, "2026-10-26_210000.log"), "w", encoding="utf-8") as fh:
        fh.write("[2026-10-26 21:31:05] こんばんは、ケム太郎です\n"
                 "[2026-10-26 21:31:12] 今日もまったり作業していきます\n")
    r = mcp.ok("read_recent_logs", sessions=1)
    lines = r["logs"][0]["lines"] if r["logs"] else []
    check("配信ログが読める", len(lines) == 2 and "ケム太郎" in lines[0], r)

    print("後片付け")
    for _ in range(10):
        if not mcp.ok("undo")["ok"]:
            break
    hw = {x["surface"] for x in http("/api/hotwords")["entries"]}
    gl = {x["ja"] for x in http("/api/glossary")["entries"]}
    check("undo を重ねると辞書の追加も消える", "煙太郎" not in hw and "煙太郎" not in gl
          and "スモーク伏せ字" not in http("/api/banned")["words"])
    check("取り消すものが無くなると ok=false", mcp.ok("undo")["ok"] is False)


def main():
    if not os.path.exists(os.path.join(APP, "Mojicast.exe")):
        sys.exit("dist\\Mojicast\\Mojicast.exe がありません。先に pyinstaller → build_bundle.ps1 を実行してください")
    script = os.path.join(APP, "mcp", "mojicast_mcp.py")
    for f in (script, script + ".lock"):
        if not os.path.exists(f):
            sys.exit(f"{f} がありません（build_bundle.ps1 の同梱漏れ）")
    if server_up():
        sys.exit(f"ポート {PORT} で Mojicast が動いています。本物の設定を触らないよう、閉じてから実行してください")
    for d in ("data", "logs"):
        shutil.rmtree(os.path.join(APP, d), ignore_errors=True)

    print("Mojicast.exe を起動中...")
    exe = subprocess.Popen([os.path.join(APP, "Mojicast.exe")], cwd=APP)
    mcp = None
    try:
        for _ in range(120):
            if server_up():
                break
            time.sleep(0.5)
        else:
            sys.exit("Mojicast.exe のサーバが応答しません")
        sse = Sse()
        mcp = Mcp(script)
        check("MCP サーバが uv で起動して応答する", mcp.info.get("serverInfo"), mcp.info)
        run(mcp, sse)
    except Exception as e:  # 途中で落ちても結果をまとめて出す
        check("最後まで実行できた", False, repr(e))
    finally:
        if mcp:
            mcp.close()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(exe.pid)], capture_output=True)
        time.sleep(1)
        for d in ("data", "logs"):
            shutil.rmtree(os.path.join(APP, d), ignore_errors=True)

    ng = [n for n, ok in results if not ok]
    print(f"\n=== MCP SMOKE TEST: {'PASS' if not ng else 'FAIL'} "
          f"（{len(results) - len(ng)}/{len(results)}） ===")
    for n in ng:
        print("  NG:", n)
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
