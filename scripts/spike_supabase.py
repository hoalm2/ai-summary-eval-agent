from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.persist import SupabaseStore


def main() -> None:
    load_dotenv()
    store = SupabaseStore()
    key = f"spike_{uuid.uuid4().hex}"
    value = {"ok": True}
    store.set_state(key, value)
    read_value = store.get_state(key)
    store.client.table("agent_state").delete().eq("key", key).execute()
    print(json.dumps({"insert_read_delete_ok": read_value == value, "key": key}, ensure_ascii=False))


if __name__ == "__main__":
    main()
