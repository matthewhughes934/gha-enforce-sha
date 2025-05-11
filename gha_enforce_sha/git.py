import subprocess

from gha_enforce_sha.errors import UserError

_REMOTE_NAME = "origin"


def init_repo_from_action(repo_path: str, action: str) -> None:
    must_run_git_cmd("init", "--bare", repo_path)
    must_run_git_cmd("-C", repo_path, "remote", "add", _REMOTE_NAME, action)


def resolve_tag(repo_path: str, partial_tag: str | None) -> tuple[str, str]:
    if partial_tag is None:
        partial_tag = ""

    must_run_git_cmd(
        "-C",
        repo_path,
        "fetch",
        "--tags",
        _REMOTE_NAME,
        f"refs/tags/{partial_tag}*:refs/tags/{partial_tag}*",
    )

    if partial_tag != "":
        retcode, stdout, _ = _run_git_cmd("-C", repo_path, "rev-parse", partial_tag)
        if retcode == 0:
            # partial tag is actual tag, e.g. v1.2.3
            return partial_tag, stdout.rstrip("\n")

    # sort tags by version
    stdout = must_run_git_cmd(
        "-C", repo_path, "tag", "--list", "--sort=-version:refname", f"{partial_tag}*"
    )

    if stdout == "":
        raise UserError(f"could not find any tag matching {partial_tag}")

    tags = stdout.rstrip("\n").split("\n")
    tag = tags[0]
    return tag, must_run_git_cmd("-C", repo_path, "rev-parse", tag)


def _run_git_cmd(*args: str) -> tuple[int, str, str]:
    cmd = ["git", *args]
    return _cmd_output(*cmd)


def must_run_git_cmd(*args: str) -> str:
    retcode, stdout, stderr = _run_git_cmd(*args)
    if retcode != 0:
        raise UserError(
            f"failed to run git command: {args}\nstdout: {stdout}\nstderr: {stderr}"
        )

    return stdout.rstrip("\n")


def _cmd_output(*cmd: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except OSError as exc:
        raise UserError(f"failed running command {cmd}: error: {exc}")

    return proc.returncode, proc.stdout.decode(), proc.stderr.decode()
