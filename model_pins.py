"""
Hugging Face から取得するモデルの版（コミットID）の固定

モデルは初回にだけ Hugging Face Hub から取得する。取得元リポジトリの main を
そのまま落とすと、リポジトリ側が書き換えられた（乗っ取り・誤操作）ときに、
新しく入れた利用者へ検証していない重みが届いてしまう。そこで「取りに行く版」を
ここに書いたコミットIDへ固定する。更新手順は docs/MODEL_PINS.md。

既存の利用者で再ダウンロードを起こさないための解決順（resolve_snapshot / resolve_file）:
  1. 固定したコミットIDのスナップショットが手元にあればそれ（新しく取得した人・同梱版）
  2. 無ければ、以前の Mojicast が main で取得したキャッシュ（refs/main が指す版）
     … 手元にあるものはすでに一度取得・使用している。版が古くてもそのまま使い、
       通信も再取得もしない（固定前と同じ振る舞い）
  3. どちらも無いときだけ、固定したコミットIDを取得する
1・2 はどちらも local_files_only（通信しない）。
"""

# 2026-11-10 時点。いずれも Hugging Face 上の main の先頭と、開発機・配布物（同梱版の
# models\hub）・インストール版のキャッシュの refs/main が一致することを確認した版。
REVISIONS = {
    # 認識（k2・日本語）
    "reazon-research/reazonspeech-k2-v2":
        "291488c8151be24d7da4bf7af26e533fad96e407",
    # 認識（SenseVoice・多言語）
    "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17":
        "2365baeacb507f821a0c8120fcee3d484dba7a07",
    # 句読点（BERT・ONNX 変換済み）
    "ishiki-emo/mojicast-punct-onnx":
        "6bef44545db904999043648af48ee17cd6177ee4",
    # 英訳（FuguMT・CTranslate2 変換済み）
    "ishiki-emo/mojicast-fugumt-ja-en-ct2":
        "ca461b1c71739ae79ff8e1a3904bbb846e3e9d89",
    # 多言語訳（M2M-100・CTranslate2 変換済み）
    "ishiki-emo/mojicast-m2m100-ct2":
        "0ea75b31e8fe07a27ba6c2b9b1b1a279e7f44b93",
    # リアクション演出（音イベント分類）
    "k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15":
        "c24f9d0fdf7d0b8c0c4f9733aa0cac51fda10c95",
}


def revision(repo_id: str) -> str:
    """固定したコミットID（未登録のリポジトリは KeyError＝固定し忘れを起動時に気づかせる）"""
    return REVISIONS[repo_id]


def resolve_snapshot(repo_id, *, allow_patterns=None, download=True):
    """snapshot_download の版固定つき版。戻り値はスナップショットのフォルダ。
    download=False なら手元にあるときだけ返し、無ければ例外（通信しない）"""
    import huggingface_hub as hf
    rev = revision(repo_id)
    kw = {} if allow_patterns is None else {"allow_patterns": allow_patterns}
    try:
        return hf.snapshot_download(repo_id, revision=rev, local_files_only=True, **kw)
    except Exception:
        pass
    try:
        # 以前の Mojicast（版固定前）が取得したキャッシュ・引き継いだ models\
        return hf.snapshot_download(repo_id, local_files_only=True, **kw)
    except Exception:
        if not download:
            raise
    return hf.snapshot_download(repo_id, revision=rev, **kw)


def resolve_file(repo_id, filename, *, download=True):
    """hf_hub_download の版固定つき版。戻り値はファイルのパス"""
    import huggingface_hub as hf
    rev = revision(repo_id)
    try:
        return hf.hf_hub_download(repo_id, filename, revision=rev, local_files_only=True)
    except Exception:
        pass
    try:
        return hf.hf_hub_download(repo_id, filename, local_files_only=True)
    except Exception:
        if not download:
            raise
    return hf.hf_hub_download(repo_id, filename, revision=rev)
