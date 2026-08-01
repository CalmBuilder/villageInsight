from pathlib import Path

from village_insight.question_benchmark import load_question_benchmark


def test_real_question_benchmark_is_read_without_styles() -> None:
    path = Path("docs/datafiles/济南院-查村情测试清单.xlsx")

    corpus = load_question_benchmark(path)

    assert (
        corpus.workbook_sha256
        == "13949b4d8e9c453fb1d8eb66ce4f83bddf1bcfb5893fe622004e5bb6626d87bc"
    )
    assert corpus.sheet_count == 12
    assert corpus.question_sheet_count == 11
    assert corpus.question_row_count == 482
    assert corpus.unique_question_count == 237
    assert corpus.excluded_non_question_rows == 67
    assert sum(len(case.occurrences) for case in corpus.cases) == 482
    assert all(
        occurrence.source_row > 1
        for case in corpus.cases
        for occurrence in case.occurrences
    )
    assert all(case.normalized_question for case in corpus.cases)
