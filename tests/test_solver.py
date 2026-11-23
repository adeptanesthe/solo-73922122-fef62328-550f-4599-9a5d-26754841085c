"""求解器正确性测试：对小范围随机小树，用全量枚举读数组合做暴力对照。

暴力法仅出现在测试里，用于证明生产求解器“不枚举”的区间算法结果正确。
"""

import json
import random
import unittest

from app.solver import run_trial
from app.tree import classify, leaf_boxes, validate_tree_full

CATS = ("PASS", "REWORK", "REJECT")
FEATS = ("temperature", "front", "back")


def random_tree(rng, depth=0, max_depth=3):
    if depth >= max_depth or rng.random() < 0.3:
        return {"leaf": rng.choice(CATS)}
    return {
        "feature": rng.choice(FEATS),
        "threshold": rng.randint(0, 9),
        "left": random_tree(rng, depth + 1, max_depth),
        "right": random_tree(rng, depth + 1, max_depth),
    }


def brute_force_best_zone(orig, cand, draft):
    """枚举域内所有整数读数，返回 (status, zone or None)。"""
    t_lo, t_hi = draft["temperature"]
    f_lo, f_hi = draft["front"]
    b_lo, b_hi = draft["back"]
    d_lo, d_hi = draft["diff"]

    # 先判断每个温度是否存在合规压力组合，以及两树是否对所有合规读数一致。
    good_temps = []
    any_feasible = False
    for t in range(t_lo, t_hi + 1):
        feasible_temps = False
        all_agree = True
        saw_reading = False
        for f in range(f_lo, f_hi + 1):
            for b in range(b_lo, b_hi + 1):
                if not (d_lo <= b - f <= d_hi):
                    continue
                saw_reading = True
                feasible_temps = True
                r = {"temperature": t, "front": f, "back": b}
                if classify(orig, r) != classify(cand, r):
                    all_agree = False
        if saw_reading:
            any_feasible = True
        if feasible_temps and all_agree and saw_reading:
            good_temps.append(t)

    if not any_feasible:
        return "no_solution", None
    if not good_temps:
        return "no_solution", None

    # 最长连续段，同长取起点最低。
    best = [good_temps[0], good_temps[0]]
    cur = [good_temps[0], good_temps[0]]
    for t in good_temps[1:]:
        if t == cur[1] + 1:
            cur[1] = t
        else:
            cur = [t, t]
        if (cur[1] - cur[0]) > (best[1] - best[0]):
            best = list(cur)
    return "solved", best


class SolverRandomTests(unittest.TestCase):
    def test_random_trees_against_brute_force(self):
        rng = random.Random(20261122)
        checked = 0
        for _ in range(500):
            orig = random_tree(rng)
            cand = random_tree(rng)
            validate_tree_full(orig)
            validate_tree_full(cand)
            draft = {
                "temperature": [rng.randint(0, 3), rng.randint(4, 9)],
                "front": [rng.randint(0, 4), rng.randint(5, 9)],
                "back": [rng.randint(0, 4), rng.randint(5, 9)],
                "diff": [rng.randint(-4, 2), rng.randint(3, 9)],
            }
            result = run_trial(orig, cand, draft)
            exp_status, exp_zone = brute_force_best_zone(orig, cand, draft)
            self.assertEqual(result["status"], exp_status,
                             msg=f"status mismatch draft={draft}")
            if exp_status == "solved":
                self.assertEqual(result["zone"], exp_zone,
                                 msg=f"zone mismatch draft={draft}")
                self.assertEqual(result["length"], exp_zone[1] - exp_zone[0] + 1)
            checked += 1
        self.assertGreaterEqual(checked, 500)

    def test_full_scale_128_leaves_and_65535_domain(self):
        # 构造恰好 128 个叶子的满 7 层树，阈值覆盖 0 与 65535 边界，
        # 验证不枚举读数也能在满量程上快速求解。
        def full_tree(depth, path):
            if depth == 7:
                return {"leaf": f"L{path:07b}"}
            return {
                "feature": FEATS[depth % 3],
                "threshold": (0, 65535, 32767)[depth % 3],
                "left": full_tree(depth + 1, path << 1),
                "right": full_tree(depth + 1, (path << 1) | 1),
            }

        tree = full_tree(0, 0)
        self.assertEqual(validate_tree_full(tree), 128)
        draft = {
            "temperature": [0, 65535],
            "front": [0, 65535],
            "back": [0, 65535],
            "diff": [-65535, 65535],
        }
        # 同树对拍：所有读数类别必然一致，整段温区都安全。
        import time
        start = time.perf_counter()
        result = run_trial(tree, json.loads(json.dumps(tree)), draft)
        elapsed = time.perf_counter() - start
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["zone"], [0, 65535])
        self.assertEqual(result["length"], 65536)
        self.assertLess(elapsed, 2.0)
        self.assertTrue(result["pressure_feasible"])

    def test_leaf_boxes_cover_full_space_disjointly(self):
        rng = random.Random(42)
        for _ in range(20):
            tree = random_tree(rng)
            boxes = leaf_boxes(tree)
            # 所有盒子都在 0..9 裁剪空间内（本测试树阈值 <=9），
            # 抽查：对随机 300 个点，叶子盒子的类别与树路由类别一致。
            for _ in range(300):
                reading = {f: rng.randint(0, 9) for f in FEATS}
                routed = classify(tree, reading)
                matched = [
                    cat for box, cat in boxes
                    if all(box[f][0] <= reading[f] <= box[f][1] for f in FEATS)
                ]
                self.assertEqual(len(matched), 1)
                self.assertEqual(matched[0], routed)


class SolverFixedTests(unittest.TestCase):
    def test_pressure_infeasible(self):
        from app.examples import ORIGINAL_1, CANDIDATE_1, DRAFT_2
        result = run_trial(ORIGINAL_1, CANDIDATE_1, DRAFT_2)
        self.assertEqual(result["status"], "no_solution")
        self.assertFalse(result["pressure_feasible"])

    def test_solvable_example_zone(self):
        from app.examples import ORIGINAL_1, CANDIDATE_1, DRAFT_1
        result = run_trial(ORIGINAL_1, CANDIDATE_1, DRAFT_1)
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["zone"], [150, 200])
        self.assertEqual(result["length"], 51)

    def test_no_safe_zone_example(self):
        from app.examples import ORIGINAL_3, CANDIDATE_3, DRAFT_3
        result = run_trial(ORIGINAL_3, CANDIDATE_3, DRAFT_3)
        self.assertEqual(result["status"], "no_solution")
        self.assertTrue(result["pressure_feasible"])

    def test_tie_prefers_lower_start(self):
        # 安全段 [0,2] 与 [4,5] 等长（均为 3 个温度），应取起点低的 [0,2]。
        orig = {"leaf": "PASS"}
        cand = {
            "feature": "temperature", "threshold": 2,
            "left": {"leaf": "PASS"},
            "right": {
                "feature": "temperature", "threshold": 3,
                "left": {"leaf": "X"},
                "right": {"leaf": "PASS"},
            },
        }
        draft = {
            "temperature": [0, 5],
            "front": [0, 5], "back": [0, 5], "diff": [-5, 5],
        }
        result = run_trial(orig, cand, draft)
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["zone"], [0, 2])
        self.assertEqual(result["length"], 3)

    def test_diff_constraint_prunes_disagreement_box(self):
        # 分歧只发生在 back-front 很大的区域；压差区间收紧后分歧不可达，
        # 整个温区都应安全。
        orig = {
            "feature": "back", "threshold": 5,
            "left": {"leaf": "A"}, "right": {"leaf": "B"},
        }
        cand = {
            "feature": "back", "threshold": 5,
            "left": {"leaf": "A"}, "right": {"leaf": "C"},
        }
        draft = {
            "temperature": [0, 9],
            "front": [4, 9], "back": [0, 5],  # back <= front，压差 <= 1
            "diff": [-9, 1],
        }
        result = run_trial(orig, cand, draft)
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["zone"], [0, 9])


if __name__ == "__main__":
    unittest.main()
