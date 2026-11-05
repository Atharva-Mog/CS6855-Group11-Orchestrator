"""Extra test set in the style of the public set, with NetworkX-computed answers.

    python dev/make_extra.py        # writes dev/extra_query_set.csv

Same columns as the public set, so run_batches.py / run_queries.py / evaluate.py read it directly.
Conventions follow the public set: unweighted centralities, NetworkX defaults, closeness on directed
graphs with incoming distances (NetworkX's default), ego network = node + neighbours + edges among them.
Mix: answerable (single and multi-step), answerable look-alikes of ambiguous questions (to catch
over-asking), clarification_needed and unsupported.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import networkx as nx
from graphkit import GraphStore
from graphkit.testing import to_networkx

S = GraphStore()
K, L, A, C, P = (to_networkx(S.get(g)) for g in ["karate", "lesmis", "airports", "courses", "projects"])
rows = []


def top(d, k=1, exclude=()):
    items = sorted(((n, v) for n, v in d.items() if n not in exclude), key=lambda x: (-x[1], x[0]))
    return [n for n, _ in items[:k]]


def add(qid, graph, question, status, atype="", answer="", working="", notes=""):
    if isinstance(answer, list):
        answer = (" → " if atype == "path" else ", ").join(answer)
    if isinstance(answer, float):
        answer = round(answer, 4)
    rows.append({"Query ID": qid, "Graph": graph, "Question": question, "Expected status": status,
                 "Answer type": atype or "—", "Expected answer": answer if answer != "" else "—",
                 "Working (NetworkX)": working, "Notes": notes})


def country(g, c):
    return g.subgraph([n for n, d in g.nodes(data=True) if d.get("country") == c]).copy()


# ---------------------------------------------------------------- answerable, single tool
add("x01", "lesmis", "How many pairs of characters appear together in at least one chapter?", "answered", "number",
    L.number_of_edges())
dl = dict(L.degree()); add("x02", "lesmis", "Which character shares chapters with the most other characters?",
                           "answered", "node", top(dl)[0], f"degree {dl[top(dl)[0]]}")
add("x03", "airports", "What is the fewest number of flights needed to get from Bengaluru to Honolulu?", "answered",
    "number", nx.shortest_path_length(A, "BLR", "HNL"))
add("x04", "airports", "What is the shortest route by distance from Delhi to Sydney?", "answered", "path",
    nx.shortest_path(A, "DEL", "SYD", weight="weight"), f"{nx.shortest_path_length(A, 'DEL', 'SYD', weight='weight'):.0f} km")
pk = nx.pagerank(K)
add("x05", "karate", "What are the top 3 members of the karate club by PageRank?", "answered", "node_list", top(pk, 3),
    notes="Ranked: order matters")
pp = nx.pagerank(K, personalization={"m34": 1})
add("x06", "karate", "Which 3 members, other than the administrator, are most relevant to the administrator?",
    "answered", "node_list", top(pp, 3, exclude={"m34"}), "personalized PageRank from m34", "Ranked: order matters")
add("x07", "lesmis", "Which characters would split the co-appearance network into disconnected parts if removed?",
    "answered", "node_list", sorted(nx.articulation_points(L)))
add("x08", "courses", "Can all the courses be taken in an order where every prerequisite comes first?", "answered",
    "boolean", "true" if nx.is_directed_acyclic_graph(C) else "false")
a, b = set(K["m1"]), set(K["m34"])
add("x09", "karate", "What is the Jaccard similarity of the instructor's and the administrator's friends?",
    "answered", "number", len(a & b) / len(a | b), f"{len(a & b)} common")
bk = nx.betweenness_centrality(K)
add("x10", "karate", "Which member lies on the most shortest paths between other members?", "answered", "node",
    top(bk)[0])
add("x11", "airports", "Can you fly from Honolulu to Reykjavik with some sequence of flights?", "answered", "boolean",
    "true" if nx.has_path(A, "HNL", "KEF") else "false")
add("x12", "lesmis", "What is the largest number of characters that all appear with each other?", "answered",
    "number", max(len(q) for q in nx.find_cliques(L)))

# ---------------------------------------------------------------- answerable, multi-step
fr = country(A, "France"); dfr = dict(fr.degree())
add("x13", "airports", "Which French airport has the most routes to other French airports?", "answered", "node",
    top(dfr)[0], f"French subgraph {fr.number_of_nodes()} airports; degree in+out")
ego = nx.ego_graph(L, "c74")
add("x14", "lesmis", "How many triangles are there in Valjean's immediate circle?", "answered", "number",
    sum(nx.triangles(ego).values()) // 3, f"ego network of c74 ({ego.number_of_nodes()} nodes)")
egoK = nx.ego_graph(K, "m34"); de = {n: d for n, d in egoK.degree() if n != "m34"}
add("x15", "karate", "Among the administrator's friends, who has the most connections within the administrator's circle?",
    "answered", "node", top(de)[0], "ego network of m34, ego excluded")
it = country(A, "Italy"); bi = nx.betweenness_centrality(it)
add("x16", "airports", "Within Italy, which airport lies on the most shortest routes between other Italian airports?",
    "answered", "node", top(bi)[0], f"{it.number_of_nodes()} Italian airports")
core = nx.k_core(K); pc = nx.pagerank(core)
add("x17", "karate", "Who is the most influential member, by PageRank, within the karate club's densest core?",
    "answered", "node", top(pc)[0], f"main core k={max(nx.core_number(K).values())}")
egoJ = nx.ego_graph(L, "c40")
add("x18", "lesmis", "What is the average clustering coefficient of Javert's immediate circle?", "answered", "number",
    nx.average_clustering(egoJ))
cl = nx.closeness_centrality(A)
w = "BLR" if cl["BLR"] > cl["MAA"] else "MAA"
add("x19", "airports", "Which has the higher closeness centrality, Chennai or Bengaluru, and by how much?", "answered",
    "node", w, f"MAA {cl['MAA']:.4f}, BLR {cl['BLR']:.4f}", "Structured answer is the airport")

# ---------------------------------------------------------------- answerable look-alikes (must NOT ask)
add("x20", "airports", "What is the fewest number of flights from Paris Charles de Gaulle to Tokyo Haneda?", "answered",
    "number", nx.shortest_path_length(A, "CDG", "HND"), notes="Specific airports and measure given: not ambiguous")
add("x21", "airports", "What is the shortest route by distance from London Heathrow to New York JFK?", "answered",
    "path", nx.shortest_path(A, "LHR", "JFK", weight="weight"), notes="Measure given: not ambiguous")
add("x22", "lesmis", "What is the fewest number of steps between Marius and Cosette in the co-appearance network?",
    "answered", "number", nx.shortest_path_length(L, "c50", "c19"), notes="Measure given (hops): not ambiguous")
add("x23", "karate", "Which member of the karate club has the most friends?", "answered", "node",
    top(dict(K.degree()))[0])

# ---------------------------------------------------------------- clarification_needed
for qid, g, q, why in [
    ("x24", "airports", "What's the shortest route from London to New York?", "London has 5 airports, New York 2; fewest flights or least distance?"),
    ("x25", "airports", "How many flights does it take to get from Moscow to Chennai?", "Moscow has 3 airports (DME, SVO, VKO)"),
    ("x26", "airports", "How far is Delhi from Mumbai?", "Hops, route distance in km, or direct distance?"),
    ("x27", "lesmis", "How similar are Fantine and Cosette?", "Which measure: shared neighbours, Jaccard, embeddings?"),
    ("x28", "lesmis", "How connected are Valjean and Javert?", "Direct edge weight, hops, common neighbours, or flow?"),
    ("x29", "(none)", "How many nodes does the graph have?", "No graph is named"),
    ("x30", "(none)", "What is the shortest path between the two most important nodes?", "No graph is named"),
    ("x31", "airports", "Which routes connect Chicago and Shanghai?", "Two airports each (MDW/ORD, PVG/SHA); direct only or any path?"),
    ("x32", "lesmis", "Who is closest to Thenardier?", "Thenardier vs MmeThenardier; and 'closest' by which measure?"),
]:
    add(qid, g, q, "clarification_needed", notes=why)

# ---------------------------------------------------------------- unsupported
for qid, g, q, why in [
    ("x33", "airports", "Which airport has the longest runway?", "No runway data"),
    ("x34", "airports", "How many passengers flew from Delhi to Mumbai in 2014?", "No passenger data"),
    ("x35", "airports", "Which routes does Lufthansa fly out of Frankfurt?", "No airline names, only counts"),
    ("x36", "karate", "How old is the club's instructor?", "No age attribute"),
    ("x37", "courses", "Who teaches Machine Learning?", "No instructor data"),
    ("x38", "lesmis", "In which chapter do Valjean and Javert first meet?", "No chapter data, only counts"),
    ("x39", "karate", "What is the treewidth of the karate network?", "No tool computes treewidth"),
    ("x40", "lesmis", "Does the Les Miserables network have a Hamiltonian cycle?", "No tool computes this"),
    ("x41", "airports", "What will the weather be in Chennai tomorrow?", "Not graph data"),
]:
    add(qid, g, q, "unsupported", notes=why)

out = ROOT / "dev" / "extra_query_set.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)} questions -> {out}")
