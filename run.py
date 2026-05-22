#!/usr/bin/env python3
"""牛牛贴图 OCR 提取系统 — CLI 入口。

用法:
    python run.py process -f <图片路径>    # 处理单张图片
    python run.py query [--date <日期>]    # 查询历史记录
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from src.config_loader import get_config
from src.ocr_engine import OCREngine
from src.extractor import Extractor
from src.storage import init_db, insert_record, query_records


# ---- 支持的图片格式 ----
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def cmd_process(args: argparse.Namespace) -> None:
    """处理单张图片：OCR → 提取 → 入库。"""
    image_path = Path(args.file)
    if not image_path.is_file():
        logger.error("文件不存在: {}", image_path)
        sys.exit(1)
    if image_path.suffix.lower() not in SUPPORTED_EXTS:
        logger.error("不支持的图片格式: {}，支持: {}", image_path.suffix, ", ".join(SUPPORTED_EXTS))
        sys.exit(1)

    print(f"正在处理: {image_path.name}")

    # 初始化
    config = get_config()
    init_db()
    ocr = OCREngine(config)
    extractor = Extractor(config["extraction"])

    # OCR
    print("[OCR] 正在识别...")
    try:
        text, confidence, engine_name = ocr.recognize(image_path)
    except Exception as e:
        logger.error("OCR 失败: {}", e)
        sys.exit(1)

    print(f"[OCR] 引擎: {engine_name}, 置信度: {confidence:.2%}")
    if text:
        preview = text[:200].replace("\n", "\\n")
        if len(text) > 200:
            preview += "..."
        print(f"[OCR] 文本预览: {preview}")
    else:
        print("[OCR] [WARN] 未识别到文字")

    # 提取
    print("[提取] 正在结构化...")
    result = extractor.extract(text)

    print(f"[提取] 识别日期: {result.get('date', '未识别')}")
    fields_preview = {k: v for k, v in result.get("fields", {}).items() if v is not None}
    print(f"[提取] 字段: {json.dumps(fields_preview, ensure_ascii=False)}")

    # 入库
    status = "ok" if confidence >= 0.5 else "needs_review"
    record_data = {
        "source_image": image_path.name,
        "image_path": str(image_path.resolve()),
        "extracted_date": result.get("date"),
        "raw_text": text,
        "fields": result.get("fields", {}),
        "confidence": confidence,
        "ocr_engine": engine_name,
        "status": status,
    }

    print("[存储] 正在写入数据库...")
    try:
        record_id = insert_record(record_data)
    except Exception as e:
        logger.error("存储失败: {}", e)
        sys.exit(1)

    # 打印结果
    print()
    print("=" * 60)
    print(f"  ID:       {record_id}")
    print(f"  图片:     {record_data['source_image']}")
    print(f"  日期:     {record_data['extracted_date'] or '未识别'}")
    print(f"  引擎:     {record_data['ocr_engine']}")
    print(f"  置信度:   {record_data['confidence']:.2%}")
    print(f"  状态:     {record_data['status']}")
    print("-" * 60)
    print(f"  识别文本 (前500字):")
    print(f"  {text[:500]}")
    print("-" * 60)
    print(f"  结构化字段:")
    for k, v in result.get("fields", {}).items():
        if v is not None:
            print(f"    {k}: {v}")
    print("=" * 60)
    print(f"\n[OK] 记录 #{record_id} 已保存")


def cmd_batch(args: argparse.Namespace) -> None:
    """批量处理目录下所有图片。"""
    input_dir = Path(args.dir)
    if not input_dir.is_dir():
        logger.error("目录不存在: {}", input_dir)
        sys.exit(1)

    # 收集所有支持的图片
    images = sorted(
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
    )

    if not images:
        print(f"在 {input_dir} 中未找到支持的图片 ({', '.join(SUPPORTED_EXTS)})")
        return

    print(f"找到 {len(images)} 张图片，开始批量处理...\n")

    config = get_config()
    init_db()
    ocr = OCREngine(config)
    extractor = Extractor(config["extraction"])

    success_count = 0
    fail_count = 0

    for i, image_path in enumerate(images, 1):
        print(f"[{i}/{len(images)}] 正在处理: {image_path.name}")

        try:
            text, confidence, engine_name = ocr.recognize(image_path)
        except Exception as e:
            logger.error("OCR 失败 [{}]: {}", image_path.name, e)
            fail_count += 1
            continue

        result = extractor.extract(text)
        status = "ok" if confidence >= 0.5 else "needs_review"
        record_data = {
            "source_image": image_path.name,
            "image_path": str(image_path.resolve()),
            "extracted_date": result.get("date"),
            "raw_text": text,
            "fields": result.get("fields", {}),
            "confidence": confidence,
            "ocr_engine": engine_name,
            "status": status,
        }

        try:
            record_id = insert_record(record_data)
            print(f"  → 记录 #{record_id} | 日期: {result.get('date', '-')} | 引擎: {engine_name} | 置信度: {confidence:.2%}")
            success_count += 1
        except Exception as e:
            logger.error("存储失败 [{}]: {}", image_path.name, e)
            fail_count += 1

    print()
    print("=" * 50)
    print(f"  批量处理完成: 成功 {success_count} 张, 失败 {fail_count} 张")
    print("=" * 50)


def cmd_query(args: argparse.Namespace) -> None:
    """查询历史记录。"""
    get_config()
    init_db()

    records = query_records(date=args.date)

    if not records:
        print("未找到匹配记录。")
        return

    print(f"共 {len(records)} 条记录:\n")
    if args.verbose:
        for r in records:
            print(f"--- 记录 #{r['id']} ---")
            print(f"  图片:     {r['source_image']}")
            print(f"  日期:     {r.get('extracted_date', '-')}")
            print(f"  引擎:     {r.get('ocr_engine', '-')}")
            print(f"  置信度:   {(r.get('confidence') or 0):.2%}")
            print(f"  状态:     {r.get('status', '-')}")
            print(f"  全文:\n{r.get('raw_text', '')[:300]}")
            print()
    else:
        # 简洁表格
        header = f"{'ID':<5} {'日期':<12} {'图片':<25} {'引擎':<14} {'置信度':<8} {'状态':<12}"
        print(header)
        print("-" * len(header))
        for r in records:
            print(
                f"{r['id']:<5} "
                f"{r.get('extracted_date') or '-':<12} "
                f"{r['source_image'][:24]:<25} "
                f"{r.get('ocr_engine') or '-':<14} "
                f"{(r.get('confidence') or 0):.0%}".ljust(8) + " "
                f"{r.get('status') or '-':<12}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="牛牛贴图 OCR 提取系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # process 子命令
    p_process = sub.add_parser("process", help="处理单张图片")
    p_process.add_argument("-f", "--file", required=True, help="图片文件路径")

    # batch 子命令
    p_batch = sub.add_parser("batch", help="批量处理目录下所有图片")
    p_batch.add_argument("-d", "--dir", default="./input", help="图片目录路径 (默认: ./input)")

    # query 子命令
    p_query = sub.add_parser("query", help="查询历史记录")
    p_query.add_argument("--date", default=None, help="日期筛选 (YYYY-MM-DD)")
    p_query.add_argument("--verbose", "-v", action="store_true", help="显示详细文本")

    args = parser.parse_args()

    if args.command == "process":
        cmd_process(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "query":
        cmd_query(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
