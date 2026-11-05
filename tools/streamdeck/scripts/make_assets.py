"""Stream Deck プラグインの manifest・アイコン・設定画面（PI）を1つの表から作る。

    python tools/streamdeck/scripts/make_assets.py

ボタンを足す・名前を変えるときは ACTIONS を直して実行し直す。
アイコンは暫定デザイン（色付きの角丸に1文字）。本番の絵に差し替えるときは
imgs/ 以下を上書きし、このスクリプトのアイコン生成を止める。
"""
import json
import os
from xml.sax.saxutils import escape

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "com.ishiki-emo.mojicast.sdPlugin")
REPO = os.path.dirname(os.path.dirname(ROOT))
UUID = "com.ishiki-emo.mojicast"
VERSION = "0.1.0.0"

OFF_BG, OFF_FG = "#2a2833", "#8a8796"
PLAIN_BG = "#34303f"
RED, PURPLE, BLUE, AMBER, GREEN = "#e5484d", "#8b5cf6", "#3b82f6", "#f59e0b", "#22c55e"

# kind: toggle（状態2つ・文言付き）/ select（状態2つ・名前はプラグインが下に出す）/ plain（状態1つ）
# pi: 設定画面の種類（select=プルダウン / random=対象の選択 / none=接続設定だけ）
ACTIONS = [
    dict(id="engine", name="認識 スタート/ストップ", tip="音声認識を開始・停止します",
         kind="toggle", glyph=("●", "●"), caption=("停止中", "認識中"), color=RED, pi="none"),
    dict(id="clear", name="字幕クリア", tip="画面の字幕を消します",
         kind="plain", glyph=("消",), caption=("クリア",), color=PLAIN_BG, pi="none"),
    dict(id="scene", name="シーン切替", tip="指定したシーン（デザイン＋レイアウト）に切り替えます",
         kind="select", glyph=("場", "場"), color=PURPLE, pi="select", setting="id", label="シーン"),
    dict(id="preset", name="デザイン切替", tip="指定した字幕デザインに切り替えます",
         kind="select", glyph=("字", "字"), color=PURPLE, pi="select", setting="id", label="デザイン"),
    dict(id="box", name="レイアウト切替", tip="指定したレイアウトに切り替えます",
         kind="select", glyph=("枠", "枠"), color=PURPLE, pi="select", setting="id", label="レイアウト"),
    dict(id="random", name="おまかせ切替", tip="シーン・デザイン・レイアウトをおまかせで切り替えます",
         kind="plain", glyph=("？",), caption=("おまかせ",), color=PLAIN_BG, pi="random"),
    dict(id="translate", name="翻訳 ON/OFF", tip="翻訳をオン・オフします（認識中は数秒止まります）",
         kind="toggle", glyph=("訳", "訳"), caption=("翻訳OFF", "翻訳ON"), color=BLUE, pi="none",
         note="認識中に押すと、反映のため認識が数秒止まります。"),
    dict(id="translate-lang", name="翻訳先の切替", tip="翻訳先の言語を切り替えます（翻訳もオンになります）",
         kind="select", glyph=("A", "A"), color=BLUE, pi="select", setting="lang", label="翻訳先",
         note="翻訳もオンになります。認識中に押すと、反映のため認識が数秒止まります。"),
    dict(id="fx", name="演出を出す", tip="笑い・拍手などの演出を手動で出します",
         kind="plain", glyph=("★",), caption=None, color="#3a3020", fg="#f5c542", pi="select",
         setting="group", label="演出",
         note="演出の画像・位置は Mojicast の［音イベント演出］で設定します。音の自動検出がOFFでも出せます。"),
    dict(id="word-fx", name="単語エフェクト ON/OFF", tip="単語エフェクトの表示をオン・オフします",
         kind="toggle", glyph=("語", "語"), caption=("演出OFF", "演出ON"), color=AMBER, pi="none"),
    dict(id="vrchat", name="VRChat転送 ON/OFF", tip="VRChatのチャットボックスへの転送をオン・オフします",
         kind="toggle", glyph=("VRC", "VRC"), caption=("転送OFF", "転送ON"), color=GREEN, pi="none"),
    dict(id="vrchat-source", name="VRChat送信内容の切替", tip="VRChatへ送る内容（文字起こし／訳文）を切り替えます",
         kind="toggle", glyph=("原", "訳"), caption=("文字起こし", "訳文"), color=BLUE, pi="none",
         off_plain=True),
]

FONT = "'Yu Gothic UI','Hiragino Sans','Noto Sans JP',sans-serif"


def key_svg(glyph, caption, bg, fg):
    """キー画像（144x144 基準）。caption が無いときは下をプラグインの名前表示に空ける"""
    size = 64 if len(glyph) == 1 else 44
    gy = 70 if caption else 64
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">',
             f'<rect x="0" y="0" width="144" height="144" rx="20" fill="{bg}"/>',
             f'<text x="72" y="{gy}" font-family="{FONT}" font-size="{size}" font-weight="700" '
             f'fill="{fg}" text-anchor="middle" dominant-baseline="middle">{escape(glyph)}</text>']
    if caption:
        parts.append(f'<text x="72" y="120" font-family="{FONT}" font-size="22" font-weight="700" '
                     f'fill="{fg}" text-anchor="middle">{escape(caption)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def mono_svg(glyph, size):
    """アクション一覧・カテゴリ用の白一色アイコン（透明背景）"""
    fs = size * (0.8 if len(glyph) == 1 else 0.5)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<text x="{size / 2}" y="{size / 2}" font-family="{FONT}" font-size="{fs:.1f}" font-weight="700" '
            f'fill="#FFFFFF" text-anchor="middle" dominant-baseline="central">{escape(glyph)}</text></svg>')


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


CONNECTION = """
  <sdpi-item label="接続キー">
    <sdpi-password setting="key" global placeholder="Mojicastで発行したキー"></sdpi-password>
  </sdpi-item>
  <sdpi-item label="ポート">
    <sdpi-textfield setting="port" global placeholder="8765"></sdpi-textfield>
  </sdpi-item>
  <sdpi-item label="">
    <span class="hint">キーは Mojicast の［アプリ設定］→［AI連携］→［Stream Deckと接続する］で発行します。接続設定は全ボタン共通です。</span>
  </sdpi-item>"""


def pi_html(a):
    body = []
    if a["pi"] == "select":
        body.append(f"""
  <sdpi-item label="{a['label']}">
    <sdpi-select setting="{a['setting']}" datasource="getChoices" hot-reload placeholder="選んでください"></sdpi-select>
  </sdpi-item>""")
        if a["kind"] == "select" or a["id"] == "fx":
            body.append("""
  <sdpi-item label="名前">
    <sdpi-checkbox setting="hideName" label="ボタンに名前を出さない"></sdpi-checkbox>
  </sdpi-item>""")
    elif a["pi"] == "random":
        body.append("""
  <sdpi-item label="対象">
    <sdpi-select setting="target" default="scene">
      <option value="scene">シーン</option>
      <option value="preset">デザイン</option>
      <option value="box">レイアウト</option>
    </sdpi-select>
  </sdpi-item>""")
    if a.get("note"):
        body.append(f"""
  <sdpi-item label="">
    <span class="hint">{escape(a['note'])}</span>
  </sdpi-item>""")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>{escape(a['name'])}</title>
  <script src="sdpi-components.js"></script>
  <style>.hint {{ font-size: 9pt; opacity: .75; line-height: 1.5; }}</style>
</head>
<body>{''.join(body)}{CONNECTION}
</body>
</html>
"""


def main():
    actions = []
    for a in ACTIONS:
        base = f"imgs/actions/{a['id']}"
        fg_on = a.get("fg", "#FFFFFF")
        states = []
        n = 1 if a["kind"] == "plain" else 2
        for i in range(n):
            on = i == 1 or a["kind"] == "plain" or (a.get("off_plain") and i == 0)
            bg = a["color"] if on else OFF_BG
            if a.get("off_plain") and i == 0:
                bg = PLAIN_BG
            fg = fg_on if on else OFF_FG
            cap = a["caption"][i] if a.get("caption") else None
            write(os.path.join(PLUGIN, f"{base}/key{i}.svg"), key_svg(a["glyph"][i], cap, bg, fg))
            st = {"Image": f"{base}/key{i}"}
            if not cap:
                st.update({"TitleAlignment": "bottom", "FontSize": 11, "ShowTitle": True})
            else:
                st["ShowTitle"] = False
            states.append(st)
        write(os.path.join(PLUGIN, f"{base}/icon.svg"), mono_svg(a["glyph"][-1], 40))
        write(os.path.join(PLUGIN, f"ui/{a['id']}.html"), pi_html(a))
        actions.append({
            "Name": a["name"],
            "UUID": f"{UUID}.{a['id']}",
            "Icon": f"{base}/icon",
            "Tooltip": a["tip"],
            "PropertyInspectorPath": f"ui/{a['id']}.html",
            "Controllers": ["Keypad"],
            "DisableAutomaticStates": True,
            "SupportedInMultiActions": True,
            "UserTitleEnabled": a["kind"] != "toggle",
            "States": states,
        })

    write(os.path.join(PLUGIN, "imgs/plugin/category-icon.svg"), mono_svg("M", 56))
    src = Image.open(os.path.join(REPO, "assets", "branding", "mojicast-mo-icon-light.png")).convert("RGBA")
    for name, size in (("marketplace", 256), ("marketplace@2x", 512)):
        src.resize((size, size), Image.LANCZOS).save(os.path.join(PLUGIN, f"imgs/plugin/{name}.png"))

    manifest = {
        "$schema": "https://schemas.elgato.com/streamdeck/plugins/manifest.json",
        "Name": "Mojicast",
        "Version": VERSION,
        "Author": "癒色えも",
        "Description": "Mojicast（配信用のリアルタイム字幕アプリ）をボタンで操作します。認識の開始・停止、字幕クリア、シーン・デザイン・翻訳の切替、演出の手動発火など。",
        "UUID": UUID,
        "URL": "https://ishiki-emo.com/mojicast",
        "Icon": "imgs/plugin/marketplace",
        "Category": "Mojicast",
        "CategoryIcon": "imgs/plugin/category-icon",
        "CodePath": "bin/plugin.js",
        "SDKVersion": 3,
        "Software": {"MinimumVersion": "7.1"},
        "OS": [{"Platform": "windows", "MinimumVersion": "10"},
               {"Platform": "mac", "MinimumVersion": "12"}],
        "Nodejs": {"Version": "24"},
        "Actions": actions,
    }
    write(os.path.join(PLUGIN, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(actions)} actions written to {PLUGIN}")


if __name__ == "__main__":
    main()
