"""Open a project's model in Studio Pro, or say why it was not opened."""

from engine import studio_pro
from tools._utils import mx_path, require_mpr, resolve_project


def open_app(project: str = None, timeout: int = None) -> dict:
    """Adapter: resolve the project, hand the paths to the engine."""
    try:
        key, proj = resolve_project(project)
        mpr = require_mpr(proj, key)
        exe_source = mx_path(proj)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    try:
        result = studio_pro.open_app(
            mpr,
            exe_source,
            timeout=timeout or studio_pro.DEFAULT_OPEN_TIMEOUT,
        )
    except (FileNotFoundError, OSError) as e:
        return {"success": False, "project": key, "error": str(e)}

    return {"success": True, "project": key, **result}
