# cairn — プロジェクトルール

## 概要
Apple Silicon 上のローカル MLX モデルを「各種エージェント CLI（Claude Code /
Codex …）のバックエンド」として立て、切り替えて使うための CLI。
バックエンドは `vllm-mlx`（OpenAI + Anthropic 両対応）に shell out する。

## アーキテクチャ
- `src/cairn/config.py` — 凍結 dataclass + TOML 型検証ローダ
- `src/cairn/backend.py` — `vllm-mlx serve` の起動/停止/死活（PID, loopback, health）
- `src/cairn/profiles.py` — フロントエンド別の env 差（claude / codex）
- `src/cairn/bench.py` — warm 生成スループット計測
- `src/cairn/cli.py` — argparse ディスパッチ（up/down/status/bench/claude/codex/run）

設計の検討記録と実測値は `docs/DESIGN.md` を参照。

## コマンド（開発）
- セットアップ: `uv sync`
- 型チェック: `uv run pyright`（**strict**。型エラーは0で維持）
- lint: `uv run ruff check`
- テスト: `uv run pytest`

## 規約・方針
- Python 3.12+ の最新の書き方（`type` 文・`Self`・PEP 604 union・`match`）。
- **型安全**: pyright strict を通すこと。`Any` の素通しを避ける。
- ランタイム依存ゼロ（stdlib のみ）。バックエンドは別途 `uv tool install vllm-mlx`。
- **サブスク（Claude Pro/Max）の OAuth トークンは絶対に扱わない**。cairn は
  ローカルモデル専用。サブスクは素の `claude` 直結で使う（規約遵守）。

## 禁止事項
- バックエンド（vllm-mlx）を import しない（PATH 越しに呼ぶ）。
- サーバを 0.0.0.0 等の外部にバインドしない（loopback 限定）。
