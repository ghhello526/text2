# 牛牛贴图 OCR 提取系统 — 实施计划

> 基于设计文档: [2026-05-21-niuniu-ocr-pipeline-design.md](./specs/2026-05-21-niuniu-ocr-pipeline-design.md)
> 日期: 2026-05-21

---

## 整体策略

先跑通核心闭环：**图片 → OCR 提取文字 → 字段结构化 → 入库**。用真实贴图验证效果，验收通过再扩展周边功能（文件监听、批量、日志、导出等）。

---

## MVP 阶段（必做）

### Step 1: 项目骨架

**目标**: 目录和依赖就绪，`config.yaml` 可加载。

```
8_niuniu/
├── input/              # 待处理的图片（手动放入）
├── processed/           # 处理完成的归档
├── data/                # SQLite 数据库文件
├── src/
│   ├── config_loader.py # 读取 config.yaml + .env
│   ├── ocr_engine.py
│   ├── extractor.py
│   └── storage.py
├── config.yaml
├── run.py               # CLI 入口
├── requirements.txt
└── .env.example
```

**文件**:
- `requirements.txt`: paddleocr, paddlepaddle, opencv-python, pillow, pyyaml, python-dotenv, openpyxl
- `.env.example`: `QWEN_API_KEY=` / `OPENAI_API_KEY=`
- `config.yaml`: OCR 模式、预处理参数、提取规则
- `src/config_loader.py`: 读取 yaml + .env 环境变量，暴露 `get_config()` 接口

**验收**: `python -c "from src.config_loader import get_config; print(get_config())"` 正常输出配置字典。

---

### Step 2: OCR 引擎（含预处理）

**目标**: 输入图片路径，输出识别到的全文文本。

**文件**: `src/ocr_engine.py`

**内容**:

```
OCR 前预处理（与服务 OCR 一体）:
  1. 方向纠正（EXIF 自动旋转）
  2. 灰度化 + 自适应二值化
  3. 中值滤波去噪
  4. CLAHE 对比度增强
  5. 长图切分（高度 > 1500px 时分段）

双引擎调度:
  - PaddleOCR（主力，本地免费）
  - 云端 VL（qwen-vl / gpt-4v，兜底）
  - 模式: local_first / local_only / cloud_only（config.yaml 控制）
```

**类和函数**:

| 名称 | 职责 |
|------|------|
| `preprocess(image_path) → list[ndarray]` | 读取 + 预处理，返回图片片段列表 |
| `PaddleOCREngine` | `__init__(config)`, `recognize(img) → (text, confidence)` |
| `CloudVLEngine` | `__init__(config)`, `recognize(img) → (text, confidence)` |
| `OCREngine` | 统一入口，按 `mode` 调度双引擎，返回 `(text, confidence, engine_name)` |

**验收**: 拿一张真实贴图，调用 `OCREngine.recognize()`，终端打印出人能看懂的文字。

---

### Step 3: 字段提取

**目标**: 从 OCR 原始文本中，按配置规则抽出结构化字段。

**文件**: `src/extractor.py`

**内容**:

```
OCR 原始文本
  → 文本清洗（去空行 / 乱码 / 繁简转换）
  → 按 config.extraction.fields 中的正则/位置规则逐字段提取
  → 数值归一化（万→×10000, 亿→×100000000, %→÷100）
  → 输出 dict
```

**类和函数**:

| 名称 | 职责 |
|------|------|
| `clean_text(raw) → str` | 去噪、繁简转换 |
| `normalize_number(text) → float | str` | 数值单位归一化 |
| `Extractor(config)` | 加载字段规则 |
| `Extractor.extract(raw_text) → dict` | 返回 `{"date": "...", "fields": {...}, ...}` |

**config.yaml 提取规则示例**（后续根据实际贴图调）:

```yaml
extraction:
  numeric_normalize: true
  fields:
    - name: date
      pattern: "\\d{4}[-/年]\\d{1,2}[-/月]\\d{1,2}日?"
    - name: body_text
      type: fulltext
```

**验收**: 喂入 OCR 出来的原始文本，`extract()` 能返回包含目标字段的 dict。

---

### Step 4: 存储

**目标**: 提取结果写入 SQLite，支持按日期查询。

**文件**: `src/storage.py`

**表结构**:

```sql
CREATE TABLE records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_image    TEXT,      -- 原图文件名
    image_path      TEXT,      -- 归档路径
    extracted_date  TEXT,      -- 提取到的日期
    raw_text        TEXT,      -- OCR 原始全文
    fields_json     TEXT,      -- 结构化字段 JSON
    confidence      REAL,      -- 识别置信度
    ocr_engine      TEXT,      -- paddleocr / qwen-vl / gpt-4v
    status          TEXT DEFAULT 'ok',  -- ok / needs_review / failed
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   INTEGER REFERENCES records(id),
    original_name TEXT,
    stored_path TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);
```

**函数**:

| 名称 | 职责 |
|------|------|
| `init_db(db_path)` | 建库建表 |
| `insert_record(data) → id` | 写入 records + images |
| `query_records(date=None) → list` | 按日期查询 |
| `get_record(id) → dict` | 单条查询 |

**验收**: 手动构造一条提取结果，调用 `insert_record()`，再用 `query_records()` 能查回相同内容。

---

### Step 5: CLI 串联

**目标**: 一条命令跑通全流程。

**文件**: `run.py`

**命令**:

```bash
# 处理单张图片（MVP 核心命令）
python run.py process -f input/截图.png

# 查询历史记录
python run.py query --date 2024-05-20
```

**process 流程**:
```
OCR → Extract → Store → 打印结果到终端
```

使用 `argparse` 实现，零额外依赖。

**验收**: `python run.py process -f input/真实贴图.png` → 终端打印识别文本和结构化字段 → `python run.py query` 能查到入库记录。

---

## MVP 验收标准（决策门）

用 **3-5 张真实贴图** 跑 `process` 命令：

| 检查项 | 标准 |
|--------|------|
| OCR 可读性 | 文字完整、无明显错乱 |
| 字段提取 | 日期、数值等关键字段准确抽出 |
| 入库 | query 能查到对应的 records |
| 整体 | 一张图从处理到入库，终端打印结果，无需人工干预中间步骤 |

**通过 → 进入完善阶段。不通过 → 调优 OCR 参数 / 提取规则，不写新模块。**

---

## 完善阶段（MVP 通过后）

| 步骤 | 内容 |
|------|------|
| 6. 图片预处理增强 | 在 preprocess 里按需加去噪/增强/长图切分的参数调优 |
| 7. 字段规则完善 | 根据多张真实贴图的规律，丰富 `extraction.fields` 的配置 |
| 8. 导出功能 | Excel / JSON / CSV 导出 (`export.py`) |
| 9. 文件监测 | watchdog 监听 `input/` 目录，新图片自动处理 |
| 10. 批量处理 | `python run.py batch` 处理已有图片 |
| 11. 日志系统 | loguru 日志文件 + 每日处理报告 |

---

## 实施顺序总览

```
Step 1 (骨架)
    │
    ▼
Step 2 (OCR引擎) ──────────────────┐
    │                               │
    ▼                               ▼
Step 3 (字段提取)            Step 4 (存储)
    │                               │
    └───────────┬───────────────────┘
                ▼
         Step 5 (CLI串联)
                │
                ▼
        【决策门：验证通过？】
         │            │
      通过          不通过
         │            │
         ▼            ▼
   完善阶段     调优 OCR/提取规则
                 ↗ 回到验证
```

Step 2 和 Step 4 之间无依赖，可并行开发。其余按顺序。
