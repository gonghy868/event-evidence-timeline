"""运行配置。密钥只从环境变量读，不写进代码和日志。"""
import os
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """取回的数据、缓存、事件文件都放这里，不在代码仓库里。"""
    d = Path(os.environ.get("DATA_DIR") or (PRODUCT_DIR / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


IFIND_NEWS_URL = "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp"
FUYAO_BASE = "https://fuyao.aicubes.cn"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 现场查询的上限，页面上会显示
DAILY_QUERY_LIMIT = int(os.environ.get("DAILY_QUERY_LIMIT") or 10)
NOTICE_SIZE = int(os.environ.get("NOTICE_SIZE") or 10)
NEWS_SIZE = int(os.environ.get("NEWS_SIZE") or 15)
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or 60)
EXPIRE_DAYS = 30   # 我们自己定的假设值，页面上写明
STALE_HOURS = 24
ROUNDUP_MIN_OTHER_COMPANIES = 3
ROUNDUP_MAX_TARGET_MENTIONS = 2
