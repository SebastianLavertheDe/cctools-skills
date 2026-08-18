# cctools-skills

cctools 桌面应用的 Skill 分发仓库。每个顶层目录是一个独立 Skill 包（`cctools.skill.yaml` 清单 + `SKILL.md` 文档），可通过 desktop-app 的 Skill Center 从本仓库的 GitHub URL 安装。

## 安装方式

在 desktop-app → 设置 → Skills → 添加来源 → Git，输入本仓库地址：

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

Skill 通过环境变量绑定数据与配置（与仓库位置解耦）：

- `CCTOOLS_MYMIND_ROOT`：mymind 数据根（文章/日报/Reddit 输出）
- `CCTOOLS_PROVIDERS_FILE`：AI provider 配置（`config/providers.yaml`，含密钥，不入库）
- desktop-app 安装运行时会自动注入；crontab 直跑时需显式设置
