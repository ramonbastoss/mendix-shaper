"""Configuration, paths and the process-level guards every tool shares."""

import json
import os
import subprocess

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "projects.json")

# Where tool output goes. Inside this repository, never inside the Mendix
# working copy: a file written next to the model would show up in the app's own
# git status and be indistinguishable from an edit the user made.
TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", "temp")

# `mx check` walks the whole model. Minutes on a large app, so the ceiling is
# generous - but it is still a ceiling, because a tool that never returns takes
# the server down with it.
MX_TIMEOUT = 1800
GIT_TIMEOUT = 120


def _load_config() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        raise FileNotFoundError(
            "projects.json not found. Copy projects.example.json to "
            "projects.json and fill in the paths for this machine."
        )
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_project(project: str = None) -> tuple[str, dict]:
    """Return (key, project_config) from projects.json.

    Each entry is one working copy, not one app: if you keep a checkout per
    branch, each branch is its own entry.
    """
    config = _load_config()
    key = project or config.get("default")
    if not key:
        raise ValueError(
            "No project given and no 'default' set in projects.json."
        )
    projects = config.get("projects", {})
    if key not in projects:
        available = list(projects.keys())
        raise ValueError(f"Project '{key}' not found. Available: {available}")
    return key, projects[key]


def require_mpr(proj: dict, key: str) -> str:
    """The project's .mpr path, checked to exist."""
    mpr_path = proj.get("mpr_path")
    if not mpr_path:
        raise ValueError(f"'mpr_path' is not set for project '{key}'.")
    if not os.path.exists(mpr_path):
        raise FileNotFoundError(f".mpr file not found: {mpr_path}")
    return mpr_path


def project_root(mpr_path: str) -> str:
    """The folder holding the .mpr, which is also `mprcontents/`'s parent.

    It is the git working copy's root too, in every layout Studio Pro produces.
    Deriving it rather than configuring it removes a way for the two to drift
    apart and point at different checkouts.
    """
    return os.path.dirname(os.path.abspath(mpr_path))


def mx_path(proj: dict) -> str:
    """The Studio Pro CLI to shell out to.

    Per project on purpose: two apps on this machine can sit on two different
    Studio Pro versions, and running the wrong one against a model is how a
    silent metamodel migration starts.
    """
    return proj.get("mx_path", "mx")


def run_mx(proj: dict, *args, stdout=None) -> subprocess.CompletedProcess:
    """Run `mx` with the guards that keep this server alive.

    `stdin=DEVNULL` is not optional. Under the stdio transport this process's
    stdin is the JSON-RPC pipe the client is reading; a child that inherits it
    and waits on it never returns, and holds the output pipes open while it
    does - which hangs the whole server, not just one call.
    """
    return subprocess.run(
        [mx_path(proj), *args],
        stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MX_TIMEOUT,
        text=stdout is None,
    )


def git_text(repo: str, *args) -> str:
    """Run git inside `repo` and return stdout, raising on failure.

    `--no-pager` and `stdin=DEVNULL` for the same reason as `run_mx`: anything
    that waits for a terminal that is not there hangs the server.
    """
    result = subprocess.run(
        ["git", "--no-pager", *args],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout.decode(errors="replace")


def temp_file(*parts: str) -> str:
    """Absolute path inside temp/, with the parent directory created."""
    path = os.path.abspath(os.path.join(TEMP_DIR, *parts))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
