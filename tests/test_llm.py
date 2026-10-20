"""LLM-layer tests with a FAKE client (no network). Run: python -m tests.test_llm"""
from google.genai import errors

from app import config, llm

passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        raise SystemExit(f"test failed: {name}")
    passed += 1


class Resp:
    def __init__(self, text):
        self.text = text


class FakeClient:
    def __init__(self, plan=None, delay=0.0):
        self.plan, self.calls, self.configs, self.delay = plan or {}, [], [], delay

    @property
    def models(self):
        return self

    def generate_content(self, model, contents, config):
        import time
        self.calls.append(model)
        self.configs.append(config)
        time.sleep(self.delay)
        a = self.plan.get(model, "answer")
        if isinstance(a, Exception):
            raise a
        return Resp(a)


def err(code):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"message": "x"}}, None)


nosleep = lambda s: None
FIRST, SECOND = config.GEMINI_MODELS[0], config.GEMINI_MODELS[1]

c = FakeClient()
r = llm.generate("hi", client=c, sleep=nosleep)
check("healthy call uses the first model once", r.model == FIRST and c.calls == [FIRST] and r.text == "answer")

c = FakeClient({FIRST: err(503)})
r = llm.generate("hi", client=c, sleep=nosleep)
check("503 -> retry once, then fall back to the next model", r.model == SECOND and c.calls == [FIRST, FIRST, SECOND])

c = FakeClient({FIRST: err(429)})
check("429 (rate limit) falls back like 503", llm.generate("hi", client=c, sleep=nosleep).model == SECOND)

c = FakeClient({FIRST: err(404)})
check("404 (model retired) skips without retrying", llm.generate("hi", client=c, sleep=nosleep).model == SECOND and c.calls == [FIRST, SECOND])

c = FakeClient({m: err(503) for m in config.GEMINI_MODELS})
try:
    llm.generate("hi", client=c, sleep=nosleep)
    ok = False
except llm.LLMUnavailable:
    ok = True
check("all models failing raises LLMUnavailable", ok and len(c.calls) == 2 * len(config.GEMINI_MODELS))

c = FakeClient({FIRST: err(403)})
try:
    llm.generate("hi", client=c, sleep=nosleep)
    ok = False
except errors.ClientError:
    ok = True
check("403 (bad key) is raised at once, no retry, no fallback", ok and len(c.calls) == 1)

c = FakeClient({FIRST: ""})
r = llm.generate("hi", client=c, sleep=nosleep)
check("empty answer counts as a failure and falls back", r.model == SECOND)

old = config.LLM_TOTAL_DEADLINE_SECONDS
config.LLM_TOTAL_DEADLINE_SECONDS = 2
c = FakeClient({m: err(503) for m in config.GEMINI_MODELS}, delay=1.2)
try:
    llm.generate("hi", client=c, sleep=nosleep)
    ok = False
except llm.LLMUnavailable as e:
    ok = "deadline" in str(e)
config.LLM_TOTAL_DEADLINE_SECONDS = old
check("total deadline stops a slow chain early", ok and len(c.calls) < 2 * len(config.GEMINI_MODELS))

c = FakeClient()
llm.generate("hi", client=c, sleep=nosleep, json_mode=True)
check("json_mode asks the API for JSON", c.configs[0].response_mime_type == "application/json")
c = FakeClient()
llm.generate("hi", client=c, sleep=nosleep)
check("default mode does not force JSON", c.configs[0].response_mime_type is None)
print(f"\nall {passed} checks passed")