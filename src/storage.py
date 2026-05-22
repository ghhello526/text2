"""存储模块：SQLite 数据库操作 — 建表、写入、查询。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from src.config_loader import get_config


def init_db(db_path: str | Path | None = None) -> Path:
    """初始化数据库，创建表和索引（幂等操作）。

    Args:
        db_path: 数据库文件路径，None 则从 config 读取

    Returns:
        数据库文件的 Path 对象
    """
    if db_path is None:
        config = get_config()
        db_path = Path(config["paths"]["data_dir"]) / "niuniu.db"
    else:
        db_path = Path(db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_image    TEXT    NOT NULL,
            image_path      TEXT,
            extracted_date  TEXT,
            raw_text        TEXT,
            fields_json     TEXT,
            confidence      REAL,
            ocr_engine      TEXT,
            status          TEXT    DEFAULT 'ok',
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS images (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id       INTEGER NOT NULL REFERENCES records(id),
            original_name   TEXT,
            stored_path     TEXT,
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_records_date
            ON records(extracted_date);
        CREATE INDEX IF NOT EXISTS idx_records_created
            ON records(created_at);
    """)

    conn.commit()
    conn.close()
    logger.debug("数据库已就绪: {}", db_path)
    return db_path


def insert_record(data: dict, db_path: str | Path | None = None) -> int:
    """插入一条识别记录。

    Args:
        data: {
            "source_image": str,        # 必填
            "image_path": str | None,
            "extracted_date": str | None,
            "raw_text": str,
            "fields": dict,             # 将序列化为 JSON
            "confidence": float,
            "ocr_engine": str,
            "status": str | None,       # 默认 "ok"
        }
        db_path: 数据库路径

    Returns:
        新记录的 id
    """
    if db_path is None:
        config = get_config()
        db_path = Path(config["paths"]["data_dir"]) / "niuniu.db"

    init_db(db_path)

    fields_json = json.dumps(data.get("fields", {}), ensure_ascii=False)
    status = data.get("status", "ok") or "ok"

    conn = sqlite3.connect(str(db_path), timeout=5.0)

    try:
        cur = conn.execute(
            """INSERT INTO records
               (source_image, image_path, extracted_date, raw_text,
                fields_json, confidence, ocr_engine, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["source_image"],
                data.get("image_path"),
                data.get("extracted_date"),
                data.get("raw_text", ""),
                fields_json,
                data.get("confidence", 0.0),
                data.get("ocr_engine", ""),
                status,
            ),
        )
        record_id = cur.lastrowid

        conn.execute(
            """INSERT INTO images (record_id, original_name, stored_path)
               VALUES (?, ?, ?)""",
            (
                record_id,
                data["source_image"],
                data.get("image_path"),
            ),
        )

        conn.commit()
        logger.info("记录已写入: id={}, source={}, engine={}",
                     record_id, data["source_image"], data.get("ocr_engine"))
        return record_id

    except Exception as e:
        conn.rollback()
        logger.error("数据库写入失败: {}", e)
        # 降级到 JSON 文件
        fallback_path = db_path.parent / f"fallback_{record_id if 'record_id' in dir() else 'error'}.json"
        try:
            fallback_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.warning("已降级写入 JSON 文件: {}", fallback_path)
        except Exception:
            pass
        raise

    finally:
        conn.close()


def query_records(date: str | None = None, limit: int = 50,
                  db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """查询历史记录。

    Args:
        date: 日期筛选（YYYY-MM-DD），支持模糊匹配
        limit: 最大返回条数
        db_path: 数据库路径

    Returns:
        记录列表，每条记录的 fields_json 已反序列化
    """
    if db_path is None:
        config = get_config()
        db_path = Path(config["paths"]["data_dir"]) / "niuniu.db"

    init_db(db_path)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row

    if date:
        rows = conn.execute(
            "SELECT * FROM records WHERE extracted_date LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{date}%", limit),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM records ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    records = []
    for row in rows:
        rec = dict(row)
        try:
            rec["fields"] = json.loads(rec.get("fields_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            rec["fields"] = {}
        records.append(rec)

    conn.close()
    return records


def get_record(record_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """查询单条记录。

    Args:
        record_id: 记录 ID
        db_path: 数据库路径

    Returns:
        记录 dict 或 None
    """
    if db_path is None:
        config = get_config()
        db_path = Path(config["paths"]["data_dir"]) / "niuniu.db"

    init_db(db_path)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    conn.close()

    if row is None:
        return None

    rec = dict(row)
    try:
        rec["fields"] = json.loads(rec.get("fields_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        rec["fields"] = {}
    return rec
