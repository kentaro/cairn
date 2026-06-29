# cairn 設計と検討

Claude Code / Codex などのエージェント CLI を、Apple Silicon 上のローカル MLX
モデルのバックエンドで動かすための小さな常駐サーバ兼ランチャ。本書は設計判断の
記録と、実用可否を判断するための実測値をまとめたものである。

実測環境: M4 Max / 64GB、`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`、vllm-mlx。

---

## 1. 背景と目的

- Claude Code / Codex のバックエンドを、サブスクや従量 API から切り離して
  ローカル LLM に向けられるか。簡単なタスクをローカルに逃がしてコストを節約したい。
- ゴール: ローカルモデルを各種エージェント CLI のバックエンドとしてワンコマンドで
  立て、`cairn claude` / `cairn codex` で切り替えて使う。

## 2. 差し替えの原理

- Claude Code は **Anthropic Messages API**（`/v1/messages`）を喋り、`ANTHROPIC_BASE_URL`
  で接続先を差し替えられる（公式サポート）。Codex は **OpenAI API** を喋る。
- 一般的なローカル推論サーバ（`mlx_lm.server` 等）は OpenAI 形式しか喋らないため、
  Anthropic クライアントを向けるには翻訳プロキシ（claude-code-router / LiteLLM）が要る。
- cairn は **`vllm-mlx`** を採用する。これは 1 プロセスで OpenAI（`/v1/chat/completions`,
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

## 7. 実測: Claude Code と Codex の決定的な差

### 観測方法

vllm-mlx の scheduler はリクエストごとに、入力トークン数・prefix キャッシュ再利用数・
実 prefill 数をログに出す:
```
[cache_fetch] HIT prompt_tokens=15999 cached=15903 remaining=96
[schedule]    prompt_tokens=15999 tokens_to_prefill=96, 15903 cached
```
同一モデル・同一タスクで両 CLI を走らせ、agentic ループ中の各リクエストのこの値を
比較した。`tokens_to_prefill`（＝実際に計算したトークン数）が体感速度の本体である。

### 結果

**Claude Code**（45K プロンプト、同一セッション）:

| リクエスト | prompt_tokens | cached | 実 prefill |
| --- | --- | --- | --- |
| ターン1 | 45,045 | 3 | 45,042 |
| ターン2 | 45,193 | 3 | 45,190 |

→ ターン2でもキャッシュが効かず、毎ターン 45K を丸ごと prefill。Apple silicon の
prefill 速度では 1 ターン約 3〜4 分（188 tok の出力に 215.6s ＝実質 0.9 tok/s）。

**Codex CLI**（同じバックエンド・同じタスク、Responses API）:

| リクエスト | prompt_tokens | cached | 実 prefill |
| --- | --- | --- | --- |
| ターン1 | 15,852 | 3 | 15,849（cold） |
| ターン2 | 15,999 | 15,903 | **96** |
| ターン3 | 16,149 | 16,033 | **116** |

→ プロンプトが CC の約 1/3、初回 cold 以降は自動 prefix キャッシュが **99% ヒット**し、
実 prefill は毎ターン約100トークン。

| | CC ターン2 | Codex ターン2 |
| --- | --- | --- |
| 実 prefill | **45,190** | **96** |
| キャッシュ | 全 bust | 99% hit |

### なぜ差が出るか

差を生むのはモデルでもサーバでもなく、**クライアントがプロンプト先頭を byte 安定に
保つか**である。

- **vllm-mlx の自動 prefix キャッシュは正しく機能する**。同一プレフィクスで 36倍、
  tools フィールド一致で 50倍の再利用を実測（warm 生成は 91 tok/s、Metal active 21.6GB）。
- **Codex** はプロンプト先頭を安定させるため、自動キャッシュだけでターン跨ぎに効く。
- **Claude Code** は先頭に per-request の揮発バイトを混ぜる。これは本家 Anthropic の
  **サーバ側 prompt caching（`cache_control` breakpoint）と一体で設計されている**ため
  本家では問題にならないが、ローカルでは自動キャッシュがトークン4付近で全 bust する。

### サーバ側で吸収できるか

- vllm-mlx は Anthropic の **`cache_control` を honor しない**（リクエストモデルに
  フィールドが無く黙って捨てる）。
- 代わりに CC の `x-anthropic-billing-header`（per-request ハッシュ）を strip して
  自動キャッシュを救う実装を持つ。これはヘッダだけが変わる合成ケースでは 100% ヒットを
  確認できるが、**実 CC はヘッダ以外の揮発バイトも先頭に混ぜるため、strip 込みでも
  ターン2で `cached=3`**。現状サーバ側の最適化だけでは実 CC の prefill を救えない。

## 8. 推論 API をエージェントのバックエンドとして提供するときの論点

利用形態が「ステートレスな one-shot 推論」から「エージェントのバックエンド」へ
広がると負荷の質が変わる。

- **負荷**: エージェントは蓄積コンテキスト全部を毎ターン再送し何十ターンも回すため、
  負荷は **コンテキスト長 × ターン数 × 同時数** で効き prefill が支配的になる。
  短プロンプト前提のサイジングは破綻する。
- **劣化時のレバー（提供側）**: prefix/KV cache・continuous batching・admission control・
  トークン上限・オートスケール。ただし自動 prefix キャッシュはプレフィクスが byte 安定な
  クライアントにしか効かない。CC のような相手には **明示キャッシュ制御
  （`cache_control` honor）** が要る。本家 CC が 45K/ターンでも実用的なのは、まさに
  Anthropic がこれをサーバ側でやっているから。
- **劣化時のレバー（ユーザ側）**: プロンプト圧縮、tool 定義削減、履歴短縮、モデル選択。
- **クライアント側の含意**: Codex が示すとおり、クライアントが先頭を byte 安定に保てば
  明示キャッシュ無しでも自動キャッシュだけで吸収できる。提供側は「明示キャッシュを
  実装する」か「クライアントに安定プレフィクスを促す」かのどちらかが要る。

## 9. 利用規約

- Claude Code / Codex を**ローカルモデルに**向けること自体は問題ない。
- **サブスク（Pro/Max）の OAuth トークンを第三者プロキシ/ツールに通して使う**のは
  Anthropic の Consumer 規約違反（OAuth は Claude Code / claude.ai 専用）。
- よって cairn は **サブスクのトークンを一切扱わない**。ローカルモデル専用とし、
  本番の難タスクは素の `claude`（サブスク直結）で行う。1 セッション内の自動ハイブリッド
  ではなく、用途でコマンドを打ち分ける設計とする。

## 10. 実用可否の結論

- **モデルも vllm-mlx も十分速い**。遅さの原因はクライアントのプロンプト・プレフィクスの
  非安定性であって、モデルやサーバではない。
- **Apple Silicon でローカルモデルをエージェントのバックエンドにするなら Codex が実用的、
  Claude Code は非現実的**。Codex はプレフィクスが安定で自動キャッシュが効く。CC は本家の
  サーバ側明示キャッシュ前提のため、ローカルでは毎ターン全 prefill になる。
- フルローカルの **Claude Code 日常運用は避け、素の `claude`（サブスク）を使う**。
  cairn のローカルモデルは「プレフィクスが安定する用途」で活きる: 自作アプリ/スクリプトの
  バックエンド、短プロンプトのバッチ、テンプレ反復、RAG、そして **Codex CLI**。
- tool-use は通るが小さな精度の綻び（生成ファイル名の崩れ等）があり、agentic な安定性は
  商用モデルに及ばない。

## 11. ローカルでフロンティア級を狙うなら（ハード観点）

64GB / 30B クラスは「実用」止まりで、最難の agentic coding は商用に届かない。フロンティア級
オープンモデル（例: GLM-5.2、744B MoE / 約40B 活性化、MIT）を自宅で動かす場合の目安:

| 量子化 | 必要メモリ | ハード |
| --- | --- | --- |
| 2-bit | 〜240GB | Mac Studio M3 Ultra 256GB（〜$8k） |
| 4-bit | 〜476GB | 512GB Mac / 2×A100 80GB / 4×RTX6000 Ada |
| FP8 | 〜744GB | 8×H200 |

- 質: フロンティア級オープンモデルは設計/フロントエンド・数学で商用最上位に並ぶ/超える
  領域がある一方、SWE-bench 系の agentic coding ではまだ商用が上。
- 速度: MoE ゆえ decode は速いが、**Apple silicon の prefill 律速は残る**（巨大 context を
  毎ターン投げる agentic は NVIDIA 系でないと厳しい）。
- コスト: オープンモデルの従量 API は商用最上位の 1/10 前後と安い。自前ホストの優位は
  プライバシー/オフライン/大量バッチに限られる。

## 12. 既知の課題 / TODO

- prefill 律速の緩和。本筋は vllm-mlx に Anthropic `cache_control` の honor を実装し、
  CC の breakpoint で segment 単位キャッシュを効かせること（本家と同じ機構）。当面の
  代替は CC が先頭に混ぜる揮発バイトの特定と strip 範囲の拡張。
- 6/8bit や他モデル（GLM 等）の切り替え UX。
- `bench` に実運用に近い大プロンプト prefill 指標を追加。

## 13. 実装方針

- Python 3.12+（`type` 文・`Self`・PEP 604 union・`match`）。
- 型安全（pyright strict、ランタイム依存ゼロの stdlib 構成）。
- ruff + pytest。バックエンド（vllm-mlx）は `uv tool install vllm-mlx` で別途導入。
