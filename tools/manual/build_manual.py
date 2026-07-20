"""マニュアルのビルド（開発ツール・配布物ではない）

shots/ のスクショを切り出し→base64化し、manual_template.html の
{{IMG:名前}} プレースホルダへ埋め込んで、リポジトリ直下の マニュアル.html を生成する。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\shoot.py        # 先に撮影
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\build_manual.py
"""
import base64
import io
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")
TEMPLATE = os.path.join(HERE, "manual_template.html")
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "マニュアル.html"))

# クローズアップの切り出し（元画像, (left, top, right, bottom)）
CROPS = {
    "cockpit_collab_right": ("cockpit_collab", (848, 70, 1160, 632)),
    "cockpit_chips": ("cockpit_solo", (16, 174, 842, 246)),
}


def load(name):
    return Image.open(os.path.join(SHOTS, name + ".png"))


def to_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


images = {}
for name in ("cockpit_guide", "cockpit_solo", "cockpit_collab",
             "collab_window", "studio_style", "studio_words",
             "overlay_collab"):
    images[name] = to_data_uri(load(name))
for name, (src, box) in CROPS.items():
    images[name] = to_data_uri(load(src).crop(box))

with open(TEMPLATE, encoding="utf-8") as f:
    html = f.read()

missing = []
for name, uri in images.items():
    ph = "{{IMG:" + name + "}}"
    if ph not in html:
        missing.append(name)
    html = html.replace(ph, uri)
if "{{IMG:" in html:
    raise SystemExit("未解決の画像プレースホルダが残っています")
if missing:
    print("[注意] テンプレート未使用の画像:", missing)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)
print(f"OK: {OUT} ({os.path.getsize(OUT)//1024} KB)")
