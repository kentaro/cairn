# cairn 設計

Claude Code / Codex などのエージェント CLI を、Apple Silicon 上のローカル MLX
モデルのバックエンドで動かすための小さな常駐サーバ兼ランチャ。本書は設計判断の記録。

精度・速度の実測と考察は [BENCHMARK.md](BENCHMARK.md) を参照。

---

## 1. 背景と目的

- Claude Code / Codex のバックエンドを、サブスクや従量 API から切り離してローカル LLM に
  向けられるか。簡単なタスクをローカルに逃がしてコストを節約したい。
- ゴール: ローカルモデルを各種エージェント CLI のバックエンドとしてワンコマンドで立て、
  `cairn claude` / `cairn codex` で切り替えて使う。

## 2. 差し替えの原理

- Claude Code は **Anthropic Messages API**（`/v1/messages`）を喋り、`ANTHROPIC_BASE_URL`
  で接続先を差し替えられる（公式サポート）。Codex は **OpenAI API** を喋る。
- 一般的なローカル推論サーバ（`mlx_lm.server` 等）は OpenAI 形式しか喋らないため、
  Anthropic クライアントを向けるには翻訳プロキシ（claude-code-router / LiteLLM）が要る。
- cairn は **`vllm-mlx`** を採用する。1 プロセスで OpenAI（`/v1/chat/completions`,
  `/v1/responses`）と Anthropic（`/v1/messages`）の両方をネイティブに公開するため、
  翻訳プロキシが不要で `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` をそのまま向けられる。

## 3. ランタイムとモデル選定

- **MLX** は Apple Silicon ネイティブで、特に MoE モデルで GGUF/llama.cpp より速い。
  Ollama の MLX バックエンドは M5 系専用プレビューで、M4 Max では GGUF にフォールバック
  するため、MLX ランタイムを直接使う。
- サーバは **Anthropic ネイティブ ＋ tool-call パーサ（`qwen3_coder`）＋ continuous
  batching ＋ prefix cache** を備える `vllm-mlx` を採用。
- モデルは `Qwen3-Coder-30B-A3B-Instruct-4bit`（30B-A3B MoE、約3B活性化、4bit、約17GB）。
  agentic coding 向けに RL 学習され tool calling がネイティブ、256K コンテキスト、
  MoE ゆえ生成が速く 64GB に余裕で載る。

## 4. アーキテクチャ

- `config.py` — 凍結 dataclass + TOML 型検証ローダ（`$XDG_CONFIG_HOME/cairn/config.toml`）。
- `backend.py` — `vllm-mlx serve` の起動/停止/死活監視（PID ファイル、loopback 限定、
  ヘルスチェック）。バックエンドは import せず PATH 越しに呼ぶ。
- `profiles.py` — フロントエンド別の env 差と既定 CLI 引数。`claude`（Anthropic 系）/
  `codex`（OpenAI 系）。プロファイルを足すだけで対応 CLI を増やせる。
- `bench.py` — warm 生成スループットと cold/warm prefix キャッシュの計測。
- `cli.py` — `up / down / status / bench` ＋ 起動コマンド `claude / codex / run`。
  起動コマンドは引数を verbatim で渡し、env と既定引数を被せて `os.execvpe` で CLI に
  ハンドオフする。

## 5. 認証

- サーバは **127.0.0.1 限定バインド**なので API キー不要を既定とする。
- `--api-key` を与えた場合、`/v1/messages` は `Authorization: Bearer` を要求し
  `x-api-key` を拒否する。したがって Claude Code には `ANTHROPIC_API_KEY`（x-api-key）
  ではなく **`ANTHROPIC_AUTH_TOKEN`（Bearer）** を渡す。

## 6. プロファイル別の接続設定

- **claude**: env に `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` を
  被せるだけ。既定引数なし。
- **codex**: env（`OPENAI_BASE_URL` / `OPENAI_API_KEY`）に加え、既定引数で Codex の
  プロバイダ設定を注入する。Codex（>= 0.142）はモデルプロバイダを config から読み、
  `wire_api = "chat"` を廃止して `responses` 必須に変わったため、env だけでは実 OpenAI に
  繋がってしまう。`cairn codex` は次を自動で付け、`~/.codex/config.toml` を編集せず
  ローカルに向ける:
  ```
  -c model_providers.cairn.base_url=<base>/v1
  -c model_providers.cairn.wire_api=responses
  -c model_providers.cairn.env_key=OPENAI_API_KEY
  -c model_provider=cairn
  -m <model>
  ```

## 7. 利用規約

- Claude Code / Codex を**ローカルモデルに**向けること自体は問題ない。
- **サブスク（Pro/Max）の OAuth トークンを第三者プロキシ/ツールに通して使う**のは
  Anthropic の Consumer 規約違反（OAuth は Claude Code / claude.ai 専用）。
- よって cairn は **サブスクのトークンを一切扱わない**。ローカルモデル専用とし、本番の
  難タスクは素の `claude`（サブスク直結）で行う。1 セッション内の自動ハイブリッドでは
  なく、用途でコマンドを打ち分ける設計とする。

## 8. 実装方針

- Python 3.12+（`type` 文・`Self`・PEP 604 union・`match`）。
- 型安全（pyright strict、ランタイム依存ゼロの stdlib 構成）。
- ruff + pytest。バックエンド（vllm-mlx）は `uv tool install vllm-mlx` で別途導入。

## 9. 既知の課題 / TODO

- prefill 律速の緩和（詳細は [BENCHMARK.md](BENCHMARK.md) の考察）。本筋は vllm-mlx に
  Anthropic `cache_control` の honor を実装し、CC の breakpoint で segment 単位キャッシュを
  効かせること。当面の代替は CC が先頭に混ぜる揮発バイトの特定と strip 範囲の拡張。
- 6/8bit や他モデル（GLM 等）の切り替え UX。
- `bench` に実運用に近い大プロンプト prefill 指標を追加。
