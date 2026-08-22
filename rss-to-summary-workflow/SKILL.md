---
name: rss-to-summary-workflow
description: Orchestrate multi-skill workflows (e.g., run rss-article-saver, then daily-article-summarizer) with ordered steps, per-step timing, and run logs. Also declared as a desktop Workflow Engine package via cctools.skill.yaml. Use when Codex needs to run several skills in sequence, keep cron working, or reason about desktop workflow scheduling.
allowed-tools: Bash,Write,Read
---

# RSS 到每日总结 Workflow

本包同时支持两条执行路径，互不替代：

1. **本地 / cron launcher**：`scripts/run_workflow.py` + `workflows.yaml`
2. **桌面端 Workflow Engine**：`cctools.skill.yaml` 中的 `kind: workflow` DAG

## 本地 / cron

本地 launcher 需要外部注入内容根（Broker 场景由 openmind-app 自动注入）：

```bash
export OPENMIND_ROOT=<用户在 openmind-app 选择的数据根>
uv run --project rss-article-saver python rss-to-summary-workflow/scripts/run_workflow.py \
  --workflow rss-to-summary
```

常用命令（在本 skill 目录或仓库根目录）：

```bash
python scripts/run_workflow.py --list
python scripts/run_workflow.py --workflow rss-to-summary
python scripts/run_workflow.py --dry-run --workflow rss-to-summary
```

`workflows.yaml` 默认步骤（与历史 cron 一致）：

1. `rss-article-saver`
2. `daily-article-summarizer`

每步结果追加到 `logs/workflow_runs.jsonl`；cron stdout/stderr 追加到 `logs/cron.log`。

## 桌面端 Workflow Engine

宿主只读取 `cctools.skill.yaml`，按 Skill ID / 版本范围调度子 Run，不调用
`run_workflow.py`，也不读取 `workflows.yaml`。

桌面端步骤：

1. `fetch` → `rss-article-saver@^1.0.0`
2. `summarize` → `daily-article-summarizer@^1.0.0`（依赖 fetch）
3. `select-topic` → `daily-topic-selector@^1.0.0`（依赖 summarize）

所有子 Run 共享同一个 `mymindRoot` 快照和 Workflow 互斥锁；Secret 由 Broker
在执行时绑定。

## 配置边界

| 文件 | 用途 |
|------|------|
| `cctools.skill.yaml` | 桌面端唯一执行契约 |
| `workflows.yaml` | 本地/cron launcher 步骤配置 |
| `scripts/run_workflow.py` | 本地/cron 入口 |
| `SKILL.md` | Agent / 人类说明 |

本地 launcher 可沿仓库祖先解析 skill 路径并使用各子 skill 自己的 `.env`
（仅当该文件存在时加载）；内容根由外部注入的 `OPENMIND_ROOT` 提供，
未设置时各步骤直接报错停止。`OPENMIND_SKILL_DATA_DIR` 未设置时默认为
各子 skill 目录（沿用 skill 目录内既有 cache）。

桌面端不读取工作区 `.env`，不沿源码祖先推断内容根，由 Broker 注入同样绑定。

## Failure Behavior

本地 launcher：任一步失败或超时即停止，不执行后续步骤。  
桌面端：遵循 Workflow Engine 的 retry / resume 语义，可单独重试失败步骤。
