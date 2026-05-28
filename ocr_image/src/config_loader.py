"""配置加载模块：读取 config.yaml + .env，解析 ${ENV_VAR} 占位符。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

_CONFIG_CACHE: Optional[dict] = None
_PROJECT_ROOT: Optional[Path] = None


def _get_project_root() -> Path:
    """返回项目根目录（包含 config.yaml 的目录）。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    return _PROJECT_ROOT


def _load_env() -> None:
    """加载项目根目录下的 .env 文件（若存在）。"""
    env_path = _get_project_root() / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)  # 尝试默认行为


def _substitute_env(value: Any) -> Any:
    """递归遍历配置值，将 ${VAR_NAME} 替换为环境变量。"""
    if isinstance(value, str):
        def _replacer(m: re.Match) -> str:
            var_name = m.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                import sys
                print(f"[WARNING] 环境变量 {var_name} 未设置，保留原始占位符", file=sys.stderr)
                return m.group(0)
            return env_val
        return re.sub(r'\$\{(\w+)\}', _replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env(item) for item in value]
    return value


def _merge_env_overrides(config: dict) -> dict:
    """用特定环境变量覆盖 config 中对应字段。"""
    threshold = os.environ.get("OCR_CONFIDENCE_THRESHOLD")
    if threshold is not None:
        try:
            config["ocr"]["confidence_threshold"] = float(threshold)
        except ValueError:
            pass
    return config


def _resolve_paths(config: dict) -> dict:
    """将 paths 中的相对路径转为基于项目根目录的绝对 Path。"""
    root = _get_project_root()
    for key, path_val in config.get("paths", {}).items():
        p = Path(path_val)
        if not p.is_absolute():
            config["paths"][key] = str((root / p).resolve())
    return config


def get_config(config_path: str = "config.yaml") -> dict:
    """读取并返回完整配置字典（单例缓存）。

    Args:
        config_path: 配置文件路径，默认为项目根目录下的 config.yaml

    Returns:
        完整的配置字典，其中 paths 已解析为绝对路径，${VAR} 已替换

    Raises:
        FileNotFoundError: config.yaml 不存在
        yaml.YAMLError: YAML 语法错误
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    _load_env()

    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = _get_project_root() / cfg_path

    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if config is None:
        config = {}

    config = _substitute_env(config)
    config = _merge_env_overrides(config)
    config = _resolve_paths(config)

    _CONFIG_CACHE = config
    return config
