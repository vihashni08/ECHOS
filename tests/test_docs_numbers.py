from scripts.docgen.check_docs_numbers import check


def test_docs_numbers_match_metrics():
    assert check() == []
