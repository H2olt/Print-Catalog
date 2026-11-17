"""
Orynt3D Catalog Exporter (stage 1 of 2 — runs on your PC)
Authored by Holt Harper — Published by HarperCrafthall
https://linktr.ee/harpercrafthall

Reads your Orynt3D database, sources thumbnails from your library, and
writes a data snapshot into this repo:

    data/catalog.json   — catalog metadata + models
    data/thumbs/*.webp  — thumbnails

Commit and push data/ to publish. Netlify then runs build.py (stage 2),
which turns data/ + site/ into the live site. No HTML is produced here.

Requires: pip install Pillow
"""

import json
import shutil
import sys
from pathlib import Path, PurePosixPath

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required.  Run: pip install Pillow")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
DATA_DIR    = SCRIPT_DIR / "data"

REQUIRED_KEYS = [
    "models_db", "collections_db", "library_root",
    "thumbnail_cache_dir",
]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"ERROR: config.json not found at {CONFIG_FILE}")
        print("Copy config.example.json → config.json and fill in your paths.")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        print(f"ERROR: config.json is missing required keys: {missing}")
        sys.exit(1)
    return cfg


# ── Disclaimer ─────────────────────────────────────────────────────────────────

DISCLAIMER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                        IMPORTANT — PLEASE READ                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  This tool publishes a browsable index of 3D printable model files.        ║
║                                                                             ║
║  It is ILLEGAL to use this catalog to sell or offer prints of models       ║
║  you are not licensed to sell. Each publisher sets their own commercial    ║
║  terms. Personal use licenses do NOT grant selling rights.                 ║
║                                                                             ║
║  By continuing you confirm this catalog will NOT be used to support        ║
║  an unlicensed commercial print sales operation.                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

def show_disclaimer():
    print(DISCLAIMER)
    while True:
        ans = input("  Confirm — this catalog will not be used for unlicensed commercial sales [y/N]: ").strip().lower()
        if ans in ("y", "yes"):
            print()
            return
        if ans in ("", "n", "no"):
            print("\n  Exiting. No files were written.")
            sys.exit(0)
        print("  Please enter y or n.")


# ── Database ───────────────────────────────────────────────────────────────────

def read_models(path: str) -> list[dict]:
    """
    Read the Orynt3D models DB and deduplicate by _id.

    The models DB is append-only — Orynt3D writes revision lines for edited
    models rather than overwriting. If Orynt3D is running during a build,
    the file may contain multiple entries per model. Last-write-wins gives
    the current state of each model.

    ALWAYS close Orynt3D before running this script.
    """
    seen: dict[str, dict] = {}
    p = Path(path)
    if not p.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)
    raw_count = 0
    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARN: Skipping malformed line {lineno}: {e}")
                continue
            raw_count += 1
            rid = record.get("_id")
            if not rid:
                continue
            seen[rid] = record  # last entry wins

    unique = list(seen.values())
    if raw_count != len(unique):
        print(f"  Deduplication: {raw_count} raw lines → {len(unique)} unique models.")
        print(f"  NOTE: If this number is large, Orynt3D may have been running during a")
        print(f"  previous build. Always close Orynt3D before building the catalog.")
    return unique


def read_collections(path: str) -> dict[str, str]:
    """
    Read collections DB. Handles tombstone entries ($$deleted).
    Returns {uuid: name} for active collections only.
    """
    p = Path(path)
    if not p.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)
    result: dict[str, str] = {}
    tombstone_count = 0
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = c.get("_id")
            if not cid:
                continue
            if c.get("$$deleted"):
                result.pop(cid, None)
                tombstone_count += 1
            elif "name" in c:
                result[cid] = c["name"]
    if tombstone_count:
        print(f"\n  WARNING: {tombstone_count} deleted collection(s) still in database.")
        print("  Close Orynt3D completely and re-run for accurate results.\n")
    return result


# ── Image resolution ───────────────────────────────────────────────────────────

def resolve_cover(cache_dir: Path, cover_filename: str) -> Path | None:
    """Resolve userdata.cover filename to a path in the Orynt3D thumbnail cache."""
    if not cover_filename:
        return None
    p = cache_dir / cover_filename
    return p if p.exists() else None


def resolve_contents_image(library_root: Path, contents: list[dict]) -> Path | None:
    """First non-archived image in contents[], resolved to a real path on disk."""
    for item in contents:
        if "archive" in item:
            continue
        ext = item.get("ext", "").lower()
        if ext not in IMAGE_EXTS:
            continue
        name = item.get("name", "")
        raw  = item.get("path", "").lstrip("/")
        c1   = Path(raw) / (name + item.get("ext", ""))
        if c1.exists():
            return c1
        parts = PurePosixPath(raw).parts
        if len(parts) >= 2:
            c2 = library_root / Path(*parts[2:]) / (name + item.get("ext", ""))
            if c2.exists():
                return c2
    return None


# ── Thumbnail processing ───────────────────────────────────────────────────────

def process_thumbnail(src: Path, dest: Path, size: tuple, quality: int, method: int = 4):
    """
    Resize/crop to size, save as RGBA WebP (transparency preserved).
    method: 0 = fastest encode, 6 = best compression. Default 4 is a good balance.
    """
    with Image.open(src) as img:
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        tw, th = size
        scale  = max(tw / img.width, th / img.height)
        new_w  = max(int(img.width  * scale), tw)
        new_h  = max(int(img.height * scale), th)
        img    = img.resize((new_w, new_h), Image.LANCZOS)
        left   = (new_w - tw) // 2
        top    = (new_h - th) // 2
        img    = img.crop((left, top, left + tw, top + th))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "WEBP", quality=quality, method=method)


# ── Path-derived publisher / bundle fallback ───────────────────────────────────

def path_field_at_depth(source_path: str, depth: int) -> str:
    """
    Extract a folder name at a given 1-based depth from the source path.
    e.g. depth=2 on '/_Bundles/LOOTStudios/2401_.../Model/' → 'LOOTStudios'
    Returns empty string if path is too shallow.
    """
    parts = [x for x in source_path.strip("/").split("/") if x]
    idx   = depth - 1
    return parts[idx] if idx < len(parts) else ""


# ── Attribute helpers ──────────────────────────────────────────────────────────

def attr_first(attrs: list[dict], key: str) -> str:
    """Return the first value for a given attribute key, or empty string."""
    for a in attrs:
        if a.get("key") == key and a.get("value"):
            return a["value"]
    return ""


def attr_all(attrs: list[dict], key: str) -> list[str]:
    """Return all values for a given attribute key."""
    return [a["value"] for a in attrs if a.get("key") == key and a.get("value")]


def attr_extras(attrs: list[dict], reserved: set[str]) -> dict[str, list[str]]:
    """
    Collect all attribute key/value pairs whose key is not in reserved.
    Returns {key: [values]}.
    """
    extras: dict[str, list[str]] = {}
    for a in attrs:
        k = a.get("key", "")
        v = a.get("value", "")
        if k and v and k not in reserved:
            extras.setdefault(k, []).append(v)
    return extras


# ── Overwrite prompt ───────────────────────────────────────────────────────────

def prompt_overwrite(existing_count: int) -> bool:
    print(f"\n  {existing_count} thumbnail(s) already exist in output folder.")
    while True:
        ans = input("  Overwrite ALL existing thumbnails? [y/N]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("", "n", "no"):
            return False
        print("  Please enter y or n.")


# ── Build ──────────────────────────────────────────────────────────────────────

def export(cfg: dict):
    print("=== Orynt3D Catalog Exporter ===")
    print("    Authored by Holt Harper / HarperCrafthall\n")

    library_root   = Path(cfg["library_root"])
    cache_dir      = Path(cfg["thumbnail_cache_dir"])
    output_dir     = DATA_DIR
    thumbs_dir     = output_dir / "thumbs"
    entry_limit    = int(cfg.get("entry_limit") or 0)
    thumb_size     = (int(cfg.get("thumb_width", 400)), int(cfg.get("thumb_height", 400)))
    thumb_quality  = int(cfg.get("thumb_quality", 82))
    thumb_method   = int(cfg.get("thumb_encode_method", 4))
    exclude_colls  = set(cfg.get("exclude_collections", ["NoExport"]))
    demo_pubs      = set(cfg.get("demo_publishers", []))

    # Folder depth for path-derived publisher/bundle fallback (1-based).
    # e.g. if your structure is Root/Category/Publisher/Bundle/Model,
    # set publisher_folder_depth: 3 and bundle_folder_depth: 4.
    # Set to 0 to disable path-derived fallback for that field.
    pub_depth = int(cfg.get("publisher_folder_depth", 2))
    bun_depth = int(cfg.get("bundle_folder_depth",    3))

    # Reserved attribute keys — used for publisher/bundle lookup only.
    # All other attribute keys, including any you add in Orynt3D, are
    # collected dynamically and surfaced as filters automatically.
    reserved_attr_keys = {"publisher", "bundle"}

    for p, label in [(library_root, "library_root"), (cache_dir, "thumbnail_cache_dir")]:
        if not p.exists():
            print(f"ERROR: {label} not found: {p}")
            sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    if entry_limit:
        print(f"  [TEST MODE] entry_limit={entry_limit}  (set to 0 to process all)\n")
    if demo_pubs:
        print(f"  [DEMO MODE] Restricting to publishers: {sorted(demo_pubs)}\n")

    # ── Collections ────────────────────────────────────────────────────────────
    print("Loading collections...")
    collections_map = read_collections(cfg["collections_db"])
    print(f"  {len(collections_map)} active collection(s): {list(collections_map.values())}")
    if exclude_colls:
        print(f"  Excluding models in: {sorted(exclude_colls)}")

    # ── Models ─────────────────────────────────────────────────────────────────
    print("\nLoading models...")
    raw_models  = read_models(cfg["models_db"])
    total_in_db = len(raw_models)
    print(f"  {total_in_db} unique models found.")

    if entry_limit:
        raw_models = raw_models[:entry_limit]
        print(f"  Limiting to first {len(raw_models)}.")

    # ── Overwrite check ────────────────────────────────────────────────────────
    existing_thumbs = list(thumbs_dir.glob("*.webp"))
    overwrite = False
    if existing_thumbs:
        overwrite = prompt_overwrite(len(existing_thumbs))
        if overwrite:
            print(f"  Deleting {len(existing_thumbs)} existing thumbnails...")
            for f in existing_thumbs:
                f.unlink()

    # ── Process ────────────────────────────────────────────────────────────────
    print(f"\nProcessing (thumb={thumb_size}, quality={thumb_quality}, method={thumb_method})...")
    models_out: list[dict] = []
    errors: list[str]      = []
    processed = skipped = missing = excluded = demo_excluded = 0
    all_extra_keys: set[str] = set()

    for m in raw_models:
        model_id  = m.get("_id", "")
        src_data  = m.get("sourcedata", {})
        user_data = m.get("userdata", {})
        source    = m.get("source", {})
        contents  = m.get("contents", [])
        src_path  = source.get("path", "")
        attrs     = user_data.get("attributes") or []

        # ── Resolve publisher and bundle ───────────────────────────────────────
        # Attribute value takes priority; folder depth used as fallback.
        publisher = (attr_first(attrs, "publisher")
                     or (path_field_at_depth(src_path, pub_depth) if pub_depth else ""))
        bundle    = (attr_first(attrs, "bundle")
                     or (path_field_at_depth(src_path, bun_depth) if bun_depth else ""))

        # ── Demo publisher filter ──────────────────────────────────────────────
        if demo_pubs and publisher not in demo_pubs:
            demo_excluded += 1
            continue

        # ── NoExport collection filter ─────────────────────────────────────────
        coll_ids   = user_data.get("collections") or []
        coll_names = [collections_map.get(cid, cid) for cid in coll_ids]
        if exclude_colls.intersection(coll_names):
            excluded += 1
            continue

        # ── Name ───────────────────────────────────────────────────────────────
        name = (user_data.get("name") or src_data.get("name") or "Unnamed").strip()

        # ── All other attributes — fully dynamic ───────────────────────────────
        # No keys are hardwired. Every attribute you add in Orynt3D appears
        # automatically as a filter in the catalog.
        extras = attr_extras(attrs, reserved_attr_keys)
        all_extra_keys.update(extras.keys())

        tags  = user_data.get("tags") or []
        notes = user_data.get("notes") or ""
        exts  = sorted({c.get("ext", "").lower() for c in contents if c.get("ext")})

        # ── Thumbnail ──────────────────────────────────────────────────────────
        thumb_rel  = None
        cover_name = user_data.get("cover")

        img_src = resolve_cover(cache_dir, cover_name) if cover_name else None
        if img_src is None:
            img_src = resolve_contents_image(library_root, contents)

        if img_src:
            fname      = f"{model_id}.webp"
            thumb_dest = thumbs_dir / fname
            if thumb_dest.exists() and not overwrite:
                skipped += 1
            else:
                try:
                    process_thumbnail(img_src, thumb_dest, thumb_size, thumb_quality, thumb_method)
                    processed += 1
                except Exception as e:
                    errors.append(f"{name}: {e}")
                    missing += 1
                    fname = None
            if fname:
                thumb_rel = f"thumbs/{fname}"
        else:
            missing += 1

        # scales/themes/types promoted to top-level for viewer compatibility.
        # They remain in extras too if those keys exist in Orynt3D attributes.
        models_out.append({
            "id":          model_id,
            "name":        name,
            "publisher":   publisher,
            "bundle":      bundle,
            "tags":        tags,
            "scales":      extras.get("scale", []),
            "themes":      extras.get("theme", []),
            "types":       extras.get("type", []),
            "extras":      extras,
            "collections": [c for c in coll_names if c not in exclude_colls],
            "notes":       notes,
            "thumb":       thumb_rel,
            "file_types":  exts,
            "path":        src_path,
        })

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n  Total unique models : {total_in_db}")
    print(f"  Exported            : {len(models_out)}")
    if demo_excluded:
        print(f"  Demo filtered out   : {demo_excluded}")
    if excluded:
        print(f"  NoExport excluded   : {excluded}")
    print(f"  Thumbs processed    : {processed}")
    print(f"  Thumbs skipped      : {skipped} (already existed)")
    print(f"  Thumbs missing      : {missing} (no image found)")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors[:20]: print(f"    {e}")
        if len(errors) > 20: print(f"    … and {len(errors)-20} more")

    # ── Bundle map ─────────────────────────────────────────────────────────────
    bundle_map: dict = {}
    for mo in models_out:
        if mo["publisher"] and mo["bundle"]:
            bundle_map.setdefault(mo["publisher"], set()).add(mo["bundle"])
    bundle_map = {k: sorted(v) for k, v in sorted(bundle_map.items())}

    # ── Meta ───────────────────────────────────────────────────────────────────
    active_collections = sorted({
        c for m in models_out for c in m["collections"]
    })

    meta = {
        "title":         cfg.get("catalog_title",    "The Forge"),
        "subtitle":      cfg.get("catalog_subtitle", "3D PRINT CATALOG"),
        "total_in_db":   total_in_db,
        "total":         len(models_out),
        "entry_limited": bool(entry_limit),
        "demo_mode":     bool(demo_pubs),
        "send_url":      cfg.get("send_url") or None,
        "publishers":    sorted({m["publisher"] for m in models_out if m["publisher"]}),
        "bundle_map":    bundle_map,
        "tags":          sorted({t for m in models_out for t in m["tags"]}),
        "collections":   active_collections,
        "extra_keys":    sorted(all_extra_keys),
        "extras_map":    {
            k: sorted({v for m in models_out for v in m["extras"].get(k, [])})
            for k in sorted(all_extra_keys)
        },
    }

    # ── Prune orphaned thumbnails ──────────────────────────────────────────────
    # Thumbs for models that were deleted or excluded would otherwise pile up
    # in git forever. Skipped on test/demo runs so a partial export can't wipe
    # thumbs you'll need for the full catalog.
    if not entry_limit and not demo_pubs:
        keep = {Path(mo["thumb"]).name for mo in models_out if mo["thumb"]}
        orphans = [f for f in thumbs_dir.glob("*.webp") if f.name not in keep]
        for f in orphans:
            f.unlink()
        if orphans:
            print(f"  Pruned {len(orphans)} orphaned thumbnail(s).")

    # ── catalog.json ───────────────────────────────────────────────────────────
    # One model per line so git diffs show exactly which models changed.
    catalog = output_dir / "catalog.json"
    with open(catalog, "w", encoding="utf-8", newline="\n") as f:
        f.write('{\n"meta": ')
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write(',\n"models": [\n')
        f.write(",\n".join(
            json.dumps(mo, ensure_ascii=False, separators=(",", ":"))
            for mo in models_out
        ))
        f.write("\n]\n}\n")
    print(f"\n  catalog.json → {catalog}")

    print(f"\n✓ Export done → {output_dir.resolve()}")
    print("  Next: commit and push the data folder to publish.")
    if entry_limit:
        print(f"  [TEST MODE] Set entry_limit to 0 for a full export. Don't publish this one.")
    if demo_pubs:
        print(f"  [DEMO MODE] Remove demo_publishers from config for full catalog.")


if __name__ == "__main__":
    show_disclaimer()
    cfg = load_config()
    export(cfg)
