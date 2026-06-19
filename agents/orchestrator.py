"""Agent 编排器 - 协调多个 Agent 协作"""
import logging
from datetime import datetime
from typing import Any, Optional

from agents.acquisition_agent import AcquisitionAgent
from agents.classification_agent import ClassificationAgent
from agents.sales_agent import SalesAgent
from agents.followup_agent import FollowupAgent
from crm.database import Database

logger = logging.getLogger(__name__)


class Orchestrator:
    """编排器: 协调多 Agent 完成销售自动化流程"""

    def __init__(self) -> None:
        self.db = Database()
        self.acquisition = AcquisitionAgent()
        self.classification = ClassificationAgent()
        self.sales = SalesAgent()
        self.followup = FollowupAgent()

    def run_new_lead_pipeline(self, lead_info: dict) -> dict[str, Any]:
        """
        新线索全流程:
        获客 → 分类 → 分配销售 → 创建跟进任务
        """
        logger.info("===== 新线索流程开始 =====")

        # Step 1: 获客 - 创建线索
        lead_result = self.acquisition.process({
            "action": "create_lead",
            "lead_info": lead_info,
        })
        if lead_result.get("status") != "created":
            return lead_result
        customer_id = lead_result["customer_id"]

        # Step 2: 分类 - 对新客户评分
        classify_result = self.classification.process({
            "action": "classify",
            "customer_id": customer_id,
        })

        # Step 3: 根据分类触发跟进
        category = classify_result.get("category", "C")
        priority = "high" if category == "A" else "normal"

        # Step 4: 如果是A类立即创建高优先级跟进
        if category == "A":
            self.followup.process({
                "action": "generate_content",
                "customer_id": customer_id,
                "scenario": "price_followup",
            })

        logger.info(
            "新线索处理完成: id=%d category=%s score=%d",
            customer_id, category, classify_result.get("score", 0),
        )
        logger.info("===== 新线索流程结束 =====\n")

        return {
            "status": "completed",
            "customer_id": customer_id,
            "category": category,
            "lead_result": lead_result,
            "classify_result": classify_result,
        }

    def process_incoming_message(self, customer_id: int, message: str) -> dict[str, Any]:
        """
        处理客户发来的消息:
        记录 → 提取意图 → 推荐报价/话术 → 更新分类
        """
        logger.info("===== 处理客户消息: %s =====", message[:50])

        # 1. 记录消息
        self.db.execute(
            """INSERT INTO interactions (customer_id, type, content, intent)
               VALUES (?, 'wechat_chat', ?, 'incoming')""",
            (customer_id, message),
        )
        self.db.commit()

        # 2. 提取意图
        intent_result = self._detect_intent(message)
        intent = intent_result["intent"]

        # 3. 如果是询价→生成报价
        response = {}
        if intent in ("inquiry", "pricing", "urgent"):
            # 尝试识别产品
            product_name = intent_result.get("product", "")
            quantity = intent_result.get("quantity", 0)
            response = self.sales.process({
                "action": "generate_quote",
                "product_name": product_name,
                "quantity": quantity or 100,
                "customer_id": customer_id,
                "is_urgent": intent == "urgent",
            })

        # 4. 推荐回复话术
        script = self.sales.process({
            "action": "recommend_script",
            "scenario": self._map_intent_to_scenario(intent),
            "customer_id": customer_id,
        })

        # 5. 重新分类
        self.classification.process({
            "action": "classify",
            "customer_id": customer_id,
        })

        # 6. 更新客户状态
        if intent == "ordering":
            self.db.execute(
                "UPDATE customers SET status = 'negotiating' WHERE id = ?",
                (customer_id,),
            )
            self.db.commit()

        logger.info("客户 %d 消息处理完成: intent=%s", customer_id, intent)

        return {
            "status": "ok",
            "customer_id": customer_id,
            "intent": intent,
            "quote": response if response.get("status") == "ok" else None,
            "suggested_script": script.get("script", ""),
            "suggested_action": self.sales._suggest_action(intent),
        }

    def _detect_intent(self, message: str) -> dict[str, Any]:
        """基于简单规则检测客户意图"""
        result = {"intent": "other", "product": "", "quantity": 0}

        import re

        # 产品识别
        product_keywords = {
            "牛奶丝": "牛奶丝", "罗纹": "罗纹", "四面弹": "四面弹",
            "汗布": "汗布", "摇粒绒": "摇粒绒", "卫衣布": "卫衣布",
        }
        for kw, name in product_keywords.items():
            if kw in message:
                result["product"] = name
                break

        # 数量提取
        q_match = re.search(r"(\d+)\s*[米kg公斤码]", message)
        if q_match:
            result["quantity"] = int(q_match.group(1))

        # 意图分类
        if any(kw in message for kw in ["下单", "要了", "安排", "发货"]):
            result["intent"] = "ordering"
        elif any(kw in message for kw in ["急", "马上", "现在就要", "尽快"]):
            result["intent"] = "urgent"
        elif any(kw in message for kw in ["多少钱", "价格", "报价", "怎么卖"]):
            result["intent"] = "pricing"
        elif any(kw in message for kw in ["样品", "打样", "看看质量"]):
            result["intent"] = "sample"
        elif any(kw in message for kw in ["什么面料", "有", "推荐"]):
            result["intent"] = "inquiry"
        elif any(kw in message for kw in ["贵", "便宜", "比价"]):
            result["intent"] = "comparison"
        elif any(kw in message for kw in ["考虑", "想想", "回头"]):
            result["intent"] = "objection"

        return result

    def _map_intent_to_scenario(self, intent: str) -> str:
        mapping = {
            "ordering": "closing",
            "urgent": "urgency",
            "pricing": "price_followup",
            "comparison": "objection_price",
            "objection": "objection_quality",
            "sample": "new_customer",
        }
        return mapping.get(intent, "new_customer")

    def run_daily_routine(self) -> dict[str, Any]:
        """每日例行任务"""
        logger.info("===== 日例行任务开始 =====")

        # 1. 生成跟进任务
        tasks_result = self.followup.process({"action": "generate_tasks"})

        # 2. 自动调整分类
        adjust_result = self.followup.process({"action": "auto_adjust"})

        # 3. 统计
        from crm.report import ReportManager
        report = ReportManager()
        daily = report.daily_report()

        logger.info("===== 日例行任务完成 =====")
        return {
            "status": "completed",
            "tasks_created": tasks_result.get("tasks_created", 0),
            "adjustments": adjust_result.get("adjustments", []),
            "daily_report": daily,
        }

    def process_wechat_chat_export(self, chat_records: list[dict]) -> list[dict]:
        """批量处理导出的微信聊天记录"""
        results = []
        for record in chat_records:
            customer_id = record.get("customer_id")
            content = record.get("content", "")
            sender = record.get("sender", "customer")

            if sender == "customer":
                result = self.process_incoming_message(customer_id, content)
                results.append(result)
        return results
