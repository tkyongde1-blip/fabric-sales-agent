"""销售 Agent - 报价生成、话术推荐"""
import re
from datetime import datetime
from typing import Any, Optional

from agents.base_agent import BaseAgent
from config import PRICING


class SalesAgent(BaseAgent):
    """销售: 报价 + 话术推荐"""

    def __init__(self) -> None:
        super().__init__("SalesAgent")

    def process(self, context: dict[str, Any]) -> dict[str, Any]:
        action = context.get("action", "generate_quote")
        if action == "generate_quote":
            return self._generate_quote(context)
        elif action == "recommend_script":
            return self._recommend_script(context)
        elif action == "process_intent":
            return self._process_intent(context.get("intent_data", {}))
        return {"status": "error", "message": f"未知动作: {action}"}

    def _lookup_product(self, product_name: str, spec: Optional[str] = None) -> Optional[dict]:
        """查询产品信息"""
        sql = "SELECT * FROM products WHERE name LIKE ? AND is_active = 1"
        params = (f"%{product_name}%",)
        if spec:
            sql += " AND spec LIKE ?"
            params = (f"%{product_name}%", f"%{spec}%")
        row = self.db.dict_fetchone(sql, params)
        return row

    def _calculate_price(
        self,
        product: dict,
        quantity: int,
        is_repeat_customer: bool = False,
        is_urgent: bool = False,
    ) -> dict[str, Any]:
        """计算报价"""
        base_price = float(product["standard_price"])
        cost_price = float(product["cost_price"])
        min_price = float(product["min_price"])

        # 批量折扣
        if quantity >= 3000:
            discount = PRICING["bulk_discount"] - 0.03  # 92折
        elif quantity >= 1000:
            discount = PRICING["bulk_discount"]         # 95折
        elif quantity >= 500:
            discount = 0.98
        else:
            discount = 1.0

        # 老客户价
        customer_factor = 0.95 if is_repeat_customer else 1.0

        # 急单加价
        urgency_factor = PRICING["urgent_surcharge"] if is_urgent else 1.0

        # 最终报价
        final_price = base_price * discount * customer_factor * urgency_factor

        # 底线检查
        min_acceptable = max(min_price, cost_price * (1 + PRICING["min_profit_margin"]))
        final_price = max(final_price, min_acceptable)

        return {
            "base_price": base_price,
            "cost_price": cost_price,
            "min_acceptable": round(min_acceptable, 2),
            "discount_applied": round((1 - discount) * 100, 1),
            "customer_discount": round((1 - customer_factor) * 100, 1),
            "urgency_markup": round((urgency_factor - 1) * 100, 1),
            "final_price": round(final_price, 2),
            "total_amount": round(final_price * quantity, 2),
            "is_bottom_line": final_price <= min_acceptable * 1.02,
        }

    def _generate_quote(self, context: dict) -> dict[str, Any]:
        """生成报价"""
        product_name = context.get("product_name", "")
        spec = context.get("spec")
        quantity = context.get("quantity", 0)
        customer_id = context.get("customer_id")
        is_urgent = context.get("is_urgent", False)

        customer = self.db.dict_fetchone(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ) if customer_id else None

        is_repeat = bool(customer and customer["status"] in ("repeat", "trial_order"))

        product = self._lookup_product(product_name, spec)
        if not product:
            return {"status": "error", "message": f"找不到产品: {product_name}"}

        price_info = self._calculate_price(product, quantity, is_repeat, is_urgent)

        quote_data = {
            "customer_id": customer_id,
            "product_id": product["id"],
            "product_name": product["name"],
            "product_spec": product["spec"],
            "quantity": quantity,
            "unit": product["unit"],
            **price_info,
        }

        # 生成报价编号
        quote_no = f"Q-{datetime.now().strftime('%Y%m%d')}-{customer_id or 0:05d}"

        # 保存报价到数据库
        if customer_id:
            self.db.execute(
                """INSERT INTO quotations
                   (quote_no, customer_id, product_id, quantity, unit_price, total_amount, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'sent')""",
                (
                    quote_no, customer_id, product["id"],
                    quantity, price_info["final_price"], price_info["total_amount"],
                ),
            )
            self.db.commit()

        self.log_action("生成报价", f"customer={customer_id} product={product_name} price={price_info['final_price']}")

        return {
            "status": "ok",
            "quote_no": quote_no,
            **quote_data,
        }

    def _recommend_script(self, context: dict) -> dict[str, Any]:
        """推荐话术"""
        scenario = context.get("scenario", "greeting")
        customer_id = context.get("customer_id")

        customer = self.db.dict_fetchone(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ) if customer_id else None

        # 话术映射
        scripts = {
            "new_customer": "您好！我是XX针织厂的小X，看到您问面料..."
            if customer else "",
            "price_followup": "X总，昨天发您的报价看了吗？这款面料现在库存充足...",
            "objection_quality": "我们做的是A品足米足秤，不偷工减料...",
            "objection_price": "我给您报的已经是实价了，我们是工厂直营...",
            "urgency": "这款面料最近走得很快，库存不多了...",
            "closing": "好嘞！那帮您确认一下订单...",
            "after_sale": "X总，您的货已经发出啦！物流单号: ...",
        }

        script = scripts.get(scenario, "")
        self.log_action("推荐话术", f"customer={customer_id} scenario={scenario}")
        return {"status": "ok", "scenario": scenario, "script": script}

    def _process_intent(self, intent_data: dict) -> dict[str, Any]:
        """处理客户意图并生成响应"""
        intent = intent_data.get("intent", "unknown")
        content = intent_data.get("content", "")
        customer_id = intent_data.get("customer_id")

        # 根据意图生成响应
        responses = {
            "inquiry": "推荐对应产品并提供报价",
            "pricing": "发送标准报价并说明质量优势",
            "comparison": "说明产品差异化优势",
            "sample": "安排寄样并跟进",
            "urgent": "确认现货并加价报价",
            "objection": "安抚并重申价值",
            "ordering": "确认订单细节并安排发货",
        }

        response = responses.get(intent, "标准回复")
        self.log_action("意图处理", f"customer={customer_id} intent={intent}")

        # 记录互动
        if customer_id:
            self.db.execute(
                """INSERT INTO interactions (customer_id, type, content, intent, followup_action)
                   VALUES (?, 'auto_reply', ?, ?, ?)""",
                (customer_id, content, intent, response),
            )
            self.db.commit()

        return {
            "status": "ok",
            "intent": intent,
            "suggested_response": response,
            "suggested_action": self._suggest_action(intent),
        }

    def _suggest_action(self, intent: str) -> str:
        action_map = {
            "inquiry": "send_catalog_and_quote",
            "pricing": "send_quote",
            "comparison": "highlight_differentiation",
            "sample": "arrange_sample",
            "urgent": "prioritize_and_quote",
            "objection": "handle_objection",
            "ordering": "confirm_and_ship",
        }
        return action_map.get(intent, "standard_followup")
