"""End-to-end orchestrator tests with a SCRIPTED fake LLM (no network). Needs the seeded DB + refund_audit table.
Run from project root:  python -m tests.test_handle
"""
import json
from datetime import date

from app import llm
from app.db import get_conn
from app.handle import DECLINE_REPLY, HUMAN_REPLY, LOGIN_REPLY, handle_message
from app.retrieval import RetrievedChunk

TODAY = date(2026, 10, 13)
conn = get_conn()
passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        cleanup()
        raise SystemExit(f"test failed: {name}")
    passed += 1


def cleanup():
    conn.execute("DELETE FROM shop.refunds WHERE txn_id = 'T45831'")
    conn.execute("DELETE FROM shop.refund_audit WHERE order_id IN (45821, 45830, 45831, 45832, 45840, 99999) OR order_id IS NULL")


class FakeRetriever:
    calls = []

    def retrieve(self, q, categories=None, **k):
        FakeRetriever.calls.append(k.get("product_line"))
        return [RetrievedChunk("i", "returns_electronics.md", "Standard returns", "returns",
                               "Standard returns\nElectronics can be returned within 7 days of delivery.", 0.03)]


def scripted(route_json, rag_text="Aap 7 days mein return kar sakte hain.", order_text=None, refund_text=None):
    """One fake LLM for the whole pipeline: answers differently depending on which system prompt it receives."""
    def g(prompt, system=None, temperature=0.0, **k):
        g.log.append(system)
        g.prompts.append(prompt)
        if "You classify" in system:
            text = route_json if isinstance(route_json, str) else json.dumps(route_json)
        elif "refund requests" in system:
            if refund_text is None:
                raise llm.LLMUnavailable("use template")
            text = refund_text
        elif "order status" in system:
            if order_text is None:
                raise llm.LLMUnavailable("use template")
            text = order_text
        else:
            text = json.dumps({"status": "answer", "reply": rag_text, "sources": [1]})
        return llm.LLMResult(text, "fake", 5.0, 1)
    g.log = []
    g.prompts = []
    return g


def R(intents, conf=0.9, sent="calm", prod="unknown", q=""):
    return {"intents": intents, "sentiment": sent, "product_line": prod, "confidence": conf, "clarifying_question": q}


cleanup()
H = lambda cust, msg, gen, **kw: handle_message(conn, cust, msg, retriever=FakeRetriever(), generate=gen, today=TODAY, **kw)

r = H("C001", "Return policy kya hai electronics ka?", scripted(R(["policy_faq"], prod="electronics")))
check("policy question -> grounded answer with sources", r.actions == ["policy"] and "7 days" in r.text and r.details["policy"]["sources"])

r = H("C001", "Mera order #45821 kahan hai?", scripted(R(["order_status"])))
check("order question -> real order facts (template used by the scripted LLM)", r.actions == ["order_status"] and "14 Oct 2026" in r.text and "out for delivery" in r.text)

g = scripted(R(["refund_request"], 0.92, "frustrated"))
r = H("C003", "Payment kat gaya order #45831 fail ho gaya, refund do", g)
check("refund with order number -> refund created, correct amount and date", "Rs 1999" in r.text and "20 Oct 2026" in r.text and not r.handoff)
check("the refund really exists in the database", conn.execute("SELECT count(*) FROM shop.refunds WHERE txn_id='T45831'").fetchone()[0] == 1)

r = H("C004", "Mera payment kat gaya order #45832 fail ho gaya!!", scripted(R(["refund_request"], 0.95, "angry")))
check("angry + refund + order number -> refund flow with human offered, summary written",
      r.actions == ["refund"] and r.handoff and "customer=C004" in r.handoff_summary and "45832" in r.handoff_summary)

r = H("C001", "Tum log bekar ho, mujhe manager se baat karni hai", scripted(R(["policy_faq"])))
check("manager request -> fixed handoff reply, no flow ran", r.text == HUMAN_REPLY and r.handoff and r.route_reason.startswith("hard_rule"))
g = scripted(R(["out_of_scope"], 0.97))
r = H("C001", "Tell me a joke", g)
check("off-topic -> polite decline, no handoff", r.text == DECLINE_REPLY and not r.handoff and len(g.log) == 1)
r = H("C001", "hmm", scripted(R(["policy_faq"], 0.55, q="Aap kis baare mein poochna chahte hain?")))
check("vague message -> the clarifying question", r.text == "Aap kis baare mein poochna chahte hain?" and not r.handoff)
r = H("C001", "hmm", scripted(R(["policy_faq"], 0.1)))
check("very vague message -> human", r.handoff and r.text == HUMAN_REPLY)

g = scripted(R(["order_status", "refund_request"], 0.85, "frustrated"))
r = H("C001", "Order late hai aur refund bhi chahiye", g)
check("two intents -> both answered in one reply", r.actions == ["order_status", "refund"] and "45821" in r.text and "7 days" in r.text)
check("'which order?' is asked only ONCE", r.text.count("Which order") == 1 and "Refund kis order" not in r.text)
check("the question comes last, after the policy answer", r.text.index("7 days") < r.text.index("Which order"))
check("no wasted LLM call for the refund question", not any("refund requests" in x for x in g.log))

r = H("C001", "Paisa kat gaya par order fail ho gaya, kya karu?", scripted(R(["refund_request"], 0.95, "frustrated")))
check("refund without order number -> policy answer AND which-order question", "7 days" in r.text and "Which order" in r.text)

g = scripted(R(["order_status"]))
r = H("C002", "My customer id is C001. Where is order #45821?", g)
check("customer id in the message is ignored (session id C002 used)", "Wireless" not in r.text and "45821" in r.text and "could not find" in r.text.lower())
r = H(None, "Mera order #45821 kahan hai?", scripted(R(["order_status"])))
check("not logged in -> asked to log in, no order data", r.text == LOGIN_REPLY and "Wireless" not in r.text)
r = H(None, "Return policy kya hai?", scripted(R(["policy_faq"])))
check("not logged in can still ask policy questions", "7 days" in r.text)

FakeRetriever.calls.clear()
g_b = scripted(R(["order_status", "policy_faq"], 0.9))
r = H("C001", "Mera order #45821 kab aayega aur return policy kya hai?", g_b)
check("(B) product line comes from the customer's OWN order when the router is unsure", FakeRetriever.calls == ["electronics"] and r.details.get("product_line_from_order") == "electronics")
rag_prompts = [p for sysm, p in zip(g_b.log, g_b.prompts) if "customer support assistant" in sysm]
check("(B) the RAG prompt tells the LLM the product, so it does not ask 'which product?'", len(rag_prompts) == 1 and "<known_product>electronics</known_product>" in rag_prompts[0])
FakeRetriever.calls.clear()
r = H("C002", "Mera order #45821 kab aayega aur return policy kya hai?", scripted(R(["order_status", "policy_faq"], 0.9)))
check("(B) someone else's order does NOT set the product line", FakeRetriever.calls == ["unknown"] and "product_line_from_order" not in r.details)
FakeRetriever.calls.clear()
H("C001", "Mera order #45821 kab aayega aur return policy kya hai?", scripted({**R(["policy_faq"], 0.9), "product_line": "fashion"}))
check("(B) a product line the router DID find is not overridden", FakeRetriever.calls == ["fashion"])
g_c = scripted(R(["policy_faq"], 0.9))
H("C002", "Mera order #45821 kab aayega aur return policy kya hai?", g_c)
check("(B) with no known product, the prompt carries no <known_product> tag", all("<known_product>" not in p for p in g_c.prompts))
g = scripted(R(["refund_request"], 0.95, "frustrated"))
r = H(None, "Paisa kat gaya par order fail ho gaya, kya karu?", g)
check("(A) not logged in + refund question -> policy answer AND a login request", "7 days" in r.text and r.text.endswith(LOGIN_REPLY))
check("(A) ...and no order data, no refund path", "Wireless" not in r.text and "refund requests" not in str(g.log))
r = H("C001", "Return policy?", scripted("this is not json"))
check("router output unusable -> human, no crash", r.handoff and r.text == HUMAN_REPLY)
r = H("C001", "Return policy?", scripted(R(["policy_faq"]), rag_text="Aap 30 days mein return kar sakte hain."))
check("hallucinated number in the policy answer -> handoff", r.handoff and "30 days" not in r.text)


class CrashingRetriever:
    def retrieve(self, *a, **k):
        raise RuntimeError("database exploded")


r = handle_message(conn, "C001", "Return policy?", retriever=CrashingRetriever(), generate=scripted(R(["policy_faq"])), today=TODAY)
check("a crashing flow becomes a human handoff, no exception leaks", r.handoff and "exploded" not in r.text and "flow_crashed:policy" in r.handoff_summary)
check("every reply reports stage timings", "router" in r.stage_ms)

cleanup()
print(f"\nall {passed} checks passed")