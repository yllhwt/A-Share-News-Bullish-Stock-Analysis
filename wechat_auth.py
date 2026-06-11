# -*- coding: utf-8 -*-
"""
微信扫码登录模块
使用微信开放平台网站应用 OAuth 2.0
需要: 微信开放平台 (open.weixin.qq.com) 注册网站应用，获取 AppID + AppSecret
"""

import hashlib
import secrets
import time
import requests
from urllib.parse import urlencode

# ═══════════════════════════════════════════════════
# 配置（部署前填入你自己的）
# ═══════════════════════════════════════════════════

WECHAT_APP_ID = ""        # 微信开放平台 → 网站应用 → AppID
WECHAT_APP_SECRET = ""    # 微信开放平台 → 网站应用 → AppSecret
CALLBACK_URL = ""         # 回调地址，如 https://your-app.streamlit.app/callback

# ═══════════════════════════════════════════════════
# OAuth 流程
# ═══════════════════════════════════════════════════

def get_auth_url(state: str = None) -> str:
    """
    生成微信扫码登录链接
    用户扫码后，微信会跳转到 CALLBACK_URL?code=xxx&state=xxx
    """
    if not WECHAT_APP_ID:
        raise RuntimeError("未配置 WECHAT_APP_ID，请在 wechat_auth.py 中填入")

    if state is None:
        state = secrets.token_urlsafe(16)

    params = {
        "appid": WECHAT_APP_ID,
        "redirect_uri": CALLBACK_URL,
        "response_type": "code",
        "scope": "snsapi_login",  # 网站应用用 snsapi_login
        "state": state,
    }
    return f"https://open.weixin.qq.com/connect/qrconnect?{urlencode(params)}#wechat_redirect"


def exchange_code(code: str) -> dict:
    """
    用 authorization code 换取 access_token 和 openid
    返回: {"openid": "xxx", "access_token": "xxx", "unionid": "xxx"}
    """
    if not WECHAT_APP_ID or not WECHAT_APP_SECRET:
        raise RuntimeError("未配置微信 AppID/AppSecret")

    url = "https://api.weixin.qq.com/sns/oauth2/access_token"
    params = {
        "appid": WECHAT_APP_ID,
        "secret": WECHAT_APP_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }
    resp = requests.get(url, params=params, timeout=10).json()

    if "errcode" in resp:
        raise RuntimeError(f"微信登录失败: {resp.get('errmsg', '未知错误')}")

    return {
        "openid": resp["openid"],
        "access_token": resp["access_token"],
        "unionid": resp.get("unionid", ""),
    }


def get_user_info(access_token: str, openid: str) -> dict:
    """获取微信用户信息（昵称、头像）"""
    url = "https://api.weixin.qq.com/sns/userinfo"
    params = {"access_token": access_token, "openid": openid}
    resp = requests.get(url, params=params, timeout=10).json()

    if "errcode" in resp:
        raise RuntimeError(f"获取用户信息失败: {resp.get('errmsg', '')}")

    return {
        "nickname": resp.get("nickname", ""),
        "headimgurl": resp.get("headimgurl", ""),
        "openid": resp.get("openid", ""),
    }


def wechat_user_key(openid: str) -> str:
    """把 openid 转成系统内部用户标识"""
    return hashlib.sha256(f"wx_{openid}".encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════
# 生成登录二维码 HTML
# ═══════════════════════════════════════════════════

def login_qr_html(auth_url: str) -> str:
    """生成显示微信登录二维码的 HTML"""
    return f"""
    <div style="text-align:center;padding:20px;">
        <h3>微信扫码登录</h3>
        <p style="color:#888;">扫码后每天免费额度提升至 3 次</p>
        <img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={auth_url}"
             style="border:1px solid #eee;border-radius:8px;" />
        <p style="margin-top:12px;color:#aaa;font-size:12px;">打开微信扫一扫</p>
    </div>
    """


# ═══════════════════════════════════════════════════
# 简化模式：无微信开放平台时的方案
# ═══════════════════════════════════════════════════

def is_configured() -> bool:
    """检查微信登录是否已配置"""
    return bool(WECHAT_APP_ID and WECHAT_APP_SECRET and CALLBACK_URL)
