import argparse
import os

import lancedb

def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value


def safe_print(line: str) -> None:
    encoded = str(line).encode("cp932", errors="replace").decode("cp932", errors="replace")
    print(encoded)


def main() -> None:
    load_env_file(".env")

    parser = argparse.ArgumentParser(description="Delete specific long-memory rows by id.")
    parser.add_argument("--db-dir", default=os.getenv("LONG_MEMORY_LANCE_DIR", "./lancedb_data"))
    parser.add_argument("--table", default=os.getenv("LONG_MEMORY_LANCE_TABLE", "long_memory"))
    parser.add_argument("--ids", nargs="+", required=True, help="One or more record IDs to delete.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted.")
    args = parser.parse_args()

    db = lancedb.connect(args.db_dir)
    listing = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    tables = listing if isinstance(listing, list) else getattr(listing, "tables", [])
    if args.table not in tables:
        print(f"Table not found: {args.table}")
        print(f"Available tables: {tables}")
        return

    table = db.open_table(args.table)
    rows = table.to_arrow().to_pylist()

    id_set = {x.strip() for x in args.ids if x.strip()}
    to_delete = [r for r in rows if str(r.get("id", "")).strip() in id_set]
    missing = sorted(id_set - {str(r.get("id", "")).strip() for r in to_delete})

    safe_print(f"table={args.table}")
    safe_print(f"requested_ids={len(id_set)} matched={len(to_delete)} missing={len(missing)} dry_run={args.dry_run}")
    for r in to_delete:
        safe_print(f"- delete id={r.get('id')} category={r.get('category')} memory_text={r.get('memory_text')}")
    for mid in missing:
        safe_print(f"- missing id={mid}")

    if args.dry_run or not to_delete:
        return

    escaped_ids = [i.replace("'", "''") for i in id_set]
    for one_id in escaped_ids:
        table.delete(f"id = '{one_id}'")
    safe_print("delete_done=true")


if __name__ == "__main__":
    main()
