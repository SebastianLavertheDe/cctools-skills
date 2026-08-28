# cctools-skills

[openmind-app](https://github.com/SebastianLavertheDe/openmind-app) 的 Skill 分发仓库。每个顶层目录是一个独立 Skill 包（`cctools.skill.yaml` 清单 + `SKILL.md` 文档），可通过 openmind-app 的 Skill Center 从本仓库的 GitHub URL 安装。

## 安装方式

在 openmind-app → 设置 → Skills → 添加来源 → Git，输入本仓库地址：

```
https://github.com/SebastianLavertheDe/cctools-skills.git
```

## Skill 一览

| Skill | 类型 | 说明 |
|-------|------|------|
| rss-article-saver | process | RSS 订阅抓取，保存为本地 Markdown |
| daily-article-summarizer | process | 每日文章 AI 摘要 |
| rss-to-summary-workflow | workflow | 抓取 → 摘要 串联工作流 |
| reddit-fetcher | process | Reddit 热帖抓取 |
| x-following-fetcher | process | X/Twitter 关注流抓取与总结 |
| knowledge-wiki-compiler | process | 知识库 Wiki 增量编译 |
| ai-media-topic-selector | process | 自媒体每日选题生成 |
| daily-topic-selector | process | 公众号 AI 选题 + 当日 X 帖子总结 |
| daily-writing-orchestrator | process | 每日写作编排器 |
| creative-writing | process | 多平台中文内容创作 |
| collect-interest-markers | process | 兴趣标记收集 |
| skill-run-reporter | process | 技能运行日报 |
| asset-collector | process | 创作素材与证据收集 |
| agent-browser | process | Playwright 浏览器自动化 |
| video-burnin-subtitles | process | 视频字幕检测/生成与烧录 |
| VideoDownload | process | 视频下载与整理 |
| _shared | — | 技能共享 Python 库（非独立 Skill） |

## 运行时绑定

Skill 通过环境变量绑定数据与配置（与仓库位置解耦）。内容根是**用户在 openmind-app 里自选的数据根**（即数据仓根本身，扁平布局：`article/`、`post/` 等内容目录直接在根下）：

- `OPENMIND_ROOT`：内容根（唯一契约名）
- `OPENMIND_SKILL_DATA_DIR`：单个 Skill 的持久化私有数据目录（用户来源、缓存、登录状态等）；openmind-app Broker 自动注入，工作流和 cron wrapper 默认使用对应 Skill 目录，Skill 包更新不会覆盖
- `OPENMIND_RUN_DIR`：Broker 为单次运行注入的临时运行目录
- `OPENMIND_PROVIDER_*` / `OPENMIND_PROVIDERS_FILE`：AI provider 绑定（`config/providers.yaml`，含密钥，不入库）
- openmind-app 安装运行时由 Broker 自动注入；crontab 直跑时需显式设置内容根与 provider 文件

文档与配置中的相对路径均以内容根为基准（等价于 `$OPENMIND_ROOT/xxx`）。
