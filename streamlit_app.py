# -*- coding: utf-8 -*-
"""
A股利好新闻AI大模型分析（价格去问AI我不担责版）系统 — Hugging Face Spaces 版
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
    get_my_invite_link, record_ad_view, FREE_CACHE, FREE_FRESH_NEW, INVITE_BONUS, AD_BONUS,
    record_api_usage, get_daily_cost, get_admin_stats, get_cached_analysis,
)
from datetime import datetime, timezone, timedelta

TZ_BEIJING = timezone(timedelta(hours=8))

st.set_page_config(
    page_title="A股利好新闻AI大模型分析",
    page_icon="📰",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── 手机端适配 ──
st.markdown("""
<style>
@media (max-width: 768px) {
    .stApp { padding: 0.3rem !important; }
    h1 { font-size: 1.2rem !important; }
    h2 { font-size: 1rem !important; }
    h3 { font-size: 0.9rem !important; }
    .stButton button { width: 100% !important; padding: 8px !important; font-size: 0.9rem !important; }
    .stCheckbox label { font-size: 0.8rem !important; line-height: 1.3 !important; }
    .stExpander { font-size: 0.85rem !important; }
    .stWarning { font-size: 0.75rem !important; padding: 0.4rem !important; }
    .stMarkdown table { font-size: 0.7rem !important; }
}
/* 表格自适应 */
.stMarkdown table { display: block; overflow-x: auto; white-space: nowrap; max-width: 100%; }
/* 按钮全宽 */
.stLinkButton { margin-top: 0.3rem; }
</style>
""", unsafe_allow_html=True)

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
        st.toast(f"邀请码已使用！双方各得 +{INVITE_BONUS} 次全新分析")

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

    max_news = st.slider("每源抓取条数", 10, 50, 30)

    st.divider()
    st.subheader("🎯 免费额度")
    if quota.get("is_vip"):
        st.success("👑 VIP 无限")
    else:
        st.metric("看缓存剩余", quota["cache_rem"])
        st.metric("全新分析剩余", quota["fresh_rem"])
        if quota["is_new"]:
            st.success(f"🎁 首次登录得 {quota['fresh_base']} 次全新分析")
    st.caption(f"分享/看广告得全新分析次数 | 现有 +{quota['bonus']} 次")

    # ── 打赏 ──
    st.divider()
    st.markdown("**☕ 感谢打赏，随机赠送分析次数，助力作者维护与后续开发**")
    st.image("qr.png", use_container_width=True)
    st.divider()
    st.caption("📢 广告/商务合作：yllhwt@outlook.com")


    # 额度用完
    if quota["remaining"] <= 0:
        st.warning("次数已用完！")
        with st.expander("🔄 获取更多次数", expanded=True):
            st.markdown("### 方式1: 分享链接")
            st.code(invite_link, language=None)
            st.caption(f"朋友通过你的链接访问，双方各得 {INVITE_BONUS} 次全新分析")
            st.caption("分享功能开发中")

            st.markdown("### 方式2: 看激励广告")
            if st.button(f"📺 看广告得{AD_BONUS}次全新分析", use_container_width=True):
                record_ad_view(user_key)
                st.success(f"+{AD_BONUS}次全新分析！")
                st.rerun()

    st.caption(f"你的标识: `{user_key[:12]}`")

# ═══════════════════════════════════════════════════
# 主页面
# ═══════════════════════════════════════════════════

st.markdown("# 📰 A股利好新闻AI大模型分析系统<br><small>——（价格去问AI我不担责版）</small>", unsafe_allow_html=True)
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
        kept, dropped = fetch_today_news(limit_per_source=max_news, ai_filter=True)
        st.session_state.news_list = kept
        st.session_state.dropped_news = dropped
        st.session_state.results = []
    st.success(f"抓取完成，共 {len(kept)} 条新闻，过滤 {len(dropped)} 条")
    st.rerun()

news_list = st.session_state.news_list

if news_list:
    st.warning(
        "**本程序非炒股建议**，仅作为已有券商研报信息或AI分析信息分享，"
        "全程为AI自动化运行，无任何人工干预。预期的保守/中性/乐观价也与程序开发者无任何关系。"
        "投资有风险，入市须谨慎。",
        icon="⚠️"
    )
    st.subheader(f"📋 今日新闻 ({len(news_list)} 条)")

    from quota import get_news_analysis_count, get_top_keywords, save_keyword, log_news_analysis

    # 构建选择状态和关键词
    if "selected_news" not in st.session_state:
        st.session_state.selected_news = []
    if "news_keywords" not in st.session_state:
        st.session_state.news_keywords = {}

    for i, n in enumerate(news_list):
        count = get_news_analysis_count(n.get("title",""), n.get("source",""))
        url = n.get("url", "")

        # 热度渐变色：0=绿 → 5=灰 → 10+=红
        if count == 0: cname = "green"
        elif count <= 2: cname = "green"
        elif count <= 4: cname = "yellow"
        elif count <= 6: cname = "orange"
        else: cname = "red"

        prefix = f"【今日已被分析{count}次】" if count > 0 else ""
        label = f":{cname}[{prefix}{n.get('time','')} [{n['source']}]]" if count > 0 else f"{prefix}{n.get('time','')} [{n['source']}]"
        title_text = n['title'][:70]

        checked = st.checkbox(f"{label} {title_text}", value=False, key=f"news_{i}")

        if checked and n not in st.session_state.selected_news:
            st.session_state.selected_news.append(n)
        elif not checked and n in st.session_state.selected_news:
            st.session_state.selected_news.remove(n)

        # 勾选后：原文链接 + 关键词同行
        if checked:
            top_kws = get_top_keywords(n.get("title",""), n.get("source",""), 3)
            kw_options = [kw for kw, _ in top_kws]
            hint = f"历史: {'/'.join(kw_options[:2])}" if kw_options else ""

            kw_key = f"kw_val_{i}"
            btn_key = f"btn_news_{i}"
            if kw_key not in st.session_state:
                st.session_state[kw_key] = kw_options[0] if kw_options else ""

            # 原文链接 + 关键词输入同行
            c0, c1, c2 = st.columns([1.5, 2, 1])
            with c0:
                if url:
                    st.caption(f"[📰原文]({url})")
            with c1:
                kw = st.text_input(
                    f"核心关键词（1-6字，默认取历史最多，可改）  {hint}",
                    key=kw_key,
                    placeholder="如：钼代钨",
                )
                if kw and len(kw) > 6:
                    st.session_state[kw_key] = kw[:6]
                    st.rerun()
                if kw:
                    st.session_state.news_keywords[n.get("title","")] = kw
            with c2:
                if st.button("📰原文", key=btn_key, help="按新闻原文分析（不提取关键词）"):
                    st.session_state[kw_key] = ""
                    st.session_state.news_keywords[n.get("title","")] = ""
                    st.rerun()

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
        error_msg = ""

        for i, item in enumerate(selected_items):
            # 判断是否走缓存
            is_cached = get_cached_analysis(item.get("title",""), item.get("source","")) is not None

            if not admin_on and not use_one(user_key, fresh=not is_cached):
                error_msg = "看缓存次数用完，分享或看广告获取" if is_cached else "全新分析次数用完，看广告或分享获取"
                break

            if not admin_on and get_daily_cost() >= DAILY_COST_LIMIT:
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
        quota = get_quota(user_key)
        st.session_state.results = results
        st.session_state.result_msg = f"分析完成: 成功 {success} · 失败 {total - success}"
        st.session_state.error_msg = error_msg
        st.session_state.selected_news = []
        st.rerun()

# ── 展示结果 ──
results = st.session_state.results
if st.session_state.get("error_msg"):
    st.error(st.session_state.error_msg)
    st.session_state.error_msg = ""
if st.session_state.get("result_msg"):
    st.success(st.session_state.result_msg)
    st.session_state.result_msg = ""
if results:
    st.divider()
    st.subheader("📊 分析结果")
    for i, r in enumerate(results):
        with st.expander(f"📊 新闻 {i+1}: {r['news']['title'][:50]}...", expanded=(i == 0)):
            news = r["news"]
            st.caption(f"来源: {news['source']} | {news.get('time','')} | [原文]({news.get('url','#')})")
            if r["success"]:
                st.markdown(r["analysis"])
            else:
                st.error(f"分析失败: {r.get('error','')}")

    # ── 一键复制去 AI 做价格分析 ──
    st.divider()
    st.subheader("🔍 一键复制 → 去 AI 做价格分析")

    full_copy = ""
    for i, r in enumerate(results):
        if r["success"]:
            kw = st.session_state.news_keywords.get(r['news'].get('title',''), '')
            if kw:
                full_copy += f"关键词：{kw}\n"
            full_copy += f"新闻：{r['news']['title']}\n\n"
            full_copy += r["analysis"]
            full_copy += "\n\n"

    full_copy += """---
以上是公司列表，请联网搜索后做价格分析，按产业链分组输出表格：

| 代码 | 名称 | 现价 | 保守目标价 | 中性目标价 | 乐观目标价 | 价格来源 |

规则：
- 现价取最新收盘价，保守/中性/乐观基于行业估值+基本面+近期催化
- 价格来源标注券商名；搜不到可结合行业PE/PS估值或行业对比推算，标"AI推算（PE法/PS法/行业对比法等）"
- 禁止编造，确实无法推算的写"暂无"
- 客观、简洁，每家公司一行"""

    st.code(full_copy, language=None)
    c1, c2 = st.columns(2)
    c1.markdown('<a href="https://chat.deepseek.com/" target="_blank" style="text-decoration:none;color:white;background:#4A90D9;padding:8px 16px;border-radius:6px;display:inline-block">🚀 去 DeepSeek</a>', unsafe_allow_html=True)
    c2.markdown('<a href="https://www.doubao.com/" target="_blank" style="text-decoration:none;color:white;background:#07C160;padding:8px 16px;border-radius:6px;display:inline-block">🚀 去豆包</a>', unsafe_allow_html=True)
    st.caption("👆 全选→复制→打开 AI 平台→粘贴→获得价格分析。本平台不提供价格预测。")


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

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("♻ 重置所有人免费次数", use_container_width=True):
            from quota import reset_all_quota
            n = reset_all_quota()
            st.success(f"已重置 {n} 个用户")
    with col_b:
        if st.button("🗑 清空当日分析缓存", use_container_width=True):
            from quota import clear_today_cache
            n = clear_today_cache()
            st.success(f"已清空 {n} 条今日缓存，重新抓新闻即可重分析")

st.divider()
st.caption("免责声明：AI 生成内容不构成投资建议。投资有风险，入市须谨慎。")
