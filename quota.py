# -*- coding: utf-8 -*-
"""
免费额度管理模块
- IP 地址追踪 + 免费次数限制
- 邀请链接裂变（分享后双方各加额度）
- SQLite 存储，零外部依赖
"""

import os
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

try:
    from app_config import VIP_FPS
except ImportError:
    VIP_FPS = []

def _is_vip(ip: str) -> bool:
    return ip in VIP_FPS

TZ_BEIJING = timezone(timedelta(hours=8))
# HF Spaces 用 /data 持久化，本地用当前目录
_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "quota.db")

# 配置
FREE_CACHE = 3        # 每日免费看缓存次数
FREE_FRESH = 3        # 每个用户终生免费全新分析次数
INVITE_BONUS = 1      # 邀请后双方各得全新分析次数
AD_BONUS = 2          # 看广告得全新分析次数

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    """初始化数据库表"""
    conn = _db()
    # 检查旧表结构，如有冲突自动迁移
    cur = conn.execute("PRAGMA table_info(ip_quota)")
    cols = [r[1] for r in cur.fetchall()]
    # 增量迁移，不丢数据
    if cols:
        if "date" not in cols:
            conn.execute("DROP TABLE ip_quota")
            cols = []
        else:
            if "bonus" not in cols:
                conn.execute("ALTER TABLE ip_quota ADD COLUMN bonus INTEGER DEFAULT 0")
            if "cache_used" not in cols:
                conn.execute("ALTER TABLE ip_quota ADD COLUMN cache_used INTEGER DEFAULT 0")
            if "fresh_used" not in cols:
                conn.execute("ALTER TABLE ip_quota ADD COLUMN fresh_used INTEGER DEFAULT 0")
            if "fresh_lifetime" not in cols:
                conn.execute("ALTER TABLE ip_quota ADD COLUMN fresh_lifetime INTEGER DEFAULT 0")
    if not cols:
        conn.execute("""
            CREATE TABLE ip_quota (
                ip TEXT NOT NULL,
                date TEXT NOT NULL,
                bonus INTEGER DEFAULT 0,
                cache_used INTEGER DEFAULT 0,
                fresh_used INTEGER DEFAULT 0,
                fresh_lifetime INTEGER DEFAULT 0,
                PRIMARY KEY (ip, date)
            )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            code TEXT PRIMARY KEY,
            creator_ip TEXT NOT NULL,
            created_at TEXT NOT NULL,
            used_by TEXT,
            used_at TEXT,
            claimed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ad_views (
            ip TEXT NOT NULL,
            viewed_at TEXT NOT NULL,
            ad_type TEXT DEFAULT 'reward_video'
        )
    """)
    conn.commit()
    conn.close()


def _today():
    return datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")


def get_quota(ip: str) -> dict:
    """获取某 IP 今日剩余额度（每天重置基础免费）"""
    if _is_vip(ip):
        return {"remaining": 999, "cache_rem": 999, "fresh_rem": 999,
                "bonus": 0, "can_use": True, "is_new": False, "is_vip": True}

    today = _today()
    conn = _db()
    row = conn.execute("SELECT bonus, cache_used, fresh_used FROM ip_quota WHERE ip=? AND date=?", (ip, today)).fetchone()
    total_fresh = conn.execute("SELECT COALESCE(SUM(fresh_used), 0) FROM ip_quota WHERE ip=?", (ip,)).fetchone()[0]
    conn.close()

    bonus = row["bonus"] if row else 0
    cache_used = row["cache_used"] if row else 0
    fresh_used = row["fresh_used"] if row else 0
    fresh_base = max(0, FREE_FRESH - total_fresh)

    cache_rem = max(0, FREE_CACHE - cache_used)
    fresh_rem = max(0, fresh_base + bonus - fresh_used)
    return {"remaining": cache_rem + fresh_rem, "cache_rem": cache_rem,
            "fresh_rem": fresh_rem, "bonus": bonus,
            "can_use": cache_rem + fresh_rem > 0,
            "is_new": not row, "fresh_base": fresh_base}


def use_one(ip: str, fresh: bool = False) -> bool:
    """消耗一次额度（fresh=True=全新分析，False=看缓存），返回是否成功"""
    if _is_vip(ip):
        return True
    today = _today()
    conn = _db()
    # 先查历史总量（在插入今日行之前！）
    total_fresh = conn.execute("SELECT COALESCE(SUM(fresh_used), 0) FROM ip_quota WHERE ip=?", (ip,)).fetchone()[0]

    row = conn.execute("SELECT bonus, cache_used, fresh_used FROM ip_quota WHERE ip=? AND date=?", (ip, today)).fetchone()
    if not row:
        conn.execute("INSERT INTO ip_quota (ip, date, bonus, cache_used, fresh_used, fresh_lifetime) VALUES (?,?,0,0,0,?)", (ip, today, total_fresh))
        bonus, cache_used, fresh_used = 0, 0, 0
    else:
        bonus, cache_used, fresh_used = row["bonus"], row["cache_used"], row["fresh_used"]

    if fresh:
        fresh_base = max(0, FREE_FRESH - total_fresh)
        if fresh_used >= fresh_base + bonus:
            conn.close()
            return False
        conn.execute("UPDATE ip_quota SET fresh_used=fresh_used+1 WHERE ip=? AND date=?", (ip, today))
    else:
        if cache_used >= FREE_CACHE:
            conn.close()
            return False
        conn.execute("UPDATE ip_quota SET cache_used=cache_used+1 WHERE ip=? AND date=?", (ip, today))
    conn.commit()
    conn.close()
    return True


def add_bonus(ip: str, amount: int = INVITE_BONUS):
    """给某 IP 增加额外次数（邀请/广告奖励），加到今日"""
    today = _today()
    conn = _db()
    conn.execute(
        "INSERT INTO ip_quota (ip, date, bonus, used) VALUES (?,?,?,0) "
        "ON CONFLICT(ip, date) DO UPDATE SET bonus=bonus+?",
        (ip, today, amount, amount)
    )
    conn.commit()
    conn.close()


def create_invite_code(ip: str) -> str:
    """生成邀请码，返回 code"""
    code = secrets.token_urlsafe(8)[:10]
    conn = _db()
    conn.execute(
        "INSERT INTO invite_codes (code, creator_ip, created_at) VALUES (?,?,?)",
        (code, ip, datetime.now(TZ_BEIJING).isoformat())
    )
    conn.commit()
    conn.close()
    return code


def claim_invite_code(code: str, new_ip: str) -> bool:
    """使用邀请码，双方各得奖励。返回是否成功"""
    conn = _db()
    row = conn.execute(
        "SELECT creator_ip, claimed FROM invite_codes WHERE code=?",
        (code,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    if row["claimed"]:
        conn.close()
        return False  # 已被使用

    creator_ip = row["creator_ip"]

    # 不能自己邀请自己
    if creator_ip == new_ip:
        conn.close()
        return False

    # 标记已使用
    now = datetime.now(TZ_BEIJING).isoformat()
    conn.execute(
        "UPDATE invite_codes SET claimed=1, used_by=?, used_at=? WHERE code=?",
        (new_ip, now, code)
    )
    conn.commit()
    conn.close()

    # 双方加额度
    add_bonus(creator_ip, INVITE_BONUS)
    add_bonus(new_ip, INVITE_BONUS)

    return True


def get_my_invite_link(code: str, base_url: str = "") -> str:
    """生成邀请链接"""
    if not base_url:
        base_url = "http://localhost:8501"
    return f"{base_url}/?invite={code}"


def record_ad_view(ip: str) -> int:
    """记录一次广告观看，返回新增额度"""
    conn = _db()
    conn.execute(
        "INSERT INTO ad_views (ip, viewed_at, ad_type) VALUES (?,?,?)",
        (ip, datetime.now(TZ_BEIJING).isoformat(), "reward_video")
    )
    conn.commit()
    conn.close()

    add_bonus(ip, AD_BONUS)
    return AD_BONUS


# ═══════════════════════════════════════════════════
# API 消费追踪
# ═══════════════════════════════════════════════════

def _today():
    return datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")


def _ensure_cost_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ip TEXT NOT NULL,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_rmb REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def record_api_usage(ip: str, tokens_in: int, tokens_out: int):
    """记录一次 API 调用"""
    # DeepSeek V3: ¥1/M in, ¥2/M out
    cost = (tokens_in / 1_000_000) * 1 + (tokens_out / 1_000_000) * 2
    today = _today()
    conn = _db()
    _ensure_cost_table(conn)
    conn.execute(
        "INSERT INTO api_usage (date, ip, tokens_in, tokens_out, cost_rmb, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (today, ip, tokens_in, tokens_out, cost, datetime.now(TZ_BEIJING).isoformat())
    )
    conn.commit()
    conn.close()


def get_daily_cost(date_str: str = None) -> float:
    """获取某日 API 总消费"""
    if date_str is None:
        date_str = _today()
    conn = _db()
    _ensure_cost_table(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_rmb), 0) AS total FROM api_usage WHERE date=?",
        (date_str,)
    ).fetchone()
    conn.close()
    return round(row["total"], 4)


def get_admin_stats(date_str: str = None) -> dict:
    """获取后台统计数据"""
    if date_str is None:
        date_str = _today()
    conn = _db()
    _ensure_cost_table(conn)

    # 今日统计
    today_cost = get_daily_cost(date_str)

    # 今日分析次数
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM api_usage WHERE date=?", (date_str,)
    ).fetchone()
    today_analyses = row["cnt"]

    # 今日独立用户
    row = conn.execute(
        "SELECT COUNT(DISTINCT ip) AS cnt FROM ip_quota WHERE date=?", (date_str,)
    ).fetchone()
    today_users = row["cnt"]

    # 今日总 token
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout "
        "FROM api_usage WHERE date=?", (date_str,)
    ).fetchone()
    today_tokens = (row["tin"] + row["tout"])

    # 累计统计
    row = conn.execute("SELECT COUNT(DISTINCT ip) FROM ip_quota").fetchone()
    total_users = row[0]

    row = conn.execute("SELECT COALESCE(SUM(cost_rmb),0) FROM api_usage").fetchone()
    total_cost = round(row[0], 4)

    # 邀请统计
    row = conn.execute("SELECT COUNT(*) FROM invite_codes WHERE claimed=1").fetchone()
    total_invites = row[0]

    row = conn.execute("SELECT COUNT(*) FROM ad_views").fetchone()
    total_ads = row[0]

    conn.close()

    return {
        "date": date_str,
        "today_cost": today_cost,
        "today_analyses": today_analyses,
        "today_users": today_users,
        "today_tokens": today_tokens,
        "total_users": total_users,
        "total_cost": total_cost,
        "total_invites": total_invites,
        "total_ads": total_ads,
    }


# ═══════════════════════════════════════════════════
# 新闻抓取缓存（定时刷新，省 token）
# ═══════════════════════════════════════════════════

import json as _json

REFRESH_TIMES = ["08:00", "12:30", "17:00"]
NEWS_CACHE_DAYS = 2


def _ensure_news_cache_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_cache (
            date TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            news_json TEXT NOT NULL
        )
    """)
    conn.commit()


def get_cached_news(today: str):
    """检查新闻缓存。返回 (新闻列表, 刷新时间) 或 (None, last_refresh) 表示需刷新"""
    now = datetime.now(TZ_BEIJING)
    current_time = now.strftime("%H:%M")
    last_refresh = ""
    for t in REFRESH_TIMES:
        if current_time >= t:
            last_refresh = t

    conn = _db()
    _ensure_news_cache_table(conn)
    row = conn.execute(
        "SELECT date, fetched_at, news_json FROM news_cache ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        return None, last_refresh

    from datetime import timedelta as _td
    cutoff = (now - _td(days=NEWS_CACHE_DAYS)).strftime("%Y-%m-%d")
    if row["date"] < cutoff:
        return None, last_refresh

    cache_date = row["date"]
    cached_time = row["fetched_at"][:16]
    if cache_date == today and last_refresh:
        if cached_time < f"{today}T{last_refresh}":
            return None, last_refresh

    return _json.loads(row["news_json"]), last_refresh


def set_cached_news(news_list: list):
    """写入新闻缓存"""
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    now = datetime.now(TZ_BEIJING).isoformat()
    serializable = [{
        "id": n.get("id", ""), "title": n.get("title", ""),
        "summary": n.get("summary", ""), "source": n.get("source", ""),
        "url": n.get("url", ""), "time": n.get("time", ""), "date": n.get("date", ""),
    } for n in news_list]

    conn = _db()
    _ensure_news_cache_table(conn)
    conn.execute("DELETE FROM news_cache WHERE date=?", (today,))
    from datetime import timedelta as _td
    cutoff = (datetime.now(TZ_BEIJING) - _td(days=NEWS_CACHE_DAYS + 1)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM news_cache WHERE date < ?", (cutoff,))
    conn.execute(
        "INSERT INTO news_cache (date, fetched_at, news_json) VALUES (?,?,?)",
        (today, now, _json.dumps(serializable, ensure_ascii=False))
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# 分析结果缓存
# ═══════════════════════════════════════════════════

def _ensure_analysis_cache_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            cache_key TEXT PRIMARY KEY,
            news_title TEXT NOT NULL,
            news_source TEXT NOT NULL,
            analysis TEXT NOT NULL,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_hit TEXT NOT NULL
        )
    """)
    conn.commit()


def get_cached_analysis(title: str, source: str):
    """查缓存，命中返回结果，未命中返回 None"""
    import hashlib
    key = hashlib.md5(f"{source}|{title}".encode()).hexdigest()
    conn = _db()
    _ensure_analysis_cache_table(conn)
    row = conn.execute(
        "SELECT analysis, tokens_in, tokens_out, hit_count FROM analysis_cache WHERE cache_key=?",
        (key,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE analysis_cache SET hit_count=hit_count+1, last_hit=? WHERE cache_key=?",
            (datetime.now(TZ_BEIJING).isoformat(), key)
        )
        conn.commit()
        conn.close()
        return {
            "analysis": row["analysis"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "hit_count": row["hit_count"] + 1,
            "cached": True,
        }
    conn.close()
    return None


def set_cached_analysis(title: str, source: str, analysis: str,
                        tokens_in: int, tokens_out: int):
    """存入缓存"""
    import hashlib
    key = hashlib.md5(f"{source}|{title}".encode()).hexdigest()
    now = datetime.now(TZ_BEIJING).isoformat()
    conn = _db()
    _ensure_analysis_cache_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO analysis_cache "
        "(cache_key, news_title, news_source, analysis, tokens_in, tokens_out, created_at, last_hit) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (key, title, source, analysis, tokens_in, tokens_out, now, now)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# 并发锁：同关键词排队，后来者等先来者结果
# ═══════════════════════════════════════════════════

def _ensure_pending_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_queries (
            cache_key TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def acquire_query_lock(cache_key: str) -> bool:
    """尝试获取查询锁。返回 True 表示可以执行（你是第一个），False 表示别人在查"""
    conn = _db()
    _ensure_pending_table(conn)
    try:
        conn.execute("INSERT INTO pending_queries (cache_key, status, created_at) VALUES (?,?,?)",
                     (cache_key, 'pending', datetime.now(TZ_BEIJING).isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def release_query_lock(cache_key: str):
    """释放查询锁"""
    conn = _db()
    conn.execute("DELETE FROM pending_queries WHERE cache_key=?", (cache_key,))
    conn.commit()
    conn.close()


def wait_and_check_cache(cache_key: str, title: str, source: str, timeout: int = 30):
    """等待别人完成查询，然后从缓存读结果。最多等 timeout 秒"""
    import time as _time
    waited = 0
    while waited < timeout:
        _time.sleep(1)
        waited += 1
        cached = get_cached_analysis(title, source)
        if cached:
            return cached
    return None


# ═══════════════════════════════════════════════════
# 关键词当日缓存（同关键词当天复用，不重复调API）
# ═══════════════════════════════════════════════════

def _ensure_kw_cache_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_cache (
            keyword TEXT NOT NULL,
            date TEXT NOT NULL,
            analysis TEXT NOT NULL,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (keyword, date)
        )
    """)
    conn.commit()


def get_cached_keyword(keyword: str):
    """查当日关键词缓存"""
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    conn = _db()
    _ensure_kw_cache_table(conn)
    row = conn.execute(
        "SELECT analysis, tokens_in, tokens_out, hit_count FROM keyword_cache WHERE keyword=? AND date=?",
        (keyword, today)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE keyword_cache SET hit_count=hit_count+1 WHERE keyword=? AND date=?",
            (keyword, today)
        )
        conn.commit()
        conn.close()
        return {"analysis": row["analysis"], "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"], "hit_count": row["hit_count"] + 1}
    conn.close()
    return None


def set_cached_keyword(keyword: str, analysis: str, tokens_in: int, tokens_out: int):
    """存入当日关键词缓存"""
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    now = datetime.now(TZ_BEIJING).isoformat()
    conn = _db()
    _ensure_kw_cache_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO keyword_cache (keyword, date, analysis, tokens_in, tokens_out, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (keyword, today, analysis, tokens_in, tokens_out, now)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# 用户关键词（人工筛选核心概念，防新闻标题污染）
# ═══════════════════════════════════════════════════

def get_news_hash(title: str, source: str) -> str:
    return hashlib.md5(f"{source}|{title}".encode()).hexdigest()[:16]


def _ensure_keyword_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_keywords (
            news_hash TEXT NOT NULL,
            keyword TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            PRIMARY KEY (news_hash, keyword)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_hash TEXT NOT NULL,
            date TEXT NOT NULL,
            analyzed_at TEXT NOT NULL
        )
    """)
    conn.commit()


def get_top_keywords(title: str, source: str, limit: int = 3) -> list:
    """获取某新闻被输入最多的前N个关键词，返回 [(keyword, count), ...]"""
    nh = get_news_hash(title, source)
    conn = _db()
    _ensure_keyword_table(conn)
    rows = conn.execute(
        "SELECT keyword, count FROM news_keywords WHERE news_hash=? ORDER BY count DESC LIMIT ?",
        (nh, limit)
    ).fetchall()
    conn.close()
    return [(r["keyword"], r["count"]) for r in rows]


def save_keyword(title: str, source: str, keyword: str):
    """记录用户对某新闻输入的关键词"""
    nh = get_news_hash(title, source)
    conn = _db()
    _ensure_keyword_table(conn)
    conn.execute(
        "INSERT INTO news_keywords (news_hash, keyword, count) VALUES (?,?,1) "
        "ON CONFLICT(news_hash, keyword) DO UPDATE SET count=count+1",
        (nh, keyword)
    )
    conn.commit()
    conn.close()


def get_news_analysis_count(title: str, source: str, date_str: str = None) -> int:
    """某新闻当日被分析次数"""
    nh = get_news_hash(title, source)
    if date_str is None:
        date_str = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    conn = _db()
    _ensure_keyword_table(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM news_analysis_log WHERE news_hash=? AND date=?",
        (nh, date_str)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def log_news_analysis(title: str, source: str):
    """记录一次分析"""
    nh = get_news_hash(title, source)
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    now = datetime.now(TZ_BEIJING).isoformat()
    conn = _db()
    _ensure_keyword_table(conn)
    conn.execute(
        "INSERT INTO news_analysis_log (news_hash, date, analyzed_at) VALUES (?,?,?)",
        (nh, today, now)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# 管理员操作
# ═══════════════════════════════════════════════════

def reset_all_quota():
    """重置所有用户免费次数（used 归零，bonus 保留）"""
    conn = _db()
    conn.execute("UPDATE ip_quota SET cache_used = 0, fresh_used = 0")
    count = conn.total_changes
    conn.commit()
    conn.close()
    return count


def clear_today_cache():
    """清空当日分析缓存（关键词、分析结果、分析日志），方便重新分析"""
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    conn = _db()
    n = 0
    n += conn.execute("DELETE FROM keyword_cache WHERE date=?", (today,)).rowcount
    n += conn.execute("DELETE FROM analysis_cache WHERE last_hit LIKE ?", (f"{today}%",)).rowcount
    n += conn.execute("DELETE FROM news_analysis_log WHERE date=?", (today,)).rowcount
    conn.commit()
    conn.close()
    return n


# ═══════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════

init()
