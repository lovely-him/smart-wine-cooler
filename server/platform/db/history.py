"""历史记录的归属：一个 id 是观察、是确认，还是根本不能单独删。

观察与确认共用一个单调序列，界面上是一份 id 列表；删之前必须先认出各是哪一类。
"""

from . import checkpoints, observations


def classify(db, ids):
    """返回（观察 id、确认 id、认不出的）。认不出来的是事件——事件没有独立身份。"""
    observation_ids = []
    checkpoint_ids = []
    unknown = []
    for entry_id in ids:
        if observations.load_observation(db, entry_id) is not None:
            observation_ids.append(entry_id)
        elif checkpoints.get_checkpoint(db, entry_id) is not None:
            checkpoint_ids.append(entry_id)
        else:
            unknown.append(entry_id)
    return observation_ids, checkpoint_ids, unknown
