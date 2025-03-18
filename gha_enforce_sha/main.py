import argparse
import enum
import os.path
import re
import string
import sys
from collections.abc import Generator, Iterable, Sequence
from pathlib import Path

import yaml

def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:  # pragma: no cover
        argv = sys.argv[1:]

    parser = _build_arg_parse()
    args = parser.parse_args(argv)

    # default: look under .github/workflows/*.ya?ml
    # optional: ... ?
    # can there be YAML files that aren't actions?
    # we should allow passing filenames too
    # TODO: if no filepaths, _iter_workflows
    for path in filter(_is_yaml, args.filepaths):
        pass

    return 0


def _build_arg_parse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "filepaths",
        nargs="*",
        default=str(Path(".github/workflows")),
        help="Filepaths to scan for dependency usage",
    )

    return parser


def _is_yaml(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext in (".yaml", ".yml")


def _iter_workflows(filepaths: Iterable[str]) -> Generator[str, None, None]:
    workflow_path = Path.cwd() / ".github" / "workflows"
    if not workflow_path.is_dir():
        raise Exception(f"can't list paths in '{workflow_path}': it doesn't exist or isn't a directory")

    for path in filepaths:
        yield from filter(_is_yaml, workflow_path.iterdir())

class ActionType(enum.Enum):
    ACTION = enum.auto()
    WORKFLOW = enum.auto()

_remoteActionLookup = {
    ActionType.ACTION:
    ActionType.WORKFLOW
}

def _get_remote_actions_for_workflow(workflow: dict[str, object]) -> Generator[str, None, None]:
    for job in workflow["jobs"].values():
        yield from (step["uses"] for step in job["steps"])

def _get_remote_actions_for_action(action: dict[str, object]) -> Generator[str, None, None]:
    for job in action["runs"].values():
        yield from (step["uses"] for step in runs["steps"])

def _get_type(yaml_content: dict[str, object]) -> ActionType | None:
    if "jobs" in yaml_content:
        return ActionType.Workflow
    if "runs" in yaml_content:
        return ActionType.Action
    return None

def _is_local(workflow_reference: str) -> bool:
    # https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#example-using-an-action-in-the-same-repository-as-the-workflow
    return workflow_reference.startswith(".") or workflow_reference.startswith("/") 

def _is_docker(workflow_reference: str) -> bool:
    # https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#example-using-a-docker-hub-action
    return workflow_reference.startswith("docker://")

_hex_digits = string.hexdigits.lower()
_git_sha_length = 40
_action_version_step = "@"

def _is_missing_complete_git_sha(step: dict[str, object]) -> bool:
    if not "uses" in step:
        raise Exceptin("need a uses")

    uses = step["uses"]
    if _is_local(uses) or _is_docker(uses):
        return False

    if _action_version_step not in uses:
        return Exception("no version specified, I guess it's bad")

    action_path, version = uses.split(_action_version_step)

    if _is_complete_git_sha(version):
        return False

def _is_complete_git_sha(ref: str) -> bool:
    return len(ref) == _git_sha_length and all(c in _hex_digits)

def _check_version_shas(action_path: str) -> None:
    with open(action_path) as f:
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        content = yaml.load(f, Loader=loader)

    if "jobs" in content:
        for job_name, job in content["jobs"].items():
            if "steps" not in job:
                raise Exception("need some steps")
    elif "runs" in content:
        if "steps" not in content["runs"]:
            raise Exception("need some steps")

