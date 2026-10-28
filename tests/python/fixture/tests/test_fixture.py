from fixture import laps


def test_laps() -> None:
    assert laps([3, 4]) == 7
