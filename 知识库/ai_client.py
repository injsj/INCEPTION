# -*- coding: utf-8 -*-
"""
AI 客户端（感官与手）—— 符号 AI 与知识库之间的唯一接口。
你的 AI 只需要 import 这个文件，就能获得感知和操作知识库的全部能力。

反馈信号语义（对符号 AI 而言，这些就是它的"感觉"）：
    200      成功取得知识
    401      身份不被承认 —— 密钥配置有误，应停止并告警
    403      知识存在但无权限 —— 这是边界感知，可申请授权或提交 REQ
    404      标记不存在 —— 自身推理/记忆有误，应修正引用
    503      世界关闭 —— 管理员关闭了总开关，应休眠等待
    KBError.status 为 -1  —— 无法连接知识库（服务未启动？）

学习回路：
    propose() 提交修改申请 → wait() 轮询 → 管理员执行/拒绝 + 意见
    状态与 admin_note 是 AI 最重要的外部纠错信号，应写入它的经验系统。

零第三方依赖（仅用 Python 标准库）。
"""

import json
import urllib.error
import urllib.request

# 五种申请动作（与知识库申请区一一对应）
MODIFY = "modify"                  # 修改已有知识
NEW = "new"                        # 新增知识
APPEND_EXPERIENCE = "append_experience"   # 追加经验（主要面向存疑区）
MOVE_ZONE = "move_zone"            # 建议移区（如存疑→信任）
SUSPECT_TO_PENDING = "suspect_to_pending"  # 发现存疑知识有误，申请打回待审核


class KBError(Exception):
    """知识库返回的错误。status 为 HTTP 状态码，-1 表示无法连接。"""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__("[{}] {}".format(status, message))


class KBClient:
    """知识库客户端。一个实例 = AI 的一双眼睛 + 一双手。"""

    def __init__(self, api_key, base_url="http://127.0.0.1:8320", timeout=10):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── 底层通信 ──

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail", "")
            except Exception:
                pass
            raise KBError(e.code, detail or e.reason)
        except urllib.error.URLError as e:
            raise KBError(-1, "无法连接知识库：{}".format(e.reason))

    # ── 感知 ──

    def catalog(self):
        """浏览公开目录（信任区 + 存疑区的标记/标题/简介，无正文）。
        返回 [{"zone":..,"label":..,"entries":[{"id","type","title","summary"}]}]"""
        return self._req("GET", "/api/catalog")["zones"]

    def read(self, eid):
        """读取一条知识的完整内容（含详细用法/经验/局限性/正文）。
        AI 可读全部区（含待审核区）；越权抛 KBError(403)。"""
        return self._req("GET", "/api/kb/{}".format(eid))["entry"]

    # ── 行动（修改申请）──

    def propose(self, action, target="", proposal="", reason="", proof=""):
        """向申请区提交一条修改申请。理由(reason)与证明过程(proof)必填。
        返回申请编号（如 "REQ5"）。"""
        d = self._req("POST", "/api/req", {
            "action": action, "target": target,
            "proposal": proposal, "reason": reason, "proof": proof})
        return d["id"]

    def my_requests(self):
        """查询自己全部申请及状态（待审核/已执行/已拒绝 + 管理员意见）。"""
        return self._req("GET", "/api/req")["reqs"]

    def wait(self, req_id, poll_seconds=5, max_polls=720):
        """轮询等待某个申请被处理（默认每 5 秒查一次，最长 1 小时）。
        返回该申请 dict（含 status 与 admin_note）。
        用法：申请提交后循环感知管理员的裁决，这是 AI 的学习时刻。"""
        import time
        for _ in range(max_polls):
            try:
                for r in self.my_requests():
                    if r["id"] == req_id and r["status"] != "待审核":
                        return r
            except KBError as e:
                # 第三轮审查 R3-4：401/403 是身份/权限问题，重试再久也不会自己好——
                # 立即抛出让 AI 告警，而不是傻等一小时后报超时误导排查方向。
                if e.status in (401, 403):
                    raise
                pass  # 服务暂时不可达（如管理员重启），继续等
            time.sleep(poll_seconds)
        raise KBError(408, "等待超时：申请 {} 仍未被处理".format(req_id))


def demo():
    """最小演示：浏览目录 → 读取一条 → 提交申请 → 查看状态。
    用法：python ai_client.py <AI密钥>  （密钥缺省时尝试环境变量 KB_API_KEY）"""
    import os
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KB_API_KEY", "")
    if not key:
        print("请提供 AI 密钥：python ai_client.py <密钥>")
        raise SystemExit(1)
    ai = KBClient(key)
    print("1) 浏览目录……")
    for zone in ai.catalog():
        print("   {}：".format(zone["label"]))
        for e in zone["entries"]:
            print("     {}  {} —— {}".format(e["id"], e["title"], e["summary"]))
    print("2) 读取 P1……")
    try:
        entry = ai.read("P1")
        print("   读到：{} | 用法:{}字 经验:{}字 局限:{}字".format(
            entry["title"], len(entry["usage"]), len(entry["experience"]),
            len(entry["limitations"])))
    except KBError as e:
        print("   读取失败：[{}] {}".format(e.status, e.message))
    print("3) 提交一条追加经验申请……")
    try:
        rid = ai.propose(APPEND_EXPERIENCE, target="P2",
                         proposal="在一次任务中验证了该手法的边界",
                         reason="补充实战样本，帮助未来判断适用范围",
                         proof="任务场景→执行步骤→观察结果（此处略，真实 AI 应输出完整链）")
        print("   已提交 {}".format(rid))
    except KBError as e:
        print("   提交失败：[{}] {}".format(e.status, e.message))
        rid = None
    print("4) 当前申请进度：")
    for r in ai.my_requests():
        print("   {} [{}] {} → {} {}".format(
            r["id"], r["action_name"], r["target"] or "（新增）",
            r["status"], ("| 管理员意见: " + r["admin_note"]) if r["admin_note"] else ""))


if __name__ == "__main__":
    demo()
