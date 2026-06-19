"""分类 Agent - 根据客户行为和对话内容进行 A/B/C 分类"""
import re
from typing import Any, Optional

from agents.base_agent import BaseAgent
from crm.customer import CustomerManager
from crm.database import Database


class ClassificationAgent(BaseAgent):
    """客户分类: 基于对话内容和行为评分"""

    def __init__(self) -> None:
        super().__init__("ClassificationAgent")
        self.customer_mgr = CustomerManager()

    def process(self, context: dict[str, Any]) -> dict[str, Any]:
        action = context.get("action", "classify")
        if action == "classify":
            return self._classify_customer(context.get("customer_id"))
        elif action == "reclassify_all":
            return self._reclassify_all()
        elif action == "score_chat":
            return self._score_from_chat(context.get("customer_id"))
        return {"status": "error", "message": f"未知动作: {action}"}

    def _classify_customer(self, customer_id: int) -> dict[str, Any]:
        """对单个客户执行分类"""
        customer = self.customer_mgr.get_customer(customer_id)
        if not customer:
            return {"status": "error", "message": f"客户 {customer_id} 不存在"}

        # 综合评分：意向分 + 决策分 + 匹配分 - 降分项
        score = 0
        reasons = []

        # 1. 查询最近的互动记录
        interactions = Database().dict_fetchall(
            "SELECT content, intent FROM interactions WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20",
            (customer_id,),
        )

        all_text = " ".join(
            (i.get("content") or "") for i in interactions
        )

        # 2. 关键词评分
        keyword_scores = Database().fetchall(
            "SELECT keyword, weight FROM keyword_rules WHERE is_active = 1"
        )

        for row in keyword_scores:
            kw = row["keyword"]
            weight = row["weight"]
            if kw in all_text:
                if weight > 0:
                    score += weight
                    reasons.append(f"+{weight}(关键词:{kw})")
                else:
                    score += weight  # 负分
                    reasons.append(f"{weight}(负面:{kw})")

        # 3. 行为评分
        total_msgs = len(interactions)
        score += min(total_msgs * 2, 10)  # 消息越多意向越高，上限10分
        if total_msgs >= 3:
            reasons.append(f"+{min(total_msgs * 2, 10)}(消息数:{total_msgs})")

        # 4. 客户属性评分
        if customer.get("phone"):
            score += 5
            reasons.append("+5(有电话)")
        if customer.get("company"):
            score += 5
            reasons.append("+5(有公司名)")

        # 5. 成交历史加分
        total_purchased = float(customer.get("total_purchased", 0))
        if total_purchased > 0:
            score += 15
            reasons.append("+15(有成交记录)")
        if total_purchased > 10000:
            score += 10
            reasons.append("+10(大客户)")

        # 确定分类
        if score >= 80:
            category = "A"
        elif score >= 50:
            category = "B"
        else:
            category = "C"

        # 黑名单检查
        blacklist_keywords = ["赊账", "月结", "先发货"]
        for kw in blacklist_keywords:
            if kw in all_text:
                category = "C"
                score = max(score - 20, 0)
                reasons.append("-20(黑名单触发)")
                break

        # 更新
        self.customer_mgr.update_classification(customer_id, category, score)

        self.log_action(
            "客户分类",
            f"id={customer_id} category={category} score={score} reasons={'; '.join(reasons)}",
        )

        return {
            "status": "ok",
            "customer_id": customer_id,
            "category": category,
            "score": score,
            "reasons": reasons,
        }

    def _reclassify_all(self) -> dict[str, Any]:
        """全量重新分类"""
        customers = Database().dict_fetchall(
            "SELECT id FROM customers WHERE status NOT IN ('blacklist', 'lost')"
        )
        results = {"A": 0, "B": 0, "C": 0}
        for c in customers:
            result = self._classify_customer(c["id"])
            results[result["category"]] += 1
        self.log_action("全量分类完成", str(results))
        return {"status": "ok", **results}

    def _score_from_chat(self, customer_id: int) -> dict[str, Any]:
        """基于聊天内容提取客户需求并评分"""
        messages = Database().dict_fetchall(
            """SELECT content, sender FROM wechat_messages
               WHERE customer_id = ? ORDER BY timestamp ASC""",
            (customer_id,),
        )
        if not messages:
            return {"status": "error", "message": "无聊天记录"}

        customer_text = " ".join(
            m["content"] for m in messages if m["sender"] == "customer"
        )

        # 提取需求
        needs = []
        product_map = {
            "牛奶丝": "牛奶丝",
            "罗纹": "罗纹",
            "四面弹": "四面弹",
            "汗布": "汗布",
            "摇粒绒": "摇粒绒",
            "卫衣": "卫衣布",
        }
        for kw, name in product_map.items():
            if kw in customer_text:
                needs.append(name)

        # 提取数量
        quantity = None
        q_match = re.search(r"(\d+)\s*[米|kg|公斤|码]", customer_text)
        if q_match:
            quantity = int(q_match.group(1))

        # 提取交期信息
        urgent = any(kw in customer_text for kw in ["急", "马上", "现在就要", "尽快"])

        self.log_action(
            "聊天评分",
            f"id={customer_id} 需求={needs} 数量={quantity} 加急={urgent}",
        )

        return {
            "status": "ok",
            "customer_id": customer_id,
            "extracted_needs": needs,
            "estimated_quantity": quantity,
            "is_urgent": urgent,
        }
