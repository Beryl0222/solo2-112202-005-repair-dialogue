"""返修接力的领域规则测试。"""
from __future__ import annotations

import unittest

from pack_check import read_tables
from repairs import (
    AssetPool,
    CaseFile,
    Condition,
    Config,
    Finding,
    Observation,
    SourceKind,
    Station,
)
from repairs.knowledge import CHECK_NAMES

TABLES = read_tables()
MANUAL = TABLES["manual.tsv"]


def make_stations():
    return [Station.from_row(r, CHECK_NAMES) for r in TABLES["stations.tsv"]]


def make_assets():
    return AssetPool.from_rows(TABLES["loan_log.tsv"])


def make_case(case):
    return CaseFile.build(case, TABLES)


def obs(case, source, head, gear, finding, body=None, load=None, oid="T#1"):
    return Observation(
        id=oid, case=case, source=source,
        config=Config(head=head, body=body),
        condition=Condition(gear=gear, load=load),
        finding=finding, text="测试录入", origin=oid.split("#")[0],
    )


class ExtractionTest(unittest.TestCase):
    """转写：原说法、对象与上下文都保留。"""

    @classmethod
    def setUpClass(cls):
        cls.r7 = make_case("R7")

    def find(self, oid):
        return next(o for o in self.r7.observations if o.id == oid)

    def test_untested_is_not_read_as_silent(self):
        """“高档没试”是未试，不能读成“没响”。"""
        obs = self.find("M1#2")
        self.assertIs(obs.finding, Finding.UNTESTED)
        self.assertEqual(obs.condition.gear, "高档")
        self.assertEqual(obs.config.head, "原机头")

    def test_anaphora_binds_to_symptom_and_new_object(self):
        """“还是这样”按最近症状理解，对象换成备用头，并记下指代来源。"""
        obs = self.find("M1#3")
        self.assertEqual(obs.config.head, "备用头")
        self.assertEqual(obs.condition.gear, "低档")
        self.assertIs(obs.finding, Finding.SOUNDING)
        self.assertEqual(obs.resolved_from, "M1#1")

    def test_hearsay_is_marked_and_kept_out_of_evidence(self):
        hearsay = self.find("M3#4")
        self.assertIs(hearsay.source, SourceKind.HEARSAY)
        self.assertNotIn(hearsay, self.r7.direct_observations())
        self.assertEqual(hearsay.status_label(), "只听转述")

    def test_correction_retargets_object_and_keeps_original(self):
        original = self.find("M4#1")
        revised = self.find("M5#1")
        self.assertEqual(original.config.head, "B头")  # 原记录不改写
        self.assertEqual(original.corrected_by, "M5#1")
        self.assertEqual(revised.config.head, "原机头")
        self.assertEqual(revised.corrects, "M4#1")
        live_heads = [o.config.head for o in self.r7.live_observations()]
        self.assertNotIn("B头", live_heads)  # 订正后不再按 B 头理解

    def test_intermittent_and_conditional_statuses(self):
        self.assertIs(self.find("M4#2").finding, Finding.INTERMITTENT)
        self.assertIs(self.find("D1#1").finding, Finding.INTERMITTENT)
        self.assertEqual(self.find("M3#2").status_label(), "条件复现")
        self.assertEqual(self.find("M3#3").status_label(), "明确排除")

    def test_scheduled_but_not_performed_check_is_untested(self):
        d2 = self.find("D2#1")
        self.assertIs(d2.finding, Finding.UNTESTED)
        self.assertIn("预约未领仪器", d2.note)

    def test_every_observation_keeps_wording_and_origin(self):
        for o in self.r7.observations:
            self.assertTrue(o.text, o.id)
            self.assertTrue(o.origin, o.id)

    def test_frontdesk_questions_are_kept(self):
        self.assertEqual(len(self.r7.asked), 1)
        self.assertIn("备用头在哪个档位", self.r7.asked[0].text)


class ReconcileTest(unittest.TestCase):
    """复测与口述的对账规则。"""

    def test_same_config_same_condition_overturns(self):
        case = CaseFile.from_observations("X", [
            obs("X", SourceKind.USER, "原机头", "低档", Finding.SOUNDING,
                load="装杯", oid="U#1"),
            obs("X", SourceKind.TECH, "原机头", "低档", Finding.SILENT,
                load="装杯", oid="T#1"),
        ], MANUAL)
        self.assertEqual(len(case.overturns), 1)
        self.assertEqual(case.overturns[0].overturned.id, "U#1")
        self.assertFalse(case.differences)

    def test_missing_condition_keeps_difference(self):
        """找不到对应条件时保留差异，不判胜负。"""
        case = make_case("R7")
        self.assertFalse(case.overturns)
        self.assertEqual(len(case.differences), 1)
        diff = case.differences[0]
        self.assertEqual(diff.user_obs.id, "M1#1")
        self.assertEqual(diff.tech_obs.id, "M5#1")

    def test_distinct_conditions_neither_overturn_nor_difference(self):
        """空转排除与清水偶发是两种工况，互不否定。"""
        case = CaseFile.from_observations("X", [
            obs("X", SourceKind.USER, "备用头", "低档", Finding.SILENT,
                load="空转", oid="U#1"),
            obs("X", SourceKind.TECH, "备用头", "低档", Finding.INTERMITTENT,
                load="清水", oid="T#1"),
        ], MANUAL)
        self.assertFalse(case.overturns)
        self.assertFalse(case.differences)

    def test_gear_negation_does_not_cancel_other_gears(self):
        """否定某一档位不取消其他档位的观察。"""
        case = CaseFile.from_observations("X", [
            obs("X", SourceKind.USER, "原机头", "低档", Finding.SILENT, oid="U#1"),
            obs("X", SourceKind.USER, "原机头", "高档", Finding.SOUNDING, oid="U#2"),
        ], MANUAL)
        live = {(o.condition.gear, o.finding) for o in case.live_observations()}
        self.assertIn(("高档", Finding.SOUNDING), live)
        self.assertIn(("低档", Finding.SILENT), live)


class RouteTest(unittest.TestCase):
    """原因候选来自检修手册，按证据评估。"""

    def test_r7_routes(self):
        routes = {o.route: o for o in make_case("R7").routes()}
        self.assertEqual(routes["H1"].state, "成立")   # 装杯才发声且换机头仍出现
        self.assertEqual(routes["H2"].state, "未证实")  # 未见同工况机头差别
        self.assertEqual(routes["H3"].state, "未证实")  # 未做机身对照
        self.assertEqual(routes["H4"].state, "未证实")  # 高档未试
        self.assertTrue(routes["H4"].gaps)

    def test_r8_body_route_opens_after_swap_check(self):
        """一次对照观察排除机头路径，开启必须由专岗完成的轴座检查。"""
        routes = {o.route: o for o in make_case("R8").routes()}
        self.assertEqual(routes["H3"].state, "成立")
        self.assertEqual(routes["H2"].state, "未证实")
        bearing = routes["H3"].checks[0]
        self.assertIn("轴座检具", bearing.requires)  # 专岗检具

    def test_empty_cup_does_not_cancel_water(self):
        """空杯未响不否定带水复现（手册 H1 原文规则）。"""
        routes = {o.route: o for o in make_case("R7").routes()}
        self.assertEqual(routes["H1"].state, "成立")
        self.assertTrue(any("不可相互否定" in r for r in routes["H1"].reasons))


class PlanTest(unittest.TestCase):
    """下一步：按工位能力与前置条件，不按问题词数。"""

    @classmethod
    def setUpClass(cls):
        cls.stations = make_stations()
        cls.assets = make_assets()

    def test_r7_discriminating_check_at_current_station(self):
        """对照件借出时，改用当前工位可做且能区分两条候选的检查。"""
        plan = make_case("R7").plan(self.stations, self.assets)
        self.assertEqual(plan.decision, "先做鉴别检查")
        feasible = [c for c in plan.do_now if c.feasible]
        self.assertTrue(feasible)
        self.assertTrue(all(c.station == "前台兼观察台" for c in feasible))
        self.assertTrue(any("高档" in c.name for c in feasible))

    def test_r7_signed_check_paused_when_premise_fails(self):
        """新信息推翻交接中的检查前提：通知经手人停，前台看到下一棒。"""
        plan = make_case("R7").plan(self.stations, self.assets)
        paused = [n for n in plan.notices if n.action == "暂停"]
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].station, "对照台")
        self.assertIn("套筒跟随检查", paused[0].reason)

    def test_r7_blocked_checks_carry_reasons(self):
        plan = make_case("R7").plan(self.stations, self.assets)
        blocked_text = "；".join(b for c in plan.blocked for b in c.blockers)
        self.assertIn("B头借出", blocked_text)
        self.assertIn("尚未归还", blocked_text)

    def test_r7_config_consumption_warning(self):
        """装 B 头会拆下用户封装的备用头：先做的观察与维修作业分开交接。"""
        plan = make_case("R7").plan(self.stations, self.assets)
        self.assertTrue(plan.warnings)
        warning = plan.warnings[0]
        self.assertIn("备用头", warning)
        self.assertIn("D1#1", warning)  # 间歇观察将不可复现
        self.assertIn("分开交接", warning)

    def test_r7_no_question_when_station_can_observe(self):
        """缺的观察站点都能补，不占用用户一次回复。"""
        plan = make_case("R7").plan(self.stations, self.assets)
        self.assertEqual(plan.questions, [])

    def test_r8_dispatch_to_capable_station(self):
        plan = make_case("R8").plan(self.stations, self.assets)
        self.assertEqual(plan.decision, "派修到精修台")
        self.assertEqual(plan.do_now[0].name, "轴座检查")
        self.assertEqual(plan.do_now[0].station, "精修台")
        incoming = [n for n in plan.notices if n.action == "接件"]
        self.assertEqual(incoming[0].station, "精修台")

    def test_r8_speed_bench_blocked_by_availability(self):
        plan = make_case("R8").plan(self.stations, self.assets)
        blocked_text = "；".join(b for c in plan.blocked for b in c.blockers)
        self.assertIn("速度台调校中", blocked_text)

    def test_r8_idle_loan_can_be_returned(self):
        """B 头对照前提不成立，提示可归还对照台。"""
        plan = make_case("R8").plan(self.stations, self.assets)
        self.assertTrue(any("归还" in w for w in plan.warnings))

    def test_question_only_when_it_changes_next_step(self):
        """站点做不了、记录里也没有、答案会改变下一步时才追问。"""
        case = CaseFile.from_observations("Q", [
            obs("Q", SourceKind.USER, "原机头", "低档", Finding.SOUNDING,
                load="装杯", oid="U#1"),
            obs("Q", SourceKind.USER, "原机头", "低档", Finding.SILENT,
                load="空转", oid="U#2"),
        ], MANUAL)
        # 站点没有清水，装杯观察做不了；高档表现用户也没提过
        poor_stations = [Station.from_row({
            "id": "S9", "station": "空台",
            "availability": "无清水，无空杯", "handoff": "",
        }, CHECK_NAMES)]
        plan = case.plan(poor_stations, AssetPool.from_rows([]))
        self.assertEqual(plan.decision, "先问")
        self.assertTrue(plan.questions)
        self.assertIn("高档", plan.questions[0])


class AssetTest(unittest.TestCase):
    def test_same_set_only_lent_to_one_open_repair(self):
        pool = make_assets()
        with self.assertRaisesRegex(RuntimeError, "只能托给一个"):
            pool.book("B头", "R7")  # R8 尚未归还
        pool.release("B头", "R8")
        pool.book("B头", "R7")  # 归还后才能托给下一案
        self.assertEqual(pool.get("B头").holder, "R7")

    def test_consumed_config_parsed_from_retained_state(self):
        pool = make_assets()
        self.assertEqual(pool.consumed_configs("B头"), ["备用头"])
        self.assertEqual(pool.consumed_configs("备机身"), [])


class StatusTest(unittest.TestCase):
    """用户凭接件编号查询。"""

    @classmethod
    def setUpClass(cls):
        cls.stations = make_stations()
        cls.assets = make_assets()

    def test_r7_status(self):
        case = make_case("R7")
        status = case.status(case.plan(self.stations, self.assets))
        self.assertEqual(status.next_station, "前台兼观察台")
        pending = "\n".join(status.pending)
        self.assertIn("高档", pending)
        self.assertIn("未试", pending)
        self.assertIn("差异保留", pending)  # 口述与复测的差异不消失
        self.assertIn("只听转述", pending)
        confirmed = "\n".join(status.confirmed)
        self.assertIn("装杯", confirmed)
        self.assertTrue(any("签收不代表故障已确认" in n for n in status.notes))

    def test_r8_status(self):
        case = make_case("R8")
        status = case.status(case.plan(self.stations, self.assets))
        self.assertEqual(status.next_station, "精修台")
        confirmed = "\n".join(status.confirmed)
        self.assertIn("另一台机身", confirmed)
        pending = "\n".join(status.pending)
        self.assertIn("轴座检查", pending)      # 成立路径上未完成的检查
        self.assertIn("速度台调校中", pending)  # 被阻塞的检查也列出


class ResponsibilityTest(unittest.TestCase):
    """并存症状各自保留未完成的检修责任；完成的检查留原证据。"""

    def make_two_symptom_case(self):
        return CaseFile.from_observations("M", [
            obs("M", SourceKind.USER, "原机头", "低档", Finding.SOUNDING,
                load="装杯", oid="U#1"),
            obs("M", SourceKind.USER, "原机头", "低档", Finding.SILENT,
                load="空转", oid="U#2"),
            obs("M", SourceKind.USER, "备用头", "低档", Finding.SOUNDING,
                load="装杯", oid="U#3"),
            obs("M", SourceKind.TECH, "原机头", "高档", Finding.SOUNDING,
                load="清水", oid="T#1"),
        ], MANUAL)

    def test_multiple_symptoms_keep_own_responsibilities(self):
        case = self.make_two_symptom_case()
        symptoms = {s.label(): s for s in case.symptoms()}
        self.assertGreaterEqual(len(symptoms), 2)
        before = case.open_responsibilities()
        # 完成低档装杯症状的配合检查，另一症状的责任不消失
        case.complete_check("H1.load", "S1 观察台记录 2026-11-24")
        after = case.open_responsibilities()
        self.assertTrue(any("已完成，证据留存" in item
                            for items in after.values() for item in items))
        high_gear = [label for label in after if "高档" in label]
        self.assertTrue(high_gear)
        # 证据留档：观察记录不因检查完成或换担当而消失
        self.assertEqual(len(case.observations), 4)

    def test_completed_check_keeps_evidence_and_leaves_plan(self):
        case = make_case("R7")
        case.complete_check("H1.load", "观察台记录")
        plan = case.plan(make_stations(), make_assets())
        planned_names = [c.name for c in plan.do_now + plan.blocked]
        self.assertNotIn("真实装杯载荷配合检查", planned_names)
        self.assertIn("H1.load", case.completed_checks)  # 证据仍在


if __name__ == "__main__":
    unittest.main()
