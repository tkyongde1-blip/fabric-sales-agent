"""报表统计模块"""
from datetime import datetime, timedelta
from typing import Any

from crm.database import Database


class ReportManager:
    """数据报表"""

    def __init__(self) -> None:
        self.db = Database()

    def customer_summary(self) -> dict[str, Any]:
        """客户概览统计"""
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM customers")["cnt"]
        by_category = self.db.fetchall(
            "SELECT category, COUNT(*) as cnt FROM customers GROUP BY category"
        )
        by_status = self.db.fetchall(
            "SELECT status, COUNT(*) as cnt FROM customers GROUP BY status"
        )
        by_source = self.db.fetchall(
            "SELECT source, COUNT(*) as cnt FROM customers GROUP BY source"
        )

        return {
            "total_customers": total,
            "by_category": {r["category"]: r["cnt"] for r in by_category},
            "by_status": {r["status"]: r["cnt"] for r in by_status},
            "by_source": {r["source"]: r["cnt"] for r in by_source},
        }

    def sales_summary(self, days: int = 30) -> dict[str, Any]:
        """销售概览统计"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        total_orders = self.db.fetchone(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(total_amount), 0) as total FROM orders WHERE created_at > ?",
            (cutoff,),
        )

        pending_followups = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM followup_tasks WHERE status = 'pending' AND due_at < ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )

        return {
            "period_days": days,
            "total_orders": total_orders["cnt"],
            "total_revenue": float(total_orders["total"]),
            "avg_order_value": float(total_orders["total"]) / max(total_orders["cnt"], 1),
            "pending_followups": pending_followups["cnt"],
        }

    def conversion_funnel(self) -> dict[str, int]:
        """获客成交转化漏斗"""
        stages = {
            "new_leads": self.db.fetchone("SELECT COUNT(*) as c FROM customers")["c"],
            "contacted": self.db.fetchone("SELECT COUNT(*) as c FROM customers WHERE status IN ('contacted','negotiating','sample_sent')")["c"],
            "negotiating": self.db.fetchone("SELECT COUNT(*) as c FROM customers WHERE status IN ('negotiating','sample_sent')")["c"],
            "trial_order": self.db.fetchone("SELECT COUNT(*) as c FROM customers WHERE status IN ('trial_order','repeat')")["c"],
            "repeat": self.db.fetchone("SELECT COUNT(*) as c FROM customers WHERE status = 'repeat'")["c"],
        }
        return stages

    def followup_efficiency(self, days: int = 30) -> dict[str, Any]:
        """跟进效率统计"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        total = self.db.fetchone(
            "SELECT COUNT(*) as c FROM followup_tasks WHERE created_at > ?", (cutoff,)
        )["c"]
        completed = self.db.fetchone(
            "SELECT COUNT(*) as c FROM followup_tasks WHERE created_at > ? AND status = 'completed'",
            (cutoff,),
        )["c"]

        return {
            "total_tasks": total,
            "completed": completed,
            "completion_rate": round(completed / max(total, 1) * 100, 1),
        }

    def daily_report(self) -> dict[str, Any]:
        """日报 - 今日关键数据"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_start = f"{today} 00:00:00"
        today_end = f"{today} 23:59:59"

        new_customers = self.db.fetchone(
            "SELECT COUNT(*) as c FROM customers WHERE created_at BETWEEN ? AND ?",
            (today_start, today_end),
        )["c"]

        new_orders = self.db.fetchone(
            "SELECT COUNT(*) as c, COALESCE(SUM(total_amount), 0) as t FROM orders WHERE created_at BETWEEN ? AND ?",
            (today_start, today_end),
        )

        pending = self.db.fetchone(
            "SELECT COUNT(*) as c FROM followup_tasks WHERE status = 'pending'"
        )["c"]

        return {
            "date": today,
            "new_customers": new_customers,
            "new_orders": new_orders["c"],
            "today_revenue": float(new_orders["t"]),
            "pending_followups": pending,
        }
