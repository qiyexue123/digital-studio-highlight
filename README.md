# 数字工作室 · AI Highlight

> 数字工作室出品的 AI 前沿洞察页面，追踪 Agentic UX 时代的模型、产品与设计趋势。

## 预览

**[👉 在线预览](https://zorazwang.github.io/digital-studio-highlight/)**

## 功能特性

- **时间线** — 2025.07 → 2026.03 AI 能力跃迁关键节点
- **Agentic UX** — 从点击流到意图流的设计范式转变分析
- **模型** — Claude Opus 4.6 / GPT-5.3 Codex / Gemini 3.1 Pro 深度对比
- **产品** — OpenClaw、Notion Custom Agents、Obsidian CLI 等产品趋势
- **设计洞察** — 数字工作室出品的 5 条 Agentic 时代设计行动指南

## 自动化引擎

```bash
# 完整运行（抓取 RSS + 生成卡片 + 更新页面）
python3 engine.py --run

# 只抓取，查看新内容
python3 engine.py --fetch

# 生成审校报告
python3 engine.py --report
```

## 技术栈

- **前端**: 纯 HTML + Tailwind CSS CDN + Vanilla JS
- **数据**: `data/highlights.json` 结构化内容
- **自动化**: Python 3 + RSS 抓取 + 信号评分

## 本地预览

```bash
python3 -m http.server 9090
# 访问 http://localhost:9090
```

---

*© 2026 数字工作室 · AI Highlight · Vol.05*
