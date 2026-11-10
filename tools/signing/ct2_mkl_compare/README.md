# ct2_mkl_compare — 翻訳エンジンの MKL あり/なし比較（Intel 機用）

SignPath Foundation の署名条件（同梱物がすべて OSI 承認ライセンス）を満たすため、
翻訳エンジン CTranslate2 から Intel oneMKL（非 OSI）を外せるかを判断する計測ツール。
AMD 機はもともと MKL を使っていないので、**Intel 機で**測る。

## 使い方（Intel 機）

1. `ct2_mkl_compare-win-x64.zip` を展開する
2. `ct2_mkl_compare.exe` をダブルクリック
   - 初回はモデル（約 0.7GB）を `models\` にダウンロードする
   - Mojicast の models を流用するなら、コマンドプロンプトで
     `ct2_mkl_compare.exe --models "C:\...\Mojicast\models"`
3. 10〜20 分ほどで終わる（Mojicast・ゲーム・配信ソフトは閉じておく）
4. `result_日時.txt` と `result_日時.json` を開発機に持ち帰る

## 見る所

- 「使われた実装」で、MKL あり側が `Use Intel MKL: true`、なし側が `false` になっていること
  （両方 false なら、その CPU では元から MKL を使っていない）
- **なし/あり**: 1 未満なら MKL なしの方が速い。1.3 倍程度までなら字幕の遅れとしては
  体感しにくい（英訳は 1 文数十 ms）。中韓訳は 1 文 100〜300ms なので倍率が大きいと効く
- **訳文一致**: 数値計算の順序が変わるので、M2M（中韓）は数 % 変わるのが普通。英訳は
  ほぼ一致するはず。変わった文は結果ファイルの末尾に並ぶ

※ Zip には配信の書き起こし（`sentences.txt`）が入っている。自分の機械以外に配らない。

## 作り方（開発機）

```powershell
powershell -ExecutionPolicy Bypass -File tools\signing\ct2_mkl_compare\build.ps1
```

開発実行: `reazonspeech-env\Scripts\python.exe tools\signing\ct2_mkl_compare\compare.py --limit 6`
