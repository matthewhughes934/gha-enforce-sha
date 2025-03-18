from pathlib import Path
from textwrap import dedent

import pytest

from gha_enforce_sha.main import main


def test_errors_on_invalid_path(tmp_path: Path) -> None:
    path = tmp_path / "not" / "exist"


VALID_FILES = {
    "single_job_single_step": dedent(
        """\
        jobs:
            just-checkout:
            steps:
                - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        """,
    ),
    "single_job_multiple_step": dedent(
        """\
        jobs:
            just-checkout:
            steps:
                - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
                - uses: actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38
        """,
    ),
}

INVALID_FILES = {
    "single_job_single_step_major_version": dedent(
        """\
        jobs:
            just-checkout:
            steps:
                - uses: actions/checkout@v4
        """,
    ),
    "single_job_single_step_full_tag": dedent(
        """\
        jobs:
            just-checkout:
            steps:
                - uses: actions/checkout@v4.2.2
        """,
    ),
    "single_job_multiple_step": dedent(
        """\
        jobs:
            just-checkout:
            steps:
                - uses: actions/checkout@v4
                - uses: actions/setup-python@v4
        """,
    ),
}


@pytest.mark.parametrize(
    "yaml_content",
    (pytest.param(value, id=key) for key, value in VALID_FILES.items()),
)
def test_actions_with_shas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], yaml_content: str
) -> None:
    path = tmp_path / "file.yaml"
    path.write_text(yaml_content)
    args = (str(path),)

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 0
    assert captured.out == ""
    assert captured.err == ""
