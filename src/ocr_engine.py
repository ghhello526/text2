"""OCR 引擎模块：图片预处理 + 双引擎 OCR 调度（EasyOCR + 云端 VL）。"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np
import requests
from PIL import Image, ImageOps
from loguru import logger

from src.config_loader import get_config


# ============================================================
#  预处理
# ============================================================

def preprocess(image_path: str | Path) -> list[np.ndarray]:
    """完整的图片预处理管线，返回一个或多个处理后的图片片段。

    Pipeline:
      读取 → EXIF 旋转 → 灰度化 → CLAHE 增强 → 中值滤波 → 自适应二值化 → 长图切分
    """
    config = get_config()
    pp = config["preprocess"]
    image_path = Path(image_path)

    # 1. 读取 & EXIF 自动旋转
    pil_img = Image.open(image_path)
    if pp.get("auto_rotate", True):
        pil_img = ImageOps.exif_transpose(pil_img)
    if pil_img.mode not in ("RGB", "L"):
        pil_img = pil_img.convert("RGB")

    # 2. 转 OpenCV BGR
    np_img = np.array(pil_img)
    if len(np_img.shape) == 3:
        img = cv2.cvtColor(np_img, cv2.COLOR_RGB2BGR)
    else:
        img = np_img

    # 3. 灰度化
    if pp.get("grayscale", True):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # 4. CLAHE 对比度增强（在灰度图上做）
    if pp.get("enhance_contrast", True) and len(img.shape) == 2:
        clip = pp.get("clip_limit", 2.0)
        grid = tuple(pp.get("tile_grid_size", [8, 8]))
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=grid)
        img = clahe.apply(img)

    # 5. 中值滤波去噪
    ksize = pp.get("denoise_kernel", 3)
    if ksize > 0:
        if ksize % 2 == 0:
            ksize += 1
        img = cv2.medianBlur(img, ksize)

    # 6. 自适应二值化
    if pp.get("adaptive_binarize", True) and len(img.shape) == 2:
        img = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

    # 7. 长图切分
    h, w = img.shape[:2]
    split_height = pp.get("split_height", 1500)
    overlap = pp.get("split_overlap", 100)

    if not pp.get("split_long_image", True) or h <= split_height:
        return [img]

    segments = []
    step = split_height - overlap
    y = 0
    while y < h:
        end = min(y + split_height, h)
        segments.append(img[y:end, :])
        y += step

    if not segments:
        return [img]
    return segments


# ============================================================
#  Tesseract 引擎
# ============================================================

class TesseractEngine:
    """封装 Tesseract OCR，提供统一的 recognize 接口。"""

    def __init__(self, config: dict | None = None):
        if config is None:
            config = get_config()["ocr"]["local"]
        self._lang = config.get("lang", "chi_sim+eng")
        self._oem = config.get("oem", 3)   # OCR Engine Mode (3 = default)
        self._psm = config.get("psm", 6)   # Page Segmentation Mode (6 = uniform block)
        self._tesseract_cmd = config.get("tesseract_cmd", None)

    def _lazy_init(self):
        # lazy init is lightweight for tesseract, just verify it works
        try:
            import pytesseract
            if self._tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
            # verify tesseract is callable
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR 初始化成功 (lang={})", self._lang)
        except ImportError:
            raise ImportError(
                "pytesseract 未安装。请运行: pip install pytesseract"
            )
        except Exception as e:
            raise RuntimeError(f"Tesseract 不可用: {e}")

    def recognize(self, image_path: str | Path) -> Tuple[str, float]:
        """识别图片，返回 (文本, 平均置信度)。

        先对图片做预处理（灰度化、CLAHE增强、去噪、二值化、长图切分），
        再逐段 OCR，最后拼接结果。
        """
        import pytesseract

        if self._tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

        image_path = Path(image_path)

        try:
            # ---- 预处理：灰度化 + 增强 + 去噪 + 二值化 + 切分 ----
            segments = preprocess(image_path)

            all_texts = []
            all_confidences = []

            for seg in segments:
                # numpy array → PIL Image
                pil_img = Image.fromarray(seg)

                # OCR 文本
                text = pytesseract.image_to_string(
                    pil_img,
                    lang=self._lang,
                    config=f"--oem {self._oem} --psm {self._psm}",
                ).strip()

                # OCR 置信度
                data = pytesseract.image_to_data(
                    pil_img,
                    lang=self._lang,
                    config=f"--oem {self._oem} --psm {self._psm}",
                    output_type=pytesseract.Output.DICT,
                )

                if text:
                    all_texts.append(text)

                for conf in data["conf"]:
                    if conf > 0:
                        all_confidences.append(conf / 100.0)

            combined_text = "\n".join(all_texts)

            if not combined_text:
                return ("", 0.0)

            avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.5
            return (combined_text, avg_conf)

        except Exception as e:
            logger.warning("Tesseract 识别异常: {}", e)
            return ("", 0.0)


# ============================================================
#  云端 VL 引擎
# ============================================================

class CloudVLEngine:
    """封装云端视觉大模型 API（通义千问 VL / OpenAI 兼容接口）。"""

    def __init__(self, config: dict | None = None):
        if config is None:
            config = get_config()["ocr"]["cloud"]

        self._provider = config.get("provider", "qwen")
        self._model = config.get("model", "qwen-vl-max")
        self._timeout = config.get("timeout", 30)
        self._max_retries = config.get("max_retries", 3)

        # 根据 provider 确定 API Key 和端点
        if self._provider == "qwen":
            self._api_key = config.get("api_key") or config.get("QWEN_API_KEY")
            if not self._api_key or "your-key" in str(self._api_key):
                from src.config_loader import _load_env
                import os
                self._api_key = os.environ.get("QWEN_API_KEY", "")
            self._endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        elif self._provider == "openai":
            self._api_key = config.get("api_key") or config.get("OPENAI_API_KEY")
            if not self._api_key or "your-key" in str(self._api_key):
                import os
                self._api_key = os.environ.get("OPENAI_API_KEY", "")
            base_url = config.get("api_base") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        else:
            raise ValueError(f"未知的云端 provider: {self._provider}")

        if not self._api_key or "your-key" in str(self._api_key):
            logger.warning("云端 API Key 未配置，cloud 模式将不可用")

    @staticmethod
    def _encode_image(img_path: Path) -> str:
        """将图片文件编码为 base64 data URL。"""
        ext = img_path.suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "image/png")
        data = img_path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    def recognize(self, img_path: str | Path) -> Tuple[str, float]:
        """识别图片，返回 (文本, 置信度)。"""
        img_path = Path(img_path)
        if not img_path.exists():
            raise FileNotFoundError(f"图片不存在: {img_path}")

        if not self._api_key or "your-key" in str(self._api_key):
            raise ValueError("云端 API Key 未配置，请在 .env 文件中设置 QWEN_API_KEY 或 OPENAI_API_KEY")

        data_url = self._encode_image(img_path)

        payload = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请仔细识别并提取图片中的所有文字内容。直接返回识别到的文字，不要添加任何解释或说明。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            }],
            "max_tokens": 2000,
            "temperature": 0.1,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(self._max_retries):
            try:
                resp = requests.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    return (content, 1.0)  # 云端 API 无置信度，默认 1.0

                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                    logger.warning("云端 API 限流，{}秒后重试 (第{}/{})", retry_after, attempt + 1, self._max_retries)
                    time.sleep(retry_after)

                elif resp.status_code in (401, 403):
                    raise RuntimeError(f"云端 API 认证失败: {resp.status_code} - {resp.text[:200]}")

                else:
                    logger.warning("云端 API 返回 {} (尝试 {}/{})", resp.status_code, attempt + 1, self._max_retries)
                    time.sleep(2 ** attempt)

            except requests.Timeout:
                logger.warning("云端 API 超时 (尝试 {}/{})", attempt + 1, self._max_retries)
                time.sleep(2 ** attempt)
            except requests.ConnectionError:
                logger.warning("云端 API 连接失败 (尝试 {}/{})", attempt + 1, self._max_retries)
                time.sleep(2 ** attempt)

        logger.error("云端 API 所有重试均失败")
        return ("", 0.0)


# ============================================================
#  统一 OCR 入口
# ============================================================

class OCREngine:
    """统一 OCR 引擎入口，按配置的 mode 调度本地/云端引擎。"""

    def __init__(self, config: dict | None = None):
        if config is None:
            config = get_config()

        self._mode = config["ocr"]["mode"]
        self._threshold = config["ocr"].get("confidence_threshold", 0.85)

        self._local = None
        self._cloud = None

        if self._mode in ("local_first", "local_only"):
            self._local = TesseractEngine(config["ocr"]["local"])
            logger.info("已初始化 Tesseract OCR 引擎")

        if self._mode in ("local_first", "cloud_only"):
            self._cloud = CloudVLEngine(config["ocr"]["cloud"])
            logger.info("已初始化云端 VL 引擎 ({})", self._cloud._model)

    def recognize(self, image_path: str | Path) -> Tuple[str, float, str]:
        """识别图片，返回 (文本, 置信度, 引擎名称)。

        按 mode 调度：
          - local_first: Tesseract 先跑，低置信度/空白时调用云端
          - local_only:  仅使用 Tesseract
          - cloud_only:  仅使用云端 VL
        """
        image_path = Path(image_path)

        if self._mode == "cloud_only":
            text, conf = self._cloud.recognize(image_path)
            return (text, conf, self._cloud._model)

        # local_only 或 local_first：直接传路径给 Tesseract
        local_text, local_conf = self._local.recognize(image_path)

        # local_only 模式直接返回
        if self._mode == "local_only":
            return (local_text, local_conf, "tesseract")

        # local_first 模式：判断是否需要云端兜底
        if local_conf >= self._threshold and local_text.strip():
            logger.info("Tesseract 置信度 {:.2f} >= 阈值 {:.2f}，使用本地结果", local_conf, self._threshold)
            return (local_text, local_conf, "tesseract")

        if not local_text.strip():
            logger.info("Tesseract 返回空文本，启动云端兜底")
        else:
            logger.info("Tesseract 置信度 {:.2f} < 阈值 {:.2f}，启动云端兜底", local_conf, self._threshold)

        try:
            cloud_text, cloud_conf = self._cloud.recognize(image_path)
            if cloud_text.strip():
                return (cloud_text, cloud_conf, self._cloud._model)
            else:
                logger.warning("云端也返回空文本，回退到本地结果")
                return (local_text, local_conf, "tesseract")
        except Exception as e:
            logger.error("云端兜底失败: {}，回退到本地结果", e)
            return (local_text, local_conf, "tesseract")
