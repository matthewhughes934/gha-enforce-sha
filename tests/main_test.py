from __future__ import annotations

import os
import os.path
from collections.abc import Collection
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Generator, NamedTuple

import pytest

from gha_enforce_sha.git import must_run_git_cmd
from gha_enforce_sha.main import main


@contextmanager
def git_config(key: str, value: str) -> Generator[None]:
    # See https://git-scm.com/docs/git-config#ENVIRONMENT
    count_env_var = "GIT_CONFIG_COUNT"
    count = os.environ.get(count_env_var, "0")

    key_env_var = f"GIT_CONFIG_KEY_{count}"
    value_env_var = f"GIT_CONFIG_VALUE_{count}"

    try:
        os.environ[key_env_var] = key
        os.environ[value_env_var] = value
        os.environ[count_env_var] = str(int(count) + 1)
        yield
    finally:
        del os.environ[key_env_var]
        del os.environ[value_env_var]
        os.environ[count_env_var] = count


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
    "single_job_single_step_nested_action": dedent(
        """\
        jobs:
            just-checkout:
                steps:
                    - uses: some-user/some-repo/path/to/action@11bd71901bbe5b1630ceea73d27597364c9af683
        """,
    ),
    "single_job_no_steps": dedent(
        """\
        jobs:
            just-checkout:
                # TODO: validation should check this
                uses: octo-org/this-repo/.github/workflows/workflow-1.yml@172239021f7ba04fe7327647b213799853a9eb89
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
def test_checking_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], yaml_content: str
) -> None:
    path = tmp_path / "file.yaml"
    path.write_text(yaml_content)
    args = ("check", str(path))

    return_code = main(args)
    captured = capsys.readouterr()

    assert return_code == 0, captured.err
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
    "single_job_single_step_no_version": InvalidCase(
        yaml_content=dedent(
            """\
            jobs:
                just-checkout:
                    steps:
                        - uses: actions/checkout
            """,
        ),
        report_details=["in job just-checkout: in step #1: actions/checkout"],
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
def test_checking_failures(
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


def test_checking_failure_multiple_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file_map = {
        # implicitly test default file discovery
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

    assert return_code == 1, captured.err
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


def test_errors_on_invalid_default_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_error = "Error: cannot list paths in '.github/workflows': it doesn't exist or isn't a directory\n"

    with as_cwd(tmp_path):  # some path with not .github/
        return_code = main(["check"])

    captured = capsys.readouterr()

    assert return_code == 1, captured.err
    assert captured.err == expected_error
    assert captured.out == ""


def create_repo(repo_path: Path, tags: Collection[str]) -> dict[str, str]:
    must_run_git_cmd("init", str(repo_path))

    commits = tuple(create_commit(repo_path) for _ in range(len(tags)))
    for commit, tag in zip(commits, tags):
        must_run_git_cmd("-C", str(repo_path), "tag", tag, commit)

    return dict(zip(tags, commits))


def create_commit(repo_path: Path) -> str:
    must_run_git_cmd(
        "-C",
        str(repo_path),
        "commit",
        "--allow-empty",
        "--allow-empty-message",
        "--message",
        "",
    )
    return must_run_git_cmd("-C", str(repo_path), "rev-parse", "HEAD")


def test_enforcing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tags1 = ("v1.0.0", "v1.1.0", "v1.1.1", "v2.0.0")
    tags2 = ("v0.1.0", "v3.2.0")

    repo1 = tmp_path / "repo1"
    tag_map1 = create_repo(repo1, tags1)
    repo2 = tmp_path / "repo2"
    tag_map2 = create_repo(repo2, tags2)

    workflows = {
        # each file is intentionally dented separately to ensure we always write back with the same indentation
        "first.yaml": dedent(
            """\
            jobs:
                first:
                    steps:
                        - uses: fake-user/fake-repo1@v1
                        - uses: fake-user/fake-repo1@v1.0
                        - uses: fake-user/fake-repo1@v1.1
                        - uses: fake-user/fake-repo1@v1.1.1
                        - uses: fake-user/fake-repo1/nested/action@v1.1.1
                        - uses: fake-user/fake-repo2@v0
                        - uses: fake-user/fake-repo2
            """,
        ),
        "second.yaml": dedent(
            """\
            jobs:
              first:
                steps:
                - uses: fake-user/fake-repo1@v2
                - uses: fake-user/fake-repo2@v3.2
            """,
        ),
    }

    expected_workflows = {
        "first.yaml": dedent(
            f"""\
            jobs:
                first:
                    steps:
                        - uses: fake-user/fake-repo1@{tag_map1['v1.1.1']}  # v1.1.1
                        - uses: fake-user/fake-repo1@{tag_map1['v1.0.0']}  # v1.0.0
                        - uses: fake-user/fake-repo1@{tag_map1['v1.1.1']}  # v1.1.1
                        - uses: fake-user/fake-repo1@{tag_map1['v1.1.1']}  # v1.1.1
                        - uses: fake-user/fake-repo1/nested/action@{tag_map1['v1.1.1']}  # v1.1.1
                        - uses: fake-user/fake-repo2@{tag_map2['v0.1.0']}  # v0.1.0
                        - uses: fake-user/fake-repo2@{tag_map2['v3.2.0']}  # v3.2.0
            """,
        ),
        "second.yaml": dedent(
            f"""\
            jobs:
              first:
                steps:
                - uses: fake-user/fake-repo1@{tag_map1["v2.0.0"]}  # v2.0.0
                - uses: fake-user/fake-repo2@{tag_map2["v3.2.0"]}  # v3.2.0
            """,
        ),
    }

    filepaths = tuple(tmp_path / path for path in workflows.keys())
    for path, content in zip(filepaths, workflows.values()):
        path.write_text(content)

    args = ("enforce", *map(str, filepaths))

    with (
        git_config(
            f"url.file://{repo1}.insteadOf", "https://github.com/fake-user/fake-repo1"
        ),
        git_config(
            f"url.file://{repo2}.insteadOf", "https://github.com/fake-user/fake-repo2"
        ),
    ):
        return_code = main(args)

    captured = capsys.readouterr()

    assert return_code == 1, captured.err
    assert captured.out == ""
    assert captured.err == ""
    for path in filepaths:
        with open(path) as f:
            assert expected_workflows[os.path.basename(path)] == f.read()


def test_enforcing_errors_on_bad_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tags = ("v1.0.0",)
    repo = tmp_path / "repo1"
    create_repo(repo, tags)
    bad_tag = "v2"

    workflow_path = tmp_path / "workflow.yaml"
    content = dedent(
        f"""\
        jobs:
            job-bad-version:
                steps:
                    - uses:  fake-user/fake-repo@{bad_tag}
        """,
    )
    expected_err = "could not find any tag matching " + bad_tag

    workflow_path.write_text(content)

    args = ("enforce", str(workflow_path))
    with git_config(
        f"url.file://{repo}.insteadOf", "https://github.com/fake-user/fake-repo"
    ):
        return_code = main(args)

    captured = capsys.readouterr()

    assert return_code == 1, captured.err
    assert captured.out == ""
    assert captured.err == "Error: " + expected_err + "\n"
    with open(workflow_path) as f:
        assert f.read() == content, "workflow should be unmodified on error"
