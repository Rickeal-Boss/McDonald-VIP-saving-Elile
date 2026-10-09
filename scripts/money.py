"""money.py · 金额单位换算与一致性硬校验（🔴RL-02 / 🔴RL-20）。

契约：内部统一为「分」整数；跨接口只在边界换算；输出前回「元」。
零第三方依赖。禁止二进制 float 直接参与金额乘除（一律走 Decimal/整数字符串）。

真机实测单位**三源**（ADR-12 / probe §4.1）：
  1. 菜单类 `query-meals`：**元，且为字符串**（如 "28" / "117.5"）      → UNIT_YUAN
  2. `calculate-price` 主价 `price/productPrice/originalPrice/discount`：**分整数**（2800） → UNIT_CENT
  3. `enjoyable/enjoyed.realDiscount`：**元(number)**（5.0）           → UNIT_YUAN_FLOAT
     而 `enjoyable.balance`：**分**（1100）                            → UNIT_CENT_MIXED

硬规则：**任一来源单位与声明不符即显式抛 UnitMismatch**，禁止静默换算
（例如把菜单字符串当分、把算价整数当元，都会被拒绝 —— 从根上杜绝 100 倍误差）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Union

# ---- 单位常量 -------------------------------------------------------------
UNIT_YUAN = "yuan"           # 菜单类接口（query-meals），元且为字符串
UNIT_CENT = "cent"           # calculate-price 主价（price/discount/...），分整数
UNIT_YUAN_FLOAT = "yuan_f"   # enjoyable / enjoyed.realDiscount，元(number)
UNIT_CENT_MIXED = "cent_mix" # enjoyable.balance，分整数

_ALL_UNITS = frozenset({UNIT_YUAN, UNIT_CENT, UNIT_YUAN_FLOAT, UNIT_CENT_MIXED})

# 门槛提示类字段前缀：**不得**计入到手价（🔴RL-20）
_MARKETING_ROOTS = ("enjoyable",)


class UnitMismatch(ValueError):
    """金额单位不一致（🔴RL-02）。触发 RL-02 报告话术，**不产出数字**。"""


def _is_bool(value: object) -> bool:
    # bool 是 int 的子类，必须先排除，否则 True 会被当成 1 分
    return isinstance(value, bool)


def _reject(tag: str, unit: str, value: object, expected: str) -> None:
    raise UnitMismatch(
        f"[{tag}] 单位不符：声明 unit={unit!r} 期望 {expected}，"
        f"实得 {type(value).__name__}={value!r}（禁止静默换算，🔴RL-02）"
    )


def to_cents(value: Union[int, float, str], unit: str = UNIT_YUAN, *, tag: str = "?") -> int:
    """将任意来源金额转为「分」整数（int）。

    - `unit=UNIT_YUAN`      要求 value 为**字符串元**（菜单形态）；
    - `unit=UNIT_CENT`      要求 value 为**整数分**；
    - `unit=UNIT_YUAN_FLOAT`要求 value 为**元(number)**；
    - `unit=UNIT_CENT_MIXED`要求 value 为**整数分**（balance）。

    类型与声明单位不符 → 抛 `UnitMismatch`。
    """
    if unit not in _ALL_UNITS:
        raise UnitMismatch(f"[{tag}] 未知单位 {unit!r}，合法值 {sorted(_ALL_UNITS)}")

    # 1) 菜单：元 + 字符串
    if unit == UNIT_YUAN:
        if not isinstance(value, str):
            _reject(tag, unit, value, "元(字符串)，如 '28' / '117.5'")
        s = value.strip()
        if s == "":
            raise UnitMismatch(f"[{tag}] 空字符串不是合法金额（🔴RL-02）")
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise UnitMismatch(f"[{tag}] 字符串 {value!r} 无法解析为金额（🔴RL-02）")
        return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))

    # 2) / 4) 分（整数）；balance 与主价同为整数分
    if unit in (UNIT_CENT, UNIT_CENT_MIXED):
        if _is_bool(value) or not isinstance(value, int):
            _reject(tag, unit, value, "整数分，如 2800")
        return int(value)

    # 3) 元(number)
    if unit == UNIT_YUAN_FLOAT:
        if _is_bool(value) or not isinstance(value, (int, float)):
            _reject(tag, unit, value, "元(number)，如 5.0")
        d = Decimal(str(value))
        return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))

    raise AssertionError("unreachable")  # pragma: no cover


def to_yuan_str(cents: int) -> str:
    """分 → 元字符串（两位小数）：6450 → '64.50'。仅接受 int。"""
    if _is_bool(cents) or not isinstance(cents, int):
        raise UnitMismatch(
            f"[to_yuan_str] 期望『分(整数)』，实得 {type(cents).__name__}={cents!r}（🔴RL-02）"
        )
    neg = cents < 0
    n = -cents if neg else cents
    whole, frac = divmod(n, 100)
    s = f"{whole}.{frac:02d}"
    return f"-{s}" if neg else s


def assert_unit(tag: str, value: object, unit: str) -> None:
    """单位一致性硬校验：不符即抛 UnitMismatch（🔴RL-02）。通过则返回 None。"""
    to_cents(value, unit, tag=tag)  # 复用类型校验，不关心换算结果


def is_marketing_hint(field_name: str) -> bool:
    """识别门槛提示类字段（`enjoyable` 及其子字段）—— **不得**计入到手价（🔴RL-20）。"""
    if not isinstance(field_name, str):
        return False
    f = field_name.strip().lower()
    return any(f == root or f.startswith(root + ".") or f.startswith(root + "_")
               for root in _MARKETING_ROOTS)


def yuan_str(value: Union[int, float, str]) -> str:
    """便捷：任意来源金额 → 元字符串。菜单字符串直接规范化，其余先归一分再回元。"""
    if isinstance(value, str):
        d = Decimal(value.strip())
        return to_yuan_str(int((d * 100).to_integral_value(rounding=ROUND_HALF_UP)))
    if _is_bool(value):
        raise UnitMismatch("[yuan_str] 布尔不是合法金额")
    if isinstance(value, int):
        return to_yuan_str(value)
    if isinstance(value, float):
        return to_yuan_str(to_cents(value, UNIT_YUAN_FLOAT))
    raise UnitMismatch(f"[yuan_str] 不支持的类型 {type(value).__name__}")


# 自测锚点（selfcheck 会调用）：6450 分 == '64.50' 元
SELF_TEST_CASES = [(6450, "64.50"), (2990, "29.90"), (0, "0.00")]


def _self_test() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures += 1

    print("money.py self-test")
    for cents, want in SELF_TEST_CASES:
        got = to_yuan_str(cents)
        check(got == want, f"to_yuan_str({cents}) == {want} (got {got})")

    check(to_cents("28", UNIT_YUAN) == 2800, "to_cents('28', yuan) == 2800")
    check(to_cents("117.5", UNIT_YUAN) == 11750, "to_cents('117.5', yuan) == 11750")
    check(to_cents(2800, UNIT_CENT) == 2800, "to_cents(2800, cent) == 2800")
    check(to_cents(5.0, UNIT_YUAN_FLOAT) == 500, "to_cents(5.0, yuan_f) == 500")
    check(to_cents(1100, UNIT_CENT_MIXED) == 1100, "to_cents(1100, cent_mix) == 1100")

    # 单位不符必须显式抛错（负例）
    for label, fn in (
        ("菜单 int 当元 → 抛 UnitMismatch", lambda: to_cents(2800, UNIT_YUAN)),
        ("菜单字符串当分 → 抛 UnitMismatch", lambda: to_cents("28", UNIT_CENT)),
        ("bool 当分 → 抛 UnitMismatch", lambda: to_cents(True, UNIT_CENT)),
        ("float 当分 → 抛 UnitMismatch", lambda: to_cents(28.0, UNIT_CENT)),
    ):
        try:
            fn()
            check(False, label + "（未抛错）")
        except UnitMismatch:
            check(True, label)

    check(is_marketing_hint("enjoyable") and is_marketing_hint("enjoyable.balance"), "is_marketing_hint 命中")
    check(not is_marketing_hint("enjoyed"), "is_marketing_hint 不误伤 enjoyed")

    print("money.py:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_self_test())
