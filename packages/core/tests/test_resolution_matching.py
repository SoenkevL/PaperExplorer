from paperexplorer.parsing import Reference
from paperexplorer.resolution import Candidate, DoiConflict, Level, Match, match


def _ref(
    *,
    title: str | None = None,
    surnames: list[str] | None = None,
    year: str | None = None,
    doi: str | None = None,
) -> Reference:
    return Reference(
        id="b0",
        text="as printed",
        page=1,
        surnames=surnames or [],
        year=year,
        title=title,
        doi=doi,
    )


def test_printed_doi_a_candidate_holds_is_certain() -> None:
    ref = _ref(title="Another", doi="https://doi.org/10.1000/ABC.")
    candidates = [
        Candidate(title="Unrelated", surnames=["Smith"], year=2020, doi="10.1000/xyz"),
        Candidate(title="Another", surnames=["Jones"], year=2021, doi="10.1000/abc"),
    ]

    found = match(ref, candidates)

    assert isinstance(found, Match)
    assert (found.candidate, found.level, found.evidence) == (1, Level.CERTAIN, ["doi"])


ATTENTION = Candidate(
    title="Attention Is All You Need: Transformers for Sequence Transduction",
    surnames=["Vaswani"],
    year=2017,
    doi="10.5555/attention",
)


def test_exact_title_after_normalization_with_first_surname_is_high() -> None:
    ref = _ref(
        title="ATTENTION is all you need — transformers for séquence transduction!",
        surnames=["Vaswani", "Shazeer"],
        year="2018",
    )

    found = match(ref, [ATTENTION])

    assert isinstance(found, Match)
    assert (found.level, found.evidence) == (Level.HIGH, ["title", "surname"])


def test_exact_title_with_year_alone_is_high() -> None:
    ref = _ref(title=ATTENTION.title, surnames=["Vasvani"], year="2017a")

    found = match(ref, [ATTENTION])

    assert isinstance(found, Match)
    assert (found.level, found.evidence) == (Level.HIGH, ["title", "year"])


def test_missing_subtitle_is_high_when_surname_and_year_agree() -> None:
    ref = _ref(title="Attention is all you need", surnames=["Vaswani"], year="2017")

    found = match(ref, [ATTENTION])

    assert isinstance(found, Match)
    assert (found.level, found.evidence) == (Level.HIGH, ["main_title", "surname", "year"])


def test_missing_subtitle_with_only_the_year_is_no_match() -> None:
    ref = _ref(title="Attention is all you need", surnames=["Parmar"], year="2017")

    assert match(ref, [ATTENTION]) is None


def test_wrong_year_near_miss_is_low() -> None:
    ref = _ref(
        title="Attention is all you need: transformer for sequence transduction",
        surnames=["Smith"],
        year="2009",
    )

    found = match(ref, [ATTENTION])

    assert isinstance(found, Match)
    assert (found.level, found.evidence) == (Level.LOW, ["near_title"])


def test_a_title_never_overrules_a_printed_doi() -> None:
    # A preprint and its published Version often share a title.
    ref = _ref(title=ATTENTION.title, surnames=["Vaswani"], year="2017", doi="10.48550/arxiv.1")

    assert match(ref, [ATTENTION]) is None


def test_printed_doi_the_library_lacks_still_matches_a_candidate_without_doi() -> None:
    candidate = ATTENTION.model_copy(update={"doi": None})
    ref = _ref(title=ATTENTION.title, surnames=["Vaswani"], doi="10.48550/arxiv.1")

    found = match(ref, [candidate])

    assert isinstance(found, Match)
    assert found.level == Level.HIGH


def test_the_best_level_wins_over_order() -> None:
    near = ATTENTION.model_copy(
        update={
            "title": "Attention is all you need: transformers for sequence transductions",
            "doi": None,
        }
    )
    ref = _ref(title=ATTENTION.title, surnames=["Vaswani"], year="2017")

    found = match(ref, [near, ATTENTION])

    assert isinstance(found, Match)
    assert (found.candidate, found.level) == (1, Level.HIGH)


def test_no_title_and_no_doi_is_no_match() -> None:
    assert match(_ref(surnames=["Vaswani"], year="2017"), [ATTENTION]) is None


def test_a_garbage_string_is_no_match() -> None:
    ref = _ref(title="pp. 1-12 ;; ibid.", surnames=["Vaswani"], year="2017")

    assert match(ref, [ATTENTION]) is None


def test_no_candidates_is_no_match() -> None:
    assert match(_ref(title=ATTENTION.title, doi="10.5555/attention"), []) is None


UNRELATED = Candidate(
    title="Deep residual learning for image recognition",
    surnames=["He"],
    year=2016,
    doi="10.5555/resnet",
)


def test_a_doi_that_agrees_despite_a_spelling_slip_is_certain() -> None:
    ref = _ref(
        title="Atention is all you need: transformers for sequence transduction",
        doi="10.5555/attention",
    )

    found = match(ref, [ATTENTION])

    assert found == Match(candidate=0, level=Level.CERTAIN, evidence=["doi"])


def test_a_doi_alone_has_nothing_to_disagree_with() -> None:
    assert match(_ref(doi="10.5555/attention"), [ATTENTION]) == Match(
        candidate=0, level=Level.CERTAIN, evidence=["doi"]
    )


def test_a_neighbours_doi_loses_to_the_title_capped_at_low() -> None:
    # The printed DOI is a neighbour's: it names a Work its title does not.
    ref = _ref(title=ATTENTION.title, surnames=["Vaswani"], year="2017", doi="10.5555/resnet")

    found = match(ref, [UNRELATED, ATTENTION])

    assert found == Match(
        candidate=1,
        level=Level.LOW,
        evidence=["title", "surname", "year", "doi_conflict"],
        doi_conflict=0,
    )


def test_a_doi_conflict_with_no_title_match_keeps_the_dois_work_as_the_option() -> None:
    ref = _ref(title="A title the Library does not hold", doi="10.5555/resnet")

    assert match(ref, [UNRELATED, ATTENTION]) == DoiConflict(candidate=0)


def test_without_a_title_the_doi_must_agree_with_surname_or_year() -> None:
    disagrees = _ref(surnames=["Vaswani"], year="2017", doi="10.5555/resnet")
    agrees = _ref(surnames=["Smith"], year="2016", doi="10.5555/resnet")

    assert match(disagrees, [UNRELATED]) == DoiConflict(candidate=0)
    assert match(agrees, [UNRELATED]) == Match(candidate=0, level=Level.CERTAIN, evidence=["doi"])


def test_a_title_matchs_confidence_is_the_titles_similarity() -> None:
    ref = _ref(title="Attention is all you need: transformer for sequence transduction")

    found = match(ref, [ATTENTION])

    assert isinstance(found, Match)
    assert 0.9 <= found.confidence < 1
