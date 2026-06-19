"""任务调度模块 - 定时执行各项自动化任务"""
import logging
import time
from datetime import datetime
from typing import Any, Optional

import schedule

from automation.wechat_parser import WeChatParser
from agents.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class Scheduler:
    """定时任务调度器"""

    def __init__(self) -> None:
        self.orchestrator = Orchestrator()
        self.parser = WeChatParser()
        self._running = False

    def setup_jobs(self) -> None:
        """注册定时任务"""

        # 每天 09:00 运行日例行
        schedule.every().day.at("09:00").do(self._job_wrapper, "日例行", self._daily_routine)

        # 每天 10:00 A类客户跟进
        schedule.every().day.at("10:00").do(self._job_wrapper, "A类跟进", self._followup_a)

        # 每天 10:30 B类客户跟进
        schedule.every().day.at("10:30").do(self._job_wrapper, "B类跟进", self._followup_b)

        # 每天 11:00 C类客户跟进提醒
        schedule.every().day.at("11:00").do(self._job_wrapper, "C类跟进", self._followup_c)

        # 每天 14:00 扫描评论
        schedule.every().day.at("14:00").do(self._job_wrapper, "评论扫描", self._scan_comments)

        # 每天 20:00 再次扫描评论
        schedule.every().day.at("20:00").do(self._job_wrapper, "评论扫描", self._scan_comments)

        logger.info("定时任务已注册")

    def _job_wrapper(self, job_name: str, func) -> None:
        """任务包装器，附带异常处理和日志"""
        try:
            logger.info("[调度] 开始执行: %s", job_name)
            result = func()
            logger.info("[调度] 完成: %s | %s", job_name, result)
        except Exception as e:
            logger.error("[调度] 失败: %s | %s", job_name, e, exc_info=True)

    def _daily_routine(self) -> dict:
        return self.orchestrator.run_daily_routine()

    def _followup_a(self) -> dict:
        return self.orchestrator.followup.process({
            "action": "generate_tasks",
        })

    def _followup_b(self) -> dict:
        return self.orchestrator.followup.process({
            "action": "generate_tasks",
        })

    def _followup_c(self) -> dict:
        return self.orchestrator.followup.process({
            "action": "generate_tasks",
        })

    def _scan_comments(self) -> dict:
        try:
            from automation.comment_scraper import CommentScraper
            scraper = CommentScraper("douyin")
            return scraper.scan_and_process([])
        except Exception as e:
            logger.warning("评论扫描暂未接入API: %s", e)
            return {"status": "skipped", "message": str(e)}

    def start(self, run_once: bool = False) -> None:
        """启动调度器"""
        self.setup_jobs()
        self._running = True
        logger.info("调度器已启动")

        if run_once:
            schedule.run_all()
            return

        while self._running:
            schedule.run_pending()
            time.sleep(30)

    def stop(self) -> None:
        """停止调度器"""
        self._running = False
        logger.info("调度器已停止")
