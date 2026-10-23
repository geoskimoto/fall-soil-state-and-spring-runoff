"""M2a: Eckhardt and Lyne-Hollick baseflow filters (interface: tests/INTERFACE_M2A.md)."""
import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from fallsoil.responses import eckhardt_baseflow, lyne_hollick_baseflow

FILTERS = [
    pytest.param(eckhardt_baseflow, id="eckhardt"),
    pytest.param(lyne_hollick_baseflow, id="lyne_hollick"),
]

flow_arrays = st.lists(st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
                       min_size=2, max_size=300).map(lambda v: np.array(v, dtype=float))

BASE, SPIKE = 2.0, 20.0
SPIKE_DAYS = (50, 300)


def spiky(n=400):
    q = np.full(n, BASE)
    for i in SPIKE_DAYS:
        q[i] += SPIKE
    return q


# ---------------------------------------------------------------- shape / bounds
@pytest.mark.parametrize("f", FILTERS)
def test_output_shape_dtype_and_bounds_on_spiky_series(f):
    q = spiky()
    b = f(q)
    assert isinstance(b, np.ndarray) and b.dtype == np.float64 and b.shape == q.shape
    assert np.all(np.isfinite(b))
    assert np.all(b >= 0.0)
    assert np.all(b <= q + 1e-12)


@pytest.mark.parametrize("f", FILTERS)
def test_input_not_mutated(f):
    q = spiky()
    before = q.copy()
    f(q)
    assert np.array_equal(q, before)


@pytest.mark.parametrize("f", FILTERS)
@pytest.mark.parametrize("bad", [[], [1.0, float("nan"), 2.0], [1.0, -0.5, 2.0], [1.0, float("inf")]])
def test_invalid_input_raises_value_error(f, bad):
    with pytest.raises(ValueError):
        f(np.array(bad, dtype=float))


@pytest.mark.parametrize("f", FILTERS)
@settings(max_examples=60, deadline=None)
@given(q=flow_arrays)
def test_baseflow_between_zero_and_flow(f, q):
    b = f(q)
    assert b.shape == q.shape and np.all(np.isfinite(b))
    assert np.all(b >= 0.0)
    assert np.all(b <= q * (1 + 1e-12) + 1e-12)
    total = q.sum()
    if total > 0:
        assert 0.0 <= b.sum() / total <= 1.0 + 1e-12


@pytest.mark.parametrize("f", FILTERS)
@settings(max_examples=40, deadline=None)
@given(q=flow_arrays, c=st.floats(min_value=0.01, max_value=100.0))
def test_positive_homogeneity(f, q, c):
    # Both recursions are linear apart from min/max against the flow itself, which commute with scaling.
    np.testing.assert_allclose(f(c * q), c * f(q), rtol=1e-9, atol=1e-9 * (1 + c))


# ---------------------------------------------------------------- constant flow (derived from the recursions)
def test_eckhardt_constant_flow_tends_to_bfi_max():
    # steady state b = (1-B)*a*b + (1-a)*B*c all over (1-a*B)  =>  b = B*c   (B = BFImax)
    q = np.full(1000, 5.0)
    b = eckhardt_baseflow(q, alpha=0.98, bfi_max=0.80)
    assert b[-1] == pytest.approx(0.80 * 5.0, rel=1e-6)
    # any sane initialisation (b0 between B*c and c) keeps the year-mean BFI within 0.01 of B over 1000 days
    assert b.sum() / q.sum() == pytest.approx(0.80, abs=0.01)


@pytest.mark.parametrize("bfi_max", [0.5, 0.8, 0.95])
def test_eckhardt_steady_state_follows_bfi_max(bfi_max):
    q = np.full(2000, 3.0)
    assert eckhardt_baseflow(q, alpha=0.98, bfi_max=bfi_max)[-1] == pytest.approx(bfi_max * 3.0, rel=1e-5)


def test_lyne_hollick_constant_flow_is_all_baseflow():
    q = np.full(365, 5.0)
    np.testing.assert_allclose(lyne_hollick_baseflow(q), q, rtol=1e-6)


# ---------------------------------------------------------------- baseflow plus known storm spikes
@pytest.mark.parametrize("f", FILTERS)
def test_spike_days_are_mostly_quickflow(f):
    q = spiky()
    b = f(q)
    for i in SPIKE_DAYS:
        assert b[i] < q[i]
        assert b[i] < 0.5 * q[i]  # >half of a 10x storm spike must be removed as quickflow


def test_eckhardt_relaxes_to_bfi_max_times_base_far_from_spikes():
    # between the spikes (>=110 days from each) the recursion has relaxed (pole ~0.907/day, 0.907^110 ~ 2e-5)
    b = eckhardt_baseflow(spiky(), alpha=0.98, bfi_max=0.80)
    far = b[160:190]
    np.testing.assert_allclose(far, 0.80 * BASE, rtol=1e-2)


def test_lyne_hollick_returns_to_base_far_from_spikes():
    # filtered quickflow decays like alpha^t: 0.925^110 ~ 2e-4 times <=20 -> well inside 2 percent of BASE
    b = lyne_hollick_baseflow(spiky())
    np.testing.assert_allclose(b[160:190], BASE, rtol=2e-2)


def test_lyne_hollick_bfi_drops_when_storms_are_added():
    flat = lyne_hollick_baseflow(np.full(400, BASE))
    storm = lyne_hollick_baseflow(spiky())
    assert flat.sum() / (400 * BASE) == pytest.approx(1.0, abs=1e-6)
    assert storm.sum() / spiky().sum() < 0.999
    assert 0.0 < storm.sum() / spiky().sum() < 1.0


def test_lh_pass_count_matters_and_is_respected():
    q = spiky()
    one = lyne_hollick_baseflow(q, alpha=0.925, passes=1)
    three = lyne_hollick_baseflow(q, alpha=0.925, passes=3)
    assert not np.allclose(one, three)
    # more passes remove more quickflow: smoother baseflow, never above flow
    assert np.all(three <= q + 1e-12) and np.all(one <= q + 1e-12)


def test_alpha_and_passes_validation():
    q = spiky()
    for bad in (0.0, 1.0, 1.5, -0.1):
        with pytest.raises(ValueError):
            lyne_hollick_baseflow(q, alpha=bad)
        with pytest.raises(ValueError):
            eckhardt_baseflow(q, alpha=bad)
    with pytest.raises(ValueError):
        lyne_hollick_baseflow(q, passes=0)
    with pytest.raises(ValueError):
        eckhardt_baseflow(q, bfi_max=0.0)
    with pytest.raises(ValueError):
        eckhardt_baseflow(q, bfi_max=1.5)
