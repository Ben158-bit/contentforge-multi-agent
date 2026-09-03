"""评测数据集（evals/dataset.jsonl）的完整性测试。

验证门槛「30 条样本全部可重复运行」的基础：数据规模、渠道分布、
case_id 唯一性、字段非空、无密钥、约束分布合理、渠道 id 与后端模板同步。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from schema import (
    VALID_CHANNEL_IDS,
    EvalCase,
    dataset_summary,
    load_dataset,
)

DATASET = Path(__file__).resolve().parent / "dataset.jsonl"
CHANNELS_YAML = (
    Path(__file__).resolve().parent.parent / "backend" / "app" / "templates" / "channels.yaml"
)


@pytest.fixture(scope="module")
def cases() -> list[EvalCase]:
    return load_dataset(DATASET)


def test_dataset_file_exists() -> None:
    assert DATASET.exists(), f"评测数据集缺失: {DATASET}"


def test_30_cases(cases: list[EvalCase]) -> None:
    assert len(cases) == 30


def test_case_ids_unique(cases: list[EvalCase]) -> None:
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_channel_distribution(cases: list[EvalCase]) -> None:
    summary = dataset_summary(cases)
    assert summary["by_channel"] == {
        "xiaohongshu": 9,
        "wechat": 9,
        "weibo": 6,
        "product_page": 6,
    }


def test_every_case_has_required_fields(cases: list[EvalCase]) -> None:
    for c in cases:
        assert c.case_id and c.topic and c.brand_name and c.target_audience
        assert c.channel_id in VALID_CHANNEL_IDS


def test_no_secret_material(cases: list[EvalCase]) -> None:
    """样本不得含疑似 API Key / 私钥内容。"""
    secret_re = re.compile(r"(sk-[A-Za-z0-9]{16,}|BEGIN .*PRIVATE KEY)", re.IGNORECASE)
    for c in cases:
        blob = c.text_blob()
        assert not secret_re.search(blob), f"{c.case_id} 疑似包含密钥"


def test_about_one_third_has_forbidden_terms(cases: list[EvalCase]) -> None:
    """约 1/3 样本带禁用词约束（8-14 条区间容忍）。"""
    n = sum(1 for c in cases if c.forbidden_terms)
    assert 8 <= n <= 14, f"带禁用词样本数 {n} 偏离约 1/3"


def test_hard_constraints_present_but_not_everywhere(cases: list[EvalCase]) -> None:
    """多数样本有硬约束，但允许纯自由创作样本存在。"""
    n = sum(1 for c in cases if c.hard_constraints)
    assert n >= len(cases) * 0.8


def test_every_case_has_extra_requirements(cases: list[EvalCase]) -> None:
    for c in cases:
        assert c.extra_requirements, f"{c.case_id} 缺少 extra_requirements"


def test_channel_ids_sync_with_backend_templates() -> None:
    """schema.VALID_CHANNEL_IDS 必须与 backend channels.yaml 的渠道 id 一致。

    若后端新增/删除渠道模板而评测集未同步，评测将失真，此测试负责拦截。
    """
    import yaml

    assert CHANNELS_YAML.exists(), f"找不到渠道模板: {CHANNELS_YAML}"
    data = yaml.safe_load(CHANNELS_YAML.read_text(encoding="utf-8"))
    backend_ids = set(data["channels"].keys())
    assert set(VALID_CHANNEL_IDS) == backend_ids, (
        f"评测渠道 {sorted(VALID_CHANNEL_IDS)} 与后端渠道 {sorted(backend_ids)} 不一致"
    )


def test_eval_case_rejects_unknown_channel() -> None:
    with pytest.raises(ValueError):
        EvalCase(
            case_id="bad-001",
            topic="主题",
            brand_name="虚构品牌",
            target_audience="受众",
            channel_id="douyin",
        )


def test_duplicate_case_ids_rejected(tmp_path: Path) -> None:
    p = tmp_path / "dup.jsonl"
    p.write_text(
        '{"case_id": "a", "topic": "t", "brand_name": "b", '
        '"target_audience": "u", "channel_id": "weibo"}\n'
        '{"case_id": "a", "topic": "t2", "brand_name": "b2", '
        '"target_audience": "u2", "channel_id": "wechat"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        load_dataset(p)


def test_secret_in_case_rejected(tmp_path: Path) -> None:
    p = tmp_path / "secret.jsonl"
    p.write_text(
        '{"case_id": "a", "topic": "t", "brand_name": "b", '
        '"target_audience": "u", "channel_id": "weibo", '
        '"reference_notes": "key sk-abcdef1234567890abcdef"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="密钥"):
        load_dataset(p)
