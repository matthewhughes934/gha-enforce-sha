import argparse
import itertools
import logging
import string
import sys
import tempfile
from collections import defaultdict
from collections.abc import Generator, Iterator, Sequence
from pathlib import Path
from typing import NamedTuple, Self

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from gha_enforce_sha.errors import UserError, log_error
from gha_enforce_sha.git import init_repo_from_action, resolve_tag

logger = logging.getLogger("unused-deps")


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:  # pragma: no cover
        argv = sys.argv[1:]

    parser = _build_arg_parse()
    args = parser.parse_args(argv)

    try:
        return 0 if _run(args) else 1
    except Exception as exc:
        retcode, msg = log_error(exc)
        print(msg, file=sys.stderr)
        return retcode


def _run(args: argparse.Namespace) -> int:
    if args.command == "check":
        return _check_gha_shas(args.filepaths)
    elif args.command == "enforce":
        return _enforce_gha_shas(args.filepaths)
    assert False, "unimplemented command"


def _enforce_gha_shas(paths: Sequence[str]) -> bool:
    if len(paths) == 0:
        paths_iter: Iterator[str] = _iter_workflows()
    else:
        paths_iter = iter(paths)

    reps_map = {path: tuple(_find_missing_shas(Path(path))) for path in paths_iter}
    # fetch all the corresponding repos
    repo_map = defaultdict(list)
    # map of repo -> partial tag -> [actual_tag, sha]
    resolved_tag_map: dict[str, dict[str | None, tuple[str, str]]] = defaultdict(dict)
    for rep in itertools.chain.from_iterable(reps_map.values()):
        repo_map[rep.action_version.path].append(rep.action_version.version)

    for action, versions in repo_map.items():
        # TODO: caching: avoid looking up the same tag twice...
        with tempfile.TemporaryDirectory() as repo_path:
            init_repo_from_action(repo_path, _repo_url_from_action(action))
            for version in versions:
                full_tag, sha = resolve_tag(repo_path, version)
                resolved_tag_map[action][version] = (full_tag, sha)

    for path, reps in reps_map.items():
        # give me the new line plz, i.e. the fix
        content = YAML().load(Path(path))
        for rep in reps:
            full_tag, sha = resolved_tag_map[rep.action_version.path][
                rep.action_version.version
            ]
            new_line = f"{rep.action_version.path}@{sha}"
            if "jobs" in content:
                step = content["jobs"][rep.job_name]["steps"][rep.step_index]
            else:
                step = content["runs"][rep.job_name]["steps"][rep.step_index]
            # line.replace(action@version -> action@version # blah...
            step["uses"] = new_line
            step.yaml_add_eol_comment(f"# {full_tag}", "uses")

        with open(path, "w") as f:
            YAML().dump(content, f)

    return all(len(reps) == 0 for reps in reps_map.values())


def _repo_url_from_action(path: str) -> str:
    return "https://github.com/" + path


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
                f"in workflow file {rep.path}: in job {rep.job_name}: in step #{rep.step_index+1}: {rep.action_version}",
                file=sys.stderr,
            )

    return success


def _build_arg_parse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="check action use full SHAs")
    check_parser.add_argument(
        "filepaths",
        nargs="*",
        help="Filepaths to scan for dependency usage",
    )

    enforce_parser = subparsers.add_parser(
        "enforce", help="enforce use of full SHAs in actions"
    )
    enforce_parser.add_argument(
        "filepaths",
        nargs="*",
        help="Filepaths to scan for dependency usage",
    )

    return parser


_ACTION_VERSION_SEP = "@"


class ActionVersion(NamedTuple):
    path: str
    version: str | None

    def __str__(self) -> str:
        if self.version is not None:
            return f"{self.path}{_ACTION_VERSION_SEP}{self.version or ''}"
        else:
            return f"{self.path}"

    @classmethod
    def parse(cls, raw_action: str) -> Self:
        parts = raw_action.split(_ACTION_VERSION_SEP, 1)
        if len(parts) == 1:
            return cls(path=parts[0], version=None)
        else:
            return cls(path=parts[0], version=parts[1])


class MissingSHA(NamedTuple):
    path: Path
    job_name: str
    step_index: int
    action_version: ActionVersion


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
            action_version = _parse_action(step)
            if action_version is None:
                continue
            if action_version.version is None or not _is_complete_git_sha(
                action_version.version
            ):
                yield MissingSHA(
                    path=workflow_path,
                    job_name=job_name,
                    action_version=action_version,
                    step_index=i,
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


def _parse_action(step: CommentedMap) -> ActionVersion | None:
    if "uses" not in step:
        return None

    uses = step["uses"]
    if _is_local(uses) or _is_docker(uses):
        return None

    return ActionVersion.parse(uses)


_hex_digits = string.hexdigits.lower()
_git_sha_length = 40


def _is_complete_git_sha(ref: str) -> bool:
    return len(ref) == _git_sha_length and all(c in _hex_digits for c in ref)
