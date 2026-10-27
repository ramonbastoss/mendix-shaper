"""List the Mendix working copies configured on this machine."""

import os

from tools._utils import _load_config, project_root


def list_projects() -> dict:
    try:
        config = _load_config()
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    default = config.get("default")
    projects = []

    for key, proj in config.get("projects", {}).items():
        mpr_path = proj.get("mpr_path", "")
        exists = bool(mpr_path) and os.path.exists(mpr_path)
        contents = (
            os.path.isdir(os.path.join(project_root(mpr_path), "mprcontents"))
            if exists else False
        )
        projects.append({
            "key": key,
            "name": proj.get("name", key),
            "is_default": key == default,
            "mpr_path": mpr_path,
            "mpr_exists": exists,
            # Without mprcontents/ there is nothing to write: the .mpr alone is
            # an index. Worth reporting here rather than failing later.
            "mprcontents_exists": contents,
        })

    return {"success": True, "default": default, "projects": projects}
