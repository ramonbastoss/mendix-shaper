from mcp.server.fastmcp import FastMCP

from tools.list_projects import list_projects
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
