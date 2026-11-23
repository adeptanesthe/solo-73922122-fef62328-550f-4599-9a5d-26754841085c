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


# ======================================================================
# 备用控制器规则转换示例
# 压力码 = 原值高 8 位：码 c 覆盖原值 [256c, 256c+255]。
# ======================================================================

# 转换示例 1：无确认方案，纯原树分流，转换成功可下载。
# 阈值刻意取 256 码块边界（5119=20*256-1、2559=10*256-1），同码格不会分裂类别。
CONV_ORIG_1 = _node(
    "temperature", 5119,
    _leaf("PASS"),
    _node("back", 2559, _leaf("REWORK"), _leaf("REJECT")),
)
CONV_CONDITION_1 = {
    "temperature": [4000, 6000],
    "front": [512, 1023],     # 码 2..3，整码块
    "back": [256, 4095],      # 码 1..15，整码块
    "diff": [-1000, 2000],
}

# 转换示例 2：压差无解（后压最大 500、前压最小 1000，b-f 至多 -500，
# 无法满足 [100,500]），工况无合规读数。
CONV_ORIG_2 = CONV_ORIG_1
CONV_CONDITION_2 = {
    "temperature": [0, 100],
    "front": [1000, 2000],
    "back": [0, 500],
    "diff": [100, 500],
}

# 转换示例 3：同码格类别冲突，禁用下载。
# 两树在前压阈值 600（落在码 2 块 [512,767] 内部）处分歧：原树 A/B、候选 A/C。
# 确认草稿只覆盖前压 [0,600]（两树在该侧同为 A），试算可成功并确认；
# 转换工况把前压放宽到整个码 2 块 [512,767]、压差放宽到 [-1000,1000]：
# 码格 (fc=2, bc=2) 内，f≥601 的读数在获准盒子外走原树得 B，
# f≤600 的读数获准走候选树得 A —— 同温度同两个压力码却有两个类别 → 冲突。
CONV_ORIG_3 = _node("front", 600, _leaf("A"), _leaf("B"))
CONV_CAND_3 = _node("front", 600, _leaf("A"), _leaf("C"))
CONV_DRAFT_3 = {
    "temperature": [100, 200],
    "front": [0, 600],
    "back": [512, 2048],
    "diff": [0, 400],
}
CONV_CONDITION_3 = {
    "temperature": [100, 200],
    "front": [512, 767],
    "back": [512, 2048],
    "diff": [-1000, 1000],
}

CONVERSION_EXAMPLES = [
    {
        "key": "conv_solvable",
        "title": "转换示例一：无冲突，可下载 JSON 规则（纯原树分流）",
        "description": "只需保存原树并填写转换工况；阈值均在 256 码块边界上，转换成功。",
        "original_tree": CONV_ORIG_1,
        "candidate_tree": None,
        "draft": None,
        "condition": CONV_CONDITION_1,
        "needs_confirmation": False,
    },
    {
        "key": "conv_infeasible",
        "title": "转换示例二：工况压差无解（无合规读数，说明原因）",
        "description": "后压-前压无法落入压差闭区间，转换返回无解原因，不提供下载。",
        "original_tree": CONV_ORIG_2,
        "candidate_tree": None,
        "draft": None,
        "condition": CONV_CONDITION_2,
        "needs_confirmation": False,
    },
    {
        "key": "conv_conflict",
        "title": "转换示例三：同压力码分类冲突（自动试算确认后转换，禁用下载）",
        "description": "页面自动保存两树与草稿、试算并确认，再用放宽后的转换工况触发冲突。",
        "original_tree": CONV_ORIG_3,
        "candidate_tree": CONV_CAND_3,
        "draft": CONV_DRAFT_3,
        "condition": CONV_CONDITION_3,
        "needs_confirmation": True,
    },
]
