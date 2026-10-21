"""Automated Tests for HospitalOS Phase 2 Integration.

Covers:
- Scheduling Algorithms: FCFS, SJF, SRTF (preemptive), Round Robin, Priority, Hospital Adaptive
- Clinical Deadlines & Emergency Preemption
- Scheduling Events & State Transitions
- Metrics Verification (waiting, turnaround, response, context switches, preemptions, deadline misses)
- Full Regression Test: Routes, Doctor Assignment, Banker's, Simulation vs Live Kernel separation
"""

import unittest
import json
from algorithms import (
    schedule_patients,
    calculate_deadline,
    calculate_adaptive_score,
    bankers_algorithm,
)
from contracts import (
    ProcessState,
    SchedulingEventType,
)
from app import app, provider_manager, get_db


class TestSchedulingAlgorithms(unittest.TestCase):
    """Unit tests for Phase 2 CPU scheduling engine."""

    def test_fcfs_arrival_order(self):
        patients = [
            {"id": 1, "name": "Patient A", "arrival_time": 0, "burst_time": 5, "priority": 3},
            {"id": 2, "name": "Patient B", "arrival_time": 2, "burst_time": 3, "priority": 1},
            {"id": 3, "name": "Patient C", "arrival_time": 10, "burst_time": 2, "priority": 2},
        ]
        res = schedule_patients(patients, algorithm="FCFS")
        self.assertEqual(res["algorithm"], "FCFS")
        # Arrival order execution: A (0-5), B (5-8), Idle (8-10), C (10-12)
        gantt_labels = [g["label"] for g in res["gantt"]]
        self.assertEqual(gantt_labels, ["Patient A", "Patient B", "Idle", "Patient C"])
        self.assertEqual(res["simulation_clock"], 12)
        # Verify waiting times: A=0, B=5-2=3, C=10-10=0
        res_map = {r["id"]: r for r in res["results"]}
        self.assertEqual(res_map[1]["waiting_time"], 0)
        self.assertEqual(res_map[2]["waiting_time"], 3)
        self.assertEqual(res_map[3]["waiting_time"], 0)

    def test_sjf_shortest_job_first(self):
        patients = [
            {"id": 1, "name": "Patient A", "arrival_time": 0, "burst_time": 8, "priority": 3},
            {"id": 2, "name": "Patient B", "arrival_time": 1, "burst_time": 4, "priority": 2},
            {"id": 3, "name": "Patient C", "arrival_time": 1, "burst_time": 2, "priority": 1},
        ]
        res = schedule_patients(patients, algorithm="SJF")
        # Non-preemptive: A starts at 0, finishes at 8.
        # At t=8, both B (burst 4) and C (burst 2) are available. C must run before B.
        gantt_labels = [g["label"] for g in res["gantt"]]
        self.assertEqual(gantt_labels, ["Patient A", "Patient C", "Patient B"])
        self.assertEqual(res["results"][1]["id"], 3)  # C is scheduled second

    def test_srtf_preemption(self):
        patients = [
            {"id": 1, "name": "Long Job", "arrival_time": 0, "burst_time": 8, "priority": 3},
            {"id": 2, "name": "Short Arrival", "arrival_time": 2, "burst_time": 2, "priority": 2},
        ]
        res = schedule_patients(patients, algorithm="SRTF")
        self.assertEqual(res["algorithm"], "SRTF")
        # Long Job runs from 0 to 2, then Short Arrival preempts it at t=2.
        # Short Arrival runs from 2 to 4. Long Job resumes at 4 to 10.
        self.assertEqual(len(res["gantt"]), 3)
        s1, s2, s3 = res["gantt"]
        self.assertEqual(s1["id"], 1)
        self.assertEqual(s1["start"], 0)
        self.assertEqual(s1["end"], 2)
        self.assertIn(s1["slice_type"], ("preempted", "emergency_preempt"))
        self.assertEqual(s1["remaining_after"], 6)

        self.assertEqual(s2["id"], 2)
        self.assertEqual(s2["start"], 2)
        self.assertEqual(s2["end"], 4)
        self.assertEqual(s2["slice_type"], "completed")

        self.assertEqual(s3["id"], 1)
        self.assertEqual(s3["start"], 4)
        self.assertEqual(s3["end"], 10)
        self.assertEqual(s3["slice_type"], "completed")

        # Verify preemption was recorded
        self.assertGreaterEqual(res["total_preemptions"], 1)
        preempt_events = [e for e in res["events"] if e["event_type"] in ("PREEMPT", "EMERGENCY_PREEMPT")]
        self.assertGreaterEqual(len(preempt_events), 1)

    def test_round_robin_slicing_and_rotation(self):
        patients = [
            {"id": 1, "name": "P1", "arrival_time": 0, "burst_time": 5, "priority": 2},
            {"id": 2, "name": "P2", "arrival_time": 0, "burst_time": 3, "priority": 2},
        ]
        res = schedule_patients(patients, algorithm="ROUND_ROBIN", quantum=2)
        self.assertEqual(res["algorithm"], "ROUND_ROBIN")
        self.assertEqual(res["quantum"], 2)
        # Slices:
        # P1: 0-2 (rem: 3) -> QUANTUM_EXPIRE
        # P2: 2-4 (rem: 1) -> QUANTUM_EXPIRE
        # P1: 4-6 (rem: 1) -> QUANTUM_EXPIRE
        # P2: 6-7 (rem: 0) -> COMPLETE
        # P1: 7-8 (rem: 0) -> COMPLETE
        gantt_pids = [g["pid"] for g in res["gantt"]]
        self.assertEqual(gantt_pids, [1, 2, 1, 2, 1])
        slice_types = [g["slice_type"] for g in res["gantt"]]
        self.assertEqual(slice_types, ["quantum_expired", "quantum_expired", "quantum_expired", "completed", "completed"])

    def test_round_robin_invalid_quantum(self):
        patients = [{"id": 1, "name": "P1", "arrival_time": 0, "burst_time": 4, "priority": 2}]
        with self.assertRaises(ValueError):
            schedule_patients(patients, algorithm="ROUND_ROBIN", quantum=0)
        with self.assertRaises(ValueError):
            schedule_patients(patients, algorithm="ROUND_ROBIN", quantum=-3)

    def test_priority_emergency_convention(self):
        # Priority convention: 1 is highest priority.
        patients = [
            {"id": 1, "name": "Routine", "arrival_time": 0, "burst_time": 4, "priority": 4},
            {"id": 2, "name": "Emergency", "arrival_time": 0, "burst_time": 2, "priority": 1},
            {"id": 3, "name": "Urgent", "arrival_time": 0, "burst_time": 3, "priority": 2},
        ]
        res = schedule_patients(patients, algorithm="PRIORITY")
        self.assertEqual(res["algorithm"], "PRIORITY")
        # Ordering must be Emergency (P1) -> Urgent (P2) -> Routine (P4)
        order = [r["id"] for r in res["results"]]
        self.assertEqual(order, [2, 3, 1])

    def test_emergency_preemption(self):
        # Code Red Emergency arrives at t=2 while Priority 3 process is executing
        patients = [
            {"id": 1, "name": "Routine Patient", "arrival_time": 0, "burst_time": 6, "priority": 3},
            {"id": 2, "name": "Emergency Code Red", "arrival_time": 2, "burst_time": 3, "priority": 1},
        ]
        res = schedule_patients(patients, algorithm="PRIORITY")
        # Routine Patient should be preempted at t=2
        s1 = res["gantt"][0]
        self.assertEqual(s1["id"], 1)
        self.assertEqual(s1["end"], 2)
        self.assertEqual(s1["slice_type"], "emergency_preempt")

        s2 = res["gantt"][1]
        self.assertEqual(s2["id"], 2)
        self.assertEqual(s2["start"], 2)
        self.assertEqual(s2["end"], 5)
        self.assertEqual(s2["slice_type"], "completed")

        # Verify explicit EMERGENCY_PREEMPT event
        emerg_events = [e for e in res["events"] if e["event_type"] == "EMERGENCY_PREEMPT"]
        self.assertEqual(len(emerg_events), 1)
        self.assertEqual(emerg_events[0]["source"], "simulation")
        self.assertEqual(emerg_events[0]["simulation_time"], 2)

    def test_hospital_adaptive_scoring_and_deterministic_execution(self):
        # Candidate A: waited 10 units, Priority 3
        # Candidate B: just arrived, Priority 1
        p_waiting = {"id": 1, "priority": 3, "arrival_time": 0, "burst_time": 6, "remaining_time": 6, "deadline": None}
        p_new_emerg = {"id": 2, "priority": 1, "arrival_time": 10, "burst_time": 4, "remaining_time": 4, "deadline": 18}

        score_a = calculate_adaptive_score(p_waiting, current_time=10)
        score_b = calculate_adaptive_score(p_new_emerg, current_time=10)

        # Both scores must be deterministically reproducible floats
        self.assertIsInstance(score_a, float)
        self.assertIsInstance(score_b, float)
        # Score calculation check:
        # A: 30 (urgency) + 10*3 (aging) + (15-6)*2 (short) + 0 = 30 + 30 + 18 = 78
        self.assertEqual(score_a, 78.0)
        # B: 100 (urgency) + 0*3 (aging) + (15-4)*2 (short: 22) + 10 (deadline bonus) = 132
        self.assertEqual(score_b, 132.0)

        # Run scheduler with adaptive
        patients = [p_waiting, p_new_emerg]
        res = schedule_patients(patients, algorithm="HOSPITAL_ADAPTIVE", quantum=2)
        self.assertEqual(res["algorithm"], "HOSPITAL_ADAPTIVE")
        self.assertGreater(len(res["gantt"]), 0)

    def test_clinical_deadlines_met_and_missed(self):
        # Deterministic formula: P1: arr + burst * 2, P2: arr + burst * 3, P3: None
        self.assertEqual(calculate_deadline(0, 4, 1), 8)
        self.assertEqual(calculate_deadline(5, 3, 2), 14)
        self.assertIsNone(calculate_deadline(0, 5, 3))

        # Test deadline miss scenario:
        # P1 has burst 10 (arr 0), P2 is P1 emergency (arr 0, burst 3, deadline 6)
        # If FCFS executes long job P1 first, P2 will miss deadline
        patients = [
            {"id": 1, "name": "Long Normal", "arrival_time": 0, "burst_time": 10, "priority": 3},
            {"id": 2, "name": "Urgent Patient", "arrival_time": 0, "burst_time": 4, "priority": 2},
        ]
        # In FCFS: P1 runs 0-10, P2 runs 10-14.
        # P2 deadline is arrival(0) + burst(4)*3 = 12. P2 completes at 14 > 12 -> Missed!
        res = schedule_patients(patients, algorithm="FCFS")
        p2_res = next(r for r in res["results"] if r["id"] == 2)
        self.assertEqual(p2_res["deadline"], 12)
        self.assertEqual(p2_res["deadline_status"], "missed")
        self.assertEqual(res["total_deadline_misses"], 1)

        miss_events = [e for e in res["events"] if e["event_type"] == "DEADLINE_MISS"]
        self.assertEqual(len(miss_events), 1)

    def test_metrics_integrity(self):
        patients = [
            {"id": 1, "name": "P1", "arrival_time": 0, "burst_time": 4, "priority": 2},
            {"id": 2, "name": "P2", "arrival_time": 2, "burst_time": 2, "priority": 1},
        ]
        res = schedule_patients(patients, algorithm="SRTF")
        # Total time = 6
        # P1: 0-2 (2u), P2: 2-4 (2u, finished), P1: 4-6 (2u, finished)
        # P1: start 0, completion 6, waiting = 6 - 0 - 4 = 2, turnaround = 6, response = 0
        # P2: start 2, completion 4, waiting = 4 - 2 - 2 = 0, turnaround = 2, response = 0
        p1 = next(r for r in res["results"] if r["id"] == 1)
        p2 = next(r for r in res["results"] if r["id"] == 2)
        self.assertEqual(p1["waiting_time"], 2)
        self.assertEqual(p1["turnaround_time"], 6)
        self.assertEqual(p1["response_time"], 0)

        self.assertEqual(p2["waiting_time"], 0)
        self.assertEqual(p2["turnaround_time"], 2)
        self.assertEqual(p2["response_time"], 0)

        self.assertEqual(res["average_waiting_time"], 1.0)
        self.assertEqual(res["average_turnaround_time"], 4.0)
        self.assertEqual(res["average_response_time"], 0.0)
        self.assertEqual(res["cpu_utilization"], 100.0)


class TestWebRoutesAndRegression(unittest.TestCase):
    """Integration & regression tests for Flask web endpoints and provider layer."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_pages_load(self):
        # 1. Dashboard
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"CPU scheduling simulator", r.data)
        self.assertIn(b"PROCESS SCHEDULING", r.data)

        # 2. Doctors
        r = self.client.get("/doctors")
        self.assertEqual(r.status_code, 200)

        # 3. Resources / Deadlock
        r = self.client.get("/resources")
        self.assertEqual(r.status_code, 200)

    def test_api_schedule_endpoints(self):
        # Test full simulation
        r = self.client.post("/api/schedule", json={"algorithm": "SRTF"})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["algorithm"], "SRTF")
        self.assertIn("gantt", data)
        self.assertIn("events", data)

        # Test Round Robin with quantum
        r = self.client.post("/api/schedule", json={"algorithm": "ROUND_ROBIN", "quantum": 3})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["algorithm"], "ROUND_ROBIN")
        self.assertEqual(data["quantum"], 3)

        # Test invalid algorithm
        r = self.client.post("/api/schedule", json={"algorithm": "NONEXISTENT"})
        self.assertEqual(r.status_code, 400)

        # Test invalid quantum
        r = self.client.post("/api/schedule", json={"algorithm": "ROUND_ROBIN", "quantum": -1})
        self.assertEqual(r.status_code, 400)

    def test_api_schedule_step_and_reset(self):
        # Admit a patient so there is an active process to schedule and step
        self.client.post("/patients", data={
            "name": "Step Test Patient",
            "arrival_time": "0",
            "burst_time": "4",
            "priority": "2",
            "specialization": "General Medicine"
        })

        # Test step
        r = self.client.post("/api/schedule/step", json={"algorithm": "FCFS"})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["success"])

        # Test reset
        r = self.client.post("/api/schedule/reset")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["success"])

    def test_bankers_api_preserved(self):
        payload = {
            "allocation": [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]],
            "maximum": [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]],
            "available": [3, 3, 2]
        }
        r = self.client.post("/api/bankers", json=payload)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["safe"])
        self.assertGreater(len(data["safe_sequence"]), 0)

    def test_live_kernel_mode_separation(self):
        # Switch mode to live_kernel
        r = self.client.post("/api/system/mode", json={"mode": "live_kernel"})
        self.assertEqual(r.status_code, 200)

        # Ensure disconnected status reported honestly
        r = self.client.get("/api/system/mode")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["is_connected"])

        # Scheduler in live_kernel mode must report disconnected, not fake execution
        r = self.client.post("/api/schedule", json={"algorithm": "FCFS"})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("disconnected", data["message"].lower())
        self.assertEqual(data["gantt"], [])

        # Step in live kernel mode must fail cleanly
        r = self.client.post("/api/schedule/step", json={"algorithm": "FCFS"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["success"])

        # Restore mode to simulation
        r = self.client.post("/api/system/mode", json={"mode": "simulation"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
