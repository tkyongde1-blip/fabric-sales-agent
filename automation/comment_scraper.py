"""评论/私信抓取模块 - 抖音/视频号平台数据采集

注意: 实际部署时需要接入对应平台 API。
当前实现提供接口抽象层和数据预处理逻辑。
"""
import logging
from datetime import datetime
from typing import Any, Optional

from crm.database import Database
from config import PLATFORM_CONFIG

logger = logging.getLogger(__name__)


class CommentScraper:
    """评论/私信抓取器"""

    def __init__(self, platform: str = "douyin") -> None:
        self.platform = platform
        self.db = Database()
        self.batch_size = PLATFORM_CONFIG["comment_batch_size"]

    def fetch_comments(self, video_id: str, max_count: int = 50) -> list[dict]:
        """
        获取视频评论区内容。

        实际接入时替换为对应平台 API 调用:
        - 抖音: https://open.douyin.com/platform/doc
        - 视频号: 微信视频号开放能力

        Args:
            video_id: 视频 ID
            max_count: 最大获取数量

        Returns:
            list[dict]: 评论列表，每条含 user, content, time 字段
        """
        logger.info("[%s] 获取评论: video=%s max=%d", self.platform, video_id, max_count)
        return []

    def fetch_private_messages(self, since_id: Optional[str] = None) -> list[dict]:
        """获取私信列表"""
        logger.info("[%s] 获取私信 since=%s", self.platform, since_id)
        return []

    def filter_business_leads(self, items: list[dict]) -> list[dict]:
        """从评论/私信中过滤出潜在客户"""
        trigger_keywords = [
            "多少钱", "价格", "怎么卖", "报价", "拿货",
            "订货", "批发", "样品", "牛奶丝", "罗纹",
            "四面弹", "针织", "面料", "怎么买", "有货吗",
        ]

        leads = []
        for item in items:
            content = item.get("content", "") or item.get("text", "") or ""
            matched = [kw for kw in trigger_keywords if kw in content]
            if matched:
                leads.append({
                    **item,
                    "matched_keywords": matched,
                    "captured_at": datetime.now().isoformat(),
                })
                logger.info("命中线索: %s | keywords=%s", content[:30], matched)

        return leads

    def lead_to_customer(self, lead: dict) -> Optional[int]:
        """将线索转为客户记录"""
        username = lead.get("user", {}).get("nickname", "") or lead.get("username", "")
        content = lead.get("content", "") or lead.get("text", "")

        if not username:
            logger.warning("线索缺少用户名，跳过")
            return None

        wechat_id = f"{self.platform}_{username}"
        existing = self.db.dict_fetchone(
            "SELECT id FROM customers WHERE wechat_id = ?", (wechat_id,)
        )
        if existing:
            return existing["id"]

        self.db.execute(
            """INSERT INTO customers (name, wechat_id, source, category, status, notes)
               VALUES (?, ?, ?, 'C', 'new', ?)""",
            (username, wechat_id, self.platform, f"来自{self.platform}: {content[:100]}"),
        )
        self.db.commit()

        customer_id = self.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        self.db.execute(
            """INSERT INTO interactions (customer_id, type, content, intent)
               VALUES (?, 'comment', ?, 'inquiry')""",
            (customer_id, content),
        )
        self.db.commit()

        logger.info("线索转客户: id=%d name=%s platform=%s", customer_id, username, self.platform)
        return customer_id

    def scan_and_process(self, video_ids: list[str]) -> dict[str, Any]:
        """扫描多个视频的评论并处理线索"""
        total_leads = 0
        total_customers = 0

        for vid in video_ids:
            comments = self.fetch_comments(vid, self.batch_size)
            leads = self.filter_business_leads(comments)
            total_leads += len(leads)

            for lead in leads:
                cid = self.lead_to_customer(lead)
                if cid:
                    total_customers += 1

        return {
            "status": "ok",
            "platform": self.platform,
            "videos_scanned": len(video_ids),
            "leads_found": total_leads,
            "customers_created": total_customers,
        }
