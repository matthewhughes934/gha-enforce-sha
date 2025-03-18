from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Generator, NamedTuple

import pytest

from gha_enforce_sha.git import must_run_git_cmd
from gha_enforce_sha.main import main


@contextmanager
def as_cwd(path: Path) -> Generator[None]:
    cwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)


VALID_FILES = {
    "single_job_single_step": dedent(
        """\
        jobs:
            just-checkout:
                steps:
                    - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        """,
    ),
    "single_action_single_step": dedent(
        """\
        runs:
            just-checkout:
                steps:
                    - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        """,
    ),
    "single_job_single_step_no_uses": dedent(
        """\
        jobs:
            echo-stuff:
                steps:
                    - runs: echo 'just some text'
        """,
    ),
    "single_job_single_step_local_uses": dedent(
        """\
        jobs:
            echo-stuff:
                steps:
                    - uses: ./some/local/action
        """,
    ),
    "single_job_single_step_docker_uses": dedent(
        """\
        jobs:
            echo-stuff:
                steps:
                    - uses: docker://my-image:my-tag
        """,
    ),
    "single_job_single_step_no_version": dedent(
        """\
        jobs:
            just-checkout:
                steps:
                    - uses: actions/checkout
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


@pytest.mark.parametrize(
    "yaml_content",
    (pytest.param(value, id=key) for key, value in VALID_FILES.items()),
)
def test_actions_with_shas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], yaml_content: str
) -> None:
    path = tmp_path / "file.yaml"
    path.write_text(yaml_content)
    args = ("check", str(path))

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 0
    assert captured.out == ""
    assert captured.err == ""


class InvalidCase(NamedTuple):
    yaml_content: str
    report_details: list[str]


INVALID_CASES = {
    "single_job_single_step_major_version": InvalidCase(
        yaml_content=dedent(
            """\
            jobs:
                just-checkout:
                    steps:
                        - uses: actions/checkout@v4
            """,
        ),
        report_details=["in job just-checkout: in step #1: actions/checkout@v4"],
    ),
    "single_action_single_step_major_version": InvalidCase(
        yaml_content=dedent(
            """\
            runs:
                just-checkout:
                    steps:
                        - uses: actions/checkout@v4
            """,
        ),
        report_details=["in job just-checkout: in step #1: actions/checkout@v4"],
    ),
    "single_job_single_step_full_tag": InvalidCase(
        yaml_content=dedent(
            """\
            jobs:
                just-checkout:
                    steps:
                        - uses: actions/checkout@v4.2.2
            """,
        ),
        report_details=["in job just-checkout: in step #1: actions/checkout@v4.2.2"],
    ),
    "single_job_multiple_step": InvalidCase(
        yaml_content=dedent(
            """\
            jobs:
                just-checkout:
                    steps:
                        - uses: actions/checkout@v4
                        - uses: actions/setup-python@v4
            """,
        ),
        report_details=[
            "in job just-checkout: in step #1: actions/checkout@v4",
            "in job just-checkout: in step #2: actions/setup-python@v4",
        ],
    ),
}


@pytest.mark.parametrize(
    "case",
    (pytest.param(value, id=key) for key, value in INVALID_CASES.items()),
)
def test_actions_without_shas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: InvalidCase
) -> None:
    path = tmp_path / "file.yaml"
    path.write_text(case.yaml_content)
    args = ("check", str(path))

    expected_report = (
        "\n".join(
            f"in workflow file {path}: {report_detail}"
            for report_detail in case.report_details
        )
        + "\n"
    )

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.err == expected_report
    assert captured.out == ""


def test_actions_without_shas_multiple_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file_map = {
        ".github/workflows/first.yaml": INVALID_CASES[
            "single_job_single_step_major_version"
        ],
        ".github/workflows/second.yaml": INVALID_CASES[
            "single_job_single_step_full_tag"
        ],
    }

    # path iteration order is not defined, so store expected elements
    expected_report_parts = []
    for rel_path, case in file_map.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case.yaml_content)

        expected_report_parts.append(
            "\n".join(
                f"in workflow file {rel_path}: {report_detail}"
                for report_detail in case.report_details
            )
        )

    with as_cwd(tmp_path):
        return_code = main(["check"])

    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.out == ""
    # len of each entry + one newline for each entry
    assert len(captured.err) == sum(map(len, expected_report_parts)) + len(
        expected_report_parts
    )
    assert all(report in captured.err for report in expected_report_parts)


def test_errors_on_non_workflow_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_content = "not-a-workflow: 1"
    path = tmp_path / "file.yaml"
    path.write_text(yaml_content)
    args = ("check", str(path))

    expected_err = "Error: " + str(path) + " does not look like a workflow or action\n"

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.err == expected_err
    assert captured.out == ""


def test_errors_on_job_with_no_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_content = dedent(
        """\
        jobs:
            job-missing-steps:
                name: Bad job
    """
    )
    path = tmp_path / "file.yaml"
    path.write_text(yaml_content)
    args = ("check", str(path))

    expected_err = (
        "Error: cannot process job (name=job-missing-steps) in "
        + str(path)
        + ": job has no steps\n"
    )

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.err == expected_err
    assert captured.out == ""


def test_errors_on_invalid_default_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_error = "Error: cannot list paths in '.github/workflows': it doesn't exist or isn't a directory\n"

    with as_cwd(tmp_path):  # some path with not .github/
        return_code = main(["check"])

    captured = capsys.readouterr()

    assert return_code == 1
    assert captured.err == expected_error
    assert captured.out == ""


def create_repo(repo_path: Path, tags: Iterable[str]) -> dict[str, str]:
    must_run_git_cmd("init", str(repo_path))

    commits = tuple(create_commit(repo_path) for _ in range(len(tags)))
    for commit, tag in zip(commits, tags):
        must_run_git_cmd("-C", str(repo_path), "tag", commit, tag)

    return dict(zip(tags, commits))


def create_commit(repo_path: Path) -> str:
    must_run_git_cmd(
        "-C", str(repo_path), "commit", "--allow-empty", "allow-empty-message"
    )
    return must_run_git_cmd("-C", str(repo_path), "rev", "parse", "HEAD")


def test_resolving_tags(tmp_path: Path) -> None:
    action_repo = "github.com/fake-user/fake-repo"
    tags = ("v1.0.0", "v1.1.0", "v1.1.1")
    tag_resolution = {
        "v1": "v1.1.1",
        "v1.0": "v1.0.0",
        "v1.1": "v1.1.1",
        "v1.1.1": "v1.1.1",
        "v2": "v2.0.0",
    }

    workflows = {
        "first.yaml": dedent(
            f"""\
            jobs:
                first:
                    steps:
                     - uses: f{action_repo}@v1
                     - uses: f{action_repo}@v1.0
                     - uses: f{action_repo}@v1.1
                     - uses: f{action_repo}@v1.1.1
            """,
        ),
        "second.yaml": dedent(
            f"""\
                first:
                    steps:
                    - uses: {action_repo}@v2
                    - uses: {action_repo}@v1
            """,
        ),
    }

    expected_workflows = {
        "first.yaml": dedent(
            f"""\
            jobs:
                first:
                    steps:
                     - uses: f{action_repo}@v1
                     - uses: f{action_repo}@v1.0
                     - uses: f{action_repo}@v1.1
                     - uses: f{action_repo}@v1.1.1
            """,
        ),
        "second.yaml": dedent(
            f"""\
                first:
                    steps:
                    - uses: {action_repo}@v2
                    - uses: {action_repo}@v1
            """,
        ),
    }

    repo = tmp_path / "repo"
    create_repo(repo, tags)
