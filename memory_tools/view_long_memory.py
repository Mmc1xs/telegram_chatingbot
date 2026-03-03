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

    parser = argparse.ArgumentParser(description="View long-term memory records from LanceDB.")
    parser.add_argument("--db-dir", default=os.getenv("LONG_MEMORY_LANCE_DIR", "./lancedb_data"))
    parser.add_argument("--table", default=os.getenv("LONG_MEMORY_LANCE_TABLE", "long_memory"))
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    db = lancedb.connect(args.db_dir)
    table_listing = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    tables = table_listing if isinstance(table_listing, list) else getattr(table_listing, "tables", [])
    if args.table not in tables:
        print(f"Table not found: {args.table}")
        print(f"Available tables: {tables}")
        return

    table = db.open_table(args.table)
    rows = table.to_arrow().to_pylist()

    filtered = []
    for row in rows:
        if args.chat_id is not None and int(row.get("chat_id", -1)) != args.chat_id:
            continue
        if args.user_id is not None and int(row.get("user_id", -1)) != args.user_id:
            continue
        filtered.append(row)

    filtered.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    result = filtered[: max(1, args.limit)]

    safe_print(f"db_dir={args.db_dir}")
    safe_print(f"table={args.table}")
    safe_print(f"total_rows={len(rows)}, filtered_rows={len(filtered)}, showing={len(result)}")
    safe_print("-" * 80)
    for i, row in enumerate(result, start=1):
        safe_print(f"[{i}] id={row.get('id')}")
        safe_print(f"    chat_id={row.get('chat_id')} user_id={row.get('user_id')}")
        safe_print(f"    category={row.get('category')} confidence={row.get('confidence')}")
        safe_print(f"    memory_text={row.get('memory_text')}")
        safe_print(f"    source_text={row.get('source_text')}")
        safe_print(f"    created_at={row.get('created_at')}")
        safe_print("-" * 80)


if __name__ == "__main__":
    main()
