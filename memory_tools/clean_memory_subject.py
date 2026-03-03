import argparse
import os
import re
from datetime import datetime, timezone

import lancedb


def normalize_subject(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    s = s.replace("使用者", "主人")
    s = re.sub(r"^我", "主人", s)
    s = re.sub(r"^叫我", "主人希望被稱呼為", s)
    s = re.sub(r"^我的名字叫", "主人的名字是", s)
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-fix memory_text subject to 主人")
    parser.add_argument("--db-dir", default=os.getenv("LONG_MEMORY_LANCE_DIR", "./lancedb_data"))
    parser.add_argument("--table", default=os.getenv("LONG_MEMORY_LANCE_TABLE", "long_memory"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = lancedb.connect(args.db_dir)
    listing = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    tables = listing if isinstance(listing, list) else getattr(listing, "tables", [])
    if args.table not in tables:
        print(f"Table not found: {args.table}")
        print(f"Available tables: {tables}")
        return

    old_table = db.open_table(args.table)
    old_rows = old_table.to_arrow().to_pylist()

    new_rows = []
    changed = 0
    for row in old_rows:
        old_text = str(row.get("memory_text", ""))
        new_text = normalize_subject(old_text)
        if new_text != old_text:
            changed += 1
        row["memory_text"] = new_text
        new_rows.append(row)

    print(f"rows={len(old_rows)}, changed={changed}, dry_run={args.dry_run}")
    if args.dry_run:
        return

    db.drop_table(args.table)
    db.create_table(args.table, data=new_rows)
    print(
        f"cleaned at {datetime.now(timezone.utc).isoformat()} -> table={args.table}, changed={changed}"
    )


if __name__ == "__main__":
    main()
