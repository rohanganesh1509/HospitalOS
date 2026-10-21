"""HospitalOS Deadlock Avoidance & RAG Analysis.

Constructs bipartite Resource Allocation Graphs (Processes <-> Resources),
detects request/allocation cycles using Depth-First Search, and executes
Dijkstra's Banker's Algorithm for deadlock avoidance.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
from contracts import DeadlockState, HospitalResource, HospitalProcess, ProcessState
from algorithms import bankers_algorithm
from .event_bus import KernelEventBus


class DeadlockManager:
    """Subsystem managing Resource Allocation Graphs and Banker's Deadlock Avoidance."""

    def __init__(self, event_bus: KernelEventBus):
        self._event_bus = event_bus

    def detect_rag_cycle_with_path(self, edges: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Runs Depth-First Search on the Resource Allocation Graph and returns cycle path if any."""
        adj: Dict[str, List[str]] = {}
        nodes = set()
        for e in edges:
            u = e["from"]
            v = e["to"]
            adj.setdefault(u, []).append(v)
            nodes.add(u)
            nodes.add(v)

        visited = set()
        rec_stack = []

        def dfs(node: str) -> Optional[List[str]]:
            visited.add(node)
            rec_stack.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    cycle = dfs(neighbor)
                    if cycle:
                        return cycle
                elif neighbor in rec_stack:
                    idx = rec_stack.index(neighbor)
                    return rec_stack[idx:] + [neighbor]
            rec_stack.pop()
            return None

        for node in sorted(nodes):
            if node not in visited:
                cycle = dfs(node)
                if cycle:
                    return True, cycle
        return False, []

    def get_deadlock_state(
        self,
        resources: List[HospitalResource],
        processes: List[HospitalProcess],
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        """Calculates Banker's safety state and builds the live Resource Allocation Graph."""
        active_resources = [r for r in resources if r.resource_id != "RES_DOC"]
        resource_names = [r.name for r in active_resources]
        avail_vector = available if available is not None else [r.available for r in active_resources]

        active_procs = [p for p in processes if getattr(p, "state", "") != "TERMINATED"]
        if not active_procs:
            # Fallback to canonical default matrices for demonstration if no active processes
            alloc = allocation if allocation is not None else [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]]
            max_m = maximum if maximum is not None else [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]]
            avail = available if available is not None else [3, 3, 2]
            p_names = [f"P{i}" for i in range(len(alloc))]
            r_names = ["ICU Beds", "Ventilators", "Patient Monitors"]
        else:
            p_names = [f"PID-{p.pid:03d}" for p in active_procs]
            r_names = resource_names
            alloc = []
            max_m = []
            for p in active_procs:
                row_alloc = []
                row_max = []
                for r in active_resources:
                    held = p.assigned_resources.get(r.name, 0)
                    row_alloc.append(held)
                    # Deterministic claim based on priority and duration
                    if r.resource_id == "RES_ICU":
                        claim = max(held, 2 if p.priority == 1 else (1 if p.priority == 2 else 0))
                    elif r.resource_id == "RES_VENT":
                        claim = max(held, 1 if p.priority <= 2 else 0)
                    elif r.resource_id == "RES_MONITOR":
                        claim = max(held, 1)
                    else:
                        claim = max(held, 1 if p.burst_time >= 5 else 0)
                    row_max.append(claim)
                alloc.append(row_alloc)
                max_m.append(row_max)
            avail = avail_vector

        # Override with explicit parameters if supplied
        if allocation is not None:
            alloc = allocation
        if maximum is not None:
            max_m = maximum
        if available is not None:
            avail = available

        # Build live RAG edges
        rag_edges: List[Dict[str, Any]] = []
        for i, p_name in enumerate(p_names):
            for j, r_name in enumerate(r_names):
                if i < len(alloc) and j < len(alloc[i]) and alloc[i][j] > 0:
                    rag_edges.append({
                        "from": r_name, "to": p_name,
                        "units": alloc[i][j], "type": "allocation"
                    })
        # Request edges from resource wait queues
        for r in active_resources:
            for w_pid in r.wait_queue:
                rag_edges.append({
                    "from": f"PID-{w_pid:03d}", "to": r.name,
                    "units": 1, "type": "request"
                })

        # Build RAG nodes
        rag_nodes: List[Dict[str, Any]] = []
        for p in active_procs:
            st = p.state.value if hasattr(p.state, 'value') else str(p.state)
            rag_nodes.append({
                "id": f"PID-{p.pid:03d}",
                "label": p.name,
                "type": "process",
                "state": st,
            })
        if not rag_nodes:
            for p_name in p_names:
                rag_nodes.append({
                    "id": p_name,
                    "label": p_name,
                    "type": "process",
                    "state": "READY",
                })
        for r in active_resources:
            rag_nodes.append({
                "id": r.name,
                "label": r.name,
                "type": "resource",
                "instances": {"total": r.total, "available": r.available, "allocated": r.allocated},
            })

        # Cycle detection on RAG
        cycle_detected, cycle_nodes = self.detect_rag_cycle_with_path(rag_edges)

        try:
            banker_res = bankers_algorithm(alloc, max_m, avail)
            safe = banker_res["safe"]
            safe_seq = [p_names[int(s[1:])] if s.startswith("P") and s[1:].isdigit() and int(s[1:]) < len(p_names) else s for s in banker_res["safe_sequence"]]
            need = banker_res["need"]
            msg = banker_res["message"]
        except Exception as exc:
            safe = False
            safe_seq = []
            need = []
            msg = f"Banker evaluation: {exc}"

        return DeadlockState(
            safe=safe,
            safe_sequence=safe_seq,
            allocation_matrix=alloc,
            maximum_matrix=max_m,
            need_matrix=need,
            available_vector=avail,
            resource_names=r_names[:len(avail)],
            process_names=p_names,
            cycle_detected=cycle_detected,
            rag_edges=rag_edges,
            rag_nodes=rag_nodes,
            cycle_nodes=cycle_nodes,
            message=msg,
        )
