"""整页最小编辑比对。

规则：增、删、替换一个字符各记一次改动（Levenshtein，替换代价为 1）。
同一对稿件（源版页稿 → 新校样）只算一次整页比对，供多条引用共用。

对源稿中的每个字符，统计它在“所有改动最少的对齐”里的去向：
  - 是否可能被删除或被替换（只要存在一种最优对齐如此，就记为可变更）；
  - 是否在所有最优对齐里都逐字匹配到新稿的同一个位置。
只有当一条引文的每个字符都唯一匹配、且落点在新稿中连续时，
才把引文登记到新位置；否则由调用方标为“待核对”。

判定依据（D 为两稿最小编辑距离，d 为前缀距离表，d2 为后缀距离表）：
  - old[k] 能在某最优对齐中逐字匹配 new[j]：old[k]==new[j] 且 d[k][j] + d2[k+1][j+1] == D
  - old[k] 能在某最优对齐中被删除：存在 j 使 d[k][j] + 1 + d2[k+1][j] == D
  - old[k] 能在某最优对齐中被替换：存在 j 使 d[k][j] + 1 + d2[k+1][j+1] == D
"""

from array import array


def _dp_rows(a: str, b: str) -> list:
    """完整 Levenshtein DP 表：rows[i][j] = a[:i] → b[:j] 的最少改动数。"""
    n, m = len(a), len(b)
    rows = [array("i", range(m + 1))]
    prev = rows[0]
    for i in range(1, n + 1):
        ai = a[i - 1]
        cur = array("i", [i])
        append = cur.append
        for j in range(1, m + 1):
            best = prev[j] + 1       # 删除 a[i-1]
            ins = cur[j - 1] + 1     # 插入 b[j-1]
            if ins < best:
                best = ins
            sub = prev[j - 1] + (0 if ai == b[j - 1] else 1)  # 逐字匹配或替换
            if sub < best:
                best = sub
            append(best)
        rows.append(cur)
        prev = cur
    return rows


class PageAlignment:
    """同一对稿件共用的整页比对结果。"""

    __slots__ = ("old", "new", "distance", "identical", "_unique_pos", "_always_matched")

    def __init__(self, old: str, new: str):
        self.old = old
        self.new = new
        self.identical = old == new
        if self.identical:
            # 两稿相同：唯一最优对齐就是恒等对齐，无需建表
            self.distance = 0
            self._unique_pos = None
            self._always_matched = None
            return
        n, m = len(old), len(new)
        fwd = _dp_rows(old, new)
        rev = _dp_rows(old[::-1], new[::-1])
        dist = fwd[n][m]
        self.distance = dist
        unique_pos = [None] * n
        always = [False] * n
        for k in range(n):
            fk = fwd[k]
            rk = rev[n - 1 - k]  # rk[m-j] = 后缀距离 d2[k+1][j]；rk[m-1-j] = d2[k+1][j+1]
            ch = old[k]
            found = -1
            count = 0
            alterable = False
            for j in range(m + 1):
                # 存在最优对齐把 old[k] 删除？
                if fk[j] + 1 + rk[m - j] == dist:
                    alterable = True
                if j < m:
                    tail = rk[m - 1 - j]
                    # 存在最优对齐把 old[k] 替换成 new[j]？
                    if fk[j] + 1 + tail == dist:
                        alterable = True
                    # 存在最优对齐把 old[k] 逐字匹配到 new[j]？
                    if ch == new[j] and fk[j] + tail == dist:
                        count += 1
                        found = j
            if count == 1 and not alterable:
                unique_pos[k] = found
                always[k] = True
        self._unique_pos = unique_pos
        self._always_matched = always

    def relocate(self, start: int, end: int):
        """把源稿区间 [start, end) 映射到新稿。

        所有改动最少的对齐都把该区间逐字对到新稿同一段连续位置时，
        返回 (new_start, new_end)；否则返回 None。
        """
        n = len(self.old)
        if not 0 <= start < end <= n:
            raise ValueError(f"区间越界: [{start}, {end})，源稿长 {n}")
        if self.identical:
            return (start, end)
        positions = []
        for k in range(start, end):
            if not self._always_matched[k]:
                return None
            positions.append(self._unique_pos[k])
        for a, b in zip(positions, positions[1:]):
            if b != a + 1:
                return None
        return (positions[0], positions[0] + (end - start))
