"""设计系统 Token 引擎：单品牌色 OKLCH 派生全调色板。

对齐 specs/brick-ui-and-ppt-brick.md §3.2 ③「Token 引擎 + WCAG-AA 门禁」：
- 输入：单一品牌色（hex）
- 在 OKLCH 色彩空间做数学派生（tints/shades + 图表系列色 + 语义色）
- 三级 token：primitives -> semantic -> variant bundles
- semantic 提供 明(light)/暗(dark) 两套完整色板
- 质量门禁：WCAG-AA 对比度检查（text/background 组合自动达标、可断言）

设计约束：
- 核心派生路径零第三方依赖（纯 stdlib 数学，OKLCH 公式为 Björn Ottosson 参考实现）；
- 颜色派生是纯函数：同一品牌色输入 -> 同一 token 输出（确定性、稳定）；
- 语义色固定固有色相（success/danger/warning/info），彩度与品牌联动，保证任何品牌
  色都得到可读的语义色，同时保持整体色板统一；
- 所有输出 hex 统一无 '#' 前缀（渲染底座 model.TextContent/ShapeContent 约定）。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# RGB / hex / OKLCH 基础数学（纯 stdlib）
# ---------------------------------------------------------------------------

SRGB_MAX = 255.0


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """'AABBCC'（可带前导 '#'）-> (r, g, b) 0-255。"""
    h = hex_str.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"非法 hex 颜色 '{hex_str}'，应为 6 位")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """(r, g, b) 0-255 -> 6 位 hex（无 '#'）。会裁剪到 [0, 255]。"""
    return "".join(f"{max(0, min(255, int(round(v)))):02X}" for v in (r, g, b))


def _srgb_to_linear(c: float) -> float:
    c /= SRGB_MAX
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _min_chroma_hex(L: float, C: float, H: float) -> str:
    """OKLCH -> hex；C/H 允许非法时自动夹紧到可渲染范围。"""
    return oklch_to_hex(_clip(L), _clip(C, 0.0, 0.4), _normalize_hue(H))


def rgb_to_oklab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """sRGB(0-255) -> OKLab。参考 Björn Ottosson 经典公式。

    正变换顺序：linear-RGB ->(F)-> 线性 LMS ->(cbrt)-> OKLab 前体。
    注意 cbrt 必须作用在 F 变换之后，逆向才与 oklab_to_rgb 精确互逆。
    """
    r, g, b = (_srgb_to_linear(v) for v in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_ = math.cbrt(l)
    m_ = math.cbrt(m)
    s_ = math.cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return (L, a, b)


def oklab_to_rgb(L: float, a: float, b: float) -> Tuple[int, int, int]:
    """OKLab -> sRGB(0-255)，越界分量夹紧。"""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ ** 3
    m = m_ ** 3
    s = s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return (
        int(round(_linear_to_srgb(_clip(r)) * SRGB_MAX)),
        int(round(_linear_to_srgb(_clip(g)) * SRGB_MAX)),
        int(round(_linear_to_srgb(_clip(b)) * SRGB_MAX)),
    )


def _normalize_hue(H: float) -> float:
    H = H % 360.0
    if H < 0:
        H += 360.0
    return H


def rgb_to_oklch(hex_str: str) -> Tuple[float, float, float]:
    """hex -> (L, C, H)；H 规范化到 [0, 360)。"""
    L, a, b = rgb_to_oklab(hex_to_rgb(hex_str))
    C = math.hypot(a, b)
    H = _normalize_hue(math.degrees(math.atan2(b, a)))
    return (L, C, H)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """OKLCH -> hex（无 '#'）。非法 H 自动规范化。"""
    a = C * math.cos(math.radians(_normalize_hue(H)))
    b = C * math.sin(math.radians(_normalize_hue(H)))
    try:
        return rgb_to_hex(*oklab_to_rgb(L, a, b))
    except OverflowError:
        return rgb_to_hex(255, 255, 255)


def mix_hex(a_hex: str, b_hex: str, t: float) -> str:
    """在 OKLCH 空间线性插值两种颜色（t=0 -> a，t=1 -> b）。

    色相走最短弧线，避免跨 0°/360° 边界跳变。
    """
    La, Ca, Ha = rgb_to_oklch(a_hex)
    Lb, Cb, Hb = rgb_to_oklch(b_hex)
    t = _clip(t)
    dh = _normalize_hue(Hb - Ha)
    if dh > 180.0:
        dh -= 360.0
    return _min_chroma_hex(La + (Lb - La) * t, Ca + (Cb - Ca) * t, Ha + dh * t)


# ---------------------------------------------------------------------------
# WCAG 对比度
# ---------------------------------------------------------------------------


def relative_luminance(hex_str: str) -> float:
    """WCAG 2.x 相对亮度（sRGB 线性化加权）。"""
    r, g, b = (_srgb_to_linear(v) for v in hex_to_rgb(hex_str))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG 对比度 (fg, bg)，范围 [1, 21]。"""
    l1 = relative_luminance(fg_hex)
    l2 = relative_luminance(bg_hex)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _ensure_contrast(fg_l, fg_c, fg_h, bg_hex, min_ratio: float) -> str:
    """把一个 OKLCH 前景色朝反向明度拉伸，直到对 bg 对比度达标。

    逐档 L 逼近是确定性的（固定步长与最大迭代），保证可单测复现。
    Return: 满足对比度的 hex。
    """
    lo, hi = fg_l, (0.12 if relative_luminance(bg_hex) > 0.3 else 0.95)
    step = 0.02
    cur = fg_l
    while cur >= lo and cur <= hi:
        cand = _min_chroma_hex(cur, fg_c, fg_h)
        if contrast_ratio(cand, bg_hex) >= min_ratio:
            return cand
        cur += step if hi > lo else -step
    # 兜底：极难达成时用纯黑/纯白
    fallback = "000000" if relative_luminance(bg_hex) > 0.5 else "FFFFFF"
    return fallback


# ---------------------------------------------------------------------------
# Token 派生
# ---------------------------------------------------------------------------


AA_TEXT = 4.5  # WCAG-AA 正文最小对比度
_AA_TEXT = AA_TEXT
_AA_LARGE = 3.0

# 图表系列暗色的目标色相（品牌色种之外的稳定语义刻度）
_SERIES_HUE_STEP = 40.0
_SEMANTIC_HUES = {
    "success": 152.0,
    "danger": 20.0,
    "warning": 70.0,
    "info": 212.0,
}
_N_SERIES = 6


def _derive_series(brand_hex: str) -> List[str]:
    """图表系列色：以品牌色相为锚，OKLCH 按固定色相步进旋转 6 档。"""
    L, C, H = rgb_to_oklch(brand_hex)
    out = []
    for i in range(_N_SERIES):
        out.append(_min_chroma_hex(_clip(L), _clip(max(C, 0.10), 0.0, 0.22), H + i * _SERIES_HUE_STEP))
    return out


def _derive_tints(brand_hex: str, n: int = 6) -> List[str]:
    """浅色阶：L 向白推进、C 递减（浅一点 = 灰一点）。"""
    L, C, H = rgb_to_oklch(brand_hex)
    out = []
    for i in range(1, n + 1):
        t = i / n
        out.append(_min_chroma_hex(L + (0.97 - L) * t, max(C * (1 - t), 0.01), H))
    return out


def _derive_shades(brand_hex: str, n: int = 6) -> List[str]:
    """深色阶：L 向黑推进、C 递减。"""
    L, C, H = rgb_to_oklch(brand_hex)
    out = []
    for i in range(1, n + 1):
        t = i / n
        out.append(_min_chroma_hex(L - (L - 0.24) * t, max(C * (1 - t), 0.01), H))
    return out


def _semantic(hex_str: str) -> str:
    """固定色相 + 品牌联动彩度的语义色。"""
    _, C, _ = rgb_to_oklch(hex_str)
    c = _clip(max(C, 0.09), 0.0, 0.16)
    return {
        k: _min_chroma_hex(0.60, c, h) for k, h in _SEMANTIC_HUES.items()
    }


def _pick_on_accent(accent_hex: str) -> str:
    """在强调色上选前景：黑/白确定性二选一（取对比度更高者）。

    决策规则（确定性、可断言）：对黑、白分别算 WCAG 对比度，取更高者。
    对接近中灰的 accent 即使两者都 <4.5 也仍有确定输出（尽力而为）。
    """
    cb = contrast_ratio("000000", accent_hex)
    cw = contrast_ratio("FFFFFF", accent_hex)
    return "000000" if cb >= cw else "FFFFFF"


def _semantic_light(brand_hex: str) -> Dict[str, str]:
    L, C, H = rgb_to_oklch(brand_hex)
    hue = H
    accent = brand_hex
    accent_strong = _min_chroma_hex(_clip(L - 0.10, 0.28, 0.55), C, hue)
    bg = _min_chroma_hex(0.97, 0.012, hue)
    accent_soft = _min_chroma_hex(0.935, max(C * 0.5, 0.03), hue)

    # 文本与背景亮度差异大，天然达标；再显式断言一次
    text = _ensure_contrast(0.28, min(C * 0.6, 0.05), hue, bg, _AA_TEXT)
    text_muted = _ensure_contrast(0.46, min(C * 0.5, 0.04), hue, bg, _AA_TEXT)

    # 强调色上的前景：黑/白确定性二选一并保证 AA
    text_on_accent = _pick_on_accent(accent)
    accent_surface = _pick_on_accent(accent_strong)

    grads = _derive_tints(brand_hex, 2)
    gradient_from = accent_strong
    gradient_to = _min_chroma_hex(_clip(L + 0.08, 0.4, 0.9), C, hue)

    sem = {
        "text": text,
        "text_muted": text_muted,
        "text_on_accent": text_on_accent,
        "background": bg,
        "surface": _min_chroma_hex(0.92, 0.02, hue),
        "border": _min_chroma_hex(0.84, 0.025, hue),
        "accent": accent,
        "accent_strong": accent_strong,
        "accent_soft": accent_soft,
        "accent_surface": accent_surface,
        "gradient_from": gradient_from,
        "gradient_to": gradient_to,
        "tint_1": grads[0],
        "tint_2": grads[1],
    }
    sem.update(_semantic(brand_hex))
    return sem


def _semantic_dark(brand_hex: str) -> Dict[str, str]:
    L, C, H = rgb_to_oklch(brand_hex)
    hue = H
    bg = _min_chroma_hex(0.15, 0.018, hue)
    # 暗底上的强调需足够亮
    accent = _min_chroma_hex(_clip(max(L, 0.72), 0.55, 0.82), _clip(C, 0.08, 0.24), hue)
    accent_soft = _min_chroma_hex(0.28, max(C * 0.5, 0.04), hue)
    accent_strong = _min_chroma_hex(_clip(max(L, 0.62), 0.45, 0.75), C, hue)

    text = _ensure_contrast(0.92, min(C * 0.6, 0.05), hue, bg, _AA_TEXT)
    text_muted = _ensure_contrast(0.75, min(C * 0.5, 0.04), hue, bg, _AA_TEXT)

    # 强调色上的前景：黑/白确定性二选一
    text_on_accent = _pick_on_accent(accent)
    accent_surface = _pick_on_accent(accent_strong)

    sem = {
        "text": text,
        "text_muted": text_muted,
        "text_on_accent": text_on_accent,
        "background": bg,
        "surface": _min_chroma_hex(0.21, 0.02, hue),
        "border": _min_chroma_hex(0.31, 0.02, hue),
        "accent": accent,
        "accent_strong": accent_strong,
        "accent_soft": accent_soft,
        "accent_surface": text_on_accent,
        "gradient_from": _min_chroma_hex(_clip(max(L, 0.62), 0.5, 0.8), C, hue),
        "gradient_to": _min_chroma_hex(_clip(L + 0.1, 0.3, 0.7), C, hue),
    }
    sem.update(_semantic(brand_hex))
    return sem


# 语气档（variant bundles）：换 variant 即换整包字体/形语/密度/配色参考
_VARIANTS = {
    "通用": {
        "palette_ref": "semantic.light",
        "font_family": ["Arial", "PingFang SC"],
        "density": "normal",
        "shape_idiom": "round_rect",
        "texture": "flat",
    },
    "咨询": {
        "palette_ref": "semantic.light",
        "font_family": ["Helvetica Neue", "PingFang SC"],
        "density": "compact",
        "shape_idiom": "rect",
        "texture": "line",
    },
    "投行": {
        "palette_ref": "semantic.light",
        "font_family": ["Times New Roman", "Songti SC"],
        "density": "air",
        "shape_idiom": "rect",
        "texture": "minimal",
    },
}


def derive_tokens(
    brand_hex: str, variant: str = "通用", semantics: str = "light"
) -> Dict[str, object]:
    """单一品牌色 -> 完整三级 token 集。

    Args:
        brand_hex: 品牌主色（hex，可带 '#'）。
        variant: 语气档 key，见 ``VARIANTS``。
        semantics: 默认明暗语义档："light" | "dark"。

    Returns:
        结构化 token dict：
            - spec: 派生来源与设置
            - primitives: brand / ink / paper / tints / shades / series
            - semantic: {"light": {...}, "dark": {...}}
            - variants: 预置语气档（含当前选中的 variant key）
            - resolved: 当前生效的 semantic 快照
    """
    brand = rgb_to_hex(*hex_to_rgb(brand_hex))
    L, C, H = rgb_to_oklch(brand)
    light = _semantic_light(brand)
    dark = _semantic_dark(brand)
    resolved = light if semantics == "light" else dark

    primitives = {
        "brand": brand,
        "ink": _ensure_contrast(0.22, 0.01, H, light["background"], _AA_TEXT),
        "paper": light["background"],
        "tints": _derive_tints(brand),
        "shades": _derive_shades(brand),
        "series": _derive_series(brand),
    }

    if variant not in _VARIANTS:
        raise KeyError(f"未知语气档 '{variant}'，可选: {list(_VARIANTS)}")

    # 语气档配色倾向：就地微调装饰性 token（accent_soft/gradient_*），
    # 让「应用外观」在通用/咨询/投行间有可见差异；对比度门禁字段不动。
    _variant_flavor(resolved, variant)

    return {
        "spec": {
            "model": "oklch",
            "source": brand,
            "variant": variant,
            "semantics": semantics,
        },
        "primitives": primitives,
        "semantic": {"light": light, "dark": dark},
        "variants": _VARIANTS,
        "resolved": resolved,
    }


def _variant_flavor(resolved: dict, variant: str) -> None:
    """语气档配色倾向（就地微调装饰性 token）。

    保持对比度门禁字段（text/background/surface/accent/text_on_accent 等）
    不动，只调整装饰性点缀（accent_soft）与渐变（gradient_from/to），让
    「应用外观」在通用/咨询/投行三档间有真实可见差异：

      - 通用: 保持推导基线，最温和（flat/round_rect/normal 对应中性配色）;
      - 咨询: 彩度收敛、渐变两色趋同（compact/line/rect 的克制、冷峻中性）;
      - 投行: 点缀更浅、渐变两端对比更锐（air/minimal/rect 的硬朗、极简）。

    纯函数：同输入同输出；dark/light 语义档均适用（仅动装饰色）。
    """
    if variant == "通用":
        return
    Ls, Cs, Hs = rgb_to_oklch(resolved["accent_soft"])
    Lf, Cf, Hf = rgb_to_oklch(resolved["gradient_from"])
    Lt, Ct, Ht = rgb_to_oklch(resolved["gradient_to"])
    if variant == "咨询":
        resolved["accent_soft"] = _min_chroma_hex(_clip(Ls - 0.02), Cs * 0.72, Hs)
        resolved["gradient_from"] = _min_chroma_hex(_clip(Lf + 0.05), Cf * 0.9, Hf)
        resolved["gradient_to"] = _min_chroma_hex(_clip(Lt - 0.05), Ct * 0.9, Ht)
    elif variant == "投行":
        resolved["accent_soft"] = _min_chroma_hex(_clip(Ls + 0.04), Cs * 0.5, Hs)
        resolved["gradient_from"] = _min_chroma_hex(_clip(Lf - 0.06), Cf, Hf)
        resolved["gradient_to"] = _min_chroma_hex(_clip(Lt + 0.06), Ct, Ht)


def validate_contrast(tokens: object, min_ratio: float = _AA_TEXT) -> List[dict]:
    """WCAG-AA 门禁：对明暗两套 semantic 检查关键前景/背景组合。

    Returns:
        [{"scheme", "fg", "bg", "ratio", "ok"}] 全部组合；ok=False 表示不达标。
    """
    t = tokens
    assert isinstance(t, dict)
    sems = t["semantic"]
    checks: List[dict] = []
    for scheme, sem in sems.items():
        for fg_key, bg_key in (
            ("text", "background"),
            ("text", "surface"),
            ("text_muted", "background"),
            ("text_on_accent", "accent"),
            ("accent_surface", "accent_strong"),
        ):
            fg, bg = sem[fg_key], sem[bg_key]
            ratio = contrast_ratio(fg, bg)
            checks.append(
                {
                    "scheme": scheme,
                    "fg": fg,
                    "bg": bg,
                    "ratio": round(ratio, 3),
                    "ok": ratio >= min_ratio,
                }
            )
    return checks


# ---------------------------------------------------------------------------
# 主品牌色提取入口（素材通道的贴近版）
# ---------------------------------------------------------------------------


def extract_brand_from_image(path: str) -> Optional[str]:
    """从用户上传的参考图/模板提取单一主品牌色（hex）。

    实现为简单确定步骤：
      1. 缩小到固定网格（保持速度）；
      2. 过滤近白/近黑/低饱和像素；
      3. 按色相桶聚类，取频数最高桶（若最高桶是灰系则取次高桶）的均值色。

    复杂素材分析（多主色/梯度）留待素材通道，本入口只负责"给我一个起点
    品牌色"。

    Returns:
        6 位 hex（无 '#'）；图像不可读/无有效像素时返回 None。
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None

    img = img.resize((64, 64))
    buckets: Dict[int, List[Tuple[int, int, int]]] = {}
    for r, g, b in img.getdata():
        mx, mn = max(r, g, b), min(r, g, b)
        s = (mx - mn) / 255.0
        v = mx / 255.0
        # 过滤暗/亮/低饱和
        if v < 0.18 or v > 0.92 or s < 0.22:
            continue
        if s == 0:
            continue
        h = math.degrees(math.atan2(
            math.sqrt(3) * (g - b), 2 * r - g - b
        )) % 360.0
        key = int(h / 30.0)
        buckets.setdefault(key, []).append((r, g, b))

    if not buckets:
        return None

    # 频数最高桶；若它基本是灰系（桶内平均饱和度仍低），顺延到次高
    ranked = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    for _, pixels in ranked:
        avg = tuple(round(sum(c[i] for c in pixels) / len(pixels)) for i in range(3))
        mx, mn = max(avg), min(avg)
        if (mx - mn) / 255.0 > 0.22:
            return rgb_to_hex(*avg)
    # 全部近灰：取频数最高桶均值（尽力而为）
    return rgb_to_hex(*tuple(round(sum(c[i] for c in ranked[0][1]) / len(ranked[0][1])) for i in range(3)))


__all__ = [
    "hex_to_rgb",
    "rgb_to_hex",
    "rgb_to_oklch",
    "oklch_to_hex",
    "mix_hex",
    "relative_luminance",
    "contrast_ratio",
    "derive_tokens",
    "validate_contrast",
    "extract_brand_from_image",
    "VARIANTS",
    "AA_TEXT",
]
