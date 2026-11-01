"""Builds a small practice question set with NetworkX-computed answers (dev/queries_dev.json, dev/expected_dev.json)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import networkx as nx
from graphkit import GraphStore
from graphkit.testing import to_networkx

S = GraphStore()
k, l, a, c, p = (to_networkx(S.get(g)) for g in ["karate", "lesmis", "airports", "courses", "projects"])
pr = nx.pagerank(a); top = sorted(pr, key=lambda n: (-pr[n], n))
lab = lambda g, s: [n for n in g if s in g.nodes[n]["label"]][0]
ml, pb = lab(c, "Machine Learning"), lab(c, "Programming Basics")
core = nx.k_core(k); dc = dict(core.degree())
best_core = sorted(dc, key=lambda n: (-dc[n], n))
Q = [
 ("d01", "How many edges does the karate club network have?", "answered", {"type": "number", "value": 78}),
 ("d02", "Which member of the karate club has the most friends?", "answered", {"type": "node", "value": max(k, key=lambda n: k.degree(n))}),
 ("d03", "What are the 3 most important airports by PageRank?", "answered", {"type": "node_list", "value": top[:3], "ordered": True}),
 ("d04", "What is the fewest number of flights needed to get from Chennai to Lisbon?", "answered", {"type": "number", "value": nx.shortest_path_length(a, "MAA", "LIS")}),
 ("d05", "What is the shortest route by distance from Chennai to Lisbon? Give the path.", "answered", {"type": "path", "value": nx.shortest_path(a, "MAA", "LIS", weight="weight")}),
 ("d06", "Is the projects graph bipartite?", "answered", {"type": "boolean", "value": True}),
 ("d07", "What is the maximum number of students that can each be assigned a different project?", "answered",
  {"type": "number", "value": len(nx.bipartite.hopcroft_karp_matching(p, [n for n in p if n.startswith("S")])) // 2}),
 ("d08", "In the course catalogue, does Programming Basics lead (directly or indirectly) to Machine Learning?", "answered", {"type": "boolean", "value": ml in nx.descendants(c, pb)}),
 ("d09", "How many triangles are there in the karate club network?", "answered", {"type": "number", "value": sum(nx.triangles(k).values()) // 3}),
 ("d10", "What is the diameter of the Les Miserables co-appearance network in hops?", "answered", {"type": "number", "value": nx.diameter(l)}),
 ("d11", "In the karate network's k-core with the largest k, which member has the most connections inside that core?", "answered", {"type": "node", "value": best_core[0]}),
 ("d12", "Find the communities in the karate network with Louvain, then tell me which member of the largest community has the highest PageRank within that community.", "answered", None),
 ("d13", "What will the ticket price be for a flight from Chennai to Delhi next week?", "unsupported", None),
 ("d14", "Who is the most important one?", "clarification_needed", None),
 ("d15", "How many airports can you reach from Chennai with at most 2 flights?", "answered",
  {"type": "number", "value": len(nx.single_source_shortest_path_length(a, "MAA", cutoff=2)) - 1}),
 ("d16", "Give a valid order in which to take all the courses in the course catalogue.", "answered", None),
]
print("core degrees:", sorted(dc.items(), key=lambda x: -x[1])[:4])
(ROOT / "dev/queries_dev.json").write_text(json.dumps([{"query_id": q, "question": t} for q, t, _, _ in Q], indent=1))
(ROOT / "dev/expected_dev.json").write_text(json.dumps([{"query_id": q, "expected_status": s, "expected_answer": e} for q, _, s, e in Q], indent=1))
