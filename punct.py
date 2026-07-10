"""
日本語テキストに句読点(、。)を復元するモジュール
モデル: bobfromjapan/bert_japanese_punctuation
        (tohoku-nlp/bert-base-japanese-char-v3 + 線形層のトークン分類)

k2 など句読点を出さないASRの出力を、認識後にここへ通して整形する。
初回呼び出し時にモデルをロード（遅延ロード）。CPUで動作。
"""
import torch
import huggingface_hub as hf
from transformers import BertTokenizer, BertModel

_MODEL_NAME = "tohoku-nlp/bert-base-japanese-char-v3"
_REPO_ID = "bobfromjapan/bert_japanese_punctuation"

_tokenizer = None
_model = None


def _local_first(loader, *args, **kwargs):
    """まずローカルキャッシュから読み、無ければDL（同梱版=DL無し / 軽量版=初回DL）"""
    try:
        return loader(*args, local_files_only=True, **kwargs)
    except Exception:
        return loader(*args, **kwargs)


class _PunctuationPredictor(torch.nn.Module):
    """BERTの各トークン出力に線形層を足し、直後の句読点(、。)を予測する"""

    def __init__(self, base_model):
        super().__init__()
        self.base_model = base_model
        self.dropout = torch.nn.Dropout(0.2)
        self.linear = torch.nn.Linear(768, 2)  # [、の確率, 。の確率]

    def forward(self, input_ids, attention_mask):
        last_hidden_state = self.base_model(
            input_ids=input_ids, attention_mask=attention_mask
        ).last_hidden_state
        return self.linear(self.dropout(last_hidden_state))


def load_punctuator(num_threads: int = 4):
    """モデルとトークナイザをロード（初回のみ実行）"""
    global _tokenizer, _model
    if _model is not None:
        return
    torch.set_num_threads(num_threads)  # CPUを複数コア使う
    _tokenizer = _local_first(BertTokenizer.from_pretrained, _MODEL_NAME)
    base_model = _local_first(BertModel.from_pretrained, _MODEL_NAME)
    model = _PunctuationPredictor(base_model)
    # 重みは HF リポジトリから取得（cwd非依存の絶対パスで解決）
    weight_path = _local_first(hf.hf_hub_download, _REPO_ID,
                               "weight/punctuation_position_model.pth")
    model.load_state_dict(torch.load(weight_path, map_location="cpu"))
    model.eval()
    _model = model


def _rebuild(input_ids, comma_pos, period_pos):
    """トークン列と句読点フラグから、句読点入りテキストを組み立てる"""
    out = []
    n = len(input_ids)
    for i, (c, p) in enumerate(zip(comma_pos, period_pos)):
        token_id = input_ids[i].item()
        if token_id <= 5:  # [PAD]/[UNK]/[CLS]/[SEP]/[MASK] などの特殊トークンは除外
            continue
        if i >= n - 1:
            break
        ch = _tokenizer.convert_ids_to_tokens(token_id)
        if p:
            out.append(ch + "。")
        elif c:
            out.append(ch + "、")
        else:
            out.append(ch)
    return "".join(out)


def add_punctuation(text: str, comma_thresh: float = 0.1,
                    period_thresh: float = 0.1, max_length: int = 256) -> str:
    """
    テキストに句読点を復元して返す

    Args:
        text: 句読点なし（または混在）の日本語テキスト
        comma_thresh: 読点(、)を打つ確率しきい値。上げると、が減る
        period_thresh: 句点(。)を打つ確率しきい値。上げると。が減る
        max_length: 一度に処理する文字数（長文は分割）

    Returns:
        句読点入りテキスト
    """
    if _model is None:
        load_punctuator()

    text = text.replace("、", "").replace("。", "")
    if not text:
        return text

    result = ""
    with torch.no_grad():
        for i in range(0, len(text), max_length):
            chunk = text[i:i + max_length]
            # 文字を空白区切りにして1文字=1トークンとして扱う（MeCab不要）
            # パディングは実発話長に合わせる(longest)。512固定だと短文でも
            # 512トークン分計算して遅くなるため（357ms→30ms相当の差）。
            inputs = _tokenizer(
                " ".join(list(chunk)),
                padding="longest", truncation=True, max_length=512,
                return_tensors="pt",
            )
            output = torch.sigmoid(_model(inputs.input_ids, inputs.attention_mask))
            probs = output[0].numpy().T
            comma_pos = probs[0] > comma_thresh
            period_pos = probs[1] > period_thresh
            result += _rebuild(inputs.input_ids[0], comma_pos, period_pos)
    return result


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    samples = [
        "きょうはいい天気ですね明日は雨が降るそうです傘を持っていきましょう",
        "こんにちは私はえもですよろしくお願いします配信を始めます",
        "本日の会議では新機能の仕様について議論しますまず画面設計から確認しましょう",
    ]
    for s in samples:
        print("IN :", s)
        print("OUT:", add_punctuation(s))
        print()
