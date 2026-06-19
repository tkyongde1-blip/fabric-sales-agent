"""Agent 基类 - 所有 Agent 的公共接口"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from crm.database import Database
from config import LOGGING

logging.basicConfig(level=LOGGING["level"], format=LOGGING["format"])
logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 抽象基类"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.db = Database()
        logger.info("Agent [%s] 初始化完成", self.name)

    @abstractmethod
    def process(self, context: dict[str, Any]) -> dict[str, Any]:
        """处理入口 - 每个子类必须实现"""
        ...

    def log_action(self, action: str, detail: str = "") -> None:
        """记录动作日志"""
        logger.info("[%s] %s | %s", self.name, action, detail)

    def get_prompt(self, prompt_name: str) -> Optional[str]:
        """加载 prompts 目录下的提示文档"""
        from pathlib import Path
        from config import PROMPTS_DIR

        path = PROMPTS_DIR / f"{prompt_name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning("提示文件不存在: %s", path)
        return None

    def create_followup_task(
        self,
        customer_id: int,
        content: str,
        due_at: Optional[str] = None,
        priority: str = "normal",
    ) -> int:
        """创建跟进任务"""
        if due_at is None:
            from datetime import timedelta
            due_at = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        self.db.execute(
            """INSERT INTO followup_tasks
               (customer_id, task_type, priority, content, due_at)
               VALUES (?, 'event_driven', ?, ?, ?)""",
            (customer_id, priority, content, due_at),
        )
        self.db.commit()
        task_id = self.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.log_action("创建跟进任务", f"customer={customer_id} task={task_id}")
        return task_id
