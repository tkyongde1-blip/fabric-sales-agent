"""获客 Agent - 处理评论和私信，提取原始线索"""
import re
from datetime import datetime
from typing import Any, Optional

from agents.base_agent import BaseAgent
from crm.database import Database


class AcquisitionAgent(BaseAgent):
    """获客: 公域流量→原始线索"""

    def __init__(self) -> None:
        super().__init__("AcquisitionAgent")

        # 可触发获客的关键词
        self.trigger_keywords = [
            "多少钱", "价格", "怎么卖", "怎么买", "报价",
            "拿货", "订货", "批发", "样品",
            "牛奶丝", "罗纹", "四面弹", "针织", "面料",
        ]

    def process(self, context: dict[str, Any]) -> dict[str, Any]:
        action = context.get("action", "scan_comments")
        if action == "scan_comments":
            return self._handle_scan_comments(context)
        elif action == "process_comment":
            return self._handle_comment(context.get("comment", {}))
        elif action == "process_dm":
            return self._handle_dm(context.get("dm", {}))
        elif action == "create_lead":
            return self._create_lead(context.get("lead_info", {}))
        return {"status": "error", "message": f"未知动作: {action}"}

    def _handle_scan_comments(self, context: dict) -> dict:
        """模拟扫描评论 - 实际开发接入抖音/视频号 API"""
        platform = context.get("platform", "douyin")
        self.log_action("扫描评论", f"platform={platform}")
        return {
            "status": "ok",
            "message": f"{platform} 评论扫描完成",
            "comments_found": 0,
        }

    def _handle_comment(self, comment: dict) -> dict:
        """处理单条评论"""
        content = comment.get("content", "")
        matched = [kw for kw in self.trigger_keywords if kw in content]

        if matched:
            self.log_action("评论命中关键词", f"content={content[:30]} keywords={matched}")
            # 匹配的关键词记录下来
            return {
                "status": "lead",
                "content": content,
                "matched_keywords": matched,
                "suggested_action": "reply_and_guide_dm",
            }
        return {"status": "ignored", "content": content}

    def _handle_dm(self, dm: dict) -> dict:
        """处理私信"""
        content = dm.get("content", "")
        user_info = dm.get("user", {})

        self.log_action("收到私信", f"user={user_info.get('username')}")

        return {
            "status": "lead_created",
            "user": user_info,
            "message": content,
            "suggested_action": "add_wechat",
        }

    def _create_lead(self, lead_info: dict) -> dict:
        """创建客户线索"""
        name = lead_info.get("name", f"线索_{datetime.now().strftime('%m%d%H%M')}")
        wechat_id = lead_info.get("wechat_id", f"tmp_{datetime.now().timestamp()}")

        # 检查是否已存在
        existing = Database().dict_fetchone(
            "SELECT id FROM customers WHERE wechat_id = ?", (wechat_id,)
        )
        if existing:
            return {"status": "exists", "customer_id": existing["id"]}

        db = Database()
        db.execute(
            """INSERT INTO customers (name, wechat_id, phone, city, source, category, status, notes)
               VALUES (?, ?, ?, ?, ?, 'C', 'new', ?)""",
            (
                name, wechat_id,
                lead_info.get("phone", ""),
                lead_info.get("city", ""),
                lead_info.get("source", "douyin"),
                lead_info.get("notes", ""),
            ),
        )
        db.commit()
        customer_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 同时创建首条互动记录
        db.execute(
            """INSERT INTO interactions (customer_id, type, content, intent)
               VALUES (?, 'dm', ?, 'inquiry')""",
            (customer_id, lead_info.get("first_message", "")),
        )
        db.commit()

        # 创建跟进任务
        self.create_followup_task(
            customer_id=customer_id,
            content=f"新线索: {name}，来源: {lead_info.get('source', 'unknown')}，请在30分钟内联系",
            due_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            priority="high",
        )

        self.log_action("创建客户线索", f"id={customer_id} name={name}")
        return {"status": "created", "customer_id": customer_id}
