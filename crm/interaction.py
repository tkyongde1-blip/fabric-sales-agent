"""互动记录管理模块"""
import logging
from datetime import datetime
from typing import Optional

from crm.database import Database

logger = logging.getLogger(__name__)


class InteractionManager:
    """互动记录管理"""

    def __init__(self) -> None:
        self.db = Database()

    def add_interaction(
        self,
        customer_id: int,
        type: str,
        content: str,
        sentiment: str = "neutral",
        intent: Optional[str] = None,
        followup_action: Optional[str] = None,
    ) -> int:
        """添加互动记录"""
        self.db.execute(
            """INSERT INTO interactions
               (customer_id, type, content, sentiment, intent, followup_action)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (customer_id, type, content, sentiment, intent, followup_action),
        )
        self.db.commit()
        interaction_id = self.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 更新客户最后联系时间
        self.db.execute(
            "UPDATE customers SET last_contacted_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), customer_id),
        )
        self.db.commit()
        return interaction_id

    def get_interactions(self, customer_id: int, limit: int = 50) -> list[dict]:
        """获取客户互动历史"""
        return self.db.dict_fetchall(
            "SELECT * FROM interactions WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?",
            (customer_id, limit),
        )

    def get_sentiment_summary(self, customer_id: int) -> dict:
        """获取客户情感倾向摘要"""
        rows = self.db.fetchall(
            "SELECT sentiment, COUNT(*) as cnt FROM interactions WHERE customer_id = ? GROUP BY sentiment",
            (customer_id,),
        )
        summary = {"positive": 0, "negative": 0, "neutral": 0}
        for row in rows:
            summary[row["sentiment"]] = row["cnt"]
        return summary

    def get_recent_incoming(self, hours: int = 24) -> list[dict]:
        """获取最近 N 小时的客户消息"""
        import datetime as dt
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        return self.db.dict_fetchall(
            """SELECT i.*, c.name as customer_name, c.category, c.phone
               FROM interactions i
               JOIN customers c ON i.customer_id = c.id
               WHERE i.created_at > ? AND i.type = 'wechat_chat'
               ORDER BY i.created_at DESC""",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
