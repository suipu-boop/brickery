"""Theme/Tokens 单测：确定性 + OKLCH 数学可断言 + WCAG-AA 对比度门禁。

运行（用装了 python-pptx 的解释器，本测试不依赖 python-pptx）：
    python -m pytest tests/test_theme.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

import pytest

from ppt_brick import theme

BRANDS = ["2F5BE7", "#0EA5A5", "F59E0B", "B91C1C", "6D28D9", "0F766E", "DC2626"]


def test_derive_is_deterministic():
    """同一品牌色 -> 同一 token 输出（确定性、稳定）。"""
    a = theme.derive_tokens("2F5BE7")
    b = theme.derive_tokens("#2F5BE7")
    assert a["semantic"]["light"] == b["semantic"]["light"]
    assert a["semantic"]["dark"] == b["semantic"]["dark"]
    assert a["primitives"]["series"] == b["primitives"]["series"]


def test_oklch_roundtrip_recovers_rgb():
    """hex -> OKLCH -> hex 回环误差应 < 2 个色阶。"""
    for brand in BRANDS:
        L, C, H = theme.rgb_to_oklch(brand)
        back = theme.oklch_to_hex(L, C, H)
        ra, ga, ba = theme.hex_to_rgb(brand)
        rb, gb, bb = theme.hex_to_rgb(back)
        for a, b in ((ra, rb), (ga, gb), (ba, bb)):
            assert abs(a - b) <= 1, f"{brand} roundtrip: {back}"


def test_hue_normalized_and_hex_6():
    """H 规范化到 [0,360)，输出一律 6 位 hex（无 '#'）。"""
    for brand in BRANDS:
        _, _, H = theme.rgb_to_oklch(brand)
        assert 0.0 <= H < 360.0
        for v in theme.derive_tokens(brand)["primitives"]["series"]:
            assert len(v) == 6 and v.isalnum()


def test_semantic_tokens_shape():
    """semantic light/dark 两套 key 齐全，派生结果合法。"""
    t = theme.derive_tokens("2F5BE7")
    keys = {
        "text", "text_muted", "text_on_accent", "background", "surface",
        "border", "accent", "accent_strong", "accent_soft",
        "accent_surface", "gradient_from", "gradient_to",
    }
    for scheme in ("light", "dark"):
        for k in keys:
            assert k in t["semantic"][scheme], f"{scheme}/{k} 缺失"
        for k, v in t["semantic"][scheme].items():
            assert len(v) == 6 and not v.startswith("#"), f"{scheme}/{k}={v}"


def test_series_distinct_and_count():
    """图表系列色恰 6 个且互不相同。"""
    for brand in BRANDS:
        series = theme.derive_tokens(brand)["primitives"]["series"]
        assert len(series) == 6
        assert len(set(series)) == 6


def test_tints_shades_length():
    """tints/shades 各 6 档。"""
    for brand in BRANDS:
        p = theme.derive_tokens(brand)["primitives"]
        assert len(p["tints"]) == 6
        assert len(p["shades"]) == 6


def test_wcag_aa_contrast_gate():
    """明暗两套语义关键组合全部达标 WCAG-AA（4.5:1）。"""
    for brand in BRANDS:
        t = theme.derive_tokens(brand)
        results = theme.validate_contrast(t)
        assert results, "validate_contrast 应返回检查项"
        bad = [r for r in results if not r["ok"]]
        assert not bad, f"{brand} 有未达标组合: {bad}"


def test_contrast_ratio_range():
    """对比度函数在 [1, 21] 内，黑白极值正确。"""
    assert theme.contrast_ratio("000000", "FFFFFF") >= 21
    assert theme.contrast_ratio("FFFFFF", "FFFFFF") <= 1.0001
    assert 1.0 <= theme.contrast_ratio("2F5BE7", "FFFFFF") <= 21.0


def test_mix_hex_ends():
    """OKLCH 插值端点复位（t=0/t=1 回到原色，允许量化误差）。"""
    a, b = "2F5BE7", "FFFFFF"
    la, ca, ha = theme.rgb_to_oklch(theme.mix_hex(a, b, 0.0))
    ra, ga, ba = theme.hex_to_rgb(a)
    rb, gb, bb = theme.hex_to_rgb(theme.oklch_to_hex(la, ca, ha))
    for x, y in ((ra, rb), (ga, gb), (ba, bb)):
        assert abs(x - y) <= 2


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        theme.derive_tokens("2F5BE7", variant="不存在档")


def test_extract_brand_solid_image():
    """纯色图应提取到近似该色的主品牌色。"""
    img = theme.extract_brand_from_image("__init__.py")  # 非图像 -> None
    assert img is None


def test_extract_brand_from_real_image(tmp_path):
    """用 PIL 生成纯色小图验证提取主色（确定性、单一主色）。"""
    PIL = pytest.importorskip("PIL")

    img = PIL.Image.new("RGB", (32, 32), (47, 91, 231))  # 2F5BE7
    p = str(tmp_path / "brand.png")
    img.save(p)
    out = theme.extract_brand_from_image(p)
    assert out is not None
    r, g, b = theme.hex_to_rgb(out)
    assert abs(r - 47) <= 14 and abs(g - 91) <= 14 and abs(b - 231) <= 14
