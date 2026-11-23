from mcp.server.fastmcp import FastMCP

from tools.close_app import close_app
from tools.list_projects import list_projects
from tools.open_app import open_app
from tools.validate_model import validate_model

# Named for what it does to a model, so that the split from mendix-mapper is
# visible in every tool name that reaches the model: mapper reads, shaper
# writes. Anything registered here that only reads belongs in the mapper
# instead - the prefix is the contract.
mcp = FastMCP("Mendix Shaper")


# --- setup -----------------------------------------------------------------

@mcp.tool()
def tool_list_projects() -> dict:
    """
    List the Mendix working copies configured on this machine.
    Shows each project key (used as the 'project' argument of the other tools),
    its display name, whether it is the default, and whether its .mpr and
    mprcontents/ folder are actually there.
    Call this first when you do not know which project to use.
    """
    return list_projects()


# --- the IDE ---------------------------------------------------------------
#
# These two do not touch the model, which is why they can be registered while
# the editing surface is still an open question in BACKLOG.md. They are here
# rather than in the mapper because they mutate something - the IDE's hold on
# a working copy - and because closing Studio Pro is the precondition for every
# write this server makes.
#
# Both can come back with status "needs_user_decision". That is not an error
# and not something to work around: it means a modal is on the user's screen
# whose wrong answer cannot be undone. There is deliberately no force flag to
# find.

@mcp.tool()
def tool_close_app(project: str = None, timeout: int = None) -> dict:
    """
    Close the Studio Pro instance that has a project's model open.
    Call this before writing to a working copy the IDE has open: Studio Pro
    holds the model in memory, so a write underneath it is invisible on screen
    and is overwritten the moment somebody saves there.
    Sends the same close the window's X button sends, so the IDE shuts down its
    own way and asks about anything unsaved. It never terminates the process:
    unsaved work exists only in Studio Pro's memory - on an edited app the
    working copy on disk was measured untouched, git status clean - so killing
    it would throw that work away with nothing on disk to recover from.
    Returns status "closed" (with the seconds it took, about 2 on a healthy
    close), "not_open" if nothing had it open, or "needs_user_decision".
    On "needs_user_decision", STOP AND ASK THE USER. Do not retry, do not try
    another way to close it, and do not suggest killing the process. The reply
    carries the dialog's own text and buttons - typically "Do you want to save
    the changes to <document>?" with Save, Discard and Cancel. Put that
    question to the user and let them answer it in the IDE. Studio Pro asks once
    per edited document, so one answer may be followed by another.
    If 'project' is omitted, the configured default is used.
    """
    return close_app(project, timeout)


@mcp.tool()
def tool_open_app(project: str = None, timeout: int = None) -> dict:
    """
    Open a project's model in Studio Pro and wait until it has really loaded.
    Use it to hand the app back after writing, which is how the user checks the
    result. The IDE is taken from the folder holding the project's configured
    mx.exe, so the model is opened by the same build that validates it - a
    machine can carry several installs of one version number.
    Waits for the window title to stop being the bare product name, which takes
    roughly 40-60 seconds on a large app; the window appears long before the
    model is in it. Returns status "open" with the title and the seconds taken.
    Refuses with "needs_user_decision" and reason "already_open" if any Studio
    Pro already holds that model, without launching anything. That refusal is
    the point: a second launch does not fail, it puts a modal on the user's
    screen saying an app cannot be opened twice, and waits there unseen with no
    exit code. ASK THE USER whether to use the instance that is open or close it
    first - tool_close_app is the way to close it. Do not launch it anyway.
    If 'project' is omitted, the configured default is used.
    """
    return open_app(project, timeout)


# --- verification ----------------------------------------------------------

@mcp.tool()
def tool_validate_model(project: str = None, save_as_baseline: bool = False) -> dict:
    """
    Check that a Mendix model still opens and still passes consistency checks.
    Runs two passes, because they catch different failures: the model loader
    first (a reference pointing at a missing unit crashes it and produces no
    consistency error at all), then 'mx check' for the CE#### errors Studio Pro
    would show.
    Call it after any write to the model, and before one too: an absolute error
    count means nothing without a baseline, because an app can carry hundreds
    of pre-existing errors. Pass save_as_baseline=true on a known-good model to
    record one; later runs then report the delta and name the error codes that
    appeared, which is what attributes a regression to the change that caused
    it.
    Slow - minutes on a large app - and it reads the .mpr from disk, so it sees
    uncommitted changes but not edits still unsaved in Studio Pro. It also
    competes with Studio Pro for the model: ask before running it on an app
    somebody has open.
    If 'project' is omitted, the configured default is used.
    """
    return validate_model(project, save_as_baseline)


if __name__ == "__main__":
    mcp.run()
