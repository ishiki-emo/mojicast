# モデルの版（コミットID）の固定

Mojicast は認識・句読点・翻訳・リアクション演出のモデルを、初回にだけ Hugging Face Hub から
取得する。取得する版は [model_pins.py](../model_pins.py) の `REVISIONS` に**コミットID（40桁）**で
固定している。取得元リポジトリの main が書き換えられても、新しく入れた利用者に届くのは
ここで固定した版だけになる。

## 固定している版（2026-11-10）

| 用途 | リポジトリ | コミットID |
|---|---|---|
| 認識（k2・日本語） | `reazon-research/reazonspeech-k2-v2` | `291488c8151be24d7da4bf7af26e533fad96e407` |
| 認識（SenseVoice・多言語） | `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` | `2365baeacb507f821a0c8120fcee3d484dba7a07` |
| 句読点（BERT・ONNX） | `ishiki-emo/mojicast-punct-onnx` | `6bef44545db904999043648af48ee17cd6177ee4` |
| 英訳（FuguMT・CT2） | `ishiki-emo/mojicast-fugumt-ja-en-ct2` | `ca461b1c71739ae79ff8e1a3904bbb846e3e9d89` |
| 多言語訳（M2M-100・CT2） | `ishiki-emo/mojicast-m2m100-ct2` | `0ea75b31e8fe07a27ba6c2b9b1b1a279e7f44b93` |
| リアクション演出（音イベント分類） | `k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15` | `c24f9d0fdf7d0b8c0c4f9733aa0cac51fda10c95` |

いずれも固定時点の Hugging Face 上の main の先頭で、開発機の `~/.cache/huggingface/hub`・
同梱版の配布物（`dist/Mojicast/models/hub`）・インストール版（`%LOCALAPPDATA%\Programs\Mojicast\models\hub`）の
`refs/main` と一致することを確認した。

## 既存の利用者で再ダウンロードが起きない理由

`model_pins.resolve_snapshot` / `resolve_file` は次の順で探す。

1. 固定したコミットIDのスナップショット（`snapshots/<ID>`）が手元にあればそれを使う
2. 無ければ、版固定前の Mojicast が main で取得したキャッシュ（`refs/main` が指す版）を使う。
   固定した版より古くても、再取得も通信もしない（固定前と同じ振る舞い）
3. どちらも無いときだけ、固定したコミットIDを取得する

1・2 は `local_files_only=True`（通信なし）。同梱版・引き継ぎ（migrate で旧フォルダの
`models\` をコピー）・手動配置の `models_conv\` もこれまでどおり読める。
コミットID指定の取得は `refs/main` を書かないが、1 が `snapshots/<ID>` を直接見るので
2回目以降もオフラインで起動できる。

## 固定する版を更新する手順

モデルを差し替える（自分のリポジトリへ新しい変換結果を上げる、上流の更新を取り込む）ときだけ行う。

1. 新しいコミットIDを調べる
   ```
   python -c "from huggingface_hub import HfApi; print(HfApi().model_info('<リポジトリ>').sha)"
   ```
   上流（`reazon-research/…`・`csukuangfj/…`・`k2-fsa/…`）の場合は、そのコミットで
   何が変わったかを Hugging Face の「Files and versions → History」で確認する。
2. `model_pins.py` の `REVISIONS` と、この文書の表を同じ値に書き換える
   （`tests/test_model_pins.py` が両者の一致を確認する）
3. 開発機のキャッシュを新しい版にそろえてから動作確認する
   （例: `huggingface-cli download <リポジトリ> --revision <ID>`。認識・句読点・翻訳が出ること）
4. 同梱版を作る場合は、`build_bundle.ps1` / `build_bundle.sh` がコピーする開発機のキャッシュが
   新しい版になっていることを確かめる（古い版のまま同梱しても動くが、新しい版は取得されない）
5. リリースノートに「〇〇のモデルを更新（初回だけ約 N MB の再ダウンロード）」と書く。
   既存の利用者は 2 の段階で古い版を使い続けるので、**新しい版を必ず使わせたい場合は**
   2 の段階を飛ばす仕組みを別途入れること（現状は入れていない）

### 注意

- 固定IDの取得に失敗しても、手元に古い版があればそれを使う（2）。モデルの差し替えを
  「全員に届ける」用途には使えない。
- `_REPO_ID` を新しいリポジトリに変えた場合は `REVISIONS` への追加が必須
  （未登録のリポジトリは `KeyError` で止まり、テストでも検出する）。
- Hugging Face のリポジトリ名は変更しない（ishiki-emo の3リポジトリは他のアプリからも直接取得されている）。
