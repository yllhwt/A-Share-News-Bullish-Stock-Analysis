# -*- coding: utf-8 -*-
"""
新闻抓取模块
支持 RSS / API / HTML 三种来源，自动去重，返回统一格式。
"""

import re
import os
import json
import time
import hashlib
import html as html_mod
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests
import feedparser

# lxml 用于 HTML 解析（站点没有 RSS 时备用）
try:
    from lxml import html as lxml_html
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

from app_config import RSS_SOURCES, DEBUG, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# 北京时区
TZ_BEIJING = timezone(timedelta(hours=8))

# 通用 session
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def _fetch_url(url, timeout=15):
    """通用抓取，返回 (status, text)"""
    try:
        r = SESSION.get(url, timeout=timeout)
        r.encoding = r.apparent_encoding or "utf-8"
        return r.status_code, r.text
    except Exception as e:
        if DEBUG:
            print(f"  [抓取失败] {url}: {e}")
        return None, None


def _hash_id(text):
    """为无 ID 的条目生成稳定 hash"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _clean_html(raw):
    """去掉 HTML 标签"""
    if not raw:
        return ""
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_mod.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def _is_a_stock_related(title):
    """快速过滤：标题是否与 A 股/产业相关"""
    keywords = [
        "A股", "股市", "板块", "涨停", "跌停", "概念", "龙头",
        "上市", "IPO", "定增", "并购", "重组", "业绩", "年报",
        "赛道", "产能", "价格", "涨价", "断供", "替代", "政策",
        "部委", "印发", "通知", "规划", "试点", "牌照",
        "光刻", "芯片", "半导体", "AI", "算力", "大模型",
        "光伏", "储能", "锂电", "新能源", "汽车",
        "医药", "创新药", "CXO", "器械",
        "消费", "白酒", "免税",
        "军工", "卫星", "低空", "飞行",
        "稀土", "钨", "铜", "锂", "钴", "锑", "钼",
        "机器人", "自动驾驶", "固态电池",
        "分红", "回购", "减持", "解禁",
    ]
    return any(kw in title for kw in keywords)


# ═══════════════════════════════════════════════════
# 各站点抓取器
# ═══════════════════════════════════════════════════

def _scrape_cls_api(url, limit=30):
    """财联社 — API 接口"""
    items = []
    try:
        # 电报快讯 API
        api_url = "https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=8.4.6"
        status, text = _fetch_url(api_url, timeout=10)
        if status != 200 or not text:
            return items

        data = json.loads(text)
        # 取滚动新闻列表
        roll_data = data.get("data", {}).get("roll_data", [])
        if isinstance(roll_data, dict):
            roll_data = list(roll_data.values())

        for entry in roll_data[:limit]:
            title = entry.get("title", "") or entry.get("brief", "")
            if not title:
                continue
            content = entry.get("content", "") or title
            ctime = entry.get("ctime", 0)
            if ctime:
                dt = datetime.fromtimestamp(ctime, tz=TZ_BEIJING)
            else:
                dt = datetime.now(TZ_BEIJING)

            items.append({
                "id": _hash_id(title),
                "title": _clean_html(title),
                "summary": _clean_html(content)[:200],
                "source": "财联社",
                "url": f"https://www.cls.cn/detail/{entry.get('id', '')}",
                "time": dt.strftime("%H:%M"),
                "date": dt.strftime("%Y-%m-%d"),
            })
    except Exception as e:
        if DEBUG:
            print(f"  [财联社解析异常] {e}")
    return items


def _scrape_wallstreetcn(url, limit=30):
    """华尔街见闻 — RSS"""
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            title = entry.get("title", "")
            if not title:
                continue
            items.append({
                "id": entry.get("id", _hash_id(title)),
                "title": _clean_html(title),
                "summary": _clean_html(entry.get("summary", ""))[:200],
                "source": "华尔街见闻",
                "url": entry.get("link", ""),
                "time": _parse_rss_time(entry),
                "date": _parse_rss_date(entry),
            })
    except Exception as e:
        if DEBUG:
            print(f"  [华尔街见闻 RSS 异常] {e}")
    return items


def _scrape_eastmoney(url, limit=30):
    """东方财富 — HTML 解析"""
    items = []
    try:
        status, text = _fetch_url(url)
        if status != 200 or not text:
            return items
        if not HAS_LXML:
            return items

        tree = lxml_html.fromstring(text)
        # 尝试多种选择器匹配新闻标题链接
        for selector in [
            "//div[contains(@class,'news')]//a[contains(@href,'.html')]",
            "//div[@id='newsListContent']//a",
            "//ul[contains(@class,'list')]//a",
            "//a[contains(@href,'/news/') and string-length(text())>10]",
        ]:
            links = tree.xpath(selector)
            if links:
                break

        seen = set()
        for a in links[:limit * 2]:
            title = "".join(a.xpath(".//text()")).strip()
            href = a.xpath("@href")[0] if a.xpath("@href") else ""
            if not title or not href:
                continue
            if len(title) < 10:  # 过滤太短的链接
                continue
            if title in seen:
                continue
            seen.add(title)

            if not href.startswith("http"):
                href = urljoin(url, href)

            items.append({
                "id": _hash_id(title),
                "title": _clean_html(title),
                "summary": "",
                "source": "东方财富",
                "url": href,
                "time": "",
                "date": datetime.now(TZ_BEIJING).strftime("%Y-%m-%d"),
            })
    except Exception as e:
        if DEBUG:
            print(f"  [东方财富解析异常] {e}")
    return items


def _scrape_sina(url, limit=30):
    """新浪财经 — HTML 解析（备用）"""
    items = []
    try:
        status, text = _fetch_url(url)
        if status != 200 or not text or not HAS_LXML:
            return items

        tree = lxml_html.fromstring(text)
        seen = set()
        for selector in [
            "//a[contains(@href,'finance.sina.com.cn') and string-length(text())>10]",
            "//div[contains(@class,'feed-card')]//a",
            "//h2//a",
        ]:
            links = tree.xpath(selector)
            if links:
                break

        for a in links[:limit * 2]:
            title = "".join(a.xpath(".//text()")).strip()
            href = a.xpath("@href")[0] if a.xpath("@href") else ""
            if not title or not href:
                continue
            if len(title) < 10:
                continue
            if title in seen:
                continue
            seen.add(title)
            items.append({
                "id": _hash_id(title),
                "title": _clean_html(title),
                "summary": "",
                "source": "新浪财经",
                "url": href if href.startswith("http") else urljoin(url, href),
                "time": "",
                "date": datetime.now(TZ_BEIJING).strftime("%Y-%m-%d"),
            })
    except Exception as e:
        if DEBUG:
            print(f"  [新浪财经解析异常] {e}")
    return items


# ═══════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════

def _parse_rss_time(entry):
    """解析 RSS 条目时间"""
    for field in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, field, None)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=TZ_BEIJING)
                return dt.strftime("%H:%M")
            except Exception:
                pass
    return ""


def _parse_rss_date(entry):
    """解析 RSS 条目日期"""
    for field in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, field, None)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=TZ_BEIJING)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
    return datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")


# 抓取器调度表
_SCRAPER_MAP = {
    "rss": _scrape_wallstreetcn,
    "api": _scrape_cls_api,
    "html": {
        "东方财富-要闻": _scrape_eastmoney,
        "新浪财经-产经": _scrape_sina,
    },
}


# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

def fetch_all_news(sources=None, limit_per_source=30, a_stock_filter=True):
    """
    抓取所有新闻源，返回去重后的新闻列表。

    参数:
        sources: 新闻源列表，默认用 config.RSS_SOURCES
        limit_per_source: 每源最多抓取条数
        a_stock_filter: 是否只保留 A 股/产业相关新闻

    返回:
        list[dict]: 统一格式的新闻列表，按时间排序
    """
    if sources is None:
        sources = RSS_SOURCES

    all_items = []
    seen_ids = set()

    for src in sources:
        name = src["name"]
        stype = src["type"]
        url = src["url"]

        if DEBUG:
            print(f"[抓取] {name} ...")

        # 找对应的抓取器
        if stype == "rss":
            items = _scrape_wallstreetcn(url, limit_per_source)
        elif stype == "api":
            items = _scrape_cls_api(url, limit_per_source)
        elif stype == "html" and name in _SCRAPER_MAP["html"]:
            items = _SCRAPER_MAP["html"][name](url, limit_per_source)
        else:
            if DEBUG:
                print(f"  [跳过] 未知类型 {stype}:{name}")
            continue

        if DEBUG:
            print(f"  → {len(items)} 条")

        # 去重 + 过滤
        for item in items:
            if item["id"] in seen_ids:
                continue
            if a_stock_filter and not _is_a_stock_related(item["title"]):
                continue
            seen_ids.add(item["id"])
            all_items.append(item)

    # 按来源+时间排序
    all_items.sort(key=lambda x: (x["source"], x.get("time", "")), reverse=False)

    return all_items


def ai_filter_news(news_list, model=None):
    """
    AI 批量预筛：将新闻标题批量发送给 DeepSeek，由 AI 判断哪些值得深入分析。

    过滤掉：
    - 大盘指数涨跌播报
    - 休市/节假日通知
    - 纯行情复盘、技术分析
    - 没有具体行业/公司指向的泛财经资讯
    - 重复/转载的旧闻

    保留：
    - 涉及具体行业、产品、公司的新闻
    - 政策/监管变化
    - 订单/产能/价格变动
    - 产业链上下游动态

    返回:
        list[dict]: 筛选后的新闻列表
    """
    if not news_list:
        return []

    if model is None:
        model = DEEPSEEK_MODEL

    # 检查 API key
    key = DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("  [!] AI 筛选跳过（未配置 DeepSeek API Key），使用关键词过滤结果")
        return news_list

    # 构建编号列表
    id_map = {}  # index -> news item
    lines = []
    for i, n in enumerate(news_list, 1):
        id_map[i] = n
        lines.append(f"[{i}] [{n['source']}] {n['title']}")

    prompt = (
        "你是财经新闻筛选助手。以下是今日A股财经新闻标题列表，请从中筛选出"
        "**有具体行业/板块指向、能影响多家公司、值得深入分析投资机会**的新闻。\n\n"
        "排除以下类型的新闻（标记为无关）：\n"
        "- 大盘指数涨跌播报（如\"沪指收跌\"、\"创指冲高回落\"）\n"
        "- 休市/节假日安排通知\n"
        "- 纯行情复盘、技术分析、资金流向总结\n"
        "- 没有具体行业/公司/产品指向的泛财经评论\n"
        "- 纯宏观经济数据播报（如PMI、CPI等，除非涉及具体行业影响）\n"
        "- **仅涉及单只个股的孤立消息**（如某公司财报、减持、人事变动、重组等，"
        "只影响该股自身，没有板块/产业链传导效应）\n"
        "- 重复/转载的旧闻\n\n"
        "保留以下类型（标记为相关）：\n"
        "- 涉及整个产业/板块的供需变化（涨价、断供、替代、技术突破、政策驱动）\n"
        "- 涉及多家公司的行业政策/监管变化（部委通知、产业规划、牌照发放）\n"
        "- 涉及产业链上下游的重大动态\n"
        "- 某行业龙头公司发生的、可能引发全行业估值重估的标志性事件\n\n"
        "请只返回筛选结果，格式为JSON数组：\n"
        "{\"keep\": [1, 3, 7, ...], \"reasons\": {\"1\": \"一句话原因\", ...}}\n"
        "只返回JSON，不要任何其他文字。\n\n"
        "新闻列表：\n" + "\n".join(lines)
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=30)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content.strip()

        # 解析 JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        keep_ids = data.get("keep", [])

        filtered = [id_map[i] for i in keep_ids if i in id_map]
        dropped = len(news_list) - len(filtered)
        print(f"  [AI筛选] {len(news_list)} 条 → {len(filtered)} 条 "
              f"(过滤 {dropped} 条) | token: {resp.usage.total_tokens}")

        if DEBUG and dropped > 0:
            dropped_ids = sorted(set(id_map.keys()) - set(keep_ids))
            for i in dropped_ids[:5]:
                print(f"    - [{i}] {id_map[i]['title'][:60]}")

        return filtered

    except Exception as e:
        print(f"  [!] AI 筛选异常，回退到关键词结果: {e}")
        return news_list


def fetch_today_news(limit_per_source=30, ai_filter=False):
    """快捷方法：抓取今日新闻（带缓存，8:00/12:30/17:00 定时刷新）"""
    from quota import get_cached_news, set_cached_news
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")

    cached, refresh_point = get_cached_news(today)
    if cached is not None:
        print(f"[缓存] 新闻缓存有效 (刷新点: {refresh_point})，共 {len(cached)} 条")
        return cached

    print(f"[抓取] 缓存过期 (刷新点: {refresh_point})，重新抓取...")
    items = fetch_all_news(limit_per_source=limit_per_source, a_stock_filter=True)
    print(f"[抓取] 关键词过滤后共 {len(items)} 条")

    if ai_filter and items:
        items = ai_filter_news(items)

    if items:
        set_cached_news(items)
        print(f"[缓存] 已写入缓存")

    print(f"[OK] 最终 {len(items)} 条\n")
    return items


# ═══════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  新闻抓取模块测试")
    print("=" * 60)
    news = fetch_today_news(limit_per_source=10)
    for i, n in enumerate(news, 1):
        print(f"[{i:2d}] {n['time']:<6s} [{n['source']}] {n['title'][:80]}")
