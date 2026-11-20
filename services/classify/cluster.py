"""特征聚类：把"看起来是同一种"的未知目标分到一组，好让大模型一簇只问一次。

贪心实现（质心 + 余弦阈值）足够：簇数少、样本少，跑离群检测没有收益。
阈值取保守值——宁可多问几次，也不要把不同商品串成一个名字（串了会写错参考库）。
"""

import numpy as np

# ── 路径与常量 ──
DEF_THRESHOLD = 0.80


# ── 函数 ──
def cluster_vectors(vectors, threshold=DEF_THRESHOLD):
    """返回 (成员下表列表, 质心列表)：与已有质心相似度 ≥ 阈值就并入，否则开新簇。"""
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.size == 0:
        return [], []
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    centroids, members = [], []
    for index in range(len(matrix)):
        vec = matrix[index]
        best, best_score = -1, -1.0
        for slot, centroid in enumerate(centroids):
            score = float(centroid @ vec)
            if score > best_score:
                best, best_score = slot, score
        if best >= 0 and best_score >= threshold:
            members[best].append(index)
            group = matrix[members[best]]
            centroid = group.mean(axis=0)
            centroids[best] = centroid / (np.linalg.norm(centroid) + 1e-12)
        else:
            centroids.append(vec.copy())
            members.append([index])
    return members, centroids
