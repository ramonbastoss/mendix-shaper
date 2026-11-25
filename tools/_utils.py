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


def refuse_unknown_arguments(mcp) -> list[str]:
    """Make every registered tool reject an argument it does not declare.

    FastMCP builds each tool's argument model from the function signature and
    leaves pydantic's `extra` at its default, which is `ignore`. So an argument
    the tool does not declare is **silently discarded** and the parameter it was
    meant to govern runs on its default. Measured on mcp 1.30.0: calling
    `tool_validate_model` with `save_as_baselines=true` - one letter wrong -
    returns a normal result in which no baseline was saved.

    On a reader that is a wasted call. On a tool that writes to a model it is
    the worst failure shape there is: the write happens, under settings nobody
    chose, and the reply says it succeeded. An agent reading that reply has no
    way to tell. Hence this runs before the first editing tool is registered
    rather than after.

    Setting `extra="forbid"` on the argument model fixes both halves at once:
    pydantic raises on the extra key, so the call fails instead of running, and
    the published schema gains `additionalProperties: false`, so a client that
    validates can refuse it before it is even sent.

    **Applied to every tool the server has, not opted into per tool.** A
    decorator would have to be remembered on each new tool, and the one it is
    forgotten on is the one that fails this way - which is the same argument
    the BACKLOG makes for not offering a `force` flag. A sweep cannot be
    forgotten.

    Raises rather than returning quietly when it cannot do its job: it reaches
    into FastMCP's tool manager, so a version that reorganises that would
    otherwise turn this into a no-op that still reports success - the very
    failure it exists to prevent. Returns the tool names it hardened.
    """
    try:
        tools = mcp._tool_manager.list_tools()
    except AttributeError as e:
        raise RuntimeError(
            f"Cannot reach FastMCP's registered tools to harden them ({e}). "
            f"This server will not start without that guarantee: without it a "
            f"misspelled argument is dropped in silence and the call runs on "
            f"defaults. See requirements.txt for the mcp version this was "
            f"written against."
        )

    if not tools:
        raise RuntimeError(
            "No tools are registered, so hardening them is a no-op. Call "
            "refuse_unknown_arguments after the @mcp.tool() definitions, not "
            "before."
        )

    hardened = []
    for tool in tools:
        model = tool.fn_metadata.arg_model
        model.model_config = {**model.model_config, "extra": "forbid"}
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema(by_alias=True)
        if tool.parameters.get("additionalProperties") is not False:
            raise RuntimeError(
                f"Tool '{tool.name}' still publishes a schema that accepts "
                f"undeclared arguments after being hardened, so pydantic did "
                f"not honour the rebuild. Fix this before serving: a tool in "
                f"this state drops a misspelled argument instead of refusing "
                f"it."
            )
        hardened.append(tool.name)
    return hardened
