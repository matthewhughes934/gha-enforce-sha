import argparse
import logging
import string
import sys
from collections.abc import Generator, Iterator, Sequence
from pathlib import Path
from typing import NamedTuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from gha_enforce_sha.errors import UserError, log_error

logger = logging.getLogger("unused-deps")


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:  # pragma: no cover
        argv = sys.argv[1:]

    parser = _build_arg_parse()
    args = parser.parse_args(argv)

    try:
        return 0 if _check_gha_shas(args.filepaths) else 1
    except Exception as exc:
        retcode, msg = log_error(exc)
        print(msg, file=sys.stderr)
        return retcode


def _check_gha_shas(paths: Sequence[str]) -> bool:
    if len(paths) == 0:
        paths_iter: Iterator[str] = _iter_workflows()
    else:
        paths_iter = iter(paths)

    success = True
    for path in paths_iter:
        for rep in _find_missing_shas(Path(path)):
            if success:
                success = False
            print(
                f"in workflow file {rep.path}: in job {rep.job_name}: in step #{rep.step_index+1}: {rep.job['steps'][rep.step_index]['uses']}",
                file=sys.stderr,
            )

    return success


def _build_arg_parse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers()

    check_parser = subparsers.add_parser("check", help="check action use full SHAs")
    check_parser.add_argument(
        "filepaths",
        nargs="*",
        help="Filepaths to scan for dependency usage",
    )

    return parser


class MissingSHA(NamedTuple):
    path: Path
    job_name: str
    job: CommentedMap
    step_index: int


def _find_missing_shas(workflow_path: Path) -> Generator[MissingSHA, None, None]:
    content = YAML().load(workflow_path)

    if "jobs" in content:
        jobs = content["jobs"]
    elif "runs" in content:
        jobs = content["runs"]
    else:
        raise UserError(f"{workflow_path} does not look like a workflow or action")

    for job_name, job in jobs.items():
        if "steps" not in job:
            raise UserError(
                f"cannot process job (name={job_name}) in {workflow_path}: job has no steps"
            )

        for i, step in enumerate(job["steps"]):
            if _is_missing_complete_git_sha(step):
                yield MissingSHA(
                    path=workflow_path, job_name=job_name, job=job, step_index=i
                )


def _is_yaml(path: Path) -> bool:
    return path.suffix in (".yaml", ".yml")


def _iter_workflows() -> Generator[str, None, None]:
    workflow_path = Path(".github") / "workflows"
    if not workflow_path.is_dir():
        raise UserError(
            f"cannot list paths in '{workflow_path}': it doesn't exist or isn't a directory"
        )

    yield from filter(_is_yaml, workflow_path.iterdir())


def _is_local(workflow_reference: str) -> bool:
    # https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#example-using-an-action-in-the-same-repository-as-the-workflow
    return workflow_reference.startswith(".") or workflow_reference.startswith("/")


def _is_docker(workflow_reference: str) -> bool:
    # https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#example-using-a-docker-hub-action
    return workflow_reference.startswith("docker://")


_hex_digits = string.hexdigits.lower()
_git_sha_length = 40
_action_version_sep = "@"


def _is_missing_complete_git_sha(step: CommentedMap) -> bool:
    if "uses" not in step:
        return False

    uses = step["uses"]
    if _is_local(uses) or _is_docker(uses):
        return False

    if _action_version_sep not in uses:
        return False

    _, version = uses.split(_action_version_sep, 1)

    return not _is_complete_git_sha(version)


def _is_complete_git_sha(ref: str) -> bool:
    return len(ref) == _git_sha_length and all(c in _hex_digits for c in ref)


# map of repo -> [tags]
# resolve that to repo -> [shas]
# then for the list of things -> change the node, write back out
