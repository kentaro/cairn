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
| 同・入力プロンプト | **44〜45K tokens** | system + tools 98 個 |
| prefix cache ヒット（実 CC） | **3 / 45,193**（ターン2でも） | 最適化込みでも全 bust（後述・再測定） |
| Metal メモリ | active 21.6GB / peak 22.3GB | 4bit 30B + KV |
| tool-use | 機能する | Write 成功、ただし生成ファイル名に綻び（`f fizzbuzz.py`） |

### prefix cache の切り分け実験

「Claude Code が遅いのはサーバが遅いのか、CC のプロンプトが悪いのか」を切り分けるため、
同一/部分/tools の3条件で prefix cache の再利用を計測した（~7-10K token prefix）。

| 実験 | 内容 | 結果 |
| --- | --- | --- |
| 同一プレフィクス | 同じ大プロンプトを2回 | T1=7.3s → **T2=0.2s（36倍）** |
| 部分プレフィクス | 共通ヘッド + 末尾だけ変化 | T1=7.6s → **T2=0.2s**（変化分だけ再計算） |
| tools フィールド | tools=40 同一 + user 変化 | T1=11.5s → **T2=0.4s（50倍）**、`cached=10368/10378` |

→ **vllm-mlx の prefix cache は完璧に機能する**（system も tools も messages も、
byte 同一のプレフィクスは KV を再利用し、変化した末尾だけ prefill する）。

### 再測定: 「最適化込み」での Claude Code（重要）

vllm-mlx は CC が system に毎ターン差し込む per-request ハッシュ
（`x-anthropic-billing-header: …`）を正規表現で除去し、自動 prefix キャッシュを
ターン跨ぎで効かせようとする（`api/anthropic_adapter.py`）。この最適化が「最初から
効いている」前提で 2 系統を測り直した。

**(1) 合成テスト（揮発バイトが billing-header だけの場合）— strip は効く**

| 条件 | 時間 | cached / prompt_tokens |
| --- | --- | --- |
| A cold（hashA） | 9.6s | 初回・格納 |
| **B: ハッシュだけ変更** | 7.8s | **8386 / 8386（100%）** |
| C: ターン2相当（+末尾） | 0.44s | 8387 / 8574（97%） |
| D: C を別ハッシュで再送 | 0.20s | 8574 / 8574（100%） |

→ per-request ハッシュ**だけ**が変わるなら strip で安定化し、ターン跨ぎで 100% ヒット。

**(2) 実 Claude Code（45K プロンプト, 同一セッション 2 ターン）— それでも効かない**

| リクエスト | prompt_tokens | cached |
| --- | --- | --- |
| ターン1 | 45,045 | **3** |
| ターン2 | 45,193 | **3** |

→ **実 CC はターン2でも `cached=3`**。strip 後もキャッシュはトークン4付近で全 bust する。
つまり CC は billing-header **以外**の揮発バイトをプロンプトの**ごく先頭**に混ぜており、
現行 vllm-mlx の最適化（特定ヘッダの strip）では救えない。45K を毎ターン丸ごと
prefill する状況は「最適化込み」でも変わらない。

**(3) Codex CLI（OpenAI Responses API 経由）— CC と正反対に「効く」**

同じ local バックエンド・同じタスクで Codex（`codex exec`, wire_api=responses）を実測。
Codex は `/v1/responses` を喋り、Anthropic アダプタ（billing-header strip）は通らない。

| リクエスト | prompt_tokens | cached | **実 prefill（tokens_to_prefill）** |
| --- | --- | --- | --- |
| ターン1 | 15,852 | 3 | 15,849（cold） |
| ターン2 | 15,999 | **15,903** | **96** |
| ターン3 | 16,149 | **16,033** | **116** |

→ **Codex はプロンプトが (a) CC の約 1/3（16K 対 45K）、(b) プレフィクスが byte 安定**。
初回 cold 以降、ターン2+は自動 prefix キャッシュが **99% ヒット**し、実 prefill は
**毎ターン約100トークンだけ**。CC（毎ターン 45,042 を prefill）とは桁違い。

| | CC ターン2 | Codex ターン2 |
| --- | --- | --- |
| prompt_tokens | 45,193 | 15,999 |
| 実 prefill | **45,190** | **96** |
| cache | 全 bust | **99% hit** |

→ **Apple silicon でローカル LLM をエージェントのバックエンドにするなら、CC ではなく
Codex のほうが圧倒的に向く**。理由はモデルでもサーバでもなく、**クライアントが
プロンプト・プレフィクスを byte 安定に保つかどうか**。Codex は保つ（明示キャッシュに
頼らずとも自動 prefix キャッシュが効く）、CC は保たない（本家のサーバ側明示キャッシュ前提）。

### 結論（実用可否）

- **モデルも vllm-mlx も十分速い**: 生成 91〜100 tok/s、prefix cache 36〜50倍。
- **Claude Code が遅い真因は「素朴なローカルサーバ」と「CC の前提」のミスマッチ**。
  実 CC ターンは `cached=3/45193`（ターン2でも）＝毎ターン byte-非同一のヘッドを送るため
  vllm-mlx の**自動 byte-prefix キャッシュ**が全 bust し、45K を毎回 prefill して
  3〜4 分/ターンになる。
- **ただし CC のプロンプト設計が悪いわけではない**。CC は Anthropic 本家バックエンドの
  **サーバ側コンテキスト最適化（明示 prompt caching）と一体で設計されている**。
  Anthropic API には `cache_control` breakpoint があり、CC は tools 定義・system・
  会話プレフィクスに breakpoint を置く。動的要素（日時・git 状態・フック注入）を
  breakpoint より後ろに置けば、安定ブロックの KV はキャッシュされ bust しない。
  これが本家で CC が実用的に動く理由。
- **「サーバ側で握れるか」の実測回答（上の再測定より）**: (a) vllm-mlx は Anthropic の
  `cache_control` を **honor しない**（リクエストモデルにフィールドが無く黙って捨てる）。
  代わりに billing-header を strip して自動キャッシュを救う実装を持つが、(b) **実 CC は
  ヘッダ以外の揮発バイトを先頭に混ぜるため、strip 込みでもターン2で `cached=3` ＝ほぼ
  全 bust**。よって現状、サーバ側の最適化だけでは実 CC のローカル prefill を救えない。
  これに Apple silicon の prefill 遅延が重なり、45K/ターンを毎回計算して 3〜4 分になる。
- **対照的に Codex CLI はローカル・バックエンドとして実用的**（再測定(3)）。プロンプトが
  CC の約 1/3 で、かつプレフィクスが byte 安定なので、ターン2+は自動 prefix キャッシュが
  99% ヒットし実 prefill は毎ターン約100トークン。差を生むのは**クライアントの
  プロンプト安定性**であって、モデルやサーバではない。
- tool-use は通るが小さな精度の綻び（生成ファイル名の崩れ等）があり、agentic な
  安定性は商用 Claude に及ばない。
- **したがって最大の価値は「プロンプト・プレフィクスを安定させられる用途」**
  （自作アプリ/スクリプトのバックエンド、短プロンプトのバッチ、テンプレ反復、RAG、
  **および Codex CLI のようにプレフィクス安定なエージェント**）。ここでは warm で
  sub-second になり実用十分。一方フルローカルの **Claude Code** 日常運用は CC 側の
  プロンプト先頭非安定性ゆえ非現実的で、本番は素の `claude`（サブスク）を使う、
  という第7節の住み分けが実測で裏づけられた。

## 9. 将来の論点: 推論 API を「エージェントのバックエンド」として提供するとき

本書の実測（§8）は「ローカル MLX を Claude Code のバックエンドにする」検証だが、
より一般に **推論 API を提供する側**から見ると、利用形態が「ステートレスな
one-shot 推論」から「AI エージェントのバックエンド」へ広がると負荷の質が変わる。
今はエージェント向けはオプショナルだが、間違いなく主流になる。設計上の論点を
§8 の実測から整理する。

### 9.1 ステートレス推論 と エージェント・バックエンドの違い

- **ステートレス推論**: 各リクエストは独立・短く有界。prefill コストも容量見積りも素直。
- **エージェント・バックエンド**: 1ターンごとに蓄積コンテキスト全部（CC で実測
  44K〜、会話とともに増える）を毎回再送し、それを何十ターンも回す。つまり負荷は
  **コンテキスト長 × ターン数 × 同時エージェント数** で効き、prefill が支配的になる。
  「短プロンプト前提」でサイジングした提供側は、エージェント流入で容量が破綻する。

### 9.2 性能劣化時に何ができるか（提供側 / ユーザ側のレバー）

- **提供側**: prefix/KV cache・continuous batching（§8 で 36〜50倍を実測）、admission
  control、リクエスト毎のトークン上限、コンテキスト長上限、オートスケール、キュー。
  ただし **prefix cache はプレフィクスが byte 安定な時しか効かない**（§8 結論）。CC の
  ように動的ヘッダを混ぜるクライアントには効かないので、**明示的なキャッシュ制御**
  （Anthropic の prompt caching の `cache_control` breakpoint 相当）を提供 API 側で
  用意できるかが鍵になる。**本家 Claude Code が 44K/ターンを送ってなお実用的なのは、
  まさに Anthropic がサーバ側でこの明示キャッシュをやっており、CC がそれを駆動する
  ように設計されているから**。提供側が「自動 byte-prefix キャッシュだけ」だと
  エージェントには効かない。
- **ユーザ側**: プロンプト圧縮、tool 定義の削減、履歴の短縮、モデル選択、レイテンシ許容。

### 9.3 エージェント対応を見越した API 設計

- **Anthropic ネイティブ・エンドポイント**を持つ（CC/エージェントが翻訳プロキシ無しで
  つながる。cairn が vllm-mlx を選んだ理由＝§2）。
- **tool-call パーサ**・**長コンテキスト**・**明示キャッシュ制御**を備える。
- これらが揃えば「`ANTHROPIC_BASE_URL` を向けるだけ」でアプリ無改造で差し替わる。
- **クライアント側の含意（§8(3) Codex の実証）**: クライアントがプロンプト先頭を
  byte 安定に保てば、明示キャッシュが無くても**自動 prefix キャッシュだけでエージェントの
  コンテキストを吸収できる**（Codex はターン2+で 99% ヒット）。逆に CC のように先頭へ
  動的バイトを混ぜる設計は、サーバ側の明示キャッシュ（cache_control honor）が無いと
  救えない。提供側は「明示キャッシュを実装する」か「クライアントに安定プレフィクスを
  促す」かのどちらかが要る。

### 9.4 コスト訴求（差し替えだけで安くなるか）

- 価値命題は「**従量課金のフロンティア API が高くなった時、バックエンドを差し替える
  だけでコストを下げられる**」。アプリ無改造（`ANTHROPIC_BASE_URL`）で効くのが魅力。
- ただし実測の含意として、エージェント負荷は **prefill ヘビー**なので、コストは
  「トークン単価が安い」だけでは決まらず「**その prefill 量をハードが捌けるか**」で
  決まる。Apple silicon は prefill 律速（§8）なので、自前ホストでのコスト優位は限定的。
- 一方、**オープンモデルの従量 API**（例: GLM 系は出力単価がフロンティアの約 1/11）
  なら差し替えによるコスト削減は具体的に効く。「自前ホスト vs 安い従量 API」は
  分けて評価すべき。

## 10. 既知の課題 / TODO

- **検証済み（§8 再測定）**: vllm-mlx は Anthropic `cache_control` を honor しない。
  billing-header strip はあるが、実 CC はヘッダ以外の揮発バイトを先頭に混ぜるため
  ターン2でも `cached=3` で全 bust する。ローカル CC を救うには次のどちらかが要る:
  - **(i) CC が先頭に混ぜる揮発バイトを特定し strip 範囲を広げる**。実 CC リクエストの
    生 body をキャプチャ（ロギングプロキシ等）して、トークン4付近の差分を突き止める。
  - **(ii) vllm-mlx に Anthropic `cache_control` の honor を実装**し、CC の breakpoint で
    segment 単位キャッシュを効かせる（本家と同じ機構）。こちらが本筋。
- prefill 律速の緩和: prompt 圧縮、tool 定義の削減、prefix cache が効く呼び出し方の
  検討（自動 byte-prefix は CC の動的ヘッダで bust するため、明示キャッシュが本筋）。
- Codex プロファイルは `OPENAI_BASE_URL`/`OPENAI_API_KEY` のみで、Codex の
  `config.toml`（model_provider）設定が別途必要なケースがある。要追補。
- 6/8bit モデルや他モデル（GLM 等）の切り替え UX。
- `bench` に prefill ベンチ（大プロンプト）を追加し、実運用に近い指標を出す。

## 11. 実装方針

- Python 3.12+（`type` 文・`StrEnum` 相当・`Self`・PEP 604）。
- 型安全（pyright strict、ランタイム依存ゼロの stdlib 構成）。
- ruff + pytest。バックエンド（vllm-mlx）は `uv tool install vllm-mlx` で別途導入。
