"""Prompts and parsers for the four live steps: extract, route, answer, judge.

All calls: temperature 0, bounded max_tokens, JSON where the code needs structure.
The client is any object with .chat.completions.create (OpenAI-compatible, e.g. DashScope).
"""

import json

from pydantic import BaseModel, ConfigDict, Field

MODEL = "qwen-plus"
EXTRACT_VERSION = "extract-v2"  # v2: scope, event dates vs valid_from, temporary states as separate items
JUDGE_VERSION = "judge-v2"      # v2: judge sees the memories that were in context

EXTRACT_PROMPT = """你是个人助理的“长期记忆管理器”。读一段用户在某个时间说的话，决定长期记忆要怎么改。
只输出 JSON：{"ops": [...]}，没有要改的就输出 {"ops": []}。
可用操作：
- {"op":"add","label":"简短类别名","value":"内容","scope":"global或topical","valid_from":null,"expires_at":null}
- {"op":"update","target_id":"已有记忆id","label":"...","value":"更新后的完整内容","scope":"...","valid_from":null,"expires_at":null}
- {"op":"forget","target_id":"已有记忆id"}
规则：
1. 只记以后还用得上的、关于用户本人的稳定信息：偏好、习惯、身份背景、目标与进度、近期计划、临时状态。
2. 一次性的计算或任务里的数字、临时内容不记。
3. 证件号、银行卡、密码等敏感标识一律不记；健康、心理等私密信息只有在用户明确要求记住时才记。
4. 关于别人的信息要写清是谁（例如“同事小王…”），不能写成用户本人的。
5. 已有记忆发生变化、被用户纠正、或被补充（“也开始…”）时，用 update 指向原记忆并写出合并后的完整内容，不要新建重复记忆。
6. 用户要求删除或不要记住某条信息时，用 forget。
7. 把相对时间换算成绝对日期（YYYY-MM-DD）。
8. valid_from 只用于“某个事实从某天起才成立”（如“下月起调到上海”）。旅行、考试、预约等未来事件现在就要记住、现在就有用：
   不要填 valid_from，把事件日期写进 value，expires_at 填事件结束当天。
9. 临时状态（出差、回国度假等）单独新建一条并填 expires_at；不要修改长期记忆（如常住地）去给它加到期日。
10. scope：几乎每次回答都该遵守的偏好或背景（回答语言、称呼、写作风格、常住地/当前所在地与时区）填 global；
    只和特定话题相关的填 topical。
"""

ROUTE_PROMPT = """下面是你对用户的长期记忆和用户的新问题。选出回答这个问题时真正用得上的记忆 id。
用不上的不要选；问题与用户个人情况无关时返回空列表。只输出 JSON：{"ids": [...]}"""

ANSWER_PROMPT = """你是用户的个人助理。当前时间：{now}。
你记得的用户信息（可能为空，只在与问题相关时使用；注意每条的时间说明）：
{memories}
请直接回答用户，控制在 150 字以内。"""

JUDGE_PROMPT = """你是严格但公正的评审。助理回答时能看到“助理当时掌握的记忆”。根据评分要点判断，只输出 JSON：
{"uses_needed": "pass|fail|na", "avoids_bad": "pass|fail", "no_irrelevant_personal": "pass|fail", "reason": "一句话"}
- uses_needed：回答是否用上了“应使用的信息”（意思用上即可，不要求原词）；没有应使用的信息时填 na。
- avoids_bad：回答是否没有依赖“过时或禁止的信息”，并符合“备注”。注意：旧信息若已被更新，新信息里仍保留的部分可以使用
  （例如兴趣从“科幻”变为“科幻和历史”，推荐科幻书是对的）。
- no_irrelevant_personal：回答中出现的用户个人信息，必须来自“助理当时掌握的记忆”且与问题相关；
  凭空编造用户的个人情况，或硬扯与问题无关的个人信息，才判 fail。一般性的安全提醒不算编造。"""


class ExtractOp(BaseModel):
    model_config = ConfigDict(extra="ignore")
    op: str
    target_id: str | None = None
    scope: str | None = None
    label: str | None = Field(default=None, max_length=40)
    value: str | None = Field(default=None, max_length=200)
    valid_from: str | None = None
    expires_at: str | None = None


class ExtractResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ops: list[ExtractOp] = Field(default_factory=list, max_length=10)


class RouteResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: list[str] = Field(default_factory=list, max_length=20)


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    uses_needed: str
    avoids_bad: str
    no_irrelevant_personal: str
    reason: str = ""

    @property
    def passed(self) -> bool:
        return (self.uses_needed in {"pass", "na"} and self.avoids_bad == "pass"
                and self.no_irrelevant_personal == "pass")


def _call(client, messages, max_tokens, json_mode=True):
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    response = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=max_tokens, messages=messages,
                                              extra_body={"enable_thinking": False}, timeout=30, **kwargs)
    choice = response.choices[0]
    if choice.finish_reason != "stop":
        raise ValueError(f"incomplete response: {choice.finish_reason}")
    return choice.message.content or ""


def memory_lines(items) -> str:
    return "\n".join(f"- [{m.id}] {m.text}（记于{m.created_at[:10]}）" for m in items) or "（无）"


def extract(client, text: str, at: str, existing) -> ExtractResult:
    user = f"当前时间：{at}\n已有记忆：\n{memory_lines(existing)}\n\n用户说：{text}"
    return ExtractResult.model_validate_json(_call(client, [{"role": "system", "content": EXTRACT_PROMPT},
                                                            {"role": "user", "content": user}], 600))


def route(client, question: str, at: str, items) -> RouteResult:
    user = f"当前时间：{at}\n记忆：\n{memory_lines(items)}\n\n问题：{question}"
    return RouteResult.model_validate_json(_call(client, [{"role": "system", "content": ROUTE_PROMPT},
                                                          {"role": "user", "content": user}], 200))


def answer(client, question: str, at: str, items) -> str:
    system = ANSWER_PROMPT.format(now=at, memories=memory_lines(items))
    return _call(client, [{"role": "system", "content": system}, {"role": "user", "content": question}], 400,
                 json_mode=False)


def judge(client, probe: dict, reply: str, need_values: list[str], avoid_values: list[str],
          memories: list[str] | None = None) -> JudgeResult:
    """judge-v2 when memories is given (the texts that were in the answer's context); judge-v1 otherwise."""
    payload = {"问题": probe["question"], "当前时间": probe["at"], "应使用的信息": need_values,
               "过时或禁止的信息": avoid_values, "问题是否与用户个人信息无关": probe["no_memory"],
               "备注": probe.get("judge_note", ""), "助理回答": reply}
    if memories is not None:
        payload["助理当时掌握的记忆"] = memories or ["（无）"]
    user = json.dumps(payload, ensure_ascii=False)
    return JudgeResult.model_validate_json(_call(client, [{"role": "system", "content": JUDGE_PROMPT},
                                                          {"role": "user", "content": user}], 300))
