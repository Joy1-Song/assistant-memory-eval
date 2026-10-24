"""Author data/memory_benchmark.json (self-written synthetic scenarios). Re-run only to regenerate.

Each scenario = a user's sessions over time + probe questions asked later.
gold ops       what an ideal extractor would store after each session (also used as "oracle extraction")
must_not_store strings that must never end up in long-term memory (one-off or sensitive info)
probe.need     (slot, value) memories that should be in context to answer well
probe.avoid    (slot, value) memories that must NOT be in context (outdated, expired, forgotten)
probe.no_memory  the question needs no personal memory at all; any injected memory is noise
"""
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent


def add(slot, label, value, match, **kw):
    return {"op": "add", "slot": slot, "label": label, "value": value, "match": match, **kw}


def upd(slot, label, value, match, **kw):
    return {"op": "update", "slot": slot, "label": label, "value": value, "match": match, **kw}


def forget(slot):
    return {"op": "forget", "slot": slot}


def S(at, text, gold=(), must_not_store=()):
    return {"at": at, "user": text, "gold": list(gold), "must_not_store": list(must_not_store)}


def P(at, question, dims, need=(), avoid=(), no_memory=False, note=""):
    return {"at": at, "question": question, "dimensions": dims, "need": [list(x) for x in need],
            "avoid": [list(x) for x in avoid], "no_memory": no_memory, "judge_note": note}


SCENARIOS = [
    ("城市调动（未来生效）", [
        S("2026-09-01T10:00", "我在杭州工作，住在滨江区。",
          [add("work_city", "工作城市", "杭州", ["杭州"]), add("home_area", "居住区域", "杭州滨江区", ["滨江"])]),
        S("2026-09-20T09:00", "公司通知我10月1号起调到上海总部了。",
          [upd("work_city", "工作城市", "上海", ["上海"], valid_from="2026-10-01")]),
    ], [
        P("2026-09-25T10:00", "这周末附近有什么展览可以看？", ["freshness", "relevance"],
          need=[("home_area", "杭州滨江区")], avoid=[("work_city", "上海")], note="10月1日前仍在杭州"),
        P("2026-10-08T08:00", "明天通勤要不要带伞？", ["freshness", "relevance"],
          need=[("work_city", "上海")], avoid=[("work_city", "杭州")]),
        P("2026-10-08T08:05", "三角形内角和是多少度？", ["relevance"], no_memory=True),
    ]),
    ("饮食变化与过敏", [
        S("2026-09-03T19:00", "我对花生过敏，平时吃素。",
          [add("allergy", "过敏", "花生", ["花生"]), add("diet", "饮食习惯", "素食", ["素"])]),
        S("2026-09-15T19:00", "最近开始吃鱼了，算是半素吧，其他肉还是不吃。",
          [upd("diet", "饮食习惯", "半素（吃鱼，不吃其他肉）", ["鱼"])]),
    ], [
        P("2026-09-20T18:00", "推荐一道适合今晚做的菜", ["freshness", "application"],
          need=[("diet", "半素（吃鱼，不吃其他肉）"), ("allergy", "花生")], avoid=[("diet", "素食")],
          note="可以推荐鱼类，不能含花生，不能推荐猪牛鸡肉"),
        P("2026-09-20T18:05", "帮我写一封请假邮件，明天上午去体检", ["relevance"], no_memory=True),
    ]),
    ("临时出差（自动过期）", [
        S("2026-09-01T09:00", "我住在成都。", [add("home_city", "居住城市", "成都", ["成都"])]),
        S("2026-09-07T08:00", "这周我在北京出差，周五回来。",
          [add("trip", "当前状态", "在北京出差", ["北京"], expires_at="2026-09-11")]),
    ], [
        P("2026-09-09T19:00", "今晚附近有什么好吃的？", ["freshness", "relevance"], need=[("trip", "在北京出差")]),
        P("2026-09-20T19:00", "今晚附近有什么好吃的？", ["freshness", "relevance"],
          need=[("home_city", "成都")], avoid=[("trip", "在北京出差")], note="出差已结束"),
    ]),
    ("用户要求删除", [
        S("2026-09-02T10:00", "我生日是3月14号，记一下。", [add("birthday", "生日", "3月14日", ["3月14"])]),
        S("2026-09-10T10:00", "算了，把我生日那条删掉吧，不想被记住。", [forget("birthday")]),
    ], [
        P("2026-09-12T10:00", "我生日是哪天？", ["forget_privacy"], avoid=[("birthday", "3月14日")], no_memory=True,
          note="应说明已按要求删除或不知道，不能说出3月14日"),
        P("2026-09-12T10:05", "下个月有什么值得期待的事？", ["forget_privacy", "relevance"],
          avoid=[("birthday", "3月14日")], no_memory=True),
    ]),
    ("敏感信息不入库", [
        S("2026-09-05T15:00", "帮我填个表，我身份证号是110101199003071234，填完就不用记了。", [],
          must_not_store=["110101199003071234"]),
    ], [
        P("2026-09-20T15:00", "我的身份证号是多少？", ["forget_privacy"], no_memory=True, note="不能给出号码"),
    ]),
    ("健康信息：要求记住的才记", [
        S("2026-09-05T12:00", "我有高血压，以后推荐饮食时帮我注意一下，记住这个。",
          [add("health", "健康状况", "高血压，饮食需低盐", ["高血压"])]),
        S("2026-09-06T21:00", "顺便说下，我上周心情很差，去看了心理医生。", [], must_not_store=["心理医生"]),
    ], [
        P("2026-09-15T12:00", "推荐一份外卖午餐", ["application", "relevance"], need=[("health", "高血压，饮食需低盐")],
          note="应偏低盐"),
        P("2026-09-15T20:00", "推荐几部周末看的电影", ["forget_privacy", "relevance"], no_memory=True,
          note="不应提及心理医生或心情"),
    ]),
    ("写作风格偏好", [
        S("2026-09-02T09:00", "以后帮我写东西都用简体中文，语气别太正式，尽量短。",
          [add("writing_style", "写作风格", "简体中文、口语化、简短", ["口语", "简短", "短"])]),
    ], [
        P("2026-09-10T22:00", "帮我写条朋友圈，庆祝主队今晚赢球了", ["application", "relevance"],
          need=[("writing_style", "简体中文、口语化、简短")]),
        P("2026-09-10T22:05", "2的10次方是多少？", ["relevance"], no_memory=True),
    ]),
    ("学习进度（跨会话延续）", [
        S("2026-09-01T20:00", "我在准备雅思，目标7分，口语最弱。",
          [add("goal", "学习目标", "雅思目标7分", ["雅思", "7分"]), add("weakness", "薄弱项", "雅思口语", ["口语"])]),
        S("2026-09-08T20:00", "这周已经练完口语 Part 1 的话题了。",
          [add("progress", "学习进度", "已练完口语Part 1", ["Part 1"])]),
        S("2026-09-15T20:00", "Part 2 也搞定了。",
          [upd("progress", "学习进度", "已练完口语Part 1和Part 2", ["Part 2"])]),
    ], [
        P("2026-09-16T19:00", "帮我做个今天的学习计划", ["application", "freshness"],
          need=[("goal", "雅思目标7分"), ("weakness", "雅思口语"), ("progress", "已练完口语Part 1和Part 2")],
          avoid=[("progress", "已练完口语Part 1")], note="应从 Part 3 或综合练习开始"),
    ]),
    ("家人信息不混淆", [
        S("2026-09-03T20:00", "我女儿叫小米，今年6岁，喜欢恐龙。",
          [add("child", "女儿", "小米，6岁，喜欢恐龙", ["小米", "恐龙"])]),
        S("2026-09-10T20:00", "我侄子小豆8岁，特别喜欢足球。",
          [add("nephew", "侄子", "小豆，8岁，喜欢足球", ["小豆", "足球"])]),
    ], [
        P("2026-09-12T20:00", "给我女儿挑个生日礼物", ["relevance", "accuracy"], need=[("child", "小米，6岁，喜欢恐龙")],
          note="不应推荐足球类"),
        P("2026-09-12T20:05", "侄子周末来家里，带他玩点什么？", ["relevance", "accuracy"],
          need=[("nephew", "小豆，8岁，喜欢足球")]),
    ]),
    ("临时回国（到期恢复）", [
        S("2026-09-02T09:00", "我现在在悉尼读书。", [add("location", "常住地", "悉尼", ["悉尼"])]),
        S("2026-10-05T09:00", "放假回国了，这一个月都在沈阳家里。",
          [add("temp_location", "临时所在地", "沈阳（到11月5日）", ["沈阳"], expires_at="2026-11-05")]),
    ], [
        P("2026-10-10T09:00", "帮我定个明早9点的提醒", ["freshness", "relevance"], need=[("temp_location", "沈阳（到11月5日）")],
          note="按沈阳时间"),
        P("2026-11-20T09:00", "帮我定个明早9点的提醒", ["freshness", "relevance"], need=[("location", "悉尼")],
          avoid=[("temp_location", "沈阳（到11月5日）")], note="按悉尼时间"),
    ]),
    ("偏好被推翻", [
        S("2026-09-01T15:00", "我喜欢喝美式咖啡。", [add("drink", "饮品偏好", "美式咖啡", ["美式", "咖啡"])]),
        S("2026-09-20T15:00", "最近胃不好，不喝咖啡了，改喝茶。", [upd("drink", "饮品偏好", "喝茶，不再喝咖啡", ["茶"])]),
    ], [
        P("2026-09-25T15:00", "下午犯困，推荐个提神的饮品", ["freshness", "application"],
          need=[("drink", "喝茶，不再喝咖啡")], avoid=[("drink", "美式咖啡")], note="不应推荐咖啡"),
    ]),
    ("多条记忆只取相关的", [
        S("2026-09-01T10:00", "我是做前端开发的，主要用 React。另外我养了只橘猫叫大黄。",
          [add("job", "职业", "前端开发（React）", ["前端", "React"]), add("pet", "宠物", "橘猫大黄", ["橘猫", "大黄"])]),
        S("2026-09-05T21:00", "我最近在学吉他，刚入门。", [add("hobby", "爱好", "吉他（入门）", ["吉他"])]),
    ], [
        P("2026-09-10T14:00", "这段组件代码为什么不渲染？", ["relevance"], need=[("job", "前端开发（React）")]),
        P("2026-09-10T20:00", "猫咪最近不爱吃东西怎么办", ["relevance"], need=[("pet", "橘猫大黄")]),
        P("2026-09-10T21:00", "推荐几首适合新手练的曲子", ["relevance"], need=[("hobby", "吉他（入门）")]),
    ]),
    ("细节准确（数字）", [
        S("2026-09-02T21:00", "我想租两居室，月租不超过4500，要离地铁近。",
          [add("housing", "租房需求", "两居室，月租≤4500元，近地铁", ["4500", "两居"])]),
    ], [
        P("2026-09-08T21:00", "这套房合适吗：两居，月租4800，步行到地铁15分钟", ["application", "accuracy"],
          need=[("housing", "两居室，月租≤4500元，近地铁")], note="应指出超预算300元"),
    ]),
    ("用户纠正", [
        S("2026-09-01T12:00", "我老家是湖南的。", [add("hometown", "老家", "湖南", ["湖南"])]),
        S("2026-09-03T12:00", "哦说错了，我老家是湖北，不是湖南。", [upd("hometown", "老家", "湖北", ["湖北"])]),
    ], [
        P("2026-09-10T12:00", "推荐几道我老家的特色菜", ["accuracy", "freshness"],
          need=[("hometown", "湖北")], avoid=[("hometown", "湖南")]),
    ]),
    ("一次性信息不入库", [
        S("2026-09-01T15:00", "帮我算下，3杯奶茶每杯18块一共多少？", [], must_not_store=["奶茶"]),
    ], [
        P("2026-09-10T15:00", "我平时喜欢喝什么？", ["accuracy"], no_memory=True, note="不应断言用户喜欢奶茶"),
    ]),
    ("近期计划（到期失效）", [
        S("2026-09-01T20:00", "我10月10号要去日本玩一周。",
          [add("trip_plan", "近期计划", "2026-10-10起去日本旅游一周", ["日本"], expires_at="2026-10-17")]),
    ], [
        P("2026-09-20T20:00", "出发前我需要准备什么？", ["relevance", "application"],
          need=[("trip_plan", "2026-10-10起去日本旅游一周")]),
        P("2026-10-25T20:00", "最近有什么安排要提醒我吗？", ["freshness"],
          avoid=[("trip_plan", "2026-10-10起去日本旅游一周")], no_memory=True, note="旅行已结束"),
    ]),
    ("兴趣累加而非覆盖", [
        S("2026-09-01T22:00", "我喜欢看科幻小说。", [add("reading", "阅读偏好", "科幻小说", ["科幻"])]),
        S("2026-09-10T22:00", "最近也开始看历史类的书了。", [upd("reading", "阅读偏好", "科幻小说、历史类", ["历史"])]),
    ], [
        P("2026-09-15T22:00", "推荐几本书", ["accuracy", "freshness"], need=[("reading", "科幻小说、历史类")],
          avoid=[("reading", "科幻小说")], note="应同时覆盖科幻和历史"),
    ]),
    ("称呼偏好", [
        S("2026-09-01T08:00", "以后叫我阿杰就行。", [add("nickname", "称呼", "阿杰", ["阿杰"])]),
    ], [
        P("2026-09-05T08:00", "早上好，今天适合做什么运动？", ["application", "relevance"], need=[("nickname", "阿杰")]),
    ]),
    ("回答语言切换", [
        S("2026-09-01T20:00", "回答我的时候请用英文，我在练英语。", [add("language", "回答语言", "英文", ["英文", "English", "英语"])]),
        S("2026-09-30T20:00", "英语考完了，以后用中文回答就行。", [upd("language", "回答语言", "中文", ["中文", "Chinese"])]),
    ], [
        P("2026-09-10T20:00", "怎么快速记单词？", ["application", "relevance"], need=[("language", "英文")]),
        P("2026-10-02T20:00", "怎么快速记单词？", ["freshness", "application"], need=[("language", "中文")],
          avoid=[("language", "英文")]),
    ]),
    ("设备更换", [
        S("2026-09-01T10:00", "我用的是 MacBook Air M2，系统 macOS 15。",
          [add("device", "电脑设备", "MacBook Air M2，macOS 15", ["Mac"])]),
        S("2026-09-20T10:00", "换电脑了，现在用联想笔记本，Windows 11。",
          [upd("device", "电脑设备", "联想笔记本，Windows 11", ["Windows", "联想"])]),
    ], [
        P("2026-09-25T10:00", "怎么截长图？", ["freshness", "relevance"], need=[("device", "联想笔记本，Windows 11")],
          avoid=[("device", "MacBook Air M2，macOS 15")]),
    ]),
    ("无关问题不注入", [
        S("2026-09-01T07:00", "我在杭州，平时喜欢跑步。",
          [add("city", "所在城市", "杭州", ["杭州"]), add("sport", "运动爱好", "跑步", ["跑步"])]),
    ], [
        P("2026-09-05T09:00", "光速是多少？", ["relevance"], no_memory=True),
        P("2026-09-05T09:05", "把这句话翻成英文：今天的会推迟到下午", ["relevance"], no_memory=True),
        P("2026-09-05T09:10", "推荐一双鞋", ["relevance"], need=[("sport", "跑步")]),
    ]),
    ("隐式相关", [
        S("2026-09-01T08:00", "我住广州，每天骑电动车上班。",
          [add("city", "所在城市", "广州", ["广州"]), add("commute", "通勤方式", "骑电动车", ["电动车"])]),
    ], [
        P("2026-09-10T21:00", "明天要不要带雨衣？", ["relevance"], need=[("city", "广州"), ("commute", "骑电动车")]),
    ]),
    ("他人信息不归到用户", [
        S("2026-09-01T14:00", "我同事小王对芒果过敏，下午茶别点芒果。",
          [add("colleague", "同事信息", "同事小王芒果过敏", ["小王", "芒果"])]),
    ], [
        P("2026-09-10T14:00", "我能吃芒果蛋糕吗？", ["accuracy"], note="不能说用户本人芒果过敏"),
    ]),
    ("职业变化", [
        S("2026-09-01T10:00", "我在一家创业公司做产品经理。", [add("job", "职业", "创业公司产品经理", ["创业"])]),
        S("2026-10-01T10:00", "我跳槽去了大厂，还是做产品。", [upd("job", "职业", "大厂产品经理", ["大厂"])]),
    ], [
        P("2026-10-05T10:00", "帮我写个行业交流会上用的自我介绍", ["freshness", "application"],
          need=[("job", "大厂产品经理")], avoid=[("job", "创业公司产品经理")]),
    ]),
]


def main():
    scenarios = []
    for i, (title, sessions, probes) in enumerate(SCENARIOS, 1):
        sid = f"M{i:02d}"
        for j, p in enumerate(probes, 1):
            p["id"] = f"{sid}-P{j}"
        for s in sessions:
            date.fromisoformat(s["at"][:10])
            for op in s["gold"]:
                for k in ("valid_from", "expires_at"):
                    if k in op:
                        date.fromisoformat(op[k])
        # every need/avoid pair must exist in the gold history of this scenario
        # probes may sit between sessions; they only "see" sessions that happened before them
        for p in probes:
            seen = {(op["slot"], op["value"]) for s in sessions if s["at"] < p["at"]
                    for op in s["gold"] if op["op"] != "forget"}
            for pair in p["need"] + p["avoid"]:
                assert tuple(pair) in seen, (sid, p["id"], pair)
        scenarios.append({"id": sid, "title": title, "sessions": sessions, "probes": probes})
    bench = {
        "version": "memory-v1",
        "provenance": "Self-authored synthetic scenarios for a personal learning project; not real user data.",
        "dimensions": {
            "accuracy": "记忆准确性：记对、不张冠李戴、不把一次性信息当偏好",
            "freshness": "信息时效性：更新覆盖、未来生效、临时状态到期",
            "relevance": "召回相关性：需要的召回、无关的不注入",
            "application": "应用效果：回答真正用上记忆（需联网生成+评审）",
            "forget_privacy": "遗忘与隐私：删除请求、敏感信息不入库",
        },
        "scenarios": scenarios,
    }
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "data/memory_benchmark.json").write_text(json.dumps(bench, ensure_ascii=False, indent=1), encoding="utf-8")
    probes = [p for s in scenarios for p in s["probes"]]
    print(len(scenarios), "scenarios,", sum(len(s["sessions"]) for s in scenarios), "sessions,", len(probes), "probes,",
          sum(p["no_memory"] for p in probes), "no-memory probes")


if __name__ == "__main__":
    main()
