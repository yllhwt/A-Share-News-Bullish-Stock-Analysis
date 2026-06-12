# -*- coding: utf-8 -*-
"""
A股利好新闻AI大模型分析（豆包版）系统 — Hugging Face Spaces 版
"""
import os, sys
import streamlit as st

# ─── 把当前目录加入 path，确保能 import 同目录模块 ───
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── HF Spaces 没有 .env 文件，key 通过 HF Secrets 注入 ───
# 如果 HF Secrets 里设了 DEEPSEEK_API_KEY，os.environ 自动能读到
# 没设的话 app_config 会 fallback 到空字符串，界面提示用户输入

from app_config import (
    OUTPUT_DIR, MAX_ANALYSIS_PER_DAY, DEEPSEEK_MODEL,
    DAILY_COST_LIMIT, ADMIN_PASSWORD,
)
from scraper import fetch_today_news, ai_filter_news
from analyzer import analyze_multiple, calc_tokens, analyze_news
from quota import (
    get_quota, use_one, create_invite_code, claim_invite_code,
    get_my_invite_link, record_ad_view, FREE_LIFETIME, INVITE_BONUS, AD_BONUS,
    record_api_usage, get_daily_cost, get_admin_stats,
)
from datetime import datetime, timezone, timedelta

TZ_BEIJING = timezone(timedelta(hours=8))

st.set_page_config(
    page_title="A股利好新闻AI大模型分析（豆包版）",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── 会话初始化 ───
@st.cache_resource
def _init_user_key():
    import uuid
    return str(uuid.uuid4())[:12]

if "_client_id" not in st.session_state:
    st.session_state._client_id = _init_user_key()
if "news_list" not in st.session_state:
    st.session_state.news_list = []
if "results" not in st.session_state:
    st.session_state.results = []

user_key = st.session_state._client_id

# ─── 处理邀请 ───
if "invite_handled" not in st.session_state:
    st.session_state.invite_handled = False
invite_code = st.query_params.get("invite", "")
if invite_code and not st.session_state.invite_handled:
    if claim_invite_code(invite_code, user_key):
        st.session_state.invite_handled = True
        st.toast(f"邀请码已使用！双方各得 +{INVITE_BONUS} 次")

# ─── 额度 ───
admin_on = st.query_params.get("admin", "") == ADMIN_PASSWORD
if admin_on:
    quota = {"total": 999, "used": 0, "bonus": 0, "free_base": 999,
             "remaining": 999, "can_use": True, "is_new": False, "is_vip": True}
else:
    quota = get_quota(user_key)

invite_link = "分享功能开发中"

# ═══════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════

with st.sidebar:
    st.title("📰 设置")

    use_ai_filter = st.checkbox("AI 预筛", value=True)
    max_news = st.slider("每源抓取条数", 10, 50, 30)

    st.divider()
    st.subheader("🎯 免费额度")
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("剩余次数", quota["remaining"])
    with col_b:
        st.metric("累计已用", quota["used"])
    if quota.get("is_vip"):
        st.success("👑 VIP 无限")
    elif quota["is_new"]:
        st.success(f"🎁 新用户免费 {FREE_LIFETIME} 次")
    st.caption(f"邀请奖励 +{quota['bonus']} 次 | 分享/看广告获取更多")

    # 额度用完
    if quota["remaining"] <= 0:
        st.warning("次数已用完！")
        with st.expander("🔄 获取更多次数", expanded=True):
            st.markdown("### 方式1: 分享链接")
            st.code(invite_link, language=None)
            st.caption(f"朋友通过你的链接访问，双方各得 {INVITE_BONUS} 次")
            st.caption("分享功能开发中")

            st.markdown("### 方式2: 看激励广告")
            if st.button(f"📺 观看广告 (+{AD_BONUS}次)", use_container_width=True):
                st.info("暂无广告，敬请期待")

    st.caption(f"你的标识: `{user_key[:12]}`")

# ═══════════════════════════════════════════════════
# 主页面
# ═══════════════════════════════════════════════════

st.title("📰 A股利好新闻AI大模型分析（豆包版）系统")
st.caption("抓取财经新闻 → 筛选 → AI 分析利好 + 映射上市公司 + 预期价格")

col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    btn_fetch = st.button("🔍 抓取新闻", use_container_width=True, type="primary")
with col2:
    btn_clear = st.button("🗑 清空", use_container_width=True)

if btn_clear:
    st.session_state.news_list = []
    st.session_state.results = []
    st.rerun()

# ── 抓取 ──
if btn_fetch:
    with st.spinner("正在抓取财经新闻..."):
        if use_ai_filter:
            kept, dropped = fetch_today_news(limit_per_source=max_news, ai_filter=True)
            st.session_state.news_list = kept
            st.session_state.dropped_news = dropped
        else:
            raw = fetch_today_news(limit_per_source=max_news)
            st.session_state.news_list = raw
            st.session_state.dropped_news = []
        st.session_state.results = []
    st.success(f"抓取完成，共 {len(st.session_state.news_list)} 条新闻，过滤 {len(st.session_state.dropped_news)} 条")
    st.rerun()

news_list = st.session_state.news_list

if news_list:
    st.subheader(f"📋 今日新闻 ({len(news_list)} 条)")

    from quota import get_news_analysis_count, get_top_keywords, save_keyword, log_news_analysis

    # 构建选择状态和关键词
    if "selected_news" not in st.session_state:
        st.session_state.selected_news = []
    if "news_keywords" not in st.session_state:
        st.session_state.news_keywords = {}

    all_checked = st.checkbox("全选", value=False)

    for i, n in enumerate(news_list):
        count = get_news_analysis_count(n.get("title",""), n.get("source",""))
        url = n.get("url", "")

        ct, ck, lk = st.columns([2, 10, 1])
        with ct:
            if count > 0:
                st.markdown(f"**:red[（{count}次）]**")
        with ck:
            label = f"{n.get('time','')} [{n['source']}] {n['title'][:60]}"
            checked = st.checkbox(label, value=all_checked, key=f"news_{i}")
        with lk:
            if url:
                st.link_button("🔗", url, help="查看原文")

        if checked and n not in st.session_state.selected_news:
            st.session_state.selected_news.append(n)
        elif not checked and n in st.session_state.selected_news:
            st.session_state.selected_news.remove(n)

        # 勾选后直接在新闻下方弹出关键词输入
        if checked:
            top_kws = get_top_keywords(n.get("title",""), n.get("source",""), 3)
            kw_options = [kw for kw, _ in top_kws]
            hint = f"历史: {'/'.join(kw_options[:2])}" if kw_options else ""

            # 关键词输入（超过6字自动截断，不顶替）
            kw_key = f"kw_val_{i}"
            if kw_key not in st.session_state:
                st.session_state[kw_key] = kw_options[0] if kw_options else ""

            kw = st.text_input(
                f"核心关键词(1-6字)  {hint}",
                key=kw_key,
                placeholder="如：钼代钨",
            )
            # 超过6字自动截断
            if kw and len(kw) > 6:
                st.session_state[kw_key] = kw[:6]
                st.rerun()
            if kw:
                st.session_state.news_keywords[n.get("title","")] = kw

    selected_items = st.session_state.selected_news
    selected_count = len(selected_items)

    # 被过滤的新闻（仅可读）
    if st.session_state.get("dropped_news"):
        with st.expander(f"🚫 已过滤新闻 ({len(st.session_state.dropped_news)} 条，单公司/利空/大盘播报等，不可分析)", expanded=False):
            for n in st.session_state.dropped_news:
                st.caption(f"[{n['source']}] {n['title'][:100]}")
            st.caption("[豆包搜索](https://www.doubao.com/) 想查可以自己去搜")

    st.caption(f"已选择 {selected_count} 条 | 上限 {MAX_ANALYSIS_PER_DAY} | 剩余 {quota['remaining']}")

    over_quota = selected_count > quota["remaining"]
    disabled = selected_count == 0 or selected_count > MAX_ANALYSIS_PER_DAY or over_quota

    if over_quota:
        st.error(f"额度不足！选了 {selected_count} 条，只剩 {quota['remaining']} 次。")

    btn_analyze = st.button("🚀 开始分析", type="primary", use_container_width=True, disabled=disabled)

    if btn_analyze:
        st.session_state.results = []

        progress_bar = st.progress(0)
        status_text = st.empty()

        results = []
        total = selected_count

        for i, item in enumerate(selected_items):
            if not use_one(user_key):
                status_text.text(f"用户额度用完！已分析 {i}/{total}")
                break

            if get_daily_cost() >= DAILY_COST_LIMIT:
                status_text.text(f"今日 API 消费已达上限 ¥{DAILY_COST_LIMIT}，明天再来！")
                break

            # 取用户输入的关键词
            user_kw = st.session_state.news_keywords.get(item.get("title",""), "").strip()
            if user_kw:
                save_keyword(item.get("title",""), item.get("source",""), user_kw)

            status_text.text(f"正在分析 [{i+1}/{total}]: {item['title'][:60]}...")
            result = analyze_news(item, search_keyword=user_kw if user_kw else None)
            results.append(result)

            if result["success"]:
                record_api_usage(user_key, result["tokens_in"], result["tokens_out"])
                log_news_analysis(item.get("title",""), item.get("source",""))

            progress_bar.progress((i + 1) / total)

        progress_bar.empty()
        status_text.empty()

        success = sum(1 for r in results if r["success"])
        st.success(f"分析完成: 成功 {success} · 失败 {total - success}")
        quota = get_quota(user_key)
        st.session_state.results = results
        st.session_state.selected_news = []
        st.rerun()

# ── 展示结果 ──
results = st.session_state.results

if results:
    st.divider()
    st.subheader("📊 分析结果")

    stats = calc_tokens(results)

    for i, r in enumerate(results):
        with st.expander(f"📊 新闻 {i+1}: {r['news']['title'][:50]}...", expanded=(i == 0)):
            news = r["news"]
            st.caption(f"来源: {news['source']} | {news.get('time','')} | [原文]({news.get('url','#')})")
            if r["success"]:
                st.markdown(r["analysis"])
            else:
                st.error(f"分析失败: {r.get('error','')}")

    # 导出
    st.divider()
    date_str = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    report_lines = [f"# A股利好新闻AI大模型分析（豆包版）报告 — {date_str}\n"]
    report_lines.append(f"> 模型: {DEEPSEEK_MODEL}\n\n---\n")
    for i, r in enumerate(results, 1):
        news = r["news"]
        report_lines.append(f"## {i}. {news['title']}\n")
        report_lines.append(f"*{news['source']} | {news.get('time','')}*\n")
        report_lines.append(r["analysis"] if r["success"] else f"*分析失败*")
        report_lines.append("\n---\n")

    st.download_button("💾 下载报告", "\n".join(report_lines), f"{date_str}.md", "text/markdown")

else:
    st.info("👆 点击「抓取新闻」开始")

# ── 管理后台 ──
admin_param = st.query_params.get("admin", "")
if admin_param == ADMIN_PASSWORD:
    st.divider()
    st.title("🔧 管理后台")
    stats = get_admin_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("今日用户", stats["today_users"])
    c2.metric("今日分析", stats["today_analyses"])
    c3.metric("今日消费", f"¥{stats['today_cost']:.4f}")
    c4.metric("累计用户", stats["total_users"])

    pct = min(100, stats["today_cost"] / DAILY_COST_LIMIT * 100)
    st.progress(int(pct), text=f"消费进度: ¥{stats['today_cost']:.4f} / ¥{DAILY_COST_LIMIT:.0f}")

    if st.button("♻ 重置所有人免费次数"):
        from quota import reset_all_quota
        n = reset_all_quota()
        st.success(f"已重置 {n} 个用户")

st.divider()
st.caption("免责声明：AI 生成内容不构成投资建议。投资有风险，入市须谨慎。")
