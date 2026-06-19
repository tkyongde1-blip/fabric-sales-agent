"""微信助手 - 基于 PyAutoGUI 模拟键盘操作（兼容任意微信版本）"""
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from hashlib import sha1
from typing import Callable, Optional

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("auto_reply_trace")

# ════════════════════════════════════════════════════════════
# 微信安装路径检测
# ════════════════════════════════════════════════════════════

WECHAT_SEARCH_PATHS = [
    "C:\\Program Files\\Tencent\\WeChat",
    "C:\\Program Files (x86)\\Tencent\\WeChat",
    "E:\\Tencent\\WeChat",
    os.path.expandvars(r"%ProgramFiles%\Tencent\WeChat"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\WeChat"),
    "D:\\Tencent\\WeChat",
    "C:\\Tencent\\WeChat",
]


def find_wechat() -> Optional[str]:
    for base in WECHAT_SEARCH_PATHS:
        exe = os.path.join(base, "WeChat.exe")
        if os.path.isfile(exe):
            return base
    return None


# ════════════════════════════════════════════════════════════
# 数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class ReceivedMessage:
    id: str
    sender: str
    content: str
    is_group: bool = False
    roomid: str = ""
    ts: int = 0
    nickname: str = ""
    is_from_customer: bool = True
    message_signature: str = ""


# ════════════════════════════════════════════════════════════
# 微信机器人（纯 PyAutoGUI）
# ════════════════════════════════════════════════════════════


class WeChatBot:
    """
    纯 PyAutoGUI 微信机器人。

    读取：激活微信 → 全选复制 → 解析联系人名称 + 最后一条客户消息
    发送：复制文本 → 激活微信 → Ctrl+F 搜索联系人 → 粘贴 → Enter
    """

    MAX_BUFFER = 50

    def __init__(self, wechat_path: Optional[str] = None):
        self._running = False
        self._lock = threading.Lock()
        self._msg_buffer: deque[ReceivedMessage] = deque(maxlen=self.MAX_BUFFER)
        self._on_message: Optional[Callable[[ReceivedMessage], None]] = None
        self._last_contact: str = ""
        self.last_message_signature: str = ""
        self._last_signature_by_contact: dict[str, str] = {}

    # ── 属性 ──

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_message(self) -> Optional[ReceivedMessage]:
        with self._lock:
            return self._msg_buffer[-1] if self._msg_buffer else None

    @property
    def last_contact(self) -> str:
        return self._last_contact

    # ── 生命周期 ──

    def start(self) -> tuple[bool, str]:
        if self._running:
            return False, "已在运行中"
        if self._find_window() is None:
            return False, "未找到微信窗口，请将微信聊天窗口置于前台。"
        self._running = True
        return True, "微信窗口已就绪"

    def stop(self):
        self._running = False
        logger.info("微信助手已停止")

    def set_callback(self, cb: Callable[[ReceivedMessage], None]):
        self._on_message = cb

    # ── 读取 ──

    def read_last_message(self, *, only_new: bool = False) -> Optional[ReceivedMessage]:
        """Read the visible chat area, preferring OCR and falling back to clipboard extraction."""
        trace_logger.info("STEP 1 read_current_message_enter only_new=%s", only_new)
        win = self._find_window()
        if win is None:
            trace_logger.info("STEP 1 STOP wechat_window_not_found")
            return None

        try:
            win.restore()
            time.sleep(0.25)
            win.activate()
        except Exception as e:
            logger.warning("Failed to activate WeChat before OCR: %s", e)
        time.sleep(0.35)

        bbox = win.box
        header_region = (
            int(bbox.left + bbox.width * 0.30),
            int(bbox.top + 45),
            int(bbox.width * 0.68),
            int(max(bbox.height * 0.09, 48)),
        )
        message_region = (
            int(bbox.left + bbox.width * 0.30),
            int(bbox.top + bbox.height * 0.13),
            int(bbox.width * 0.68),
            int(bbox.height * 0.67),
        )

        header_lines = self._ocr_region(header_region)
        message_lines = self._ocr_region(message_region)
        contact_name = self._extract_contact_from_header(header_lines) or self._last_contact or "????"
        raw_text = self._extract_last_visible_text(message_lines)
        customer_msg, message_marker = self._read_last_customer_message_via_uia(message_region)
        if not customer_msg:
            customer_msg, message_marker = self._extract_last_customer_message(message_lines, message_region[2])
        if not customer_msg:
            clipboard_contact, clipboard_msg, clipboard_is_group, clipboard_raw_text, clipboard_marker = self._read_message_via_clipboard(message_region)
            raw_text = clipboard_raw_text or raw_text
            if clipboard_msg:
                contact_name = clipboard_contact or contact_name
                customer_msg = clipboard_msg
                message_marker = clipboard_marker
                is_group = clipboard_is_group
            elif raw_text:
                # We have readable text, just not enough structure to prove whether it is a customer bubble.
                # Surface the original text to the operator instead of treating it as a total read failure.
                customer_msg = raw_text
                message_marker = clipboard_marker or _ocr_lines_position_marker(message_lines)
                is_group = clipboard_is_group or self._looks_like_group_chat(contact_name, header_lines)
        else:
            is_group = self._looks_like_group_chat(contact_name, header_lines)

        if not customer_msg:
            logger.info("OCR did not find a left-side customer message in the visible chat area")
            trace_logger.info("STEP 1 STOP no_readable_text")
            return None

        self._last_contact = contact_name
        signature = _message_signature(contact_name, customer_msg, message_marker)
        previous_signature = self._last_signature_by_contact.get(contact_name)
        if only_new and signature == previous_signature:
            trace_logger.info("STEP 1 STOP duplicate_message contact=%s", contact_name)
            return None
        self.last_message_signature = signature
        self._last_signature_by_contact[contact_name] = signature

        msg = ReceivedMessage(
            id=str(int(time.time() * 1000)),
            sender="wechat",
            content=customer_msg,
            is_group=is_group,
            ts=int(time.time()),
            nickname=contact_name,
            is_from_customer=True,
            message_signature=signature,
        )
        with self._lock:
            self._msg_buffer.append(msg)
        if self._on_message:
            try:
                self._on_message(msg)
            except Exception:
                logger.exception("WeChat callback failed")
        trace_logger.info("STEP 1 read_current_message_success contact=%s content=%r", contact_name, customer_msg)
        return msg

    def _read_message_via_clipboard(self, message_region: tuple[int, int, int, int]) -> tuple[str, str, bool, str, str]:
        """Fallback for WeChat layouts that Windows OCR cannot read reliably."""
        import pyautogui
        import pyperclip

        previous_clipboard = ""
        try:
            previous_clipboard = pyperclip.paste()
        except Exception:
            pass

        try:
            left, top, width, height = message_region
            pyautogui.click(left + width // 2, top + height // 2)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.2)
            copied = pyperclip.paste()
            contact, message, is_group, marker = _normalize_chat_parse_result(parse_chat_content(copied))
            return contact, message, is_group, _extract_last_text_from_copied_chat(copied), marker
        except Exception:
            logger.exception("Clipboard fallback read failed")
            return "", "", False, "", ""
        finally:
            try:
                pyperclip.copy(previous_clipboard)
            except Exception:
                pass

    def _read_last_customer_message_via_uia(self, message_region: tuple[int, int, int, int]) -> tuple[str, str]:
        """Read the latest visible message directly from WeChat's UIA Messages list."""
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            region_left, region_top, region_width, region_height = message_region
            region_right = region_left + region_width
            region_bottom = region_top + region_height
            windows = [
                window for window in desktop.windows()
                if _rects_overlap(window.rectangle(), region_left, region_top, region_right, region_bottom)
            ]
            if not windows:
                return "", ""
            win = max(windows, key=lambda window: _rect_overlap_area(window.rectangle(), region_left, region_top, region_right, region_bottom))
            messages = [
                control for control in win.descendants()
                if control.friendly_class_name() == "ListBox"
                and _rects_overlap(control.rectangle(), region_left, region_top, region_right, region_bottom)
                and (control.window_text() == "Messages" or _rect_overlap_area(control.rectangle(), region_left, region_top, region_right, region_bottom) > 10000)
            ]
            if not messages:
                return "", ""

            message_list = max(
                messages,
                key=lambda control: _rect_overlap_area(control.rectangle(), region_left, region_top, region_right, region_bottom),
            )
            items = []
            for item_index, item in enumerate(message_list.children()):
                rect = item.rectangle()
                if not _rects_overlap(rect, region_left, region_top, region_right, region_bottom):
                    continue
                texts = _collect_uia_texts(item)
                text = "\n".join(texts).strip()
                center_x = _uia_item_content_center_x(item, (rect.left + rect.right) / 2)
                if center_x >= region_left + region_width * 0.62:
                    continue
                looks_like_image = _uia_item_looks_like_image(item)
                if not text and not looks_like_image:
                    continue
                clipped_rect = (
                    max(int(rect.left), region_left),
                    max(int(rect.top), region_top),
                    min(int(rect.right), region_right),
                    min(int(rect.bottom), region_bottom),
                )
                position_marker = f"uia:{int(rect.top)}:{item_index}"
                items.append((rect.top, text, clipped_rect, looks_like_image, position_marker))

            for _, text, clipped_rect, looks_like_image, position_marker in reversed(sorted(items, key=lambda row: row[0])):
                if text and not _is_image_placeholder_text(text):
                    if _is_timestamp(text) or _is_ocr_noise_line(text):
                        continue
                    return _normalize_message_spacing(text), position_marker

                if not looks_like_image and not _is_image_placeholder_text(text):
                    continue

                image_text = self._ocr_message_image_rect(clipped_rect)
                if _is_effective_message_text(image_text):
                    trace_logger.info("STEP 1 image_message_ocr_success content=%r", image_text)
                    return _normalize_message_spacing(image_text), position_marker
                trace_logger.info("STEP 1 image_message_ocr_empty_fallback_previous")

            for _, text, _, _, position_marker in reversed(sorted(items, key=lambda row: row[0])):
                if not text or _is_image_placeholder_text(text):
                    continue
                if _is_timestamp(text) or _is_ocr_noise_line(text):
                    continue
                return _normalize_message_spacing(text), position_marker
        except Exception:
            logger.exception("UIA message read failed")
        return "", ""

    def _ocr_message_image_rect(self, rect: tuple[int, int, int, int]) -> str:
        left, top, right, bottom = rect
        width = max(0, right - left)
        height = max(0, bottom - top)
        if width < 24 or height < 24:
            return ""
        lines = self._ocr_region((left, top, width, height))
        return _join_ocr_message_lines(lines)

    def _ocr_region(self, region: tuple[int, int, int, int]) -> list[dict[str, object]]:
        """Run built-in Windows OCR against a screenshot region and return line boxes."""
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        from PIL import ImageGrab

        left, top, width, height = region
        screenshot = ImageGrab.grab(
            bbox=(left, top, left + width, top + height),
            all_screens=True,
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                temp_path = Path(fh.name)
            screenshot.save(temp_path)
            escaped = str(temp_path).replace("'", "''")
            script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]
function Await($task, $resultType) {{
    $netTask = $asTaskGeneric.MakeGenericMethod($resultType).Invoke($null, @($task))
    $netTask.Wait()
    return $netTask.Result
}}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped}')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @($result.Lines | ForEach-Object {{
    [PSCustomObject]@{{
        text = $_.Text
        x = $_.Words[0].BoundingRect.X
        y = $_.Words[0].BoundingRect.Y
        right = ($_.Words[-1].BoundingRect.X + $_.Words[-1].BoundingRect.Width)
        bottom = ($_.Words[-1].BoundingRect.Y + $_.Words[-1].BoundingRect.Height)
    }}
}})
$lines | ConvertTo-Json -Compress
"""
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                input=script,
                text=True,
                capture_output=True,
                timeout=20,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                logger.warning("Windows OCR failed: %s", proc.stderr.strip())
                return []
            payload = proc.stdout.strip()
            if not payload:
                return []
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            logger.exception("OCR region read failed")
            return []
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _extract_contact_from_header(self, lines: list[dict[str, object]]) -> str:
        candidates = [str(line.get("text", "")).strip() for line in lines if str(line.get("text", "")).strip()]
        for text in candidates:
            if text not in {"??", "????"} and len(text) <= 40:
                return text
        return ""

    def _extract_last_customer_message(self, lines: list[dict[str, object]], width: int) -> tuple[str, str]:
        if not lines:
            return "", ""
        ordered = sorted(lines, key=lambda line: (float(line.get("y", 0)), float(line.get("x", 0))))
        customer_lines: list[dict[str, object]] = []
        for line in ordered:
            text = str(line.get("text", "")).strip()
            if not text:
                continue
            x = float(line.get("x", 0))
            right = float(line.get("right", x))
            center_x = (x + right) / 2
            if center_x < width * 0.62 and not _is_ocr_noise_line(text):
                customer_lines.append(line)
        if not customer_lines:
            return "", ""

        last = customer_lines[-1]
        last_y = float(last.get("y", 0))
        same_bubble = [last]
        for line in reversed(customer_lines[:-1]):
            y = float(line.get("y", 0))
            if last_y - y <= 42:
                same_bubble.append(line)
                last_y = y
            else:
                break
        same_bubble.reverse()
        parts = [str(line.get("text", "")).strip() for line in same_bubble if str(line.get("text", "")).strip()]
        marker = _ocr_lines_position_marker(same_bubble)
        return "\n".join(parts).strip(), marker

    def _extract_last_visible_text(self, lines: list[dict[str, object]]) -> str:
        """Return the last readable OCR text even when sender-side detection fails."""
        if not lines:
            return ""
        ordered = sorted(lines, key=lambda line: (float(line.get("y", 0)), float(line.get("x", 0))))
        texts = [
            str(line.get("text", "")).strip()
            for line in ordered
            if str(line.get("text", "")).strip() and not _is_ocr_noise_line(str(line.get("text", "")).strip())
        ]
        return texts[-1] if texts else ""

    def _looks_like_group_chat(self, contact_name: str, header_lines: list[dict[str, object]]) -> bool:
        header_text = " ".join(str(line.get("text", "")) for line in header_lines)
        return bool(re.search(r"\(\d+\)|\uff08\d+\uff09", contact_name or header_text))

    def scan_unread_messages(self) -> list[ReceivedMessage]:
        """
        扫描左侧会话列表中的红色未读徽标，逐个打开并读取最后一条客户消息。

        这里故意不依赖 OCR：微信不同版本的 UI 文本暴露不稳定，但未读红点的视觉特征相对稳定。
        """
        if not self._running:
            return []
        win = self._find_window()
        if win is None:
            return []

        original_contact = self._last_contact
        unread_rows = self._detect_unread_rows(win)
        if not unread_rows:
            return []

        import pyautogui

        messages: list[ReceivedMessage] = []
        for row_y in unread_rows:
            try:
                bbox = win.box
                click_x = bbox.left + bbox.width * 0.16
                pyautogui.click(click_x, row_y)
                time.sleep(0.45)
                msg = self.read_last_message(only_new=True)
                if msg and self._is_safe_scan_result(msg):
                    messages.append(msg)
            except Exception:
                logger.exception("扫描未读会话失败")
        if original_contact:
            self.open_chat(original_contact)
        return messages

    def _is_safe_scan_result(self, msg: ReceivedMessage) -> bool:
        """误点保护：只接受单聊、客户消息、且联系人名称稳定的扫描结果。"""
        if not msg.nickname or msg.nickname == "微信客户":
            return False
        if msg.is_group or not msg.is_from_customer:
            return False
        return True

    def _detect_unread_rows(self, win) -> list[int]:
        """通过截图中的红色未读徽标推断左侧会话行中心。"""
        import pyautogui

        bbox = win.box
        sidebar_left = int(bbox.left)
        sidebar_top = int(bbox.top + 56)
        sidebar_width = int(bbox.width * 0.30)
        sidebar_height = int(max(bbox.height - 56, 1))
        screenshot = pyautogui.screenshot(region=(sidebar_left, sidebar_top, sidebar_width, sidebar_height))
        pixels = screenshot.load()

        visited: set[tuple[int, int]] = set()
        candidates: list[tuple[int, int, int, int, int]] = []

        def is_red(x: int, y: int) -> bool:
            r, g, b = pixels[x, y][:3]
            return r >= 180 and g <= 110 and b <= 110 and (r - max(g, b)) >= 70

        # 未读徽标通常在左栏靠右侧；缩小搜索范围能避开头像和 logo 的红色噪点。
        x_start = int(sidebar_width * 0.55)
        x_end = max(x_start + 1, int(sidebar_width * 0.98))

        for y in range(sidebar_height):
            for x in range(x_start, x_end):
                if (x, y) in visited or not is_red(x, y):
                    continue
                stack = [(x, y)]
                visited.add((x, y))
                points: list[tuple[int, int]] = []
                while stack:
                    cx, cy = stack.pop()
                    points.append((cx, cy))
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if nx < x_start or nx >= x_end or ny < 0 or ny >= sidebar_height:
                            continue
                        if (nx, ny) in visited or not is_red(nx, ny):
                            continue
                        visited.add((nx, ny))
                        stack.append((nx, ny))

                min_x = min(px for px, _ in points)
                max_x = max(px for px, _ in points)
                min_y = min(py for _, py in points)
                max_y = max(py for _, py in points)
                width = max_x - min_x + 1
                height = max_y - min_y + 1
                area = len(points)
                if 5 <= width <= 40 and 5 <= height <= 40 and area >= 18:
                    candidates.append((min_x, min_y, max_x, max_y, area))

        rows: list[int] = []
        for _, min_y, _, max_y, _ in sorted(candidates, key=lambda item: item[1]):
            center_y = sidebar_top + (min_y + max_y) // 2
            if not rows or abs(center_y - rows[-1]) > 18:
                rows.append(center_y)
        return rows

    # ── 发送 ──

    def send(self, text: str, contact: str = "", *, retries: int = 2) -> tuple[bool, str]:
        """
        发送消息到微信。

        contact 非空时：Ctrl+F 搜索联系人 → Enter 打开 → Ctrl+V → Enter
        contact 为空时：直接发送到当前活动聊天
        """
        if not self._running:
            return False, "微信未连接"
        if not text.strip():
            return False, "消息内容为空"

        last_error = "发送失败"
        for attempt in range(retries + 1):
            ok, result = self._send_once(text, contact)
            if ok:
                return True, result
            last_error = result
            time.sleep(0.5 * (attempt + 1))
        return False, last_error

    def _send_once(self, text: str, contact: str = "") -> tuple[bool, str]:
        win = self._find_window()
        if win is None:
            _log_send_failure("focus failed")
            return False, "focus failed"
        try:
            win.restore()
            time.sleep(0.2)
            win.activate()
        except Exception:
            _log_send_failure("focus failed")
            return False, "focus failed"
        time.sleep(0.35)

        import pyautogui
        import pyperclip

        previous_clipboard = ""
        try:
            previous_clipboard = pyperclip.paste()
        except Exception:
            pass

        try:
            if contact:
                print(f"[微信发送] 搜索联系人: {contact}")
                pyautogui.hotkey("ctrl", "f")
                time.sleep(0.4)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                pyautogui.press("backspace")
                time.sleep(0.15)
                pyperclip.copy(contact)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.6)
                pyautogui.press("enter")
                time.sleep(0.5)

            if not _click_wechat_input_box(win):
                _log_send_failure("focus failed")
                return False, "focus failed"
            time.sleep(0.15)

            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.08)
            pyautogui.press("backspace")
            time.sleep(0.12)

            pyperclip.copy(text)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.35)

            pasted_text = _copy_focused_input_text()
            if not pasted_text.strip():
                _log_send_failure("input box still empty")
                _log_send_failure("send skipped")
                return False, "input box still empty"
            if not _clipboard_text_matches(text, pasted_text):
                _log_send_failure("paste failed")
                _log_send_failure("send skipped")
                return False, "paste failed"

            pyautogui.press("enter")
        finally:
            try:
                pyperclip.copy(previous_clipboard)
            except Exception:
                pass

        print(f"[微信发送] 已发送到 {contact or '当前窗口'}: {text[:40]}")
        return True, "已发送"

    def open_chat(self, contact: str) -> bool:
        """通过搜索恢复到指定聊天。"""
        if not contact or not self._activate_wechat():
            return False
        import pyautogui
        import pyperclip

        previous_clipboard = ""
        try:
            previous_clipboard = pyperclip.paste()
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.3)
            pyautogui.hotkey("ctrl", "a")
            pyperclip.copy(contact)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(0.4)
            return True
        finally:
            try:
                pyperclip.copy(previous_clipboard)
            except Exception:
                pass

    def get_messages(self, limit: int = 10) -> list[ReceivedMessage]:
        with self._lock:
            return list(self._msg_buffer)[-limit:]

    # ── 窗口查找 ──

    def _find_window(self):
        """
        查找微信窗口。

        1. 打印所有可见窗口标题
        2. 排除已知非微信窗口
        3. 按优先级匹配：Weixin → 微信 → WeChat
        """
        import pygetwindow as gw

        EXCLUDE_TITLES = [
            "纺织面料销售助手", "面料销售助手",
            "Claude", "Visual Studio Code", "Google Chrome",
            "Program Manager", "Settings", "Notepad",
        ]

        all_windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
        unique_titles = sorted(set(w.title for w in all_windows))

        print("=" * 60)
        print(f"[微信检测] 当前共有 {len(unique_titles)} 个可见窗口：")
        for i, t in enumerate(unique_titles, 1):
            print(f"  {i:2d}. {t}")
        print("=" * 60)

        for w in all_windows:
            if "Weixin" in w.title:
                _print_window_geometry(w)
                return w

        keywords = ["Weixin", "微信", "WeChat"]
        for kw in keywords:
            for w in all_windows:
                title = w.title
                if any(excl in title for excl in EXCLUDE_TITLES):
                    continue
                if kw in title:
                    print(f"[微信检测] ✓ 关键词「{kw}」匹配窗口: {title}")
                    _print_window_geometry(w)
                    return w

        print("[微信检测] ✗ 未找到微信窗口，请将微信聊天窗口置于前台。")
        return None

    def _activate_wechat(self) -> bool:
        win = self._find_window()
        if win is None:
            return False
        try:
            win.restore()
            time.sleep(0.2)
            win.activate()
        except Exception:
            pass
        time.sleep(0.4)
        return True


def _print_window_geometry(win):
    try:
        print(
            "[WeChat window] "
            f"title={win.title!r} left={win.left} top={win.top} "
            f"width={win.width} height={win.height}"
        )
    except Exception:
        logger.exception("Failed to print WeChat window geometry")


def _click_wechat_input_box(win) -> bool:
    try:
        import pyautogui

        bbox = win.box
        click_x = int(bbox.left + bbox.width * 0.65)
        click_y = int(bbox.top + bbox.height * 0.90)
        pyautogui.click(click_x, click_y)
        time.sleep(0.12)
        return True
    except Exception:
        logger.exception("focus failed")
        return False


def _copy_focused_input_text() -> str:
    import pyautogui
    import pyperclip

    try:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.08)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.12)
        return str(pyperclip.paste() or "")
    except Exception:
        logger.exception("paste verification failed")
        return ""


def _clipboard_text_matches(expected: str, actual: str) -> bool:
    expected_normalized = re.sub(r"\s+", " ", expected or "").strip()
    actual_normalized = re.sub(r"\s+", " ", actual or "").strip()
    if not expected_normalized or not actual_normalized:
        return False
    return expected_normalized == actual_normalized or expected_normalized in actual_normalized


def _log_send_failure(reason: str):
    print(f"[wechat send] {reason}")
    trace_logger.info("STEP 4 wechat_send_%s", reason.replace(" ", "_"))


# ════════════════════════════════════════════════════════════
# 聊天内容解析（独立静态函数）
# ════════════════════════════════════════════════════════════

_TIMESTAMP_RE = re.compile(r'\d{1,2}:\d{2}')  # 匹配 HH:MM / H:MM
_SELF_NAMES = {"我", "自己", "Me", "me"}


def _is_timestamp(line: str) -> bool:
    """
    判断一行是否为微信聊天时间戳。

    匹配以下格式：
      - 14:30
      - 2024/1/15 14:30
      - 2024-01-15 14:30:21
      - 上午 10:30 / 下午 3:45
      - 昨天 14:30
      - 星期一 14:30
      - 2024年1月15日 14:30
    """
    if not line:
        return False
    if _TIMESTAMP_RE.search(line):
        return True
    if re.search(r'上午|下午|昨天|前天|星期[一二三四五六日天]', line):
        return True
    if re.search(r'\d{4}\s*年', line):
        return True
    return False


def _is_ocr_noise_line(line: str) -> bool:
    """Filter labels that commonly leak into the OCR crop but are not chat content."""
    normalized = line.strip()
    if not normalized:
        return True
    if _is_timestamp(normalized):
        return True
    return normalized in {
        "发送",
        "表情",
        "文件",
        "聊天信息",
        "查看更多消息",
    }


def parse_chat_content(text: str) -> tuple[str, str, bool, str]:
    """
    解析微信聊天复制内容，返回 (联系人名称, 最后一条客户消息)。

    WeChat PC 版复制格式（空行分隔消息块）:
        [时间戳行]
        [发送人]
        [消息内容行...]

        [时间戳行]
        [发送人]
        [消息内容行...]

    规则:
      - 跳过所有时间戳行
      - 跳过发送人为"我"的消息块（自己发的）
      - 联系人名称 = 第一个非"我"的发送人
      - 最后一条客户消息 = 最后一个非"我"消息块的内容
    """
    if not isinstance(text, str) or not text.strip():
        return "", "", False, ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = _split_chat_blocks(normalized)

    contact_name = ""
    last_customer_msg = ""
    last_customer_marker = ""
    non_self_senders: set[str] = set()

    for block_index, block in enumerate(blocks):
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) < 2:
            # 少于 2 行不可能是完整消息块
            continue

        # 跳过开头的所有时间戳行，找到发送人
        sender_idx = 0
        while sender_idx < len(lines) and _is_timestamp(lines[sender_idx]):
            sender_idx += 1

        if sender_idx >= len(lines):
            # 全是时间戳，没有发送人
            continue

        sender = lines[sender_idx]
        content_lines = lines[sender_idx + 1:]
        content = '\n'.join(content_lines).strip()

        if not sender or not content:
            continue

        # 联系人名称 = 第一个非"我"的发送人
        if not contact_name and sender not in _SELF_NAMES:
            contact_name = sender

        # 最后一条客户消息 = 最后一个非"我"消息块
        if sender not in _SELF_NAMES:
            non_self_senders.add(sender)
            last_customer_msg = content
            timestamp_lines = [line for line in lines[:sender_idx] if _is_timestamp(line)]
            timestamp = timestamp_lines[-1] if timestamp_lines else ""
            last_customer_marker = f"clipboard:{timestamp or block_index}:{block_index}"

    is_group = len(non_self_senders) > 1
    return contact_name, last_customer_msg, is_group, last_customer_marker


def _normalize_chat_parse_result(result: object) -> tuple[str, str, bool, str]:
    """Keep clipboard parsing safe even if a future parser branch returns an older tuple shape."""
    if not isinstance(result, tuple):
        return "", "", False, ""
    if len(result) >= 4:
        contact, message, is_group, marker = result[:4]
        return str(contact or ""), str(message or ""), bool(is_group), str(marker or "")
    if len(result) >= 3:
        contact, message, is_group = result[:3]
        return str(contact or ""), str(message or ""), bool(is_group), ""
    if len(result) == 2:
        contact, message = result
        return str(contact or ""), str(message or ""), False, ""
    if len(result) == 1:
        return "", str(result[0] or ""), False, ""
    return "", "", False, ""


def _extract_last_text_from_copied_chat(text: object) -> str:
    """Best-effort last visible message text from a copied WeChat transcript."""
    if not isinstance(text, str) or not text.strip():
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = _split_chat_blocks(normalized)
    for block in reversed(blocks):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        meaningful = [line for line in lines if not _is_timestamp(line)]
        if not meaningful:
            continue
        if len(meaningful) >= 2:
            return "\n".join(meaningful[1:]).strip() or meaningful[-1]
        return meaningful[-1]
    return ""


def _split_chat_blocks(text: str) -> list[str]:
    """兼容“空行分块”和“仅靠时间戳分块”两类复制格式。"""
    blank_split = [b for b in re.split(r'\n[ \t]*\n+', text) if b.strip()]
    if len(blank_split) > 1:
        return blank_split

    lines = [line for line in text.split("\n") if line.strip()]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _is_timestamp(line) and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks]


def _message_signature(contact: str, content: str, marker: str = "") -> str:
    normalized = re.sub(r"\s+", " ", content).strip()
    return sha1(f"{contact}\n{normalized}\n{marker}".encode("utf-8")).hexdigest()


def _ocr_lines_position_marker(lines: list[dict[str, object]]) -> str:
    if not lines:
        return ""
    try:
        ordered = sorted(lines, key=lambda line: (float(line.get("y", 0)), float(line.get("x", 0))))
        first = ordered[0]
        last = ordered[-1]
        return (
            "ocr:"
            f"{int(float(first.get('y', 0)))}:"
            f"{int(float(last.get('y', 0)))}:"
            f"{int(float(last.get('x', 0)))}"
        )
    except Exception:
        return ""


def _normalize_message_spacing(text: str) -> str:
    """Keep customer text readable when WeChat UIA collapses adjacent units."""
    normalized = re.sub(r"克(?=\d)", "克 ", text)
    return re.sub(r"\s+", " ", normalized).strip()


def _collect_uia_texts(control) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()

    def add(text: object):
        normalized = str(text or "").strip()
        if not normalized or normalized in seen:
            return
        if _is_timestamp(normalized) or _is_ocr_noise_line(normalized):
            return
        seen.add(normalized)
        texts.append(normalized)

    add(control.window_text())
    try:
        descendants = control.descendants()
    except Exception:
        descendants = []
    for child in descendants:
        try:
            add(child.window_text())
        except Exception:
            continue
    return texts


def _uia_text_center_x(control, fallback: float) -> float:
    centers: list[float] = []
    try:
        descendants = control.descendants()
    except Exception:
        descendants = []
    for child in descendants:
        try:
            text = str(child.window_text() or "").strip()
            if not text or _is_timestamp(text) or _is_ocr_noise_line(text):
                continue
            rect = child.rectangle()
            if int(rect.right) > int(rect.left):
                centers.append((int(rect.left) + int(rect.right)) / 2)
        except Exception:
            continue
    return sum(centers) / len(centers) if centers else fallback


def _uia_item_content_center_x(control, fallback: float) -> float:
    centers: list[float] = []
    try:
        descendants = control.descendants()
    except Exception:
        descendants = []
    for child in descendants:
        try:
            text = str(child.window_text() or "").strip()
            if text and not _is_timestamp(text) and not _is_ocr_noise_line(text):
                rect = child.rectangle()
                if int(rect.right) > int(rect.left):
                    centers.append((int(rect.left) + int(rect.right)) / 2)
                continue
            if _control_looks_like_image(child):
                rect = child.rectangle()
                if int(rect.right) > int(rect.left):
                    centers.append((int(rect.left) + int(rect.right)) / 2)
        except Exception:
            continue
    if centers:
        return sum(centers) / len(centers)
    return _uia_text_center_x(control, fallback)


def _uia_item_looks_like_image(control) -> bool:
    try:
        if _control_looks_like_image(control):
            return True
        for child in control.descendants():
            if _control_looks_like_image(child):
                return True
    except Exception:
        return False
    return False


def _control_looks_like_image(control) -> bool:
    try:
        text = str(control.window_text() or "").strip()
        class_name = str(control.friendly_class_name() or "")
        control_type = str(control.element_info.control_type or "")
    except Exception:
        return False
    if _is_image_placeholder_text(text):
        return True
    return class_name.lower() == "image" or control_type.lower() == "image"


def _is_image_placeholder_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "")).strip().lower()
    if not normalized:
        return False
    return normalized in {
        "[image]",
        "image",
        "photo",
        "picture",
        "[photo]",
        "[picture]",
        "图片",
        "[图片]",
        "照片",
        "[照片]",
    }


def _join_ocr_message_lines(lines: list[dict[str, object]]) -> str:
    if not lines:
        return ""
    ordered = sorted(lines, key=lambda line: (float(line.get("y", 0)), float(line.get("x", 0))))
    parts = []
    for line in ordered:
        text = str(line.get("text", "")).strip()
        if not text or _is_ocr_noise_line(text) or _is_image_placeholder_text(text):
            continue
        parts.append(text)
    return _normalize_message_spacing("\n".join(parts))


def _is_effective_message_text(text: str) -> bool:
    normalized = _normalize_message_spacing(text)
    if not normalized or _is_image_placeholder_text(normalized):
        return False
    if len(normalized) < 2:
        return False
    return bool(re.search(r"[\w\u4e00-\u9fff]", normalized))


def _rects_overlap(rect, left: int, top: int, right: int, bottom: int) -> bool:
    return _rect_overlap_area(rect, left, top, right, bottom) > 0


def _rect_overlap_area(rect, left: int, top: int, right: int, bottom: int) -> int:
    try:
        overlap_width = max(0, min(int(rect.right), right) - max(int(rect.left), left))
        overlap_height = max(0, min(int(rect.bottom), bottom) - max(int(rect.top), top))
        return overlap_width * overlap_height
    except Exception:
        return 0
