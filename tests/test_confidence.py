import pytest
from activist.confidence.basic import shrink_to_mean


def test_shrink():
    assert shrink_to_mean(0.2, 0, 0.05) == pytest.approx(0.05)
    assert shrink_to_mean(0.2, 5, 0.05) == pytest.approx(0.125)
    assert shrink_to_mean(0.2, 10, 0.05) == pytest.approx(0.2)
    assert shrink_to_mean(0.2, 100, 0.05) == pytest.approx(0.2)
