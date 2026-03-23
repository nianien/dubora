"""
SQLite → PostgreSQL 数据迁移脚本

用法:
  python migrations/sqlite_to_pg.py <sqlite_db_path>

DATABASE_URL 从 .env.test 读取。

注意:
  - 目标 PostgreSQL 库必须已经用 schema_pg.sql 建好表
  - 脚本会清空 PG 目标表再导入（幂等，可重复执行）
  - 迁移完成后会重置 SERIAL 序列，保证后续 INSERT 不冲突
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import dotenv_values

import psycopg2
import psycopg2.extras

# 按 FK 依赖顺序排列（先父后子）
TABLES = [
    "users",
    "user_auths",
    "dramas",
    "episodes",
    "tasks",
    "events",
    "utterances",
    "cues",
    "utterance_cues",
    "roles",
    "glossary",
    "artifacts",
]

# 没有 SERIAL id 的表（复合主键），不需要重置序列
NO_SERIAL = {"utterance_cues"}


def get_columns(sqlite_conn, table: str) -> list[str]:
    """读取 SQLite 表的列名列表。"""
    cur = sqlite_conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def migrate_table(sqlite_conn, pg_conn, table: str):
    """迁移单张表的全部数据。"""
    columns = get_columns(sqlite_conn, table)
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    # 读取 SQLite 数据
    cur = sqlite_conn.execute(f"SELECT {col_list} FROM {table}")
    rows = cur.fetchall()

    if not rows:
        print(f"  {table}: 0 rows (skip)")
        return

    # 写入 PostgreSQL
    pg_cur = pg_conn.cursor()
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    # 批量插入
    psycopg2.extras.execute_batch(pg_cur, insert_sql, rows, page_size=500)
    pg_conn.commit()
    print(f"  {table}: {len(rows)} rows")


def reset_sequence(pg_conn, table: str):
    """重置 SERIAL 序列为当前最大 id + 1。"""
    pg_cur = pg_conn.cursor()
    seq_name = f"{table}_id_seq"
    pg_cur.execute(f"SELECT MAX(id) FROM {table}")
    max_id = pg_cur.fetchone()[0]
    if max_id is not None:
        pg_cur.execute(f"SELECT setval('{seq_name}', {max_id})")
    pg_conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument("sqlite_path", help="Path to SQLite database file")
    args = parser.parse_args()

    if not os.path.isfile(args.sqlite_path):
        print(f"Error: SQLite file not found: {args.sqlite_path}", file=sys.stderr)
        sys.exit(1)

    # 从 .env.test 读取 DATABASE_URL
    env_file = Path(__file__).resolve().parent.parent / ".env.test"
    env = dotenv_values(env_file)
    pg_url = env.get("DB_URL")
    if not pg_url:
        print(f"Error: DB_URL not found in {env_file}", file=sys.stderr)
        sys.exit(1)

    # 连接
    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.execute("PRAGMA foreign_keys=OFF")  # 读取时不需要 FK 检查

    pg_conn = psycopg2.connect(pg_url)

    print(f"Source: {args.sqlite_path}")
    print(f"Target: {pg_url}")
    print()

    # 建表（幂等，IF NOT EXISTS）
    schema_file = Path(__file__).resolve().parent.parent / "sql" / "schema_pg.sql"
    print(f"Applying schema from {schema_file.name}...")
    pg_cur = pg_conn.cursor()
    pg_cur.execute(schema_file.read_text())
    pg_conn.commit()

    # 清空目标表（逆序删除，先子后父）
    print("Truncating target tables...")
    pg_cur = pg_conn.cursor()
    pg_cur.execute(
        "TRUNCATE {} RESTART IDENTITY CASCADE".format(", ".join(TABLES))
    )
    pg_conn.commit()

    # 逐表迁移
    print("Migrating data...")
    for table in TABLES:
        migrate_table(sqlite_conn, pg_conn, table)

    # 重置序列
    print("\nResetting sequences...")
    for table in TABLES:
        if table not in NO_SERIAL:
            reset_sequence(pg_conn, table)

    print("\nDone.")

    sqlite_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
