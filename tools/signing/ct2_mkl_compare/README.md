# Mojicast 翻訳エンジン計測ツール（Intel CPU のパソコン向け）

Mojicast の翻訳エンジンから、ライセンスの都合で外したい部品（Intel MKL）があります。
外しても翻訳が遅くならないかを、**Intel の CPU のパソコン**で確かめるためのツールです。

- Mojicast 本体の設定やファイルには一切触りません
- インターネットには、翻訳モデルのダウンロード（初回だけ・Hugging Face）以外では接続しません
- 中に入っている文（`sentences.txt`）は、癒色えもの配信の書き起こしです

## やってほしいこと

1. Zip を好きな場所に展開する
2. Mojicast・ゲーム・配信ソフトなど、重いアプリを閉じる
3. `ct2_mkl_compare.exe` をダブルクリック
   - 「Windows によって PC が保護されました」と出たら「詳細情報」→「実行」
   - インストール版の Mojicast があれば、そのモデルを使います。無ければ初回に約 0.7GB ダウンロードします
4. 10〜20 分ほど待つ（途中で窓を閉じないでください）
5. 終わると同じフォルダに `result_日時.txt` と `result_日時.json` ができるので、この 2 つを送ってください

AMD など Intel 以外の CPU では、何も測らずに終わります（測る必要がないため）。

### 結果ファイルに入るもの

CPU の名前、Windows のバージョン、翻訳にかかった時間、訳文、メモリ使用量。
ユーザー名やファイルの場所など、個人を特定する情報は入りません。

### Zip 版の Mojicast のモデルを使いたいとき

コマンドプロンプトで、Mojicast のフォルダの `models` を指定して実行します。

```
ct2_mkl_compare.exe --models "C:\...\Mojicast\models"
```

---

## 開発メモ

- 現行の ctranslate2 は環境変数 `CT2_USE_MKL=0` で MKL 抜きビルドと同じ oneDNN 経路になる。
  MKL=1→0→0→1 の4回を別プロセスで走らせ、1文ごとに同じ設定の速い方を採る
- 結果の見方: 「使われた実装」で MKL あり側が `Use Intel MKL: true` になっていること
  （false なら、その CPU では元から MKL を使っていない）。「なし/あり」が 1 未満なら MKL なしが速い。
  M2M（中韓）は数値計算の順序が変わるので、訳文が数 % 変わるのは普通
- 作り方: `powershell -ExecutionPolicy Bypass -File tools\signing\ct2_mkl_compare\build.ps1`
  （`dist\ct2_mkl_compare-win-x64.zip` ができる）
- 開発実行: `reazonspeech-env\Scripts\python.exe tools\signing\ct2_mkl_compare\compare.py --limit 6 --force`
