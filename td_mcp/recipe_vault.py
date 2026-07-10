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
    difficulty: str = "beginner",
    td_version_min: Optional[str] = None,
    technique: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a recipe (list of node specs from export_recipe) to the vault.

    tdmcp-style design-file metadata is supported: ``difficulty``
    (beginner/intermediate/advanced), ``td_version_min`` (e.g. "2023.10000"),
    and ``technique`` (a short style tag used for similarity recall).
    """
    vault = _load_vault()
    now = time.time()
    rid = str(uuid.uuid4())[:12]
    entry = {
        "id": rid,
        "name": name,
        "description": description,
        "tags": tags or [],
        "author": author,
        "difficulty": difficulty,
        "td_version_min": td_version_min,
        "technique": technique,
        "metadata": metadata or {},
        "created": now,
        "updated": now,
        "version": 1,
        "recipe": recipe,
    }
    vault["recipes"][rid] = entry
    _save_vault(vault)
    return {"ok": True, "id": rid, "name": name, "path": str(VAULT_FILE)}


def draft_recipe_from_chain(chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a recipe *skeleton* from an operator chain.

    Given a list of ``{"type": "Noise TOP", "params": {...}, "inputs": [...]}``
    specs (e.g. from a build_and_verify run or a TDN operator list), infer a
    name, tags, technique, and difficulty so the agent can persist a reusable
    blueprint without hand-authoring metadata. The chain is normalized into the
    same node-spec shape export_recipe / import_recipe expect.
    """
    if isinstance(chain, dict) and "operators" in chain:
        ops = chain["operators"]
    else:
        ops = chain

    node_specs = []
    families = set()
    for i, spec in enumerate(ops):
        op_type = spec.get("type") or spec.get("operator")
        node_specs.append({
            "name": spec.get("name") or f"op{i}",
            "type": op_type,
            "parameters": spec.get("params") or spec.get("parameters") or {},
            "inputs": spec.get("inputs") or [],
        })
        if isinstance(op_type, str) and op_type.split()[-1] in (
            "TOP", "CHOP", "SOP", "DAT", "POP", "COMP"):
            families.add(op_type.split()[-1])

    # Infer technique from the dominant family / chain shape.
    technique = "mixed"
    if families:
        technique = sorted(families)[0]  # e.g. "TOP" for a visual chain
    name = f"{technique or 'network'} chain ({len(node_specs)} ops)"
    tags = sorted(families) + [f"{len(node_specs)}-ops"]
    difficulty = "beginner" if len(node_specs) <= 4 else (
        "intermediate" if len(node_specs) <= 9 else "advanced")

    return {
        "name": name,
        "tags": tags,
        "technique": technique,
        "difficulty": difficulty,
        "description": f"Auto-drafted from a {len(node_specs)}-node {technique} chain.",
        "recipe": node_specs,
    }


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
            "difficulty": entry.get("difficulty", "beginner"),
            "td_version_min": entry.get("td_version_min"),
            "technique": entry.get("technique"),
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
def _tags_to_list(tags):
    if tags is None:
        return None
    if isinstance(tags, str):
        return tags.split(",") if tags else None
    return list(tags)


def _do_vault_save(recipe, name, tags=None, description="", author="",
                    metadata=None, difficulty="beginner", td_version_min=None,
                    technique=None):
    return save_recipe(recipe, name, tags, description, author, metadata,
                        difficulty=difficulty, td_version_min=td_version_min,
                        technique=technique)


def _do_vault_list(query="", tags=None, limit=50, offset=0):
    return list_recipes(query, _tags_to_list(tags), limit, offset)


def _do_vault_get(rid):
    return get_recipe(rid)


def _do_vault_update(rid, name=None, tags=None, description=None, recipe=None):
    return update_recipe(rid, name, _tags_to_list(tags), description, recipe)


def _do_vault_delete(rid):
    return delete_recipe(rid)


def _do_vault_export(path=None):
    return export_vault(path)


def _do_vault_import(path, merge=True):
    return import_vault(path, merge)