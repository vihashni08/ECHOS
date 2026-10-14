"""Age band from Demographics.csv ("(80, 90]") to a short label. The dataset's top band "(90, 110]" is open-ended (110 is only a bin edge, not an age), shown as "90+"."""
from __future__ import annotations

import re


def band_label(raw) -> str:
    nums = re.findall(r"\d+", str(raw))
    if len(nums) < 2:
        return "unknown"
    lo, hi = int(nums[0]), int(nums[1])
    return f"{lo}+" if hi >= 100 else f"{lo}-{hi}"
