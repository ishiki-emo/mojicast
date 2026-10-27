# Mojicast MCP サーバ（試作）

Claude Code などの AI から、Mojicast の字幕デザインとレイアウトを言葉で調整するための MCP サーバです。
起動中の Mojicast の HTTP API を中継するだけなので、Mojicast 本体には何も追加しません。

## 役割分担

| やりたいこと | 使う MCP |
|---|---|
| 字幕レイヤー全体（ブラウザソース）の位置・大きさ | OBS MCP |
| 仕上がりの確認（スクリーンショット） | OBS MCP |
| レイヤーの中での字幕枠の位置・背景・行数 | Mojicast MCP（`update_layout`） |
| フォント・色・縁取り・光彩・登場アニメ | Mojicast MCP（`update_design`） |
| 話さずに字幕を1行出す | Mojicast MCP（`show_sample_caption`） |
| 単語のエフェクト（色・動き・パーティクル） | Mojicast MCP（`set_word_effect`） |
| 認識辞書・伏せ字・固定訳への登録 | Mojicast MCP（`add_words` / `remove_words`） |
| 配信ログから誤認識を探す | Mojicast MCP（`read_recent_logs`） |

## 登録

[uv](https://docs.astral.sh/uv/) が必要です。依存（`mcp<2`）は uv が自動で用意します。

```
claude mcp add mojicast -s user -- uv run --script <Mojicast のフォルダ>\mcp\mojicast_mcp.py
```

`<Mojicast のフォルダ>` は Mojicast.exe があるフォルダです（開発版では `tools\mcp\mojicast_mcp.py`）。

Mojicast のポートを 8765 から変えている場合は、`-e MOJICAST_PORT=ポート番号` を付けてください。
登録後は Claude Code を開き直すと使えます。

## 頼み方の例

- 「字幕をアバターに被らないように、画面の下の左寄りに帯で出して」
- 「フォントをもう少し丸くて可愛い感じに。縁取りはピンクで」
- 「さっきの方がよかった、戻して」
- 「『癒色えも』に派手なエフェクトをつけて」
- 「昨日の配信ログを見て、名前の誤認識があれば登録候補を出して」

## 注意

- 変更は配信画面に**すぐ反映**されます。配信中に試すときは気をつけてください。
- `undo` で戻せるのは、この MCP サーバが起動している間の変更だけです。
- 調整中は Mojicast の「字幕デザイン」編集窓を閉じてください。開いたまま保存すると、その窓の内容で上書きされます。
- 認識辞書・伏せ字・固定訳は、認識を再起動するまで効きません。MCP からは再起動しません（配信中に止まらないように）。単語エフェクトはすぐ効きます。
- 単語の登録中は「単語」の編集窓も閉じてください（開いたまま保存すると上書きされます）。
- 同梱のデザインを残したいときは、`duplicate` で複製してから調整するよう頼んでください。
