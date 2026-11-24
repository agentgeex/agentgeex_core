"""Chinese numerals -> Arabic digits: only convert combinations with an explicit unit anchor.

Design principle (the opposite of cn2an's whole-sentence transform):
    **Better not to convert than to convert wrongly.**
    Only numeric strings immediately followed by a whitelisted quantifier/unit are handled;
    anything without an anchor is left alone.

Why not use cn2an's transform: measured on real text, whole-sentence conversion is badly error-prone --
    "yi gong" (altogether) became "1 gong", "ling xia" (below zero) became "0 xia",
    "yi qi" (together) became "1 qi", "shi fen" (very) became "10 fen". Its strategy is
    "convert on seeing a Chinese numeral", while what is wanted here is
    "convert only when it is certainly a quantity".

Three rule tiers (strongest to weakest):
    1) the "percent X" pattern -> X%
    2) "X dian Y fen (Z miao)" / "X dian ban" -> strict time pattern (fen/miao are units only here)
    3) numeral + quantifier (yuan / ge / jian / ren / sui / mi / dun / gongli / ye ...);
       wan/yi additionally get a look-ahead constraint: what follows must be a quantifier,
       punctuation or end of sentence, otherwise leave it alone
       ("qian wan" in "qian wan bie wang" = "by all means do not forget" is not a quantity)

Usage:
    python3 cn_num_normalize.py          # run the built-in cases
"""

from __future__ import annotations

import re

_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10**4, "亿": 10**8}

# Characters a digit run may contain: excluding wan/yi (kept as units in the text, which reads more naturally:
# "yi-bai-er-shi-wu wan" -> "125 wan" rather than "1250000")
_CN_NUM_CHARS = "零〇一二三四五六七八九十百千两"
_NUM_PAT = rf"[{_CN_NUM_CHARS}]+(?:点[{_CN_NUM_CHARS}]+)?"


def _bitwise(s: str) -> int | None:
    """Digit-by-digit reading: er-ling-er-liu -> 2026."""
    out = []
    for ch in s:
        if ch not in _CN_DIGIT:
            return None
        out.append(str(_CN_DIGIT[ch]))
    return int("".join(out)) if out else None


def _positional(s: str) -> int | None:
    """Positional reading: yi-bai-er-shi-wu -> 125."""
    total = section = num = 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if num == 0 and unit == 10:
                num = 1  # "shi wu" = 15
            section += num * unit
            num = 0
        elif ch in _CN_BIG:
            section = (section + num) * _CN_BIG[ch]
            total += section
            section = num = 0
        else:
            return None
    return total + section + num


def cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if any(ch in _CN_UNIT for ch in s):
        return _positional(s)
    return _bitwise(s)


def cn_to_number(s: str) -> str | None:
    """Chinese numeral -> Arabic string. Supports decimals (shi-er dian wu -> 12.5).

        Also accepts a mixed start (the model may hear "4.7" as "si dian qi"):
        if the integer part is already Arabic digits, it is used as-is.
    """
    if not s:
        return None
    if "点" in s:
        head, _, tail = s.partition("点")
        frac = "".join(str(_CN_DIGIT[c]) for c in tail if c in _CN_DIGIT)
        if not frac or len(frac) != len(tail):
            return None
        if head and head.isdigit():
            whole: int | None = int(head)
        else:
            whole = cn_to_int(head) if head else 0
        return None if whole is None else f"{whole}.{frac}"
    val = cn_to_int(s)
    return None if val is None else str(val)


# -- Quantifier whitelist -----------------------------------------
# fen/miao are deliberately excluded -- they cause false hits in "shi fen gan xie" (many thanks);
# time-related fen/miao are handled separately by the strict _TIME_RE pattern.
# bai/qian are also deliberately excluded -- they are internal numeral units (liang bai ling san = 203);
# treating them as quantifiers would split "liang bai ling san" into "2 bai ling san".
_SAFE_UNITS = (
    "年", "月", "日", "号", "点", "元", "块", "毛", "角",
    "个", "件", "台", "人", "岁", "米", "吨", "度", "倍",
    "层", "楼", "间", "天", "小时", "分钟", "周", "星期",
    "页", "行", "次", "条", "张", "本", "公斤", "公里", "克", "万元",
)
_UNIT_ALT = "|".join(sorted(_SAFE_UNITS, key=len, reverse=True))

# Big units (wan/yi): counted as a quantity only when followed by a quantifier, punctuation, a digit or end of string.
# Look-ahead relaxed (2026-11-20) to cover the model's actual output:
#   "yi qian liang bai san shi si wan 5000 yuan"     wan followed by Arabic digits
#   "yi bai er shi wan yi yuan"                     wan followed by yi (wan-yi in sequence)
#   "ba wan wu" (= 85000)                           wan followed by a single Chinese digit
_BIG_UNIT_RE = re.compile(
    rf"({_NUM_PAT})(?=[万亿]{{1,2}}(?:{_UNIT_ALT}|[0-9]|[{_CN_NUM_CHARS}]|多|余|来|[，。！？、；：,\.\)）\s]|$))"
)

_PERCENT_RE = re.compile(rf"百分之({_NUM_PAT})")
_TIME_RE = re.compile(rf"({_NUM_PAT})点({_NUM_PAT})分(?:(?:零)?({_NUM_PAT})秒)?")
_HALF_RE = re.compile(rf"({_NUM_PAT})点半")
_UNIT_RE = re.compile(rf"({_NUM_PAT})(?=(?:{_UNIT_ALT}))")

# Approximate count: "er shi ji ge" -> "20 ji ge" ("ji" is kept; only the definite part before it converts)
_APPROX_RE = re.compile(rf"({_NUM_PAT})(?=几)")

# Ordinals: di-2 duan / di-3 zhang / di-4 tiao -- only the "di" prefix counts, so "yi duan shi jian" (some time) is untouched
_ORDINAL_RE = re.compile(
    rf"第({_NUM_PAT})(?=[段章节条款项届期名位次页行句字批轮场部集首篇档])"
)

# Decimal + Latin-letter unit: 4 dian qi G / 0 dian wu M (the model outputs 4.7GB as "4 dian qi G B")
# Both starting forms are needed: Chinese digit (si dian qi G) and Arabic (4 dian qi G)
_DEC_LATIN_RE = re.compile(
    rf"((?:[{_CN_NUM_CHARS}]+|[0-9]+)点[{_CN_NUM_CHARS}]+)(?=[A-Za-z])"
)

# wan/yi + unit digit (colloquial ellipsis): ba wan wu = 85000, written 8 wan 5
# Only applies when followed by a quantifier/punctuation/end; "ba wan wu qian" does not match (qian follows wu, not a quantifier)
_WAN_UNIT_RE = re.compile(
    rf"({_NUM_PAT})([万亿])([{_CN_NUM_CHARS}])(?={_UNIT_ALT}|[，。！？、；：,\.\)）\s]|$)"
)

# Letter-by-letter abbreviations: merge "G B" "A S R" "U S B" back into "GB" "ASR" "USB"
_SPACED_ACRONYM_RE = re.compile(r"(?<![A-Za-z])(?:[A-Z] ){1,}[A-Z]\b")

# Number strings: a digit run after an explicit prefix (order no. / ticket no. / ID / phone / mobile / account)
_NUMERIC_ID_RE = re.compile(
    r"(订单号|单号|编号|工号|电话|手机号?|账号|卡号|身份证号|合同号)"
    r"(?:是|为|：|:)?\s*([零〇一二三四五六七八九幺洞]{5,})"
)

# Number-only mapping: on the phone "yao" is 1 and "dong" is 0
_ID_DIGIT = dict(_CN_DIGIT, 幺=1, 洞=0)


def _id_bitwise(s: str) -> int | None:
    out = []
    for ch in s:
        if ch not in _ID_DIGIT:
            return None
        out.append(str(_ID_DIGIT[ch]))
    return int("".join(out)) if out else None

# Protected words: not converted at all (replaced by placeholders, then restored)
_PROTECT = (
    "千万", "万万", "十万火急", "万一", "万分", "万分之",
    "一起", "一般", "一直", "一定", "一切", "一旦", "一致", "一律",
    "一半", "一边", "一向", "一贯", "一番", "一并", "一再", "一共",
    "一点儿", "一会儿", "一些", "一样", "一但", "一心", "一味", "一个人",
    "有一点", "有的人", "有没有", "十有八九",
)


def merge_spaced_acronyms(text: str) -> str:
    """Collapse letter-by-letter abbreviations: "A S R" -> "ASR", "4.7G B" -> "4.7GB".

        Qwen3-ASR outputs abbreviations letter by letter (a known verbatim-transcription artifact).
        This is a pure typesetting fix that changes no digits, so it is not governed by the ITN switch.
    """
    if not text:
        return text
    return _SPACED_ACRONYM_RE.sub(lambda m: m.group(0).replace(" ", ""), text)


def normalize(text: str) -> str:
    """Normalize Chinese numerals. Conservative strategy: better to leave it than to corrupt it."""
    if not text:
        return text

    # 0) Protected-word placeholders (invisible sentinels, so they cannot clash with the body)
    saved: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    pat = "|".join(re.escape(w) for w in sorted(_PROTECT, key=len, reverse=True))
    text = re.sub(pat, _stash, text)

    # 1) "percent X" pattern -> X%
    text = _PERCENT_RE.sub(
        lambda m: (f"{cn_to_number(m.group(1))}%" if cn_to_number(m.group(1)) else m.group(0)),
        text,
    )

    # 2) Number strings: order no. er-ling-er-liu-ling-yao-yao-wu -> order no. 20260115
    def _idnum2(m: re.Match[str]) -> str:
        v = _id_bitwise(m.group(2))
        if v is None:
            return m.group(0)
        sep = m.group(0)[len(m.group(1)) : len(m.group(0)) - len(m.group(2))]
        return f"{m.group(1)}{sep}{v}"

    text = _NUMERIC_ID_RE.sub(_idnum2, text)

    # 3) X dian Y fen (Z miao) / X dian ban
    def _time(m: re.Match[str]) -> str:
        h, mi, se = cn_to_number(m.group(1)), cn_to_number(m.group(2)), (
            cn_to_number(m.group(3)) if m.group(3) else None
        )
        if h is None or mi is None:
            return m.group(0)
        out = f"{h}点{mi}分"
        return out + (f"{se}秒" if se is not None else "")

    text = _TIME_RE.sub(_time, text)
    text = _HALF_RE.sub(
        lambda m: (f"{cn_to_number(m.group(1))}点半" if cn_to_number(m.group(1)) else m.group(0)),
        text,
    )

    # 4) Numeral + big unit (wan/yi); the look-ahead constraint already guarantees a real quantity
    def _big(m: re.Match[str]) -> str:
        v = cn_to_number(m.group(1))
        return v if v is not None else m.group(0)

    # * wan/yi + unit digit (ba wan wu -> 8 wan 5) must run before _BIG_UNIT_RE:
    #   otherwise "ba" becomes 8 first and _WAN_UNIT_RE can no longer match a Chinese digit
    def _wan_unit(m: re.Match[str]) -> str:
        a, b, c = m.group(1), m.group(2), m.group(3)
        av, cv = cn_to_number(a), cn_to_number(c)
        if av is None or cv is None:
            return m.group(0)
        return f"{av}{b}{cv}"

    text = _WAN_UNIT_RE.sub(_wan_unit, text)
    text = _BIG_UNIT_RE.sub(_big, text)

    # 5) Numeral + quantifier
    text = _UNIT_RE.sub(_big, text)

    # 5') Approximate count: er shi ji ge -> 20 ji ge
    text = _APPROX_RE.sub(_big, text)

    # 5'') Ordinals: di-2 duan -> di 2 duan ("di" kept)
    text = _ORDINAL_RE.sub(lambda m: "第" + (_big(m) if cn_to_number(m.group(1)) is not None else m.group(1)), text)

    # 5''') Decimal + Latin letters: 4 dian qi G -> 4.7G
    text = _DEC_LATIN_RE.sub(_big, text)

    # 5'''') wan/yi + unit digit is already handled before step 4; not repeated here

    # 5'''') Letter-by-letter abbreviation merge: G B -> GB (known Qwen3-ASR verbatim behavior)
    text = merge_spaced_acronyms(text)

    # 6) Restore protected words
    def _restore(m: re.Match[str]) -> str:
        return saved[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, text)


if __name__ == "__main__":
    CASES = [
        # --- Should convert ---
        "三月十五号上午十点半，预算一百二十五万元，完成率百分之八十七。",
        "会议定在二零二六年十二月三十一日下午三点二十分。",
        "这批货一共三千四百五十六件，单价八十九块七毛。",
        "订单号是二零二六零幺幺五，电话幺三八零零幺三八零零零。",
        "增长率百分之十二点五，温度零下三点五度。",
        "一共有三个方案，第一个和第二个各占一半。",
        "请在第两百零三页签个字。",
        "用时一小时三十分钟，走了十五公里。",
        # --- Must not touch (easy false hits) ---
        "我们一起去，万一有事，十分感谢，千万别忘。",
        "这个方案一般，先一直这样做，考虑一半就行。",
        "他说了一定，一切照旧，一旦开始就一致执行。",
        "三分之一的人反对，四分之三同意。",
        "这件事万分重要，务必十万火急处理。",
        "十分之一的可能性都没有。",
    ]
    ok = bad = 0
    for t in CASES:
        out = normalize(t)
        changed = out != t
        print(("  改 " if changed else "= 原 ") + out)
