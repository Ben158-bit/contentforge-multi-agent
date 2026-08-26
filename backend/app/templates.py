"""渠道模板加载。

营销内容生产流水线的"渠道参数"来源：
文案创作 Agent 依据渠道模板生成，审校 Agent 依据模板检查。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

TEMPLATES_FILE = Path(__file__).resolve().parent / "templates" / "channels.yaml"


@lru_cache
def load_channel_templates() -> dict[str, dict]:
    """加载全部渠道模板，返回 {channel_id: template}。"""
    with open(TEMPLATES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    channels = data.get("channels", {})
    if not channels:
        raise RuntimeError(f"渠道模板为空: {TEMPLATES_FILE}")
    return channels


def get_channel(channel_id: str) -> dict:
    """按 id 获取渠道模板，未知渠道直接报错。"""
    channels = load_channel_templates()
    if channel_id not in channels:
        raise KeyError(f"未知渠道: {channel_id}，可选: {', '.join(channels)}")
    return channels[channel_id]


def list_channels() -> dict[str, dict]:
    """全部渠道（用于前端下拉与校验）。"""
    return load_channel_templates()
