import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class MemoryRecord:
    chat_id: int
    user_id: int
    category: str
    memory_text: str
    confidence: float
    reason: str
    source_text: str
    created_at: str
    id: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore(Protocol):
    def save(self, record: MemoryRecord) -> None:
        ...

    def list_recent(self, chat_id: int, user_id: int, limit: int = 5) -> list[MemoryRecord]:
        ...


class JsonlMemoryStore:
    """File fallback store. Useful while LanceDB env is not ready."""

    def __init__(self, path: str = "long_memory_records.jsonl") -> None:
        self.path = path

    def save(self, record: MemoryRecord) -> None:
        if not record.id:
            record.id = str(uuid.uuid4())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def list_recent(self, chat_id: int, user_id: int, limit: int = 5) -> list[MemoryRecord]:
        if not os.path.exists(self.path):
            return []
        rows: list[MemoryRecord] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(data.get("chat_id", -1)) != int(chat_id):
                    continue
                if int(data.get("user_id", -1)) != int(user_id):
                    continue
                rows.append(MemoryRecord(**data))
        rows.sort(key=lambda x: x.created_at, reverse=True)
        return rows[:limit]


class LanceMemoryStore:
    """LanceDB-backed store for long-term memory records."""

    def __init__(self, db_dir: str = "./lancedb_data", table_name: str = "long_memory") -> None:
        try:
            import lancedb  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "lancedb is not available. Install it or use JsonlMemoryStore."
            ) from exc

        self._lancedb = lancedb
        self._db = self._lancedb.connect(db_dir)
        self._table_name = table_name

    def _table_exists(self) -> bool:
        return self._table_name in self._db.table_names()

    def save(self, record: MemoryRecord) -> None:
        if not record.id:
            record.id = str(uuid.uuid4())
        row = asdict(record)
        if self._table_exists():
            self._db.open_table(self._table_name).add([row])
        else:
            self._db.create_table(self._table_name, data=[row])

    def list_recent(self, chat_id: int, user_id: int, limit: int = 5) -> list[MemoryRecord]:
        if not self._table_exists():
            return []
        table = self._db.open_table(self._table_name)
        rows = table.to_arrow().to_pylist()
        parsed: list[MemoryRecord] = []
        for row in rows:
            if int(row.get("chat_id", -1)) != int(chat_id):
                continue
            if int(row.get("user_id", -1)) != int(user_id):
                continue
            try:
                parsed.append(MemoryRecord(**row))
            except TypeError:
                continue
        parsed.sort(key=lambda x: x.created_at, reverse=True)
        return parsed[:limit]
