"""观察、核对状态、变化记录共用的单调序号。

三张表各自自增的话，号只在自己表里有意义，跨表比大小就是错的。
"""


def next_id(conn):
    """取下一个序号。必须和对应的 INSERT 在同一个事务里。"""
    conn.execute("UPDATE record_sequence SET value = value + 1 WHERE id = 1")
    row = conn.execute("SELECT value FROM record_sequence WHERE id = 1").fetchone()
    return row["value"]


def current(conn):
    row = conn.execute("SELECT value FROM record_sequence WHERE id = 1").fetchone()
    return row["value"] if row else 0
