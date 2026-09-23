"""补全员 A：成员详情 AI 空缺填补（提案级，人审后写入）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.source_sync import (
    _AI_GAP_FIELD_WHITELIST,
    _ai_gap_fill_members,
    _proposal_has_fillable_fields,
    _seed_field_origins,
)


def test_birth_date_excluded_from_ai_whitelist():
    assert "birth_date" not in _AI_GAP_FIELD_WHITELIST
    assert "join_date" not in _AI_GAP_FIELD_WHITELIST
    assert "leave_date" not in _AI_GAP_FIELD_WHITELIST
    assert "chinese_name" in _AI_GAP_FIELD_WHITELIST


def test_seed_field_origins_from_fandom():
    p = {
        "sources": ["K-pop Fandom"],
        "artist_fields": {"chinese_name": "金某", "birth_place": None},
        "positions": ["Main Vocal"],
        "field_origins": {},
    }
    _seed_field_origins(p)
    assert p["field_origins"]["chinese_name"] == "fandom"
    assert p["field_origins"]["positions"] == "fandom"
    assert "birth_place" not in p["field_origins"]


def test_ai_gap_fill_skipped_without_cfg():
    group = SimpleNamespace(name="G", english_name="G", id=1)
    props = [{"name": "A", "artist_fields": {}, "sources": []}]
    out, meta = _ai_gap_fill_members(None, group, props, cfg=None)
    assert meta["status"] == "skipped"
    assert out is props


def test_ai_gap_fill_tags_ai_and_ignores_birth_date():
    group = SimpleNamespace(name="TEST", english_name="TEST", id=1)
    proposals = [
        {
            "action": "enrich",
            "name": "Alice",
            "korean_name": None,
            "sources": ["K-pop Fandom"],
            "artist_id": None,
            "positions": None,
            "artist_fields": {
                "birth_date": None,
                "chinese_name": None,
                "english_name": None,
                "stage_name": None,
                "birth_place": None,
                "occupation": None,
                "description": "A member of TEST.",
            },
            "field_origins": {"description": "fandom"},
        }
    ]
    fake = {
        "members": [
            {
                "name": "Alice",
                "fields": {
                    "chinese_name": "爱丽丝",
                    "english_name": "Alice",
                    "stage_name": None,
                    "birth_place": "Seoul",
                    "occupation": "歌手",
                    "description": None,
                    "positions": ["Main Vocal"],
                    "birth_date": "2001-01-01",
                },
                "basis": "公开资料",
            }
        ]
    }
    cfg = {
        "provider": "openai-compatible",
        "base_url": "http://example.invalid",
        "api_key": "k",
        "model": "m",
    }
    with patch("app.services.ai_service.chat_completion", return_value="{}"), patch(
        "app.services.ai_service.parse_json", return_value=fake
    ):
        out, meta = _ai_gap_fill_members(SimpleNamespace(), group, proposals, cfg=cfg)

    assert meta["status"] == "ok"
    assert meta["filled_fields"] >= 3
    af = out[0]["artist_fields"]
    assert af["chinese_name"] == "爱丽丝"
    assert af["birth_place"] == "Seoul"
    assert af.get("birth_date") in (None, "")
    assert out[0]["field_origins"]["chinese_name"] == "ai"
    assert out[0]["positions"] == ["Main Vocal"]
    assert out[0]["field_origins"]["positions"] == "ai"
    assert out[0]["field_origins"].get("description") == "fandom"
    assert "AI 补全" in out[0]["sources"]
    assert _proposal_has_fillable_fields(out[0])


if __name__ == "__main__":
    test_birth_date_excluded_from_ai_whitelist()
    test_seed_field_origins_from_fandom()
    test_ai_gap_fill_skipped_without_cfg()
    test_ai_gap_fill_tags_ai_and_ignores_birth_date()
    print("PASS test_ai_gap_fill")
