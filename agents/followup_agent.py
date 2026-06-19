"""跟进 Agent - 自动化跟进任务管理和话术生成"""
from datetime import datetime, timedelta
from typing import Any, Optional

from agents.base_agent import BaseAgent
from config import FOLLOWUP_INTERVALS


class FollowupAgent(BaseAgent):
    """跟进: 任务管理 + 话术生成"""

    def __init__(self) -> None:
        super().__init__("FollowupAgent")

    def process(self, context: dict[str, Any]) -> dict[str, Any]:
        action = context.get("action", "generate_tasks")
        if action == "generate_tasks":
            return self._generate_daily_tasks()
        elif action == "execute_task":
            return self._execute_followup(context.get("task_id"))
        elif action == "generate_content":
            return self._generate_content(context)
        elif action == "auto_adjust":
            return self._auto_adjust_classification()
        return {"status": "error", "message": f"未知动作: {action}"}

    def _generate_daily_tasks(self) -> dict[str, Any]:
        """生成每日跟进任务"""
        today = datetime.now().strftime("%Y-%m-%d")
        tasks_created = 0

        for cat, interval in FOLLOWUP_INTERVALS.items():
            if cat == "sleep":
                continue
            cutoff = (datetime.now() - timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S")

            customers = self.db.dict_fetchall(
                """SELECT c.*, MAX(i.created_at) as last_interaction
                   FROM customers c
                   LEFT JOIN interactions i ON c.id = i.customer_id
                   WHERE c.category = ? AND c.status NOT IN ('lost', 'blacklist')
                   GROUP BY c.id
                   HAVING last_interaction IS NULL OR last_interaction < ?""",
                (cat, cutoff),
            )

            for customer in customers:
                content = self._generate_followup_content(customer)
                due = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                self.db.execute(
                    """INSERT INTO followup_tasks
                       (customer_id, task_type, priority, content, due_at)
                       VALUES (?, 'scheduled', ?, ?, ?)""",
                    (customer["id"], self._get_priority(cat), content, due),
                )
                tasks_created += 1

        self.db.commit()
        self.log_action("生成跟进任务", f"共 {tasks_created} 个任务")
        return {"status": "ok", "tasks_created": tasks_created, "date": today}

    def _get_priority(self, category: str) -> str:
        return {"A": "high", "B": "normal", "C": "low"}.get(category, "normal")

    def _generate_followup_content(self, customer: dict) -> str:
        """根据客户状态生成跟进内容"""
        name = customer.get("name", "客户")
        product_intro = self._guess_product_interest(customer["id"])

        followup_round = self.db.fetchone(
            """SELECT COUNT(*) as c FROM followup_tasks
               WHERE customer_id = ? AND status = 'completed'""",
            (customer["id"],),
        )["c"]

        scripts_pool = [
            f"{name}，上次和您聊的{product_intro}，现在库存充足，价格也有优势，您看要不要安排？",
            f"{name}，最近生意怎么样？我们新到了一批面料，花色很不错，发您看看？",
            f"{name}，天气变化注意身体。最近面料行情有波动，有需要随时问我～",
            f"{name}，好久没联系，最近还在做服装吗？新出了几个花色，发您看看？",
        ]

        return scripts_pool[followup_round % len(scripts_pool)]

    def _guess_product_interest(self, customer_id: int) -> str:
        """猜测客户可能感兴趣的产品"""
        interactions = self.db.dict_fetchall(
            "SELECT content FROM interactions WHERE customer_id = ? ORDER BY created_at DESC LIMIT 5",
            (customer_id,),
        )
        all_text = " ".join(i.get("content", "") for i in interactions)

        products = ["牛奶丝", "罗纹", "四面弹", "汗布", "摇粒绒"]
        for p in products:
            if p in all_text:
                return p
        return "针织面料"

    def _execute_followup(self, task_id: int) -> dict[str, Any]:
        """执行跟进（标记完成）"""
        task = self.db.dict_fetchone(
            "SELECT * FROM followup_tasks WHERE id = ?", (task_id,)
        )
        if not task:
            return {"status": "error", "message": f"任务 {task_id} 不存在"}

        self.db.execute(
            "UPDATE followup_tasks SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
        )

        # 更新客户最后联系时间
        self.db.execute(
            "UPDATE customers SET last_contacted_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task["customer_id"]),
        )

        # 记录互动
        self.db.execute(
            """INSERT INTO interactions (customer_id, type, content, intent)
               VALUES (?, 'followup', ?, 'followup')""",
            (task["customer_id"], task["content"]),
        )
        self.db.commit()

        self.log_action("执行跟进", f"task={task_id} customer={task['customer_id']}")
        return {"status": "completed", "task_id": task_id}

    def _generate_content(self, context: dict) -> dict[str, Any]:
        """根据场景生成跟进内容"""
        customer_id = context.get("customer_id")
        scenario = context.get("scenario", "regular")

        customer = self.db.dict_fetchone(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ) if customer_id else None
        if not customer:
            return {"status": "error", "message": "客户不存在"}

        name = customer["name"]
        content_pool = {
            "regular": f"{name}，最近怎么样？有面料需求随时找我哈～",
            "price_followup": f"{name}，上次的报价您考虑得怎么样了？价格还能再谈～",
            "new_arrival": f"{name}，我们新到了一批高品质面料，您看看？",
            "holiday": f"{name}，节日快乐！最近有需要随时联系～",
            "winback": f"{name}，好久不见，我们最近有优惠活动，老客户有额外折扣哦！",
        }
        content = content_pool.get(scenario, content_pool["regular"])

        return {"status": "ok", "content": content, "customer": name}

    def _auto_adjust_classification(self) -> dict[str, Any]:
        """自动调整客户分类"""
        now = datetime.now()
        adjustments = []

        # A类 7天未成交且回复冷淡 → 降B
        a_threshold = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        a_to_b = self.db.dict_fetchall(
            """SELECT c.id FROM customers c
               LEFT JOIN interactions i ON c.id = i.customer_id
               WHERE c.category = 'A' AND c.status NOT IN ('repeat', 'trial_order')
               GROUP BY c.id
               HAVING MAX(i.created_at) IS NULL OR MAX(i.created_at) < ?""",
            (a_threshold,),
        )
        for c in a_to_b:
            self.db.execute("UPDATE customers SET category = 'B' WHERE id = ?", (c["id"],))
            adjustments.append({"id": c["id"], "from": "A", "to": "B"})

        # B类 14天未回复 → 降C
        b_threshold = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        b_to_c = self.db.dict_fetchall(
            """SELECT c.id FROM customers c
               LEFT JOIN interactions i ON c.id = i.customer_id
               WHERE c.category = 'B'
               GROUP BY c.id
               HAVING MAX(i.created_at) IS NULL OR MAX(i.created_at) < ?""",
            (b_threshold,),
        )
        for c in b_to_c:
            self.db.execute("UPDATE customers SET category = 'C' WHERE id = ?", (c["id"],))
            adjustments.append({"id": c["id"], "from": "B", "to": "C"})

        if adjustments:
            self.db.commit()
            self.log_action("自动调整分类", f"调整了 {len(adjustments)} 个客户")

        return {"status": "ok", "adjustments": adjustments}
