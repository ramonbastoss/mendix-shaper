"""The process-level guard: a tool refuses an argument it does not declare.

Runs against a throwaway FastMCP with one tool, through FastMCP's own call
path rather than against the helper's return value - the question is what the
server does with a bad call, not what the helper says it did. Run it directly:

    python tests/test_utils.py

Prints one line per case and exits non-zero on failure, the same shape as the
other tests here - no test runner is a dependency of this repo yet.

The case that matters is the first one, and it is the only one that passes a
*broken* server: `tool_demo(save_as_baselines=True)` - one letter wrong -
returns a normal result in which no baseline was saved. That is what every
tool in this repo did until `refuse_unknown_arguments` was applied, and it is
the failure shape a write tool cannot afford: the write runs, under settings
nobody chose, and the reply reads like success.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP          # noqa: E402

from tools._utils import refuse_unknown_arguments  # noqa: E402

PASSED = FAILED = 0


def case(title, fn, expect_error):
    global PASSED, FAILED
    try:
        fn()
        raised, message = False, ""
    except Exception as exc:                       # noqa: BLE001 - that is the assertion
        raised, message = True, str(exc)
    if raised == expect_error:
        PASSED += 1
        print(f"[PASS] {title}")
    else:
        FAILED += 1
        print(f"[FAIL] {title}")
        print(f"       expected {'an error' if expect_error else 'no error'}, "
              f"got {'an error' if raised else 'none'}")
        if message:
            print(f"       {message[:200]}")


def server():
    """A server shaped like this one: every argument optional, with a default."""
    mcp = FastMCP("test")

    @mcp.tool()
    def tool_demo(project: str = None, save_as_baseline: bool = False) -> dict:
        return {"project": project, "baseline": save_as_baseline}

    return mcp


def call(mcp, arguments):
    return asyncio.run(mcp._tool_manager.call_tool("tool_demo", arguments))


def schema(mcp):
    return mcp._tool_manager.list_tools()[0].parameters


# -- what it is for -----------------------------------------------------------

def drops_it_unhardened():
    """The defect, stated as a test so it cannot quietly come back."""
    result = call(server(), {"save_as_baselines": True})
    assert result["baseline"] is False, \
        "the misspelled argument was not dropped - has FastMCP changed?"


case("without the guard, a misspelled argument is dropped and the default runs",
     drops_it_unhardened, False)


def refuses_it():
    mcp = server()
    refuse_unknown_arguments(mcp)
    call(mcp, {"save_as_baselines": True})


case("with it, that same call is refused", refuses_it, True)


def still_works():
    mcp = server()
    refuse_unknown_arguments(mcp)
    result = call(mcp, {"save_as_baseline": True})
    assert result["baseline"] is True, "a declared argument stopped arriving"
    assert call(mcp, {})["baseline"] is False, "omitting it stopped working"


case("declared arguments still arrive, and omitting them still defaults",
     still_works, False)


def published():
    mcp = server()
    assert "additionalProperties" not in schema(mcp), \
        "FastMCP now publishes additionalProperties on its own"
    refuse_unknown_arguments(mcp)
    assert schema(mcp)["additionalProperties"] is False, \
        "the published schema still accepts undeclared arguments"
    assert "project" in schema(mcp)["properties"], \
        "hardening the schema lost the arguments"


case("the published schema says additionalProperties: false", published, False)


def reports():
    mcp = server()
    assert refuse_unknown_arguments(mcp) == ["tool_demo"], \
        "did not report the tool it hardened"


case("it reports what it hardened", reports, False)


case("it refuses to be a no-op on a server with no tools",
     lambda: refuse_unknown_arguments(FastMCP("empty")), True)

case("it refuses an object whose tools it cannot reach",
     lambda: refuse_unknown_arguments(object()), True)


# -- and the real server ------------------------------------------------------

def real_server():
    """Not a replica: the module the client loads, with its tools as they are."""
    import server as shaper

    names = sorted(shaper.HARDENED)
    assert names == ["tool_close_app", "tool_list_projects", "tool_open_app",
                     "tool_validate_model"], f"hardened {names}"
    for tool in shaper.mcp._tool_manager.list_tools():
        assert tool.parameters.get("additionalProperties") is False, \
            f"{tool.name} still accepts undeclared arguments"


case("every tool the server registers is hardened", real_server, False)


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
