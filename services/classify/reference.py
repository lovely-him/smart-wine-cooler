"""参考库的内存索引：**按类别 id** 组织（名字可改、可合并，id 才稳定）。

同一类别可以有多张原型（正面/侧面/背面），检索时取"最相似的那一张"，
所以这里存的是矩阵而不是均值。
"""

import numpy as np

# ── 路径与常量 ──
DEF_DEDUPE = 0.95


# ── 类 ──
class ReferenceIndex:
    def __init__(self):
        self.groups = {}  # {category_id: ndarray(N, D)}
        self.names = {}  # {category_id: 名字}
        self.dim = None

    def __len__(self):
        return sum(len(m) for m in self.groups.values())

    def categories(self):
        return [self.names[cid] for cid in sorted(self.groups)]

    def name_of(self, category_id):
        return self.names.get(category_id, f"#{category_id}")

    def add(self, category_id, name, vector):
        vec = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if self.dim is None:
            self.dim = vec.shape[1]
        elif vec.shape[1] != self.dim:
            raise ValueError(f"特征维度不一致: {vec.shape[1]} vs {self.dim}")
        if category_id in self.groups:
            self.groups[category_id] = np.vstack([self.groups[category_id], vec])
        else:
            self.groups[category_id] = vec
        self.names[category_id] = name

    def max_similarity(self, category_id, vector):
        matrix = self.groups.get(category_id)
        if matrix is None or len(matrix) == 0:
            return 0.0
        return float(np.max(matrix @ np.asarray(vector, dtype=np.float32).reshape(-1)))

    def should_add(self, category_id, vector, dedupe=DEF_DEDUPE, cap=0):
        """同类里已有高度相似的样本就不重复入库；超过配额则拒绝。"""
        if cap and len(self.groups.get(category_id, ())) >= int(cap):
            return False, "已达该类配额"
        if dedupe and self.max_similarity(category_id, vector) >= float(dedupe):
            return False, "与已有参考图重复"
        return True, ""

    @classmethod
    def from_store(cls, store):
        index = cls()
        for item in store.reference_groups():
            index.add(item["category_id"], item["name"], item["vector"])
        return index
