"""Step 5b: the orchestrator. One entry point for every customer message.

    handle_message(conn, customer_id, message)  ->  Reply

customer_id comes from the authenticated SESSION (the API layer, Step 8). It is never taken from the message.
Pipeline: route -> run the chosen flow(s) -> combine. Any unexpected error becomes a human handoff:
the customer never sees a stack trace and is never left without an answer.

Manual test:  python -m app.handle C001 "Mera order #45821 kahan hai?"
"""
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field

from app import config, llm, rag, refund_flow, return_flow
from app import order_flow
from app.guardrails import check_input, check_output
from app.logging_setup import hash_customer, log_interaction
from app.router import Route, route
from app.tools.orders import extract_order_ids, get_order_status

log = logging.getLogger("handle")

HUMAN_REPLY = ("I'm sorry for the trouble. I'm connecting you to a human agent who will contact you shortly. / "
               "Takleef ke liye maafi chahta hu. Main aapko ek human agent se jod raha hu, woh jaldi aapse sampark karenge.")
DECLINE_REPLY = ("I can only help with orders, returns, refunds, warranty, shipping and cancellations. / "
                 "Main sirf order, return, refund, warranty, shipping aur cancellation ke sawaalon mein madad kar sakta hu.")
RATE_REPLY = ("You are sending messages too fast. Please wait a minute and try again. / "
              "Aap bahut tez message bhej rahe hain. Kripya ek minute ruk kar dobara try karein.")
EMPTY_REPLY = "Please type your question. / Kripya apna sawaal likhein."
TOO_LONG_REPLY = ("Your message is too long. Please keep it short (under 1000 characters). / "
                  "Aapka message bahut lamba hai. Kripya use chhota rakhein (1000 akshar se kam).")
SECRET_NOTE = ("For your safety: please never share card numbers, OTP, CVV or PIN in chat. We will never ask for them. / "
               "Aapki suraksha ke liye: card number, OTP, CVV ya PIN kabhi chat mein share na karein.")
LOGIN_REPLY = ("Please log in to check your orders or refunds. / "
               "Order ya refund ke liye kripya pehle login karein.")


@dataclass
class Reply:
    text: str
    actions: list[str]
    route_reason: str
    handoff: bool = False
    handoff_summary: str | None = None
    stage_ms: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    request_id: str | None = None
    guard: dict = field(default_factory=dict)


def _summary(customer_id, message, rt: Route, extra: str | None = None) -> str:
    parts = [f"customer={customer_id}", f"route={rt.reason}", f"sentiment={rt.sentiment}",
             f"intents={rt.intents}", f"customer_message={message[:300]!r}"]
    if extra:
        parts.append(extra)
    return " | ".join(parts)


def _answer(conn, customer_id: str | None, message: str, *, retriever=None,
            generate=llm.generate, today=None) -> Reply:
    """Route + run the flows on an ALREADY CLEANED message. Use handle_message(), which adds the guardrails."""
    stage_ms: dict = {}
    details: dict = {}

    def timed(name, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            stage_ms[name] = round((time.perf_counter() - t0) * 1000)

    try:
        rt = timed("router", lambda: route(message, generate=generate))
    except Exception:
        log.exception("router crashed")
        return Reply(HUMAN_REPLY, ["human"], "router_crashed", True,
                     f"customer={customer_id} | route=router_crashed | customer_message={message[:300]!r}", stage_ms)

    details.update(intents=rt.intents, sentiment=rt.sentiment, product_line=rt.product_line,
                   confidence=rt.confidence, angry=rt.angry, models={"router": rt.model})

    if rt.actions == ["human"]:
        return Reply(HUMAN_REPLY, ["human"], rt.reason, True, _summary(customer_id, message, rt), stage_ms, details)
    if rt.actions == ["decline"]:
        return Reply(DECLINE_REPLY, ["decline"], rt.reason, False, None, stage_ms, details)
    if rt.actions == ["clarify"]:
        return Reply(rt.clarifying_question, ["clarify"], rt.reason, False, None, stage_ms, details)

    texts, question, handoff, summaries = [], None, False, []
    policy_added = False
    policy_cache: dict = {}
    if retriever is None and any(a in rt.actions for a in ("policy", "refund", "return")):
        from app.retrieval import Retriever
        retriever = Retriever()
    order_ids = extract_order_ids(message)
    have_order = bool(order_ids)
    login_needed = False

    # (B) The router often cannot tell the product ("my order #45821 ... return policy?"). If the message names an order
    # that belongs to THIS customer, the order's own category decides. Someone else's order never influences anything.
    product_line = rt.product_line
    if product_line == "unknown" and customer_id and have_order:
        try:
            owned = get_order_status(conn, customer_id, order_ids[0])
            if owned and owned["category"] in ("electronics", "fashion", "home"):
                product_line = owned["category"]
                details["product_line_from_order"] = product_line
        except Exception:
            log.exception("could not read the order's product line")

    def policy_answer():
        if "a" not in policy_cache:      # never run the policy flow twice for one message
            # Mixed message ("where is my order and what is the return policy?"): search and answer with the
            # rules part only. Searching with the whole message found shipping chunks and no returns chunk.
            policy_cache["a"] = timed("policy", lambda: rag.answer_question(
                rt.policy_question or message, retriever=retriever, generate=generate, product_line=product_line))
            details["policy_query"] = rt.policy_question
        return policy_cache["a"]

    for action in rt.actions:
        try:
            if action in ("order_status", "refund", "return") and not customer_id:
                if action == "order_status" or have_order or "policy" not in rt.actions:
                    login_needed = True      # a general rules question ("can I return X?") needs no login note
                if action in ("refund", "return"):
                    # (A) not logged in: the general rule (e.g. automatic reversal) needs no account, so give it.
                    pa = policy_answer()
                    if not pa.handoff and not policy_added:
                        texts.append(pa.reply)
                        policy_added = True
            elif action == "policy":
                a = policy_answer()
                if not policy_added:                 # the same answer must never appear twice in one reply
                    texts.append(a.reply)
                    policy_added = True
                details["policy"] = {"status": a.status, "reason": a.reason, "sources": a.sources}
                details["models"]["policy"] = a.model
                if a.handoff:
                    handoff = True
                    summaries.append(_summary(customer_id, message, rt, f"policy_unanswered:{a.reason}"))
            elif action == "order_status":
                a = timed("order_status", lambda: order_flow.answer_order_question(conn, customer_id, message, generate=generate))
                if a.status == "clarify":
                    question = question or a.reply          # asked ONCE, at the end
                else:
                    texts.append(a.reply)
                details["order_status"] = {"status": a.status, "reason": a.reason}
                details["models"]["order_status"] = a.model
            elif action in ("refund", "return") and not have_order:
                # No order number: answer the policy question AND ask which order (see eval note on G007).
                pa = policy_answer()
                if not pa.handoff and not policy_added:
                    texts.append(pa.reply)
                    policy_added = True
                facts = refund_flow.recent_orders_facts(conn, customer_id)
                question = question or refund_flow.template_reply(facts)   # fixed text: no LLM call needed
                details[action] = {"decision": "need_order_number", "policy_status": pa.status}
                if not facts["recent_orders"]:
                    handoff = True
                    summaries.append(_summary(customer_id, message, rt, f"{action}_asked_but_no_orders"))
            elif action == "return":
                a = timed("return", lambda: return_flow.answer_return_request(conn, customer_id, message, generate=generate, today=today))
                texts.append(a.reply)
                details["return"] = {"decision": a.decision, "reason": a.reason}
                details["models"]["return"] = a.model
                if a.handoff:
                    handoff = True
                    summaries.append(a.handoff_summary or _summary(customer_id, message, rt))
            elif action == "refund":
                a = timed("refund", lambda: refund_flow.answer_refund_request(
                    conn, customer_id, message, angry=rt.angry, generate=generate, today=today))
                texts.append(a.reply)
                details["refund"] = {"decision": a.decision, "reason": a.reason}
                details["models"]["refund"] = a.model
                if a.handoff:
                    handoff = True
                    summaries.append(a.handoff_summary or _summary(customer_id, message, rt))
        except Exception:
            log.exception("flow %s crashed", action)
            texts.append(HUMAN_REPLY)
            handoff = True
            summaries.append(_summary(customer_id, message, rt, f"flow_crashed:{action}"))

    if question:
        texts.append(question)
    if login_needed:
        texts.append(LOGIN_REPLY)
    text = "\n\n".join(t for t in texts if t)
    return Reply(text or HUMAN_REPLY, rt.actions, rt.reason, handoff, " || ".join(summaries) or None, stage_ms, details)


def handle_message(conn, customer_id: str | None, message, *, retriever=None, generate=llm.generate,
                   today=None, limiter=None, request_id: str | None = None) -> Reply:
    """The one entry point: rate limit -> input guard -> route and answer -> output guard -> log."""
    t0 = time.perf_counter()
    request_id = request_id or uuid.uuid4().hex[:12]
    guard: dict = {"input_reason": None, "pii": [], "injection": False, "output_reasons": []}

    def finish(reply: Reply, clean_text: str) -> Reply:
        reply.request_id, reply.guard = request_id, guard
        reply.stage_ms["total"] = round((time.perf_counter() - t0) * 1000)
        try:
            log_interaction(
                request_id=request_id, customer=hash_customer(customer_id), actions=reply.actions,
                route_reason=reply.route_reason, handoff=reply.handoff, guard=guard, stage_ms=reply.stage_ms,
                intents=reply.details.get("intents"), sentiment=reply.details.get("sentiment"),
                product_line=reply.details.get("product_line"), confidence=reply.details.get("confidence"),
                message=clean_text[:200] if config.LOG_MESSAGE_TEXT else None)
        except Exception:                                  # a logging problem must never break the customer's reply
            log.exception("could not write the interaction log")
        return reply

    if limiter is not None:
        try:
            allowed = limiter.allow(customer_id or "anonymous")
        except Exception:
            allowed = True                                 # fail open
        if not allowed:
            guard["input_reason"] = "rate_limited"
            return finish(Reply(RATE_REPLY, ["rejected"], "rate_limited"), "")

    ic = check_input(message)
    guard.update(pii=ic.pii_found, injection=ic.injection, input_reason=ic.reason)
    if not ic.ok:
        text = {"empty": EMPTY_REPLY, "too_long": TOO_LONG_REPLY}.get(ic.reason, DECLINE_REPLY)   # injection: neutral decline
        return finish(Reply(text, ["rejected"], ic.reason), ic.text)

    reply = _answer(conn, customer_id, ic.text, retriever=retriever, generate=generate, today=today)
    if set(ic.pii_found) & {"CARD", "SECRET", "AADHAAR", "PAN"}:
        reply.text += "\n\n" + SECRET_NOTE

    oc = check_output(reply.text, customer_id)
    if not oc.ok:
        guard["output_reasons"] = oc.reasons
        log.warning("output guard blocked a reply: %s", oc.reasons)
        reply = Reply(HUMAN_REPLY, ["human"], "output_guard_blocked", True,
                      f"customer={customer_id} | route=output_guard_blocked | reasons={oc.reasons} | "
                      f"customer_message={ic.text[:300]!r}", reply.stage_ms, reply.details)
    return finish(reply, ic.text)


if __name__ == "__main__":
    from app.db import get_conn
    from app.logging_setup import setup_logging
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    cust, msg = sys.argv[1], " ".join(sys.argv[2:])
    r = handle_message(get_conn(), cust or None, msg)
    print(f"actions: {r.actions} | reason: {r.route_reason} | handoff: {r.handoff} | stage ms: {r.stage_ms}")
    print(f"reply:\n{r.text}")
    if r.handoff_summary:
        print(f"handoff summary: {r.handoff_summary}")
