import pytest
from activist.confidence.basic import shrink_to_mean, final_probability, position_size, logistic


def test_shrink():
    assert shrink_to_mean(0.2, 0, 0.05) == pytest.approx(0.05)
    assert shrink_to_mean(0.2, 5, 0.05) == pytest.approx(0.125)
    assert shrink_to_mean(0.2, 10, 0.05) == pytest.approx(0.2)
    assert shrink_to_mean(0.2, 100, 0.05) == pytest.approx(0.2)


def test_final_probability():
    assert final_probability(0.8, 0.5, 1.0) == pytest.approx(0.8)
    assert final_probability(0.8, 0.5, 0.0) == pytest.approx(0.5)
    assert final_probability(0.8, 0.5, 0.5) == pytest.approx(0.65)
    assert final_probability(0.8, 0.5, 2.0) == pytest.approx(0.8)    # Klammerung
    assert final_probability(0.8, 0.5, -1.0) == pytest.approx(0.5)


def test_logistic():
    assert logistic(0) == 0.5


@pytest.mark.parametrize("exp,lo,n,size", [
    (0.02, 0.01, 100, 0.0),
    (0.08, -0.01, 100, 0.0),
    (0.04, 0.01, 100, 0.025),
    (0.07, 0.01, 100, 0.05),
    (0.12, 0.01, 30, 0.05),
    (0.12, 0.01, 50, 0.08),
    (0.03, 0.0, 10, 0.025),
])
def test_position_size(exp, lo, n, size):
    assert position_size(exp, lo, n) == size
