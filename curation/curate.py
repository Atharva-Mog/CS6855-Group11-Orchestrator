"""Test every candidate implementation against NetworkX and pick the best one per tool.

    python curation/curate.py            # test all, write curation/report.json + report.md
    python curation/curate.py --install  # also copy the winners into registry/tools/

Each implementation is scored on: correctness checks vs NetworkX, robustness
(bad inputs must return an error dict, not raise) and speed on airports.
"""
import argparse, contextlib, io, json, math, os, re, shutil, signal, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import networkx as nx
from graphkit import GraphStore, load_tool
from graphkit.testing import to_networkx

CAND = ROOT / "candidates"
TIMEOUT = 40

# ---------------------------------------------------------------- helpers
class Timeout(Exception):
    pass

def _alarm(*_):
    raise Timeout()

ALIASES = {
    "node": ["node", "seed_node", "node_a", "node1", "source", "src"],
    "node2": ["node_b", "node2", "other", "target_node", "target", "dest"],
    "source": ["source", "src", "seed_node", "node"],
    "target": ["target", "dest", "sink"],
    "sink": ["sink", "target"],
    "k": ["k", "radius", "level"],
    "num_communities": ["num_communities", "n_communities"],
    "top_k": ["top_k"],
}

def map_args(props, sem):
    out, used = {}, set()
    for key, val in sem.items():
        for alias in ALIASES.get(key, [key]):
            if alias in props and alias not in used:
                out[alias] = val
                used.add(alias)
                break
    return out

def call(tool, store, gid, **sem):
    props = tool.manifest["parameters"]["properties"]
    args = map_args(props, sem)
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(TIMEOUT)
    t = time.perf_counter()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            out = tool.run(store, gid, **args)
    except Timeout:
        return {"_crash": "timeout"}, TIMEOUT
    except Exception as e:
        return {"_crash": f"{type(e).__name__}: {e}"}, time.perf_counter() - t
    finally:
        signal.alarm(0)
    return out, time.perf_counter() - t

def numbers(out):
    """All numbers anywhere in the result and summary."""
    nums = []
    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.append(float(x))
        elif isinstance(x, dict):
            for v in x.values(): walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x: walk(v)
        elif isinstance(x, str):
            for m in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?(?:e-?\d+)?", x):
                try: nums.append(float(m.replace(",", "")))
                except ValueError: pass
    walk(out.get("result")); walk(out.get("summary"))
    return nums

def has_num(out, target, rel=1e-3, abs_=1e-6):
    return any(abs(n - target) <= max(abs_, rel * abs(target)) for n in numbers(out))

def bools(out):
    found = []
    def walk(x):
        if isinstance(x, bool): found.append(x)
        elif isinstance(x, dict):
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(out.get("result"))
    return found

def ids_in_order(out, valid):
    seen, order = set(), []
    def walk(x):
        if isinstance(x, dict):
            if "id" in x and x["id"] in valid and x["id"] not in seen:
                seen.add(x["id"]); order.append(x["id"])
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
        elif isinstance(x, str) and x in valid and x not in seen:
            seen.add(x); order.append(x)
    walk(out.get("result"))
    return order

def node_scores(out, valid):
    """{id: first numeric field} for node entries in the result."""
    sc = {}
    def walk(x):
        if isinstance(x, dict):
            if x.get("id") in valid:
                for k, v in x.items():
                    if k not in ("id", "label", "rank") and isinstance(v, (int, float)) and not isinstance(v, bool):
                        sc.setdefault(x["id"], []).append(float(v))
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(out.get("result"))
    return sc

def scores_match(out, ref, valid, rel=1e-3):
    """Every returned node has some numeric field equal to the reference (or a constant multiple of it)."""
    sc = node_scores(out, valid)
    if not sc:
        return False
    for factor in (1.0, None):
        good = 0
        for n, vals in sc.items():
            r = ref.get(n)
            if r is None: continue
            if factor is None:
                # allow unnormalised versions: fixed ratio across nodes
                ratios = [v / r for v in vals if r]
                if not ratios: continue
                factor_n = ratios
            if any(abs(v - (r if factor else v)) <= max(1e-6, rel * abs(r)) for v in vals) if factor else True:
                good += 1
        if factor and good == len(sc):
            return True
    # proportional check
    ratios = []
    for n, vals in sc.items():
        r = ref.get(n)
        if r: ratios.append([v / r for v in vals])
    if not ratios: return False
    for j in range(len(ratios[0])):
        col = [rs[j] for rs in ratios if len(rs) > j]
        if col and all(abs(c - col[0]) <= 1e-3 * abs(col[0]) for c in col) and col[0] != 0:
            return True
    return False

def top_ids(ref, k=5):
    return [n for n, _ in sorted(ref.items(), key=lambda x: (-x[1], x[0]))[:k]]

def rg_nodes(store, out):
    gid = out.get("result_graph_id")
    if not gid: return None
    try: return set(store.get(gid).nodes())
    except Exception: return None

def ok(out, store=None):
    return isinstance(out, dict) and out.get("status") == "ok"

# ---------------------------------------------------------------- reference graphs
S0 = GraphStore()
G = {g: to_networkx(S0.get(g)) for g in ["karate", "lesmis", "courses", "projects", "airports"]}
U = {g: (G[g].to_undirected() if G[g].is_directed() else G[g]) for g in G}
V = {g: set(G[g].nodes()) for g in G}

# ---------------------------------------------------------------- per-tool checks
# Each check: (description, function(tool, store) -> bool). Time is taken on airports runs.
def C(desc, gid, sem, pred):
    def f(tool, store):
        out, secs = call(tool, store, gid, **sem)
        f.secs = secs
        if "_crash" in out: return False
        try: return bool(pred(out, store))
        except Exception: return False
    f.desc, f.gid = desc, gid
    return f

def checks_for(name):
    k = G["karate"]; l = G["lesmis"]; a = G["airports"]; c = G["courses"]; p = G["projects"]
    T = []
    if name == "degree_centrality":
        ref = nx.degree_centrality(k); raw = dict(k.degree())
        T += [C("karate top-5 ids", "karate", {"top_k": 5}, lambda o, s: ids_in_order(o, V["karate"])[:5] == top_ids(raw, 5)),
              C("karate values", "karate", {"top_k": 10}, lambda o, s: scores_match(o, ref, V["karate"]) or scores_match(o, raw, V["karate"])),
              C("airports runs, top node", "airports", {"top_k": 5}, lambda o, s: ok(o) and ids_in_order(o, V["airports"])[0] in top_ids(dict(a.degree()), 3))]
    elif name == "pagerank":
        refs = [nx.pagerank(a), nx.pagerank(a, weight=None)]
        T += [C("airports values", "airports", {"top_k": 10}, lambda o, s: any(scores_match(o, r, V["airports"]) for r in refs)),
              C("airports top-5", "airports", {"top_k": 5}, lambda o, s: ids_in_order(o, V["airports"])[:5] in [top_ids(r, 5) for r in refs]),
              C("karate values", "karate", {"top_k": 10}, lambda o, s: scores_match(o, nx.pagerank(k), V["karate"]))]
    elif name == "personalized_pagerank":
        ref = nx.pagerank(k, personalization={"m34": 1})
        T += [C("karate seed m34 values", "karate", {"node": "m34", "top_k": 10}, lambda o, s: scores_match(o, ref, V["karate"]) or
                scores_match(o, {n: v for n, v in ref.items() if n != "m34"}, V["karate"] - {"m34"})),
              C("airports seed MAA runs", "airports", {"node": "MAA", "top_k": 5}, lambda o, s: ok(o)),
              C("bad node -> error", "karate", {"node": "zzz"}, lambda o, s: o.get("status") == "error")]
    elif name == "hits":
        h, au = nx.hits(a, max_iter=1000)
        T += [C("airports top hub or authority", "airports", {"top_k": 5}, lambda o, s: ok(o) and (ids_in_order(o, V["airports"])[0] in top_ids(h, 3) + top_ids(au, 3))),
              C("karate runs", "karate", {"top_k": 5}, ok)]
    elif name == "closeness_centrality":
        ref = nx.closeness_centrality(k); refl = nx.closeness_centrality(l)
        T += [C("karate values", "karate", {"top_k": 10, "node": "m1"}, lambda o, s: scores_match(o, ref, V["karate"])),
              C("lesmis values", "lesmis", {"top_k": 10, "node": "c01"}, lambda o, s: scores_match(o, refl, V["lesmis"]) or scores_match(o, nx.closeness_centrality(l, distance="weight"), V["lesmis"])),
              C("airports runs", "airports", {"top_k": 5, "node": "MAA"}, ok)]
    elif name == "betweenness_centrality":
        ref = nx.betweenness_centrality(k); refu = nx.betweenness_centrality(k, normalized=False)
        T += [C("karate values", "karate", {"top_k": 10}, lambda o, s: scores_match(o, ref, V["karate"]) or scores_match(o, refu, V["karate"])),
              C("karate top-5", "karate", {"top_k": 5}, lambda o, s: ids_in_order(o, V["karate"])[:5] == top_ids(ref, 5)),
              C("airports runs", "airports", {"top_k": 5}, lambda o, s: ok(o) and ids_in_order(o, V["airports"])[0] in top_ids(nx.betweenness_centrality(a, weight=None), 3) + top_ids(nx.betweenness_centrality(a, weight="weight"), 3))]
    elif name == "clustering_coefficient":
        T += [C("karate m1 local", "karate", {"node": "m1"}, lambda o, s: has_num(o, nx.clustering(k, "m1"))),
              C("karate average", "karate", {"top_k": 5}, lambda o, s: has_num(o, nx.average_clustering(k))),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "connected_components":
        sub = S0.subgraph("karate", ["m1", "m2", "m25", "m26", "m10", "m3"], "x")
        T += [C("projects count", "projects", {"top_k": 10}, lambda o, s: has_num(o, nx.number_connected_components(p))),
              C("projects largest size", "projects", {"top_k": 10}, lambda o, s: has_num(o, max(len(x) for x in nx.connected_components(p)))),
              C("karate one component", "karate", {}, lambda o, s: ok(o) and has_num(o, 34)),
              C("airports runs", "airports", {}, ok)]
    elif name == "strongly_connected_components":
        T += [C("courses count", "courses", {"top_k": 5}, lambda o, s: has_num(o, nx.number_strongly_connected_components(c))),
              C("airports largest", "airports", {"top_k": 5}, lambda o, s: has_num(o, max(len(x) for x in nx.strongly_connected_components(a)))),
              C("undirected -> error or ok", "karate", {}, lambda o, s: isinstance(o, dict))]
    elif name == "degree_distribution":
        degs = [d for _, d in k.degree()]
        T += [C("karate max degree", "karate", {}, lambda o, s: has_num(o, max(degs))),
              C("karate count of degree-2 nodes", "karate", {}, lambda o, s: has_num(o, degs.count(2))),
              C("airports runs", "airports", {}, ok)]
    elif name == "diameter_eccentricity":
        T += [C("karate diameter", "karate", {"top_k": 10}, lambda o, s: has_num(o, nx.diameter(k))),
              C("karate radius / center", "karate", {"top_k": 10}, lambda o, s: has_num(o, nx.radius(k)) or set(nx.center(k)) <= set(ids_in_order(o, V["karate"]))),
              C("lesmis diameter", "lesmis", {"top_k": 10}, lambda o, s: has_num(o, nx.diameter(l)) or has_num(o, nx.diameter(l, weight="weight"))),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "ego_network":
        want = {"m1"} | set(k["m1"])
        T += [C("karate m1 subgraph", "karate", {"node": "m1"}, lambda o, s: rg_nodes(s, o) in (want, want - {"m1"})),
              C("airports MAA runs", "airports", {"node": "MAA"}, lambda o, s: ok(o) and rg_nodes(s, o) is not None),
              C("bad node -> error", "karate", {"node": "zzz"}, lambda o, s: o.get("status") == "error")]
    elif name == "k_hop_neighborhood":
        want = set(nx.single_source_shortest_path_length(k, "m1", cutoff=2))
        T += [C("karate m1 k=2", "karate", {"node": "m1", "k": 2}, lambda o, s: rg_nodes(s, o) in (want, want - {"m1"})),
              C("airports MAA k=1 runs", "airports", {"node": "MAA", "k": 1}, lambda o, s: ok(o) and rg_nodes(s, o) is not None),
              C("bad node -> error", "karate", {"node": "zzz", "k": 1}, lambda o, s: o.get("status") == "error")]
    elif name == "graph_summary":
        T += [C("airports counts", "airports", {}, lambda o, s: has_num(o, 500) and has_num(o, 20058)),
              C("airports density", "airports", {}, lambda o, s: has_num(o, nx.density(a), rel=5e-3)),
              C("karate avg degree", "karate", {}, lambda o, s: has_num(o, 2 * 78 / 34, rel=5e-3))]
    elif name == "greedy_coloring":
        def valid_count(o, g):
            n = [x for x in numbers(o) if x == int(x) and 1 <= x <= 20]
            lo = max(len(cq) for cq in nx.find_cliques(g))
            hi = max(nx.greedy_color(g, "largest_first").values()) + 1
            return any(lo <= x <= hi for x in n)
        T += [C("karate colour count", "karate", {}, lambda o, s: valid_count(o, k)),
              C("lesmis colour count", "lesmis", {}, lambda o, s: valid_count(o, l)),
              C("airports runs", "airports", {}, ok)]
    elif name == "k_core":
        cn = nx.core_number(k)
        T += [C("karate max core", "karate", {"top_k": 34}, lambda o, s: has_num(o, max(cn.values()))),
              C("karate densest core nodes", "karate", {"top_k": 34}, lambda o, s: rg_nodes(s, o) == set(nx.k_core(k).nodes())),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "label_propagation":
        T += [C("karate runs + subgraph", "karate", {"top_k": 10}, lambda o, s: ok(o) and rg_nodes(s, o) is not None),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "louvain":
        def good_mod(o, g, lo, hi):
            return ok(o) and any(lo <= x <= hi for x in numbers(o))
        T += [C("karate modularity plausible", "karate", {"top_k": 10}, lambda o, s: good_mod(o, k, 0.37, 0.45) and rg_nodes(s, o)),
              C("lesmis modularity plausible", "lesmis", {"top_k": 10}, lambda o, s: good_mod(o, l, 0.5, 0.6)),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "girvan_newman":
        split = sorted(len(x) for x in next(nx.community.girvan_newman(k)))
        T += [C("karate first split sizes", "karate", {"num_communities": 2, "top_k": 10}, lambda o, s: all(has_num(o, x) for x in split)),
              C("lesmis runs", "lesmis", {"num_communities": 3, "top_k": 10}, ok)]
    elif name == "link_prediction":
        aa = sorted(nx.adamic_adar_index(k), key=lambda t: -t[2])
        top = {frozenset(aa[0][:2])}
        T += [C("karate top Adamic-Adar pair", "karate", {"top_k": 5}, lambda o, s: ok(o) and has_num(o, aa[0][2])),
              C("airports runs", "airports", {"top_k": 5, "node": "MAA"}, ok)]
    elif name == "max_flow_min_cut":
        kd = nx.DiGraph(); [kd.add_edge(u, v, capacity=1) or kd.add_edge(v, u, capacity=1) for u, v in k.edges()]
        cd = nx.DiGraph(); [cd.add_edge(u, v, capacity=1) for u, v in c.edges()]
        src, snk = "CS101", max(nx.descendants(c, "CS101"), key=lambda n: len(nx.ancestors(c, n)))
        T += [C("karate m1->m34 flow", "karate", {"source": "m1", "sink": "m34"}, lambda o, s: has_num(o, nx.maximum_flow_value(kd, "m1", "m34"))),
              C("courses flow", "courses", {"source": src, "sink": snk}, lambda o, s: has_num(o, nx.maximum_flow_value(cd, src, snk))),
              C("airports runs", "airports", {"source": "MAA", "sink": "LHR"}, ok)]
    elif name == "maximal_cliques":
        mx = max(len(q) for q in nx.find_cliques(k))
        T += [C("karate max clique size", "karate", {"top_k": 10}, lambda o, s: has_num(o, mx)),
              C("karate largest clique is a clique", "karate", {"top_k": 10}, lambda o, s: (lambda n: n is not None and len(n) == mx and all(k.has_edge(x, y) for x in n for y in n if x != y))(rg_nodes(s, o))),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "minimum_spanning_tree":
        T += [C("lesmis total weight", "lesmis", {"top_k": 10}, lambda o, s: has_num(o, nx.minimum_spanning_tree(l).size(weight="weight"))),
              C("karate edges", "karate", {"top_k": 10}, lambda o, s: has_num(o, 33)),
              C("lesmis subgraph is a tree", "lesmis", {"top_k": 10}, lambda o, s: rg_nodes(s, o) == V["lesmis"])]
    elif name == "motif_count":
        tri = sum(nx.triangles(k).values()) // 3
        T += [C("karate triangles", "karate", {"top_k": 10}, lambda o, s: has_num(o, tri)),
              C("lesmis triangles", "lesmis", {"top_k": 10}, lambda o, s: has_num(o, sum(nx.triangles(l).values()) // 3)),
              C("airports runs", "airports", {"top_k": 5}, ok)]
    elif name == "node_embedding_similarity":
        T += [C("karate m1 runs", "karate", {"node": "m1", "top_k": 5}, lambda o, s: ok(o) and len(ids_in_order(o, V["karate"])) >= 3),
              C("airports MAA runs", "airports", {"node": "MAA", "top_k": 5}, ok)]
    elif name == "node_similarity":
        a1, b1 = set(k["m1"]), set(k["m34"])
        T += [C("karate m1/m34 common", "karate", {"node": "m1", "node2": "m34"}, lambda o, s: has_num(o, len(a1 & b1))),
              C("karate m1/m34 jaccard", "karate", {"node": "m1", "node2": "m34"}, lambda o, s: has_num(o, len(a1 & b1) / len(a1 | b1))),
              C("bad node -> error", "karate", {"node": "zzz", "node2": "m1"}, lambda o, s: o.get("status") == "error")]
    elif name == "reachability":
        leaf = [n for n in c if c.out_degree(n) == 0][0]
        T += [C("courses reachable", "courses", {"source": "CS101", "target": max(nx.descendants(c, "CS101"))}, lambda o, s: True in bools(o) and False not in bools(o)[:1]),
              C("courses unreachable", "courses", {"source": leaf, "target": "CS101"}, lambda o, s: bools(o)[:1] == [False]),
              C("airports MAA->LIS", "airports", {"source": "MAA", "target": "LIS"}, lambda o, s: bools(o)[:1] == [True])]
    elif name == "shortest_path_unweighted":
        T += [C("airports MAA->LIS hops", "airports", {"source": "MAA", "target": "LIS"}, lambda o, s: has_num(o, nx.shortest_path_length(a, "MAA", "LIS"))),
              C("karate m17->m26 hops", "karate", {"source": "m17", "target": "m26"}, lambda o, s: has_num(o, nx.shortest_path_length(k, "m17", "m26"))),
              C("bad node -> error", "karate", {"source": "zzz", "target": "m1"}, lambda o, s: o.get("status") == "error")]
    elif name == "shortest_path_weighted":
        T += [C("airports MAA->LIS km", "airports", {"source": "MAA", "target": "LIS"}, lambda o, s: has_num(o, nx.shortest_path_length(a, "MAA", "LIS", weight="weight"))),
              C("lesmis c1->c70", "lesmis", {"source": "c01", "target": "c70"}, lambda o, s: has_num(o, nx.shortest_path_length(l, "c01", "c70", weight="weight"))),
              C("bad node -> error", "karate", {"source": "zzz", "target": "m1"}, lambda o, s: o.get("status") == "error")]
    elif name == "topological_sort":
        def valid_order(o):
            order = ids_in_order(o, V["courses"])
            pos = {n: i for i, n in enumerate(order)}
            return len(order) >= 15 and all(pos[u] < pos[v] for u, v in c.edges() if u in pos and v in pos)
        T += [C("courses valid order", "courses", {"top_k": 100}, lambda o, s: valid_order(o)),
              C("airports (cyclic) handled", "airports", {"top_k": 10}, lambda o, s: isinstance(o, dict) and "_crash" not in o)]
    elif name == "bipartite_check":
        T += [C("projects is bipartite", "projects", {}, lambda o, s: bools(o)[:1] == [True]),
              C("karate is not", "karate", {}, lambda o, s: bools(o)[:1] == [False] or (o.get("status") == "ok" and "not" in o["summary"].lower())),
              C("airports runs", "airports", {}, lambda o, s: isinstance(o, dict))]
    elif name == "bipartite_matching":
        size = len(nx.bipartite.hopcroft_karp_matching(p, [n for n in p if n.startswith("S")])) // 2
        T += [C("projects matching size", "projects", {"top_k": 100}, lambda o, s: has_num(o, size)),
              C("karate -> error", "karate", {}, lambda o, s: o.get("status") == "error")]
    elif name == "articulation_points_bridges":
        ap = set(nx.articulation_points(l))
        T += [C("lesmis articulation points", "lesmis", {"top_k": 100}, lambda o, s: ap <= set(ids_in_order(o, V["lesmis"]))),
              C("lesmis bridge count", "lesmis", {"top_k": 100}, lambda o, s: has_num(o, len(list(nx.bridges(l))))),
              C("airports runs", "airports", {"top_k": 10}, ok)]
    # generic robustness for all
    T.append(C("unknown graph -> error", "no_such_graph", {}, lambda o, s: o.get("status") == "error"))
    return T

# ---------------------------------------------------------------- driver
def evaluate(name, impl):
    folder = CAND / name / impl
    try:
        tool = load_tool(folder)
    except Exception as e:
        return {"impl": impl, "score": 0, "total": 0, "error": f"load: {e}"}
    res, total_time = [], 0.0
    for chk in checks_for(name):
        store = GraphStore()
        try:
            passed = chk(tool, store)
        except Exception:
            passed = False
        secs = getattr(chk, "secs", 0.0)
        if chk.gid == "airports": total_time = max(total_time, secs)
        res.append({"check": chk.desc, "pass": passed, "secs": round(secs, 3)})
    det = True
    try:
        o1, _ = call(tool, GraphStore(), "karate", top_k=5, node="m1", node2="m34", source="m1", target="m34", k=2)
        o2, _ = call(tool, GraphStore(), "karate", top_k=5, node="m1", node2="m34", source="m1", target="m34", k=2)
        strip = lambda o: json.dumps({k2: v for k2, v in o.items() if k2 != "result_graph_id"}, sort_keys=True, default=str)
        det = strip(o1) == strip(o2)
    except Exception:
        pass
    desc_len = len(tool.manifest.get("description", ""))
    n_params = len(tool.manifest["parameters"]["properties"])
    return {"impl": impl, "score": sum(r["pass"] for r in res), "total": len(res), "deterministic": det,
            "max_airports_secs": round(total_time, 3), "desc_len": desc_len, "n_params": n_params, "checks": res}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()
    report = {}
    for name in sorted(os.listdir(CAND)):
        if args.only and name != args.only: continue
        rows = [evaluate(name, impl) for impl in sorted(os.listdir(CAND / name))]
        # Ties: deterministic first, then not slow (>10 s), then more parameters (more flexible
        # for the planner), then a fuller description, then id - so the choice is stable run to run.
        rows.sort(key=lambda r: (-r["score"], not r.get("deterministic", True), r.get("max_airports_secs", 99) > 10,
                                 -r.get("n_params", 0), -r.get("desc_len", 0), r["impl"]))
        report[name] = rows
        print(f"{name:32s} winner {rows[0]['impl']} {rows[0]['score']}/{rows[0]['total']}  |  " +
              "  ".join(f"{r['impl']}:{r['score']}" for r in rows[1:]), flush=True)
    out = Path(__file__).parent
    if not args.only:
        (out / "report.json").write_text(json.dumps(report, indent=1))
        lines = ["# Tool curation report", "", "| tool | chosen | score | airports s | others |", "|---|---|---|---|---|"]
        for n, rows in report.items():
            w = rows[0]
            lines.append(f"| {n} | {w['impl']} | {w['score']}/{w['total']} | {w.get('max_airports_secs')} | " +
                         ", ".join(f"{r['impl']} {r['score']}/{r['total']}" for r in rows[1:]) + " |")
        lines += ["", "Failed checks of chosen implementations:", ""]
        for n, rows in report.items():
            bad = [c["check"] for c in rows[0].get("checks", []) if not c["pass"]]
            if bad: lines.append(f"- **{n}** ({rows[0]['impl']}): " + "; ".join(bad))
        (out / "report.md").write_text("\n".join(lines) + "\n")
    if args.install:
        dest = ROOT / "registry" / "tools"
        for n, rows in report.items():
            shutil.rmtree(dest / n, ignore_errors=True)
            shutil.copytree(CAND / n / rows[0]["impl"], dest / n / rows[0]["impl"])
        print("installed", len(report), "tools into", dest)

if __name__ == "__main__":
    main()
