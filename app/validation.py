"""入参校验：工况草稿与单条读数。所有读数与阈值都是 0..65535 的整数闭区间。"""

from .tree import READING_MAX, READING_MIN, _is_strict_int


def _check_reading(name, value):
    if not _is_strict_int(value):
        raise ValueError(f"{name} 必须是整数")
    if not READING_MIN <= value <= READING_MAX:
        raise ValueError(f"{name} 必须在 {READING_MIN}..{READING_MAX} 闭区间内")


def validate_reading(payload):
    """校验单条读数，返回 (temperature, front, back)。"""
    if not isinstance(payload, dict):
        raise ValueError("读数必须是 JSON 对象")
    missing = [k for k in ("temperature", "front", "back") if k not in payload]
    if missing:
        raise ValueError("读数缺少字段：" + "、".join(missing))
    temperature, front, back = (
        payload["temperature"], payload["front"], payload["back"]
    )
    _check_reading("温度", temperature)
    _check_reading("前压", front)
    _check_reading("后压", back)
    return temperature, front, back


def _check_interval(name, value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} 必须是包含两个整数的闭区间 [下限, 上限]")
    lo, hi = value
    if not _is_strict_int(lo) or not _is_strict_int(hi):
        raise ValueError(f"{name} 的上下限都必须是整数")
    if not (READING_MIN <= lo <= READING_MAX and READING_MIN <= hi <= READING_MAX):
        raise ValueError(f"{name} 的上下限必须在 {READING_MIN}..{READING_MAX} 闭区间内")
    if lo > hi:
        raise ValueError(f"{name} 的下限不能大于上限")
    return [lo, hi]


def validate_draft(payload):
    """校验工况草稿。

    返回规范化 dict：temperature/front/back 三个读数闭区间，
    以及 diff（后压减前压）闭区间；diff 允许落在 -65535..65535。
    """
    if not isinstance(payload, dict):
        raise ValueError("工况草稿必须是 JSON 对象")
    missing = [k for k in ("temperature", "front", "back", "diff") if k not in payload]
    if missing:
        raise ValueError("工况草稿缺少字段：" + "、".join(missing))

    draft = {
        "temperature": _check_interval("温度区间", payload["temperature"]),
        "front": _check_interval("前压区间", payload["front"]),
        "back": _check_interval("后压区间", payload["back"]),
    }

    diff = payload["diff"]
    if not isinstance(diff, (list, tuple)) or len(diff) != 2:
        raise ValueError("压差区间必须是包含两个整数的闭区间 [下限, 上限]")
    d_lo, d_hi = diff
    if not _is_strict_int(d_lo) or not _is_strict_int(d_hi):
        raise ValueError("压差区间的上下限都必须是整数")
    if d_lo > d_hi:
        raise ValueError("压差区间的下限不能大于上限")
    # 后压、前压各自在 0..65535，差值的自然范围是 -65535..65535。
    if not (-READING_MAX <= d_lo <= READING_MAX and -READING_MAX <= d_hi <= READING_MAX):
        raise ValueError("压差区间的上下限必须在 -65535..65535 闭区间内")
    draft["diff"] = [d_lo, d_hi]
    return draft
