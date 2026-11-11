"""Stream Deck プラグインの manifest・アイコン・設定画面（PI）を1つの表から作る。

    python tools/streamdeck/scripts/make_assets.py

ボタンを足す・名前を変えるときは ACTIONS を直して実行し直す。
キー画像はロゴのダーク版の色（紺の地・シアンの点灯・ピンクの演出）の線画。
多言語で配るので絵に文字は入れない（名前は Stream Deck のタイトル欄に出す）。
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

NAVY, CYAN, PINK, WHITE = "#03102d", "#00e5ff", "#fd187b", "#FFFFFF"
ON_BG, OFF_GLYPH = "#062638", "#9aa7bb"

# 線画は 144x144 の上寄り（おおむね y=18〜98）に収め、下はタイトル用に空ける。
# 線の太さ・色は描く側で付ける。{bg} {fg} は描くときに地の色・線の色に置き換わる
BUBBLE = ("M34 28h76a10 10 0 0 1 10 10v36a10 10 0 0 1-10 10H70l-16 14v-14H34"
          "a10 10 0 0 1-10-10V38a10 10 0 0 1 10-10z")
BUBBLE_LINES = f'<path d="{BUBBLE}"/><path d="M44 48h56M44 64h36"/>'
MIC = ('<rect x="58" y="22" width="28" height="44" rx="14"/>'
       '<path d="M46 54a26 26 0 0 0 52 0M72 80v12M60 94h24"/>')
SPARK = '<path d="M64 30c4 20 8 25 28 29-20 4-24 9-28 29-4-20-8-25-28-29 20-4 24-9 28-29z" fill="{fg}" stroke="none"/>'
SPARK_S = '<path d="M106 18c2 9 4 11 12 13-8 2-10 4-12 13-2-9-4-11-12-13 8-2 10-4 12-13z" fill="{fg}" stroke="none"/>'
GOGGLES = ('<path d="M32 42h80a10 10 0 0 1 10 10v22a10 10 0 0 1-10 10H92l-10-12H62l-10 12H32'
           'a10 10 0 0 1-10-10V52a10 10 0 0 1 10-10z"/>')
# カウンター: 正の字のような集計の線（4本＋斜線）
TALLY = '<path d="M40 30v56M56 30v56M72 30v56M88 30v56M28 76l76-38"/>'
SWAP = (f'<g stroke="{PINK}"><path d="M95 28a14 14 0 0 1 24 4M121 40a14 14 0 0 1-24-4"/>'
        '<path d="M119 20v12h-12M97 52V40h12"/></g>')

# kind: toggle（OFF/ON の2状態）/ select（未使用/使用中の2状態・名前はプラグインが下に出す）
#       plain（1状態）/ pair（2状態とも普通の見た目。どちらも OFF ではない）
# icons: 状態ごとの線画（1つなら全状態共通）。accent: plain/pair の線の色
# pi: 設定画面の種類（select=プルダウン / random=対象の選択 / counter=カウンターと押したときの動き /
#     none=接続設定だけ）
ACTIONS = [
    dict(id="engine", name="認識 スタート/ストップ", tip="音声認識を開始・停止します",
         kind="toggle", pi="none", icons=(
             # 停止中: 斜線入りのマイク（斜線の下は地の色で抜く）
             MIC + '<path d="M42 26l60 66" stroke="{bg}" stroke-width="16"/><path d="M42 26l60 66"/>',
             # 認識中: マイク＋音の波
             MIC + '<path d="M30 36v22M114 36v22" stroke-width="6"/>')),
    dict(id="clear", name="字幕クリア", tip="画面の字幕を消します",
         kind="plain", pi="none", icons=(f'<path d="{BUBBLE}"/><path d="M61 45l22 22M83 45l-22 22"/>',)),
    dict(id="scene", name="シーン切替", tip="指定したシーン（デザイン＋レイアウト）に切り替えます",
         kind="select", pi="select", setting="id", label="シーン", icons=(
             '<path d="M72 24l34 16-34 16-34-16z"/><path d="M38 58l34 16 34-16"/><path d="M38 76l34 16 34-16"/>',)),
    dict(id="preset", name="デザイン切替", tip="指定した字幕デザインに切り替えます",
         kind="select", pi="select", setting="id", label="デザイン", icons=(
             '<path d="M40 30h52M66 30v60"/><circle cx="102" cy="78" r="12" fill="{fg}" stroke="none"/>',)),
    dict(id="box", name="レイアウト切替", tip="指定したレイアウトに切り替えます",
         kind="select", pi="select", setting="id", label="レイアウト", icons=(
             '<rect x="26" y="24" width="92" height="68" rx="10"/><path d="M44 74h56"/>',)),
    dict(id="random", name="おまかせ切替", tip="シーン・デザイン・レイアウトをおまかせで切り替えます",
         kind="plain", pi="random", icons=(
             '<path d="M28 38h16c22 0 30 40 52 40h16M28 78h16c22 0 30-40 52-40h16"/>'
             '<path d="M104 28l10 10-10 10M104 68l10 10-10 10"/>',)),
    dict(id="fx", name="演出を出す", tip="笑い・拍手などの演出を手動で出します",
         kind="plain", accent=PINK, pi="select", setting="group", label="演出",
         note="演出の画像・位置は Mojicast の［字幕スタジオ］→［リアクション演出］で設定します。音の自動検出がOFFでも出せます。",
         icons=(SPARK + SPARK_S,)),
    dict(id="counter", name="カウンター", tip="カウンターを＋1・−1・0に戻します（ボタンに今の数を出します）",
         kind="plain", accent=CYAN, pi="counter", setting="id", label="カウンター",
         note="カウンターは Mojicast の［字幕スタジオ］→［カウンター］で作ります。ボタンには今の数が出ます。",
         icons=(TALLY,)),
    dict(id="word-fx", name="単語エフェクト ON/OFF", tip="単語エフェクトの表示をオン・オフします",
         kind="toggle", pi="none", icons=(
             '<rect x="22" y="50" width="76" height="36" rx="18"/><path d="M40 68h40"/>' + SPARK_S,)),
    dict(id="vrchat", name="VRChat転送 ON/OFF", tip="VRChatのチャットボックスへの転送をオン・オフします",
         kind="toggle", pi="none", icons=(GOGGLES,)),
    dict(id="vrchat-source", name="VRChat送信内容の切替", tip="VRChatへ送る内容（文字起こし／訳文）を切り替えます",
         kind="pair", pi="none", icons=(
             # 文字起こし: 吹き出しに2行
             BUBBLE_LINES,
             # 訳文: 吹き出し＋ロゴの入れ替え矢印
             f'<g transform="translate(-10 18) scale(.78)">{BUBBLE_LINES}</g>' + SWAP)),
]


def key_svg(icon, mode, accent=WHITE):
    """キー画像（144x144）。mode: off / on（地を少し明るく・縁と線をシアン）/ plain"""
    bg = ON_BG if mode == "on" else NAVY
    fg = {"off": OFF_GLYPH, "on": CYAN}.get(mode, accent)
    ring = (f'<rect x="5" y="5" width="134" height="134" rx="18" fill="none" stroke="{CYAN}" stroke-width="6"/>'
            if mode == "on" else "")
    body = icon.replace("{bg}", bg).replace("{fg}", fg)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">'
            f'<rect width="144" height="144" fill="{bg}"/>{ring}'
            f'<g fill="none" stroke="{fg}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</g></svg>')


def list_svg(icon, size):
    """アクション一覧・カテゴリ用の白一色（透明背景）。決まりで色付き・地の塗りは使えない"""
    body = icon.replace("{bg}", "none").replace("{fg}", WHITE).replace(PINK, WHITE)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="18 10 108 108">'
            f'<g fill="none" stroke="{WHITE}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</g></svg>')


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
    elif a["pi"] == "counter":
        body.append(f"""
  <sdpi-item label="{a['label']}">
    <sdpi-select setting="{a['setting']}" datasource="getChoices" hot-reload placeholder="選んでください"></sdpi-select>
  </sdpi-item>
  <sdpi-item label="押したとき">
    <sdpi-select setting="op" default="add">
      <option value="add">＋1</option>
      <option value="sub">−1</option>
      <option value="reset">0に戻す</option>
    </sdpi-select>
  </sdpi-item>
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
        icons = a["icons"]
        modes = {"plain": ["plain"], "pair": ["plain", "plain"]}.get(a["kind"], ["off", "on"])
        states = []
        for i, mode in enumerate(modes):
            icon = icons[min(i, len(icons) - 1)]
            write(os.path.join(PLUGIN, f"{base}/key{i}.svg"), key_svg(icon, mode, a.get("accent", WHITE)))
            states.append({"Image": f"{base}/key{i}", "TitleAlignment": "bottom", "FontSize": 11, "ShowTitle": True})
        write(os.path.join(PLUGIN, f"{base}/icon.svg"), list_svg(icons[-1], 40))
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
            "UserTitleEnabled": True,
            "States": states,
        })

    write(os.path.join(PLUGIN, "imgs/plugin/category-icon.svg"), list_svg(BUBBLE_LINES, 56))
    src = Image.open(os.path.join(REPO, "assets", "branding", "mojicast-mo-icon-light.png")).convert("RGBA")
    for name, size in (("marketplace", 256), ("marketplace@2x", 512)):
        src.resize((size, size), Image.LANCZOS).save(os.path.join(PLUGIN, f"imgs/plugin/{name}.png"))

    manifest = {
        "$schema": "https://schemas.elgato.com/streamdeck/plugins/manifest.json",
        "Name": "Mojicast",
        "Version": VERSION,
        "Author": "癒色えも",
        "Description": "Mojicast（配信用のリアルタイム字幕アプリ）をボタンで操作します。認識の開始・停止、字幕クリア、シーン・デザイン・レイアウトの切替、演出の手動発火、カウンターなど。",
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
