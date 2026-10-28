"""The fixture's one function, which its test covers in full."""


def laps(stints: list[int]) -> int:
    """The laps run over the stints."""
    return sum(stints)
