"""决策树领域逻辑：校验、叶子盒子抽取、读数分类。

树的 JSON 表示：
- 叶子节点：{"leaf": "类别名"}
- 分支节点：{"feature": "temperature" | "front" | "back",
            "threshold": 整数(0..65535),
            "left": 子树,   # 读数 <= threshold 时走左子树
            "right": 子树}  # 读数 >  threshold 时走右子树
"""

FEATURES = ("temperature", "front", "back")
MAX_LEAVES = 128
READING_MIN = 0
READING_MAX = 65535
MAX_DEPTH = 64  # 防御性深度上限，防止过深 JSON 触发递归问题


def _is_strict_int(value):
    """只接受真正的 int，拒绝 bool（Python 中 bool 是 int 的子类）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_tree(node, depth=0):
    """递归校验树结构。合法返回 None，非法抛出 ValueError（中文原因）。"""
    if depth > MAX_DEPTH:
        raise ValueError(f"决策树深度超过 {MAX_DEPTH}，无法接受")
    if not isinstance(node, dict):
        raise ValueError("节点必须是 JSON 对象")

    if "leaf" in node:
        category = node["leaf"]
        if not isinstance(category, str) or not category.strip():
            raise ValueError("叶子节点的 leaf 必须是非空字符串（分拣类别）")
        if len(node) != 1:
            raise ValueError("叶子节点只能包含 leaf 一个字段")
        return

    missing = [key for key in ("feature", "threshold", "left", "right") if key not in node]
    if missing:
        raise ValueError("分支节点缺少字段：" + "、".join(missing))
    if set(node.keys()) != {"feature", "threshold", "left", "right"}:
        raise ValueError("分支节点只能包含 feature/threshold/left/right 四个字段")
    if node["feature"] not in FEATURES:
        raise ValueError("feature 只能是 temperature、front 或 back")
    if not _is_strict_int(node["threshold"]):
        raise ValueError("threshold 必须是整数")
    if not READING_MIN <= node["threshold"] <= READING_MAX:
        raise ValueError(f"threshold 必须在 {READING_MIN}..{READING_MAX} 闭区间内")

    validate_tree(node["left"], depth + 1)
    validate_tree(node["right"], depth + 1)


def count_leaves(node):
    if isinstance(node, dict) and "leaf" in node:
        return 1
    return count_leaves(node["left"]) + count_leaves(node["right"])


def validate_tree_full(node):
    """完整校验（结构 + 叶子数量上限）。"""
    validate_tree(node)
    leaves = count_leaves(node)
    if leaves > MAX_LEAVES:
        raise ValueError(f"叶子数量为 {leaves}，超过每棵树最多 {MAX_LEAVES} 个叶子的限制")
    return leaves


def leaf_boxes(node, bounds=None):
    """抽取每个叶子对应的轴对齐整数盒子。

    bounds 为三个特征各自的闭区间 [lo, hi]，返回 [(bounds, 类别), ...]。
    分支 x <= t 把区间切成 [lo, min(hi,t)] 与 [max(lo,t+1), hi]；
    若某一侧区间为空，说明没有任何读数会走到该子树，直接丢弃。
    """
    if bounds is None:
        bounds = {f: [READING_MIN, READING_MAX] for f in FEATURES}

    # 任一特征区间已空，说明没有任何读数能到达此节点，整体剪枝
    #（叶子也不例外，否则会产出 lo>hi 的空盒子污染下游相交判断）。
    if any(lo > hi for lo, hi in bounds.values()):
        return []

    if "leaf" in node:
        return [(bounds, node["leaf"])]

    feature = node["feature"]
    threshold = node["threshold"]
    lo, hi = bounds[feature]

    result = []

    left_hi = min(hi, threshold)
    if lo <= left_hi:
        left_bounds = {f: list(v) for f, v in bounds.items()}
        left_bounds[feature][1] = left_hi
        result.extend(leaf_boxes(node["left"], left_bounds))

    right_lo = max(lo, threshold + 1)
    if right_lo <= hi:
        right_bounds = {f: list(v) for f, v in bounds.items()}
        right_bounds[feature][0] = right_lo
        result.extend(leaf_boxes(node["right"], right_bounds))

    return result


def classify(node, readings):
    """按读数在树中路由，返回叶子类别。readings 为含三个特征的 dict。"""
    while "leaf" not in node:
        if readings[node["feature"]] <= node["threshold"]:
            node = node["left"]
        else:
            node = node["right"]
    return node["leaf"]
