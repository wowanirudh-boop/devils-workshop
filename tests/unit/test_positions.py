import pytest

from devils_workshop.project.positions import Position, parse_position


@pytest.mark.parametrize("text, expected", [
    ("ch5", Position(5, 0)),
    ("@ch5", Position(5, 0)),
    ("ch5.2", Position(5, 2)),
    ("@CH12.3", Position(12, 3)),
    ("chapter 7", Position(7, 0)),
    (" @ ch 4 . 1 ", Position(4, 1)),
])
def test_parse_valid(text, expected):
    assert parse_position(text) == expected


@pytest.mark.parametrize("text", ["5", "ch", "ch0", "ch5.0", "scene 2", "ch5.2.1", "chx"])
def test_parse_invalid(text):
    with pytest.raises(ValueError):
        parse_position(text)


def test_reading_order():
    assert Position(4, 3) < Position(5) < Position(5, 2) < Position(6)
    assert sorted([Position(6), Position(5, 2), Position(5)]) == [Position(5), Position(5, 2), Position(6)]


def test_text_forms():
    assert str(Position(5)) == "ch5"
    assert str(Position(5, 2)) == "ch5.2"
    assert Position(5, 2).label() == "chapter 5, scene 2"
