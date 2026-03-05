#!/usr/bin/env python3
"""
数字工作室 AI Highlight 自动化引擎
====================================
功能：
  1. 从多个 RSS / API 源抓取最新 AI 资讯
  2. 用 OpenClaw 内置 AI 对每条新闻生成结构化摘要卡片
  3. 自动更新 data/highlights.json，触发页面重新渲染
  4. 输出 diff 报告，供编辑人工审校

用法：
  python3 engine.py --run      # 完整运行一次（抓取+摘要+更新）
  python3 engine.py --fetch    # 只抓取，不写入
  python3 engine.py --render   # 只重新渲染 HTML
  python3 engine.py --report   # 输出最新更新报告
"""

import json
import sys
import os
import time
import hashlib
import argparse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "highlights.json"
CACHE_FILE = BASE_DIR / "data" / "_cache.json"
REPORT_FILE = BASE_DIR / "data" / "_last_report.md"

# ──────────────────────────────────────────
# RSS 源列表（可自由扩展）
# ──────────────────────────────────────────
RSS_SOURCES = [
    {
        "name": "HN · AI Agent",
        "url": "https://hnrss.org/newest?q=AI+agent&count=20",
        "tags": ["英文", "社区"],
        "weight": 0.9
    },
    {
        "name": "HN · Claude",
        "url": "https://hnrss.org/newest?q=Claude&count=15",
        "tags": ["英文", "模型能力"],
        "weight": 1.0
    },
    {
        "name": "HN · GPT",
        "url": "https://hnrss.org/newest?q=GPT&count=10",
        "tags": ["英文", "模型能力"],
        "weight": 0.9
    },
    {
        "name": "HN · Best",
        "url": "https://hnrss.org/best?q=AI&count=10",
        "tags": ["英文", "社区"],
        "weight": 1.0
    },
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/news/rss.xml",
        "tags": ["模型能力", "一手"],
        "weight": 1.0
    },
    {
        "name": "MIT Tech Review",
        "url": "https://www.technologyreview.com/feed/",
        "tags": ["英文", "深度"],
        "weight": 0.9
    },
]

# AI 信号关键词（用于过滤与权重计算）
SIGNAL_KEYWORDS = {
    "high": ["agent", "agentic", "opus", "gpt-5", "gemini", "claude", "llm", "模型发布",
             "大模型", "AI assistant", "多模态", "自动化", "自主", "智能体"],
    "medium": ["openai", "anthropic", "google", "microsoft", "字节", "阿里", "腾讯",
               "设计", "ux", "产品", "发布", "更新", "benchmark"],
    "low": ["AI", "人工智能", "machine learning", "deep learning"]
}


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERR": "❌"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}")


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text("utf-8"))
    return {"seen_hashes": [], "last_run": None, "item_count": 0}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), "utf-8")


def hash_item(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()[:12]


def fetch_rss(source: dict, timeout: int = 12) -> list[dict]:
    """抓取单个 RSS 源，返回条目列表（使用 curl 绕过内网 SSL 代理问题）"""
    import subprocess, re
    items = []
    try:
        cmd = [
            "curl", "-s", "--max-time", str(timeout),
            "-A", "Mozilla/5.0 (compatible; DS-AI-Bot/1.0)",
            source["url"]
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(f"curl 失败: returncode={result.returncode}")

        raw_bytes = result.stdout

        # 检测是否返回了 HTML（被 CDN 拦截）
        preview = raw_bytes[:200].decode("utf-8", errors="ignore").lower()
        if "<html" in preview or "<!doctype" in preview:
            raise RuntimeError("返回了 HTML 而非 XML（被 CDN/代理拦截）")

        # 宽松 XML 解析：移除无效控制字符
        raw_str = raw_bytes.decode("utf-8", errors="replace")
        raw_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_str)
        # 修复未转义的 & 符号（常见于中文 RSS）
        raw_str = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', raw_str)

        root = ET.fromstring(raw_str.encode("utf-8"))

        # 支持 RSS 2.0 和 Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        channel_items = root.findall(".//item")  # RSS 2.0
        if not channel_items:
            channel_items = root.findall(".//atom:entry", ns)  # Atom

        for item in channel_items[:15]:
            title_el = item.find("title")
            if title_el is None:
                title_el = item.find("atom:title", ns)
            link_el  = item.find("link")
            if link_el is None:
                link_el = item.find("atom:link", ns)
            desc_el  = item.find("description")
            if desc_el is None:
                desc_el = item.find("atom:summary", ns)
            if desc_el is None:
                desc_el = item.find("atom:content", ns)
            date_el  = item.find("pubDate")
            if date_el is None:
                date_el = item.find("atom:published", ns)
            if date_el is None:
                date_el = item.find("atom:updated", ns)

            title = (title_el.text or "").strip() if title_el is not None else ""
            link  = (link_el.text or link_el.get("href", "")).strip() if link_el is not None else ""
            desc  = (desc_el.text or "").strip() if desc_el is not None else ""
            date  = (date_el.text or "").strip() if date_el is not None else ""

            if not title:
                continue

            # 清理 HTML 标签
            desc = re.sub(r"<[^>]+>", "", desc)[:400]

            items.append({
                "title": title,
                "url": link,
                "desc": desc,
                "date": date,
                "source": source["name"],
                "tags": source["tags"],
                "weight": source["weight"]
            })

        log(f"  {source['name']}: 抓取 {len(items)} 条", "OK")
    except Exception as e:
        log(f"  {source['name']}: 抓取失败 — {e}", "WARN")

    return items


def score_item(item: dict) -> float:
    """对新闻条目计算信号强度分数"""
    text = (item["title"] + " " + item["desc"]).lower()
    score = item["weight"] * 10

    for kw in SIGNAL_KEYWORDS["high"]:
        if kw.lower() in text:
            score += 8

    for kw in SIGNAL_KEYWORDS["medium"]:
        if kw.lower() in text:
            score += 3

    for kw in SIGNAL_KEYWORDS["low"]:
        if kw.lower() in text:
            score += 1

    return score


def generate_card_stub(item: dict, rank: int) -> dict:
    """
    生成结构化摘要卡片（Stub 版本，供 AI 填充或人工审校）
    在实际接入 AI API 后，这里可以调用 Claude/GPT 生成真实摘要
    """
    score = score_item(item)

    # 根据分数判断卡片尺寸
    if score >= 40:
        size = "lg"
        card_type = "model-card" if "模型" in str(item["tags"]) else "product-card"
    elif score >= 25:
        size = "md"
        card_type = "product-card"
    else:
        size = "sm"
        card_type = "stat"

    # 自动推断 tag
    auto_tags = []
    text = (item["title"] + " " + item["desc"]).lower()
    if any(k in text for k in ["claude", "gpt", "gemini", "模型", "llm", "发布"]):
        auto_tags.append("模型能力")
    if any(k in text for k in ["agent", "agentic", "智能体"]):
        auto_tags.append("Agentic")
    if any(k in text for k in ["产品", "app", "发布", "上线"]):
        auto_tags.append("产品")
    if not auto_tags:
        auto_tags = item["tags"][:1]

    # 置信度（基于信源权重）
    confidence = int(min(95, 60 + item["weight"] * 30 + (score - 10) * 0.5))

    return {
        "id": f"auto-{hash_item(item['title'], item['url'])}",
        "size": size,
        "type": card_type,
        "tag": auto_tags[:2],
        "tagColor": ["blue", "gray"][:len(auto_tags)],
        "title": item["title"][:80],
        "desc": item["desc"][:300] if item["desc"] else f"来源：{item['source']}",
        "source": f"{item['source']} · {item['date'][:16] if item['date'] else ''}",
        "confidence": confidence,
        "link": item["url"],
        "_score": score,
        "_auto": True,
        "_needs_review": True
    }


def run_fetch() -> list[dict]:
    """抓取所有源，返回去重后的新条目"""
    log("开始抓取 RSS 源...")
    cache = load_cache()
    seen = set(cache.get("seen_hashes", []))

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        all_items.extend(items)
        time.sleep(0.5)  # 礼貌抓取

    # 去重 + 过滤已见
    new_items = []
    for item in all_items:
        h = hash_item(item["title"], item["url"])
        if h not in seen:
            item["_hash"] = h
            new_items.append(item)

    # 按分数排序
    new_items.sort(key=score_item, reverse=True)

    log(f"发现 {len(new_items)} 条新内容（共抓取 {len(all_items)} 条）", "OK")
    return new_items


def update_highlights(new_items: list[dict], max_per_run: int = 5):
    """将新内容追加到 highlights.json，并更新缓存"""
    if not new_items:
        log("无新内容，跳过更新", "INFO")
        return

    # 只取得分最高的几条
    top_items = new_items[:max_per_run]

    # 加载现有数据
    data = json.loads(DATA_FILE.read_text("utf-8"))

    # 收集已有卡片 id 集合，防止重复插入
    existing_ids: set[str] = set()
    for section in data["sections"]:
        for card in section["items"]:
            existing_ids.add(card["id"])

    # 生成卡片并追加到合适的 section
    added = []
    for item in top_items:
        card = generate_card_stub(item, 0)
        score = card["_score"]

        # 选择 section
        text = (item["title"] + " " + item["desc"]).lower()
        if any(k in text for k in ["claude", "gpt", "gemini", "模型", "llm", "benchmark"]):
            section_id = "section-models"
        elif any(k in text for k in ["agent", "agentic", "产品", "app", "发布"]):
            section_id = "section-products"
        elif any(k in text for k in ["design", "设计", "ux", "ui"]):
            section_id = "section-design"
        else:
            section_id = "section-products"

        # 找到目标 section 并追加（跳过已存在的 id）
        for section in data["sections"]:
            if section["id"] == section_id:
                if card["id"] not in existing_ids:
                    section["items"].insert(0, card)
                    existing_ids.add(card["id"])
                    added.append(card)
                    log(f"  新增卡片: [{score:.0f}分] {item['title'][:50]}...", "OK")
                break

    # 更新 meta
    data["meta"]["date"] = datetime.now().strftime("%Y-%m-%d")
    data["meta"]["_last_updated"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["_auto_count"] = data["meta"].get("_auto_count", 0) + len(added)

    # 写入
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    # 更新缓存
    cache = load_cache()
    cache["seen_hashes"] = list(set(cache.get("seen_hashes", [])) | {i["_hash"] for i in new_items})
    cache["last_run"] = datetime.now().isoformat()
    cache["item_count"] = cache.get("item_count", 0) + len(added)
    save_cache(cache)

    log(f"highlights.json 已更新，新增 {len(added)} 张卡片", "OK")
    return added


def render_html():
    """
    重新渲染 HTML（从 highlights.json 生成）
    目前为简单触发模式：写入时间戳，让前端 JS 知晓数据已更新
    完整版可接入 Jinja2 模板引擎
    """
    data = json.loads(DATA_FILE.read_text("utf-8"))
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 更新 HTML 中的时间戳（简单替换）
    html_file = BASE_DIR / "index.html"
    if html_file.exists():
        html = html_file.read_text("utf-8")
        # 更新页脚时间
        import re
        html = re.sub(
            r'数字工作室 · AI Highlight · Vol\.\d+ · [\d\.\-]+',
            f'数字工作室 · AI Highlight · {data["meta"]["issue"]} · {data["meta"]["date"]}',
            html
        )
        html_file.write_text(html, "utf-8")
        log(f"HTML 已更新: {ts}", "OK")


def generate_report(new_items: list[dict] = None) -> str:
    """生成 Markdown 格式的更新报告"""
    data = json.loads(DATA_FILE.read_text("utf-8"))
    cache = load_cache()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 数字工作室 AI Highlight 自动化报告",
        f"",
        f"**生成时间**: {ts}  ",
        f"**本次运行**: {cache.get('last_run', 'N/A')}  ",
        f"**累计自动更新**: {cache.get('item_count', 0)} 条",
        f"",
        f"## 📊 当前内容统计",
        f""
    ]

    for section in data["sections"]:
        total = len(section["items"])
        auto = sum(1 for i in section["items"] if i.get("_auto"))
        needs_review = sum(1 for i in section["items"] if i.get("_needs_review"))
        lines.append(f"- **{section['title']}**: {total} 张卡片（其中 {auto} 张自动生成，{needs_review} 张待审校）")

    if new_items:
        lines += [
            f"",
            f"## 🆕 本次新增",
            f""
        ]
        for item in new_items[:10]:
            score = score_item(item)
            lines.append(f"- [{score:.0f}分] **{item['title'][:60]}** — {item['source']}")

    lines += [
        f"",
        f"## ✅ 需要人工审校的卡片",
        f""
    ]
    for section in data["sections"]:
        for card in section["items"]:
            if card.get("_needs_review"):
                lines.append(f"- `{card['id']}` [{section['title']}] {card['title'][:60]}")

    report = "\n".join(lines)
    REPORT_FILE.write_text(report, "utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="数字工作室 AI Highlight 自动化引擎")
    parser.add_argument("--run",    action="store_true", help="完整运行（抓取+摘要+更新+渲染）")
    parser.add_argument("--fetch",  action="store_true", help="只抓取，不写入")
    parser.add_argument("--render", action="store_true", help="只重新渲染 HTML")
    parser.add_argument("--report", action="store_true", help="输出更新报告")
    parser.add_argument("--max",    type=int, default=5, help="每次最多处理条目数（默认 5）")
    args = parser.parse_args()

    if args.fetch:
        items = run_fetch()
        print(f"\n抓取结果（Top 10）：")
        for i, item in enumerate(items[:10]):
            print(f"  {i+1:2d}. [{score_item(item):.0f}分] {item['title'][:60]}")

    elif args.render:
        render_html()

    elif args.report:
        report = generate_report()
        print(report)

    elif args.run:
        log("=== 数字工作室 AI Highlight 自动化引擎启动 ===")
        new_items = run_fetch()
        added = update_highlights(new_items, max_per_run=args.max)
        render_html()
        report = generate_report(new_items)
        log("=== 运行完成 ===", "OK")
        print(f"\n{report}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
