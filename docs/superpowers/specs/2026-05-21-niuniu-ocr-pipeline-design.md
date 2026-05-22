# 牛牛贴图 OCR 提取系统 — 设计文档

> 日期: 2026-05-21
> 状态: 待实现
> 方案: A — 手动采集 + 自动 OCR 流水线

---

## 1. 目标

从微信公众号「研究员雷牛牛」的每日贴图中，提取图片内的文字和数值，结构化存储，支持后续查询和导出。当前阶段采用**半自动**方式（手动截图 + 自动 OCR），后续可升级为全自动。

## 2. 总体架构

```
[手机截图/保存]
    │  手动 → 放入 watched/input 目录
    ▼
[文件监测 (watchdog)]
    │  检测到新图片 → 触发处理
    ▼
[图片预处理 (OpenCV)]
    │  去噪 / 增强对比度 / 纠正方向 / 长图切分
    ▼
[OCR 引擎 (双引擎)]
    │  PaddleOCR（主力）→ 大模型 VL API（兜底）
    ▼
[后处理 & 结构化]
    │  文本清洗 → 字段提取(配置驱动) → 数值归一化 → 入库
    ▼
[存储 & 输出]
    SQLite 数据库 + 可选导出 (Excel/JSON/CSV)
```

## 3. 项目结构

```
8_niuniu/
├── input/              # 放入待处理图片
├── processed/           # 处理完的图片归档 (按月: YYYY-MM/)
├── failed/             # 处理失败的图片
├── output/             # 导出结果
├── logs/               # 日志
│   └── reports/        # 每日处理报告
├── data/
│   └── niuniu.db       # SQLite 数据库
├── src/
│   ├── watcher.py      # 文件夹监听
│   ├── preprocess.py   # 图片预处理
│   ├── ocr_engine.py   # OCR 识别 (双引擎)
│   ├── extractor.py    # 后处理 & 数值提取
│   ├── storage.py      # 数据库操作
│   ├── pipeline.py     # 主流程编排
│   └── export.py       # 数据导出
├── config.yaml         # 配置文件
├── run.py              # CLI 入口
├── requirements.txt    # 依赖
└── .env.example        # 环境变量模板
```

## 4. 模块详细设计

### 4.1 文件监测 (watcher.py)

- 使用 Python `watchdog` 库监听 `input/` 目录
- 支持图片格式: PNG / JPG / JPEG / WEBP / BMP
- 检测到新文件 → 校验格式 → 加入处理队列 → 处理完成 → 移动到 `processed/YYYY-MM/`
- 启动方式: `python run.py watch` (常驻后台)
- 也支持单文件手动触发: `python run.py process -f <file>`
- 批量处理: `python run.py batch`

### 4.2 图片预处理 (preprocess.py)

OpenCV 实现，处理管道:

1. 方向纠正 — 读 EXIF 方向标签 + 自动旋转
2. 灰度化 + 自适应二值化 — 提升 OCR 准确率
3. 中值滤波去噪 — 消除截图噪点
4. CLAHE 对比度增强 — 拉亮暗部细节
5. 长图切分 — 高度超过 1500px 自动切段分别 OCR

参数通过 `config.yaml` 调整，预处理前后的图均保留便于调试。

### 4.3 OCR 引擎 (ocr_engine.py)

**双引擎策略：**

| 引擎 | PaddleOCR (本地) | 大模型 VL API (云端) |
|------|------------------|---------------------|
| 角色 | 主力，最先调用 | 兜底，复杂图片 |
| 成本 | 免费 | ~0.002-0.01 元/张 |
| 适用 | 表格、清晰文字 | 复杂排版、上下文理解 |

模式切换 (`config.yaml`):

- `local_first` (推荐) — PaddleOCR 先跑，置信度低或空白时调用云端
- `local_only` — 仅本地，零成本
- `cloud_only` — 仅云端

云端支持: 通义千问 VL (推荐) / GPT-4V / Claude Vision

### 4.4 后处理与提取 (extractor.py)

```
OCR 原始文本
  → 文本清洗 (去空行/乱码、繁简转换)
  → 字段提取 (基于 config.yaml 的 pattern 规则)
  → 数值归一化 (万→×10000, 亿→×100000000, %→÷100)
  → 结果组装 JSON → 入库
```

**配置驱动**: 字段规则在 `config.yaml` 中定义，后续根据实际贴图内容灵活调整，不需改代码。

输出结构:

```json
{
  "source_image": "2024-05-20_截图.png",
  "extracted_date": "2024-05-20",
  "raw_text": "OCR原始全文...",
  "fields": {
    "date": "2024-05-20",
    "indicators": [{ "name": "沪深300", "value": 3650.5, "change": 0.02 }],
    "comment": "今天市场整体平稳..."
  },
  "confidence": 0.92,
  "ocr_engine": "paddleocr",
  "status": "ok"
}
```

### 4.5 存储 (storage.py)

**SQLite 数据库** (`data/niuniu.db`):

- `records` 表 — 主表，存提取结果
- `images` 表 — 图片索引，关联 `records.id`

选 SQLite 理由: 零配置、便携、Python 标准库自带、数据量小足够用。

**导出 (export.py)**:

```bash
python run.py export --format excel   # → .xlsx
python run.py export --format json    # → .json
python run.py export --format csv     # → .csv
python run.py export --date 2024-05-20  # 按日期筛选
```

### 4.6 错误处理

分层异常边界:

| 层 | 异常 | 处理 |
|----|------|------|
| 文件监测 | 文件损坏/格式错误 | 移到 `failed/`，记录日志，继续监听 |
| OCR | PaddleOCR 失败 | 回退云端 API |
| OCR | 云端超时/配额耗尽 | 重试 3 次 (指数退避)，失败跳过 |
| OCR | 识别空白 | 标记低置信度，记日志 |
| 后处理 | 字段匹配不到 | 留空，不中断 |
| 后处理 | 数值异常 | 保留原始字符串，标记 `needs_review` |
| 存储 | 写入失败 | 重试 + 降级到 JSON 文件 |

### 4.7 日志

```
logs/
├── niuniu.log       # INFO 级别全量日志 (loguru)
├── error.log        # WARNING 及以上
└── reports/         # 每日摘要
    └── 2024-05-20.txt
```

## 5. 配置文件 (config.yaml)

```yaml
paths:
  input_dir: "./input"
  processed_dir: "./processed"
  failed_dir: "./failed"
  output_dir: "./output"
  data_dir: "./data"

ocr:
  mode: "local_first"          # local_first | local_only | cloud_only
  local:
    engine: "paddleocr"
    lang: "ch"
    use_gpu: false
  cloud:
    provider: "qwen"           # qwen | openai | claude
    api_key: "${QWEN_API_KEY}"
    model: "qwen-vl-max"
    timeout: 30

preprocess:
  max_width: 2048
  denoise: true
  enhance_contrast: true
  auto_rotate: true
  split_long_image: true
  split_height: 1500

extraction:
  numeric_normalize: true
  fields:
    - name: date
      pattern: "\\d{4}[-/年]\\d{1,2}[-/月]\\d{1,2}日?"
    - name: title
      position: first_line

logging:
  level: "INFO"
  daily_report: true
```

## 6. 依赖

```
watchdog          # 文件监测
opencv-python     # 图片预处理
paddleocr         # 本地 OCR
paddlepaddle      # Paddle 框架
pillow            # 图片基础处理
pyyaml            # 配置读取
python-dotenv     # 环境变量管理
loguru            # 日志增强
openpyxl          # Excel 导出
```

## 7. 启动命令汇总

```bash
pip install -r requirements.txt
cp .env.example .env      # 填入 API Key
python run.py watch        # 启动监听
python run.py process -f <file>  # 处理单张
python run.py batch         # 批量处理
python run.py export --format excel  # 导出
```

## 8. 升级路径

当前方案 A 稳定后，可按需尝试:

- **A → B**: 引入 Playwright 自动化微信桌面端，减少手动截图
- **A → C**: 引入 ADB 移动端自动化，实现全自动

OCR 提取链路不变，仅替换输入层。
