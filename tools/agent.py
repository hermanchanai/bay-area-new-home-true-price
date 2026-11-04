"""Buyer-question agent: Muse Spark with native tool calls over the database (tools/agent_tools.py).

    python -m tools.agent "4 bed under $1.1M true price, ready by January, in Contra Costa or San Joaquin"

Every fact in the answer comes from a tool result and carries a citation: [H<home_id>], [C<community_id>],
[P<plan_id>]. The model never does its own arithmetic; tools return the differences it needs.
"""
import json
import logging
import sys
from datetime import date

from tools import db, llm
from tools.agent_tools import TOOLS, call

MAX_TURNS = 8
log = logging.getLogger(__name__)

SYSTEM = """You answer home buyers' questions about new homes from production builders in eight counties \
in and around the Bay Area: Alameda, Contra Costa, Santa Clara, San Mateo, Solano, Sonoma, Napa and San Joaquin. Today is {today}.

Rules:
- Get every fact from the tools. Never state a price, rate, date, address or count a tool did not return. If \
the tools have no answer, say so plainly; for a place outside these counties, name the counties covered.
- To look up a home the buyer names by address, call search_homes with address.
- Do no arithmetic. Quote the numbers the tools return (discount_high, true_price_low, compare's differences, ...), never ones you \
computed. Write money as $1,079,990, or rounded as $1.08M or $804K; never a bare number.
- Cite with [H<home_id>] for a home, [C<community_id>] for a community, [P<plan_id>] for a floor plan: once, \
right after the address or name; facts on the same line share that citation. One id per bracket. Cite only ids \
a tool returned.
- "True price" = asking price minus the dollar value of the builder's incentives (20% down, 30-year fixed, \
market rate from FRED). When it is a range, the low end counts "up to" offers in full. Never call a figure \
guaranteed. Every figure is at 20% down: for another down payment, say the buyer should ask the builder whether \
the financing offers still apply, and don't work one out.
- When the buyer asks for homes, list the best matches first (at most 5), cheapest true price first unless they \
ask otherwise: address, community, beds, asking, true price range (one figure when both ends are equal), ready \
date ("ready now" when move_in_status is now). Only list homes that meet every condition they gave. If none do, \
say so, say which condition rules them out, and offer to relax it; do not list homes that miss a condition.
- "homes" may be only part of total_matching. For how many homes match, or where they are, use total_matching and \
by_community, which cover every match.
- Months: "ready by January" means ready_by = the coming January (YYYY-MM).
- Past sales are county assessor records as published, not estimates or appraisals: each home's assessed value
  (land + improvements). On a fully assessed new home that's usually the sale price. Call it an assessed value, never
  a sale price. Only Alameda, Contra Costa and San Joaquin have them.
- Be brief: under 200 words, plain markdown, no tables."""


def run(question: str, conn=None, chat=llm.chat, today: str | None = None, max_turns: int = MAX_TURNS,
        purpose: str = "agent") -> dict:
    conn = conn or db.connect()
    messages = [{"role": "system", "content": SYSTEM.format(today=today or date.today().isoformat())},
                {"role": "user", "content": question}]
    trace, cost = [], 0.0
    for turn in range(1, max_turns + 1):
        last = turn == max_turns
        if last:
            messages.append({"role": "user", "content": "Answer now with what the tools returned so far."})
        r = chat(messages, purpose=purpose, tools=None if last else TOOLS, conn=conn)
        cost += r.cost_usd
        if not r.tool_calls:
            return {"question": question, "answer": r.text, "trace": trace, "turns": turn, "cost_usd": cost,
                    "finish_reason": r.finish_reason}
        messages.append({"role": "assistant", "content": r.text or None, "tool_calls": r.tool_calls})
        for tc in r.tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
                result = call(conn, name, args) if isinstance(args, dict) else {"error": "arguments must be an object"}
            except json.JSONDecodeError:
                args, result = None, {"error": "arguments were not valid JSON"}
            log.info("tool %s %s -> %s", name, args, "error" if "error" in result else "ok")
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, default=str)})
    raise RuntimeError("unreachable: the last turn has no tools")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = run(" ".join(sys.argv[1:]))
    print(out["answer"])
    print(f"\n[{out['turns']} turns, tools: {', '.join(t['tool'] for t in out['trace'])}, ${out['cost_usd']:.4f}]")
