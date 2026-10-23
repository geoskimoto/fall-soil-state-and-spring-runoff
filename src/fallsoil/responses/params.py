"""Tunable parameters of the response metrics. Every threshold lives here, none is hard-coded elsewhere."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, fields

_LAST_SPRING_MD = (7, 31)  # the spring-pulse window (calendar year of the WY, Jan 1 - Jul 31) ends on Jul 31


def _check_month_day(md) -> tuple[int, int]:
    try:
        month, day = md
        month, day = int(month), int(day)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"spring_pulse_start must be a (month, day) pair, got {md!r}") from exc
    # 2001 is not a leap year, so Feb 29 is rejected: the window must exist in every water year.
    if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(2001, month)[1]):
        raise ValueError(f"spring_pulse_start {md!r} is not a valid (month, day)")
    if (month, day) > _LAST_SPRING_MD:
        raise ValueError(f"spring_pulse_start {md!r} must lie between Jan 1 and Jul 31")
    return month, day


@dataclass(frozen=True)
class ResponseParams:
    """Parameters of `wy_metrics` / `compute_responses`; validated at construction."""

    min_wy_valid_days: int = 350
    min_window_fraction: float = 0.95
    spring_pulse_start: tuple[int, int] = (2, 1)  # (month, day); the window ends Jul 31
    eckhardt_alpha: float = 0.98
    eckhardt_bfi_max: float = 0.80
    lh_alpha: float = 0.925
    lh_passes: int = 3
    recession_min_days: int = 7  # strictly decreasing steps for a falling limb to qualify
    recession_skip_days: int = 2  # decreasing days dropped after the peak before fitting
    bn_min_points: int = 10
    bn_min_r2: float = 0.8

    def __post_init__(self):
        if not 1 <= self.min_wy_valid_days <= 366:
            raise ValueError(f"min_wy_valid_days must be in 1..366, got {self.min_wy_valid_days}")
        if not 0 < self.min_window_fraction <= 1:
            raise ValueError(f"min_window_fraction must be in (0, 1], got {self.min_window_fraction}")
        for name in ("eckhardt_alpha", "lh_alpha"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0, 1), got {getattr(self, name)}")
        if not 0 < self.eckhardt_bfi_max <= 1:
            raise ValueError(f"eckhardt_bfi_max must be in (0, 1], got {self.eckhardt_bfi_max}")
        if self.lh_passes < 1:
            raise ValueError(f"lh_passes must be >= 1, got {self.lh_passes}")
        if self.recession_min_days < 2:
            raise ValueError(f"recession_min_days must be >= 2, got {self.recession_min_days}")
        if self.recession_skip_days < 0:
            raise ValueError(f"recession_skip_days must be >= 0, got {self.recession_skip_days}")
        object.__setattr__(self, "spring_pulse_start", _check_month_day(self.spring_pulse_start))

    def as_dict(self) -> dict:
        """JSON-serialisable view (tuples become lists); stored in the output metadata."""
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["spring_pulse_start"] = list(out["spring_pulse_start"])
        return out
