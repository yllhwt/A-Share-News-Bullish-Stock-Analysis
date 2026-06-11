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
from openai import OpenAI

from app_config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_TIMEOUT,
    DEBUG,
    COMPLIANCE_MODE,
)

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

SYSTEM_PROMPT = """你是一位资深的A股市场分析师，专门从财经新闻中识别投资机会。

你的任务：分析用户提供的财经新闻，输出结构化的分析报告。

## 分析要求

1. **利好识别**：识别新闻中的实质性利好因素，区分"真利好"（业绩/订单/政策落地）和"情绪利好"（题材炒作/传闻）
2. **可信度评估**：基于信息来源、信息具体程度、可验证性，给出1-10分的可信度评分
3. **公司映射**：找出受此新闻影响的A股上市公司，按受益程度分为三类：
   - [龙头]：行业龙头、市场份额第一梯队、最直接受益
   - [有订单]：已公告相关订单、产能、或主营业务高度相关
   - [概念跟风]：仅沾边概念、暂无实质业绩支撑（需标注"谨慎"）
4. **价格预期**：先给出当前股价（近5个交易日均价），再给出三段位市场共识预期价格（保守/中性/乐观），基于行业估值水平、历史波动区间、以及该利好对业绩的潜在增厚幅度估算。注意：预期价格是**未来3-6个月**的目标价区间

## 输出格式（严格遵守）

```
## 新闻摘要
[一句话概括新闻核心内容]

## 利好评估
- **利好等级**：[1-10] 分
- **可信度**：[1-10] 分
- **利好类型**：实质利好 / 情绪催化 / 政策驱动 / 行业拐点
- **影响周期**：短期(1-3天) / 中期(1-4周) / 长期(1月+)
- **评估理由**：[2-3句话说明]

## [龙头] 直接受益
| 代码 | 名称 | 受益逻辑 | 现价 | 市场共识预期价 |
|------|------|---------|------|---------------|
| 000000 | XX股份 | 一句话说明为什么受益 | 约X元 | 保守X元 / 中性X元 / 乐观X元 |

（如果没有明确的龙头公司，写"无"）

## [有订单] 实质性业务/订单
| 代码 | 名称 | 受益逻辑 | 现价 | 市场共识预期价 |
|------|------|---------|------|---------------|
（同上，1-3家公司）

## [概念跟风] 谨慎参与
| 代码 | 名称 | 受益逻辑 | 现价 | 市场共识预期价 |
|------|------|---------|------|---------------|
（同上，0-2家公司）

## 风险提示
- [该利好可能落空的风险点]
- [信息不确定性的来源]
- [市场已经price-in的可能]
```

## 重要规则

- 如果无法确定具体受益公司，诚实地说"暂无明确的A股直接受益标的"，不要编造
- 股票代码必须6位数字，格式如 000657.SZ 或 600519.SH
- **现价**尽量从联网搜索结果中获取最新股价；如果搜不到，标注"暂未获取"
- 预期价格必须是具体数字（元），不要写范围区间。保守/中性/乐观是三个独立数字
- 所有价格以人民币元为单位
- 如果新闻与A股完全无关或没有投资价值，直接说"该新闻对A股无明显利好"并结束分析
- **不要编造股票代码。如果无法确定，诚实告知。**"""


SYSTEM_PROMPT_COMPLIANCE = """你是一位财经新闻分析助手，专门为普通投资者解读产业新闻的市场含义。

你的任务：分析财经新闻，用**历史同类事件的市场反应**作为参考框架，帮助用户理解该新闻可能带来的行业影响。

## 分析要求

1. **利好识别**：识别新闻中的实质性利好因素，区分"真利好"（业绩/订单/政策落地）和"情绪利好"（题材炒作/传闻）
2. **可信度评估**：基于信息来源、信息具体程度、可验证性，给出1-10分的可信度评分
3. **公司映射**：找出受此新闻影响的A股上市公司，按受益程度分类：
   - [龙头]：行业龙头、市场份额第一梯队、最直接受益
   - [有实质业务]：已公告相关订单、产能、或主营业务高度相关
   - [概念关联]：仅沾边概念、暂无实质业绩支撑
4. **历史参考**：对每家公司，查找历史上同类事件发生时的市场反应，给出参考涨幅区间（不是预测，是历史回顾）。注意：只说"历史上同类事件发生后的**参考**区间"

## 输出格式（严格遵守）

```
## 新闻摘要
[一句话概括新闻核心内容]

## 利好评估
- **利好等级**：[1-10] 分
- **可信度**：[1-10] 分
- **利好类型**：实质利好 / 情绪催化 / 政策驱动 / 行业拐点
- **影响周期**：短期(1-3天) / 中期(1-4周) / 长期(1月+)
- **评估理由**：[2-3句话说明]

## [龙头] 直接受益
| 代码 | 名称 | 现价 | 受益逻辑 | 历史同类事件参考 |
|------|------|------|---------|-----------------|
| 000000 | XX股份 | 约X元 | 一句话说明为什么受益 | 参考：某年某同类事件该股/板块区间涨幅约X%-X% |

（如果没有明确的龙头公司，写"无"）

## [有实质业务] 业务关联
| 代码 | 名称 | 现价 | 受益逻辑 | 历史同类事件参考 |
|------|------|------|---------|-----------------|
（同上，1-3家公司）

## [概念关联] 需注意风险
| 代码 | 名称 | 现价 | 受益逻辑 | 历史同类事件参考 |
|------|------|------|---------|-----------------|
（同上，0-2家公司）

## 风险提示
- [该利好可能落空的风险点]
- [信息不确定性的来源]
- [历史表现不代表未来，仅供参考]
```

## 重要规则

- **禁止给出具体买卖价位**。只用历史同类事件的市场反应作为参考
- 参考涨幅表述为"参考：20XX年XX事件，该板块区间最大涨幅约XX%"
- 如果没有可类比的历史事件，写"暂无可类比的显著历史事件"
- 如果无法确定具体受益公司，诚实地说"暂无明确的A股直接受益标的"，不要编造
- 股票代码必须6位数字，格式如 000657.SZ 或 600519.SH
- 如果新闻与A股完全无关或没有投资价值，直接说"该新闻对A股无明显利好"并结束分析
- **全文末尾必须加上：以上内容为基于公开信息的产业新闻解读和历史数据回顾，不构成任何投资建议。投资有风险，入市须谨慎。**
- **不要编造股票代码。如果无法确定，诚实告知。**"""


# 根据合规模式选择 prompt
def _get_system_prompt():
    return SYSTEM_PROMPT_COMPLIANCE if COMPLIANCE_MODE else SYSTEM_PROMPT

USER_PROMPT_TEMPLATE = """请分析以下财经新闻：

---
来源：{source}
标题：{title}
摘要：{summary}
原文链接：{url}
---

{search_context}"""


# ═══════════════════════════════════════════════════
# 联网搜索（免费，无 API Key）
# ═══════════════════════════════════════════════════

def search_company_context(title: str, max_results: int = 6) -> str:
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
                    results = list(ddgs.text(sq, max_results=max_results // 2))
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
        # DuckDuckGo 失败时，用 requests 尝试 Bing
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
                    # 简单提取文本片段
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

def analyze_news(news_item: dict, model: str = None) -> dict:
    """
    分析单条新闻，返回结构化结果。

    参数:
        news_item: 新闻条目 (来自 scraper.fetch_all_news)
        model: 模型名称，默认用 config 中的 DEEPSEEK_MODEL

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

    # 联网搜索：先搜出相关公司信息，再喂给 LLM
    search_context = ""
    title = news_item.get("title", "")
    if title:
        print(f"  [搜索] 正在联网搜索相关公司: {title[:40]}...")
        search_context = search_company_context(title)
        if search_context:
            print(f"  [搜索] 已获取搜索结果")
        else:
            print(f"  [搜索] 无搜索结果，纯靠模型知识")

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
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,  # 低温度，保证输出稳定
            max_tokens=2048,
        )

        result["analysis"] = response.choices[0].message.content
        result["tokens_in"] = response.usage.prompt_tokens
        result["tokens_out"] = response.usage.completion_tokens
        result["success"] = True

    except Exception as e:
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
