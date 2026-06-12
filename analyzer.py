# -*- coding: utf-8 -*-
"""
LLM 分析模块
使用 DeepSeek API（兼容 OpenAI SDK）对单条新闻做：
  1. 利好识别 + 程度/可信度打分
  2. 受益 A 股公司映射（龙头 / 有订单 / 概念跟风）
  3. 三段位市场共识预期价格
"""

import json
import os
import hashlib
from openai import OpenAI

from app_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_TIMEOUT,
    DEBUG,
)
from quota import get_cached_analysis, set_cached_analysis, get_cached_keyword, set_cached_keyword, acquire_query_lock, release_query_lock, wait_and_check_cache

# 懒加载客户端，避免 import 时因缺 key 崩溃
_client = None


def _get_client():
    """懒加载 OpenAI 客户端，未配置 key 时给出明确提示"""
    global _client
    if _client is not None:
        return _client
    key = DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError(
            "未配置 DeepSeek API Key！请通过以下任一方式设置:\n"
            "  1. 环境变量: set DEEPSEEK_API_KEY=sk-xxx\n"
            "  2. 直接编辑 config.py 中的 DEEPSEEK_API_KEY"
        )
    _client = OpenAI(
        api_key=key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=LLM_TIMEOUT,
    )
    return _client

# ═══════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════

SYSTEM_PROMPT = """你是A股产业链分析师。请联网搜索最新资料，按产业链环节尽可能全面列出所有相关A股公司（代码+名称+细分环节+受益逻辑），给出风险提示。禁止编造。"""



USER_PROMPT_TEMPLATE = """请分析以下财经新闻：

---
来源：{source}
标题：{title}
摘要：{summary}
原文链接：{url}
---

{search_context}"""

# 关键词模式下不传新闻内容，避免新闻原文污染分析方向
USER_PROMPT_KEYWORD = """请分析以下产业关键词相关的A股上市公司：

---
核心关键词：{keyword}
参考新闻：[{source}] {title}
---

{search_context}"""


# ═══════════════════════════════════════════════════
# 联网搜索（免费，无 API Key）
# ═══════════════════════════════════════════════════

def search_company_context(title: str, max_results: int = 12) -> str:
    """
    根据新闻标题搜索相关A股上市公司，返回搜索结果文本。
    用于给 LLM 分析提供实时公司信息，弥补 API 版无联网搜索的短板。
    """
    # 提取搜索关键词：去掉来源标记，取核心实体词
    import re as _re
    query = _re.sub(r"[【】\[\]「」""'']", " ", title)
    # 截取前40字作为搜索词，加上"A股 上市公司"
    query = query.strip()[:40]

    searches = [
        f"{query} A股 相关上市公司",
        f"{query} 受益股 龙头",
    ]

    all_snippets = []

    try:
        # 优先用新版包 ddgs，失败则尝试旧版 duckduckgo_search
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # 旧版包名
        with DDGS() as ddgs:
            for sq in searches:
                try:
                    results = list(ddgs.text(sq, max_results=max_results))
                    for r in results:
                        body = r.get("body", "")
                        title_r = r.get("title", "")
                        href = r.get("href", "")
                        snippet = f"- {title_r}: {body[:200]}"
                        if href:
                            snippet += f" (来源: {href})"
                        all_snippets.append(snippet)
                except Exception:
                    pass
    except ImportError:
        pass

    if not all_snippets:
        # DuckDuckGo 失败，用百度搜（A股信息更准）
        try:
            import requests as _req
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            for sq in searches[:1]:
                url = f"https://www.baidu.com/s?wd={_req.utils.quote(sq)}"
                resp = _req.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup.select(".result")[:max_results]:
                        title_tag = tag.select_one("h3")
                        desc_tag = tag.select_one(".c-abstract")
                        text = f"- {title_tag.get_text(strip=True) if title_tag else ''}: "
                        text += desc_tag.get_text(strip=True)[:200] if desc_tag else ""
                        all_snippets.append(text)
        except Exception:
            pass

    if not all_snippets:
        # 百度不行再试 Bing
        try:
            import requests as _req
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            }
            for sq in searches[:1]:
                url = f"https://www.bing.com/search?q={_req.utils.quote(sq)}"
                resp = _req.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup.select("li.b_algo")[:max_results]:
                        title_tag = tag.select_one("h2")
                        desc_tag = tag.select_one(".b_caption p")
                        text = f"- {title_tag.get_text(strip=True) if title_tag else ''}: "
                        text += desc_tag.get_text(strip=True)[:200] if desc_tag else ""
                        all_snippets.append(text)
        except Exception:
            pass

    if not all_snippets:
        return ""

    # 去重 + 截断
    seen = set()
    unique = []
    for s in all_snippets:
        key = s[:60]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    unique = unique[:max_results]

    return (
        "**联网搜索到的相关上市公司信息（请参考以下信息进行分析，"
        "优先采纳搜索结果中的公司）：**\n\n" +
        "\n".join(unique) +
        "\n\n注意：上述搜索结果是实时信息，请优先以此为依据进行公司映射。"
        "如果搜索结果中的公司与你的训练知识有冲突，以搜索结果为准。\n"
    )


# ═══════════════════════════════════════════════════
# 分析函数
# ═══════════════════════════════════════════════════

def analyze_news(news_item: dict, model: str = None, search_keyword: str = None) -> dict:
    """
    分析单条新闻，返回结构化结果。

    参数:
        news_item: 新闻条目
        model: 模型名称
        search_keyword: 用户输入的核心关键词（用于搜索，替代新闻标题）

    返回:
        {
            "news": {...},         # 原始新闻
            "analysis": "...",     # LLM 分析全文 (Markdown)
            "model": "...",        # 使用的模型
            "tokens_in": 0,        # 输入 token
            "tokens_out": 0,       # 输出 token
            "success": True/False,
            "error": "...",
        }
    """
    if model is None:
        model = DEEPSEEK_MODEL

    title = news_item.get("title", "")

    # ── 关键词当日缓存（优先）──
    if search_keyword:
        cached = get_cached_keyword(search_keyword)
        if cached:
            print(f"  [关键词缓存命中] {search_keyword} (第{cached['hit_count']}次，0 token)")
            return {
                "news": news_item, "analysis": cached["analysis"],
                "model": model, "tokens_in": cached["tokens_in"],
                "tokens_out": cached["tokens_out"], "success": True, "error": None,
            }

    # ── 标题缓存 ──
    cached = get_cached_analysis(title, news_item.get("source", ""))
    if cached:
        print(f"  [缓存命中] {title[:40]}... (第{cached['hit_count']}次，0 token)")
        return {
            "news": news_item, "analysis": cached["analysis"],
            "model": model, "tokens_in": cached["tokens_in"],
            "tokens_out": cached["tokens_out"], "success": True, "error": None,
        }

    # ── 并发锁：同关键词排队等结果 ──
    lock_key = hashlib.md5(f"{title}|{news_item.get('source','')}".encode()).hexdigest()[:16]
    if not acquire_query_lock(lock_key):
        print(f"  [排队] 别人正在分析，等待中...")
        waited = wait_and_check_cache(lock_key, title, news_item.get("source",""))
        if waited:
            print(f"  [排队] 已获取他人结果，免调API")
            return {"news": news_item, "analysis": waited["analysis"],
                    "model": model, "tokens_in": waited["tokens_in"],
                    "tokens_out": waited["tokens_out"], "success": True, "error": None}
        # 超时没等到，自己来
        print(f"  [排队] 超时，自己分析")

    # ── 联网搜索（优先用用户关键词）──
    search_query = search_keyword.strip() if search_keyword else title
    search_context = ""
    if search_query:
        print(f"  [搜索] 正在联网搜索相关公司: {search_query[:40]}...")
        search_context = search_company_context(search_query)
        if search_context:
            print(f"  [搜索] 已获取搜索结果")
        else:
            print(f"  [搜索] 无搜索结果，纯靠模型知识")

    if search_keyword:
        prompt = USER_PROMPT_KEYWORD.format(
            keyword=search_keyword,
            source=news_item.get("source", "未知"),
            title=title,
            search_context=search_context,
        )
    else:
        prompt = USER_PROMPT_TEMPLATE.format(
            source=news_item.get("source", "未知"),
            title=title,
            summary=news_item.get("summary", "（无摘要）"),
            url=news_item.get("url", ""),
            search_context=search_context,
        )

    result = {
        "news": news_item,
        "analysis": "",
        "model": model,
        "tokens_in": 0,
        "tokens_out": 0,
        "success": False,
        "error": None,
    }

    try:
        client = _get_client()
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=8192,
        )
        # API 内置联网搜索
        if "doubao" in model or "deepseek" in model:
            kwargs["extra_body"] = {"enable_search": True}
            # 豆包有搜索，不用额外注入搜索结果
            if search_keyword:
                kwargs["messages"][1]["content"] = USER_PROMPT_KEYWORD.format(
                    keyword=search_keyword,
                    source=news_item.get("source", "未知"),
                    title=title,
                    search_context="",
                )
            else:
                kwargs["messages"][1]["content"] = USER_PROMPT_TEMPLATE.format(
                    source=news_item.get("source", "未知"),
                    title=title,
                    summary=news_item.get("summary", "（无摘要）"),
                    url=news_item.get("url", ""),
                    search_context="",
                )

        response = client.chat.completions.create(**kwargs)

        result["analysis"] = response.choices[0].message.content
        result["tokens_in"] = response.usage.prompt_tokens
        result["tokens_out"] = response.usage.completion_tokens
        result["success"] = True
        set_cached_analysis(title, news_item.get("source", ""),
                           result["analysis"], result["tokens_in"], result["tokens_out"])
        if search_keyword:
            set_cached_keyword(search_keyword, result["analysis"],
                              result["tokens_in"], result["tokens_out"])
        release_query_lock(lock_key)

    except Exception as e:
        release_query_lock(lock_key)
        result["error"] = str(e)
        if DEBUG:
            print(f"  [LLM 调用失败] {e}")

    return result


def analyze_multiple(news_items: list, model: str = None, callback=None) -> list:
    """
    批量分析多条新闻，逐条调用 LLM。

    参数:
        news_items: 新闻列表
        model: 模型名称
        callback: 可选回调函数 callback(index, total, result)

    返回:
        list[dict]: 分析结果列表
    """
    results = []
    total = len(news_items)

    for i, item in enumerate(news_items):
        result = analyze_news(item, model=model)
        results.append(result)

        if callback:
            callback(i, total, result)

    return results


# ═══════════════════════════════════════════════════
# 统计工具
# ═══════════════════════════════════════════════════

def calc_tokens(results: list) -> dict:
    """计算总 token 消耗"""
    total_in = sum(r["tokens_in"] for r in results)
    total_out = sum(r["tokens_out"] for r in results)
    # DeepSeek V3: ￥1/M input, ￥2/M output
    cost = (total_in / 1_000_000) * 1 + (total_out / 1_000_000) * 2
    return {
        "total_in": total_in,
        "total_out": total_out,
        "total": total_in + total_out,
        "cost_rmb": round(cost, 4),
    }


# ═══════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    # 快速测试一条
    test_news = {
        "title": "六氟化钨海外产能受限，国内厂商迎替代机遇",
        "summary": "据外媒报道，全球六氟化钨主要产地因环保政策停产，供应缺口预计持续至2026年底，国内厂商有望承接海外订单转移。",
        "source": "财联社",
        "url": "https://example.com/test",
    }
    print("🔬 测试分析一条新闻...\n")
    result = analyze_news(test_news)
    if result["success"]:
        print(result["analysis"])
        print(f"\n--- Tokens: {result['tokens_in']} in / {result['tokens_out']} out")
    else:
        print(f"❌ 失败: {result['error']}")
