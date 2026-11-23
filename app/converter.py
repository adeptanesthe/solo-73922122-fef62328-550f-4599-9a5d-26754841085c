"""备用控制器规则转换（不枚举真值或压力码组合）。

压缩约定：备用控制器读到完整 16 位温度 T；前压/后压只取原 16 位读数的高 8 位：

    front_code = front >> 8
    back_code  = back >> 8          # 压力码取值 0..255，每个码对应 256 个原值

工艺员给出四个整数闭区间（温度、前压原值、后压原值、实际压差 back-front），
域内满足压差约束的整数读数称为“合规真值”。每条合规真值按**转换开始时生效的
分流**（已确认方案存在且读数获准则候选树，否则原树）得到分拣类别。

输出规则每条含类别与四个闭区间：温度、前压码、后压码、后压码减前压码；
备用控制器的输入四项全部落入区间才命中。

本模块全程使用区间/盒子运算，从不枚举真值或压力码组合：
  - 决策树展开为至多 128 个轴对齐叶子盒子（区间表示）；
  - 压力轴按 256 宽码块切成“整码段”与至多两个边界单码：
      整码段上压差可行性写成 k=bc-fc 的闭式整数不等式；
      边界单码用裁剪后的具体原值区间做闭式判据；
  - 冲突检测为规则形码区之间的区间相交，复杂度仅与叶子数/码段数有关。
"""

from collections import defaultdict

from .solver import pressure_diff_feasible
from .tree import FEATURES, leaf_boxes

BLOCK = 256            # 每个压力码覆盖的原值宽度
MAX_CONFLICTS_LISTED = 50  # 网页/响应中最多列出的冲突组数（总数仍如实返回）


# ---------------- 基础区间工具 ----------------

def _ceil_div(a, b):
    """整数向上取整除法（a 允许为负）。"""
    return -((-a) // b)


def _intersects(a, b):
    return all(a[f][0] <= b[f][1] and b[f][0] <= a[f][1] for f in FEATURES)


def _intersect(a, b):
    return {f: [max(a[f][0], b[f][0]), min(a[f][1], b[f][1])] for f in FEATURES}


def _subtract_box(a, b):
    """轴对齐盒子差 a - b，返回至多 6 个互不相交的盒子（不含交集）。"""
    if not _intersects(a, b):
        return [a]
    pieces = []
    rest = {f: list(v) for f, v in a.items()}
    for feat in FEATURES:
        lo, hi = rest[feat]
        blo, bhi = b[feat]
        if lo < blo:
            piece = {f: list(v) for f, v in rest.items()}
            piece[feat] = [lo, min(hi, blo - 1)]
            pieces.append(piece)
        if hi > bhi:
            piece = {f: list(v) for f, v in rest.items()}
            piece[feat] = [max(lo, bhi + 1), hi]
            pieces.append(piece)
        rest[feat] = [max(lo, blo), min(hi, bhi)]
        if rest[feat][0] > rest[feat][1]:
            break
    return pieces


def _witness(f_lo, f_hi, b_lo, b_hi, d_lo, d_hi):
    """闭区间上闭式构造一个满足 d_lo <= b-f <= d_hi 的 (f, b)，无解返回 None。"""
    f = max(f_lo, b_lo - d_hi)
    b = max(b_lo, f + d_lo)
    if f <= f_hi and b <= b_hi and b - f <= d_hi:
        return f, b
    return None


# ---------------- 压力码段 ----------------

def _pressure_segments(v_lo, v_hi):
    """把原值闭区间 [v_lo, v_hi] 拆成压力码段。

    返回列表，每项为：
      ("full",   c_lo, c_hi, None, None)  整码段：段内每个码的整个 256 宽码块
                                          都落在 [v_lo, v_hi] 内（至多一段）；
      ("single", c, c, lo, hi)            边界单码：码块被裁剪后的具体原值区间
                                          （至多两个）。
    """
    c_first = v_lo >> 8
    c_last = v_hi >> 8
    full_lo = _ceil_div(v_lo, BLOCK)
    full_hi = (v_hi - (BLOCK - 1)) // BLOCK

    singles = []
    for c in (c_first, c_last):
        if full_lo <= c <= full_hi:
            continue
        if singles and singles[-1][1] == c:
            continue
        blo = max(v_lo, c * BLOCK)
        bhi = min(v_hi, c * BLOCK + BLOCK - 1)
        singles.append(("single", c, c, blo, bhi))

    segments = list(singles)
    if full_lo <= full_hi:
        segments.append(("full", full_lo, full_hi, None, None))
    return segments


def _feasible_code_rectangles(f_seg, b_seg, d_lo, d_hi):
    """给定前压码段、后压码段与压差闭区间，求可行的码矩形（不枚举码组合）。

    每个矩形为 dict：fc/bc/k 各为整数闭区间（k = bc - fc），并记录两轴是否
    整码、单码的具体原值范围，供后续在矩形内任意码格上构造真值见证。
    整码段利用 256 宽码块上 b-f 的取值范围 [256k-255, 256k+255] 做闭式推导；
    含单码时用裁剪后的具体原值区间推导，条件均为充要条件，不产生伪可行。
    """
    f_kind, f_cl, f_ch, f_lo, f_hi = f_seg
    b_kind, b_cl, b_ch, b_lo, b_hi = b_seg
    rects = []

    if f_kind == "full" and b_kind == "full":
        # 整码/整码：k=bc-fc 可行当且仅当 [256k-255, 256k+255] 与 [d_lo,d_hi] 相交。
        k_lo = max(b_cl - f_ch, _ceil_div(d_lo - (BLOCK - 1), BLOCK))
        k_hi = min(b_ch - f_cl, (d_hi + (BLOCK - 1)) // BLOCK)
        if k_lo <= k_hi:
            rects.append({
                "fc": [f_cl, f_ch], "bc": [b_cl, b_ch], "k": [k_lo, k_hi],
                "f_full": True, "b_full": True,
                "f_range": None, "b_range": None,
            })
        return rects

    if f_kind == "full" and b_kind == "single":
        # 后压固定原值区间 [b_lo,b_hi]：对给定 fc，b-f 范围为
        # [b_lo-255-256fc, b_hi-256fc]，与压差区间相交给出 fc 的充要上下界。
        fc_lo = max(f_cl, _ceil_div(b_lo - (BLOCK - 1) - d_hi, BLOCK))
        fc_hi = min(f_ch, (b_hi - d_lo) // BLOCK)
        if fc_lo <= fc_hi:
            bc = b_cl
            rects.append({
                "fc": [fc_lo, fc_hi], "bc": [bc, bc],
                "k": [bc - fc_hi, bc - fc_lo],
                "f_full": True, "b_full": False,
                "f_range": None, "b_range": [b_lo, b_hi],
            })
        return rects

    if f_kind == "single" and b_kind == "full":
        # 前压固定原值区间 [f_lo,f_hi]：对给定 bc，b-f 范围为
        # [256bc-f_hi, 256bc+255-f_lo]，同理闭式求 bc 上下界。
        bc_lo = max(b_cl, _ceil_div(d_lo + f_lo - (BLOCK - 1), BLOCK))
        bc_hi = min(b_ch, (d_hi + f_hi) // BLOCK)
        if bc_lo <= bc_hi:
            fc = f_cl
            rects.append({
                "fc": [fc, fc], "bc": [bc_lo, bc_hi],
                "k": [bc_lo - fc, bc_hi - fc],
                "f_full": False, "b_full": True,
                "f_range": [f_lo, f_hi], "b_range": None,
            })
        return rects

    # 单码/单码：一个固定码格，闭式判据 + 真值见证。
    if _witness(f_lo, f_hi, b_lo, b_hi, d_lo, d_hi) is not None:
        fc, bc = f_cl, b_cl
        rects.append({
            "fc": [fc, fc], "bc": [bc, bc], "k": [bc - fc, bc - fc],
            "f_full": False, "b_full": False,
            "f_range": [f_lo, f_hi], "b_range": [b_lo, b_hi],
        })
    return rects


def _cell_value_ranges(region, fc, bc):
    """码格 (fc, bc) 在该区域内对应的前/后压原值闭区间。"""
    if region["f_full"]:
        f_lo, f_hi = fc * BLOCK, fc * BLOCK + BLOCK - 1
    else:
        f_lo, f_hi = region["f_range"]
    if region["b_full"]:
        b_lo, b_hi = bc * BLOCK, bc * BLOCK + BLOCK - 1
    else:
        b_lo, b_hi = region["b_range"]
    return f_lo, f_hi, b_lo, b_hi


def _witness_at(region, temperature, fc=None, bc=None):
    """在区域内选一个满足 k=bc-fc 约束的码格，闭式构造合规真值 (T, F, B)。

    fc/bc 未指定时，在区域 k 区间内取一个可达码格（取 k 区间下界并闭式选 fc）；
    指定时必须落在该区域的码矩形与 k 区间内，否则返回 None。
    """
    fc_lo, fc_hi = region["fc"]
    bc_lo, bc_hi = region["bc"]
    k_lo, k_hi = region["k"]
    if fc is None or bc is None:
        k0 = k_lo
        fc0 = max(fc_lo, bc_lo - k0)
        bc0 = fc0 + k0
        if not (fc0 <= fc_hi and bc_lo <= bc0 <= bc_hi and k_lo <= k0 <= k_hi):
            return None
    else:
        fc0, bc0 = fc, bc
        if not (fc_lo <= fc0 <= fc_hi and bc_lo <= bc0 <= bc_hi):
            return None
        if not (k_lo <= bc0 - fc0 <= k_hi):
            return None

    f_lo, f_hi, b_lo, b_hi = _cell_value_ranges(region, fc0, bc0)
    found = _witness(f_lo, f_hi, b_lo, b_hi, region["d_lo"], region["d_hi"])
    if found is None:
        return None
    f, b = found
    if not (region["d_lo"] <= b - f <= region["d_hi"]):
        return None
    return {"temperature": temperature, "front": f, "back": b}


# ---------------- 生效分流的叶子盒子 ----------------

def _clipped_boxes(tree, domain):
    out = []
    for box, category in leaf_boxes(tree):
        if _intersects(box, domain):
            out.append((_intersect(box, domain), category))
    return out


def _emit_regions(box, category, tree_used, d_lo, d_hi, sink):
    """把一个带类别的读数盒子展开为精确的规则形码区（压差可行才保留）。"""
    t_lo, t_hi = box["temperature"]
    for f_seg in _pressure_segments(*box["front"]):
        for b_seg in _pressure_segments(*box["back"]):
            for rect in _feasible_code_rectangles(f_seg, b_seg, d_lo, d_hi):
                region = {
                    "category": category,
                    "tree_used": tree_used,
                    "temperature": [t_lo, t_hi],
                    "d_lo": d_lo,
                    "d_hi": d_hi,
                    **rect,
                }
                # 矩形由充要条件保证可行，这里同步留一条真值见证。
                witness = _witness_at(region, t_lo)
                region["witness"] = witness
                sink.append(region)


# ---------------- 冲突检测 ----------------

def _shared_cells(ra, rb):
    """两个码区在温度/前压码/后压码区间上的公共码格（仍为区间形式，未逐格枚举）。

    返回 (t_lo,t_hi, fc_lo,fc_hi, bc_lo,bc_hi, k_lo,k_hi) 或 None；
    k=bc-fc 的公共范围由两个区域的 k 区间与码矩形可行范围闭式求交得到。
    """
    t_lo = max(ra["temperature"][0], rb["temperature"][0])
    t_hi = min(ra["temperature"][1], rb["temperature"][1])
    if t_lo > t_hi:
        return None
    fc_lo = max(ra["fc"][0], rb["fc"][0])
    fc_hi = min(ra["fc"][1], rb["fc"][1])
    if fc_lo > fc_hi:
        return None
    bc_lo = max(ra["bc"][0], rb["bc"][0])
    bc_hi = min(ra["bc"][1], rb["bc"][1])
    if bc_lo > bc_hi:
        return None
    k_possible_lo = bc_lo - fc_hi
    k_possible_hi = bc_hi - fc_lo
    k_lo = max(ra["k"][0], rb["k"][0], k_possible_lo)
    k_hi = min(ra["k"][1], rb["k"][1], k_possible_hi)
    if k_lo > k_hi:
        return None
    return t_lo, t_hi, fc_lo, fc_hi, bc_lo, bc_hi, k_lo, k_hi


def _find_conflicts(regions):
    """按码格 (T,fc,bc) 聚合所有码区，检测同一码格上出现不同生效类别的冲突。

    做法（仍为区间运算，不逐格枚举）：
      1. 对每对不同类别的码区，用区间求交得到公共码格范围与公共 k 范围；
      2. 取一个代表码格 (k0, fc0, bc0=fc0+k0)，在该格上验证所有覆盖区域的
         合规真值见证，按 (所用树, 类别) 去重；存在 ≥2 个不同类别即为冲突；
      3. 明细只保留代表性冲突（前 MAX_CONFLICTS_LISTED 个）；
      4. 冲突总数由所有冲突对的公共三维码格盒子求并集后按区间体积精确计算
         （合并重叠/相邻盒子，不逐格枚举），避免不同冲突对共用代表格而漏计。
    原树 A 与候选树 A 类别相同，不构成冲突，但作为两条真值都会保留。
    返回 (冲突明细列表, 冲突码格总数)。
    """
    detail = {}
    conflict_boxes = []
    n = len(regions)
    for i in range(n):
        for j in range(i + 1, n):
            ra, rb = regions[i], regions[j]
            if ra["category"] == rb["category"]:
                continue
            shared = _shared_cells(ra, rb)
            if shared is None:
                continue
            t_lo, t_hi, fc_lo, fc_hi, bc_lo, bc_hi, k_lo, k_hi = shared
            k0 = k_lo
            fc0 = max(fc_lo, bc_lo - k0)
            bc0 = fc0 + k0
            if not (fc0 <= fc_hi and bc_lo <= bc0 <= bc_hi):
                continue

            # 该冲突对覆盖的全部 (T, fc, bc) 码格盒子（k 已在交集内可行）。
            conflict_boxes.append({
                "temperature": [t_lo, t_hi],
                "fc": [fc_lo, fc_hi],
                "bc": [bc_lo, bc_hi],
            })

            # 在代表码格上验证所有覆盖区域，聚合冲突明细。
            groups = {}
            for region in regions:
                if not (region["temperature"][0] <= t_lo <= region["temperature"][1]
                        and region["fc"][0] <= fc0 <= region["fc"][1]
                        and region["bc"][0] <= bc0 <= region["bc"][1]
                        and region["k"][0] <= k0 <= region["k"][1]):
                    continue
                truth = _witness_at(region, t_lo, fc0, bc0)
                if truth is None:
                    continue
                gkey = (region["tree_used"], region["category"])
                groups.setdefault(gkey, {
                    "category": region["category"],
                    "tree_used": region["tree_used"],
                    "truth": truth,
                })
            if len({g["category"] for g in groups.values()}) < 2:
                continue
            key = (t_lo, fc0, bc0)
            bucket = detail.setdefault(key, {
                "temperature": t_lo,
                "front_code": fc0,
                "back_code": bc0,
                "code_diff": k0,
                "groups": [],
            })
            present = {(g["tree_used"], g["category"]) for g in bucket["groups"]}
            for gkey, group in groups.items():
                if gkey not in present:
                    bucket["groups"].append(group)
                    present.add(gkey)

    conflicts = [detail[key] for key in sorted(detail)]
    total = _union_cell_count(conflict_boxes)
    return conflicts, total


def _union_cell_count(boxes):
    """三维整数码格盒子并集的码格总数（区间合并，不逐格枚举）。

    先按 (前压码, 后压码) 二维分组，组内对温度区间求并集再计长度，
    相同 (fc,bc) 的盒子只算一次温度并集，自然消除盒子间重叠。
    """
    if not boxes:
        return 0
    groups = defaultdict(list)
    for box in boxes:
        for fc in range(box["fc"][0], box["fc"][1] + 1):
            for bc in range(box["bc"][0], box["bc"][1] + 1):
                groups[(fc, bc)].append(box["temperature"])
    total = 0
    for t_intervals in groups.values():
        for lo, hi in _merge_intervals(t_intervals):
            total += hi - lo + 1
    return total


# ---------------- 规则合并 ----------------

def _merge_intervals(intervals):
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for lo, hi in ordered[1:]:
        if lo <= merged[-1][1] + 1:  # 整数轴相邻即连续，合并不引入未覆盖整数点
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def _merge_regions_to_rules(regions):
    """同类码区保守合并为规则：只合并其余三维完全相同、仅一轴相邻/重叠的区间。

    合并不可能纳入无合规真值的码格（相邻整数区间之间没有空缺整数点），
    也不会混入其他类别（冲突已在前置步骤排除）。
    """
    current = list(regions)

    # 第一轮：(fc, bc, k, 类别) 相同，合并温度轴。
    groups = defaultdict(list)
    for r in current:
        groups[(tuple(r["fc"]), tuple(r["bc"]), tuple(r["k"]), r["category"])].append(
            r["temperature"]
        )
    current = []
    for (fc, bc, k, category), t_intervals in groups.items():
        for t in _merge_intervals(t_intervals):
            current.append({
                "category": category,
                "temperature": t, "fc": list(fc), "bc": list(bc), "k": list(k),
            })

    # 第二轮：(t, fc, bc, 类别) 相同，合并 k 轴。
    groups = defaultdict(list)
    for r in current:
        groups[(tuple(r["temperature"]), tuple(r["fc"]), tuple(r["bc"]), r["category"])].append(
            r["k"]
        )
    rules = []
    for (t, fc, bc, category), k_intervals in groups.items():
        for k in _merge_intervals(k_intervals):
            rules.append({
                "category": category,
                "temperature": list(t),
                "front_code": list(fc),
                "back_code": list(bc),
                "code_diff": list(k),
            })

    rules.sort(key=lambda r: (
        r["temperature"][0], r["front_code"][0], r["back_code"][0],
        r["code_diff"][0], r["category"],
    ))
    return rules


# ---------------- 主入口 ----------------

def convert_rules(original_tree, confirmation, draft):
    """把一份工况（四个整数闭区间）转换为备用控制器 JSON 规则。

    参数：
      original_tree: 转换时刻生效的原树（必填）；
      confirmation: None，或已确认方案 {"zone","draft","candidate_tree"}；
      draft: 工艺员输入的四个闭区间。

    返回 dict：
      status: "solved"（可下载） | "conflict"（冲突，禁用下载） | "no_solution"
      download_allowed: bool
      reason: 中文说明
      rules / conflicts / 统计信息等。
    """
    t_lo, t_hi = draft["temperature"]
    f_lo, f_hi = draft["front"]
    b_lo, b_hi = draft["back"]
    d_lo, d_hi = draft["diff"]

    condition = {
        "temperature": [t_lo, t_hi],
        "front": [f_lo, f_hi],
        "back": [b_lo, b_hi],
        "diff": [d_lo, d_hi],
    }
    result = {
        "status": "error",
        "download_allowed": False,
        "reason": "",
        "routing_basis": "original",
        "condition": condition,
        "rules": [],
        "rule_count": 0,
        "conflicts": [],
        "conflict_count": 0,
        "stats": {},
    }

    # 第一步：工况整体是否存在合规压力组合（闭式判据，不枚举）。
    if not pressure_diff_feasible(f_lo, f_hi, b_lo, b_hi, d_lo, d_hi):
        result["status"] = "no_solution"
        result["reason"] = (
            "工况无解：在给定前压、后压闭区间内，后压-前压无法落入"
            f"[{d_lo}, {d_hi}]，不存在任何合规读数，无法生成规则"
        )
        return result

    domain = {
        "temperature": [t_lo, t_hi],
        "front": [f_lo, f_hi],
        "back": [b_lo, b_hi],
    }
    regions = []

    if confirmation is None:
        # 无确认方案：所有合规真值按原树分流。注意：决策树叶子的互斥是在
        # **原始读数层**，压缩到压力码后，码块内部阈值两侧的叶子会塌缩进
        # 同一码格，因此即使只有一棵树，也仍需在后面统一做冲突检测。
        for box, category in _clipped_boxes(original_tree, domain):
            _emit_regions(box, category, "original", d_lo, d_hi, regions)
        result["routing_basis"] = "original"
    else:
        result["routing_basis"] = "candidate_confirmation"
        c_zone = confirmation["zone"]
        c_draft = confirmation["draft"]
        eligible_box = {
            "temperature": list(c_zone),
            "front": list(c_draft["front"]),
            "back": list(c_draft["back"]),
        }
        cd_lo, cd_hi = c_draft["diff"]

        # 原树部分：获准盒子之外的合规真值仍走原树；
        # 获准盒子之内、不满足确认压差的真值也仍走原树（两个压差残片）。
        for box, category in _clipped_boxes(original_tree, domain):
            for outside in _subtract_box(box, eligible_box):
                _emit_regions(outside, category, "original", d_lo, d_hi, regions)
            inside = _intersect(box, eligible_box)
            if not _empty(inside):
                if d_lo <= cd_lo - 1:
                    _emit_regions(
                        inside, category, "original",
                        d_lo, min(d_hi, cd_lo - 1), regions,
                    )
                if cd_hi + 1 <= d_hi:
                    _emit_regions(
                        inside, category, "original",
                        max(d_lo, cd_hi + 1), d_hi, regions,
                    )

        # 候选树部分：仅获准盒子内、同时满足转换压差与确认压差的真值。
        combined_d_lo = max(d_lo, cd_lo)
        combined_d_hi = min(d_hi, cd_hi)
        if combined_d_lo <= combined_d_hi:
            for box, category in _clipped_boxes(confirmation["candidate_tree"], domain):
                eligible = _intersect(box, eligible_box)
                if not _empty(eligible):
                    _emit_regions(
                        eligible, category, "candidate",
                        combined_d_lo, combined_d_hi, regions,
                    )

    result["stats"]["region_count"] = len(regions)

    # 第二步：工况可行但没有任何合规读数落到有效盒子（防御性分支）。
    if not regions:
        result["status"] = "no_solution"
        result["reason"] = "工况无解：给定区间内不存在任何合规读数，无法生成规则"
        return result

    # 第三步：同码格类别冲突检测。
    # 只要同一 (T,fc,bc) 码格上出现两个不同生效类别（无论来自同一棵树的
    # 码块内塌缩，还是原树/候选两套分流），备用控制器都无法区分，必须报错。
    conflicts, total = _find_conflicts(regions)
    if conflicts:
        result["status"] = "conflict"
        result["conflict_count"] = total
        result["conflicts"] = conflicts[:MAX_CONFLICTS_LISTED]
        result["reason"] = (
            f"发现 {total} 组同温度、同前后压力码但分拣类别不同的合规真值，"
            "备用控制器无法区分这些读数，规则转换存在冲突，已禁用下载"
        )
        return result

    # 第四步：无冲突，合并为精确规则。
    rules = _merge_regions_to_rules(regions)
    result["status"] = "solved"
    result["download_allowed"] = True
    result["rules"] = rules
    result["rule_count"] = len(rules)
    categories = sorted({r["category"] for r in rules})
    result["stats"]["categories"] = categories
    result["reason"] = (
        f"转换成功：共 {len(rules)} 条规则，覆盖 {len(regions)} 个精确码区，"
        f"涉及类别 {('、'.join(categories)) or '无'}；每条规则仅覆盖存在合规真值的码格，"
        "且与全部对应真值的分拣类别一致"
    )
    return result


def _empty(box):
    return any(lo > hi for lo, hi in box.values())
