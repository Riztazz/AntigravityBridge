"""Remove conversations from the Antigravity summaries index.

Usage: python purge_index.py <conversation_summaries.db> <ids file, one id per line> [--dry]
Deletes the rows in conversation_summaries, conversations_fts and conversation_parents for the
given ids, then checkpoints the WAL. The App must be closed. Prints the counts.
"""
import sqlite3
import sys


def main() -> None:
    db_path, ids_path = sys.argv[1], sys.argv[2]
    dry = "--dry" in sys.argv
    ids = [line.strip() for line in open(ids_path, encoding="utf-8") if line.strip()]
    con = sqlite3.connect(db_path)
    marks = ",".join("?" * len(ids))
    tables = [("conversation_summaries", "conversation_id"),
              ("conversations_fts", "conversation_id"),
              ("conversation_parents", "conversation_id")]
    for table, column in tables:
        count = con.execute(f"select count(*) from {table} where {column} in ({marks})", ids).fetchone()[0]
        total = con.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"{table}: {count} of {total} rows match")
        if not dry and count:
            con.execute(f"delete from {table} where {column} in ({marks})", ids)
    if not dry:
        con.commit()
        con.execute("pragma wal_checkpoint(truncate)")
        left = con.execute("select count(*) from conversation_summaries").fetchone()[0]
        print(f"conversation_summaries now holds {left} rows; integrity: {con.execute('pragma integrity_check').fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main()
