"""
Migration: cues / utterances 引入 role_id 列 + 历史数据反向修复

变更：
  1. ALTER TABLE cues ADD COLUMN role_id INTEGER REFERENCES roles(id)
  2. ALTER TABLE utterances ADD COLUMN role_id INTEGER REFERENCES roles(id)
  3. 反向修复：历史上 cues.speaker 被前端改写成 role.id 整数的情况
       cue.speaker.isdigit() AND int(cue.speaker) ∈ roles.id 同 drama
       → cue.role_id = int(cue.speaker), cue.speaker = ''
  4. 同步 utterances.role_id（通过 utterance_cues 关联，从 group[0].role_id 复制）

约束（按 IRON RULES）：本脚本仅在迁移目录运行，packages/ 里不写任何兼容旧逻辑。

用法：
  python migrations/001_introduce_role_id.py
  环境：从 .env 读 DB_URL
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import psycopg2
import psycopg2.extras


def get_conn():
    # 从项目根的 .env 读取
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)
    db_url = os.getenv("DB_URL")
    if not db_url:
        print("ERROR: DB_URL not set in .env", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(db_url)


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name=%s AND column_name=%s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def add_role_id_column(cur, table: str) -> None:
    if column_exists(cur, table, "role_id"):
        print(f"  {table}.role_id already exists, skipping ALTER")
        return
    print(f"  ALTER TABLE {table} ADD COLUMN role_id ...")
    cur.execute(
        f"ALTER TABLE {table} ADD COLUMN role_id INTEGER REFERENCES roles(id)"
    )


def backfill_cues(cur) -> None:
    """对每个 drama 反向修复 cues.speaker → cues.role_id。"""
    cur.execute("SELECT id, name FROM dramas ORDER BY id")
    dramas = cur.fetchall()

    total_updated = 0
    for drama_id, drama_name in dramas:
        cur.execute(
            "SELECT id FROM roles WHERE drama_id=%s",
            (drama_id,),
        )
        role_ids = {r[0] for r in cur.fetchall()}
        if not role_ids:
            continue

        # 找该 drama 下所有 cue.speaker 是 role.id 的
        cur.execute(
            """
            SELECT c.id, c.speaker
            FROM cues c JOIN episodes e ON c.episode_id = e.id
            WHERE e.drama_id = %s AND c.speaker ~ '^[0-9]+$'
            """,
            (drama_id,),
        )
        rows = cur.fetchall()

        updated_in_drama = 0
        for cue_id, speaker_str in rows:
            try:
                spk_int = int(speaker_str)
            except (ValueError, TypeError):
                continue
            if spk_int in role_ids:
                cur.execute(
                    "UPDATE cues SET role_id=%s, speaker='' WHERE id=%s",
                    (spk_int, cue_id),
                )
                updated_in_drama += 1

        if updated_in_drama:
            print(f"  drama {drama_id} '{drama_name}': 反向修复 {updated_in_drama} 条 cue")
            total_updated += updated_in_drama

    print(f"  cues 反向修复合计：{total_updated} 条")


def sync_utterances(cur) -> None:
    """从 cues 通过 utterance_cues 同步 utterance.role_id（取 group 内任意一条 cue 的 role_id）。

    新合并规则保证：同一 utterance 下所有 cue 必然 same_identity（role_id 相同 或 都 NULL）。
    遗留数据可能违反此约束，迁移后建议重跑 calculate_utterances 重 build utterances。
    本脚本只做最佳努力：取 MIN(c.id) 那条 cue 的 role_id。
    """
    print("  同步 utterances.role_id（取关联 cue 中任意一条）...")
    cur.execute(
        """
        WITH first_cue AS (
            SELECT uc.utterance_id, MIN(c.id) AS cue_id
            FROM utterance_cues uc JOIN cues c ON uc.cue_id = c.id
            GROUP BY uc.utterance_id
        )
        UPDATE utterances u
        SET role_id = c.role_id
        FROM first_cue f
        JOIN cues c ON f.cue_id = c.id
        WHERE u.id = f.utterance_id
        """,
    )
    print(f"  utterances 同步：{cur.rowcount} 条")


def main():
    conn = get_conn()
    cur = conn.cursor()

    print("=== 1. ALTER schema ===")
    add_role_id_column(cur, "cues")
    add_role_id_column(cur, "utterances")
    conn.commit()

    print("\n=== 2. 反向修复 cues.speaker → cues.role_id ===")
    backfill_cues(cur)
    conn.commit()

    print("\n=== 3. 同步 utterances.role_id ===")
    sync_utterances(cur)
    conn.commit()

    print("\n=== 迁移完成 ===")
    print("提示：执行后建议给每个有数据的 drama 重跑 calculate_utterances，")
    print("    确保 utterance 按新合并规则（role 优先）正确分组。")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
