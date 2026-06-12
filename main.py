# -*- coding: utf-8 -*-
"""
新闻驱动 A股利好分析系统 — 主入口

流程:
  1. 自动抓取多家财经媒体的 A 股/产业新闻
  2. 展示列表，用户主观筛选要分析的新闻
  3. 逐条调用 DeepSeek 做利好识别 + 公司映射 + 三段价格预期
  4. 输出到终端 + 保存 Markdown 文件
"""

import os
import sys
from datetime import datetime, timezone, timedelta

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_config import OUTPUT_DIR, MAX_ANALYSIS_PER_DAY, DEEPSEEK_MODEL, DqEBUG
from scraper import fetch_today_news
from analyzer import analyze_multiple, calc_tokens

# 北京时区
TZ_BEIJING = timezone(timedelta(hours=8))


def display_news_list(news_list):
    """打印新闻列表供用户筛选"""
    print("─" * 72)
    for i, n in enumerate(news_list, 1):
        time_str = n.get("time", "??:??")
        source = n["source"]
        title = n["title"][:72]
        print(f" [{i:2d}] {time_str:<6s} [{source}]  {title}")
    print("─" * 72)


def get_user_selection(max_num):
    """获取用户选择的新闻编号列表"""
    while True:
        raw = input(
            "\n[选择] 请输入要分析的新闻编号 "
            "(多选用空格分隔，如 1 3 7；输入 a=全选；q=退出): "
        ).strip()

        if raw.lower() == "q":
            return None
        if raw.lower() == "a":
            if max_num > MAX_ANALYSIS_PER_DAY:
                print(f"[!] 共 {max_num} 条，超过单日上限 {MAX_ANALYSIS_PER_DAY}。"
                      f"请手动选择或修改 config.MAX_ANALYSIS_PER_DAY")
                continue
            return list(range(1, max_num + 1))

        try:
            # 支持空格、逗号、中文逗号分隔
            raw = raw.replace(",", " ").replace("，", " ")
            nums = [int(x) for x in raw.split() if x.strip()]
            nums = sorted(set(nums))
            invalid = [n for n in nums if n < 1 or n > max_num]
            if invalid:
                print(f"无效编号: {invalid}，请输入 1-{max_num}")
                continue
            if len(nums) > MAX_ANALYSIS_PER_DAY:
                print(f"[!] 选择了 {len(nums)} 条，超过单日上限 {MAX_ANALYSIS_PER_DAY}")
                continue
            return nums
        except ValueError:
            print("格式错误，请用空格分隔数字（如 1 3 7）")


def progress_callback(index, total, result):
    """分析进度回调"""
    news = result["news"]
    status = "OK" if result["success"] else f"FAIL {result['error'][:30]}"
    print(f"  [{index+1}/{total}] {status}  {news['title'][:50]}")


def format_output(results, date_str):
    """将所有分析结果拼成一份完整 Markdown"""
    lines = [
        f"# A股利好新闻分析报告 — {date_str}",
        "",
        f"> 自动生成于 {datetime.now(TZ_BEIJING).strftime('%Y-%m-%d %H:%M')} "
        f"| 模型：{DEEPSEEK_MODEL}",
        "",
        "---",
        "",
    ]

    for i, r in enumerate(results, 1):
        news = r["news"]
        lines.append(f"## 新闻 {i}：{news['title']}")
        lines.append("")
        lines.append(f"- **来源**：{news['source']}　**时间**：{news.get('time', '?')}　"
                     f"[原文链接]({news.get('url', '#')})")
        lines.append("")

        if r["success"]:
            lines.append(r["analysis"])
        else:
            lines.append(f"> 分析失败：{r.get('error', '未知错误')}")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Token 统计
    stats = calc_tokens(results)
    lines.append("## 成本统计")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 输入 Token | {stats['total_in']:,} |")
    lines.append(f"| 输出 Token | {stats['total_out']:,} |")
    lines.append(f"| 合计 Token | {stats['total']:,} |")
    lines.append(f"| 预估费用 | ¥{stats['cost_rmb']:.4f} |")
    lines.append("")
    lines.append(f"*模型：{DEEPSEEK_MODEL} | "
                 f"DeepSeek V3 定价：入 ￥1/M · 出 ￥2/M*")

    return "\n".join(lines)


def save_report(markdown_text, date_str):
    """保存报告到 output 目录"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{date_str}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    return filepath


# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main():
    date_str = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")

    print()
    print("=" * 60)
    print(f"  [A股利好分析系统]")
    print(f"  日期: {date_str}　｜　模型: {DEEPSEEK_MODEL}")
    print("=" * 60)
    print()

    # ── Step 1: 抓取新闻 + AI 预筛 ──
    result = fetch_today_news(limit_per_source=30, ai_filter=True)
    if isinstance(result, tuple):
        news_list, dropped = result
        print(f"[过滤] 保留 {len(news_list)} 条，剔除 {len(dropped)} 条")
    else:
        news_list = result

    if not news_list:
        print("未抓取到任何 A股/产业相关新闻，请检查网络或新闻源配置。")
        return

    # ── Step 2: 用户主观筛选 ──
    display_news_list(news_list)
    print(f"\n共 {len(news_list)} 条新闻　|　单日分析上限: {MAX_ANALYSIS_PER_DAY}")

    selected_nums = get_user_selection(len(news_list))
    if selected_nums is None:
        print("已退出。")
        return

    selected = [news_list[i - 1] for i in selected_nums]
    print(f"\n[OK] 已选择 {len(selected)} 条新闻，开始分析...\n")

    # ── Step 3: LLM 分析 ──
    results = analyze_multiple(selected, callback=progress_callback)

    # ── Step 4: 输出 ──
    print()
    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count
    print(f"分析完成: 成功 {success_count} · 失败 {fail_count}")

    # 终端输出每个结果
    for i, r in enumerate(results, 1):
        print(f"\n{'─' * 60}")
        if r["success"]:
            print(r["analysis"])
        else:
            print(f"[新闻 {i}] 分析失败: {r.get('error', '未知错误')}")

    # 保存 Markdown
    report = format_output(results, date_str)
    filepath = save_report(report, date_str)

    # 统计
    stats = calc_tokens(results)
    print(f"\n{'=' * 60}")
    print(f"Token: {stats['total']:,}  |  费用: ¥{stats['cost_rmb']:.4f}")
    print(f"报告已保存: {filepath}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
