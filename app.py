# -*- coding: utf-8 -*-
"""
新闻驱动 A股利好分析系统 — Streamlit 网页版
用法: streamlit run app.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import streamlit.components.v1 as components

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

TZ_BEIJING = timezone(timedelta(hours=8))

st.set_page_config(
    page_title="A股利好新闻分析",
    page_icon="📰",
    layout="centered",          # 手机用居中布局
    initial_sidebar_state="collapsed",  # 默认折叠侧边栏
)

# 手机端自适应 CSS
st.markdown("""
<style>
/* 全局 */
@media (max-width: 768px) {
    .stApp { padding: 0.5rem !important; }
    h1 { font-size: 1.3rem !important; }
    h2 { font-size: 1.1rem !important; }
    h3 { font-size: 1rem !important; }
    .stMarkdown table { font-size: 0.8rem !important; display: block; overflow-x: auto; }
    .stMetric { font-size: 0.9rem !important; }
    .stButton button { width: 100% !important; padding: 0.7rem !important; font-size: 1rem !important; }
    .stCheckbox label { font-size: 0.85rem !important; }
    .stCaption { font-size: 0.75rem !important; }
}

/* 表格横向滚动 */
.stMarkdown table {
    display: block;
    overflow-x: auto;
    white-space: nowrap;
    max-width: 100%;
}

/* 按钮全宽 */
div[data-testid="column"] .stButton button {
    width: 100%;
}

/* 侧边栏更友好 */
[data-testid="stSidebar"] { min-width: 280px !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════
# 辅助：获取客户端 IP（Streamlit 限制，用 session ID 代替）
# ═══════════════════════════════════════════════════

def get_client_ip():
    """尝试从请求头获取 IP（部署后通过 Nginx/Cloudflare 传真实 IP）"""
    try:
        # Streamlit >= 1.28 支持 st.context.headers
        headers = st.context.headers
        fwd = headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        real = headers.get("X-Real-IP", "")
        if real:
            return real
    except Exception:
        pass
    return "127.0.0.1"

# ═══════════════════════════════════════════════════
# 会话初始化（惰性，避免模块级 st 调用）
# ═══════════════════════════════════════════════════

@st.cache_resource
def _init_user_key():
    """初始化用户标识（cache_resource 只在首次渲染时跑）"""
    import uuid
    return str(uuid.uuid4())[:12]

# 会话状态初始化
if "_client_id" not in st.session_state:
    st.session_state._client_id = _init_user_key()
if "news_list" not in st.session_state:
    st.session_state.news_list = []
if "results" not in st.session_state:
    st.session_state.results = []
if "invite_handled" not in st.session_state:
    st.session_state.invite_handled = False

# 浏览器指纹：免费防清 cookie 薅羊毛
fp = st.query_params.get("fp", "")
if fp:
    user_key = fp
    st.session_state._client_id = fp  # 覆盖随机 ID
else:
    user_key = st.session_state._client_id
    # 注入 JS：生成浏览器指纹，写入 localStorage，下次自动带上
    st.components.v1.html("""
    <script>
    (function(){
        var key = '_fp';
        var stored = localStorage.getItem(key);
        if (!stored) {
            var fp = navigator.hardwareConcurrency + '|' + navigator.deviceMemory + '|' +
                     screen.colorDepth + '|' + screen.width + 'x' + screen.height + '|' +
                     Intl.DateTimeFormat().resolvedOptions().timeZone + '|' +
                     navigator.language + '|' + (!!window.chrome) + '|' +
                     navigator.platform;
            // 简单 hash
            var hash = 0;
            for (var i = 0; i < fp.length; i++) {
                hash = ((hash << 5) - hash) + fp.charCodeAt(i);
                hash |= 0;
            }
            stored = 'b' + Math.abs(hash).toString(36);
            localStorage.setItem(key, stored);
        }
        if (!window.location.search.includes('fp=')) {
            var sep = window.location.search ? '&' : '?';
            window.location.search += sep + 'fp=' + stored;
        }
    })();
    </script>
    """, height=0)
quota = get_quota(user_key)

# 邀请链接生成
code = create_invite_code(user_key)
REMOTE_STREAMLIT_URL = "https://your-app-name.streamlit.app"
invite_link = get_my_invite_link(code, REMOTE_STREAMLIT_URL)

# ═══════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════

with st.sidebar:
    st.title("📰 设置")

    use_ai_filter = st.checkbox("AI 预筛", value=True)
    max_news = st.slider("每源抓取条数", 10, 50, 30)


    # 处理邀请链接
    invite_code = st.query_params.get("invite", "")
    if invite_code and not st.session_state.invite_handled:
        if claim_invite_code(invite_code, user_key):
            st.session_state.invite_handled = True
            st.toast(f"邀请码已使用！你获得了 +{INVITE_BONUS} 次额外额度", icon="🎁")
            quota = get_quota(user_key)

    st.divider()

    # --- 额度显示 ---
    st.subheader("🎯 免费额度")
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("剩余次数", quota["remaining"])
    with col_b:
        st.metric("累计已用", quota["used"])
    if quota["is_new"]:
        st.success(f"🎁 新用户首次免费 {FREE_LIFETIME} 次")
    st.caption(f"邀请奖励 +{quota['bonus']} 次 | 分享/看广告获取更多")

    # --- 获取更多次数 ---
    if quota["remaining"] <= 0:
        st.warning("次数已用完！")

        with st.expander("🔄 获取更多次数", expanded=True):
            st.markdown("### 方式1: 分享链接")
            st.code(invite_link, language=None)
            st.caption(f"别人通过你的链接访问并使用，双方各得 {INVITE_BONUS} 次")

            st.markdown("""
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;">
                <button onclick="navigator.clipboard.writeText('""" + invite_link + """');this.innerText='已复制！'"
                    style="padding:6px 14px;border-radius:6px;border:1px solid #ddd;cursor:pointer;background:#07c160;color:white;">
                    复制邀请链接
                </button>
            </div>
            """, unsafe_allow_html=True)

            st.markdown("### 方式2: 看激励广告")
            if st.button(f"📺 观看广告 (+{AD_BONUS}次)", use_container_width=True):
                bonus = record_ad_view(user_key)
                st.success(f"观看完成！获得 +{bonus} 次额度")
                st.rerun()

    # --- 我的邀请统计 ---
    with st.expander("📊 我的邀请"):
        st.caption(f"邀请链接已生成，分享给朋友即可")
        st.caption(f"对方使用后，你获得 +{INVITE_BONUS} 次额外额度")

# ═══════════════════════════════════════════════════
# 主页面
# ═══════════════════════════════════════════════════

st.title("📰 A股利好新闻分析系统")
st.caption("抓取财经新闻 → AI 筛选 → 勾选 → 分析利好 + 映射上市公司 + 预期价格")

# ──── 操作按钮 ────
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    btn_fetch = st.button("🔍 抓取新闻", use_container_width=True, type="primary")
with col2:
    btn_clear = st.button("🗑 清空", use_container_width=True)

if btn_clear:
    st.session_state.news_list = []
    st.session_state.results = []
    st.rerun()

# ──── Step 1: 抓取 ────
if btn_fetch:
    with st.spinner("正在抓取财经新闻..."):
        raw = fetch_today_news(limit_per_source=max_news)
        if use_ai_filter and raw:
            raw = ai_filter_news(raw)
        st.session_state.news_list = raw
        st.session_state.results = []
    st.success(f"抓取完成，共 {len(raw)} 条新闻")
    st.rerun()

news_list = st.session_state.news_list

if news_list:
    st.subheader(f"📋 今日新闻 ({len(news_list)} 条)")

    all_checked = st.checkbox("全选", value=False, key="all_select")
    selected_items = []

    for i, n in enumerate(news_list):
        checked = st.checkbox(
            f"{n.get('time','')} [{n['source']}] {n['title'][:80]}",
            value=all_checked,
            key=f"news_{i}",
        )
        if checked:
            selected_items.append(n)

    selected_count = len(selected_items)
    st.caption(f"已选择 {selected_count} 条 | 上限 {MAX_ANALYSIS_PER_DAY} | 剩余次数 {quota['remaining']}")

    # 额度检查
    over_quota = selected_count > quota["remaining"]
    disabled = selected_count == 0 or selected_count > MAX_ANALYSIS_PER_DAY or over_quota

    if over_quota:
        st.error(f"额度不足！你选了 {selected_count} 条，但只剩 {quota['remaining']} 次。请减少选择或获取更多额度。")

    btn_analyze = st.button("🚀 开始分析", type="primary", use_container_width=True, disabled=disabled)

    if btn_analyze:
        st.session_state.analyzing = True
        st.session_state.results = []

        progress_bar = st.progress(0)
        status_text = st.empty()

        results = []
        total = selected_count

        for i, item in enumerate(selected_items):
            # 扣额度
            if not use_one(user_key):
                status_text.text(f"用户额度用完！已分析 {i}/{total} 条")
                break

            # 全局消费上限
            if get_daily_cost() >= DAILY_COST_LIMIT:
                status_text.text(f"今日API消费已达上限¥{DAILY_COST_LIMIT}，明天再来吧！")
                break

            status_text.text(f"正在分析 [{i+1}/{total}]: {item['title'][:60]}...")
            result = analyze_news(item)
            results.append(result)

            # 记录消费
            if result["success"]:
                record_api_usage(user_key, result["tokens_in"], result["tokens_out"])

            progress_bar.progress((i + 1) / total)

        progress_bar.empty()
        status_text.empty()

        success = sum(1 for r in results if r["success"])
        fail = total - success
        st.success(f"分析完成: 成功 {success} · 失败 {fail}")

        # 刷新额度
        quota = get_quota(user_key)

        st.session_state.results = results
        st.session_state.analyzing = False
        st.rerun()

# ──── Step 4: 展示结果 ────
results = st.session_state.results

if results:
    st.divider()
    st.subheader("📊 分析结果")

    stats = calc_tokens(results)
    with st.expander(f"💰 本次成本: {stats['total']:,} tokens | ¥{stats['cost_rmb']:.4f}", expanded=False):
        st.write(f"输入 Token: {stats['total_in']:,}")
        st.write(f"输出 Token: {stats['total_out']:,}")

    if len(results) > 1:
        tabs = st.tabs([f"新闻 {i+1}" for i in range(len(results))])
        for tab, r in zip(tabs, results):
            with tab:
                news = r["news"]
                st.caption(f"来源: {news['source']} | {news.get('time','')} | [原文]({news.get('url','#')})")
                if r["success"]:
                    st.markdown(r["analysis"])
                else:
                    st.error(f"分析失败: {r.get('error','')}")
    else:
        r = results[0]
        news = r["news"]
        st.caption(f"来源: {news['source']} | {news.get('time','')} | [原文]({news.get('url','#')})")
        if r["success"]:
            st.markdown(r["analysis"])
        else:
            st.error(f"分析失败: {r.get('error','')}")

    # 导出
    st.divider()
    date_str = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    report_lines = [f"# A股利好新闻分析报告 — {date_str}\n"]
    report_lines.append(f"> 模型: {DEEPSEEK_MODEL} | 费用: ¥{stats['cost_rmb']:.4f}\n\n---\n")
    for i, r in enumerate(results, 1):
        news = r["news"]
        report_lines.append(f"## {i}. {news['title']}\n")
        report_lines.append(f"*{news['source']} | {news.get('time','')}*\n")
        report_lines.append(r["analysis"] if r["success"] else f"*分析失败: {r.get('error','')}*")
        report_lines.append("\n---\n")

    st.download_button(
        label="💾 下载报告 (.md)",
        data="\n".join(report_lines),
        file_name=f"{date_str}.md",
        mime="text/markdown",
    )

else:
    st.info("👆 点击「抓取新闻」开始，勾选要分析的新闻后点击「开始分析」")

# ═══════════════════════════════════════════════════
# 管理员后台 (访问 ?admin=密码)
# ═══════════════════════════════════════════════════

admin_param = st.query_params.get("admin", "")
if admin_param == ADMIN_PASSWORD:
    st.divider()
    st.title("🔧 管理后台")

    stats = get_admin_stats()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("今日用户", stats["today_users"])
    with col2:
        st.metric("今日分析", stats["today_analyses"])
    with col3:
        st.metric("今日消费", f"¥{stats['today_cost']:.4f}")
    with col4:
        st.metric("累计用户", stats["total_users"])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("今日 Token", f"{stats['today_tokens']:,}")
    with col2:
        st.metric("累计消费", f"¥{stats['total_cost']:.4f}")
    with col3:
        st.metric("邀请成功", stats["total_invites"])
    with col4:
        st.metric("广告观看", stats["total_ads"])

    # 消耗进度条
    pct = min(100, stats["today_cost"] / DAILY_COST_LIMIT * 100)
    st.progress(int(pct), text=f"今日消费进度: ¥{stats['today_cost']:.4f} / ¥{DAILY_COST_LIMIT:.0f} ({pct:.0f}%)")

    # 近7天趋势
    st.subheader("近7天消费")
    import pandas as pd
    days = []
    for d_offset in range(6, -1, -1):
        d = (datetime.now(TZ_BEIJING) - timedelta(days=d_offset)).strftime("%Y-%m-%d")
        cost = get_daily_cost(d)
        users = get_admin_stats(d)["today_users"]
        days.append({"日期": d[5:], "消费(元)": cost, "用户": users})
    df = pd.DataFrame(days)
    st.bar_chart(df.set_index("日期")[["消费(元)", "用户"]])

    st.caption(f"管理员入口: {st.query_params.get('admin','')} → 关闭标签页即退出后台")

# ═══════════════════════════════════════════════════
# 底部
# ═══════════════════════════════════════════════════

st.divider()
st.caption(
    "免责声明：本工具为基于公开信息的产业新闻解读工具，AI 生成内容仅供参考，"
    "不构成任何投资建议。投资有风险，入市须谨慎。"
)
