"""演示数据播种（干净起点，用于从头走一遍主流程）。

    python scripts/seed_demo.py            # 只造数据（已有同名数据会跳过）
    python scripts/seed_demo.py --reset    # 先清空全部业务数据，再造

**清空范围**：职位 / 轮次 / JD 版本 / 候选人 / 应聘记录 / 会话 / 匹配分 / 反馈 / 通知。
**保留**：用户、角色、权限、数据范围、保留策略（账号体系不动，否则演示账号全没了）。

**不走 LLM**：JD 结构化与简历档案都是手工写的，解析确认也直接给 profile ——
播种要的是"可复现的干净起点"，不是让每次跑都烧一遍 token。
匹配分由规则同步算出（不走模型），AI 总结由 Celery 异步补写，没有 worker 时
停在 pending 不影响主流程。

造出来的五条（覆盖主流程各入口）：

| 候选人 | 职位       | 阶段     | 用途                                   |
| :----- | :--------- | :------- | :------------------------------------- |
| 张三   | 高级后端   | in_r1    | 陈技术：从 Step1 走完五步到提交        |
| 李四   | 高级后端   | in_r1    | 陈技术：第二条备面（走另一个结论）     |
| 王五   | 高级后端   | pending  | HR：走 P05 解析确认 → 派单             |
| 赵六   | 高级后端   | in_r2    | 刘架构：二面备面；一面面评已提交（P17 可看） |
| 孙七   | 前端       | in_r1    | 陈技术：两轮流程（r1 → hr），另一条职位线 |
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 从 scripts/ 里跑时项目根不在 sys.path 上（云端容器里同样是 python scripts/seed_demo.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models.candidate import Application, Candidate
from app.models.match_score import MatchFeedback, MatchScore
from app.models.position import JdVersion, Position, PositionRound
from app.models.session import InterviewSession
from app.models.user import User
from app.services import board as board_svc
from app.services import candidate as candidate_svc
from app.schemas.candidate import CandidateConfirmIn, CandidateCreateIn

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("seed_demo")

HR_EMAIL = "hr@prepilot.dev"
IV_R1_EMAIL = "interviewer@prepilot.dev"  # 陈技术
IV_R2_EMAIL = "interviewer2@prepilot.dev"  # 刘架构

BACKEND_JD = {
    "raw_text": "高级后端工程师（支付方向）",
    "hard_gates": ["本科及以上", "3 年以上后端开发经验"],
    "competencies": [
        {"text": "Java 与 JVM 调优", "weight": 40},
        {"text": "数据库与 SQL 优化", "weight": 35},
        {"text": "分布式与消息队列", "weight": 25},
    ],
    "bonuses": ["有支付 / 金融行业经验", "有高并发系统调优案例"],
}

FRONTEND_JD = {
    "raw_text": "前端工程师（数据可视化方向）",
    "hard_gates": ["本科及以上", "2 年以上前端开发经验"],
    "competencies": [
        {"text": "React 与状态管理", "weight": 40},
        {"text": "可视化与图表性能", "weight": 35},
        {"text": "工程化与构建优化", "weight": 25},
    ],
    "bonuses": ["有 Canvas / WebGL 经验"],
}


RESUMES = {
    "张三": """张三
邮箱 zhangsan@example.com  电话 13800138000  北京
工作年限：7 年
2018-2021 A公司 后端工程师 支付清结算 Java MySQL Redis
2021-至今 B公司 高级后端工程师 支付网关重构 负责人 Java Kafka Kubernetes
项目：支付网关重构（QPS 从 2k 提到 1.2w）；对账系统日终批处理优化
技能：Java JVM 调优 MySQL Redis Kafka Kubernetes
教育：某大学 计算机科学与技术 本科 2014-2018""",
    "李四": """李四
邮箱 lisi@example.com  电话 13800138001  上海
工作年限：4 年
2020-2022 C公司 后端工程师 订单系统 Go MySQL
2022-至今 D公司 后端工程师 营销活动系统 Java Redis
项目：秒杀活动支撑（峰值 5k QPS）
技能：Java Go MySQL Redis
教育：某大学 软件工程 本科 2016-2020""",
    "王五": """王五
邮箱 wangwu@example.com  电话 13800138002  深圳
工作年限：5 年
2019-2022 E公司 后端工程师 交易系统 Java MySQL
2022-至今 F公司 高级工程师 服务治理 Kafka Kubernetes
项目：服务网格落地
技能：Java MySQL Kafka Kubernetes Redis
教育：某大学 计算机 硕士 2016-2019""",
    "赵六": """赵六
邮箱 zhaoliu@example.com  电话 13800138003  杭州
工作年限：8 年
2016-2020 G公司 后端工程师 风控系统 Java Oracle
2020-至今 H公司 技术专家 支付中台 Java Kafka Flink
项目：实时风控引擎（日处理 3 亿条）；支付链路全链路压测
技能：Java JVM 调优 Kafka Flink MySQL Redis
教育：某大学 计算机 本科 2012-2016""",
    "孙七": """孙七
邮箱 sunqi@example.com  电话 13800138004  广州
工作年限：3 年
2021-至今 I公司 前端工程师 数据看板 React TypeScript ECharts
项目：经营看板（200+ 图表，首屏从 6s 优化到 1.8s）
技能：React TypeScript ECharts Webpack Vite
教育：某大学 软件工程 本科 2018-2021""",
    # HR 面演示用：简历里要能看出「跳槽频率、期望薪资、到岗时间」这类 HR 才关心的信号
    "周八": """周八
邮箱 zhouba@example.com  电话 13800138005  成都
工作年限：6 年
2019-2021 J公司 后端工程师 会员系统 Java MySQL
2021-2022 K公司 后端工程师 营销平台 Java Redis
2022-至今 L公司 高级后端工程师 支付清结算 Java Kafka
期望薪资：35-45K
到岗时间：一个月内（需交接）
技能：Java MySQL Redis Kafka
教育：某大学 软件工程 本科 2015-2019""",
}

PROFILES = {
    "张三": {
        "basic": {"name": "张三", "email": "zhangsan@example.com", "phone": "13800138000", "city": "北京"},
        "work": [
            {"company": "B公司", "title": "高级后端工程师", "period": "2021-至今", "desc": "支付网关重构负责人"},
            {"company": "A公司", "title": "后端工程师", "period": "2018-2021", "desc": "支付清结算"},
        ],
        "projects": [{"name": "支付网关重构", "role": "负责人", "desc": "QPS 从 2k 提升到 1.2w"}],
        "skills": ["Java", "JVM 调优", "MySQL", "Redis", "Kafka", "Kubernetes"],
        "education": [{"school": "某大学", "major": "计算机科学与技术", "degree": "本科", "period": "2014-2018"}],
        "confidence": {},
    },
    "李四": {
        "basic": {"name": "李四", "email": "lisi@example.com", "phone": "13800138001", "city": "上海"},
        "work": [
            {"company": "D公司", "title": "后端工程师", "period": "2022-至今", "desc": "营销活动系统"},
            {"company": "C公司", "title": "后端工程师", "period": "2020-2022", "desc": "订单系统"},
        ],
        "projects": [{"name": "秒杀活动支撑", "role": "开发", "desc": "峰值 5k QPS"}],
        "skills": ["Java", "Go", "MySQL", "Redis"],
        "education": [{"school": "某大学", "major": "软件工程", "degree": "本科", "period": "2016-2020"}],
        "confidence": {},
    },
    "王五": {
        "basic": {"name": "王五", "email": "wangwu@example.com", "phone": "13800138002", "city": "深圳"},
        "work": [
            {"company": "F公司", "title": "高级工程师", "period": "2022-至今", "desc": "服务治理"},
            {"company": "E公司", "title": "后端工程师", "period": "2019-2022", "desc": "交易系统"},
        ],
        "projects": [{"name": "服务网格落地", "role": "核心开发", "desc": "全链路灰度"}],
        "skills": ["Java", "MySQL", "Kafka", "Kubernetes", "Redis"],
        "education": [{"school": "某大学", "major": "计算机", "degree": "硕士", "period": "2016-2019"}],
        "confidence": {},
    },
    "赵六": {
        "basic": {"name": "赵六", "email": "zhaoliu@example.com", "phone": "13800138003", "city": "杭州"},
        "work": [
            {"company": "H公司", "title": "技术专家", "period": "2020-至今", "desc": "支付中台"},
            {"company": "G公司", "title": "后端工程师", "period": "2016-2020", "desc": "风控系统"},
        ],
        "projects": [{"name": "实时风控引擎", "role": "负责人", "desc": "日处理 3 亿条"}],
        "skills": ["Java", "JVM 调优", "Kafka", "Flink", "MySQL", "Redis"],
        "education": [{"school": "某大学", "major": "计算机", "degree": "本科", "period": "2012-2016"}],
        "confidence": {},
    },
    "孙七": {
        "basic": {"name": "孙七", "email": "sunqi@example.com", "phone": "13800138004", "city": "广州"},
        "work": [{"company": "I公司", "title": "前端工程师", "period": "2021-至今", "desc": "数据看板"}],
        "projects": [{"name": "经营看板", "role": "前端负责人", "desc": "首屏 6s 优化到 1.8s"}],
        "skills": ["React", "TypeScript", "ECharts", "Webpack", "Vite"],
        "education": [{"school": "某大学", "major": "软件工程", "degree": "本科", "period": "2018-2021"}],
        "confidence": {},
    },
    "周八": {
        "basic": {"name": "周八", "email": "zhouba@example.com", "phone": "13800138005", "city": "成都"},
        "work": [
            {"company": "L公司", "title": "高级后端工程师", "period": "2022-至今", "desc": "支付清结算"},
            {"company": "K公司", "title": "后端工程师", "period": "2021-2022", "desc": "营销平台"},
            {"company": "J公司", "title": "后端工程师", "period": "2019-2021", "desc": "会员系统"},
        ],
        "projects": [],
        "skills": ["Java", "MySQL", "Redis", "Kafka"],
        "education": [{"school": "某大学", "major": "软件工程", "degree": "本科", "period": "2015-2019"}],
        "confidence": {},
    },
}

# 赵六的一面面评（预置一份已提交的面评，供 P17 演示）
ZHAOLIU_R1_EVAL = {
    "items": [
        {
            "row_id": "m1",
            "capability": "Java 与 JVM 调优",
            "score": 5,
            "evidences": [
                {"id": "e1", "text": "讲清了 CMS 与 G1 的取舍，给出过一次 Full GC 排查过程", "quote": True}
            ],
            "note": "深度足够，能讲参数背后的取舍",
        },
        {
            "row_id": "m2",
            "capability": "数据库与 SQL 优化",
            "score": 4,
            "evidences": [
                {"id": "e2", "text": "慢查询定位说得清楚，分库分表方案偏理论", "quote": False}
            ],
            "note": "",
        },
        {
            "row_id": "m3",
            "capability": "分布式与消息队列",
            "score": 4,
            "evidences": [{"id": "e3", "text": "Kafka 零拷贝与分区分配讲得对", "quote": True}],
            "note": "",
        },
    ],
    "summary": "技术深度达到专家级别，沟通有条理，建议推进到二面重点验证架构与带人能力。",
    "summary_original": "这人技术很扎实，讲得清楚，可以进二面。",
    "complete": True,
    "polished_adopted": True,
}


# ---------------------------------------------------------------- 清空


async def reset(db) -> None:
    """清空业务数据（账号与策略不动）。顺序按外键依赖：子表在前。"""
    tables = [
        "match_feedbacks",
        "match_scores",
        "interview_sessions",
        "applications",
        "candidates",
        "jd_versions",
        "position_rounds",
        "positions",
    ]
    for t in tables:
        await db.execute(text(f"DELETE FROM {t}"))
    await db.execute(text("DELETE FROM notifications"))
    await db.commit()
    print(f"[reset] 已清空：{', '.join(tables)} + notifications")


# ---------------------------------------------------------------- 造数据


async def _user(db, email: str) -> User:
    u = await db.scalar(select(User).where(User.email == email))
    if u is None:
        raise SystemExit(f"找不到用户 {email}，请先跑 scripts/seed.py 播种账号")
    return u


async def _position(db, hr: User, name: str, jd: dict) -> Position:
    """直接建职位 + 已确认的 JD（不调 AI 拆解）。"""
    pos = Position(
        name=name,
        owner_id=hr.id,
        status="open",
        jd_raw_text=jd["raw_text"],
        jd_hard_gates=list(jd["hard_gates"]),
        jd_competencies=list(jd["competencies"]),
        jd_bonuses=list(jd["bonuses"]),
        jd_status="confirmed",
        jd_version=1,
    )
    db.add(pos)
    await db.flush()
    db.add(
        JdVersion(
            position_id=pos.id,
            version=1,
            change_summary="初始版本：3 项核心能力 + 2 项硬性门槛",
            changed_by=hr.id,
            snapshot_json={
                "raw_text": jd["raw_text"],
                "hard_gates": jd["hard_gates"],
                "competencies": jd["competencies"],
                "bonuses": jd["bonuses"],
            },
        )
    )
    await db.flush()
    return pos


async def _rounds(db, pos: Position, spec: list[tuple[str, int]]) -> None:
    for i, (rtype, iv_id) in enumerate(spec, start=1):
        db.add(
            PositionRound(
                position_id=pos.id,
                seq=i,
                type=rtype,
                name={"r1": "技术一面", "r2": "技术二面", "hr": "HR 面试"}[rtype],
                interviewer_id=iv_id,
            )
        )
    await db.flush()
    # 关系预加载：派单会读 position.rounds，新建对象上访问未加载的关系会触发同步 IO（MissingGreenlet）
    await db.refresh(pos, ["rounds"])


async def _upload(db, hr: User, pos: Position, name: str) -> Candidate:
    payload = CandidateCreateIn(
        position_id=pos.id,
        name=name,
        contact_email=f"{name}@example.com",
        contact_phone="13800138000",
        raw_text=RESUMES[name],
        auth_tick=True,
    )
    return await candidate_svc.create_candidate(db, hr, payload)


async def _confirm(db, hr: User, cid: int, name: str) -> Candidate:
    cand = await db.get(Candidate, cid)
    await db.refresh(cand, ["applications"])
    _, notice = await candidate_svc.confirm_candidate(
        db, hr, cand, dict(PROFILES[name])
    )
    if notice:
        print(f"  ! 派单提示（{name}）：{notice}")
    return cand


async def _submit_r1_evaluation(db, candidate_id: int) -> None:
    """把该候选人的 r1 会话写成已提交（跳过 LLM / 向量库）。"""
    s = await db.scalar(
        select(InterviewSession).where(
            InterviewSession.candidate_id == candidate_id,
            InterviewSession.round_type == "r1",
        )
    )
    if s is None:
        return
    s.evaluation_json = dict(ZHAOLIU_R1_EVAL)
    s.status = "submitted"
    s.conclusion = "pass"
    s.submitted_at = __import__("datetime").datetime.now()
    s.fairness_json = {"result": "pass", "scanned_at": __import__("datetime").datetime.now().isoformat()}
    await db.commit()


async def seed() -> None:
    async with SessionLocal(expire_on_commit=False) as db:
        hr = await _user(db, HR_EMAIL)
        iv1 = await _user(db, IV_R1_EMAIL)
        iv2 = await _user(db, IV_R2_EMAIL)

        # 1) 职位 A：高级后端（r1 陈技术 / r2 刘架构 / hr）
        pos_a = await _position(db, hr, "高级后端工程师（支付方向）", BACKEND_JD)
        await _rounds(db, pos_a, [("r1", iv1.id), ("r2", iv2.id), ("hr", hr.id)])
        # 2) 职位 B：前端（r1 陈技术 / hr）
        pos_b = await _position(db, hr, "前端工程师（数据可视化）", FRONTEND_JD)
        await _rounds(db, pos_b, [("r1", iv1.id), ("hr", hr.id)])
        await db.commit()
        print(f"[ok] 职位 {pos_a.name}(id={pos_a.id}) / {pos_b.name}(id={pos_b.id})")

        # 3) 候选人：张三、李四（in_r1）/ 王五（pending）/ 赵六（推进到 in_r2）/
        #    周八（推进到 in_hr，HR 面演示入口）/ 孙七（职位 B in_r1）
        for name, pos in (("张三", pos_a), ("李四", pos_a)):
            c = await _upload(db, hr, pos, name)
            await _confirm(db, hr, c.id, name)
            print(f"[ok] {name} → {pos.name} in_r1（陈技术备面）")

        c = await _upload(db, hr, pos_a, "王五")
        print(f"[ok] 王五 → {pos_a.name} pending（待 HR 解析确认）")

        c = await _upload(db, hr, pos_a, "赵六")
        await _confirm(db, hr, c.id, "赵六")
        await _submit_r1_evaluation(db, c.id)
        app = await db.scalar(
            select(Application).where(Application.candidate_id == c.id)
        )
        await board_svc.transition(db, hr, app.id, "advance")
        print("[ok] 赵六 → 一面已提交并推进到 in_r2（刘架构备面；P17 可看一面面评）")

        c = await _upload(db, hr, pos_b, "孙七")
        await _confirm(db, hr, c.id, "孙七")
        print(f"[ok] 孙七 → {pos_b.name} in_r1（陈技术备面）")

        # HR 面的演示入口：流程走到 hr 轮，王招聘本人就是该轮面试官（可写）。
        # 没有这条数据的话，HR 轮次画像（BR-29）根本没地方验证 ——
        # 建档只给当前轮派单，会话要推进过去才存在。
        c = await _upload(db, hr, pos_a, "周八")
        await _confirm(db, hr, c.id, "周八")
        app = await db.scalar(
            select(Application).where(Application.candidate_id == c.id)
        )
        for _ in range(2):  # 确认建档已在 in_r1 → r2 → hr
            await board_svc.transition(db, hr, app.id, "advance")
        print("[ok] 周八 → 已推进到 in_hr（王招聘本人面试，HR 面轮次画像演示）")

    print("\n完成。登录方式：hr@prepilot.dev / interviewer@prepilot.dev / interviewer2@prepilot.dev，密码 Prepilot@123")


async def main(reset_first: bool) -> None:
    if reset_first:
        async with SessionLocal() as db:
            await reset(db)
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空全部业务数据再播种")
    args = ap.parse_args()
    asyncio.run(main(args.reset))
