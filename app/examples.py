"""内置示例：一组可直接成功试算，另两组分别演示压差不可行与温区无安全段。"""


def _node(feature, threshold, left, right):
    return {"feature": feature, "threshold": threshold, "left": left, "right": right}


def _leaf(category):
    return {"leaf": category}


# 示例 1：可成功试算。
# 两树仅在“高温且后压偏高”时分歧（原树 REJECT、候选 REWORK）；
# 工况温区 150..300 中，150..200 全程两树一致，且 200 之后出现分歧，
# 因此最长安全温区为 [150,200]，共 51 个温度。
ORIGINAL_1 = _node(
    "temperature", 200,
    _leaf("PASS"),
    _node("back", 400, _leaf("PASS"), _leaf("REJECT")),
)
CANDIDATE_1 = _node(
    "temperature", 200,
    _leaf("PASS"),
    _node("back", 400, _leaf("PASS"), _leaf("REWORK")),
)
DRAFT_1 = {
    "temperature": [150, 300],
    "front": [100, 200],
    "back": [150, 500],
    "diff": [0, 300],
}

# 示例 2：压差不可行。后压最大 120、前压最小 100，压差至多 20，
# 无法满足 [100, 200]，任何温度都没有合规压力组合。
ORIGINAL_2 = ORIGINAL_1
CANDIDATE_2 = CANDIDATE_1
DRAFT_2 = {
    "temperature": [150, 300],
    "front": [100, 200],
    "back": [100, 120],
    "diff": [100, 200],
}

# 示例 3：压力可行但两树在工况温区每个温度上都存在分歧读数。
# 原树恒为 PASS；候选树按温度 200 分流为 A/B，温区 150..300 内
# 低温段候选为 A、高温段候选为 B，两段均与原树类别不同 → 无安全温区。
ORIGINAL_3 = _leaf("PASS")
CANDIDATE_3 = _node("temperature", 200, _leaf("A"), _leaf("B"))
DRAFT_3 = {
    "temperature": [150, 300],
    "front": [100, 200],
    "back": [150, 500],
    "diff": [0, 300],
}

EXAMPLES = [
    {
        "key": "solvable",
        "title": "示例一：可成功试算（接管温区 [150,200]）",
        "original_tree": ORIGINAL_1,
        "candidate_tree": CANDIDATE_1,
        "draft": DRAFT_1,
    },
    {
        "key": "infeasible_pressure",
        "title": "示例二：压差区间无解（无合规压力组合）",
        "original_tree": ORIGINAL_2,
        "candidate_tree": CANDIDATE_2,
        "draft": DRAFT_2,
    },
    {
        "key": "no_safe_zone",
        "title": "示例三：压力可行但无一致温区",
        "original_tree": ORIGINAL_3,
        "candidate_tree": CANDIDATE_3,
        "draft": DRAFT_3,
    },
]
