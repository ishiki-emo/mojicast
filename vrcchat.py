"""VRChatチャットボックスへの字幕送信（OSC/UDP）

確定字幕（または訳文）を VRChat の OSC チャットボックス
（既定 UDP 127.0.0.1:9000 の /chatbox/input）へ転送する。

- VRChatのスパム対策（約1.5秒未満の連投でタイムアウト）に合わせ、
  送信は SEND_INTERVAL 秒以上の間隔でまとめ送りする。待機中に確定が
  複数溜まったら新しい行を優先して1メッセージ（144文字以内）に結合する
- 発話中は /chatbox/typing でタイピング中表示を出す
- 設定は configure(cfg) でスナップショットを持つ（load_config は毎回
  ディスクを読むため、partial毎に呼ばれるフックからは参照しない）
- 送信は都度 sendto のステートレス。VRChat未起動でもUDPなので害はない
"""
import socket
import threading
import time

HOST = "127.0.0.1"
SEND_INTERVAL = 1.6     # VRChatのスパム判定（約1.5秒）に余裕を持たせる
TYPING_INTERVAL = 2.0   # タイピング中表示の再送間隔
MAX_CHARS = 144         # チャットボックスの文字数上限
FID_KEEP = 128          # 訳文モード用に覚えておく「自分の発話fid」の上限

_lock = threading.Lock()
_cfg = {"enabled": False, "source": "ja", "port": 9000,
        "collab": False, "self_name": "自分"}
_pending = []           # 送信待ちの確定テキスト（古い順）
_self_fids = []         # 自分の発話のfid（訳文モードのフィルタ用・有界）
_typing = False
_typing_sent = 0.0
_last_send = 0.0
_wake = threading.Event()
_worker = None


# ---------------- OSCエンコードと送信 ----------------

def _pad4(b):
    """NUL終端を付けて4バイト境界までパディング（OSC仕様）"""
    return b + b"\x00" * (4 - len(b) % 4)


def _osc_msg(addr, *args):
    """OSCメッセージのバイト列を組む。引数は str と bool のみ対応。

    真偽値はタイプタグ（T/F）だけでデータ部を持たない。VRChatの
    /chatbox/input s b n は (text, 即時送信, 通知音) に対応する。
    """
    tags = ","
    data = b""
    for a in args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        else:
            tags += "s"
            data += _pad4(str(a).encode("utf-8"))
    return _pad4(addr.encode("ascii")) + _pad4(tags.encode("ascii")) + data


def _send(addr, *args):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(_osc_msg(addr, *args), (HOST, _cfg["port"]))
    except OSError:
        pass   # ポート不正等。字幕本体には影響させない


def _merge(lines, limit=MAX_CHARS):
    """送信待ちを新しい行優先で1メッセージへ結合（入らない古い行は捨てる）"""
    out = []
    total = 0
    for t in reversed(lines):
        need = len(t) + (1 if out else 0)   # 2行目以降は改行ぶん+1
        if total + need > limit:
            break
        out.append(t)
        total += need
    if not out:   # 最新の1行だけで上限超え → 末尾をカット
        return lines[-1][:limit - 1] + "…"
    return "\n".join(reversed(out))


# ---------------- 送信ワーカー ----------------

def _loop():
    global _last_send, _typing_sent, _typing
    while True:
        _wake.wait(0.25)
        _wake.clear()
        now = time.monotonic()
        text = None
        with _lock:
            if _pending and now - _last_send >= SEND_INTERVAL:
                text = _merge(_pending)
                _pending.clear()
                _last_send = now
            typing = _typing and _cfg["enabled"]
        if text is not None:
            _send("/chatbox/input", text, True, False)   # 即時送信・通知音なし
            with _lock:
                _typing = False
        elif typing and now - _typing_sent >= TYPING_INTERVAL:
            _typing_sent = now
            _send("/chatbox/typing", True)


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="vrc-osc")
        _worker.start()


# ---------------- app_server から呼ぶ公開API ----------------

def configure(cfg):
    """設定スナップショットを更新（起動時と /api/config 保存時に呼ぶ）"""
    global _typing
    was = _cfg["enabled"]
    with _lock:
        _cfg.update({
            "enabled": bool(cfg.get("vrchat")),
            "source": cfg.get("vrchat_source", "ja"),
            "port": int(cfg.get("vrchat_port", 9000) or 9000),
            "collab": bool(cfg.get("collab")),
            "self_name": (cfg.get("self_name") or "自分").strip() or "自分",
        })
        off = was and not _cfg["enabled"]
        if off:
            _pending.clear()
            _typing = False
    if off:
        _send("/chatbox/typing", False)   # 出しっぱなしの「入力中」を消す


def _is_self(spk):
    if not _cfg["collab"]:
        return True
    return (spk or "") in ("", _cfg["self_name"])


def on_final(text, fid, spk=""):
    """確定字幕（伏せ字等の後処理適用済み）。自分の発話のみ転送対象"""
    with _lock:
        if not (_cfg["enabled"] and text and _is_self(spk)):
            return
        if fid is not None:                  # 訳文モードのために記録
            _self_fids.append(fid)
            del _self_fids[:-FID_KEEP]
        if _cfg["source"] != "ja":
            return
        _ensure_worker()
        _pending.append(text)
    _wake.set()


def on_translation(fid, text):
    """訳文の完成。訳文モードかつ自分の発話ぶんだけ転送する"""
    with _lock:
        if not (_cfg["enabled"] and text and _cfg["source"] == "tr"
                and fid in _self_fids):
            return
        _ensure_worker()
        _pending.append(text)
    _wake.set()


def on_partial(text, spk=""):
    """発話中の途中経過。タイピング中表示のON/OFFに使う"""
    global _typing
    with _lock:
        if not (_cfg["enabled"] and _is_self(spk)):
            return
        was = _typing
        _typing = bool(text)
        _ensure_worker()
    if was and not text:
        _send("/chatbox/typing", False)
    elif text:
        _wake.set()
