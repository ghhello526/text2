"""字段提取模块：文本清洗 + 字段提取 + 数值归一化。"""

from __future__ import annotations

import re
from typing import Any, Union

from loguru import logger

from src.config_loader import get_config


def clean_text(raw: str) -> str:
    """清洗 OCR 原始文本：去噪、去乱码、繁简转换。

    Args:
        raw: OCR 原始输出文本

    Returns:
        清洗后的简体中文文本
    """
    if not raw or not raw.strip():
        return ""

    # 规范化换行
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    prev_empty = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            # 保留段落间单个空行，压缩连续空行
            if not prev_empty:
                lines.append("")
                prev_empty = True
            continue
        prev_empty = False

        # 去除纯标点符号行（整行不含 CJK 字符、字母数字或金融箭头符号）
        has_content = any(
            "\u4e00" <= c <= "\u9fff" or
            "\u3400" <= c <= "\u4dbf" or
            c.isalnum() or
            c in "↑↓▲▼+-%."
            for c in line
        )
        if not has_content:
            continue

        # 去除不可见控制字符（保留换行、箭头字符）
        line = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", line)
        lines.append(line)

    text = "\n".join(lines)

    # 繁简转换
    try:
        import zhconv
        text = zhconv.convert(text, "zh-cn")
    except ImportError:
        logger.warning("zhconv 未安装，跳過繁简转换")

    # 压缩连续空白
    text = re.sub(r" {2,}", " ", text)
    # 移除 CJK 字符之间以及 CJK 与数字之间的空格（Tesseract 常见 artifact）
    # CRITICAL: 用 [ 　\t] 而非 \s，避免误删换行符
    _cjk = r"\u4e00-\u9fff\u3400-\u4dbf"
    _sp = r"[ 　\t]+"
    text = re.sub(rf"([{_cjk}]){_sp}([{_cjk}])", r"\1\2", text)
    text = re.sub(rf"([{_cjk}]){_sp}(\d)", r"\1\2", text)
    text = re.sub(rf"(\d){_sp}([{_cjk}])", r"\1\2", text)
    # 移除数字/小数点之间的空格 (e.g., "3650. 5" -> "3650.5", "0. 52" -> "0.52")
    text = re.sub(r"\.{}(\d)".format(_sp), r".\1", text)
    # 移除 CJK 与冒号之间的空格 (e.g., "指数 :" -> "指数:")
    text = re.sub(rf"([{_cjk}])[ 　\t]*:", r"\1:", text)
    # 移除 CJK/数字 与标点之间的空格 (e.g., "平稳 ," -> "平稳,")
    _punct = r"，,。.;:：;；!！?？、"
    text = re.sub(rf"([{_cjk}]){_sp}([{_punct}])", r"\1\2", text)
    text = re.sub(rf"([{_punct}]){_sp}([{_cjk}])", r"\1\2", text)
    text = re.sub(rf"([{_punct}]){_sp}(\d)", r"\1\2", text)

    # ---- 金融数据专用清洗 ----
    # 将中文冒号统一为英文冒号（便于后续 key:value 解析）
    text = text.replace("：", ":")
    # 修复 OCR 常见的数字粘连中文问题 (e.g., "当前值3.28" -> "当前值:3.28")
    text = re.sub(
        rf"(当前值|分位点|危险值|中位数|机会值|最大值|平均值|最小值|标准差|[Zz]分数)"
        rf"([+-]?[\d,]+\.?\d*%?)",
        r"\1:\2", text
    )
    # 压缩连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def normalize_number(text: str) -> Union[float, str]:
    """将中文数值表达式转为标准 Python 数字。

    支持: 1.5万→15000, 3.2亿→320000000, 5%→0.05, -1.2万→-12000

    Args:
        text: 可能含中文单位的数值字符串

    Returns:
        归一化后的 float，或无法识别时返回原字符串
    """
    if not text or not isinstance(text, str):
        return text

    text = text.strip()
    if not text:
        return 0.0

    # 去除逗号
    text = text.replace(",", "").replace("，", "")

    # 检测负号
    sign = 1.0
    for prefix in ("-", "负"):
        if text.startswith(prefix):
            sign = -1.0
            text = text[len(prefix):]
            break

    # 复合单位 "万亿"
    if "万亿" in text:
        try:
            num_part = float(text.replace("万亿", "").strip())
            return sign * num_part * 1_0000_0000_0000
        except ValueError:
            return _orig(text, sign)

    # 单位映射
    unit_map = {
        "亿": 100_000_000,
        "万": 10_000,
        "千": 1_000,
        "%": 0.01,
        "％": 0.01,
    }

    for unit, multiplier in unit_map.items():
        if unit in text:
            try:
                num_part = float(text.replace(unit, "").strip())
                return sign * num_part * multiplier
            except ValueError:
                pass

    # 无单位，直接解析
    try:
        return sign * float(text)
    except ValueError:
        return _orig(text, sign)


def _orig(text: str, sign: float) -> str:
    """还原原始符号前缀。"""
    if sign < 0:
        return f"-{text}"
    return text


class Extractor:
    """字段提取器：加载配置规则，从 OCR 文本中结构抽取字段。

    支持三种提取策略（按优先级）：
      1. pattern — 正则匹配捕获组
      2. key_value — key:value 行式提取
      3. position — 按行号/首行/末行提取
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            config = get_config()["extraction"]

        self._fields = config.get("fields", [])
        self._numeric_normalize = config.get("numeric_normalize", True)

    def extract(self, raw_text: str) -> dict:
        """从原始 OCR 文本中提取结构化字段。

        Args:
            raw_text: OCR 引擎返回的原始文本

        Returns:
            {
                "date": "YYYY-MM-DD" | None,
                "body_text": "清洗后的全文",
                "fields": {field_name: value, ...}
            }
        """
        cleaned = clean_text(raw_text)

        result: dict[str, Any] = {
            "date": None,
            "body_text": cleaned,
            "fields": {},
        }

        if not cleaned:
            return result

        lines = cleaned.splitlines()

        for rule in self._fields:
            name = rule.get("name", "")
            if not name:
                continue

            value = None

            # ---- 策略 1：正则匹配 ----
            if "pattern" in rule:
                m = re.search(rule["pattern"], cleaned)
                if m:
                    value = m.group(1) if m.lastindex else m.group(0)

            # ---- 策略 2：key:value 行式提取 ----
            if value is None and "key_label" in rule:
                value = self._extract_key_value(cleaned, rule["key_label"])

            # ---- 策略 3：位置提取 ----
            if value is None and "position" in rule:
                pos = rule["position"]
                if pos == "first_line" and lines:
                    value = lines[0]
                elif pos == "last_line" and lines:
                    value = lines[-1]
                elif isinstance(pos, int) and 0 < pos <= len(lines):
                    value = lines[pos - 1]

            # ---- 类型提取 ----
            if value is None and "type" in rule:
                if rule["type"] == "fulltext":
                    value = cleaned

            # 数值归一化（跳过 fulltext 类型、或逐字段关闭归一化的场景）
            field_normalize = rule.get("numeric_normalize", True)
            if (
                field_normalize
                and self._numeric_normalize
                and isinstance(value, str)
                and value.strip()
                and rule.get("type") != "fulltext"
            ):
                normalized = normalize_number(value)
                value = normalized

            result["fields"][name] = value

        # 额外日期扫描：无论是否有 date 字段规则都执行
        date_patterns = [
            r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",      # 2024-05-20
            r"(\d{4})年(\d{1,2})月(\d{1,2})日?",         # 2024年5月20日
            r"(\d{4})[.](\d{1,2})[.](\d{1,2})",          # 2024.05.20
        ]
        for pat in date_patterns:
            m = re.search(pat, cleaned)
            if m:
                y, mo, d = m.group(1), m.group(2), m.group(3)
                result["date"] = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                break

        return result

    @staticmethod
    def _extract_key_value(text: str, key_label: str) -> str | None:
        """从文本中按 key:value 格式提取值。

        匹配模式: key_label[:：]\s*value
        支持一行多个 key:value 对。

        Args:
            text: 待搜索文本
            key_label: 键标签（如 "当前值"、"中位数"）

        Returns:
            匹配到的值字符串，或 None
        """
        # 构建模式：key_label 后接可选后缀(+1)/(-1)、可选分隔符，再提取数值
        # 支持: "当前值: 3.28" / "当前值3.28" / "标准差(+1) 7.61" / "标准差(-1)3.87"
        pattern = rf"{re.escape(key_label)}(?:\([+-]?\d+\))?[ 　\t]*[:：]?[ 　\t]*([+-]?[\d,]+\.?\d*%?)"
        m = re.search(pattern, text)
        if m:
            return m.group(1)
        return None
