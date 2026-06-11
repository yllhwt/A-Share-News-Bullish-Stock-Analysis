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

TZ_BEIJING = timezone(timedelta(hours=8))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quota.db")

# 配置
FREE_LIFETIME = 1        # 新用户终身免费次数（仅首次）
INVITE_BONUS = 3         # 邀请成功后双方各得次数
AD_BONUS = 1             # 看一次广告获得次数

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    """初始化数据库表"""
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ip_quota (
            ip TEXT PRIMARY KEY,
            free_claimed INTEGER DEFAULT 0,   -- 是否领过首次免费
            bonus INTEGER DEFAULT 0,           -- 通过邀请/广告获得的额外次数
            used INTEGER DEFAULT 0             -- 已使用次数
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


def get_quota(ip: str) -> dict:
    """获取某 IP 的剩余额度"""
    conn = _db()
    row = conn.execute("SELECT free_claimed, bonus, used FROM ip_quota WHERE ip=?", (ip,)).fetchone()
    conn.close()

    if row:
        free_base = FREE_LIFETIME if row["free_claimed"] == 0 else 0
        bonus = row["bonus"]
        used = row["used"]
    else:
        free_base = FREE_LIFETIME  # 新用户有首次免费
        bonus = 0
        used = 0

    total = free_base + bonus
    remaining = max(0, total - used)

    return {
        "total": total,
        "used": used,
        "bonus": bonus,
        "free_base": free_base,
        "remaining": remaining,
        "can_use": remaining > 0,
        "is_new": free_base > 0 and used == 0,  # 还没用过首次免费
    }


def use_one(ip: str) -> bool:
    """消耗一次额度，返回是否成功"""
    conn = _db()

    row = conn.execute("SELECT free_claimed, bonus, used FROM ip_quota WHERE ip=?", (ip,)).fetchone()

    if row:
        free_claimed = row["free_claimed"]
        bonus = row["bonus"]
        used = row["used"]
    else:
        free_claimed = 0
        bonus = 0
        used = 0
        conn.execute(
            "INSERT INTO ip_quota (ip, free_claimed, bonus, used) VALUES (?,0,0,0)",
            (ip,)
        )

    free_base = FREE_LIFETIME if free_claimed == 0 else 0
    total = free_base + bonus

    if used >= total:
        conn.close()
        return False

    conn.execute("UPDATE ip_quota SET used=used+1, free_claimed=1 WHERE ip=?", (ip,))
    conn.commit()
    conn.close()
    return True


def add_bonus(ip: str, amount: int = INVITE_BONUS):
    """给某 IP 增加额外次数（邀请/广告奖励）"""
    conn = _db()
    conn.execute(
        "INSERT INTO ip_quota (ip, free_claimed, bonus, used) VALUES (?,1,?,0) "
        "ON CONFLICT(ip) DO UPDATE SET bonus=bonus+?",
        (ip, amount, amount)
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
        "SELECT COUNT(DISTINCT ip) AS cnt FROM api_usage WHERE date=?", (date_str,)
    ).fetchone()
    today_users = row["cnt"]

    # 今日总 token
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout "
        "FROM api_usage WHERE date=?", (date_str,)
    ).fetchone()
    today_tokens = (row["tin"] + row["tout"])

    # 累计统计
    row = conn.execute("SELECT COUNT(DISTINCT ip) FROM api_usage").fetchone()
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
# 初始化
# ═══════════════════════════════════════════════════

init()
