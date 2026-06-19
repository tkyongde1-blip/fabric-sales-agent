"""客户管理模块"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from crm.database import Database
from config import CLASSIFICATION_THRESHOLDS, FOLLOWUP_INTERVALS

logger = logging.getLogger(__name__)


class CustomerManager:
    """客户管理"""

    def __init__(self) -> None:
        self.db = Database()

    def create_customer(self, **kwargs) -> int:
        """创建新客户，返回 customer_id"""
        required = ["name", "wechat_id"]
        for field in required:
            if field not in kwargs:
                raise ValueError(f"缺少必填字段: {field}")

        self.db.execute(
            """INSERT INTO customers (name, company, phone, wechat_id, city, source, notes)
               VALUES (:name, :company, :phone, :wechat_id, :city, :source, :notes)""",
            {
                "name": kwargs.get("name"),
                "company": kwargs.get("company", ""),
                "phone": kwargs.get("phone", ""),
                "wechat_id": kwargs.get("wechat_id"),
                "city": kwargs.get("city", ""),
                "source": kwargs.get("source", "wechat"),
                "notes": kwargs.get("notes", ""),
            },
        )
        self.db.commit()
        customer_id = self.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        logger.info("创建新客户: %s (ID=%d)", kwargs["name"], customer_id)
        return customer_id

    def get_customer(self, customer_id: int) -> Optional[dict]:
        """获取客户详情"""
        return self.db.dict_fetchone(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        )

    def find_by_wechat(self, wechat_id: str) -> Optional[dict]:
        """通过微信号查找客户"""
        return self.db.dict_fetchone(
            "SELECT * FROM customers WHERE wechat_id = ?", (wechat_id,)
        )

    def find_by_phone(self, phone: str) -> Optional[dict]:
        """通过电话号码查找客户"""
        return self.db.dict_fetchone(
            "SELECT * FROM customers WHERE phone = ?", (phone,)
        )

    def update_customer(self, customer_id: int, **kwargs) -> bool:
        """更新客户信息"""
        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ("name", "company", "phone", "city", "source", "category",
                       "score", "status", "notes", "total_purchased"):
                fields.append(f"{key} = ?")
                values.append(value)

        if not fields:
            return False

        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(customer_id)

        sql = f"UPDATE customers SET {', '.join(fields)} WHERE id = ?"
        self.db.execute(sql, values)
        self.db.commit()
        return True

    def update_classification(self, customer_id: int, category: str, score: int) -> None:
        """更新客户分类和评分"""
        self.update_customer(customer_id, category=category, score=score)
        logger.info("客户 %d 分类更新: %s (评分=%d)", customer_id, category, score)

    def get_customers_by_category(self, category: str) -> list[dict]:
        """按分类获取客户列表"""
        return self.db.dict_fetchall(
            "SELECT * FROM customers WHERE category = ? AND status != 'blacklist' ORDER BY score DESC",
            (category,)
        )

    def get_customers_due_for_followup(self) -> list[dict]:
        """获取需要跟进的客户"""
        today = datetime.now()
        results = []

        for cat, interval in FOLLOWUP_INTERVALS.items():
            if cat == "sleep":
                continue
            cutoff = today - timedelta(days=interval)
            customers = self.db.dict_fetchall(
                """SELECT * FROM customers
                   WHERE category = ? AND status NOT IN ('lost', 'blacklist')
                   AND (last_contacted_at IS NULL OR last_contacted_at < ?)""",
                (cat, cutoff.strftime("%Y-%m-%d %H:%M:%S"))
            )
            results.extend(customers)

        # 沉睡客户
        sleep_cutoff = today - timedelta(days=FOLLOWUP_INTERVALS["sleep"])
        sleep_customers = self.db.dict_fetchall(
            """SELECT * FROM customers
               WHERE status = 'lost' AND last_contacted_at IS NOT NULL
               AND last_contacted_at < ?""",
            (sleep_cutoff.strftime("%Y-%m-%d %H:%M:%S"))
        )
        results.extend(sleep_customers)
        return results

    def search_customers(self, keyword: str) -> list[dict]:
        """搜索客户（按名称、公司、电话、微信号）"""
        pattern = f"%{keyword}%"
        return self.db.dict_fetchall(
            """SELECT * FROM customers
               WHERE name LIKE ? OR company LIKE ? OR phone LIKE ? OR wechat_id LIKE ?
               LIMIT 20""",
            (pattern, pattern, pattern, pattern)
        )
