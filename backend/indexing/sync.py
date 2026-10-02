"""plan_sync: decide what has to be embedded, saved and deleted. A pure function: no files, no database, no model.

The caller passes the stored rows and the freshly chunked chunks for the SAME set of files. A chunk that is in `old`
but not in `new` is reported as gone, so never pass `old` rows for files you did not re-chunk (the file-hash shortcut
must leave those files out of both lists).
"""
from dataclasses import dataclass, field

# Row fields compared when the content_hash is the same. `file_path` is left out on purpose: it is an absolute
# path that goes stale when the repo folder moves, and nothing uses it.
ROW_FIELDS = ("rel_path", "ordinal", "kind", "symbol", "parent", "names", "part", "part_count",
              "start_line", "end_line", "text", "embed_text", "synthetic", "parse_error")


@dataclass
class SyncPlan:
    new: list[dict] = field(default_factory=list)           # no stored row: save and embed
    changed: list[dict] = field(default_factory=list)       # stored, but content_hash differs: save and embed
    needs_vector: list[dict] = field(default_factory=list)  # same hash, but no vector record for THIS model: embed (and save)
    moved: list[dict] = field(default_factory=list)         # same hash and vector, but a row field (line numbers...) differs: save only
    unchanged: list[dict] = field(default_factory=list)     # nothing to do
    gone: list[str] = field(default_factory=list)           # stored ids that are no longer chunked: delete row and vector
    to_embed: list[dict] = field(default_factory=list)      # new + changed + needs_vector, in chunk order

    def summary(self) -> dict:
        return {name: len(getattr(self, name)) for name in ("new", "changed", "needs_vector", "moved", "unchanged", "gone", "to_embed")}


def plan_sync(old_rows: list[dict], new_chunks: list[dict], model: str, dim: int) -> SyncPlan:
    """Sort `new_chunks` against the stored `old_rows` for the embedder (`model`, `dim`) that is in use now."""
    if not isinstance(model, str) or not model or not isinstance(dim, int) or isinstance(dim, bool) or dim < 1:
        raise ValueError(f"need a model name and a positive dimension, got {model!r}, {dim!r}")
    old = _by_id(old_rows, "stored rows")
    _by_id(new_chunks, "new chunks")  # only to refuse duplicates

    plan = SyncPlan()
    for chunk in new_chunks:
        row = old.get(chunk["id"])
        if row is None:
            plan.new.append(chunk)
        elif row["content_hash"] != chunk["content_hash"]:
            plan.changed.append(chunk)
        elif row.get("embed_model") != model or row.get("embed_dim") != dim:
            # Same content, but no record that THIS model embedded it (never embedded, or another model/size).
            # Same hash alone must never mean done.
            plan.needs_vector.append(chunk)
        elif any(row[f] != chunk[f] for f in ROW_FIELDS):
            plan.moved.append(chunk)
        else:
            plan.unchanged.append(chunk)

    wanted = {c["id"] for c in new_chunks}
    plan.gone = [i for i in old if i not in wanted]
    to_embed = {c["id"] for c in plan.new + plan.changed + plan.needs_vector}
    plan.to_embed = [c for c in new_chunks if c["id"] in to_embed]
    return plan


def _by_id(items: list[dict], what: str) -> dict:
    result = {}
    for item in items:
        if item["id"] in result:
            raise ValueError(f"duplicate id {item['id']} in {what}")
        result[item["id"]] = item
    return result
