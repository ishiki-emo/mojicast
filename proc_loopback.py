"""
プロセス指定ループバックキャプチャ（1対1コラボ・方式2）

Windows 10 2004 (build 19041) 以降の Process Loopback API で
「指定アプリ（例: Discord.exe）の出力音声だけ」を仮想ケーブル無しで取り込む。
依存を増やさないため COM 呼び出しはすべて ctypes で直接行う。

提供するもの:
  is_supported()                 この Windows で使えるか
  list_audio_apps()              音声セッションを持つアプリ一覧（UI の選択肢用）
  resolve_pid(exe_name)          exe 名 → キャプチャ対象の root PID
  ProcessLoopbackCapture(...)    キャプチャ本体（context manager・16kHz mono float32）
"""
import ctypes
import os
import sys
import threading
from ctypes import POINTER, byref, c_long, c_longlong, c_uint, c_ulong, c_void_p
from ctypes import wintypes

import numpy as np

SAMPLE_RATE = 16000

_ole32 = ctypes.WinDLL("ole32")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HRESULT = c_long
E_NOINTERFACE = -2147467262        # 0x80004002
COINIT_MULTITHREADED = 0
RPC_E_CHANGED_MODE = -2147417850   # 0x80010106


# ---------------- GUID ----------------

class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, s=None):
        super().__init__()
        if s:
            _ole32.CLSIDFromString(s, byref(self))


IID_IUnknown = GUID("{00000000-0000-0000-C000-000000000046}")
IID_IAgileObject = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
IID_ICompletionHandler = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"


def _same_guid(a, b):
    return bytes(a) == bytes(b)


# ---------------- COM 呼び出しヘルパ（vtable 直叩き） ----------------

def _com(ptr, idx, *argtypes):
    """COMインターフェースポインタの vtable[idx] を呼べる関数を返す"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)
    return lambda *args: proto(vtbl[idx])(ptr, *args)


def _release(ptr):
    if ptr:
        proto = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
        vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
        proto(vtbl[2])(ptr)


def _check(hr, what):
    if hr < 0:
        raise OSError(f"{what} failed (hr=0x{hr & 0xFFFFFFFF:08X})")


def _coinit():
    """このスレッドで COM を初期化。CoUninitialize が必要なら True"""
    hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr == RPC_E_CHANGED_MODE:
        return False          # 既に別モードで初期化済み（そのまま使える）
    return True


# ---------------- 構造体 ----------------

class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


def _float_format(rate, channels):
    f = WAVEFORMATEX()
    f.wFormatTag = 3            # WAVE_FORMAT_IEEE_FLOAT
    f.nChannels = channels
    f.nSamplesPerSec = rate
    f.wBitsPerSample = 32
    f.nBlockAlign = channels * 4
    f.nAvgBytesPerSec = rate * f.nBlockAlign
    return f


class _ACTIVATION_PARAMS(ctypes.Structure):
    """AUDIOCLIENT_ACTIVATION_PARAMS（PROCESS_LOOPBACK 固定なので union は展開）"""
    _fields_ = [("ActivationType", c_uint),          # 1 = PROCESS_LOOPBACK
                ("TargetProcessId", wintypes.DWORD),
                ("ProcessLoopbackMode", c_uint)]     # 0 = INCLUDE_TARGET_PROCESS_TREE


class _BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("pBlobData", c_void_p)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("blob", _BLOB)]


# ---------------- 対応チェック ----------------

def is_supported():
    """Process Loopback API が使える Windows か（Win10 2004 / build 19041 以降）"""
    try:
        return sys.getwindowsversion().build >= 19041
    except Exception:
        return False


# ---------------- 非同期アクティベーション完了ハンドラ ----------------

class _CompletionHandler:
    """IActivateAudioInterfaceCompletionHandler の最小実装（ctypes 手組み）。
    完了イベントを立てるだけ。寿命は Python 側で握るため AddRef/Release はダミー。"""

    _QI = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
    _REF = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
    _DONE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)

    def __init__(self):
        self.done = threading.Event()

        def qi(this, riid, ppv):
            iid = riid.contents
            if (_same_guid(iid, IID_IUnknown)
                    or _same_guid(iid, IID_ICompletionHandler)
                    or _same_guid(iid, IID_IAgileObject)):
                ppv[0] = this
                return 0
            ppv[0] = None
            return E_NOINTERFACE

        def completed(this, op):
            self.done.set()
            return 0

        # GC されないようインスタンスに保持
        self._funcs = [self._QI(qi), self._REF(lambda t: 2),
                       self._REF(lambda t: 1), self._DONE(completed)]
        self._vtbl = (c_void_p * 4)(*[ctypes.cast(f, c_void_p)
                                      for f in self._funcs])
        self._box = (c_void_p * 1)(ctypes.cast(self._vtbl, c_void_p))
        self.ptr = ctypes.cast(self._box, c_void_p)


# ---------------- キャプチャ本体 ----------------

class ProcessLoopbackCapture:
    """指定プロセス（とその子孫）の出力音声を 16kHz mono float32 で取り込む。

    with ProcessLoopbackCapture("Discord.exe", on_audio=fn): ...
    on_audio(mono: np.ndarray) はキャプチャスレッドから呼ばれる。
    対象が無音の間もゼロ詰めで流し続ける（VADの発話確定を止めないため）。
    実行中のエラーは .error に入る（利用側がポーリングして検知）。
    """

    def __init__(self, target, on_audio):
        self._target = target          # exe名 or PID
        self._on_audio = on_audio
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._init_error = None
        self._thread = None
        self.error = None

    # -- context manager --
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        if not is_supported():
            raise RuntimeError(
                "この Windows はアプリ音声の直接取り込みに未対応です"
                "（Windows 10 2004 以降が必要）。仮想ケーブル方式をお使いください")
        pid = self._target
        if not isinstance(pid, int):
            pid = resolve_pid(str(self._target))
            if pid is None:
                raise RuntimeError(
                    f"{self._target} が見つかりません。アプリを起動してから開始してください")
        self._pid = pid
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._init_error:
            raise RuntimeError(self._init_error)
        if not self._ready.is_set():
            self._stop.set()
            raise RuntimeError("アプリ音声の取り込み開始がタイムアウトしました")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # -- capture thread --
    def _run(self):
        uninit = False
        audio_client = c_void_p()
        capture = c_void_p()
        event = None
        started = False
        try:
            uninit = _coinit()
            audio_client = self._activate(self._pid)

            # 16k mono を AUTOCONVERTPCM で直接要求 → だめなら 48k stereo で受けて自前変換
            fmt = None
            for rate, ch in ((SAMPLE_RATE, 1), (48000, 2)):
                f = _float_format(rate, ch)
                flags = (0x00020000     # LOOPBACK
                         | 0x00040000   # EVENTCALLBACK
                         | 0x80000000   # AUTOCONVERTPCM
                         | 0x08000000)  # SRC_DEFAULT_QUALITY
                hr = _com(audio_client, 3, c_uint, wintypes.DWORD, c_longlong,
                          c_longlong, POINTER(WAVEFORMATEX), c_void_p)(
                    0, flags, 2000000, 0, byref(f), None)   # shared / 200ms
                if hr >= 0:
                    fmt = f
                    break
            if fmt is None:
                raise OSError("IAudioClient.Initialize failed")

            event = _kernel32.CreateEventW(None, False, False, None)
            _check(_com(audio_client, 13, c_void_p)(event), "SetEventHandle")
            _check(_com(audio_client, 14, POINTER(GUID), POINTER(c_void_p))(
                byref(IID_IAudioCaptureClient), byref(capture)), "GetService")
            _check(_com(audio_client, 10)(), "Start")
            started = True
            self._ready.set()

            get_next = _com(capture, 5, POINTER(c_uint))
            get_buf = _com(capture, 3, POINTER(c_void_p), POINTER(c_uint),
                           POINTER(wintypes.DWORD), c_void_p, c_void_p)
            rel_buf = _com(capture, 4, c_uint)
            silence_block = np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32)

            while not self._stop.is_set():
                r = _kernel32.WaitForSingleObject(event, 100)
                if r != 0:                       # タイムアウト＝対象が無音
                    self._emit(silence_block)    # ゼロ詰めで VAD を進める
                    continue
                while True:
                    n = c_uint()
                    if get_next(byref(n)) < 0 or n.value == 0:
                        break
                    data = c_void_p()
                    frames = c_uint()
                    dflags = wintypes.DWORD()
                    if get_buf(byref(data), byref(frames), byref(dflags),
                               None, None) < 0:
                        break
                    try:
                        nf = frames.value
                        if nf:
                            if dflags.value & 2:     # SILENT
                                mono = np.zeros(nf, dtype=np.float32)
                            else:
                                buf = ctypes.string_at(
                                    data.value, nf * fmt.nBlockAlign)
                                a = np.frombuffer(buf, dtype=np.float32)
                                mono = (a.reshape(-1, fmt.nChannels).mean(axis=1)
                                        if fmt.nChannels > 1 else a.copy())
                            if fmt.nSamplesPerSec != SAMPLE_RATE:
                                mono = self._resample(mono, fmt.nSamplesPerSec)
                            self._emit(mono)
                    finally:
                        rel_buf(frames.value)
        except Exception as e:
            if self._ready.is_set():
                self.error = f"アプリ音声の取り込みが停止しました: {e}"
            else:
                self._init_error = str(e)
                self._ready.set()
        finally:
            if started:
                try:
                    _com(audio_client, 11)()     # Stop
                except Exception:
                    pass
            _release(capture)
            _release(audio_client)
            if event:
                _kernel32.CloseHandle(event)
            if uninit:
                _ole32.CoUninitialize()

    def _emit(self, mono):
        try:
            self._on_audio(mono)
        except Exception:
            pass

    _res_carry = None

    def _resample(self, mono, src_rate):
        """簡易ダウンサンプル（48k→16k 前提の整数分周・平均でエイリアス軽減）"""
        step = src_rate // SAMPLE_RATE
        if step <= 1:
            return mono
        if self._res_carry is not None and len(self._res_carry):
            mono = np.concatenate([self._res_carry, mono])
        n = (len(mono) // step) * step
        self._res_carry = mono[n:]
        if n == 0:
            return np.empty(0, dtype=np.float32)
        return mono[:n].reshape(-1, step).mean(axis=1).astype(np.float32)

    def _activate(self, pid):
        """ActivateAudioInterfaceAsync でプロセスループバックの IAudioClient を得る"""
        params = _ACTIVATION_PARAMS(1, pid, 0)   # PROCESS_LOOPBACK / TARGET_TREE
        pv = _PROPVARIANT()
        pv.vt = 65                               # VT_BLOB
        pv.blob.cbSize = ctypes.sizeof(params)
        pv.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()
        op = c_void_p()
        mmdev = ctypes.WinDLL("mmdevapi")
        mmdev.ActivateAudioInterfaceAsync.argtypes = [
            wintypes.LPCWSTR, POINTER(GUID), POINTER(_PROPVARIANT),
            c_void_p, POINTER(c_void_p)]
        mmdev.ActivateAudioInterfaceAsync.restype = HRESULT
        _check(mmdev.ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient),
            byref(pv), handler.ptr, byref(op)), "ActivateAudioInterfaceAsync")
        try:
            if not handler.done.wait(timeout=5.0):
                raise OSError("ActivateAudioInterfaceAsync timeout")
            hr_act = HRESULT()
            client = c_void_p()
            _check(_com(op, 3, POINTER(HRESULT), POINTER(c_void_p))(
                byref(hr_act), byref(client)), "GetActivateResult")
            _check(hr_act.value, "activation")
            return client
        finally:
            _release(op)


# ---------------- プロセス解決（toolhelp・COM不要） ----------------

def _snapshot_processes():
    """[(pid, ppid, exe名小文字), ...]"""
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = _kernel32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    if snap == -1:
        return []
    out = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = _kernel32.Process32FirstW(snap, byref(e))
        while ok:
            out.append((e.th32ProcessID, e.th32ParentProcessID,
                        e.szExeFile.lower()))
            ok = _kernel32.Process32NextW(snap, byref(e))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def resolve_pid(exe_name):
    """exe名 → キャプチャ対象PID。同名プロセス群の root（親が同名でないもの）を返す。
    TARGET_PROCESS_TREE でキャプチャするので root を掴めば子（音声プロセス）も入る。"""
    name = exe_name.lower()
    procs = _snapshot_processes()
    same = {pid for pid, _pp, ex in procs if ex == name}
    if not same:
        return None
    roots = [pid for pid, pp, ex in procs if ex == name and pp not in same]
    return roots[0] if roots else next(iter(same))


def _pid_to_exe(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(h)


# ---------------- 音声セッションを持つアプリの列挙（UIの選択肢用） ----------------

def list_audio_apps():
    """全ての再生デバイスの音声セッションから [(exe名, 再生中か)] を集める。
    戻り値: [{"name": "Discord.exe", "active": True}, ...]（active優先・名前順）"""
    if not is_supported():
        return []
    uninit = _coinit()
    apps = {}     # exe名 → active
    enum = c_void_p()
    try:
        _check(_ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, 1,   # CLSCTX_INPROC_SERVER
            byref(IID_IMMDeviceEnumerator), byref(enum)), "CoCreateInstance")
        coll = c_void_p()
        # eRender=0 / DEVICE_STATE_ACTIVE=1
        _check(_com(enum, 3, c_uint, wintypes.DWORD, POINTER(c_void_p))(
            0, 1, byref(coll)), "EnumAudioEndpoints")
        try:
            count = c_uint()
            _com(coll, 3, POINTER(c_uint))(byref(count))
            for i in range(count.value):
                dev = c_void_p()
                if _com(coll, 4, c_uint, POINTER(c_void_p))(i, byref(dev)) < 0:
                    continue
                try:
                    _collect_sessions(dev, apps)
                finally:
                    _release(dev)
        finally:
            _release(coll)
    except OSError:
        pass
    finally:
        _release(enum)
        if uninit:
            _ole32.CoUninitialize()
    return [{"name": n, "active": a} for n, a in
            sorted(apps.items(), key=lambda kv: (not kv[1], kv[0].lower()))]


def _collect_sessions(dev, apps):
    """1つの再生デバイスのセッションを apps（exe名→active）へマージ"""
    mgr = c_void_p()
    if _com(dev, 3, POINTER(GUID), wintypes.DWORD, c_void_p,
            POINTER(c_void_p))(byref(IID_IAudioSessionManager2), 1, None,
                               byref(mgr)) < 0:
        return
    sess_enum = c_void_p()
    try:
        if _com(mgr, 5, POINTER(c_void_p))(byref(sess_enum)) < 0:
            return
        count = c_uint()
        _com(sess_enum, 3, POINTER(c_uint))(byref(count))
        for i in range(count.value):
            ctl = c_void_p()
            if _com(sess_enum, 4, c_uint, POINTER(c_void_p))(
                    i, byref(ctl)) < 0:
                continue
            try:
                ctl2 = c_void_p()
                if _com(ctl, 0, POINTER(GUID), POINTER(c_void_p))(
                        byref(IID_IAudioSessionControl2), byref(ctl2)) < 0:
                    continue
                try:
                    if _com(ctl2, 15)() == 0:     # IsSystemSoundsSession → S_OK
                        continue
                    pid = wintypes.DWORD()
                    if _com(ctl2, 14, POINTER(wintypes.DWORD))(
                            byref(pid)) < 0 or not pid.value:
                        continue
                    exe = _pid_to_exe(pid.value)
                    if not exe or exe.lower() == "mojicast.exe":
                        continue                   # 自分自身は候補に出さない
                    state = c_uint()
                    _com(ctl2, 3, POINTER(c_uint))(byref(state))
                    active = state.value == 1      # AudioSessionStateActive
                    apps[exe] = apps.get(exe, False) or active
                finally:
                    _release(ctl2)
            finally:
                _release(ctl)
    finally:
        _release(sess_enum)
        _release(mgr)
