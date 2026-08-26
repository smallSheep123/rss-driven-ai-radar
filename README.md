# RSS-driven AI Radar

一个面向 AI Agent 的轻量 RSS 情报雷达。它不是 FreshRSS 的 Python 复刻版，也不以 Dashboard 为核心；它只保留对 AI 最有价值的信息链路：

```text
RSS / Atom 来源
   ↓
低成本抓取标题、摘要、链接、来源、时间
   ↓
SQLite 去重 + 短期记忆
   ↓
AI 第一轮相关性判断
   ├─ 不值得：只保留短期 RSS 回波
   └─ 值得：访问原网页提取全文
                    ↓
              提供给 Agent 深挖
```

## 为什么 v0.1 不做 Dashboard

因为主要消费者是 AI Agent，而不是人类浏览器。第一版优先把 CLI、JSON、数据生命周期、缓存、来源健康度、AI 筛选、全文抓取做完整。需要人工观察时，用 `stats` / `latest` / `article` 已经足够。

后续可以加一个只读 Dashboard / TUI，用于看来源健康度、手动 pin、观察命中率，但不让它成为核心依赖。

## 时效性 / 记忆生命周期

默认配置：

- 普通 RSS 条目：3 天后自动删除
- AI 选中条目：14 天后删除
- 手动 `pin`：永久保留
- 运行日志：30 天后删除
- 每次 `update` / `run` 后自动 cleanup

这样数据库不会无限长大。

## 功能

- RSS / Atom 抓取
- HTTP `ETag` / `Last-Modified` 条件请求，减少无效流量
- SQLite 持久化
- GUID/URL 去重
- Feed 分类
- Feed 健康度 / 连续失败数
- RSS 条目改变后自动重新进入 AI 评分
- OpenAI-compatible `/chat/completions` AI 筛选
- 只有被 AI 选中的条目才抓取原网页
- Trafilatura 正文提取
- 3 天 / 14 天 / Pin 生命周期
- OPML 导入
- JSON CLI 输出，方便 OpenClaw / Codex / Claude Code 等 Agent 调用
- systemd timer 示例
- `doctor` 自检

## 安装

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp feeds.example.yaml feeds.yaml
python run.py init-db
```

## 从 OPML 导入

```bash
python run.py import-opml Ray-RSS-Starter-V2.opml --out feeds.yaml
```

## 只抓 RSS，不调用 AI

```bash
python run.py update
python run.py latest --limit 30
python run.py stats
```

## AI 筛选

把 `config.yaml` 中 `ai.enabled` 改为 `true`，然后通过环境变量提供 Key：

```bash
export RADAR_API_KEY='...'
python run.py run
```

完整 `run` 流程：

```text
更新 RSS
→ 只评分未评分/发生变化的条目
→ score >= threshold 且 fetch_full=true
→ 抓原网页全文
→ 清理过期数据
```

## Agent 调用

```bash
python run.py latest --limit 50
python run.py latest --selected --limit 20
python run.py article 123
python run.py pin 123
python run.py stats
python run.py doctor
```

全部输出 JSON。

## 本机定时运行

这个项目默认就是给本机 AI / CLI 使用，不要求服务器。

Windows 推荐使用“任务计划程序”，程序填写你的虚拟环境 Python，例如：

```text
C:\path\to\rss-driven-ai-radar\.venv\Scripts\python.exe
```

参数：

```text
C:\path\to\rss-driven-ai-radar\run.py run
```

起始于：

```text
C:\path\to\rss-driven-ai-radar
```

建议每 30~60 分钟运行一次。每次 `run` 已经自带过期清理，因此不需要另建 cleanup 任务。

Linux/macOS 用户如果需要，仓库 `examples/` 仍保留 systemd timer 示例。

## v0.1 的边界

- 不绕过登录墙、付费墙、验证码、反爬机制
- 不做浏览器自动化
- 不默认保存网页 HTML，只保存抽取出的正文文本
- 不做向量数据库
- 不做复杂多 Agent
- Dashboard 暂缓，避免工具变重

## 下一阶段

- GitHub / Hugging Face /论文链接自动识别
- 图片候选提取
- 公众号选题评分
- 选中条目的二次深度总结
- 来源优先级 / 信任分
- 只读 Web Dashboard 或 TUI
- MCP / OpenClaw Tool wrapper
