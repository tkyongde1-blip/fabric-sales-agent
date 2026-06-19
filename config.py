"""全局配置 - 所有配置集中管理，不要硬编码"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent

# 数据库
DB_PATH = os.getenv("FABRIC_DB_PATH", str(BASE_DIR / "data" / "fabric_sales.db"))

# 数据文件路径
DATA_DIR = BASE_DIR / "data"
PROMPTS_DIR = BASE_DIR / "prompts"

# 客户分类阈值（综合评分）
CLASSIFICATION_THRESHOLDS = {
    "A": 80,   # 80分以上为A类
    "B": 50,   # 50-79分为B类
    "C": 0,    # 50分以下为C类
}

# 跟进周期（天）
FOLLOWUP_INTERVALS = {
    "A": 1,     # A类每天跟进
    "B": 3,     # B类每3天跟进
    "C": 7,     # C类每7天跟进
    "sleep": 15 # 沉睡客户每15天唤醒一次
}

# 报价加成比例
PRICING = {
    "default_markup": 1.3,      # 默认加价30%
    "bulk_discount": 0.95,      # 大批量95折
    "urgent_surcharge": 1.05,   # 急单加价5%
    "min_profit_margin": 0.15,  # 最低利润率15%
}

# 抖音/视频号配置
PLATFORM_CONFIG = {
    "comment_batch_size": 50,
    "scan_interval_minutes": 30,
}

# 微信聊天记录路径
WECHAT_CONFIG = {
    "export_path": os.getenv("WECHAT_EXPORT_PATH", ""),
    # 数据库中的微信号 -> 客户ID 映射
}

# 日志配置
LOGGING = {
    "level": "INFO",
    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    "file": str(BASE_DIR / "data" / "app.log"),
}
