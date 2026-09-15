#!/usr/bin/env python3
"""护理工单演示数据种子（/work-orders 主从页 2026-08-25 改版配套）。

近 14 天（含今天）× 每天 24-34 单，确定性随机（seed 固定，重跑结果一致）。
完成率口径：更早的历史日 88-97%、昨天 ~90%、今天 ~62%（留足"待处理"演示）。
staff_name 严格取老人所在楼栋的护理员（对齐员工台账映射），note 按工单类型模板。

幂等方式：表上只有 PK、无 (resident_id,date,type) 唯一索引，不能用 ON CONFLICT，
先 DELETE date >= CURRENT_DATE - 13 再 INSERT。写入走 docker exec psql，
不改动 03-nursing-seed.sql（那是首装基线，动态演示数据由本脚本维护）。

用法：python3 scripts/seed_work_orders_demo.py [--dry-run] [--lang zh|en]

--lang en（P5 广交会英文演示，默认 zh）：type/note 英文化，staff_name 用
ERP --lang en 重灌后的员工英文名（与员工台账映射保持一致）。随机分布与
zh 完全一致（zh 键生成、事后查表翻译）——两种语言重跑结果可逐行对齐。
楼栋键（1号楼…）只是脚本内部给老人配护理员的映射键：nursing_work_orders
表本身无楼栋列；PG 三表（users/residents/schedules）的 building/floor
枚举值双语切换由 seed_pg_demo_en.py 负责（与本脚本同 --lang 跑即可）。
"""

from __future__ import annotations

import random
import subprocess
import sys
from datetime import date, timedelta

SEED = 20260825
DAYS = 14  # 含今天


def _parse_lang() -> str:
    if "--lang" in sys.argv:
        i = sys.argv.index("--lang")
        val = sys.argv[i + 1] if i + 1 < len(sys.argv) else "zh"
    else:
        val = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--lang=")), "zh")
    if val not in ("zh", "en"):
        sys.exit(f"--lang 只支持 zh|en，收到 {val!r}")
    return val


LANG = _parse_lang()

# 楼栋 → 护理员（与员工台账 nursing_users / Employee 一致；en 名与 ERP
# rebuild_demo_data.py NAME_ZH_EN 保持同步——员工台账英文重灌后同名同拼）
STAFF = {
    "1号楼": ["侯玉芬", "刘小梅", "李芳", "王强", "王组长", "李护士"],
    "2号楼": ["张敏", "赵丽华", "陈建国", "陈组长", "王护士"],
    "3号楼": ["吴秀丽", "周玉英", "孙志明", "赵小明"],
    "4号楼": ["冯德才", "杨桂兰", "郑文斌", "孙组长"],
    "5号楼": ["潘丽丽", "钱玉兰", "韩立明", "钱小红"],
    "6号楼": ["姚士杰", "方永刚", "蒋秀兰", "黄组长"],
}
STAFF_EN = {
    "侯玉芬": "Hou Yufen", "刘小梅": "Liu Xiaomei", "李芳": "Li Fang",
    "王强": "Wang Qiang", "王组长": "Team Lead Wang", "李护士": "Nurse Li",
    "张敏": "Zhang Min", "赵丽华": "Zhao Lihua", "陈建国": "Chen Jianguo",
    "陈组长": "Team Lead Chen", "王护士": "Nurse Wang",
    "吴秀丽": "Wu Xiuli", "周玉英": "Zhou Yuying", "孙志明": "Sun Zhiming",
    "赵小明": "Zhao Xiaoming",
    "冯德才": "Feng Decai", "杨桂兰": "Yang Guilan", "郑文斌": "Zheng Wenbin",
    "孙组长": "Team Lead Sun",
    "潘丽丽": "Pan Lili", "钱玉兰": "Qian Yulan", "韩立明": "Han Liming",
    "钱小红": "Qian Xiaohong",
    "姚士杰": "Yao Shijie", "方永刚": "Fang Yonggang", "蒋秀兰": "Jiang Xiulan",
    "黄组长": "Team Lead Huang",
}

# 老人 id → 楼栋（R001-R006=1号楼 … R031-R036=6号楼）
RESIDENTS = [(f"R{i:03d}", f"{(i - 1) // 6 + 1}号楼") for i in range(1, 37)]

# 24 种工单类型 → 记录模板（演示口径：数据可以假，措辞必须像真的）
NOTES = {
    "出入量记录": ["入量 850ml，出量 700ml，量正常", "入量 600ml，出量偏少，已报告值班护士"],
    "协助排便": ["顺利排便一次，无不适", "使用开塞露后排便，肛周皮肤完好"],
    "口腔护理": ["口腔清洁完毕，黏膜无破损", "义齿取出清洗，口腔湿润无异味"],
    "吸氧": ["持续低流量吸氧 2 小时，血氧 96%", "吸氧 1 小时后血氧回升至 95%，自述气顺"],
    "喂饭协助": ["半流食一碗，进食顺利，无呛咳", "喂食约二十分钟，进食过半，已记录入量"],
    "夜尿协助": ["起夜 2 次，协助如厕，无跌倒风险", "夜间更换纸尿裤一次，后半夜安睡"],
    "康复理疗": ["中频理疗 20 分钟，配合良好", "红外线照疗 15 分钟，局部皮肤无异常"],
    "康复训练": ["下肢肌力训练 30 分钟，步态稳定", "床边坐立平衡训练，可自主保持 10 分钟"],
    "心电图复查": ["窦性心律，未见明显异常", "复查心电图已送医务科阅图，待回报"],
    "心电监护": ["监护 4 小时，心率血压平稳", "监护期间偶发房早 3 次，已告知值班医生"],
    "情绪安抚": ["想念家人情绪低落，已电话联系子女，情绪好转",
               "因室友鼾声休息不好，已调换活动室午休"],
    "换药": ["伤口换药完毕，敷料干燥，无渗液", "骶尾部换药，创面较昨日缩小，肉芽新鲜"],
    "沟通训练": ["词汇表达练习 20 分钟，吐字较昨日清晰", "识字卡片练习，能完成简单句子复述"],
    "洗澡协助": ["洗浴完毕，皮肤清洁，注意防滑", "床浴擦洗全身，换纯棉衣物，皮肤干燥无汗"],
    "理疗": ["颈肩推拿 30 分钟，自述酸痛缓解", "艾灸腰部 20 分钟，注意观察皮肤温度"],
    "纸尿裤更换": ["更换纸尿裤，皮肤清洁干燥，无红疹", "更换纸尿裤并涂护臀膏，腹股沟轻微泛红"],
    "翻身护理": ["每 2 小时翻身一次，皮肤完好无压痕", "翻身并拍背排痰，左侧卧位 30 分钟"],
    "药物调整": ["遵医嘱调整降压药剂量，服药后血压 135/85",
               "餐前降糖药已按新医嘱执行，餐后血糖 7.8"],
    "营养评估": ["本周进食良好，体重持平，营养评分正常", "进食量下降，营养师会诊已预约"],
    "血压测量": ["血压 138/86 mmHg，脉搏 76 次/分", "晨起血压 152/94 偏高，复测后已报告医生"],
    "进食鼓励": ["自主进食约半碗，已鼓励加餐", "食欲欠佳，予酸奶一杯，家属送来喜欢的小菜"],
    "防走失巡查": ["楼层巡查 3 次，重点老人均在视野内",
                "午后一度独自走向电梯间，已耐心劝返并告知家属"],
    "陪同活动": ["参加楼栋手工活动 40 分钟，情绪愉快", "院子里散步两圈，与同伴聊天，状态放松"],
    "鼻饲": ["鼻饲流食 300ml，温度适宜，无返流", "鼻饲前后温水冲管，胃管固定良好，刻度无移位"],
}
TYPES = list(NOTES)

# ── P5 en 串表：type/note 英文（键保持 zh，与 NOTES 逐条对齐）──────────
TYPE_EN = {
    "出入量记录": "Intake/Output Record", "协助排便": "Bowel Assistance",
    "口腔护理": "Oral Care", "吸氧": "Oxygen Therapy",
    "喂饭协助": "Feeding Assistance", "夜尿协助": "Night Toileting",
    "康复理疗": "Rehab Physiotherapy", "康复训练": "Rehab Training",
    "心电图复查": "ECG Follow-up", "心电监护": "ECG Monitoring",
    "情绪安抚": "Emotional Comfort", "换药": "Dressing Change",
    "沟通训练": "Communication Training", "洗澡协助": "Bathing Assistance",
    "理疗": "Physiotherapy", "纸尿裤更换": "Diaper Change",
    "翻身护理": "Repositioning", "药物调整": "Medication Adjustment",
    "营养评估": "Nutrition Assessment", "血压测量": "BP Measurement",
    "进食鼓励": "Eating Encouragement", "防走失巡查": "Anti-wandering Patrol",
    "陪同活动": "Activity Escort", "鼻饲": "NG Tube Feeding",
}
NOTES_EN = {  # 与 NOTES 同键；内层列表与 zh 版逐条对齐
    "出入量记录": ["Intake 850 ml, output 700 ml; within normal range",
                "Intake 600 ml, output on the low side; reported to the duty nurse"],
    "协助排便": ["Bowel movement passed smoothly, no discomfort",
              "Bowel movement after glycerin suppository; perianal skin intact"],
    "口腔护理": ["Mouth cleaned; oral mucosa intact",
              "Dentures removed and cleaned; mouth moist, no odor"],
    "吸氧": ["Low-flow oxygen for 2 hours; SpO2 96%",
           "SpO2 back up to 95% after 1 hour of oxygen; breathing easier"],
    "喂饭协助": ["Semi-liquid meal, one bowl; ate smoothly, no choking",
              "Fed for about 20 minutes; ate more than half, intake recorded"],
    "夜尿协助": ["Up twice at night, assisted to the toilet; no fall risk",
              "Diaper changed once at night; slept soundly afterwards"],
    "康复理疗": ["Medium-frequency therapy 20 min; good cooperation",
              "Infrared therapy 15 min; local skin normal"],
    "康复训练": ["Lower-limb strength training 30 min; steady gait",
              "Bedside sitting-balance training; held for 10 minutes unaided"],
    "心电图复查": ["Sinus rhythm, no obvious abnormality",
                "Repeat ECG sent to the medical office for review"],
    "心电监护": ["Monitored 4 hours; heart rate and BP stable",
              "3 occasional PACs during monitoring; duty doctor informed"],
    "情绪安抚": ["Missing family and feeling low; children phoned, mood improved",
              "Poor rest due to roommate's snoring; moved to activity room for the nap"],
    "换药": ["Dressing changed; dressing dry, no exudate",
           "Sacral dressing changed; wound smaller than yesterday, healthy granulation"],
    "沟通训练": ["Word-expression practice 20 min; clearer speech than yesterday",
              "Word-card practice; able to repeat simple sentences"],
    "洗澡协助": ["Bath finished; skin clean, slip precautions taken",
              "Bed bath and full-body wipe; changed into cotton clothes, skin dry"],
    "理疗": ["Neck-shoulder massage 30 min; soreness relieved",
           "Moxibustion on the lower back 20 min; watching skin temperature"],
    "纸尿裤更换": ["Diaper changed; skin clean and dry, no rash",
               "Diaper changed and barrier cream applied; slight groin redness"],
    "翻身护理": ["Turned every 2 hours; skin intact, no pressure marks",
              "Turned with back percussion for phlegm; left lateral 30 min"],
    "药物调整": ["Antihypertensive dose adjusted per orders; BP 135/85 after dosing",
              "Pre-meal hypoglycemic given per new orders; post-meal glucose 7.8"],
    "营养评估": ["Good intake this week, weight stable; nutrition score normal",
              "Intake declining; dietitian consultation scheduled"],
    "血压测量": ["BP 138/86 mmHg, pulse 76 bpm",
              "Morning BP 152/94, on the high side; rechecked and reported"],
    "进食鼓励": ["Ate about half a bowl by herself; snack encouraged",
              "Poor appetite; given a yogurt and the favorite dish the family brought"],
    "防走失巡查": ["Floor patrolled 3 times; key residents all in sight",
               "Walked toward the elevator alone in the afternoon; patiently led back, family informed"],
    "陪同活动": ["Joined the building handcraft session 40 min; in good spirits",
             "Two laps in the garden chatting with peers; relaxed"],
    "鼻饲": ["NG feeding 300 ml at proper temperature; no reflux",
           "Tube flushed with warm water before/after feeding; tube secure, markings unchanged"],
}


def translate_rows(rows: list[tuple]) -> list[tuple]:
    """zh 行 → en 行（type/note/staff 查表；rid/日期/完成态原样）。"""
    note_map = {zh: en for k in NOTES for zh, en in zip(NOTES[k], NOTES_EN[k])}
    return [
        (rid, d, TYPE_EN[otype], done, STAFF_EN[staff], note_map[note])
        for rid, d, otype, done, staff, note in rows
    ]


def _lit(v: object) -> str:
    """SQL 字面量。字符串一律单引号+'' 转义——repr() 遇撇号会改用双引号，
    psql 会把里面的 :word 当主变量替换（en 备注 "roommate's" 实测炸出）。"""
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def day_rate(days_ago: int, rng: random.Random) -> float:
    """按日期远近给完成率：更早 88-97%、昨天 ~90%、今天 ~62%。"""
    if days_ago == 0:
        return 0.62
    if days_ago == 1:
        return 0.90
    return rng.uniform(0.88, 0.97)


def build_rows(today: date) -> list[tuple]:
    rng = random.Random(SEED)
    rows: list[tuple] = []
    # (date, resident_id, type) 去重：rng.choice 组合会重样（每日 ~30 单抽 10 类型
    # 的生日碰撞），旧库无唯一索引时静默落成重复行；08-31 补建
    # uq_work_orders_res_date_type 后 09-03 重灌即 INSERT 失败。
    seen: set[tuple[str, str, str]] = set()
    for days_ago in range(DAYS - 1, -1, -1):
        d = today - timedelta(days=days_ago)
        n = rng.randint(24, 34)
        rate = day_rate(days_ago, rng)
        for _ in range(n):
            rid, building = rng.choice(RESIDENTS)
            otype = rng.choice(TYPES)
            key = (d.isoformat(), rid, otype)
            if key in seen:
                continue
            seen.add(key)
            rows.append((
                rid,
                d.isoformat(),
                otype,
                rng.random() < rate,
                rng.choice(STAFF[building]),
                rng.choice(NOTES[otype]),
            ))
    return rows


def main() -> None:
    dry = "--dry-run" in sys.argv
    today = date.today()
    rows = build_rows(today)
    if LANG == "en":
        rows = translate_rows(rows)

    done = sum(1 for r in rows if r[3])
    per_day: dict[str, int] = {}
    for r in rows:
        per_day[r[1]] = per_day.get(r[1], 0) + 1
    print(f"[lang={LANG}] 生成 {len(rows)} 单 / {len(per_day)} 天，完成 {done}（{done * 100 // len(rows)}%）")
    for d in sorted(per_day):
        dd = sum(1 for r in rows if r[1] == d and r[3])
        print(f"  {d}: {per_day[d]} 单，完成 {dd}")
    if LANG == "en":
        print("  en 样例（前 3 单）：")
        for r in rows[:3]:
            print(f"    {r}")

    if dry:
        # dry-run 下也把 SQL 生成一遍并展示头部，验证参数拼装（不写库）
        values = ",\n  ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in rows)
        oldest = (today - timedelta(days=DAYS - 1)).isoformat()
        sql = (
            f"DELETE FROM nursing_work_orders WHERE date >= '{oldest}';\n"
            "INSERT INTO nursing_work_orders"
            " (resident_id, date, type, completed, staff_name, note) VALUES\n"
            f"  {values};\n"
        )
        print(f"  [dry-run] SQL 已生成（{len(sql)} 字符），头部预览：")
        print("  " + "\n  ".join(sql.splitlines()[:4]))
        return

    values = ",\n  ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in rows)
    # DELETE 与 INSERT 同用宿主机日期字面量：避免容器时区跨日时 CURRENT_DATE
    # 比 host 落后一天、删不干净上轮残留（表无唯一索引，重复行无从合并）。
    oldest = (today - timedelta(days=DAYS - 1)).isoformat()
    sql = (
        f"DELETE FROM nursing_work_orders WHERE date >= '{oldest}';\n"
        "INSERT INTO nursing_work_orders"
        " (resident_id, date, type, completed, staff_name, note) VALUES\n"
        f"  {values};\n"
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", "dato-postgres", "psql", "-U", "dato", "-d", "dato",
         "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        sys.exit(f"psql 失败：{proc.stderr[:500]}")
    print(proc.stdout.strip())


if __name__ == "__main__":
    main()
