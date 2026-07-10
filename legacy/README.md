# legacy/

GUI 化（`app.py`）以前の実験・プロトタイプ。**現在のアプリでは使用していません**。
仕組みの参考用に残しています。

| ファイル | 内容 |
|---|---|
| `transcribe_k2.py` | k2 ASR の最小の文字起こしサンプル |
| `transcribe_mic.py` / `transcribe_mic_vad.py` | マイク入力＋VADのCLI版 |
| `transcribe_stream.py` | 疑似ストリーミング認識（現 `engine.py` の原型） |
| `caption.py` | 旧 LiveCaption 表示クラス |
| `overlay_server.py` | 旧オーバーレイ配信サーバ（現 `app_server.py` に統合） |
| `config.yaml` | 旧設定ファイル（現 `config.json` に移行） |
