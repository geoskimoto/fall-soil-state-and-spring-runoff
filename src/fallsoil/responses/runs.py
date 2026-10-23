"""Run detection on boolean masks."""
from __future__ import annotations

import numpy as np


def runs_of_true(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True as half-open (start, stop) index pairs."""
    padded = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]
