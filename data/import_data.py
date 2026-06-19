"""数据导入脚本 - 从 CSV/Excel 导入产品目录、客户、聊天记录"""
import csv
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from crm.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent


def import_products(csv_path: Optional[Path] = None) -> int:
    """导入产品目录"""
    path = csv_path or DATA_DIR / "product_catalog.csv"
    if not path.exists():
        logger.error("产品目录文件不存在: %s", path)
        return 0

    db = Database()
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            db.execute(
                """INSERT OR IGNORE INTO products
                (name, spec, category, cost_price, standard_price, min_price, unit, stock_quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["name"], row["spec"], row["category"],
                    float(row["cost_price"]), float(row["standard_price"]),
                    float(row["min_price"]), row["unit"], int(row["stock_quantity"])
                ),
            )
            count += 1
    db.commit()
    logger.info("已导入 %d 个产品", count)
    return count


def import_customers(csv_path: Optional[Path] = None) -> int:
    """导入客户列表"""
    path = csv_path or DATA_DIR / "sample_customers.csv"
    if not path.exists():
        logger.error("客户文件不存在: %s", path)
        return 0

    db = Database()
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            db.execute(
                """INSERT OR IGNORE INTO customers
                (name, company, phone, wechat_id, city, source, category, score, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["name"], row.get("company", ""), row.get("phone", ""),
                    row["wechat_id"], row["city"], row["source"],
                    row["category"], int(row["score"]), row["status"], row.get("notes", "")
                ),
            )
            count += 1
    db.commit()
    logger.info("已导入 %d 个客户", count)
    return count


def import_chat_records(csv_path: Optional[Path] = None) -> int:
    """导入微信聊天记录"""
    path = csv_path or DATA_DIR / "sample_chat.csv"
    if not path.exists():
        logger.error("聊天记录文件不存在: %s", path)
        return 0

    db = Database()
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            db.execute(
                """INSERT INTO wechat_messages
                (customer_id, sender, content, timestamp)
                VALUES (?, ?, ?, ?)""",
                (
                    int(row["customer_id"]), row["sender"],
                    row["content"], row["timestamp"]
                ),
            )
            count += 1
    db.commit()
    logger.info("已导入 %d 条聊天记录", count)
    return count


def import_from_excel(filepath: str) -> dict:
    """从 Excel 文件导入（支持多 sheet）"""
    result = {"products": 0, "customers": 0, "chats": 0}
    try:
        xl = pd.ExcelFile(filepath)
        if "products" in xl.sheet_names:
            df = xl.parse("products")
            df.to_sql("products", Database().conn, if_exists="append", index=False)
            result["products"] = len(df)

        if "customers" in xl.sheet_names:
            df = xl.parse("customers")
            df.to_sql("customers", Database().conn, if_exists="append", index=False)
            result["customers"] = len(df)

        logger.info("Excel 导入完成: %s", result)
    except Exception as e:
        logger.error("Excel 导入失败: %s", e)
    return result


if __name__ == "__main__":
    # 初始化数据库
    db = Database()
    db.initialize()

    # 导入数据
    import_products()
    import_customers()
    import_chat_records()

    logger.info("数据导入完成！")
