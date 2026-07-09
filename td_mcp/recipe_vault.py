"""Recipe vault — persistent, tagged, searchable blueprint storage.

Builds on export_recipe / import_recipe to give a shareable, versioned
library of network patterns (tdmcp-style vault).
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_VAULT_DIR = Path(os.environ.get("TD_MCP_VAULT_DIR", Path.home() / ".td_mcp" / "vault"))
DEFAULT_VAULT_DIR.mkdir(parents=True, exist_ok=True)
VAULT_FILE = DEFAULT_VAULT_DIR / "recipes.json"

_SCHEMA_VERSION = 1


def _load_vault() -> Dict[str, Any]:
    if VAULT_FILE.exists():
        try:
            with VAULT_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("schema_version", 0) == _SCHEMA_VERSION:
                return data
        except Exception:
            pass
    return {"schema_version": _SCHEMA_VERSION, "recipes": {}}


def _save_vault(data: Dict[str, Any]) -> None:
    tmp = VAULT_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(VAULT_FILE)


def save_recipe(
    recipe: List[Dict[str, Any]],
    name: str,
    tags: Optional[List[str]] = None,
    description: str = "",
    author: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist a recipe (list of node specs from export_recipe) to the vault."""
    vault = _load_vault()
    now = time.time()
    rid = str(uuid.uuid4())[:12]
    entry = {
        "id": rid,
        "name": name,
        "description": description,
        "tags": tags or [],
        "author": author,
        "metadata": metadata or {},
        "created": now,
        "updated": now,
        "version": 1,
        "recipe": recipe,
    }
    vault["recipes"][rid] = entry
    _save_vault(vault)
    return {"ok": True, "id": rid, "name": name, "path": str(VAULT_FILE)}


def list_recipes(
    query: str = "",
    tags: Optional[List[str]] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """List recipes with optional text search and tag filtering."""
    vault = _load_vault()
    results = []
    q = query.lower()
    tag_set = set(tags or [])
    for rid, entry in vault["recipes"].items():
        if q and q not in entry["name"].lower() and q not in entry.get("description", "").lower():
            continue
        if tag_set and not tag_set.issubset(set(entry.get("tags", []))):
            continue
        results.append({
            "id": rid,
            "name": entry["name"],
            "description": entry.get("description", ""),
            "tags": entry.get("tags", []),
            "author": entry.get("author", ""),
            "created": entry["created"],
            "updated": entry["updated"],
            "version": entry["version"],
            "node_count": len(entry.get("recipe", [])),
        })
    results.sort(key=lambda x: x["updated"], reverse=True)
    total = len(results)
    return {"ok": True, "total": total, "recipes": results[offset:offset + limit]}


def get_recipe(rid: str) -> Dict[str, Any]:
    """Retrieve a single recipe by ID."""
    vault = _load_vault()
    entry = vault["recipes"].get(rid)
    if not entry:
        return {"ok": False, "error": f"recipe not found: {rid}"}
    return {"ok": True, "recipe": entry}


def update_recipe(
    rid: str,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    description: Optional[str] = None,
    recipe: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Update metadata or the recipe body; increments version."""
    vault = _load_vault()
    entry = vault["recipes"].get(rid)
    if not entry:
        return {"ok": False, "error": f"recipe not found: {rid}"}
    if name is not None:
        entry["name"] = name
    if tags is not None:
        entry["tags"] = tags
    if description is not None:
        entry["description"] = description
    if recipe is not None:
        entry["recipe"] = recipe
    entry["updated"] = time.time()
    entry["version"] = entry.get("version", 1) + 1
    _save_vault(vault)
    return {"ok": True, "id": rid, "version": entry["version"]}


def delete_recipe(rid: str) -> Dict[str, Any]:
    vault = _load_vault()
    if rid not in vault["recipes"]:
        return {"ok": False, "error": f"recipe not found: {rid}"}
    del vault["recipes"][rid]
    _save_vault(vault)
    return {"ok": True, "deleted": rid}


def export_vault(path: Optional[str] = None) -> Dict[str, Any]:
    """Export the entire vault to a portable JSON file."""
    vault = _load_vault()
    out = Path(path or (DEFAULT_VAULT_DIR / f"td_mcp_vault_export_{int(time.time())}.json"))
    with out.open("w", encoding="utf-8") as f:
        json.dump(vault, f, indent=2, ensure_ascii=False)
    return {"ok": True, "file": str(out), "recipe_count": len(vault["recipes"])}


def import_vault(path: str, merge: bool = True) -> Dict[str, Any]:
    """Import a vault export (merge or replace)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema_version", 0) != _SCHEMA_VERSION:
        return {"ok": False, "error": "incompatible schema version"}
    vault = _load_vault() if merge else {"schema_version": _SCHEMA_VERSION, "recipes": {}}
    added = 0
    for rid, entry in data.get("recipes", {}).items():
        if rid in vault["recipes"]:
            rid = str(uuid.uuid4())[:12]
        vault["recipes"][rid] = entry
        added += 1
    _save_vault(vault)
    return {"ok": True, "added": added, "total": len(vault["recipes"])}


# ---------------------------------------------------------------------------
# Bridge tool helpers (to be registered in td_mcp_bridge.py)
# ---------------------------------------------------------------------------
def _do_vault_save(recipe, name, tags=None, description="", author="", metadata=None):
    return save_recipe(recipe, name, tags, description, author, metadata)


def _do_vault_list(query="", tags=None, limit=50, offset=0):
    tag_list = tags.split(",") if tags else None
    return list_recipes(query, tag_list, limit, offset)


def _do_vault_get(rid):
    return get_recipe(rid)


def _do_vault_update(rid, name=None, tags=None, description=None, recipe=None):
    tag_list = tags.split(",") if tags else None
    return update_recipe(rid, name, tag_list, description, recipe)


def _do_vault_delete(rid):
    return delete_recipe(rid)


def _do_vault_export(path=None):
    return export_vault(path)


def _do_vault_import(path, merge=True):
    return import_vault(path, merge)