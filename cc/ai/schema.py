"""Minimal JSON-schema checker (no dependency). Supports type, required, properties, values (schema for every value
of a free-keyed object), enum, maxLength, minimum, maximum, items, maxItems. Objects reject unknown keys."""

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(t, v):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
    return isinstance(v, _TYPES[t])


def validate(schema, obj, path="$"):
    """Return a list of error strings; empty means valid."""
    t = schema.get("type")
    if t and not _is(t, obj):
        return [f"{path}: expected {t}"]
    errs = []
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: not one of {schema['enum']}")
    if t == "string" and len(obj) > schema.get("maxLength", 10_000):
        errs.append(f"{path}: longer than {schema.get('maxLength', 10_000)}")
    if t in ("integer", "number"):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if t == "array":
        if len(obj) > schema.get("maxItems", 200):
            errs.append(f"{path}: more than {schema.get('maxItems', 200)} items")
        for i, v in enumerate(obj[:schema.get("maxItems", 200)]):
            errs += validate(schema.get("items", {}), v, f"{path}[{i}]")
    if t == "object":
        props = schema.get("properties", {})
        errs += [f"{path}.{k}: required" for k in schema.get("required", ()) if k not in obj]
        for k, v in obj.items():
            if k in props:
                errs += validate(props[k], v, f"{path}.{k}")
            elif "values" in schema:
                errs += validate(schema["values"], v, f"{path}.{k}")
            else:
                errs.append(f"{path}.{k}: unexpected key")
            if len(errs) > 20:
                break
    return errs
