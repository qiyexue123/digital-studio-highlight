#!/usr/bin/env python3
"""
ISUX AI Highlight 自动化引擎 v2
====================================
功能：
  1. 从 RSS + 搜索 + 直接爬取 多路来源抓取最新 AI 资讯
  2. 过滤、评分、生成结构化 timeline 事件
  3. 自动把新事件插入 index.html 时间线最顶部
  4. git commit + push 到 GitHub Pages
  5. 输出变更报告

用法：
  python3 engine.py --run     # 完整运行（抓取+插入+推送）
  python3 engine.py --fetch   # 只抓取，打印结果
  python3 engine.py --push    # 只推送当前 git 变更
  python3 engine.py --report  # 打印上次运行报告
"""

import json, sys, os, time, hashlib, argparse, subprocess, re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR    = Path(__file__).parent
CACHE_FILE  = BASE_DIR / "data" / "_cache.json"
REPORT_FILE = BASE_DIR / "data" / "_last_report.md"
INDEX_HTML  = BASE_DIR / "index.html"

# ──────────────────────────────────────────────────────
# 数据源（多路）
# ──────────────────────────────────────────────────────
def _today_queries():
    """动态生成含今日日期的搜索查询"""
    today = datetime.now().strftime("%Y年%m月%d日")
    ymd   = datetime.now().strftime("%Y-%m-%d")
    return [
        {"name": "搜索·今日AI发布",   "type": "search",
         "query": f"AI 大模型 发布 上线 {today}", "lang": "zh-CN", "weight": 1.3},
        {"name": "搜索·今日AI产品",   "type": "search",
         "query": f"AI产品 发布 热议 {today}", "lang": "zh-CN", "weight": 1.2},
        {"name": "搜索·模型发布今日", "type": "search",
         "query": f"Claude GPT Gemini Grok 发布 {today}", "lang": "zh-CN", "weight": 1.3},
        {"name": "搜索·今日AI英文",   "type": "search",
         "query": f"AI model released launched {ymd}", "lang": "en", "weight": 1.1},
    ]

RSS_SOURCES = _today_queries() + [

    # 中文 RSS
    {"name": "36氪·AI",    "type": "rss",
     "url": "https://36kr.com/feed", "weight": 1.1},
    {"name": "InfoQ·AI",   "type": "rss",
     "url": "https://feed.infoq.com/", "weight": 1.0},
    {"name": "CSDN·AI",    "type": "rss",
     "url": "https://blog.csdn.net/rss/list?type=1&tagId=10010201", "weight": 0.8},

    # 英文 RSS — HN
    {"name": "HN·AI Agent", "type": "rss",
     "url": "https://hnrss.org/newest?q=AI+agent&count=15", "weight": 1.0},
    {"name": "HN·Claude",   "type": "rss",
     "url": "https://hnrss.org/newest?q=Claude&count=10",   "weight": 1.0},
    {"name": "HN·GPT",      "type": "rss",
     "url": "https://hnrss.org/newest?q=GPT&count=10",      "weight": 0.9},
    {"name": "HN·Best·AI",  "type": "rss",
     "url": "https://hnrss.org/best?q=AI&count=10",         "weight": 1.0},

    # 英文 RSS — 官方博客
    {"name": "OpenAI Blog", "type": "rss",
     "url": "https://openai.com/news/rss.xml",            "weight": 1.2},
    {"name": "Anthropic Blog","type":"rss",
     "url": "https://www.anthropic.com/news/rss.xml",      "weight": 1.2},
    {"name": "Google Deepmind","type":"rss",
     "url": "https://deepmind.google/blog/rss/feed.xml",   "weight": 1.2},
    {"name": "MIT Tech Review","type":"rss",
     "url": "https://www.technologyreview.com/feed/",       "weight": 0.9},
]

# 高权重关键词：命中即大幅加分
HIGH_KW = [
    "发布","上线","推出","宣布","release","launch","announce",
    "大模型","智能体","agent","agentic","llm","gpt","claude","gemini",
    "opus","sonnet","grok","qwen","千问","deepseek","glm",
    "多模态","multimodal","context","benchmark","agi",
    "claude code","codex","cursor","copilot",
    "设计","ux","ui","figma","设计师",
]
MED_KW = [
    "openai","anthropic","google","microsoft","xai","字节","阿里","腾讯","百度","华为",
    "产品","app","工具","功能","更新","升级","版本",
    "ai","人工智能","机器学习",
]

TBA_SCRIPT = Path("/usr/local/lib/.nvm/versions/node/v22.17.0/lib/node_modules/openclaw/skills/tencent/edgebrowser_search/scripts/tba_search.py")

# ──────────────────────────────────────────────────────
# 日志
# ──────────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts  = datetime.now().strftime("%H:%M:%S")
    pre = {"INFO":"ℹ️","OK":"✅","WARN":"⚠️","ERR":"❌"}.get(level,"·")
    print(f"[{ts}] {pre} {msg}")


# ──────────────────────────────────────────────────────
# 缓存
# ──────────────────────────────────────────────────────
def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text("utf-8"))
    return {"seen_hashes": [], "last_run": None, "timeline_ids": []}

def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), "utf-8")

def item_hash(title, url=""):
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()[:12]


# ──────────────────────────────────────────────────────
# 抓取：RSS
# ──────────────────────────────────────────────────────
def fetch_rss(source, timeout=15):
    items = []
    try:
        cmd = ["curl","-s","--max-time",str(timeout),
               "-A","Mozilla/5.0 (ISUX-AI-Bot/2.0)", source["url"]]
        r = subprocess.run(cmd, capture_output=True, timeout=timeout+5)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError(f"curl 失败 {r.returncode}")
        raw = r.stdout.decode("utf-8", errors="replace")
        if "<html" in raw[:300].lower() or "<!doctype" in raw[:300].lower():
            raise RuntimeError("返回 HTML（被拦截）")

        # 修复常见 XML 问题
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
        raw = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', raw)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw.encode("utf-8"))
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for e in entries[:20]:
            def txt(tag, alt=""):
                el = e.find(tag) or e.find(f"atom:{tag}", ns)
                return (el.text or el.get("href","") if el is not None else alt).strip()
            title = txt("title"); link = txt("link")
            desc  = re.sub(r"<[^>]+>", "", txt("description") or txt("summary") or txt("content"))[:500]
            date  = txt("pubDate") or txt("published") or txt("updated")
            if title:
                items.append({"title":title,"url":link,"desc":desc,
                               "date":date,"source":source["name"],"weight":source["weight"]})
        log(f"  {source['name']}: {len(items)} 条", "OK")
    except Exception as e:
        log(f"  {source['name']}: 失败 — {e}", "WARN")
    return items


# ──────────────────────────────────────────────────────
# 抓取：TBA 搜索（中文内网搜索）
# ──────────────────────────────────────────────────────
def fetch_search(source, timeout=20):
    items = []
    if not TBA_SCRIPT.exists():
        log(f"  {source['name']}: TBA 脚本不存在，跳过", "WARN")
        return items
    try:
        lang = source.get("lang", "zh-CN")
        country = "cn" if "zh" in lang else "us"
        cmd = ["python3", str(TBA_SCRIPT), source["query"], country, lang]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(TBA_SCRIPT.parent.parent))
        lines = r.stdout.splitlines()
        cur = {}
        for line in lines:
            line = line.strip()
            if re.match(r'^\d+\.', line):
                if cur.get("title"):
                    items.append(cur)
                title = re.sub(r'^\d+\.\s*', '', line).split(' - ')[0].strip()
                cur = {"title":title,"url":"","desc":"",
                       "source":source["name"],"weight":source["weight"],"date":""}
            elif line.startswith("链接:"):
                cur["url"] = line[3:].strip()
            elif line.startswith("摘要:"):
                raw_desc = line[3:].strip()
                cur["desc"] = raw_desc[:400]
                # 从摘要中提取日期
                extracted = _extract_date_from_text(raw_desc + " " + cur["title"])
                if extracted:
                    cur["date"] = extracted
        if cur.get("title"):
            items.append(cur)
        log(f"  {source['name']}: {len(items)} 条（含日期: {sum(1 for i in items if i['date'])} 条）", "OK")
    except Exception as e:
        log(f"  {source['name']}: 失败 — {e}", "WARN")
    return items


def _extract_date_from_text(text):
    """从文本中提取发布日期（支持多种中英文格式）"""
    # 中文：X天前 / X小时前
    m = re.search(r'(\d+)\s*天前', text)
    if m:
        days = int(m.group(1))
        from datetime import timedelta
        dt = datetime.now() - timedelta(days=days)
        return dt.strftime("%Y-%m-%d")
    m = re.search(r'(\d+)\s*小时前', text)
    if m:
        return datetime.now().strftime("%Y-%m-%d")
    m = re.search(r'(\d+)\s*分钟前|刚刚|今天', text)
    if m:
        return datetime.now().strftime("%Y-%m-%d")
    # YYYY年MM月DD日
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    # YYYY-MM-DD
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # MM/DD/YYYY or DD/MM/YYYY
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    # "Jan 15, 2026" / "15 Jan 2026"
    months = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
              "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
    m = re.search(r'(\w{3})\s+(\d{1,2})[,\s]+(\d{4})', text, re.I)
    if m and m.group(1).lower() in months:
        return f"{m.group(3)}-{months[m.group(1).lower()]}-{m.group(2).zfill(2)}"
    m = re.search(r'(\d{1,2})\s+(\w{3})[,\s]+(\d{4})', text, re.I)
    if m and m.group(2).lower() in months:
        return f"{m.group(3)}-{months[m.group(2).lower()]}-{m.group(1).zfill(2)}"
    return ""


# ──────────────────────────────────────────────────────
# 评分
# ──────────────────────────────────────────────────────
def score(item):
    text = (item["title"] + " " + item.get("desc","")).lower()
    s = item["weight"] * 10
    for kw in HIGH_KW:
        if kw in text: s += 8
    for kw in MED_KW:
        if kw in text: s += 2
    # 新鲜度加分（今天/昨天）
    today = datetime.now().strftime("%Y-%m-%d")
    if today in item.get("date","") or datetime.now().strftime("%b %d") in item.get("date",""):
        s += 15
    return s


# ──────────────────────────────────────────────────────
# 主抓取入口
# ──────────────────────────────────────────────────────
def run_fetch():
    log("=== 开始多路抓取 ===")
    cache = load_cache()
    seen  = set(cache.get("seen_hashes", []))
    all_items = []

    for src in RSS_SOURCES:
        if src["type"] == "rss":
            all_items.extend(fetch_rss(src))
        elif src["type"] == "search":
            all_items.extend(fetch_search(src))
        time.sleep(0.3)

    # 去重
    new_items, new_hashes = [], []
    seen_titles = set()
    for item in all_items:
        h = item_hash(item["title"], item["url"])
        title_norm = item["title"][:30].lower()
        if h not in seen and title_norm not in seen_titles:
            item["_hash"] = h
            new_items.append(item)
            seen_titles.add(title_norm)
            new_hashes.append(h)

    new_items.sort(key=score, reverse=True)
    log(f"共抓取 {len(all_items)} 条，去重后新增 {len(new_items)} 条", "OK")
    return new_items


# ──────────────────────────────────────────────────────
# AI 判断：是否值得写进时间线（≥60分 且标题含关键发布词）
# ──────────────────────────────────────────────────────
RELEASE_KW = ["发布","上线","推出","宣布","release","launch","announce","发行","开源"]

# ──────────────────────────────────────────────────────
# 质量过滤：只留真正的"发布事件"，丢弃综述/榜单/趋势文章
# ──────────────────────────────────────────────────────

# 黑名单词：含这些词的直接丢弃（综述/榜单/趋势文章）
BLACKLIST_KW = [
    "盘点","汇总","合集","榜单","趋势","指南","白皮书","全景","总结","回顾","综述",
    "对比","比较","评测","top10","top 10","最佳","完全指南","全面解析","深度解析",
    "timeline","tracker","so far","complete list","every major","best ai",
    "2026年ai","2026 ai","年度","前瞻","展望","未来","入门","教程","学习",
]

# 必须含有"具体产品/模型名 + 发布动词"，才算发布事件
PRODUCT_NAMES = [
    "claude","gpt","gemini","grok","qwen","千问","deepseek","glm","llama",
    "opus","sonnet","haiku","codex","cursor","copilot","manus","openClaw",
    "sora","seedance","seedream","midjourney","stable diffusion","flux",
    "apple","iphone","macbook","pixel","samsung","华为","小米","vivo","oppo",
    "figma","notion","obsidian","linear","arc","perplexity","mistral",
    "kimi","moonshot","minimax","baidu","wenxin","文心","讯飞","spark",
]

def is_real_event(item):
    """判断是否是真实的具体发布/上线事件（非综述文章）"""
    title = item["title"].lower()
    desc  = item.get("desc","").lower()
    text  = title + " " + desc

    # 1. 黑名单过滤
    for bw in BLACKLIST_KW:
        if bw in text:
            return False

    # 2. 必须含具体产品/模型名
    has_product = any(p in text for p in PRODUCT_NAMES)

    # 3. 必须含发布动词
    has_release = any(kw in text for kw in RELEASE_KW)

    # 4. 必须有可解析的近期日期（date 字段非空）
    has_date = bool(item.get("date","").strip())

    return has_product and has_release and has_date


def is_timeline_worthy(item):
    s = score(item)
    # 先过质量关
    if not is_real_event(item):
        return False
    # 再过分数关（具体事件阈值可低一些）
    return s >= 45


# ──────────────────────────────────────────────────────
# 生成时间线节点 HTML
# ──────────────────────────────────────────────────────
DOT_COLORS = ["bg-blue-500","bg-violet-500","bg-emerald-500","bg-orange-400",
              "bg-cyan-500","bg-pink-500","bg-yellow-500","bg-red-500"]

def make_dot_color(item):
    text = (item["title"]+" "+item.get("desc","")).lower()
    if any(k in text for k in ["claude","anthropic"]):      return "bg-violet-500"
    if any(k in text for k in ["gpt","openai","codex"]):    return "bg-emerald-500"
    if any(k in text for k in ["gemini","google"]):         return "bg-blue-500"
    if any(k in text for k in ["grok","xai"]):              return "bg-gray-600"
    if any(k in text for k in ["千问","qwen","阿里"]):      return "bg-orange-400"
    if any(k in text for k in ["deepseek","字节","seed"]):  return "bg-cyan-500"
    if any(k in text for k in ["苹果","apple","iphone"]):   return "bg-gray-500"
    return "bg-blue-400"

def make_badge(item):
    text = (item["title"]+" "+item.get("desc","")).lower()
    if any(k in text for k in ["claude","anthropic"]):      return ("Anthropic","bg-violet-100 text-violet-700")
    if any(k in text for k in ["gpt","openai","codex"]):    return ("OpenAI","bg-green-100 text-green-700")
    if any(k in text for k in ["gemini","google"]):         return ("Google","bg-blue-100 text-blue-700")
    if any(k in text for k in ["grok","xai"]):              return ("xAI","bg-gray-100 text-gray-700")
    if any(k in text for k in ["千问","qwen","阿里"]):      return ("阿里","bg-orange-100 text-orange-700")
    if any(k in text for k in ["deepseek"]):                return ("DeepSeek","bg-cyan-100 text-cyan-700")
    if any(k in text for k in ["苹果","apple","iphone"]):   return ("Apple","bg-gray-100 text-gray-700")
    if any(k in text for k in ["设计","figma","ux","ui"]):  return ("设计","bg-pink-100 text-pink-700")
    return ("AI资讯","bg-blue-100 text-blue-700")

def format_date_label(item):
    """从 date 字段提取 YYYY.MM.DD，失败则返回 None（调用方负责过滤）"""
    d = item.get("date","").strip()
    if not d:
        return None
    # 尝试常见格式
    for fmt in ["%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(d[:25], fmt[:len(d[:25])])
            return dt.strftime("%Y.%m.%d")
        except: pass
    # 正则兜底：提取 YYYY-MM-DD
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', d)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    # 兜底：提取 DD Mon YYYY
    m = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', d)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y")
            return dt.strftime("%Y.%m.%d")
        except: pass
    return None  # 无法解析则返回 None

def build_timeline_node(item, node_id):
    date_label = format_date_label(item)
    if not date_label:
        return None   # 日期解析失败，不插入
    dot   = make_dot_color(item)
    badge_text, badge_cls = make_badge(item)
    title = item["title"][:80]
    desc  = item.get("desc","")[:280]
    link  = item.get("url","")
    link_html = f'<a href="{link}" target="_blank" class="text-xs text-blue-500 hover:underline mt-2 inline-block"><i class="fas fa-external-link-alt mr-1"></i>查看原文</a>' if link else ""

    return f"""
          <!-- AUTO:{node_id} date:{date_label} -->
          <div class="relative pb-7" id="tl-{node_id}">
            <div class="timeline-dot {dot}"></div>
            <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-4 card-hover">
              <div class="flex items-start justify-between gap-2 flex-wrap">
                <div>
                  <span class="mono text-xs font-bold text-gray-500 uppercase tracking-wide">{date_label}</span>
                  <h3 class="text-base font-bold text-gray-900 mt-1">{title}</h3>
                </div>
                <span class="tag-badge {badge_cls}">{badge_text}</span>
              </div>
              <p class="text-gray-600 text-sm mt-2">{desc}</p>
              {link_html}
            </div>
          </div>"""


# ──────────────────────────────────────────────────────
# 把新节点插入 index.html 时间线最顶部
# ──────────────────────────────────────────────────────
TIMELINE_ANCHOR = '        <div class="relative ml-4 pl-8 border-l-2 border-blue-200 space-y-0">'

def insert_timeline_nodes(new_nodes_html: list[str]) -> bool:
    if not INDEX_HTML.exists():
        log("index.html 不存在", "ERR")
        return False
    html = INDEX_HTML.read_text("utf-8")
    if TIMELINE_ANCHOR not in html:
        log("时间线锚点未找到", "ERR")
        return False
    insert_after = html.find(TIMELINE_ANCHOR) + len(TIMELINE_ANCHOR)
    injection = "\n" + "\n".join(new_nodes_html)
    html = html[:insert_after] + injection + html[insert_after:]
    INDEX_HTML.write_text(html, "utf-8")
    log(f"已插入 {len(new_nodes_html)} 个时间线节点", "OK")
    return True


# ──────────────────────────────────────────────────────
# Git commit + push
# ──────────────────────────────────────────────────────
def git_push(message: str):
    cwd = str(BASE_DIR)
    try:
        subprocess.run(["git","add","index.html","data/"], cwd=cwd, check=True)
        result = subprocess.run(["git","diff","--cached","--stat"], cwd=cwd,
                                capture_output=True, text=True)
        if not result.stdout.strip():
            log("git: 无变更，跳过提交", "INFO")
            return False
        subprocess.run(["git","commit","-m", message], cwd=cwd, check=True)
        subprocess.run(["git","push","origin","main"], cwd=cwd, check=True)
        log(f"git push 成功: {message}", "OK")
        return True
    except subprocess.CalledProcessError as e:
        log(f"git 操作失败: {e}", "ERR")
        return False


# ──────────────────────────────────────────────────────
# 生成报告
# ──────────────────────────────────────────────────────
def generate_report(new_items=None, inserted=None):
    ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
    cache = load_cache()
    lines = [
        f"# ISUX AI Highlight 自动化报告",
        f"",
        f"**运行时间**: {ts}",
        f"**上次运行**: {cache.get('last_run','N/A')}",
        f"",
    ]
    if inserted:
        lines += [f"## 🆕 本次插入时间线（{len(inserted)} 条）", ""]
        for item in inserted:
            lines.append(f"- [{score(item):.0f}分] **{item['title'][:70]}** — {item['source']}")
    if new_items:
        lines += ["", f"## 📋 全部抓取 Top 20", ""]
        for item in new_items[:20]:
            worthy = "⭐" if is_timeline_worthy(item) else "  "
            lines.append(f"- {worthy} [{score(item):.0f}分] {item['title'][:65]} — {item['source']}")
    report = "\n".join(lines)
    REPORT_FILE.write_text(report, "utf-8")
    return report


# ──────────────────────────────────────────────────────
# CLI 主入口
# ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ISUX AI Highlight 自动化引擎 v2")
    parser.add_argument("--run",    action="store_true", help="完整运行（抓取+插入时间线+推送）")
    parser.add_argument("--fetch",  action="store_true", help="只抓取，不写入")
    parser.add_argument("--push",   action="store_true", help="只推送当前 git 变更")
    parser.add_argument("--report", action="store_true", help="打印上次运行报告")
    parser.add_argument("--max",    type=int, default=5,  help="每次最多插入节点数（默认 5）")
    args = parser.parse_args()

    if args.fetch:
        items = run_fetch()
        print(f"\n=== Top 20（⭐=值得上时间线）===")
        for i, item in enumerate(items[:20]):
            worthy = "⭐" if is_timeline_worthy(item) else "  "
            print(f"  {i+1:2d}. {worthy} [{score(item):.0f}分] {item['title'][:65]}")
        return

    if args.push:
        git_push("chore: 手动推送更新")
        return

    if args.report:
        if REPORT_FILE.exists():
            print(REPORT_FILE.read_text("utf-8"))
        else:
            print("暂无报告，请先运行 --run")
        return

    if args.run:
        log("=== ISUX AI Highlight Engine v2 启动 ===")
        ts_start = time.time()

        # 1. 抓取
        new_items = run_fetch()

        # 2. 过滤出值得上时间线的条目
        cache = load_cache()
        existing_tl_ids = set(cache.get("timeline_ids", []))
        worthy = [
            item for item in new_items
            if is_timeline_worthy(item)
            and item_hash(item["title"], item["url"]) not in existing_tl_ids
        ][:args.max]

        if not worthy:
            log("本次无值得插入时间线的新事件", "INFO")
            report = generate_report(new_items, [])
            print(f"\n{report}")
            return

        # 3. 生成 HTML 节点（跳过日期解析失败的）
        today = datetime.now().strftime("%Y%m%d")
        nodes_html = []
        valid_worthy = []
        for i, item in enumerate(worthy):
            node_id = f"{today}-{i+1:02d}"
            node = build_timeline_node(item, node_id)
            if node:
                nodes_html.append(node)
                valid_worthy.append(item)

        if not nodes_html:
            log("所有候选条目日期解析失败，不插入", "WARN")
            return

        # 4. 插入 index.html
        ok = insert_timeline_nodes(nodes_html)
        if not ok:
            log("插入失败，中止", "ERR")
            return

        # 5. 更新缓存（用 valid_worthy 而非 worthy）
        new_hashes = [item_hash(i["title"], i["url"]) for i in valid_worthy]
        all_new_hashes = [item_hash(i["title"], i["url"]) for i in new_items]
        cache["seen_hashes"] = list(set(cache.get("seen_hashes",[]) + all_new_hashes))
        cache["timeline_ids"] = list(existing_tl_ids | set(new_hashes))
        cache["last_run"] = datetime.now().isoformat()
        save_cache(cache)

        # 6. Git push
        titles_short = " / ".join(i["title"][:20] for i in valid_worthy[:3])
        commit_msg = f"auto: 时间线更新 {datetime.now().strftime('%Y-%m-%d')} ({len(valid_worthy)}条: {titles_short}...)"
        git_push(commit_msg)

        # 7. 报告
        elapsed = time.time() - ts_start
        report = generate_report(new_items, valid_worthy)
        log(f"=== 完成，耗时 {elapsed:.1f}s ===", "OK")
        print(f"\n{report}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
