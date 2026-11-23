"""Close the Studio Pro instance holding a project's model.

The step that has to happen before writing into a working copy the IDE has
open. It reports rather than insists: a close the IDE will not complete is a
question for whoever is sitting in front of it.
"""

from engine import studio_pro
from tools._utils import require_mpr, resolve_project


def close_app(project: str = None, timeout: int = None) -> dict:
    """Adapter: resolve the project, hand the path to the engine."""
    try:
        key, proj = resolve_project(project)
        mpr = require_mpr(proj, key)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    try:
        result = studio_pro.close_app(
            mpr,
            timeout=timeout or studio_pro.DEFAULT_CLOSE_TIMEOUT,
        )
    except OSError as e:
        return {"success": False, "project": key, "error": str(e)}

    return {"success": True, "project": key, **result}
