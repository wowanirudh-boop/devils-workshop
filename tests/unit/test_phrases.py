"""The phrases a book keeps using, for the writer who never sees its other scenes (context/phrases.py)."""

from devils_workshop.context.phrases import overused

SCENES = [
    "Mara climbed the iron stair and her breath caught in her throat. The lamp room door stood open.",
    "Tobias waited by the lamp room door; her breath caught in her throat when she saw him.",
    "The gale beat the glass. Her breath caught in her throat again, and the lamp room door banged.",
    "She laughed, and her breath caught in her throat, and she could not stop.",
]


def test_a_phrase_the_book_keeps_using_is_listed_once_but_never_the_storys_own_things():
    found = overused(SCENES, own="The lamp room door is green; the keeper's cottage stands behind it.")
    assert found == ["her breath caught in her throat"]  # one form of it, the longest the book keeps using
    assert overused(SCENES[:2]) == []  # twice is a choice, not a tic
    assert "the lamp room door" in overused(SCENES)  # (were it not the story's own, named by the guide or a brief)


def test_a_line_the_briefs_keep_quoting_or_the_guides_own_phrasing_is_listed_but_never_the_storys_facts():
    from devils_workshop.context.phrases import own_words
    guide = ("## Voice and style\nHer fear shows as a thin keening, nothing left but noise.\n"
             "## Characters\nMara keeps the lamp room door locked.\n")
    briefs = ['Mara begs, "hold me tight, Tobias", by the lamp room door.'] * 3
    scenes = ['The gale beat the glass. "Hold me tight, Tobias," Mara begged, nothing left but noise.',
              'Tobias lit the wick. "Hold me tight, Tobias," she said by the lamp room door, nothing left but noise.',
              'Rain came. Nothing left but noise in her throat. "Hold me tight, Tobias." The lamp room door banged.',
              'She ran down, nothing left but noise, and Tobias heard "hold me tight, Tobias" from the lamp room door.']
    found = overused(scenes, own_words("Mara climbs the stair.", briefs, [], guide))
    assert any("hold me tight" in p for p in found) and "nothing left but noise" in found
    assert not any("lamp room" in p for p in found)  # the story's own place, in the briefs and the guide's facts
    found = overused(scenes, own_words(briefs[0], briefs, [], guide))
    assert not any("hold me tight" in p for p in found)  # this scene's brief gives the line: it is said


def test_a_thought_the_briefs_keep_giving_in_italics_is_worn_out_too():
    from devils_workshop.context.phrases import own_words
    briefs = ["Mara climbs; her mind runs *a keeper's daughter, alone at the lamp*, and she goes on."] * 4
    scenes = [f"{lead} A keeper's daughter, alone at the lamp. {tail}" for lead, tail in (
        ("The gale beat the glass.", "She trimmed the wick."), ("Tobias was late.", "The oil was low."),
        ("Rain came in sideways.", "She counted the ships."), ("The bell rang twice.", "Nobody answered it."))]
    found = overused(scenes, own_words("Mara climbs the stair.", briefs, []))
    assert any("keeper's daughter alone" in p for p in found)
