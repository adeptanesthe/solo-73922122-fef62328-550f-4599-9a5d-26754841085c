"""规则转换 Web 端到端自测：接口、确认分流、冲突禁用下载、校验、持久化。"""

import os
import tempfile
import time
import unittest

from app.web import create_app


class RuleConversionWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "state.json")
        app = create_app(data_file=self.db)
        app.testing = True
        self.client = app.test_client()
        data = self.client.get("/api/examples").get_json()
        self.conv_examples = {e["key"]: e for e in data["conversion_examples"]}

    def tearDown(self):
        self.tmp.cleanup()

    def _save_original(self, tree):
        r = self.client.post("/api/original-tree", json={"tree": tree})
        self.assertEqual(r.status_code, 200)

    def _convert(self, condition):
        return self.client.post("/api/rule-conversions", json=condition)

    # ---------- 无确认：可下载 ----------
    def test_solvable_conversion_allows_download(self):
        ex = self.conv_examples["conv_solvable"]
        self._save_original(ex["original_tree"])
        r = self._convert(ex["condition"])
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "solved")
        self.assertTrue(data["download_allowed"])
        self.assertGreater(data["rule_count"], 0)
        # 每条规则含类别与四个闭区间。
        for rule in data["rules"]:
            self.assertIn("category", rule)
            for key in ("temperature", "front_code", "back_code", "code_diff"):
                self.assertEqual(len(rule[key]), 2)
                self.assertLessEqual(rule[key][0], rule[key][1])
            self.assertTrue(0 <= rule["front_code"][0] <= 255)
            self.assertTrue(0 <= rule["back_code"][1] <= 255)
        # 分流依据为原树。
        self.assertEqual(data["routing_basis"], "original")

    # ---------- 无解 ----------
    def test_infeasible_condition_no_download_with_reason(self):
        ex = self.conv_examples["conv_infeasible"]
        self._save_original(ex["original_tree"])
        r = self._convert(ex["condition"])
        data = r.get_json()
        self.assertEqual(data["status"], "no_solution")
        self.assertFalse(data["download_allowed"])
        self.assertEqual(data["rules"], [])
        self.assertIn("无解", data["reason"])

    # ---------- 确认后冲突：禁用下载并列出两组真值 ----------
    def test_conflict_after_confirmation_disables_download(self):
        ex = self.conv_examples["conv_conflict"]
        self._save_original(ex["original_tree"])
        # 先保存候选树与草稿、试算并确认，使确认方案生效。
        r = self.client.post("/api/draft", json={
            "candidate_tree": ex["candidate_tree"], "draft": ex["draft"],
        })
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/trials")
        self.assertEqual(r.status_code, 201)
        job_id = r.get_json()["job_id"]
        for _ in range(50):
            job = self.client.get(f"/api/trials/{job_id}").get_json()["job"]
            if job["status"] not in ("pending", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "solved")
        r = self.client.post("/api/confirmations", json={"trial_id": job_id})
        self.assertEqual(r.status_code, 200)

        # 用放宽后的转换工况触发冲突。
        r = self._convert(ex["condition"])
        data = r.get_json()
        self.assertEqual(data["status"], "conflict")
        self.assertFalse(data["download_allowed"])
        self.assertEqual(data["routing_basis"], "candidate_confirmation")
        self.assertGreaterEqual(data["conflict_count"], 1)
        # 明细最多列出 50 组，且不超过总数。
        self.assertLessEqual(len(data["conflicts"]), 50)
        self.assertLessEqual(len(data["conflicts"]), data["conflict_count"])
        c0 = data["conflicts"][0]
        # 列出同温度同两个压力码、不同类别的两组（含原树与候选树）真值。
        cats = {g["category"] for g in c0["groups"]}
        self.assertGreaterEqual(len(cats), 2)
        trees = {g["tree_used"] for g in c0["groups"]}
        self.assertIn("candidate", trees)
        for g in c0["groups"]:
            self.assertEqual(g["truth"]["front"] >> 8, c0["front_code"])
            self.assertEqual(g["truth"]["back"] >> 8, c0["back_code"])

    # ---------- 无原树拒绝 ----------
    def test_conversion_without_original_tree_rejected(self):
        r = self._convert({
            "temperature": [0, 10], "front": [0, 10],
            "back": [0, 10], "diff": [0, 10],
        })
        self.assertEqual(r.status_code, 409)

    # ---------- 入参校验 ----------
    def test_conversion_validation(self):
        ex = self.conv_examples["conv_solvable"]
        self._save_original(ex["original_tree"])
        # 缺字段
        r = self.client.post("/api/rule-conversions", json={
            "temperature": [0, 10], "front": [0, 10], "back": [0, 10],
        })
        self.assertEqual(r.status_code, 400)
        # 区间反向
        r = self._convert({
            "temperature": [10, 0], "front": [0, 10],
            "back": [0, 10], "diff": [0, 10],
        })
        self.assertEqual(r.status_code, 400)
        # 布尔冒充整数
        r = self._convert({
            "temperature": [True, 10], "front": [0, 10],
            "back": [0, 10], "diff": [0, 10],
        })
        self.assertEqual(r.status_code, 400)
        # 压差越界
        r = self._convert({
            "temperature": [0, 10], "front": [0, 10],
            "back": [0, 10], "diff": [0, 70000],
        })
        self.assertEqual(r.status_code, 400)

    # ---------- 转换为只读，不改 revision、不需试算 ----------
    def test_conversion_is_stateless(self):
        ex = self.conv_examples["conv_solvable"]
        self._save_original(ex["original_tree"])
        before = self.client.get("/api/state").get_json()["revision"]
        self._convert(ex["condition"])
        self._convert(ex["condition"])
        after = self.client.get("/api/state").get_json()["revision"]
        self.assertEqual(before, after)
        state = self.client.get("/api/state").get_json()
        self.assertEqual(state["jobs"], [])


if __name__ == "__main__":
    unittest.main()
