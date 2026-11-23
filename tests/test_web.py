"""Web 端到端自测：保存、试算、改稿过期、确认拒绝、分流规则、输入校验。"""

import json
import os
import tempfile
import time
import unittest

from app.tree import count_leaves
from app.web import create_app


def _count(node):
    return count_leaves(node)


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "state.json")
        app = create_app(data_file=self.db)
        app.testing = True
        self.client = app.test_client()
        self._load_example("solvable")

    def tearDown(self):
        self.tmp.cleanup()

    def _load_example(self, key):
        data = self.client.get("/api/examples").get_json()
        ex = next(e for e in data["examples"] if e["key"] == key)
        r = self.client.post("/api/original-tree", json={"tree": ex["original_tree"]})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/draft", json={
            "candidate_tree": ex["candidate_tree"],
            "draft": ex["draft"],
        })
        self.assertEqual(r.status_code, 200)
        return ex

    def _run_trial(self):
        r = self.client.post("/api/trials")
        self.assertEqual(r.status_code, 201)
        job_id = r.get_json()["job_id"]
        for _ in range(50):
            job = self.client.get(f"/api/trials/{job_id}").get_json()["job"]
            if job["status"] not in ("pending", "running"):
                return job
            time.sleep(0.05)
        self.fail("试算任务超时未完成")

    # ---------- 基本流程 ----------
    def test_full_flow_trial_confirm_and_classification(self):
        job = self._run_trial()
        self.assertEqual(job["status"], "solved")
        self.assertEqual(job["result"]["zone"], [150, 200])
        self.assertFalse(job["stale"])

        r = self.client.post("/api/confirmations", json={"trial_id": job["id"]})
        self.assertEqual(r.status_code, 200)
        conf = r.get_json()["confirmation"]
        self.assertEqual(conf["zone"], [150, 200])

        # 温区内 + 合规工况 → 候选树（示例中两树在此区域一致，类别同为 PASS）
        r = self.client.post("/api/classify", json={
            "temperature": 180, "front": 150, "back": 300,
        })
        data = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(data["tree_used"], "candidate")
        self.assertTrue(data["eligible"])
        self.assertEqual(data["category"], "PASS")

        # 温度在温区但压差不合规 → 原树
        r = self.client.post("/api/classify", json={
            "temperature": 180, "front": 100, "back": 500,  # 差 400 > 300
        })
        data = r.get_json()
        self.assertEqual(data["tree_used"], "original")
        self.assertFalse(data["eligible"])
        self.assertIn("压差", data["reason"])

        # 温度不在温区（高温且后压高，两树类别不同：原树 REJECT）→ 原树
        r = self.client.post("/api/classify", json={
            "temperature": 250, "front": 150, "back": 450,
        })
        data = r.get_json()
        self.assertEqual(data["tree_used"], "original")
        self.assertEqual(data["category"], "REJECT")

        # 温度不在温区但其余合规 → 原树
        r = self.client.post("/api/classify", json={
            "temperature": 140, "front": 150, "back": 200,
        })
        self.assertEqual(r.get_json()["tree_used"], "original")

        # 压力出区间 → 原树
        r = self.client.post("/api/classify", json={
            "temperature": 180, "front": 90, "back": 200,
        })
        self.assertEqual(r.get_json()["tree_used"], "original")

    # ---------- 旧试算被拒（核心验收点）----------
    def test_stale_trial_rejected_even_after_change_back(self):
        job = self._run_trial()
        self.assertEqual(job["status"], "solved")

        # 改一次草稿：旧试算立即过期
        state = self.client.get("/api/state").get_json()
        draft = state["draft"]
        draft["temperature"] = [150, 299]
        r = self.client.post("/api/draft", json={
            "candidate_tree": state["candidate_tree"], "draft": draft,
        })
        self.assertEqual(r.status_code, 200)

        stale = self.client.get(f"/api/trials/{job['id']}").get_json()["job"]
        self.assertTrue(stale["stale"])
        r = self.client.post("/api/confirmations", json={"trial_id": job["id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("重新试算", r.get_json()["error"])

        # 改回原值：revision 仍然变化，旧试算依旧被拒
        draft["temperature"] = [150, 300]
        r = self.client.post("/api/draft", json={
            "candidate_tree": state["candidate_tree"], "draft": draft,
        })
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/confirmations", json={"trial_id": job["id"]})
        self.assertEqual(r.status_code, 409)

        # 重新试算后才能确认
        job2 = self._run_trial()
        self.assertFalse(job2["stale"])
        r = self.client.post("/api/confirmations", json={"trial_id": job2["id"]})
        self.assertEqual(r.status_code, 200)

    def test_trial_running_while_saving_draft(self):
        # 发起试算后立刻保存草稿，保存必须成功（试算不阻塞草稿）
        r = self.client.post("/api/trials")
        self.assertEqual(r.status_code, 201)
        job_id = r.get_json()["job_id"]
        state = self.client.get("/api/state").get_json()
        r = self.client.post("/api/draft", json={
            "candidate_tree": state["candidate_tree"], "draft": state["draft"],
        })
        self.assertEqual(r.status_code, 200)
        # 该试算绑定旧快照，完成后应为 stale
        for _ in range(50):
            job = self.client.get(f"/api/trials/{job_id}").get_json()["job"]
            if job["status"] not in ("pending", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "solved")
        self.assertTrue(job["stale"])

    # ---------- 无解 / 失败 ----------
    def test_infeasible_pressure_trial_cannot_confirm(self):
        self._load_example("infeasible_pressure")
        job = self._run_trial()
        self.assertEqual(job["status"], "no_solution")
        self.assertIn("无解", job["result"]["reason"])
        r = self.client.post("/api/confirmations", json={"trial_id": job["id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("沿用当前分流", r.get_json()["error"])

    def test_no_safe_zone_trial(self):
        self._load_example("no_safe_zone")
        job = self._run_trial()
        self.assertEqual(job["status"], "no_solution")
        self.assertTrue(job["result"]["pressure_feasible"])

    def test_unknown_job_and_missing_trial_id(self):
        r = self.client.post("/api/confirmations", json={"trial_id": "nope"})
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/api/confirmations", json={})
        self.assertEqual(r.status_code, 400)

    # ---------- 校验 ----------
    def test_input_validation(self):
        # 树叶子超上限：每层把当前树复制成两份，7 层后 2^7=128，8 层后 256
        big = {"leaf": "A"}
        for _ in range(8):
            big = {"feature": "temperature", "threshold": 1,
                   "left": big, "right": dict(big)}
        self.assertEqual(256, _count(big))
        r = self.client.post("/api/original-tree", json={"tree": big})
        self.assertEqual(r.status_code, 400)
        self.assertIn("128", r.get_json()["error"])

        # 非法 feature
        bad = {"feature": "weight", "threshold": 1,
               "left": {"leaf": "A"}, "right": {"leaf": "B"}}
        r = self.client.post("/api/original-tree", json={"tree": bad})
        self.assertEqual(r.status_code, 400)

        # 阈值越界 / 布尔冒充整数
        r = self.client.post("/api/original-tree", json={"tree": {
            "feature": "front", "threshold": 70000,
            "left": {"leaf": "A"}, "right": {"leaf": "B"}}})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/original-tree", json={"tree": {
            "feature": "front", "threshold": True,
            "left": {"leaf": "A"}, "right": {"leaf": "B"}}})
        self.assertEqual(r.status_code, 400)

        # 工况区间反了 / 读数越界
        r = self.client.post("/api/draft", json={
            "candidate_tree": {"leaf": "A"},
            "draft": {"temperature": [300, 150], "front": [0, 1],
                      "back": [0, 1], "diff": [0, 1]},
        })
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/classify", json={
            "temperature": 65536, "front": 0, "back": 0})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/classify", json={
            "temperature": 1.5, "front": 0, "back": 0})
        self.assertEqual(r.status_code, 400)

    def test_trial_without_trees_rejected(self):
        # 新库、未录入任何内容
        db2 = os.path.join(self.tmp.name, "empty.json")
        app2 = create_app(data_file=db2)
        client2 = app2.test_client()
        r = client2.post("/api/trials")
        self.assertEqual(r.status_code, 409)
        r = client2.post("/api/classify", json={
            "temperature": 1, "front": 2, "back": 3})
        self.assertEqual(r.status_code, 409)

    def test_persistence_across_app_recreation(self):
        job = self._run_trial()
        self.client.post("/api/confirmations", json={"trial_id": job["id"]})
        # 用同一数据文件重建“应用”（模拟重启）：确认仍在，任务内存清空
        app2 = create_app(data_file=self.db)
        client2 = app2.test_client()
        state = client2.get("/api/state").get_json()
        self.assertIsNotNone(state["confirmation"])
        self.assertEqual(state["confirmation"]["zone"], [150, 200])
        self.assertEqual(state["jobs"], [])
        # 分流规则仍然生效
        r = client2.post("/api/classify", json={
            "temperature": 180, "front": 150, "back": 300})
        self.assertEqual(r.get_json()["tree_used"], "candidate")


if __name__ == "__main__":
    unittest.main()
