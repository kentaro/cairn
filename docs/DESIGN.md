# cairn 設計と検討

Claude Code / Codex などのエージェント CLI を、Apple Silicon 上のローカル MLX
モデルで動かすための小さな常駐サーバ兼ランチャ。本書は「なぜこの形にしたか」の
検討記録であり、実測値に基づく実用可否の判断材料を残すことを目的とする。

## 1. 背景と目的

- 動機: Claude Code のバックエンドを、サブスク（Claude 本家）から切り離して
  ローカル LLM 等に差し替えられるか。簡単なタスクをローカルに逃がして
  トークンを節約したい。
- ゴール: ローカルモデルを「各種エージェント CLI のバックエンド」として
  ワンコマンドで立て、切り替えて使えるようにする。

## 2. 差し替えの原理（なぜプロキシ／なぜ wire 形式が問題か）

- Claude Code は **Anthropic Messages API**（`/v1/messages`）を喋り、`claude` は
  `ANTHROPIC_BASE_URL` で接続先を差し替えられる（公式サポート機能。LLM gateway /
  Bedrock / Vertex も同様に公式が支持）。
- 一方、一般的なローカル推論サーバ（`mlx_lm.server` 等）は **OpenAI 形式**
  （`/v1/chat/completions`）しか喋らない。形式が違うため、素朴に
  `ANTHROPIC_BASE_URL=http://localhost:... claude` としても通らない。
- 解決策は2つ:
  1. **翻訳プロキシ**（claude-code-router / LiteLLM）を挟む。
  2. **Anthropic 形式をネイティブに喋るサーバ**を使う。
- cairn は **(2)** を採る。`vllm-mlx` は OpenAI と Anthropic の両方を 1 プロセスで
  公開するため、翻訳プロキシが不要になり、`ANTHROPIC_BASE_URL=... claude` が
  そのまま成立する。依存とプロセスが減り、構成が単純になる。

## 3. ランタイム選定（なぜ MLX / なぜ vllm-mlx）

- 対象機は M4 Max / 64GB。MLX は Apple Silicon ネイティブで、特に MoE モデルでは
  GGUF/llama.cpp 比で大きく速い（実測: 後述の warm 生成 91 tok/s）。
- ただし Ollama の MLX バックエンドは執筆時点で **M5 系専用プレビュー**であり、
  M4 Max では Ollama は GGUF にフォールバックする。よって Ollama ではなく
  MLX ランタイムを直接使う。
- サーバは複数候補（mlx_lm.server / mlx-serve / vllm-mlx）を比較し、
  **Anthropic ネイティブ＋ tool-call パーサ（`qwen3_coder` を含む）＋ continuous
  batching ＋ prefix cache** を備える `vllm-mlx` を採用。

## 4. モデル選定

- `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`（30B-A3B MoE、約3B活性化、
  4bit、約17GB）。
- 理由: agentic coding 向けに RL 学習され tool calling がネイティブで安定、
  256K コンテキスト、MoE ゆえ生成が速く 64GB に余裕で載る。
- 4bit を既定とした（最高精度より、ディスク・メモリ・速度の実用バランスを優先。
  必要なら 6/8bit に上げられる）。

## 5. アーキテクチャ

- `config.py` — 凍結 dataclass + TOML の型検証ローダ。設定は
  `$XDG_CONFIG_HOME/cairn/config.toml`。
- `backend.py` — `vllm-mlx serve` プロセスの起動/停止/死活監視（PID ファイル、
  loopback 限定、ヘルスチェック）。バイナリは import せず PATH 越しに呼ぶ
  （重い推論スタックを cairn 環境に持ち込まない）。
- `profiles.py` — フロントエンドごとの env 差を表現。`claude`（Anthropic 系 env）/
  `codex`（OpenAI 系 env）。プロファイルを足すだけで対応 CLI を増やせる。
- `bench.py` — warm 生成スループット計測。
- `cli.py` — `up / down / status / bench / claude / codex / run` のディスパッチ。
  起動コマンドは `os.execvpe` で env を被せて CLI にハンドオフする。

## 6. 認証の扱い

- サーバは **127.0.0.1 限定バインド**なので API キー不要を既定とする。
- `vllm-mlx` に `--api-key` を与えた場合、`/v1/messages` は
  **`Authorization: Bearer`** を要求し、`x-api-key` は拒否する。したがって
  Claude Code には `ANTHROPIC_API_KEY`（= x-api-key）ではなく
  **`ANTHROPIC_AUTH_TOKEN`（= Bearer）** を渡す（`profiles.py` で実装）。

## 7. 利用規約上の整理（重要）

- 「Claude Code を**ローカルモデルに**向ける」こと自体は規約上問題ない。
- 一方、**サブスク（Pro/Max）の OAuth トークンを第三者プロキシ／ツールに通して
  Claude を使う**のは Anthropic の Consumer 規約違反（OAuth は Claude Code /
  claude.ai 専用）であり、アカウント停止の実例もある。
- よって cairn は **サブスクのトークンを一切扱わない**。本番の難タスクは素の
  `claude`（サブスク直結・無加工）で行い、cairn は**ローカルモデル専用**として
  簡単/使い捨て/オフライン用途に使う。1 セッション内の自動ハイブリッドではなく、
  **用途でコマンドを打ち分ける**設計とした（規約セーフ＋トークン節約）。

## 8. 実測（M4 Max / 64GB, qwen3-coder:30b-4bit, vllm-mlx）

| 項目 | 値 | 備考 |
| --- | --- | --- |
| warm 生成スループット | **91 tok/s** | 59 tok / 0.65s、短プロンプト |
| cold（初回ロード込み） | 0.2 tok/s | モデルロードの一過性 |
| Claude Code 実ターン | **188 tok を 215.6s** | 実質 0.9 tok/s |
| 同・入力プロンプト | **44,347 tokens** | system + tools 98 個 |
| prefix cache ヒット | 17 / 44,347 | ターン跨ぎでほぼ効かず |
| Metal メモリ | active 21.6GB / peak 22.3GB | 4bit 30B + KV |
| tool-use | 機能する | Write 成功、ただし生成ファイル名に綻び（`f fizzbuzz.py`） |

### 結論（実用可否）

- **生成は速い（91 tok/s、GGUF 想定の約2倍）。**
- **しかし Claude Code の実運用は prefill 律速**。毎ターン約 44K トークン
  （巨大な system prompt + 98 tool 定義）を prefill し直すため **3〜4 分/ターン**
  かかり、prefix cache もほぼ効かない。多ステップのタスクは 10 分超になりうる。
- tool-use は通るが小さな精度の綻びがある。
- したがって **フルローカルの Claude Code は「日常の本番作業」には非現実的**で、
  実験・オフライン・使い捨て・軽量プロンプトのワンショット向き。本番は素の
  `claude`（サブスク）を使う、という第7節の住み分けが妥当だと実測が裏づけた。

## 9. 既知の課題 / TODO

- prefill 律速の緩和: prompt 圧縮、tool 定義の削減、prefix cache が効く呼び出し方の
  検討（Claude Code 側のプロンプト先頭に動的要素があるとキャッシュが busting する
  可能性）。
- Codex プロファイルは `OPENAI_BASE_URL`/`OPENAI_API_KEY` のみで、Codex の
  `config.toml`（model_provider）設定が別途必要なケースがある。要追補。
- 6/8bit モデルや他モデル（GLM 等）の切り替え UX。
- `bench` に prefill ベンチ（大プロンプト）を追加し、実運用に近い指標を出す。

## 10. 実装方針

- Python 3.12+（`type` 文・`StrEnum` 相当・`Self`・PEP 604）。
- 型安全（pyright strict、ランタイム依存ゼロの stdlib 構成）。
- ruff + pytest。バックエンド（vllm-mlx）は `uv tool install vllm-mlx` で別途導入。
