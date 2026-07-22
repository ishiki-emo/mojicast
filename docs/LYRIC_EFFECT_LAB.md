# リリック演出ラボ

`/ui/lyric_lab` は、リリックビデオ風字幕の演出を比較・調整するための独立デモです。本番オーバーレイには、ここで試作した12系統の表現を軽量化して組み込んでいます。

## 現行方式から変えるポイント

旧 `FX.lyricSpawn()` は、分割した語句をランダムな位置・サイズ・回転で置き、6種類の入場から選ぶ方式でした。短い時間では賑やかですが、画面全体の構図や退場の意味付けが弱くなりやすい構造でした。

ラボでは、以下をまとめて1つの「演出プリセット」として扱います。

- 画面構成: 中央、誌面、左右分割、縦書き、周回
- 文字表現: 塗り、アウトライン、グラデーション、残像、グリッチ
- 入場: フォーカス、ワイプ、文字送り、ラッシュ、インパクト
- 退場: ディゾルブ、帯抜け、分離、散開、収束、バースト
- 背景アクセント: グリッド、走査線、リング、Canvasパーティクル

## 収録プリセット

| プリセット | 主な用途 | 強度 | 主な技術 |
|---|---|---:|---|
| ソフトフォーカス | 静かな語り、バラード | 1 | blur、opacity |
| エディトリアル | 余白を活かしたAメロ | 2 | grid、clip-path |
| タイプカスケード | リズムの細かな歌詞 | 2 | 文字分割、stagger |
| 縦書きレイヤー | 和風、エモーショナル | 2 | writing-mode、clip-path |
| リボンワイプ | 歌詞を強く読みやすく出す | 3 | clip-path、transform |
| ダイアゴナルラッシュ | 疾走感のあるパート | 3 | skew、単語分割 |
| スプリットスクリーン | 掛け合い、対比 | 3 | 2面レイアウト、wipe |
| オービット | 浮遊感、転調 | 3 | 円形配置、transform |
| エコートレイル | 余韻、リバーブ感 | 3 | clone、mix-blend-mode |
| グラデーション走査 | 歌唱進行の強調 | 3 | background-clip、clip-path |
| グリッチカット | デジタル系アクセント | 4 | filter、blend、steps |
| コーラスインパクト | サビ、決め台詞 | 4 | scale、flash、Canvas |

## シーケンス方式

- 手動: 選択したプリセットだけを比較する
- ランダム: シード付き疑似乱数。同じシードなら同じ順番を再現する
- 静 → サビ: 強度1から4へ段階的に上げる
- 緩急: 弱い演出と強い演出を交互に配置する

本番の雑談向けモードは、文章の長さ・区切り・直前の演出・強い演出のクールダウンを入力にしたルール方式を基本にし、条件に合う候補の中からランダム選択します。完全ランダムで同じ演出や強い演出が連続することを防ぎます。

## 実装方針

- DOM文字は CSS transforms / opacity / filter / clip-path と Web Animations API で動かす
- Canvas は背景アクセントだけに限定し、文字の読みやすさと編集性を保つ
- 外部ライブラリ・CDN・ネットワーク素材に依存しない
- WebView2 Runtimeの更新差に備え、追加機能は `CSS.supports()` 等で機能検出する
- `prefers-reduced-motion` が有効な環境では時間と移動量を抑える
- 1つのWebView内で描画を完結し、演出ごとにWebViewを増やさない

## 参考にしたWeb標準資料

- Microsoft: WebView2 distribution / Runtime behavior
  - https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
- Microsoft: WebView2 browser features
  - https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/browser-features
- MDN: Web Animations API
  - https://developer.mozilla.org/en-US/docs/Web/API/Web_Animations_API
- MDN: Keyframe formats
  - https://developer.mozilla.org/en-US/docs/Web/API/Web_Animations_API/Keyframe_Formats
- MDN: clip-path
  - https://developer.mozilla.org/en-US/docs/Web/CSS/clip-path
- MDN: mask-image
  - https://developer.mozilla.org/en-US/docs/Web/CSS/mask-image
- MDN: mix-blend-mode
  - https://developer.mozilla.org/en-US/docs/Web/CSS/mix-blend-mode
- MDN: Canvas animation
  - https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API/Tutorial/Basic_animations
- MDN: prefers-reduced-motion
  - https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
