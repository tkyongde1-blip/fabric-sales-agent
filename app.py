"""纺织面料销售助手 - Windows GUI 客户端"""
import json
import os
import re
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 路径处理（支持 PyInstaller 打包） ──
if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys._MEIPASS)
    _WORK_DIR = Path(os.path.dirname(sys.executable))
    os.environ.setdefault("FABRIC_DB_PATH", str(_WORK_DIR / "data" / "fabric_sales.db"))
    os.environ.setdefault("FABRIC_DATA_DIR", str(_BASE_DIR / "data"))
    os.environ.setdefault("TCL_LIBRARY", str(_BASE_DIR / "_tcl_data"))
    os.environ.setdefault("TK_LIBRARY", str(_BASE_DIR / "_tk_data"))
else:
    _BASE_DIR = Path(__file__).parent
    _WORK_DIR = _BASE_DIR
    os.environ.setdefault("FABRIC_DB_PATH", str(_BASE_DIR / "data" / "fabric_sales.db"))
    os.environ.setdefault("FABRIC_DATA_DIR", str(_BASE_DIR / "data"))

# 确保 data 目录存在
Path(os.environ["FABRIC_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)

# ── 屏蔽非必要日志 ──
_TRACE_LOG_PATH = _WORK_DIR / "data" / "auto_reply_trace.log"
logging.basicConfig(level=logging.WARNING)
_trace_logger = logging.getLogger("auto_reply_trace")
if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(_TRACE_LOG_PATH) for h in _trace_logger.handlers):
    _trace_logger.setLevel(logging.INFO)
    _trace_handler = logging.FileHandler(_TRACE_LOG_PATH, encoding="utf-8")
    _trace_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _trace_logger.addHandler(_trace_handler)
    _trace_logger.propagate = False

# ── CustomTkinter ──
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
except ImportError:
    print("=" * 50)
    print("需要安装 customtkinter，请运行: pip install customtkinter")
    print("=" * 50)
    sys.exit(1)

# ── 项目模块 ──
sys.path.insert(0, str(_BASE_DIR))
from config import PRICING
from crm.database import Database
from agents.sales_agent import SalesAgent
from agents.wechat_bot import WeChatBot, ReceivedMessage


# ══════════════════════════════════════════════════════════
# 面料规格解析器
# ══════════════════════════════════════════════════════════

class FabricSpecParser:
    """从客户消息中提取面料规格参数"""

    PRODUCT_KEYWORDS = [
        "牛奶丝", "罗纹布", "罗纹", "四面弹", "汗布",
        "摇粒绒", "卫衣布", "加绒罗纹", "加绒",
    ]

    PRODUCT_MAP = {
        "牛奶丝": "牛奶丝", "罗纹": "罗纹布", "罗纹布": "罗纹布",
        "四面弹": "四面弹", "汗布": "汗布", "摇粒绒": "摇粒绒",
        "卫衣布": "卫衣布", "加绒罗纹": "加绒罗纹", "加绒": "加绒罗纹",
    }

    COLORS = [
        "白色", "黑色", "红色", "蓝色", "绿色", "黄色", "灰色",
        "粉色", "紫色", "橙色", "藏青", "深蓝", "浅蓝", "米色",
        "卡其", "咖啡", "棕色", "酒红", "枣红", "玫红", "深灰", "浅灰",
        "漂白", "本白", "象牙白", "藏蓝", "天蓝", "宝蓝", "墨绿",
        "草绿", "荧光绿", "桔色", "驼色", "烟灰", "麻灰", "银灰",
        "深灰", "中灰", "浅灰",
    ]

    def parse(self, text: str) -> dict[str, Any]:
        """解析客户消息，返回结构化数据"""
        if not text or not text.strip():
            return {}

        text = text.strip()
        result: dict[str, Any] = {
            "原消息": text,
            "产品": "",
            "幅宽": "",
            "克重": "",
            "颜色": "",
            "工艺": "",
            "数量": 0,
            "数量单位": "米",
            "是否加急": False,
        }

        result["产品"] = self._extract_product(text)
        result["幅宽"] = self._extract_width(text)
        result["克重"] = self._extract_weight(text)
        result["颜色"] = self._extract_color(text)
        result["工艺"] = self._extract_process(text)

        qty, unit = self._extract_quantity(text)
        if qty:
            result["数量"] = qty
            result["数量单位"] = unit

        result["是否加急"] = any(kw in text for kw in ["急", "马上", "尽快", "加急"])

        # 合成规格字符串
        spec_parts = []
        if result["幅宽"]:
            spec_parts.append(result["幅宽"])
        if result["克重"]:
            spec_parts.append(result["克重"])
        result["规格"] = " ".join(spec_parts) if spec_parts else ""

        return result

    # ── 内部提取方法 ──

    def _extract_product(self, text: str) -> str:
        for kw in self.PRODUCT_KEYWORDS:
            if kw in text:
                return self.PRODUCT_MAP.get(kw, kw)
        return ""

    def _extract_width(self, text: str) -> str:
        """提取幅宽: 150cm, 180门幅, D150"""
        m = re.search(r"(\d{2,3})\s*(?:cm|CM|门幅|幅宽|D数|D|d\b)", text)
        if m:
            return f"{m.group(1)}cm"
        return ""

    def _extract_weight(self, text: str) -> str:
        """提取克重: 180g, 200克, 克重220"""
        m = re.search(r"克重[：:]?\s*(\d{2,3})", text)
        if m:
            return f"{m.group(1)}g/m²"
        m = re.search(r"(\d{2,3})\s*(?:g|G|克)\s*/?\s*(?:m|米|㎡)?", text)
        if m:
            return f"{m.group(1)}g/m²"
        return ""

    def _extract_color(self, text: str) -> str:
        # 长颜色词优先匹配
        sorted_colors = sorted(self.COLORS, key=len, reverse=True)
        for color in sorted_colors:
            if color in text:
                return color
        return ""

    def _extract_process(self, text: str) -> str:
        if "双磨" in text:
            return "双磨"
        if "单磨" in text:
            return "单磨"
        return ""

    def _extract_quantity(self, text: str) -> tuple[int, str]:
        m = re.search(r"(\d+)\s*(米|m|M|公斤|kg|KG|码|匹|卷)", text)
        if m:
            raw_unit = m.group(2).lower()
            unit_map = {"m": "米", "kg": "公斤", "k": "公斤"}
            unit = unit_map.get(raw_unit, m.group(2))
            return int(m.group(1)), unit
        return 0, "米"


# ══════════════════════════════════════════════════════════
# 回复生成器（与现有 Agent 集成）
# ══════════════════════════════════════════════════════════

class ResponseGenerator:
    """基于解析结果和系统话术生成各种回复"""

    def __init__(self):
        self._db: Optional[Database] = None
        self._sales: Optional[SalesAgent] = None
        self._products_cache: list[dict] = []
        self._init_backend()

    def _init_backend(self):
        """初始化数据库和 Agent"""
        try:
            self._db = Database()
            # 测试连接
            self._db.execute("SELECT COUNT(*) FROM products")
        except Exception:
            # 初始化数据库
            from automation.pipeline import Pipeline
            pipeline = Pipeline()
            pipeline.initialize()
            self._db = Database()

        try:
            self._sales = SalesAgent()
            self._products_cache = self._db.dict_fetchall(
                "SELECT * FROM products WHERE is_active = 1"
            )
        except Exception:
            self._products_cache = []

    def _find_matching_product(self, parsed: dict) -> Optional[dict]:
        """找到最匹配的产品"""
        if not self._products_cache:
            return None

        product_name = parsed.get("产品", "")
        width = parsed.get("幅宽", "")
        weight = parsed.get("克重", "")

        scored: list[tuple[int, dict]] = []
        for p in self._products_cache:
            score = 0
            pname = p.get("name", "")
            pspec = p.get("spec", "")

            if product_name and product_name in pname:
                score += 10
            if product_name == pname:
                score += 5  # 精确匹配额外加分

            if width and width.split("c")[0] in pspec:
                score += 5
            if weight:
                w = weight.split("g")[0]
                if w in pspec:
                    score += 5

            scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored and scored[0][0] > 0 else None

    # ── 报价生成 ──

    def generate_quote(self, parsed: dict, customer_name: str = "",
                       wechat_id: str = "") -> dict[str, Any]:
        """生成报价"""
        product = self._find_matching_product(parsed)

        result: dict[str, Any] = {"success": False, "text": "", "customer_id": None}

        quantity = parsed.get("数量", 0) or 100
        is_urgent = parsed.get("是否加急", False)

        # ── 有产品 + 数量 → 计算价格 ──
        if product and quantity:
            price_info = self._calc_price(product, quantity, is_urgent)

            spec_str = parsed.get("规格", "") or product.get("spec", "")
            detail_parts = [f"规格：{spec_str}"]
            if parsed.get("颜色"):
                detail_parts.append(f"颜色：{parsed['颜色']}")
            if parsed.get("工艺"):
                detail_parts.append(f"工艺：{parsed['工艺']}")

            quote_no = f"Q-{datetime.now().strftime('%Y%m%d')}-PREVIEW"
            customer_id = self._ensure_customer(customer_name, wechat_id)
            if customer_id:
                try:
                    self._db.execute(
                        """INSERT INTO quotations
                           (quote_no, customer_id, product_id, quantity, unit_price, total_amount, status)
                           VALUES (?, ?, ?, ?, ?, ?, 'draft')""",
                        (quote_no, customer_id, product["id"],
                         quantity, price_info["final_price"], price_info["total_amount"]),
                    )
                    self._db.commit()
                    result["customer_id"] = customer_id
                except Exception:
                    pass

            result.update({
                "success": True,
                "product_name": product["name"],
                "product_spec": product.get("spec", ""),
                "unit_price": price_info["final_price"],
                "total_amount": price_info["total_amount"],
                "text": (
                    f"【报价单】\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"产品：{product['name']}\n"
                    f"{chr(10).join(detail_parts)}\n"
                    f"数量：{quantity}{parsed.get('数量单位', '米')}\n"
                    f"单价：¥{price_info['final_price']:.2f}/米\n"
                    f"总金额：¥{price_info['total_amount']:,.2f}\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"交期：现货（款到发货）\n"
                    f"付款：现金 / 转账\n"
                    f"有效：7 天\n\n"
                    f"我们是工厂直发，价格实价，质量保证 A 品足米足秤。\n"
                    f"现货充足，随时可发！"
                ),
            })
            return result

        # ── 有产品但没数量 ──
        if product:
            price_info = self._calc_price(product, 100, is_urgent)
            result.update({
                "success": True,
                "text": (
                    f"【参考报价】{product['name']} {product.get('spec', '')}\n"
                    f"参考单价：¥{price_info['final_price']:.2f}/米\n"
                    f"（请告知具体数量，以便给您精确报价）\n\n"
                    f"我们是工厂直发，价格实价，支持寄样！"
                ),
            })
            return result

        # ── 什么也没识别到 ──
        result["text"] = f"未识别到产品信息，请确认消息中包含面料名称（如牛奶丝、罗纹等）。"
        return result

    def _calc_price(self, product: dict, quantity: int, is_urgent: bool) -> dict:
        """计算价格（复用 SalesAgent 逻辑）"""
        try:
            if self._sales:
                return self._sales._calculate_price(product, quantity, False, is_urgent)
        except Exception:
            pass

        # 兜底计算
        base_price = float(product.get("standard_price", 0))
        cost_price = float(product.get("cost_price", 0))
        min_price = float(product.get("min_price", 0))

        if quantity >= 3000:
            discount = 0.92
        elif quantity >= 1000:
            discount = 0.95
        elif quantity >= 500:
            discount = 0.98
        else:
            discount = 1.0

        urgency = 1.05 if is_urgent else 1.0
        unit_price = base_price * discount * urgency
        min_acceptable = max(min_price, cost_price * 1.15)
        unit_price = max(unit_price, min_acceptable)

        return {
            "final_price": round(unit_price, 2),
            "total_amount": round(unit_price * quantity, 2),
            "base_price": base_price,
            "cost_price": cost_price,
        }

    def _ensure_customer(self, name: str, wechat_id: str) -> Optional[int]:
        """查找或创建客户"""
        if not name and not wechat_id:
            return None
        try:
            if wechat_id:
                existing = self._db.dict_fetchone(
                    "SELECT id FROM customers WHERE wechat_id = ?", (wechat_id,)
                )
                if existing:
                    return existing["id"]
            if name:
                existing = self._db.dict_fetchone(
                    "SELECT id FROM customers WHERE name = ?", (name,)
                )
                if existing:
                    return existing["id"]

            display_name = name or f"客户_{datetime.now().strftime('%m%d%H%M')}"
            self._db.execute(
                """INSERT INTO customers (name, wechat_id, source, status, score, category)
                   VALUES (?, ?, 'manual', 'new_lead', 0, 'B')""",
                (display_name, wechat_id or ""),
            )
            self._db.commit()
            return self._db.conn.lastrowid
        except Exception:
            return None

    # ── 样品回复 ──

    def generate_sample_reply(self, parsed: dict) -> str:
        return (
            "A4手感样不需要收费。\n\n"
            "公司规定，剪样1米需收取散剪费用，作为损耗成本。后续正式开单时，财务会返还部分散剪费用。\n\n"
            "请问您需要剪多少米样布？"
        )

    # ── 催付款 ──

    def generate_payment_reminder(self, parsed: dict) -> str:
        product = parsed.get("产品", "这款面料")
        quantity = parsed.get("数量", 0)
        qty_str = f" {quantity}{parsed.get('数量单位', '米')}" if quantity else ""

        return (
            f"X总您好！打扰您了。\n\n"
            f"{product}{qty_str} 的报价您看过了，如果没问题的话，麻烦安排一下付款。\n\n"
            f"我们收到款后马上安排仓库备货，当天就可以发货。\n\n"
            f"付款方式：银行转账 / 微信 / 支付宝\n"
            f"确认后我第一时间安排生产！"
        )

    # ── 发货确认 ──

    def generate_shipping_confirmation(self, parsed: dict, tracking_no: str = "") -> str:
        product = parsed.get("产品", "您的货")
        quantity = parsed.get("数量", 0)
        qty_str = f" {quantity}{parsed.get('数量单位', '米')}" if quantity else ""
        track_str = f"\n物流单号：{tracking_no}" if tracking_no else ""

        return (
            f"X总，您的货已经发出啦！{track_str}\n\n"
            f"产品：{product}{qty_str}\n"
            f"预计 3-5 天到货，到了麻烦确认一下质量。\n\n"
            f"有任何问题随时找我！"
        )


# ══════════════════════════════════════════════════════════
# GUI 主界面
# ══════════════════════════════════════════════════════════

class App(ctk.CTk):
    """纺织面料销售助手主窗口"""

    # 颜色方案
    BG = "#f0f0f0"
    FG = "#1a1a2e"
    ACCENT = "#2d6a9f"
    SUCCESS = "#27ae60"
    WARN = "#e67e22"

    PAD = 12

    def __init__(self):
        super().__init__()
        self.update_idletasks()
        self.geometry("1000x700+100+100")
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.after(1000, lambda: self.attributes("-topmost", False))

        self.title("纺织面料销售助手")
        self.minsize(720, 680)

        # ── 后端 ──
        self.parser = FabricSpecParser()
        self.generator = ResponseGenerator()
        self._ensure_runtime_schema()
        self._last_parsed: dict[str, Any] = {}
        self._last_quote: Optional[dict] = None
        self._wechat_bot: Optional[WeChatBot] = None
        self._wechat_path: str = ""
        self._last_wechat_msg: Optional[ReceivedMessage] = None
        self._last_wechat_contact: str = ""
        self._last_parsed_by_contact: dict[str, dict[str, Any]] = {}
        self._last_replied_text_by_contact: dict[str, str] = {}
        self._last_replied_signature_by_contact: dict[str, str] = {}
        self._wechat_auto_mode = False
        self._wechat_auto_after_id: Optional[str] = None
        self._wechat_autostart_after_id: Optional[str] = None
        self._last_daily_summary_date: str = ""

        # ── UI ──
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)  # 输出区域自动伸缩

        self._build_header()
        self._build_customer_frame()
        self._build_message_frame()
        self._build_parse_frame()
        self._build_action_buttons()
        self._build_wechat_frame()
        self._build_output_frame()
        self._build_status_bar()

        # 初始化状态
        self._set_status("就绪 | 输入客户消息后点击「解析消息」")
        # 启动后自动尝试接管当前微信聊天窗口；若微信稍后才打开，则持续轻量重试。
        self.after(800, self._bootstrap_wechat_automation)
        self.after(1500, self._schedule_daily_summary_check)

    # ── UI 构建 ──

    def _ensure_runtime_schema(self):
        """为已有数据库补齐新增的运行期表。"""
        from crm.database import Database
        db = Database()
        db.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_whitelist (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   contact_name TEXT UNIQUE NOT NULL,
                   created_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_controls (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   contact_name TEXT UNIQUE NOT NULL,
                   paused BOOLEAN DEFAULT 0,
                   manual_takeover BOOLEAN DEFAULT 0,
                   updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_audit (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   contact_name TEXT NOT NULL,
                   incoming_message TEXT,
                   reply_text TEXT,
                   status TEXT NOT NULL,
                   error_message TEXT,
                   created_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_state (
                   contact_name TEXT PRIMARY KEY,
                   last_replied_signature TEXT,
                   updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.commit()

    def _build_header(self):
        """顶部标题栏"""
        header = ctk.CTkFrame(self, fg_color=self.ACCENT, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(
            header, text="🧵 纺织面料销售助手",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white",
        )
        title.grid(row=0, column=0, padx=16, pady=10)

        self._db_label = ctk.CTkLabel(
            header, text="数据库：已连接",
            font=ctk.CTkFont(size=12),
            text_color="#d0e8ff",
        )
        self._db_label.grid(row=0, column=1, padx=12, sticky="e")

    def _build_customer_frame(self):
        """客户信息输入区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, padx=self.PAD, pady=(self.PAD, 0), sticky="ew")
        frame.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(frame, text="客户信息", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 2)
        )

        ctk.CTkLabel(frame, text="姓名：").grid(row=1, column=0, sticky="w", padx=6)
        self._entry_name = ctk.CTkEntry(frame, placeholder_text="选填")
        self._entry_name.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=4)

        ctk.CTkLabel(frame, text="微信：").grid(row=1, column=2, sticky="w")
        self._entry_wechat = ctk.CTkEntry(frame, placeholder_text="选填")
        self._entry_wechat.grid(row=1, column=3, sticky="ew", padx=(0, 6), pady=4)

    def _build_message_frame(self):
        """客户消息输入区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, padx=self.PAD, pady=(self.PAD, 0), sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="客户消息", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )

        self._btn_parse = ctk.CTkButton(
            header, text="🔍 解析消息", width=110, height=28,
            command=self._on_parse,
        )
        self._btn_parse.grid(row=0, column=1, padx=4)

        self._text_input = ctk.CTkTextbox(frame, height=90, wrap="word")
        self._text_input.grid(row=1, column=0, sticky="ew", padx=6, pady=(4, 6))
        self._text_input.insert("0.0", "例如：牛奶丝 150cm 180g 白色 单磨 500米 多少钱？")

        # 绑定快捷键
        self._text_input.bind("<Control-Return>", lambda e: self._on_parse())

    def _build_parse_frame(self):
        """解析结果显示区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=3, column=0, padx=self.PAD, pady=(self.PAD, 0), sticky="ew")
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(frame, text="识别结果", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 2)
        )

        # 用标签占位，后续更新
        self._lbl_product = ctk.CTkLabel(frame, text="产品：—", anchor="w")
        self._lbl_product.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=1)

        self._lbl_spec = ctk.CTkLabel(frame, text="规格：—", anchor="w")
        self._lbl_spec.grid(row=1, column=2, columnspan=2, sticky="w", padx=6, pady=1)

        self._lbl_color = ctk.CTkLabel(frame, text="颜色：—", anchor="w")
        self._lbl_color.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=1)

        self._lbl_process = ctk.CTkLabel(frame, text="工艺：—", anchor="w")
        self._lbl_process.grid(row=2, column=2, columnspan=2, sticky="w", padx=6, pady=1)

        self._lbl_qty = ctk.CTkLabel(frame, text="数量：—", anchor="w")
        self._lbl_qty.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(1, 6))

        self._lbl_urgent = ctk.CTkLabel(frame, text="加急：—", anchor="w")
        self._lbl_urgent.grid(row=3, column=2, columnspan=2, sticky="w", padx=6, pady=(1, 6))

    def _build_action_buttons(self):
        """操作按钮区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=4, column=0, padx=self.PAD, pady=(self.PAD, 0), sticky="ew")
        frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._btn_quote = ctk.CTkButton(
            frame, text="📄 生成报价", height=36,
            fg_color=self.ACCENT, command=self._on_quote,
        )
        self._btn_quote.grid(row=0, column=0, padx=6, pady=8, sticky="ew")

        self._btn_sample = ctk.CTkButton(
            frame, text="🧪 样品回复", height=36,
            fg_color="#8e44ad", command=self._on_sample,
        )
        self._btn_sample.grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        self._btn_payment = ctk.CTkButton(
            frame, text="💰 催付款", height=36,
            fg_color=self.WARN, command=self._on_payment,
        )
        self._btn_payment.grid(row=0, column=2, padx=6, pady=8, sticky="ew")

        self._btn_shipping = ctk.CTkButton(
            frame, text="🚚 发货确认", height=36,
            fg_color=self.SUCCESS, command=self._on_shipping,
        )
        self._btn_shipping.grid(row=0, column=3, padx=6, pady=8, sticky="ew")

    def _build_wechat_frame(self):
        """微信助手控制区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=5, column=0, padx=self.PAD, pady=(self.PAD, 0), sticky="ew")
        frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(frame, text="微信助手", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 2)
        )

        # 状态灯
        self._wc_status = ctk.CTkLabel(frame, text="●  未连接", text_color="#95a5a6", anchor="w")
        self._wc_status.grid(row=1, column=0, padx=6, pady=2, sticky="w")

        self._btn_wc_start = ctk.CTkButton(
            frame, text="▶ 启动微信助手", width=120, height=28,
            fg_color="#27ae60", command=self._on_wc_start,
        )
        self._btn_wc_start.grid(row=1, column=1, padx=4, pady=2)

        self._btn_wc_stop = ctk.CTkButton(
            frame, text="■ 停止", width=70, height=28,
            fg_color="#7f8c8d", state="disabled", command=self._on_wc_stop,
        )
        self._btn_wc_stop.grid(row=1, column=2, padx=4, pady=2)

        # 微信路径选择
        self._btn_wc_path = ctk.CTkButton(
            frame, text="📂 选择微信路径", width=110, height=28,
            fg_color="#5b2c6f", command=self._on_select_wechat_path,
        )
        self._btn_wc_path.grid(row=1, column=3, padx=4, pady=2)

        self._wc_path_label = ctk.CTkLabel(
            frame, text="", anchor="w",
            text_color="#7f8c8d",
        )
        self._wc_path_label.grid(row=1, column=4, padx=(0, 6), pady=2, sticky="w")

        # 操作按钮
        self._btn_wc_read = ctk.CTkButton(
            frame, text="📥 读取当前消息", width=120, height=28,
            state="disabled", command=self._on_wc_read,
        )
        self._btn_wc_read.grid(row=2, column=0, padx=6, pady=(0, 6), sticky="w")

        self._btn_wc_send = ctk.CTkButton(
            frame, text="📤 发送到微信", width=120, height=28,
            fg_color="#2d6a9f", state="disabled", command=self._on_wc_send,
        )
        self._btn_wc_send.grid(row=2, column=1, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_auto = ctk.CTkButton(
            frame, text="🤖 开启自动处理", width=120, height=28,
            fg_color="#8e44ad", state="disabled", command=self._on_wc_auto_toggle,
        )
        self._btn_wc_auto.grid(row=2, column=2, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_allow = ctk.CTkButton(
            frame, text="✅ 加入白名单", width=110, height=28,
            fg_color="#16a085", command=self._on_add_whitelist,
        )
        self._btn_wc_allow.grid(row=3, column=0, padx=6, pady=(0, 6), sticky="w")

        self._btn_wc_remove_allow = ctk.CTkButton(
            frame, text="↩ 移出白名单", width=110, height=28,
            fg_color="#7f8c8d", command=self._on_remove_whitelist,
        )
        self._btn_wc_remove_allow.grid(row=3, column=1, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_blacklist = ctk.CTkButton(
            frame, text="⛔ 拉黑当前客户", width=120, height=28,
            fg_color="#c0392b", command=self._on_blacklist_contact,
        )
        self._btn_wc_blacklist.grid(row=3, column=2, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_pause = ctk.CTkButton(
            frame, text="⏸ 暂停自动回复", width=120, height=28,
            fg_color="#d68910", command=self._on_pause_contact,
        )
        self._btn_wc_pause.grid(row=3, column=3, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_resume = ctk.CTkButton(
            frame, text="▶ 恢复自动回复", width=120, height=28,
            fg_color="#229954", command=self._on_resume_contact,
        )
        self._btn_wc_resume.grid(row=3, column=4, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_takeover = ctk.CTkButton(
            frame, text="👤 人工接管", width=110, height=28,
            fg_color="#6c3483", command=self._on_takeover_contact,
        )
        self._btn_wc_takeover.grid(row=4, column=0, padx=6, pady=(0, 6), sticky="w")

        self._btn_wc_release = ctk.CTkButton(
            frame, text="🤖 释放接管", width=110, height=28,
            fg_color="#2874a6", command=self._on_release_contact,
        )
        self._btn_wc_release.grid(row=4, column=1, padx=4, pady=(0, 6), sticky="w")

        self._btn_wc_summary = ctk.CTkButton(
            frame, text="📊 今日汇总", width=110, height=28,
            fg_color="#34495e", command=self._on_show_daily_summary,
        )
        self._btn_wc_summary.grid(row=4, column=2, padx=4, pady=(0, 6), sticky="w")

        self._wc_sender = ctk.CTkLabel(frame, text="发送人：—", anchor="w")
        self._wc_sender.grid(row=2, column=3, padx=4, pady=(0, 6), sticky="w")

        self._wc_preview = ctk.CTkLabel(frame, text="最新消息：—", anchor="w")
        self._wc_preview.grid(row=2, column=4, columnspan=2, padx=6, pady=(0, 6), sticky="w")

        # 启动时自动搜索微信路径
        self._auto_detect_wechat()

    # ── 微信助手事件 ──

    def _auto_detect_wechat(self):
        """启动时自动搜索微信路径"""
        from agents.wechat_bot import find_wechat
        path = find_wechat()
        if path:
            self._wechat_path = path
            self._wc_path_label.configure(
                text=f"✓ {path}",
                text_color="#27ae60",
            )

    def _on_select_wechat_path(self):
        """手动选择微信路径"""
        from tkinter import filedialog
        exe = filedialog.askopenfilename(
            title="选择 WeChat.exe",
            filetypes=[("WeChat.exe", "WeChat.exe")],
            initialdir="C:\\Program Files",
        )
        if not exe:
            return
        wechat_dir = os.path.dirname(exe)
        self._wechat_path = wechat_dir
        self._wc_path_label.configure(
            text=f"✓ {wechat_dir}",
            text_color="#27ae60",
        )
        self._set_status(f"✅ 已选择微信路径: {wechat_dir}")

    def _on_wc_start(self):
        """启动微信助手"""
        self._start_wechat_bot(auto_enable=False)

    def _bootstrap_wechat_automation(self):
        """启动后自动连接微信，并进入当前聊天窗口自动处理模式。"""
        self._wechat_autostart_after_id = None
        if self._wechat_bot and self._wechat_bot.is_running:
            if not self._wechat_auto_mode:
                self._enable_wechat_auto_mode()
            return

        if self._start_wechat_bot(auto_enable=True, quiet=True):
            return

        # 微信可能尚未打开或窗口暂时不可见；继续等待，不要求用户再点一次。
        self._wechat_autostart_after_id = self.after(5000, self._bootstrap_wechat_automation)

    def _start_wechat_bot(self, *, auto_enable: bool, quiet: bool = False) -> bool:
        """连接微信窗口；可选择在连接成功后立即进入自动处理模式。"""
        if self._wechat_autostart_after_id:
            try:
                self.after_cancel(self._wechat_autostart_after_id)
            except Exception:
                pass
            self._wechat_autostart_after_id = None

        self._set_status("⏳ 正在连接微信...")
        self.update()

        bot = WeChatBot(wechat_path=self._wechat_path or None)
        ok, msg = bot.start()
        if not ok:
            if quiet:
                self._set_status("等待微信聊天窗口就绪，随后将自动开始监控")
            else:
                self._set_status("❌ 微信连接失败（详见输出区）")
                self._show_output(
                    f"微信连接失败\n"
                    f"{'─' * 40}\n"
                    f"{msg}\n"
                    f"{'─' * 40}\n\n"
                    f"请尝试：\n"
                    f"  1. 确保微信已安装并登录\n"
                    f"  2. 点击「选择微信路径」手动选择 WeChat.exe\n"
                    f"  3. 以管理员身份运行本程序\n"
                    f"  4. 重启微信后重试"
                )
            return False

        self._wechat_bot = bot
        bot.set_callback(self._on_wechat_msg)

        # 更新 UI
        self._wc_status.configure(text="●  已连接", text_color="#27ae60")
        self._btn_wc_start.configure(state="disabled")
        self._btn_wc_stop.configure(state="normal")
        self._btn_wc_read.configure(state="normal")
        self._btn_wc_auto.configure(state="normal")
        self._set_status("✅ 微信助手已启动，微信窗口已就绪")
        if auto_enable:
            self._enable_wechat_auto_mode()
        return True

    def _on_wc_stop(self):
        """停止微信助手"""
        if self._wechat_bot:
            self._wechat_bot.stop()
            self._wechat_bot = None
        self._stop_wechat_auto_mode()
        if self._wechat_autostart_after_id:
            try:
                self.after_cancel(self._wechat_autostart_after_id)
            except Exception:
                pass
            self._wechat_autostart_after_id = None

        self._wc_status.configure(text="●  未连接", text_color="#95a5a6")
        self._btn_wc_start.configure(state="normal")
        self._btn_wc_stop.configure(state="disabled")
        self._btn_wc_read.configure(state="disabled")
        self._btn_wc_send.configure(state="disabled")
        self._btn_wc_auto.configure(state="disabled", text="🤖 开启自动处理")
        self._wc_sender.configure(text="发送人：—")
        self._wc_preview.configure(text="最新消息：—")
        self._set_status("微信助手已停止")

    def _on_wechat_msg(self, msg: ReceivedMessage):
        """收到微信消息回调（在 bot 线程中）"""
        def _update():
            preview = msg.content[:40] + "..." if len(msg.content) > 40 else msg.content
            self._wc_sender.configure(text=f"发送人：{msg.nickname}")
            self._wc_preview.configure(text=f"最新消息：{preview}")
            self._last_wechat_msg = msg

        # after 安全地回到 GUI 线程
        try:
            self.after(0, _update)
        except Exception:
            pass

    def _on_wc_read(self):
        """一键读取微信当前聊天中的最后一条客户消息"""
        if not self._wechat_bot:
            return

        self._set_status("⏳ 正在读取微信消息...")
        self.update()

        try:
            _trace_logger.info("STEP 1 manual_read_entry")
            msg = self._wechat_bot.read_last_message()
            if not msg:
                _trace_logger.info("STEP 1 STOP no_message")
                self._show_output("未能识别最后一条客户消息，请确认微信聊天窗口已打开且包含客户消息。")
                self._set_status("⚠️ 未能识别最后一条客户消息")
                return

            _trace_logger.info("STEP 2 manual_message_read contact=%s content=%r", msg.nickname, msg.content)
            self._process_wechat_message(msg, auto_send=True)

        except Exception:
            self._show_output("读取失败，请查看控制台日志。")
            self._set_status("⚠️ 读取失败")
            logging.getLogger(__name__).exception("读取微信消息异常")

    def _on_wc_send(self):
        """发送当前回复到微信（搜索已保存的联系人 → 粘贴发送）"""
        text = self._text_output.get("0.0", "end").strip()
        if not text:
            self._set_status("⚠️ 回复内容为空")
            return

        if not self._wechat_bot or not self._wechat_bot.is_running:
            self._set_status("⚠️ 微信未连接")
            return

        # 取已保存的联系人名称（没有则直接发送到当前活动聊天）
        contact = self._entry_name.get().strip() or self._last_wechat_contact
        ok, result = self.send_to_wechat(text, contact)
        if ok:
            self._set_status(f"✅ 已发送到 {contact or '当前窗口'}")
            # 保存本次交互到数据库
            self._save_wechat_interaction(contact, text)
            self._record_auto_reply_audit(contact or self._last_wechat_contact, "", text, "manual_sent", "")
        else:
            self._record_auto_reply_audit(contact or self._last_wechat_contact, "", text, "manual_failed", result)
            self._raise_failure_alert(contact or self._last_wechat_contact or "未知联系人", result)
            self._set_status(f"❌ {result}")

    def _on_wc_auto_toggle(self):
        """切换自动读取 → 解析 → 回复 → 发送流水线。"""
        if self._wechat_auto_mode:
            self._stop_wechat_auto_mode()
            self._set_status("自动处理已关闭")
            return
        if not self._wechat_bot or not self._wechat_bot.is_running:
            self._set_status("⚠️ 微信未连接")
            return
        self._enable_wechat_auto_mode()

    def _enable_wechat_auto_mode(self):
        """把当前聊天窗口纳入自动监控，并以最后一条现有消息作为基线。"""
        if self._wechat_auto_mode:
            return
        if not self._wechat_bot or not self._wechat_bot.is_running:
            return
        # 先把“当前已存在的最后一条消息”当作基线，避免刚开启就误发旧消息。
        try:
            baseline = self._wechat_bot.read_last_message(only_new=False)
            if baseline:
                baseline_signature = self._message_signature_for_reply(baseline)
                self._last_replied_signature_by_contact[baseline.nickname] = baseline_signature
                self._last_wechat_contact = baseline.nickname
                self._last_wechat_msg = baseline
        except Exception:
            logging.getLogger(__name__).exception("初始化自动处理基线失败")
        self._wechat_auto_mode = True
        self._btn_wc_auto.configure(text="⏹ 关闭自动处理", fg_color="#c0392b")
        self._set_action_buttons_enabled(False)
        self._set_status("🤖 自动处理已开启：检测到新客户消息后将自动回复")
        self._schedule_wechat_auto_poll()

    def _stop_wechat_auto_mode(self):
        self._wechat_auto_mode = False
        if self._wechat_auto_after_id:
            try:
                self.after_cancel(self._wechat_auto_after_id)
            except Exception:
                pass
        self._wechat_auto_after_id = None
        if hasattr(self, "_btn_wc_auto"):
            self._btn_wc_auto.configure(text="🤖 开启自动处理", fg_color="#8e44ad")
        self._set_action_buttons_enabled(True)

    def _schedule_wechat_auto_poll(self):
        if self._wechat_auto_mode:
            if self._wechat_auto_after_id:
                try:
                    self.after_cancel(self._wechat_auto_after_id)
                except Exception:
                    pass
                self._wechat_auto_after_id = None
            self._wechat_auto_after_id = self.after(2000, self._poll_wechat_once)

    def _poll_wechat_once(self):
        self._wechat_auto_after_id = None
        if not self._wechat_auto_mode or not self._wechat_bot:
            return
        try:
            _trace_logger.info("STEP 1 auto_poll_entry")
            unread_messages = self._wechat_bot.scan_unread_messages()
            if unread_messages:
                _trace_logger.info("STEP 2 auto_poll_unread_count=%d", len(unread_messages))
                for msg in unread_messages:
                    self._process_wechat_message(msg, auto_send=True)
            else:
                # 没有未读时仍保留当前窗口轮询，兼容“消息刚到但红点尚未稳定”的瞬间。
                msg = self._wechat_bot.read_last_message(only_new=True)
                if msg:
                    _trace_logger.info("STEP 2 auto_poll_current_message contact=%s content=%r", msg.nickname, msg.content)
                    self._process_wechat_message(msg, auto_send=True)
                else:
                    _trace_logger.info("STEP 2 STOP auto_poll_no_new_message")
        except Exception:
            logging.getLogger(__name__).exception("自动读取微信消息异常")
        finally:
            self._schedule_wechat_auto_poll()

    def _process_wechat_message(self, msg: ReceivedMessage, *, auto_send: bool):
        """把读取、解析、生成、发送收束为一条可复用链路。"""
        contact = msg.nickname
        _trace_logger.info("STEP 3 process_start auto_send=%s contact=%s content=%r", auto_send, contact, msg.content)
        message_signature = self._message_signature_for_reply(msg)
        if auto_send and self._has_replied_signature(contact, message_signature):
            _trace_logger.info("STEP 3 STOP duplicate_replied contact=%s", contact)
            return
        if msg.is_group:
            _trace_logger.info("STEP 3 STOP skipped_group contact=%s", contact)
            self._set_status(f"已跳过群聊消息：{contact}")
            self._record_auto_reply_audit(contact, msg.content, "", "skipped_group", "")
            return
        if self._is_blacklisted(contact):
            _trace_logger.info("STEP 3 STOP skipped_blacklist contact=%s", contact)
            self._set_status(f"已跳过黑名单客户：{contact}")
            self._record_auto_reply_audit(contact, msg.content, "", "skipped_blacklist", "")
            return
        if self._is_paused(contact):
            _trace_logger.info("STEP 3 STOP skipped_paused contact=%s", contact)
            self._set_status(f"已暂停 {contact} 的自动回复")
            self._record_auto_reply_audit(contact, msg.content, "", "skipped_paused", "")
            return
        if self._is_manual_takeover(contact):
            _trace_logger.info("STEP 3 STOP skipped_manual_takeover contact=%s", contact)
            self._set_status(f"{contact} 当前由人工接管，已跳过自动回复")
            self._record_auto_reply_audit(contact, msg.content, "", "skipped_manual_takeover", "")
            return
        parsed = self.parser.parse(msg.content)
        if parsed and not parsed.get("产品"):
            inferred_product = self._infer_known_product_from_text(msg.content)
            if inferred_product:
                parsed["产品"] = inferred_product
        _trace_logger.info("STEP 4 parsed product=%r parsed=%r", parsed.get("产品"), parsed)
        if not parsed.get("产品"):
            previous_parsed = self._last_parsed_by_contact.get(contact, {})
            if previous_parsed.get("产品"):
                merged = dict(previous_parsed)
                merged.update({k: v for k, v in parsed.items() if v not in ("", 0, False, None)})
                parsed = merged
        is_sample_request = self._is_sample_request(msg.content)
        result = {"success": False}
        if is_sample_request:
            reply_text = self.generator.generate_sample_reply(parsed)
        else:
            result = self.generator.generate_quote(parsed, contact, contact)
        if result.get("success"):
            reply_text = result["text"]
        elif not is_sample_request and parsed.get("产品"):
            reply_text = self.generator.generate_sample_reply(parsed)
        elif not is_sample_request:
            reply_text = (
                f"{contact}您好，已收到您的消息。"
                "为了尽快给您准确回复，麻烦补充一下需要的面料品类、门幅、克重、颜色和数量，"
                "我这边马上为您核算。"
            )
        reply_text = _clean_customer_reply(reply_text)
        _trace_logger.info("STEP 5 reply_ready success=%s reply=%r", result.get("success"), reply_text)

        self._last_wechat_contact = contact
        self._last_wechat_msg = msg
        self._last_parsed = parsed
        if parsed.get("产品"):
            self._last_parsed_by_contact[contact] = dict(parsed)
        if result.get("success"):
            self._last_quote = result

        self._entry_name.delete(0, "end")
        self._entry_name.insert(0, contact)
        self._entry_wechat.delete(0, "end")
        self._entry_wechat.insert(0, contact)
        self._text_input.delete("0.0", "end")
        self._text_input.insert("0.0", msg.content)

        self._wc_sender.configure(text=f"发送人：{contact}")
        preview = msg.content[:40] + "..." if len(msg.content) > 40 else msg.content
        self._wc_preview.configure(text=f"最新消息：{preview}")
        self._update_parse_display(parsed)
        self._auto_save_customer(contact, contact)
        self._show_output(reply_text)

        if auto_send and reply_text and self._wechat_bot:
            # 自动模式只处理“当前聊天”，直接回当前窗口比重新按昵称搜索更稳，
            # 也避免同名联系人把消息送错人。
            _trace_logger.info("STEP 6 before_send contact=%s reply=%r", contact, reply_text)
            ok, send_result = self.send_to_wechat(reply_text, "", retries=2)
            _trace_logger.info("STEP 6 after_send ok=%s result=%r", ok, send_result)
            if ok:
                self._store_replied_signature(contact, message_signature)
                self._save_wechat_interaction(contact, reply_text)
                self._record_auto_reply_audit(contact, msg.content, reply_text, "sent", "")
                self._refresh_wechat_message_view(contact, msg.content)
                self._set_status(f"🤖 已自动回复 {contact}")
            else:
                self._record_auto_reply_audit(contact, msg.content, reply_text, "failed", send_result)
                self._raise_failure_alert(contact, send_result)
                self._set_status(f"⚠️ 自动回复失败：{send_result}")
        elif auto_send:
            _trace_logger.info("STEP 6 STOP skipped_no_reply contact=%s", contact)
            self._record_auto_reply_audit(contact, msg.content, "", "skipped_no_reply", "")
            self._set_status(f"🤖 已识别 {contact} 的消息，但未生成可发送内容")
        elif result.get("success"):
            self._set_status(f"✅ 已读取 {contact} 的消息，并生成建议回复")
        elif parsed.get("产品"):
            self._set_status(f"已读取 {contact} 的消息（未生成报价）")
        else:
            self._set_status(f"已读取 {contact} 的消息")

    def _infer_known_product_from_text(self, text: str) -> str:
        """Recover a known fabric name when the general parser leaves product blank."""
        if not text:
            return ""
        for keyword in self.parser.PRODUCT_KEYWORDS:
            if keyword and keyword in text:
                return self.parser.PRODUCT_MAP.get(keyword, keyword)
        return ""

    def _is_sample_request(self, text: str) -> bool:
        text = str(text or "")
        return any(keyword in text for keyword in ("手感样", "A4", "a4", "剪样", "样布", "样品", "寄样"))

    def _message_signature_for_reply(self, msg: ReceivedMessage) -> str:
        signature = getattr(msg, "message_signature", "") or ""
        if signature:
            return signature
        from agents.wechat_bot import _message_signature
        return _message_signature(msg.nickname, msg.content)

    def _has_replied_signature(self, contact: str, signature: str) -> bool:
        if not contact or not signature:
            return False
        if self._last_replied_signature_by_contact.get(contact) == signature:
            return True
        try:
            from crm.database import Database
            db = Database()
            row = db.dict_fetchone(
                "SELECT last_replied_signature FROM auto_reply_state WHERE contact_name = ?",
                (contact,),
            )
            stored = str(row.get("last_replied_signature") or "") if row else ""
            if stored:
                self._last_replied_signature_by_contact[contact] = stored
            return stored == signature
        except Exception:
            return False

    def _store_replied_signature(self, contact: str, signature: str):
        if not contact or not signature:
            return
        self._last_replied_signature_by_contact[contact] = signature
        try:
            from crm.database import Database
            db = Database()
            db.execute(
                """INSERT INTO auto_reply_state (contact_name, last_replied_signature)
                   VALUES (?, ?)
                   ON CONFLICT(contact_name) DO UPDATE SET
                       last_replied_signature = excluded.last_replied_signature,
                       updated_at = CURRENT_TIMESTAMP""",
                (contact, signature),
            )
            db.commit()
        except Exception:
            logging.getLogger(__name__).exception("保存自动回复签名失败")

    def _set_action_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for attr in ("_btn_quote", "_btn_sample", "_btn_payment", "_btn_shipping"):
            btn = getattr(self, attr, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def send_to_wechat(self, text: str, contact: str = "", *, retries: int = 2) -> tuple[bool, str]:
        """Single send entrypoint used by both manual and automatic reply flows."""
        text = _clean_customer_reply(text)
        _trace_logger.info("STEP 6 send_to_wechat_enter contact=%s retries=%s text=%r", contact, retries, text)
        if not self._wechat_bot or not self._wechat_bot.is_running:
            _trace_logger.info("STEP 6 STOP wechat_bot_not_running")
            return False, "微信助手未启动"
        if not text:
            _trace_logger.info("STEP 6 STOP empty_clean_reply")
            return False, "reply text empty after log filtering"
        return self._wechat_bot.send(text, contact, retries=retries)

    def _refresh_wechat_message_view(self, contact: str, content: str):
        """Keep sender/latest-message labels in sync after an automatic send completes."""
        self._last_wechat_contact = contact
        self._wc_sender.configure(text=f"发送人：{contact}")
        preview = content[:40] + "..." if len(content) > 40 else content
        self._wc_preview.configure(text=f"最新消息：{preview}")

    def _auto_save_customer(self, name: str, wxid: str):
        """自动保存客户信息到数据库"""
        if not wxid:
            return
        try:
            from crm.database import Database
            db = Database()
            existing = db.dict_fetchone(
                "SELECT id FROM customers WHERE wechat_id = ?", (wxid,)
            )
            if not existing:
                db.execute(
                    """INSERT INTO customers (name, wechat_id, source, status, score, category)
                       VALUES (?, ?, 'wechat', 'new_lead', 0, 'B')""",
                    (name, wxid),
                )
                db.commit()
                logger = logging.getLogger(__name__)
                logger.info("自动保存客户: %s (%s)", name, wxid)
        except Exception as e:
            pass  # 静默处理，不阻塞用户操作

    def _is_blacklisted(self, contact: str) -> bool:
        from crm.database import Database
        db = Database()
        row = db.dict_fetchone(
            "SELECT status FROM customers WHERE name = ? OR wechat_id = ? LIMIT 1",
            (contact, contact),
        )
        return bool(row and row.get("status") == "blacklist")

    def _whitelist_count(self) -> int:
        from crm.database import Database
        db = Database()
        row = db.dict_fetchone("SELECT COUNT(*) AS c FROM auto_reply_whitelist")
        return int(row["c"]) if row else 0

    def _is_auto_reply_allowed(self, contact: str) -> bool:
        from crm.database import Database
        db = Database()
        total = self._whitelist_count()
        if total == 0:
            return True
        row = db.dict_fetchone(
            "SELECT id FROM auto_reply_whitelist WHERE contact_name = ?",
            (contact,),
        )
        return bool(row)

    def _on_add_whitelist(self):
        contact = self._entry_name.get().strip() or self._last_wechat_contact
        if not contact:
            self._set_status("请先读取或填写客户名称")
            return
        from crm.database import Database
        db = Database()
        db.execute(
            "INSERT OR IGNORE INTO auto_reply_whitelist (contact_name) VALUES (?)",
            (contact,),
        )
        db.commit()
        self._set_status(f"已加入自动回复白名单：{contact}")

    def _on_remove_whitelist(self):
        contact = self._entry_name.get().strip() or self._last_wechat_contact
        if not contact:
            self._set_status("请先读取或填写客户名称")
            return
        from crm.database import Database
        db = Database()
        db.execute("DELETE FROM auto_reply_whitelist WHERE contact_name = ?", (contact,))
        db.commit()
        self._set_status(f"已移出自动回复白名单：{contact}")

    def _on_blacklist_contact(self):
        contact = self._entry_name.get().strip() or self._last_wechat_contact
        if not contact:
            self._set_status("请先读取或填写客户名称")
            return
        from crm.database import Database
        db = Database()
        existing = db.dict_fetchone(
            "SELECT id FROM customers WHERE name = ? OR wechat_id = ? LIMIT 1",
            (contact, contact),
        )
        if existing:
            db.execute(
                "UPDATE customers SET status = 'blacklist', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing["id"],),
            )
        else:
            db.execute(
                "INSERT INTO customers (name, wechat_id, source, status) VALUES (?, ?, 'wechat', 'blacklist')",
                (contact, contact),
            )
        db.execute("DELETE FROM auto_reply_whitelist WHERE contact_name = ?", (contact,))
        db.commit()
        self._set_status(f"已加入黑名单并停止自动回复：{contact}")

    def _get_contact_control(self, contact: str) -> dict:
        from crm.database import Database
        db = Database()
        return db.dict_fetchone(
            "SELECT paused, manual_takeover FROM auto_reply_controls WHERE contact_name = ?",
            (contact,),
        ) or {}

    def _is_paused(self, contact: str) -> bool:
        return bool(self._get_contact_control(contact).get("paused"))

    def _is_manual_takeover(self, contact: str) -> bool:
        return bool(self._get_contact_control(contact).get("manual_takeover"))

    def _set_contact_control(self, contact: str, *, paused: Optional[bool] = None,
                             manual_takeover: Optional[bool] = None):
        from crm.database import Database
        db = Database()
        current = self._get_contact_control(contact)
        next_paused = int(paused if paused is not None else bool(current.get("paused")))
        next_takeover = int(manual_takeover if manual_takeover is not None else bool(current.get("manual_takeover")))
        db.execute(
            """INSERT INTO auto_reply_controls (contact_name, paused, manual_takeover)
               VALUES (?, ?, ?)
               ON CONFLICT(contact_name) DO UPDATE SET
                   paused = excluded.paused,
                   manual_takeover = excluded.manual_takeover,
                   updated_at = CURRENT_TIMESTAMP""",
            (contact, next_paused, next_takeover),
        )
        db.commit()

    def _current_contact_or_warn(self) -> str:
        contact = self._entry_name.get().strip() or self._last_wechat_contact
        if not contact:
            self._set_status("请先读取或填写客户名称")
        return contact

    def _on_pause_contact(self):
        contact = self._current_contact_or_warn()
        if contact:
            self._set_contact_control(contact, paused=True)
            self._set_status(f"已暂停 {contact} 的自动回复")

    def _on_resume_contact(self):
        contact = self._current_contact_or_warn()
        if contact:
            self._set_contact_control(contact, paused=False, manual_takeover=False)
            self._set_status(f"已恢复 {contact} 的自动回复")

    def _on_takeover_contact(self):
        contact = self._current_contact_or_warn()
        if contact:
            self._set_contact_control(contact, manual_takeover=True)
            self._set_status(f"{contact} 已切换为人工接管")

    def _on_release_contact(self):
        contact = self._current_contact_or_warn()
        if contact:
            self._set_contact_control(contact, manual_takeover=False)
            self._set_status(f"{contact} 已释放人工接管")

    def _record_auto_reply_audit(self, contact: str, incoming: str, reply: str,
                                 status: str, error: str):
        from crm.database import Database
        db = Database()
        db.execute(
            """INSERT INTO auto_reply_audit
               (contact_name, incoming_message, reply_text, status, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (contact, incoming, reply, status, error),
        )
        db.commit()

    def _raise_failure_alert(self, contact: str, error: str):
        alert_text = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {contact}: {error}\n"
        alert_path = Path(os.environ.get("FABRIC_DB_PATH", "data/fabric_sales.db")).parent / "auto_reply_alerts.log"
        with open(alert_path, "a", encoding="utf-8") as f:
            f.write(alert_text)
        logging.getLogger(__name__).error("自动回复失败告警 %s: %s", contact, error)

    def _schedule_daily_summary_check(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_daily_summary_date != today:
            self._write_daily_summary(today)
            self._last_daily_summary_date = today
        self.after(60 * 60 * 1000, self._schedule_daily_summary_check)

    def _daily_summary_text(self, day: str) -> str:
        from crm.database import Database
        db = Database()
        rows = db.dict_fetchall(
            """SELECT status, COUNT(*) AS c
               FROM auto_reply_audit
               WHERE DATE(created_at) = ?
               GROUP BY status""",
            (day,),
        )
        counts = {row["status"]: row["c"] for row in rows}
        sent = counts.get("sent", 0)
        failed = counts.get("failed", 0)
        skipped = sum(v for k, v in counts.items() if k.startswith("skipped_"))
        active_contacts = db.dict_fetchone(
            """SELECT COUNT(DISTINCT contact_name) AS c
               FROM auto_reply_audit
               WHERE DATE(created_at) = ?""",
            (day,),
        )["c"]
        return (
            f"自动回复日报 {day}\n"
            f"{'-' * 24}\n"
            f"触达客户数：{active_contacts}\n"
            f"自动发送成功：{sent}\n"
            f"发送失败：{failed}\n"
            f"策略跳过：{skipped}\n"
        )

    def _write_daily_summary(self, day: str):
        summary_dir = Path(os.environ.get("FABRIC_DB_PATH", "data/fabric_sales.db")).parent / "daily_summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / f"{day}.txt"
        summary_path.write_text(self._daily_summary_text(day), encoding="utf-8")

    def _on_show_daily_summary(self):
        today = datetime.now().strftime("%Y-%m-%d")
        text = self._daily_summary_text(today)
        self._show_output(text)
        self._set_status("已生成今日自动回复汇总")

    def _save_wechat_interaction(self, contact: str, sent_text: str):
        """保存微信发送记录到 SQLite"""
        if not contact and not sent_text:
            return
        try:
            original_msg = self._text_input.get("0.0", "end").strip()
            from crm.database import Database
            db = Database()

            # 查找或创建客户
            existing = db.dict_fetchone(
                "SELECT id FROM customers WHERE name = ?", (contact,)
            )
            if existing:
                customer_id = existing["id"]
            else:
                db.execute(
                    "INSERT INTO customers (name, source, status) VALUES (?, 'wechat', 'new_lead')",
                    (contact,),
                )
                db.commit()
                customer_id = db.conn.lastrowid

            # 识别结果 JSON
            parsed_json = json.dumps(self._last_parsed, ensure_ascii=False) if self._last_parsed else "{}"
            product_name = self._last_parsed.get("产品", "") if self._last_parsed else ""

            # 写入 interactions 表
            db.execute(
                """INSERT INTO interactions
                   (customer_id, type, content, sentiment, intent, followup_action)
                   VALUES (?, 'wechat_reply', ?, ?, ?, ?)""",
                (customer_id, original_msg, parsed_json, product_name, sent_text),
            )
            db.commit()
            logger = logging.getLogger(__name__)
            logger.info("已保存微信交互记录: %s", contact)
        except Exception:
            pass  # 不阻塞用户操作

    def _build_output_frame(self):
        """输出结果区"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=6, column=0, padx=self.PAD, pady=(self.PAD, self.PAD), sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="输出结果", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )

        self._btn_copy = ctk.CTkButton(
            header, text="📋 复制回复", width=110, height=28,
            fg_color="#2c3e50", command=self._on_copy,
        )
        self._btn_copy.grid(row=0, column=1, padx=4)

        self._btn_clear = ctk.CTkButton(
            header, text="清空", width=60, height=28,
            fg_color="#7f8c8d", command=self._on_clear_output,
        )
        self._btn_clear.grid(row=0, column=2, padx=4)

        self._text_output = ctk.CTkTextbox(frame, wrap="word", state="normal")
        self._text_output.grid(row=1, column=0, sticky="nsew", padx=6, pady=(4, 6))

    def _build_status_bar(self):
        """底部状态栏"""
        self._status_bar = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=11),
            fg_color="#e8e8e8",
        )
        self._status_bar.grid(row=7, column=0, sticky="ew", padx=0, pady=0)

    # ── 事件处理 ──

    def _on_parse(self):
        """解析消息"""
        text = self._text_input.get("0.0", "end").strip()
        if not text or text == "例如：牛奶丝 150cm 180g 白色 单磨 500米 多少钱？":
            self._set_status("⚠️ 请输入客户消息")
            return

        parsed = self.parser.parse(text)
        if not parsed.get("产品"):
            self._set_status("⚠️ 未识别出面料产品，请确认消息包含面料名称")
            self._update_parse_display(parsed)
            return

        self._last_parsed = parsed
        self._update_parse_display(parsed)
        self._set_status(f"✅ 已识别：{parsed['产品']} | 数量：{parsed['数量']}{parsed['数量单位']}")

    def _update_parse_display(self, parsed: dict):
        """更新解析结果显示"""
        self._lbl_product.configure(
            text=f"产品：{parsed.get('产品', '—') or '—'}"
        )
        self._lbl_spec.configure(
            text=f"规格：{parsed.get('规格', '—') or '—'}"
        )
        self._lbl_color.configure(
            text=f"颜色：{parsed.get('颜色', '—') or '—'}"
        )
        self._lbl_process.configure(
            text=f"工艺：{parsed.get('工艺', '—') or '—'}"
        )
        self._lbl_qty.configure(
            text=f"数量：{parsed.get('数量', 0) or '—'} {parsed.get('数量单位', '米') if parsed.get('数量') else ''}"
        )
        urgent = parsed.get("是否加急", False)
        urgent_text = "🔥 加急" if urgent else "否"
        urgent_color = self.WARN if urgent else None
        if urgent_color is not None:
            self._lbl_urgent.configure(
                text=f"加急：{urgent_text}",
                text_color=urgent_color,
            )
        else:
            self._lbl_urgent.configure(
                text=f"加急：{urgent_text}",
            )

    def _on_quote(self):
        """生成报价"""
        if self._wechat_auto_mode:
            self._set_status("自动处理开启时已禁用手动报价")
            return
        if not self._last_parsed:
            self._set_status("⚠️ 请先点击「解析消息」")
            return

        name = self._entry_name.get().strip()
        wechat = self._entry_wechat.get().strip()

        result = self.generator.generate_quote(self._last_parsed, name, wechat)
        self._show_output(result["text"])
        if result.get("success"):
            self._last_quote = result
            tag = "预览" if not result.get("customer_id") else "已保存"
            self._set_status(f"✅ 报价已生成（{tag}）")
        else:
            self._set_status(f"❌ {result['text']}")

    def _on_sample(self):
        """生成样品回复"""
        if self._wechat_auto_mode:
            self._set_status("自动处理开启时已禁用手动样品回复")
            return
        if not self._last_parsed:
            self._set_status("⚠️ 请先点击「解析消息」")
            return

        text = self.generator.generate_sample_reply(self._last_parsed)
        self._show_output(text)
        self._set_status("✅ 样品回复已生成")

    def _on_payment(self):
        """生成催付款话术"""
        if self._wechat_auto_mode:
            self._set_status("自动处理开启时已禁用手动催付款")
            return
        if not self._last_parsed:
            self._set_status("⚠️ 请先点击「解析消息」")
            return

        text = self.generator.generate_payment_reminder(self._last_parsed)
        self._show_output(text)
        self._set_status("✅ 催付款话术已生成")

    def _on_shipping(self):
        """生成发货确认话术"""
        if self._wechat_auto_mode:
            self._set_status("自动处理开启时已禁用手动发货确认")
            return
        if not self._last_parsed:
            self._set_status("⚠️ 请先点击「解析消息」")
            return

        text = self.generator.generate_shipping_confirmation(self._last_parsed)
        self._show_output(text)
        self._set_status("✅ 发货确认话术已生成")

    def _on_copy(self):
        """复制输出到剪贴板"""
        content = self._text_output.get("0.0", "end").strip()
        if not content:
            self._set_status("⚠️ 没有内容可复制")
            return

        self.clipboard_clear()
        self.clipboard_append(content)
        self._set_status("✅ 已复制到剪贴板")

    def _on_clear_output(self):
        """清空输出"""
        self._text_output.delete("0.0", "end")

    # ── 辅助方法 ──

    def _show_output(self, text: str):
        text = _clean_customer_reply(text)
        self._text_output.delete("0.0", "end")
        self._text_output.insert("0.0", text)
        # 有输出内容时自动启用「发送到微信」
        if self._wechat_bot and self._wechat_bot.is_running:
            self._btn_wc_send.configure(state="normal")

    def _set_status(self, msg: str):
        self._status_bar.configure(text=f"  {msg}")


_FORBIDDEN_REPLY_LOG_MARKERS = (
    "Problem:",
    "INFO:",
    "[INFO]",
    "DEBUG:",
    "[DEBUG]",
    "WARNING:",
    "[WARNING]",
    "ERROR:",
    "[ERROR]",
    "Traceback",
    "agents.base_agent",
    "[agent]",
)


def _clean_customer_reply(text: object) -> str:
    if text is None:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        lower_line = line.lower()
        if any(marker.lower() in lower_line for marker in _FORBIDDEN_REPLY_LOG_MARKERS):
            continue
        cleaned_lines.append(raw_line.rstrip())
    cleaned = "\n".join(cleaned_lines).strip()
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned


def _message_dedupe_key(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ══════════════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════════════

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
