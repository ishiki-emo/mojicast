"""ガイドHTMLのビルド（開発ツール・配布物ではない）

docs/STYLE_GUIDE.md / docs/EFFECT_GUIDE.md を、マニュアル.html と同じ見た目の
単一HTMLへ変換し、リポジトリ直下へ スタイルガイド.html / エフェクトガイド.html
として書き出す。画像は base64 で埋め込み（1ファイルで配布可能）。

使い方:
    reazonspeech-env\\Scripts\\python.exe tools\\manual\\build_guides.py

前提: pip install markdown（開発環境のみ）。
Markdown が原本。HTML を直接編集しないこと（次のビルドで消える）。
"""
import base64
import os
import re

import markdown
from markdown.extensions.toc import TocExtension

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(ROOT, "docs")
OUT_DIR = os.path.join(ROOT, "ガイド")   # 入口のマニュアル.htmlだけルート、ガイドはここへ

GUIDES = [
    ("STYLE_GUIDE.md", "スタイルガイド.html", "スタイル・レイアウト作成ガイド",
     "自分だけの字幕デザインを作る — 目安とコツ集"),
    ("EFFECT_GUIDE.md", "エフェクトガイド.html", "エフェクトガイド",
     "単語単位の演出を作る — アニメ13種・パーティクル6種"),
]

# .md 間の相互リンク → 同梱HTMLのパスへ（ガイド同士は同フォルダ、マニュアルは1つ上）
LINK_MAP = {
    "STYLE_GUIDE.md": "スタイルガイド.html",
    "EFFECT_GUIDE.md": "エフェクトガイド.html",
    "MANUAL.md": "../マニュアル.html",
}

CSS = """
  :root {
    --bg: #0d1117; --panel: #161c26; --line: #2a3342;
    --fg: #e6edf3; --dim: #8b98a9; --acc: #71e7fe; --warn: #ffd400;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--fg);
    font-family: "Yu Gothic UI", "Meiryo", sans-serif;
    line-height: 1.8; font-size: 15px;
  }
  .wrap { max-width: 880px; margin: 0 auto; padding: 32px 20px 80px; }
  header { text-align: center; padding: 28px 0 8px; }
  header h1 { font-size: 26px; letter-spacing: .04em; }
  header h1 .accent { color: var(--acc); }
  header .sub { color: var(--dim); margin-top: 6px; font-size: 13px; }
  nav.toc {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 16px 22px; margin: 26px 0;
  }
  nav.toc b { color: var(--dim); font-size: 12px; letter-spacing: .1em; }
  nav.toc ol { margin: 8px 0 0 22px; }
  a { color: var(--acc); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h2 {
    font-size: 20px; padding-bottom: 8px; margin: 46px 0 16px;
    border-bottom: 2px solid var(--line);
  }
  h3 { font-size: 16px; margin: 24px 0 8px; color: var(--acc); }
  h4 { font-size: 15px; margin: 18px 0 6px; }
  p { margin: 8px 0; }
  ul, ol { margin: 8px 0 8px 24px; }
  li { margin: 4px 0; }
  code {
    background: #1f2733; border: 1px solid var(--line); border-radius: 5px;
    padding: 1px 7px; font-family: Consolas, monospace; font-size: 13px; color: #9fd8ff;
  }
  blockquote {
    background: var(--panel); border: 1px solid var(--line);
    border-left: 4px solid var(--warn); border-radius: 10px;
    padding: 10px 18px; margin: 12px 0;
  }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
  th, td { border: 1px solid var(--line); padding: 8px 12px; text-align: left; vertical-align: top; }
  th { background: #1a212d; color: var(--dim); font-weight: 600; }
  img {
    max-width: 100%; height: auto; margin: 8px 0;
    border: 1px solid var(--line); border-radius: 10px;
  }
  footer { margin-top: 60px; text-align: center; color: var(--dim); font-size: 12px; }
"""

PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mojicast {title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Moji<span class="accent">cast</span> {title}</h1>
  <div class="sub">{sub}</div>
</header>
{toc}
{body}
<footer>Mojicast — このHTMLは docs/{src} から生成されています</footer>
</div>
</body>
</html>
"""


def gh_slugify(value, separator):
    """GitHub風スラッグ（docs/*.md の #アンカー表記と一致させる）"""
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"\s", separator, value.strip())


def embed_images(html):
    def repl(m):
        path = os.path.join(DOCS, m.group(1).replace("/", os.sep))
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'src="data:{mime};base64,{b64}"'
    return re.sub(r'src="(images/[^"]+)"', repl, html)


def build_toc(md):
    strip_no = lambda name: re.sub(r"^\d+\. ", "", name)
    items = "".join(
        f'<li><a href="#{t["id"]}">{strip_no(t["name"])}</a></li>'
        for t in md.toc_tokens)   # toc_depth=2-2 なので全トークンがH2（olが番号を振る）
    if not items:
        return ""
    return f"<nav class=\"toc\"><b>目次</b><ol>{items}</ol></nav>"


for src, out_name, title, sub in GUIDES:
    with open(os.path.join(DOCS, src), encoding="utf-8") as f:
        text = f.read()

    # 先頭のH1はページヘッダーで表示するので本文からは除く
    text = re.sub(r"^# .+?\n", "", text, count=1)

    md = markdown.Markdown(
        extensions=["tables", TocExtension(slugify=gh_slugify, toc_depth="2-2")])
    body = md.convert(text)

    # 相互リンクを同梱HTML名へ差し替え（アンカー付きも対応）
    for mdname, htmlname in LINK_MAP.items():
        body = body.replace(f'href="{mdname}', f'href="{htmlname}')

    # 内部アンカーの検証（リンク切れは警告）
    ids = set(re.findall(r'id="([^"]+)"', body))
    for anchor in re.findall(r'href="#([^"]+)"', body):
        if anchor not in ids:
            print(f"[注意] {src}: リンク切れアンカー #{anchor}")

    html = PAGE.format(title=title, sub=sub, css=CSS,
                       toc=build_toc(md), body=embed_images(body), src=src)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK: {out} ({os.path.getsize(out)//1024} KB)")
