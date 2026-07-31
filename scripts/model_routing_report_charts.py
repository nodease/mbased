"""모델 라우팅 실험 보고서용 PNG 그래프를 생성한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1600
HEIGHT = 900
BACKGROUND = "#F8FAFC"
INK = "#0F172A"
MUTED = "#64748B"
GRID = "#CBD5E1"
COLORS = ("#2563EB", "#F59E0B", "#059669", "#7C3AED", "#DC2626")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((64, 42), title, fill=INK, font=_font(34, bold=True))
    draw.text((64, 92), subtitle, fill=MUTED, font=_font(19))
    return image, draw


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _bar_group(
    draw: ImageDraw.ImageDraw,
    *,
    title: str,
    labels: list[str],
    values: list[float],
    box: tuple[int, int, int, int],
    value_format: str,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=8, fill="white", outline=GRID, width=2)
    draw.text((left + 24, top + 18), title, fill=INK, font=_font(24, bold=True))
    chart_top = top + 78
    chart_bottom = bottom - 70
    chart_left = left + 74
    chart_right = right - 32
    maximum = max(values, default=0) or 1
    slot = (chart_right - chart_left) / max(1, len(values))
    bar_width = min(100, int(slot * 0.55))
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        center = chart_left + slot * (index + 0.5)
        height = (chart_bottom - chart_top) * max(0, value) / maximum
        x0 = int(center - bar_width / 2)
        x1 = int(center + bar_width / 2)
        y0 = int(chart_bottom - height)
        draw.rounded_rectangle((x0, y0, x1, chart_bottom), radius=5, fill=COLORS[index % len(COLORS)])
        rendered = value_format.format(value)
        value_box = draw.textbbox((0, 0), rendered, font=_font(18, bold=True))
        draw.text((center - (value_box[2] - value_box[0]) / 2, y0 - 30), rendered, fill=INK, font=_font(18, bold=True))
        label_box = draw.textbbox((0, 0), label, font=_font(16))
        draw.text((center - (label_box[2] - label_box[0]) / 2, chart_bottom + 18), label, fill=MUTED, font=_font(16))


def write_economics_chart(
    path: Path,
    *,
    summaries: dict[str, dict[str, Any]],
    quality: dict[str, Any],
    assessment: dict[str, Any],
) -> Path:
    """고가/저가/자동의 비용과 품질을 한 이미지에서 비교한다."""

    image, draw = _canvas(
        "자동 모델 라우팅 경제성",
        "같은 입력에서 고성능 고정, 저비용 고정, 자동 라우팅을 비교",
    )
    labels = ["고성능 고정", "저비용 고정", "자동 라우팅"]
    costs = [
        _safe_float(summaries.get(key, {}).get("total_cost"))
        for key in ("high_fixed", "low_fixed", "automatic")
    ]
    auto_vs_high = quality.get("automatic_vs_high") or {}
    low_vs_high = quality.get("low_vs_high") or {}
    high_score = _safe_float(
        quality.get("high_reference_score")
        or auto_vs_high.get("average_baseline_score")
    )
    auto_score = _safe_float(
        auto_vs_high.get("average_candidate_score")
        or auto_vs_high.get("comparison_average_score")
    )
    low_score = _safe_float(
        low_vs_high.get("average_candidate_score")
        or low_vs_high.get("comparison_average_score")
    )
    scores = [high_score, low_score, auto_score]
    _bar_group(
        draw,
        title="총 실행 비용",
        labels=labels,
        values=costs,
        box=(54, 145, 785, 690),
        value_format="${:.4f}",
    )
    _bar_group(
        draw,
        title="출력 품질 점수",
        labels=labels,
        values=scores,
        box=(815, 145, 1546, 690),
        value_format="{:.1f}",
    )
    savings = _safe_float(assessment.get("runtime_savings_rate_pct"))
    break_even = assessment.get("break_even_requests")
    draw.rounded_rectangle((54, 720, 1546, 844), radius=8, fill="#EFF6FF", outline="#93C5FD", width=2)
    draw.text((82, 748), f"고성능 고정 대비 절감률: {savings:.1f}%", fill="#1D4ED8", font=_font(23, bold=True))
    draw.text(
        (82, 790),
        f"초기 검증비 손익분기: {break_even if break_even is not None else '계산 불가'}건",
        fill=INK,
        font=_font(20),
    )
    incident_threshold = (assessment.get("low_fixed_tradeoff") or {}).get(
        "cost_per_avoided_severe_regression_usd"
    )
    if incident_threshold is not None:
        draw.text(
            (760, 790),
            f"품질 사고 1건 회피 추가비용: ${float(incident_threshold):.4f}",
            fill=INK,
            font=_font(20),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    return path


def write_lifecycle_chart(
    path: Path,
    *,
    checkpoints: list[dict[str, Any]],
    model_windows: list[dict[str, Any]],
) -> Path:
    """정책 갱신 시점과 구간별 모델 선택 변화를 시각화한다."""

    image, draw = _canvas(
        "자동 모델 라우팅 정책 수명주기",
        "정책 점검 시점의 규칙·검증비와 10회 구간별 실제 선택 모델",
    )
    left, top, right, bottom = 54, 145, 1546, 520
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill="white", outline=GRID, width=2)
    draw.text((left + 24, top + 18), "정책 점검 이력", fill=INK, font=_font(24, bold=True))
    if checkpoints:
        max_sequence = max(int(row.get("sequence") or 0) for row in checkpoints) or 1
        max_spend = max(_safe_float(row.get("validation_spend_usd")) for row in checkpoints) or 1
        x0, x1 = left + 90, right - 60
        y_rules, y_spend = top + 145, top + 270
        draw.line((x0, y_rules, x1, y_rules), fill=GRID, width=2)
        draw.line((x0, y_spend, x1, y_spend), fill=GRID, width=2)
        draw.text((left + 24, y_rules - 12), "규칙", fill=MUTED, font=_font(17))
        draw.text((left + 24, y_spend - 12), "검증비", fill=MUTED, font=_font(17))
        for row in checkpoints:
            sequence = int(row.get("sequence") or 0)
            x = x0 + (x1 - x0) * sequence / max_sequence
            rules = int(row.get("active_rule_count") or 0)
            spend = _safe_float(row.get("validation_spend_usd"))
            draw.ellipse((x - 7, y_rules - rules * 18 - 7, x + 7, y_rules - rules * 18 + 7), fill=COLORS[2])
            draw.text((x - 20, y_rules + 14), f"{sequence}회", fill=MUTED, font=_font(14))
            draw.ellipse((x - 7, y_spend - (spend / max_spend) * 80 - 7, x + 7, y_spend - (spend / max_spend) * 80 + 7), fill=COLORS[1])
            draw.text((x - 28, y_spend + 14), f"${spend:.3f}", fill=MUTED, font=_font(14))

    draw.rounded_rectangle((54, 550, 1546, 844), radius=8, fill="white", outline=GRID, width=2)
    draw.text((78, 572), "10회 구간별 실제 모델 분포", fill=INK, font=_font(24, bold=True))
    all_models = sorted({model for window in model_windows for model in (window.get("models") or {})})
    for row_index, window in enumerate(model_windows[:8]):
        y = 625 + row_index * 25
        label = f"{window.get('start')}-{window.get('end')}회"
        draw.text((80, y), label, fill=MUTED, font=_font(15))
        counts = window.get("models") or {}
        total = sum(int(value) for value in counts.values()) or 1
        x = 210
        available = 1280
        for model_index, model in enumerate(all_models):
            count = int(counts.get(model) or 0)
            if not count:
                continue
            width = available * count / total
            draw.rectangle((x, y + 2, x + width, y + 18), fill=COLORS[model_index % len(COLORS)])
            if width > 90:
                draw.text((x + 6, y + 2), f"{model} {count}", fill="white", font=_font(12, bold=True))
            x += width
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)
    return path
