"""OS依存処理の集約（Windows / macOS）

Windows専用API（winreg / GDI / windll / os.startfile / WASAPI）に触れてよいのは
このモジュールと proc_loopback.py だけ。他モジュールはここの関数を経由することで
OSを意識せず動く（tests/test_platform_isolation.py がこの約束を機械的に監視する）。

macOS側の実装は pywebview が依存する pyobjc（AppKit / Foundation）を利用する。
"""
import subprocess
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def collab_supported() -> bool:
    """1対1コラボが使えるOSか。

    実装が WASAPI プロセスループバック（proc_loopback.py・純Windows）のため
    Windows 限定。mac は将来 BlackHole 等の仮想デバイス経路で対応予定
    （ROADMAP 10章）。UI はこの値でコラボ欄をグレーアウトし、サーバ側も
    config への collab=True 書き込みを拒否する（多重ガード）。"""
    return IS_WIN


def ui_scale() -> float:
    """起動モニタの論理px幅からGUI窓の拡大率を決める（overlayには効かせない）。

    論理pxの相場がOSで違うため分母は別々:
    - Windows: QHD(2560)以上=等倍、FullHDで約0.8、下限0.75
    - macOS: Retina論理幅は1280〜1728程度が相場なので1600を基準に緩く縮小
    """
    try:
        if IS_WIN:
            import ctypes
            w = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN（論理px）
            return max(0.75, min(1.0, round(w / 2400, 2)))
        if IS_MAC:
            from AppKit import NSScreen
            w = int(NSScreen.mainScreen().frame().size.width)
            return max(0.75, min(1.0, round(w / 1600, 2)))
    except Exception:
        pass
    return 1.0


def fatal_dialog(msg: str):
    """起動不能エラーの通知。windowed実行ではコンソールが無いのでOSのダイアログで出す。
    表示に失敗しても例外は投げない（呼び元が print へフォールバックできるよう False を返す）。"""
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "Mojicast", 0x10)  # MB_ICONERROR
            return True
        if IS_MAC:
            safe = msg.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'display alert "Mojicast" message "{safe}" as critical'],
                check=True, capture_output=True, timeout=60)
            return True
    except Exception:
        pass
    return False


def os_ui_lang() -> str:
    """OSの表示言語 → 'ja'/'zh'/'en'/'ko'/'other'（初回のおすすめ設定用）"""
    try:
        if IS_WIN:
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF
            return {0x11: "ja", 0x04: "zh", 0x09: "en", 0x12: "ko"}.get(lid, "other")
        if IS_MAC:
            from Foundation import NSLocale
            langs = NSLocale.preferredLanguages()
            head = str(langs[0])[:2] if langs else ""
            return head if head in ("ja", "zh", "en", "ko") else "other"
    except Exception:
        pass
    return "other"


def cpu_name() -> str:
    """CPUのモデル名（初回のおすすめ設定の適性判定用）。取得不能時は空文字。"""
    try:
        if IS_WIN:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        if IS_MAC:
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, timeout=5)
            return out.stdout.strip()   # 例: "Apple M4"
    except Exception:
        pass
    return ""


def list_system_fonts() -> list:
    """インストール済みフォントのファミリー名一覧（キャッシュは呼び元が持つ）"""
    if IS_WIN:
        return _list_fonts_gdi()
    if IS_MAC:
        return _list_fonts_coretext()
    return []


def _list_fonts_gdi() -> list:
    """Windows GDI の EnumFontFamiliesExW で列挙"""
    import ctypes
    from ctypes import wintypes

    class LOGFONTW(ctypes.Structure):
        _fields_ = [
            ("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG),
            ("lfEscapement", wintypes.LONG), ("lfOrientation", wintypes.LONG),
            ("lfWeight", wintypes.LONG), ("lfItalic", ctypes.c_byte),
            ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
            ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
            ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
            ("lfPitchAndFamily", ctypes.c_byte),
            ("lfFaceName", ctypes.c_wchar * 32),
        ]

    gdi32 = ctypes.WinDLL("gdi32")
    user32 = ctypes.WinDLL("user32")
    names = set()
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LOGFONTW),
                              ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM)

    def cb(lf, tm, ftype, lparam):
        name = lf.contents.lfFaceName
        if name and not name.startswith("@"):   # @付きは縦書き用
            names.add(name)
        return 1

    hdc = user32.GetDC(0)
    lf = LOGFONTW()
    lf.lfCharSet = 1  # DEFAULT_CHARSET: 全キャラセットを列挙
    gdi32.EnumFontFamiliesExW(hdc, ctypes.byref(lf), PROC(cb), 0, 0)
    user32.ReleaseDC(0, hdc)
    return sorted(names)


def _list_fonts_coretext() -> list:
    """macOS の NSFontManager でファミリー名を列挙"""
    from AppKit import NSFontManager
    fams = NSFontManager.sharedFontManager().availableFontFamilies()
    # 先頭 "." はシステム内部フォント（UI選択肢に出さない）
    return sorted(str(f) for f in fams if not str(f).startswith("."))


def open_folder(path: str):
    """OSのファイラでフォルダを開く。失敗時は OSError（呼び元の except OSError を維持）"""
    if IS_WIN:
        import os
        os.startfile(path)   # エクスプローラで開く
        return
    if IS_MAC:
        r = subprocess.run(["open", path], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or f"open failed: {path}")
        return
    raise OSError(f"unsupported platform: {sys.platform}")
