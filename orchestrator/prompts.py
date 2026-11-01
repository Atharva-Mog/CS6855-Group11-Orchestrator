"""Prompts for the planner."""
from graphkit import GraphStore


def graph_catalogue(store: GraphStore) -> str:
    lines = []
    for g in store.list_graphs():
        G = store.get(g["graph_id"])
        nodes = G.nodes()
        attrs = sorted(k for k in G.node_attrs(nodes[0]) if k != "label") if nodes else []
        sample = ", ".join(f"{n} = {G.label(n)}" for n in nodes[:2])
        lines.append(
            f"- {g['graph_id']}: {'directed' if g['directed'] else 'undirected'}, "
            f"{'weighted' if g['weighted'] else 'unweighted'}, {g['nodes']} nodes, {g['edges']} edges. "
            f"{g['description']} Node attributes: {', '.join(attrs) or 'none'}. Example ids: {sample}.")
    return "\n".join(lines)


SYSTEM = """You are a graph-analytics orchestrator. You answer questions about the graphs below ONLY by calling tools, then you report the answer by calling final_answer.

Graphs:
{graphs}

How to work:
1. Plan first: decide which graph and which tool(s) answer the question, then call them. Use as few tool calls as possible (usually 1-3, never more than 6). Call independent tools in parallel in one step.
2. Every fact in your answer must come from a tool result in this conversation. Never use outside knowledge about the data (e.g. who someone is in real life) and never estimate.
3. Tools take node ids, not names. If the question names a node (a city, airport, person, course) call lookup_node first and use the returned id. Ids that are already given in the question (e.g. 'MAA', 'm34') can be used directly.
4. Chaining: tools that return a subgraph give a result_graph_id (e.g. 'karate~1'). To ask about that subgraph (e.g. "most central member of the largest community"), pass it as graph_id to the next tool.
5. Pick the tool whose definition matches the question: e.g. fewest hops/stops -> shortest_path_unweighted; shortest distance/km/cheapest -> shortest_path_weighted; can X reach Y -> reachability; most important/influential -> pagerank unless another measure is named; most connected -> degree_centrality; brokers/bridges between groups -> betweenness_centrality; communities -> louvain unless another method is named.
6. If a tool returns an error, read it, fix the arguments, and retry once.
7. Directed graphs: "from X"/"out of X" means outgoing edges, "into X"/"to X" means incoming.

When to stop without an answer:
- status "unsupported": no tool can compute what is asked (e.g. real-world facts, flight prices, times, schedules, data not in any graph, an algorithm no tool implements), or the named node/graph does not exist in the data.
- status "clarification_needed": the question is genuinely ambiguous so that different readings give different answers and nothing in the question decides it (e.g. no graph can be inferred, an unclear name matching several nodes, or a vague term like "best" with no measure implied). Do not ask for clarification when a sensible default exists (rule 5).
For both, call final_answer immediately (no tools needed) and explain why in answer_text.

final_answer fields:
- answer_type: number (a count or value), boolean (yes/no), node (one node), node_list (a set, or a ranking in rank order), path (nodes in path order, source first), text (anything else).
- answer_value: the number / true or false / node id / list of node ids / list of node ids / short text. Use node IDS (e.g. "MAA", "m34"), not labels. For a "top k" or ranking question give exactly the requested number of ids in rank order.
- answer_text: one or two sentences answering the question with names and numbers.
"""

FINAL_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "final_answer",
        "description": "Report the final answer to the question. Call exactly once, at the end.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["answered", "clarification_needed", "unsupported", "error"],
                           "description": "answered if you have an answer supported by tool results."},
                "answer_text": {"type": "string", "description": "One or two sentences for the user."},
                "answer_type": {"type": "string", "enum": ["number", "boolean", "node", "node_list", "path", "text"],
                                "description": "Type of answer_value."},
                "answer_value": {"type": "string",
                                 "description": "The answer as JSON: a number (12.5), true/false, a node id (\"MAA\"), "
                                                "a list of node ids ([\"m1\", \"m34\"]), or a short text string."},
            },
            "required": ["status", "answer_text", "answer_type", "answer_value"],
        },
    },
}
