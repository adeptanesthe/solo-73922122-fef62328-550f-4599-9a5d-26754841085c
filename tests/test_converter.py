"""规则转换正确性测试：小量程随机小树 + 全量枚举暴力对照。

生产转换器 app/converter.py 全程区间运算、不枚举真值或压力码组合；
本测试用全量枚举（遍历域内每个整数真值）构造“神谕”，逐条核对：
  1. 无冲突时，每条规则覆盖的码格都存在合规真值，且类别与所有真值一致；
  2. 无合规真值的码格不被任何规则命中（规则码格并集 == 合规真值码格集合）；
  3. 冲突判定与暴力法一致（同码格是否出现不同类别、冲突组数、真值见证）。
暴力枚举仅出现在本测试文件中，且只在小量程（约 0..900）上运行。
"""

import random
import time
import unittest

from app.converter import convert_rules
from app.tree import classify, validate_tree_full

CATS = ("PASS", "REWORK", "REJECT")
FEATS = ("temperature", "front", "back")


def random_tree(rng, depth=0, max_depth=3):
    if depth >= max_depth or rng.random() < 0.3:
        return {"leaf": rng.choice(CATS)}
    # 小量程暴力对照：阈值 0..12，落在单个 256 码块内部，集中考察区间逻辑。
    threshold = rng.randint(0, 12)
    return {
        "feature": rng.choice(FEATS),
        "threshold": threshold,
        "left": random_tree(rng, depth + 1, max_depth),
        "right": random_tree(rng, depth + 1, max_depth),
    }


def random_boundary_tree(rng, depth=0, max_depth=2):
    """阈值刻意落在 256 码块边界/内部的小树，用于考察整码段与边界单码。"""
    if depth >= max_depth or rng.random() < 0.3:
        return {"leaf": rng.choice(CATS)}
    threshold = rng.choice([255, 256, 511, 512, 600, 767, 768])
    return {
        "feature": rng.choice(FEATS),
        "threshold": threshold,
        "left": random_boundary_tree(rng, depth + 1, max_depth),
        "right": random_boundary_tree(rng, depth + 1, max_depth),
    }


def effective_category(original_tree, confirmation, reading):
    """复刻 web 分流规则，返回 (tree_used, category)。"""
    if confirmation is None:
        return "original", classify(original_tree, reading)
    zone = confirmation["zone"]
    draft = confirmation["draft"]
    t, f, b = reading["temperature"], reading["front"], reading["back"]
    eligible = (
        zone[0] <= t <= zone[1]
        and draft["front"][0] <= f <= draft["front"][1]
        and draft["back"][0] <= b <= draft["back"][1]
        and draft["diff"][0] <= b - f <= draft["diff"][1]
    )
    if eligible:
        return "candidate", classify(confirmation["candidate_tree"], reading)
    return "original", classify(original_tree, reading)


def brute_oracle(original_tree, confirmation, condition):
    """全量枚举域内真值，返回 cell_map: (T,fc,bc) -> {categories, samples}。"""
    t_lo, t_hi = condition["temperature"]
    f_lo, f_hi = condition["front"]
    b_lo, b_hi = condition["back"]
    d_lo, d_hi = condition["diff"]
    cell_map = {}
    for t in range(t_lo, t_hi + 1):
        for f in range(f_lo, f_hi + 1):
            for b in range(b_lo, b_hi + 1):
                if not (d_lo <= b - f <= d_hi):
                    continue
                reading = {"temperature": t, "front": f, "back": b}
                tree_used, category = effective_category(
                    original_tree, confirmation, reading
                )
                key = (t, f >> 8, b >> 8)
                bucket = cell_map.setdefault(
                    key, {"categories": set(), "samples": {}}
                )
                bucket["categories"].add(category)
                bucket["samples"].setdefault(
                    category, {"tree_used": tree_used, "reading": dict(reading)}
                )
    return cell_map


def rule_cells(rules):
    cells = set()
    for rule in rules:
        for t in range(rule["temperature"][0], rule["temperature"][1] + 1):
            for fc in range(rule["front_code"][0], rule["front_code"][1] + 1):
                for bc in range(rule["back_code"][0], rule["back_code"][1] + 1):
                    if rule["code_diff"][0] <= bc - fc <= rule["code_diff"][1]:
                        cells.add((t, fc, bc))
    return cells


class ConverterBruteForceTests(unittest.TestCase):
    def assert_rules_match_oracle(self, result, cell_map):
        rules = result["rules"]
        cells = rule_cells(rules)
        # 无合规真值的码格不被命中；所有合规真值码格都被覆盖（无遗漏）。
        self.assertEqual(cells, set(cell_map.keys()))
        # solved 前提下每个码格类别唯一；核对每条规则类别与该格真值类别一致。
        for rule in rules:
            for key in cells:
                t, fc, bc = key
                if (rule["temperature"][0] <= t <= rule["temperature"][1]
                        and rule["front_code"][0] <= fc <= rule["front_code"][1]
                        and rule["back_code"][0] <= bc <= rule["back_code"][1]
                        and rule["code_diff"][0] <= bc - fc <= rule["code_diff"][1]):
                    self.assertEqual(cell_map[key]["categories"],
                                     {rule["category"]})

    def test_random_without_confirmation(self):
        rng = random.Random(20261123)
        solved = conflicted = 0
        for _ in range(200):
            original = random_tree(rng)
            validate_tree_full(original)
            # 小量程：单边至多 13 个整数，单例枚举约 13^3 次，暴力法很快。
            condition = {
                "temperature": [rng.randint(0, 4), rng.randint(6, 12)],
                "front": [rng.randint(0, 4), rng.randint(6, 12)],
                "back": [rng.randint(0, 4), rng.randint(6, 12)],
                "diff": [rng.randint(-12, 2), rng.randint(4, 12)],
            }
            cell_map = brute_oracle(original, None, condition)
            result = convert_rules(original, None, condition)
            if not cell_map:
                self.assertEqual(result["status"], "no_solution")
                self.assertFalse(result["download_allowed"])
                continue

            oracle_conflicts = {
                key: bucket for key, bucket in cell_map.items()
                if len(bucket["categories"]) >= 2
            }
            if oracle_conflicts:
                # 纯原树分流也会冲突：码块内部阈值在压缩后塌缩进同一码格。
                conflicted += 1
                self.assertEqual(result["status"], "conflict")
                self.assertFalse(result["download_allowed"])
                self.assertEqual(result["conflict_count"], len(oracle_conflicts))
                for c in result["conflicts"]:
                    key = (c["temperature"], c["front_code"], c["back_code"])
                    self.assertIn(key, oracle_conflicts)
                    self.assertGreaterEqual(
                        len({g["category"] for g in c["groups"]}), 2
                    )
            else:
                solved += 1
                self.assertEqual(result["status"], "solved", msg=result["reason"])
                self.assertTrue(result["download_allowed"])
                self.assert_rules_match_oracle(result, cell_map)
        self.assertGreater(solved, 20)
        self.assertGreater(conflicted, 3)

    def test_random_with_confirmation(self):
        rng = random.Random(778899)
        solved = conflicted = no_solution = 0
        for _ in range(200):
            original = random_tree(rng)
            candidate = random_tree(rng)
            validate_tree_full(original)
            validate_tree_full(candidate)
            zone = [rng.randint(0, 4), rng.randint(6, 12)]
            confirmation = {
                "zone": zone,
                "draft": {
                    "temperature": list(zone),
                    "front": [rng.randint(0, 4), rng.randint(6, 12)],
                    "back": [rng.randint(0, 4), rng.randint(6, 12)],
                    "diff": [rng.randint(-8, 2), rng.randint(4, 12)],
                },
                "candidate_tree": candidate,
            }
            condition = {
                "temperature": [rng.randint(0, 4), rng.randint(6, 12)],
                "front": [rng.randint(0, 4), rng.randint(6, 12)],
                "back": [rng.randint(0, 4), rng.randint(6, 12)],
                "diff": [rng.randint(-12, 2), rng.randint(4, 12)],
            }
            cell_map = brute_oracle(original, confirmation, condition)
            result = convert_rules(original, confirmation, condition)
            if not cell_map:
                self.assertEqual(result["status"], "no_solution")
                no_solution += 1
                continue

            oracle_conflicts = {
                key: bucket for key, bucket in cell_map.items()
                if len(bucket["categories"]) >= 2
            }
            if oracle_conflicts:
                conflicted += 1
                self.assertEqual(result["status"], "conflict")
                self.assertFalse(result["download_allowed"])
                self.assertEqual(result["conflict_count"], len(oracle_conflicts))
                reported = {(c["temperature"], c["front_code"], c["back_code"])
                            for c in result["conflicts"]}
                # 报告的每个冲突码格都真实存在（截断时为子集）。
                self.assertTrue(reported.issubset(set(oracle_conflicts.keys())))
                # 列出的真值必须真实合规且压缩码与码格一致、类别≥2。
                for c in result["conflicts"]:
                    cats = set()
                    for g in c["groups"]:
                        truth = g["truth"]
                        self.assertEqual(truth["temperature"], c["temperature"])
                        self.assertEqual(truth["front"] >> 8, c["front_code"])
                        self.assertEqual(truth["back"] >> 8, c["back_code"])
                        self.assertIn(
                            g["category"],
                            cell_map[(c["temperature"], c["front_code"],
                                      c["back_code"])]["categories"],
                        )
                        cats.add(g["category"])
                    self.assertGreaterEqual(len(cats), 2)
            else:
                solved += 1
                self.assertEqual(result["status"], "solved", msg=result["reason"])
                self.assertTrue(result["download_allowed"])
                self.assert_rules_match_oracle(result, cell_map)
        self.assertGreater(solved, 20)
        self.assertGreater(conflicted, 3)

    def test_boundary_codes_against_brute_force(self):
        # 阈值与工况边界跨 256 码块：枚举仍可控（单边约 1400 以内的窄域），
        # 专门验证整码段/边界单码的闭式判据与暴力法一致。
        rng = random.Random(20261225)
        for _ in range(40):
            original = random_boundary_tree(rng)
            condition = {
                "temperature": [100, 105],
                "front": [rng.choice([250, 500, 512]), rng.choice([260, 600, 767])],
                "back": [rng.choice([250, 500, 512]), rng.choice([260, 600, 767])],
                "diff": [rng.randint(-500, 50), rng.randint(100, 500)],
            }
            if condition["front"][0] > condition["front"][1]:
                condition["front"] = [512, 600]
            if condition["back"][0] > condition["back"][1]:
                condition["back"] = [512, 600]
            cell_map = brute_oracle(original, None, condition)
            result = convert_rules(original, None, condition)
            if not cell_map:
                self.assertEqual(result["status"], "no_solution")
            elif any(len(b["categories"]) >= 2 for b in cell_map.values()):
                self.assertEqual(result["status"], "conflict")
                self.assertFalse(result["download_allowed"])
            else:
                self.assertEqual(result["status"], "solved")
                self.assert_rules_match_oracle(result, cell_map)

    def test_full_scale_performance_and_exactness(self):
        # 满量程、128 叶满树：毫秒级返回，规则并集精确覆盖全部码格（区间计数核对）。
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
        condition = {
            "temperature": [0, 65535],
            "front": [0, 65535],
            "back": [0, 65535],
            "diff": [-65535, 65535],
        }
        start = time.perf_counter()
        result = convert_rules(tree, None, condition)
        elapsed = time.perf_counter() - start
        self.assertEqual(result["status"], "solved")
        self.assertLess(elapsed, 2.0)
        # 满量程宽压差下每个码格都有合规真值，规则并集须恰好覆盖
        # 65536 温度 × 256 前压码 × 256 后压码（用区间体积求和，不逐格枚举）。
        total = 0
        for rule in result["rules"]:
            nt = rule["temperature"][1] - rule["temperature"][0] + 1
            nfc = rule["front_code"][1] - rule["front_code"][0] + 1
            nbc = rule["back_code"][1] - rule["back_code"][0] + 1
            total += nt * nfc * nbc
        self.assertEqual(total, 65536 * 256 * 256)


class ConverterFixedTests(unittest.TestCase):
    def test_builtin_examples(self):
        from app.examples import (
            CONV_ORIG_1, CONV_CONDITION_1,
            CONV_ORIG_2, CONV_CONDITION_2,
            CONV_ORIG_3, CONV_CAND_3, CONV_DRAFT_3, CONV_CONDITION_3,
        )
        r1 = convert_rules(CONV_ORIG_1, None, CONV_CONDITION_1)
        self.assertEqual(r1["status"], "solved")
        self.assertTrue(r1["download_allowed"])
        self.assertEqual(r1["rule_count"], 3)

        r2 = convert_rules(CONV_ORIG_2, None, CONV_CONDITION_2)
        self.assertEqual(r2["status"], "no_solution")
        self.assertFalse(r2["download_allowed"])
        self.assertIn("无解", r2["reason"])

        confirmation = {
            "zone": list(CONV_DRAFT_3["temperature"]),
            "draft": CONV_DRAFT_3,
            "candidate_tree": CONV_CAND_3,
        }
        r3 = convert_rules(CONV_ORIG_3, confirmation, CONV_CONDITION_3)
        self.assertEqual(r3["status"], "conflict")
        self.assertFalse(r3["download_allowed"])
        self.assertGreaterEqual(r3["conflict_count"], 1)
        c0 = r3["conflicts"][0]
        self.assertEqual(c0["front_code"], 2)
        cats = {g["category"] for g in c0["groups"]}
        self.assertIn("A", cats)
        self.assertIn("B", cats)

    def test_rules_only_hit_compliant_inputs_boundary_codes(self):
        # 边界单码场景：规则码格并集必须恰好等于合规真值码格集合。
        tree = {"feature": "front", "threshold": 600,
                "left": {"leaf": "A"}, "right": {"leaf": "B"}}
        condition = {
            "temperature": [100, 100],
            "front": [512, 600],
            "back": [512, 767],
            "diff": [0, 255],
        }
        cell_map = brute_oracle(tree, None, condition)
        result = convert_rules(tree, None, condition)
        self.assertEqual(result["status"], "solved")
        self.assertEqual(rule_cells(result["rules"]), set(cell_map.keys()))


if __name__ == "__main__":
    unittest.main()
