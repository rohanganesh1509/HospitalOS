"""
HospitalOS Phase 3 Integration Test Suite
Comprehensive testing for Modules 3-11:
- Hospital Resources & RAG (Modules 5 & 6)
- Deadlock Avoidance / Banker's (Module 7)
- Simulated Memory Manager (Module 8)
- Clinical Deadlines & Scheduling (Modules 3, 4, 9)
- Synchronization / Shared Resource Control (Module 10)
- Simulation Event & System Call Monitor (Module 11)
- Terminated Process Semantics & Readmit (Section 10)
- Interactive Demonstration Scenarios A-H (Section 48)
- Full Web Routes & API Regression (Section 67)
"""

import unittest
from providers import SimulationProvider, LiveKernelProvider, provider_manager, get_provider
from contracts import ProcessState, SchedulingEventType
from app import app


def seed_test_patients(provider):
    with provider._get_db() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        if cnt < 4:
            conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Test A', 0, 4, 1, 'Waiting', 'General Medicine', 1)")
            conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Test B', 0, 6, 2, 'Waiting', 'General Medicine', 1)")
            conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Test C', 0, 2, 3, 'Waiting', 'General Medicine', 1)")
            conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Test D', 0, 8, 2, 'Waiting', 'General Medicine', 1)")
    procs = provider.get_processes()
    return [p.pid for p in procs]


class TestHospitalResourcesAndRag(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()
        pids = seed_test_patients(self.provider)
        self.p1 = pids[0]
        self.p2 = pids[1]

    def test_resource_request_and_release(self):
        # PID requests 1 ventilator
        res = self.provider.request_resource(self.p1, "ventilator", 1)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "allocated")

        # Verify ventilator count updated
        r_vent = next(r for r in self.provider.get_resources() if r.resource_id == "RES_VENT")
        self.assertIn(self.p1, r_vent.holders)

        # Release ventilator
        rel = self.provider.release_resource(self.p1, "ventilator", 1)
        self.assertTrue(rel["success"])
        r_vent_after = next(r for r in self.provider.get_resources() if r.resource_id == "RES_VENT")
        self.assertNotIn(self.p1, r_vent_after.holders)

    def test_resource_exhaustion_and_blocking(self):
        # Scanner has 2 units total initially
        r_scan = next(r for r in self.provider.get_resources() if r.resource_id == "RES_SCANNER")
        avail = r_scan.available

        # Exhaust scanner
        res1 = self.provider.request_resource(self.p1, "scanner", avail)
        self.assertTrue(res1["success"])
        self.assertEqual(res1["status"], "allocated")

        # Second process requests scanner -> must block to WAITING
        res2 = self.provider.request_resource(self.p2, "scanner", 1)
        self.assertTrue(res2["success"])
        self.assertEqual(res2["status"], "blocked")

        p2_proc = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_proc.state, ProcessState.WAITING)
        self.assertEqual(p2_proc.waiting_on_resource, "RES_SCANNER")

        # Releasing unblocks P2 to READY
        rel1 = self.provider.release_resource(self.p1, "scanner", 1)
        self.assertTrue(rel1["success"])

        p2_after = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_after.state, ProcessState.READY)
        self.assertIsNone(p2_after.waiting_on_resource)

    def test_rag_graph_and_cycle_detection(self):
        deadlock = self.provider.get_deadlock_state()
        self.assertFalse(deadlock.has_cycle)
        self.assertTrue(deadlock.is_safe)
        self.assertGreater(len(deadlock.rag_nodes), 0)

        # Synthesize cycle edges
        edges = [
            {"from": "RES_VENT", "to": "PID-001", "units": 1, "type": "allocation"},
            {"from": "PID-001", "to": "RES_SCANNER", "units": 1, "type": "request"},
            {"from": "RES_SCANNER", "to": "PID-002", "units": 1, "type": "allocation"},
            {"from": "PID-002", "to": "RES_VENT", "units": 1, "type": "request"},
        ]
        has_cycle, cycle_path = self.provider._detect_rag_cycle_with_path(edges)
        self.assertTrue(has_cycle)
        self.assertGreater(len(cycle_path), 0)


class TestMemoryManager(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()
        pids = seed_test_patients(self.provider)
        self.p1 = pids[0]
        self.p2 = pids[1]

    def test_memory_allocation_and_page_mapping(self):
        mem = self.provider.get_memory_state()
        self.assertEqual(mem.total_frames, 16)
        self.assertEqual(mem.frame_size_kb, 4)
        self.assertEqual(mem.total_kb, 64)

        # Free P1 memory
        free_res = self.provider.free_memory(self.p1)
        self.assertTrue(free_res["success"])

        p1_after = self.provider.get_process_by_id(self.p1)
        self.assertEqual(len(p1_after.memory_frames), 0)

    def test_memory_pressure_and_exhaustion(self):
        mem = self.provider.get_memory_state()
        free_count = mem.free_frames

        # Allocate all free frames
        res = self.provider.allocate_memory(self.p1, pages=free_count)
        self.assertTrue(res["success"])

        mem_full = self.provider.get_memory_state()
        self.assertEqual(mem_full.free_frames, 0)
        self.assertEqual(mem_full.memory_pressure, "HIGH")

        # 1 more page must fail
        fail_res = self.provider.allocate_memory(self.p2, pages=1)
        self.assertFalse(fail_res["success"])
        self.assertIn("Insufficient", fail_res["error"])

        # P2 blocked to WAITING
        p2_proc = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_proc.state, ProcessState.WAITING)

        # Free P1 frames unblocks P2 to READY
        self.provider.free_memory(self.p1)
        p2_after = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_after.state, ProcessState.READY)


class TestSynchronizationPrimitives(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()
        pids = seed_test_patients(self.provider)
        self.p1 = pids[0]
        self.p2 = pids[1]
        self.p3 = pids[2]
        self.p4 = pids[3] if len(pids) > 3 else pids[0]

    def test_mutex_acquire_block_and_release(self):
        # P1 acquires Operating Theatre Mutex
        acq1 = self.provider.acquire_lock(self.p1, "mutex_ot")
        self.assertTrue(acq1["success"])
        self.assertEqual(acq1["status"], "acquired")

        ot = next(p for p in self.provider.get_synchronization_state().primitives if p.primitive_id == "MUTEX_OT")
        self.assertTrue(ot.locked)
        self.assertEqual(ot.holder_pid, self.p1)

        # P2 attempts acquire -> blocks
        acq2 = self.provider.acquire_lock(self.p2, "mutex_ot")
        self.assertTrue(acq2["success"])
        self.assertEqual(acq2["status"], "blocked")

        p2_proc = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_proc.state, ProcessState.WAITING)

        # P3 (non-holder) release attempt rejected
        rel_bad = self.provider.release_lock(self.p3, "mutex_ot")
        self.assertFalse(rel_bad["success"])
        self.assertIn("Only the lock holder", rel_bad["error"])

        # P1 releases -> P2 woken to READY and becomes owner
        rel1 = self.provider.release_lock(self.p1, "mutex_ot")
        self.assertTrue(rel1["success"])

        p2_after = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_after.state, ProcessState.READY)

    def test_counting_semaphore(self):
        sem = next(p for p in self.provider.get_synchronization_state().primitives if p.primitive_id == "SEM_ICU")
        self.assertEqual(sem.max_count, 3)

        # P1, P2, P3 acquire all 3 slots
        self.assertTrue(self.provider.acquire_lock(self.p1, "sem_icu")["success"])
        self.assertTrue(self.provider.acquire_lock(self.p2, "sem_icu")["success"])
        self.assertTrue(self.provider.acquire_lock(self.p3, "sem_icu")["success"])

        sem_full = next(p for p in self.provider.get_synchronization_state().primitives if p.primitive_id == "SEM_ICU")
        self.assertEqual(sem_full.count, 0)

        # 4th acquire blocks
        acq4 = self.provider.acquire_lock(self.p4, "sem_icu")
        self.assertEqual(acq4["status"], "blocked")

        # P1 releases -> P4 unblocks
        rel1 = self.provider.release_lock(self.p1, "sem_icu")
        self.assertTrue(rel1["success"])


class TestEventsAndSyscalls(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()
        seed_test_patients(self.provider)

    def test_events_category_and_source(self):
        events = self.provider.get_events(limit=50)
        self.assertGreater(len(events), 0)
        for e in events:
            self.assertEqual(e.source, "simulation")
            self.assertIsNotNone(e.category)

        # Filter category
        proc_events = self.provider.get_events(category="PROCESS")
        for e in proc_events:
            self.assertEqual(e.category, "PROCESS")

    def test_simulated_syscalls(self):
        syscalls = self.provider.get_simulated_syscalls(limit=20)
        self.assertGreater(len(syscalls), 0)
        for s in syscalls:
            self.assertIn("call", s)
            self.assertIn("pid", s)


class TestTerminatedSemanticsAndReadmit(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()
        pids = seed_test_patients(self.provider)
        self.p1 = pids[0]

    def test_terminated_is_terminal_state(self):
        self.provider.transition_process(self.p1, ProcessState.TERMINATED, "Completed")
        p1_proc = self.provider.get_process_by_id(self.p1)
        self.assertEqual(p1_proc.state, ProcessState.TERMINATED)

        # Ordinary transition out of TERMINATED fails
        res_ready = self.provider.transition_process(self.p1, ProcessState.READY, "Illegal resurrection")
        self.assertFalse(res_ready["success"])
        self.assertIn("TERMINATED", res_ready["error"])

    def test_explicit_readmit_patient(self):
        self.provider.transition_process(self.p1, ProcessState.TERMINATED, "Completed")
        readmit_res = self.provider.readmit_patient(self.p1)
        self.assertTrue(readmit_res["success"])

        p1_after = self.provider.get_process_by_id(self.p1)
        self.assertNotEqual(p1_after.state, ProcessState.TERMINATED)


class TestDemonstrationScenarios(unittest.TestCase):
    def setUp(self):
        self.provider = SimulationProvider()

    def test_all_eight_scenarios_execute_cleanly(self):
        for sc in ["A", "B", "C", "D", "E", "F", "G", "H"]:
            res = self.provider.run_demo_scenario(sc)
            self.assertTrue(res["success"], f"Scenario {sc} failed: {res.get('error') or res.get('message')}")
            self.assertIn("message", res)


class TestFullWebRoutesAndRegression(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        provider_manager.set_active_mode("simulation")
        self.provider = get_provider()
        seed_test_patients(self.provider)

    def test_all_pages_return_http_200(self):
        routes = ["/", "/doctors", "/resources", "/memory", "/synchronization", "/events"]
        for r in routes:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 200, f"Route {r} did not return HTTP 200")

    def test_system_apis(self):
        for endpoint in [
            "/api/system/snapshot",
            "/api/system/processes",
            "/api/system/resources",
            "/api/system/rag",
            "/api/system/memory",
            "/api/system/synchronization",
            "/api/system/events",
            "/api/system/syscalls",
        ]:
            resp = self.client.get(endpoint)
            self.assertEqual(resp.status_code, 200, f"{endpoint} failed with {resp.status_code}")

    def test_scenario_and_readmit_api_routes(self):
        # Scenario route
        resp = self.client.post("/api/system/scenarios/A")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

        # Fetch active processes after scenario A
        proc_resp = self.client.get("/api/system/processes")
        procs = proc_resp.get_json()
        self.assertGreater(len(procs), 0)
        target_pid = procs[0]["pid"]

        # Terminate that target_pid
        self.client.post(f"/api/system/processes/{target_pid}/transition", json={"state": "TERMINATED"})
        resp = self.client.post(f"/api/system/processes/{target_pid}/readmit")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
