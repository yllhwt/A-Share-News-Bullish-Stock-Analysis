# -*- coding: utf-8 -*-
"""
新闻驱动 A股利好分析系统 — 配置文件
"""

import os

# ==================== .env 文件加载 ====================
# 自动读取同目录下的 .env 文件（无需额外依赖）
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                os.environ.setdefault(_key, _val)

# ==================== API 密钥 ====================
# 豆包 API (火山引擎) — 优先级：环境变量 > .env 文件
# 获取 key: https://console.volcengine.com/ark → 开通豆包模型
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"  # DeepSeek V3，¥1/M in ¥2/M out

# 切豆包：
# DEEPSEEK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# DEEPSEEK_MODEL = "doubao-seed-2-0-pro-260215"

# ==================== 新闻源配置 ====================
# 注：部分 RSS 源可能因反爬机制失效，可自行增减
RSS_SOURCES = [
    {
        "name": "财联社",
        "url": "https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=8.4.6",
        "type": "api",  # 财联社改用 API
    },
    {
        "name": "华尔街见闻",
        "url": "https://wallstreetcn.com/rss",
        "type": "rss",
    },
    {
        "name": "东方财富-要闻",
        "url": "https://finance.eastmoney.com/a/czqyw.html",
        "type": "html",
    },
    {
        "name": "新浪财经-产经",
        "url": "https://finance.sina.com.cn/stock/",
        "type": "html",
    },
]

# ==================== 输出配置 ====================
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ==================== 分析配置 ====================
# 每天最多分析条数上限（超过会提醒）
MAX_ANALYSIS_PER_DAY = 10

# LLM 调用超时（秒）
LLM_TIMEOUT = 120

# 合规模式：True=小程序版（历史涨幅替代目标价），False=个人版（具体目标价）
COMPLIANCE_MODE = False

# 每日 API 消费上限（元），超过后拒绝所有分析请求
DAILY_COST_LIMIT = 1.0

# 管理员密码（访问 ?admin=密码 查看后台）
ADMIN_PASSWORD = "yllhwt10"

# 是否显示英文调试信息
DEBUG = False
