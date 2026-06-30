# ローカルモデルの検証（精度・速度）と考察

cairn 経由で Claude Code / Codex をローカル MLX モデルのバックエンドで動かしたときの、
速度と精度の実測、およびそこから言える考察。

実測環境: M4 Max / 64GB、`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`、vllm-mlx。

---

## 計測方法

vllm-mlx の scheduler はリクエストごとに、入力トークン数・prefix キャッシュ再利用数・
実 prefill 数（実際に計算したトークン数）をログに出す:

```
[cache_fetch] HIT prompt_tokens=15999 cached=15903 remaining=96
[schedule]    prompt_tokens=15999 tokens_to_prefill=96, 15903 cached
```

同一モデル・同一タスクで両 CLI を走らせ、agentic ループ中の各リクエストのこの値を
比較した。`tokens_to_prefill`（実 prefill 数）が体感速度の本体である。

## 速度

### 素のスループット

| 項目 | 値 |
| --- | --- |
| warm 生成スループット | 91 tok/s（短プロンプト） |
| prefix キャッシュ再利用 | 同一プレフィクス 36倍 / tools 一致 50倍 |
| Metal メモリ | active 21.6GB / peak 22.3GB（4bit 30B + KV） |

→ モデルもサーバも単体では十分速い。

### Claude Code（45K プロンプト、同一セッション）

| リクエスト | prompt_tokens | cached | 実 prefill |
| --- | --- | --- | --- |
| ターン1 | 45,045 | 3 | 45,042 |
| ターン2 | 45,193 | 3 | 45,190 |

→ ターン2でもキャッシュが効かず、毎ターン 45K を丸ごと prefill。Apple silicon の
prefill 速度では 1 ターン約 3〜4 分（188 tok の出力に 215.6s ＝実質 0.9 tok/s）。

### Codex CLI（同じバックエンド・同じタスク、Responses API）

| リクエスト | prompt_tokens | cached | 実 prefill |
| --- | --- | --- | --- |
| ターン1 | 15,852 | 3 | 15,849（cold） |
| ターン2 | 15,999 | 15,903 | **96** |
| ターン3 | 16,149 | 16,033 | **116** |

→ プロンプトが CC の約 1/3、初回 cold 以降は自動 prefix キャッシュが 99% ヒットし、
実 prefill は毎ターン約100トークン。

### 比較

| | CC ターン2 | Codex ターン2 |
| --- | --- | --- |
| 実 prefill | **45,190** | **96** |
| キャッシュ | 全 bust | 99% hit |
| 体感 | 3〜4分/ターン | 初回 cold のみ、以降ほぼ瞬時 |

## 精度

- **tool-use は機能する**。ファイルの読み書き・shell 実行を伴う agentic ループは通る。
- **複数ファイルの生成も可能**。Codex（ローカルモデル）に小さなサイト生成を指示すると
  `index.html` / `styles.css` / `server.py` を生成し、実際にブラウザで表示できた。
- **コード読解も正確**。`backend.py` を読ませて2文で説明させたところ、内容は妥当だった。
- **ただし小さな綻びが出る**。生成した FizzBuzz の docstring 先頭に余分な文字が混入する
  （`"F fizzbuzz function..."`）など、商用モデルほど綺麗には出ない。30B / 4bit の限界。

→ 「動くが、ワンショットの正確性・仕上がりは商用に一段及ばない」。下書き生成や
反復作業には実用十分、最終成果物をそのまま信頼するには検証が要る。

## 考察

### 速度差の正体: クライアントのプロンプト・プレフィクス安定性

遅さの原因はモデルでもサーバでもなく、**クライアントがプロンプト先頭を byte 安定に
保つか**である。

- vllm-mlx の自動 prefix キャッシュは正しく機能する（同一プレフィクスで 36〜50倍）。
- **Codex** はプロンプト先頭を安定させるため、自動キャッシュだけでターン跨ぎに効く。
- **Claude Code** は先頭に per-request の揮発バイトを混ぜる。これは本家 Anthropic の
  サーバ側 prompt caching（`cache_control` breakpoint）と一体で設計されているため本家では
  問題にならないが、ローカルでは自動キャッシュがトークン4付近で全 bust する。

### サーバ側で吸収できるか

- vllm-mlx は Anthropic の `cache_control` を **honor しない**（リクエストモデルに
  フィールドが無く黙って捨てる）。
- 代わりに CC の `x-anthropic-billing-header`（per-request ハッシュ）を strip して
  自動キャッシュを救う実装を持つ。ヘッダだけが変わる合成ケースでは 100% ヒットを確認
  できるが、**実 CC はヘッダ以外の揮発バイトも先頭に混ぜるため、strip 込みでもターン2で
  `cached=3`**。現状サーバ側の最適化だけでは実 CC の prefill は救えない。

### 実用可否

- **Apple Silicon でローカルモデルをエージェントのバックエンドにするなら Codex が実用的、
  Claude Code は非現実的**。Codex はプレフィクスが安定で自動キャッシュが効く。CC は本家の
  サーバ側明示キャッシュ前提のため、ローカルでは毎ターン全 prefill になる。
- フルローカルの CC 日常運用は避け、本番の難タスクは素の `claude`（サブスク直結）を使う。
  ローカルモデルは「プレフィクスが安定する用途」で活きる: 自作アプリ/スクリプトの
  バックエンド、短プロンプトのバッチ、テンプレ反復、RAG、そして Codex CLI。

### ローカルでフロンティア級を狙うなら（ハード観点）

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
- コスト: オープンモデルの従量 API は商用最上位の 1/10 前後。自前ホストの優位は
  プライバシー/オフライン/大量バッチに限られる。
