import os

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ===== 改成你的信息 =====
ADMIN_ID = 5593962796
ADMIN_USERNAME = "vyfjii"
# ======================

DEFAULT_DELETE_DELAY = 6
DEFAULT_GROUP_LIMIT = 1
DEFAULT_EXPIRE_DAYS = 30
DEFAULT_AD_INTERVAL_MINUTES = 60
DEFAULT_AD_MAX_COUNT = 3

if not TOKEN:
    raise RuntimeError("未检测到 BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("未检测到 DATABASE_URL")
