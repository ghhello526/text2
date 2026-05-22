#!/usr/bin/env python3
"""快速查看数据库中的 OCR 记录。"""

import sqlite3, sys

DB = "data/niuniu.db"

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
    print(f"  OCR 原文 ({len(row['raw_text'] or '')} 字):")
    print(f"  {row['raw_text'][:600]}")
    print(f"  结构化字段:")
    import json
    try:
        fields = json.loads(row['fields_json'] or '{}')
        for k, v in fields.items():
            print(f"    {k}: {v}")
    except:
        pass
    print()

conn.close()
