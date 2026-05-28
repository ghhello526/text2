#!/usr/bin/env python3
"""快速查看数据库中的 OCR 记录（金融数据友好展示）。"""

import json
import sqlite3
import sys

DB = "data/niuniu.db"

# 金融数据字段定义（按展示顺序）
FINANCIAL_FIELDS = [
    ("fund_name",       "基金名称"),
    ("fund_code",       "代码"),
    ("index_points",    "点数"),
    ("daily_change",    "变化幅度"),
    ("analysis_type",   "分析类型"),
    ("current_value",   "当前值"),
    ("percentile",      "分位点"),
    ("danger_value",    "危险值"),
    ("median",          "中位数"),
    ("opportunity_value", "机会值"),
    ("max_value",       "最大值"),
    ("avg_value",       "平均值"),
    ("min_value",       "最小值"),
    ("std_dev",         "标准差"),
    ("z_score",         "Z分数"),
]

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 获取记录 ID（支持参数指定）
ids = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else None

if ids:
    rows = [conn.execute("SELECT * FROM records WHERE id=?", (i,)).fetchone() for i in ids]
    rows = [r for r in rows if r is not None]
else:
    rows = conn.execute("SELECT * FROM records ORDER BY id").fetchall()

if not rows:
    print("没有记录。")
    conn.close()
    sys.exit(0)

print(f"共 {len(rows)} 条记录：\n")

for row in rows:
    print(f"=== #{row['id']}  {row['source_image']} ===")
    print(f"  日期:     {row['extracted_date'] or '(未识别)'}")
    print(f"  置信度:   {(row['confidence'] or 0):.1%}")
    print(f"  引擎:     {row['ocr_engine']}")
    print(f"  状态:     {row['status']}")

    # 解析结构化字段
    try:
        fields = json.loads(row['fields_json'] or '{}')
    except (json.JSONDecodeError, TypeError):
        fields = {}

    # 展示金融数据字段（优先）
    financial_data = {}
    other_fields = {}
    for k, v in fields.items():
        if k in dict(FINANCIAL_FIELDS):
            financial_data[k] = v
        else:
            other_fields[k] = v

    if financial_data:
        print(f"  {'─' * 40}")
        print(f"  {'金融数据':^36}")
        print(f"  {'─' * 40}")
        for key, label in FINANCIAL_FIELDS:
            val = financial_data.get(key)
            if val is not None:
                print(f"  {label:<10} {val}")

    if other_fields:
        print(f"  {'─' * 40}")
        print(f"  其他字段:")
        for k, v in other_fields.items():
            print(f"    {k}: {v}")

    # OCR 原文（截断显示）
    raw = row['raw_text'] or ''
    if raw:
        print(f"  {'─' * 40}")
        print(f"  OCR 原文 ({len(raw)} 字):")
        print(f"  {raw[:300]}")
    print()

conn.close()
