"""微信聊天记录解析模块

支持导入 WeChat 导出的聊天记录（CSV/TXT/HTML 格式）。
将非结构化聊天数据解析为结构化数据并存入数据库。

当前支持的导入格式：
1. CSV 格式（推荐）: customer_id, sender, content, timestamp
2. TXT 格式: 时间\t发送人\t内容
3. HTML 格式: WeChat 导出标准 HTML
"""
import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from crm.database import Database

logger = logging.getLogger(__name__)


class WeChatParser:
    """微信聊天记录解析器"""

    def __init__(self) -> None:
        self.db = Database()

    def parse_csv(self, filepath: str, customer_id: int) -> dict[str, Any]:
        """解析 CSV 格式聊天记录"""
        path = Path(filepath)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {filepath}"}

        count = 0
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.db.execute(
                    """INSERT OR IGNORE INTO wechat_messages
                       (customer_id, sender, content, timestamp)
                       VALUES (?, ?, ?, ?)""",
                    (
                        customer_id,
                        row.get("sender", "customer"),
                        row.get("content", ""),
                        row.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    ),
                )
                count += 1

        self.db.commit()
        logger.info("微信CSV导入: customer=%d, %d条记录", customer_id, count)
        return {"status": "ok", "records_imported": count}

    def parse_txt(self, filepath: str, customer_id: int) -> dict[str, Any]:
        """解析 TXT 格式聊天记录（每行: 时间\t发送人\t内容）"""
        path = Path(filepath)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {filepath}"}

        count = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    timestamp, sender, content = parts[0], parts[1], "\t".join(parts[2:])
                    self.db.execute(
                        """INSERT INTO wechat_messages
                           (customer_id, sender, content, timestamp)
                           VALUES (?, ?, ?, ?)""",
                        (customer_id, sender, content, timestamp),
                    )
                    count += 1

        self.db.commit()
        logger.info("微信TXT导入: customer=%d, %d条记录", customer_id, count)
        return {"status": "ok", "records_imported": count}

    def parse_html(self, filepath: str, customer_id: int) -> dict[str, Any]:
        """解析 WeChat 导出的 HTML 格式聊天记录"""
        from pyquery import PyQuery

        path = Path(filepath)
        if not path.exists():
            return {"status": "error", "message": f"文件不存在: {filepath}"}

        count = 0
        try:
            doc = PyQuery(filename=str(path))
            # WeChat HTML 导出的标准结构
            messages = doc(".message")
            for msg in messages.items():
                sender_el = msg.find(".sender")
                content_el = msg.find(".content")
                time_el = msg.find(".time")

                sender = "me" if "我" in (sender_el.text() or "") else "customer"
                content = content_el.text() or ""
                timestamp = time_el.text() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if content:
                    self.db.execute(
                        """INSERT INTO wechat_messages
                           (customer_id, sender, content, timestamp)
                           VALUES (?, ?, ?, ?)""",
                        (customer_id, sender, content, timestamp),
                    )
                    count += 1

            self.db.commit()
            logger.info("微信HTML导入: customer=%d, %d条记录", customer_id, count)
        except Exception as e:
            logger.error("HTML解析失败: %s", e)
            return {"status": "error", "message": str(e)}

        return {"status": "ok", "records_imported": count}

    def extract_customer_intent(self, customer_id: int) -> dict[str, Any]:
        """从聊天记录中提取客户需求"""
        messages = self.db.dict_fetchall(
            """SELECT content FROM wechat_messages
               WHERE customer_id = ? AND sender = 'customer'
               ORDER BY timestamp ASC""",
            (customer_id,),
        )
        if not messages:
            return {"status": "error", "message": "无聊天记录"}

        all_text = " ".join(m["content"] for m in messages)

        # 提取需求关键词
        intent = {"products": [], "quantity": None, "urgency": False, "budget": None}

        product_map = {
            "牛奶丝": "牛奶丝", "罗纹": "罗纹", "四面弹": "四面弹",
            "汗布": "汗布", "摇粒绒": "摇粒绒", "卫衣": "卫衣布",
        }
        for kw, name in product_map.items():
            if kw in all_text:
                intent["products"].append(name)

        q_match = re.search(r"(\d+)\s*[米kg公斤码]", all_text)
        if q_match:
            intent["quantity"] = int(q_match.group(1))

        intent["urgency"] = any(kw in all_text for kw in ["急", "马上", "现在就要"])
        intent["has_phone"] = bool(re.search(r"1[3-9]\d{9}", all_text))
        intent["message_count"] = len(messages)

        return {"status": "ok", "intent": intent}

    def batch_import_directory(self, directory: str) -> dict[str, Any]:
        """批量导入目录下的所有聊天记录文件"""
        path = Path(directory)
        if not path.is_dir():
            return {"status": "error", "message": f"目录不存在: {directory}"}

        results = {"csv": 0, "txt": 0, "html": 0}
        for f in path.iterdir():
            if f.suffix == ".csv":
                # 尝试从文件名提取 customer_id: {customer_id}_chat.csv
                try:
                    cid = int(f.stem.split("_")[0])
                    r = self.parse_csv(str(f), cid)
                    if r["status"] == "ok":
                        results["csv"] += r["records_imported"]
                except (ValueError, IndexError):
                    logger.warning("跳过文件(无法解析customer_id): %s", f.name)

            elif f.suffix == ".txt":
                try:
                    cid = int(f.stem.split("_")[0])
                    r = self.parse_txt(str(f), cid)
                    if r["status"] == "ok":
                        results["txt"] += r["records_imported"]
                except (ValueError, IndexError):
                    logger.warning("跳过文件: %s", f.name)

        logger.info("批量导入完成: %s", results)
        return {"status": "ok", **results}
