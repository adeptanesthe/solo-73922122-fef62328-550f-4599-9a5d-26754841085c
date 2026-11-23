"""Flask 应用：原树录入、候选树与工况草稿、试算、确认、读数分流查询。"""

import json
import os
import threading

from flask import Flask, jsonify, render_template, request

from .converter import convert_rules
from .examples import CONVERSION_EXAMPLES, EXAMPLES
from .solver import run_trial
from .store import Store
from .tree import classify, validate_tree_full
from .validation import validate_draft, validate_reading


def create_app(data_file=None):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, "templates"),
        static_folder=os.path.join(base_dir, "static"),
    )
    data_file = data_file or os.environ.get("SEAL_DATA_FILE", "data/state.json")
    store = Store(data_file)
    app.config["STORE"] = store

    def error(message, status=400):
        return jsonify({"ok": False, "error": message}), status

    # ---------------- 页面 ----------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # ---------------- 元数据 ----------------
    @app.get("/api/state")
    def get_state():
        state = store.get_state()
        return jsonify({"ok": True, **state})

    @app.get("/api/examples")
    def get_examples():
        return jsonify({"ok": True, "examples": EXAMPLES,
                        "conversion_examples": CONVERSION_EXAMPLES})

    # ---------------- 原树 ----------------
    @app.post("/api/original-tree")
    def save_original_tree():
        payload = request.get_json(silent=True)
        if payload is None or "tree" not in payload:
            return error("请求体必须是包含 tree 字段的 JSON")
        tree = payload["tree"]
        try:
            validate_tree_full(tree)
        except ValueError as exc:
            return error(f"原树校验失败：{exc}")
        revision = store.save_original_tree(tree)
        return jsonify({"ok": True, "revision": revision})

    # ---------------- 候选树 + 工况草稿 ----------------
    @app.post("/api/draft")
    def save_draft():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "candidate_tree" not in payload or "draft" not in payload:
            return error("请求体必须包含 candidate_tree 与 draft 字段")
        try:
            validate_tree_full(payload["candidate_tree"])
            draft = validate_draft(payload["draft"])
        except ValueError as exc:
            return error(f"草稿校验失败：{exc}")
        revision = store.save_draft(payload["candidate_tree"], draft)
        return jsonify({"ok": True, "revision": revision})

    # ---------------- 试算（后台线程，不阻塞草稿保存）----------------
    @app.post("/api/trials")
    def create_trial():
        state = store.get_state()
        if state["original_tree"] is None:
            return error("请先录入并保存原树", 409)
        if state["candidate_tree"] is None or state["draft"] is None:
            return error("请先保存候选树与工况草稿", 409)

        revision = state["revision"]
        # 快照：试算只针对发起时的版本，后续改草稿不影响本任务，
        # 任务完成后会以 revision 是否仍为当前版本判断是否过期。
        snapshot = {
            "original_tree": json.loads(json.dumps(state["original_tree"])),
            "candidate_tree": json.loads(json.dumps(state["candidate_tree"])),
            "draft": json.loads(json.dumps(state["draft"])),
        }
        job = store.create_job(snapshot, revision)

        def worker():
            store.update_job(job["id"], status="running")
            try:
                result = run_trial(
                    snapshot["original_tree"],
                    snapshot["candidate_tree"],
                    snapshot["draft"],
                )
                store.update_job(
                    job["id"],
                    status=result["status"],
                    result=result,
                    finished_at=_now(),
                )
            except Exception as exc:  # 试算失败：记录原因，沿用当前分流
                store.update_job(
                    job["id"], status="error", error=str(exc), finished_at=_now()
                )

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"ok": True, "job_id": job["id"], "revision": revision}), 201

    @app.get("/api/trials/<job_id>")
    def get_trial(job_id):
        job = store.get_job(job_id)
        if job is None:
            return error("试算任务不存在", 404)
        current_revision = store.get_revision()
        return jsonify({
            "ok": True,
            "job": {
                "id": job["id"],
                "status": job["status"],
                "revision": job["revision"],
                "stale": job["revision"] != current_revision,
                "current_revision": current_revision,
                "result": job["result"],
                "error": job["error"],
                "created_at": job["created_at"],
                "finished_at": job["finished_at"],
            },
        })

    # ---------------- 确认 ----------------
    @app.post("/api/confirmations")
    def confirm():
        payload = request.get_json(silent=True) or {}
        job_id = payload.get("trial_id")
        if not job_id:
            return error("请提供试算任务 id（trial_id）")
        job = store.get_job(job_id)
        if job is None:
            return error("试算任务不存在，无法确认", 404)

        if job["status"] in ("pending", "running"):
            return error("该试算尚未完成，不能确认", 409)
        if job["status"] == "error":
            return error(f"试算失败，不能确认（原因：{job['error']}），沿用当前分流", 409)
        if job["status"] == "no_solution":
            reason = job["result"]["reason"] if job["result"] else "试算无解"
            return error(f"试算无解，不能确认（原因：{reason}），沿用当前分流", 409)

        current_revision = store.get_revision()
        if job["revision"] != current_revision:
            return error(
                "该试算对应的草稿已被修改（即使改回原值也必须重新试算），"
                "请用当前草稿重新试算后再确认",
                409,
            )
        if job["status"] != "solved" or not job.get("result") or not job["result"].get("zone"):
            return error("试算未产出有效温区，不能确认", 409)

        revision, confirmation = store.confirm(job)
        return jsonify({"ok": True, "revision": revision, "confirmation": confirmation})

    # ---------------- 读数分流查询 ----------------
    @app.post("/api/classify")
    def classify_reading():
        state = store.get_state()
        if state["original_tree"] is None:
            return error("尚未录入原树，无法分流", 409)
        payload = request.get_json(silent=True)
        try:
            temperature, front, back = validate_reading(payload)
        except ValueError as exc:
            return error(f"读数校验失败：{exc}")

        readings = {"temperature": temperature, "front": front, "back": back}
        confirmation = state["confirmation"]

        tree_used = "original"
        reason = "没有已确认的接管方案，全部读数使用原树"
        eligible = False
        if confirmation is not None:
            zone = confirmation["zone"]
            draft = confirmation["draft"]
            in_zone = zone[0] <= temperature <= zone[1]
            in_front = draft["front"][0] <= front <= draft["front"][1]
            in_back = draft["back"][0] <= back <= draft["back"][1]
            diff = back - front
            in_diff = draft["diff"][0] <= diff <= draft["diff"][1]
            # 获准工况以确认时的工况区间为准（温度由温区限定）。
            eligible = in_zone and in_front and in_back and in_diff
            if eligible:
                tree_used = "candidate"
                reason = (
                    f"读数位于已确认温区 [{zone[0]},{zone[1]}] 且符合获准工况，"
                    "使用候选树"
                )
            else:
                reasons = []
                if not in_zone:
                    reasons.append(f"温度 {temperature} 不在温区 [{zone[0]},{zone[1]}]")
                if not in_front:
                    reasons.append("前压不在获准区间")
                if not in_back:
                    reasons.append("后压不在获准区间")
                if not in_diff:
                    reasons.append(f"后压-前压={diff} 不在获准压差区间")
                reason = "；".join(reasons) + "，使用原树"

        tree = (
            confirmation["candidate_tree"]
            if tree_used == "candidate"
            else state["original_tree"]
        )
        category = classify(tree, readings)
        return jsonify({
            "ok": True,
            "readings": readings,
            "tree_used": tree_used,
            "eligible": eligible,
            "category": category,
            "reason": reason,
            "confirmation_zone": confirmation["zone"] if confirmation else None,
        })

    # ---------------- 备用控制器规则转换 ----------------
    @app.post("/api/rule-conversions")
    def convert_backup_rules():
        """按转换开始时生效的分流，把工况四闭区间转换为备用控制器规则。

        同步执行（区间算法毫秒级）；冲突/无解时 download_allowed=False 并说明原因，
        规则 JSON 只在无冲突且存在合规真值时返回，由前端落盘下载。
        """
        state = store.get_state()
        if state["original_tree"] is None:
            return error("尚未录入原树，无法进行规则转换", 409)
        payload = request.get_json(silent=True)
        try:
            condition = validate_draft(payload)
        except ValueError as exc:
            return error(f"转换工况校验失败：{exc}")

        # 转换开始时刻生效分流的快照：原树 + 当前确认方案（若有）。
        snapshot_state = store.get_state()
        original_tree = json.loads(json.dumps(snapshot_state["original_tree"]))
        confirmation = snapshot_state["confirmation"]
        if confirmation is not None:
            confirmation = json.loads(json.dumps(confirmation))

        output = convert_rules(original_tree, confirmation, condition)
        body = {
            "ok": True,
            "download_allowed": output["download_allowed"],
            "status": output["status"],
            "reason": output["reason"],
            "routing_basis": output["routing_basis"],
            "condition": output["condition"],
            "rules": output["rules"],
            "rule_count": output["rule_count"],
            "conflicts": output["conflicts"],
            "conflict_count": output["conflict_count"],
            "stats": output["stats"],
        }
        # 冲突/无解不是 HTTP 错误：前端仍需展示明细与原因，仅禁用下载。
        return jsonify(body)

    return app


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
