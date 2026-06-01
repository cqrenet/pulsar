from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MAPPING: dict[str, Any] = {
    "category_labels": {
        "ApplicationManagement": "Application",
        "UserManagement": "User",
        "GroupManagement": "Group",
        "RoleManagement": "Role",
        "Device": "Device",
        "Policy": "Policy",
        "ResourceManagement": "Resource",
    },
    "summary_templates": {
        "default": "{operation} on {target} by {actor}",
        "Device": "{operation} on device {target} by {actor}",
        "ApplicationManagement": "{operation} for app {target} by {actor}",
    },
    "display": {
        "default": {
            "actor_field": "actor_display",
            "actor_label": "User",
        },
        "ApplicationManagement": {
            "actor_field": "actor_upn",
            "actor_label": "User",
        },
        "Device": {
            "actor_field": "target_display",
            "actor_label": "Device",
        },
    },
}


@lru_cache(maxsize=1)
def get_mapping() -> dict[str, Any]:
    """
    Load mapping from mappings.yml if present; otherwise fall back to defaults.
    Users can edit mappings.yml to change labels and summary templates.
    """
    path = Path(__file__).parent / "mappings.yml"
    if path.exists():
        try:
            with path.open("r") as f:
                data = yaml.safe_load(f) or {}
            return {
                "category_labels": data.get("category_labels") or DEFAULT_MAPPING["category_labels"],
                "summary_templates": data.get("summary_templates") or DEFAULT_MAPPING["summary_templates"],
                "display": data.get("display") or DEFAULT_MAPPING["display"],
            }
        except Exception:
            # If mapping fails to load, use defaults to keep the app running.
            return DEFAULT_MAPPING
    return DEFAULT_MAPPING
