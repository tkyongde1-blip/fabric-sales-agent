"""自动化流水线 - 一键执行完整业务流程"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import BASE_DIR, LOGGING

logging.basicConfig(
    level=getattr(logging, LOGGING["level"]),
    format=LOGGING["format"],
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGGING["file"], encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


class Pipeline:
    """自动化流水线 - 一键执行"""

    def __init__(self) -> None:
        from agents.orchestrator import Orchestrator
        from crm.report import ReportManager

        self.orchestrator = Orchestrator()
        self.report = ReportManager()

    def initialize(self) -> dict[str, Any]:
        """初始化系统（数据库+示例数据）"""
        from crm.database import Database

        logger.info("===== 系统初始化 =====")

        db = Database()
        db.initialize()

        # 如果数据已存在则跳过导入
        existing = db.dict_fetchone("SELECT COUNT(*) as cnt FROM products")
        if existing and existing["cnt"] > 0:
            logger.info("数据已存在，跳过导入 (count=%d)", existing["cnt"])
            return {"status": "ok", "message": "数据已存在，跳过导入", "products": existing["cnt"]}

        from data.import_data import import_products, import_customers, import_chat_records
        products = import_products()
        customers = import_customers()
        chats = import_chat_records()

        logger.info("初始化完成: 产品=%d 客户=%d 聊天=%d", products, customers, chats)
        return {"status": "ok", "products": products, "customers": customers, "chats": chats}

    def daily_routine(self) -> dict[str, Any]:
        """执行日例行"""
        logger.info("===== 日例行任务 =====")
        result = self.orchestrator.run_daily_routine()
        logger.info("日例行完成: %s", result)
        return result

    def import_chat(self, filepath: str, customer_id: int, fmt: str = "csv") -> dict[str, Any]:
        """导入聊天记录"""
        from automation.wechat_parser import WeChatParser

        parser = WeChatParser()
        if fmt == "csv":
            return parser.parse_csv(filepath, customer_id)
        elif fmt == "txt":
            return parser.parse_txt(filepath, customer_id)
        elif fmt == "html":
            return parser.parse_html(filepath, customer_id)
        return {"status": "error", "message": f"不支持的格式: {fmt}"}

    def process_message(self, customer_id: int, message: str) -> dict[str, Any]:
        """处理客户消息"""
        return self.orchestrator.process_incoming_message(customer_id, message)

    def classify_all(self) -> dict[str, Any]:
        """全量重新分类"""
        from agents.classification_agent import ClassificationAgent
        agent = ClassificationAgent()
        return agent.process({"action": "reclassify_all"})

    def generate_report(self, days: int = 30) -> dict[str, Any]:
        """生成数据报表"""
        return {
            "customer_summary": self.report.customer_summary(),
            "sales_summary": self.report.sales_summary(days),
            "conversion_funnel": self.report.conversion_funnel(),
            "followup_efficiency": self.report.followup_efficiency(days),
        }

    def generate_quote(
        self,
        customer_id: int,
        product_name: str,
        quantity: int,
        is_urgent: bool = False,
    ) -> dict[str, Any]:
        """生成报价"""
        from agents.sales_agent import SalesAgent
        agent = SalesAgent()
        return agent.process({
            "action": "generate_quote",
            "customer_id": customer_id,
            "product_name": product_name,
            "quantity": quantity,
            "is_urgent": is_urgent,
        })

    def full_pipeline(self) -> dict[str, Any]:
        """全流程执行"""
        logger.info("===== 全流程开始 =====")
        results = {}

        # 1. 初始化
        results["init"] = self.initialize()

        # 2. 分类
        results["classify"] = self.classify_all()

        # 3. 生成跟进任务
        results["followup"] = self.daily_routine()

        # 4. 报表
        results["report"] = self.generate_report()

        logger.info("===== 全流程完成 =====")
        return results


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(description="纺织面料销售自动化流水线")
    parser.add_argument("--init", action="store_true", help="初始化系统")
    parser.add_argument("--daily", action="store_true", help="执行日例行")
    parser.add_argument("--full", action="store_true", help="执行全流程")
    parser.add_argument("--classify", action="store_true", help="全量分类")
    parser.add_argument("--report", type=int, nargs="?", const=30, help="生成报表（天数）")
    parser.add_argument("--import-chat", metavar="FILE", help="导入聊天记录文件")
    parser.add_argument("--customer-id", type=int, help="客户ID（配合 --import-chat）")
    parser.add_argument("--chat-format", default="csv", choices=["csv", "txt", "html"], help="聊天文件格式")
    parser.add_argument("--message", help="处理客户消息（配合 --customer-id）")

    args = parser.parse_args()
    pipeline = Pipeline()

    if args.init:
        print(pipeline.initialize())
    elif args.daily:
        print(pipeline.daily_routine())
    elif args.full:
        print(pipeline.full_pipeline())
    elif args.classify:
        print(pipeline.classify_all())
    elif args.report:
        print(pipeline.generate_report(args.report))
    elif args.import_chat and args.customer_id:
        print(pipeline.import_chat(args.import_chat, args.customer_id, args.chat_format))
    elif args.message and args.customer_id:
        print(pipeline.process_message(args.customer_id, args.message))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
