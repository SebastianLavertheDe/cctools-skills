---
name: x-following-fetcher
description: Fetches latest posts from X (Twitter) users you follow and summarizes them as a markdown table
allowed-tools: Bash,Write,Read
# desktop-app 执行配置
title: X 动态抓取
group: 采集
cwd: x-following-fetcher
runner: uv
args:
  - run
  - python
  - main.py
permission: edit
outputArtifacts:
  - mymind/article/${date}/x_*.md
  - mymind/post/${date}/
timeoutMs: 7200000
surfacesAsSource:
  sourceId: x-following
  sourceName: X 关注动态
  kind: x
artifactRoot: mymind/post
---

Desktop App contract: use `cctools.skill.yaml` as the runnable contract. The
legacy `curl.txt` files are credentials and are excluded from installed
packages. The Broker injects an approved structured session JSON as
`X_FETCHER_CREDENTIAL_JSON`; the Skill uses Python HTTP requests and never
executes the session as a shell command. Caches and external-fetch logs belong
to `CCTOOLS_SKILL_DATA_DIR`.
