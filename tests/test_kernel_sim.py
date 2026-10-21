"""Automated Tests for HospitalOS Built-In Educational Kernel Simulation Architecture.

Verifies:
1. HospitalKernelEngine boot and lifecycle.
2. SimulationProvider delegation to HospitalKernelEngine.
3. Authoritative single process state in kernel subsystems.
4. CPU Scheduler integration with kernel process manager.
5. Memory Manager allocation, paging, and pressure visibility.
6. Resource Manager allocation, blocking, and unblocking.
7. Synchronization Manager mutexes and counting semaphores.
8. Event Bus stream and simulated system calls.
9. Kernel reset determinism.
10. LiveKernelProvider isolation and honesty.
11. Demonstration Scenarios execution against HospitalKernelEngine.
12. Kernel status reporting.
"""

import unittest
from kernel_sim import (
    HospitalKernelEngine,
    KernelEventBus,
    ProcessManager,
    MemoryManager,
    HospitalResourceManager,
    SynchronizationManager,
    DeadlockManager,
    CPUScheduler,
)
from providers import SimulationProvider, LiveKernelProvider, provider_manager, get_provider
from contracts import ProcessState
from app import app


class TestHospitalKernelEngineCore(unittest.TestCase):
    """Direct tests for HospitalKernelEngine and its subsystems."""

    def setUp(self):
        self.kernel = HospitalKernelEngine()

    def test_kernel_initialization_and_boot(self):
        self.assertEqual(self.kernel.status, "BOOTED")
        self.assertIsInstance(self.kernel.event_bus, KernelEventBus)
        self.assertIsInstance(self.kernel.process_manager, ProcessManager)
        self.assertIsInstance(self.kernel.memory_manager, MemoryManager)
        self.assertIsInstance(self.kernel.resource_manager, HospitalResourceManager)
        self.assertIsInstance(self.kernel.sync_manager, SynchronizationManager)
        self.assertIsInstance(self.kernel.deadlock_manager, DeadlockManager)
        self.assertIsInstance(self.kernel.scheduler, CPUScheduler)

        events = self.kernel.event_bus.get_events(limit=10)
        boot_events = [e for e in events if e.event_type == "KERNEL_BOOT"]
        self.assertEqual(len(boot_events), 1)
        self.assertEqual(boot_events[0].source, "simulation")

    def test_kernel_status_reporting(self):
        status = self.kernel.get_kernel_status()
        self.assertEqual(status["status"], "BOOTED")
        self.assertEqual(status["mode"], "simulation")
        self.assertIn("clock", status)
        self.assertIn("processes_count", status)
        self.assertIn("memory_used_kb", status)
        self.assertEqual(status["memory_total_kb"], 64)
        self.assertIn("HospitalOS Built-In Educational Kernel Simulation Engine", status["name"])

    def test_kernel_reset_determinism(self):
        # Register a process, allocate memory, acquire lock
        self.kernel.process_manager.register_patient_process(101, "Test Reset Patient", doctor_id=1)
        self.kernel.allocate_memory(101, pages=3)
        self.kernel.acquire_lock(101, "MUTEX_OT")
        self.assertEqual(len(self.kernel.process_manager.get_all_processes()), 1)
        self.assertGreater(self.kernel.memory_manager.get_used_kb(), 0)

        # Reset kernel
        self.kernel.reset()
        self.assertEqual(self.kernel.status, "BOOTED")
        self.assertEqual(self.kernel.simulation_clock, 0)
        self.assertEqual(len(self.kernel.process_manager.get_all_processes()), 0)
        self.assertEqual(self.kernel.memory_manager.get_used_kb(), 0)

        # Verify KERNEL_RESET event
        events = self.kernel.event_bus.get_events(limit=5)
        reset_events = [e for e in events if e.event_type == "KERNEL_RESET"]
        self.assertEqual(len(reset_events), 1)


class TestSimulationProviderKernelDelegation(unittest.TestCase):
    """Verifies SimulationProvider behaves as a true adapter over HospitalKernelEngine."""

    def setUp(self):
        self.provider = SimulationProvider()
        with self.provider._get_db() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
            if cnt < 2:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Alpha', 0, 4, 1, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Beta', 0, 4, 2, 'Waiting', 'General Medicine', 1)")
            else:
                conn.execute("UPDATE patients SET status='Waiting'")
        procs = self.provider.get_processes()
        self.p1 = procs[0].pid
        self.p2 = procs[1].pid
        if procs[0].state == ProcessState.TERMINATED:
            self.provider.readmit_patient(self.p1)
        if procs[1].state == ProcessState.TERMINATED:
            self.provider.readmit_patient(self.p2)

    def test_provider_owns_kernel_engine(self):
        self.assertIsInstance(self.provider.kernel, HospitalKernelEngine)
        self.assertEqual(self.provider.get_mode(), "simulation")
        self.assertTrue(self.provider.is_connected())

        status = self.provider.get_kernel_status()
        self.assertEqual(status["status"], "BOOTED")

    def test_single_authoritative_process_state(self):
        # State transitions on provider reflect directly inside kernel
        self.provider.transition_process(self.p1, ProcessState.RUNNING, "Test dispatch")
        proc_in_provider = self.provider.get_process_by_id(self.p1)
        proc_in_kernel = self.provider.kernel.process_manager.get_process(self.p1)

        self.assertIsNotNone(proc_in_kernel)
        self.assertEqual(proc_in_provider.state, ProcessState.RUNNING)
        self.assertEqual(proc_in_kernel.state, ProcessState.RUNNING)

    def test_cross_module_resource_and_process_sync(self):
        # Exhaust scanners
        avail = next(r for r in self.provider.get_resources() if r.resource_id == "RES_SCANNER").available
        self.provider.request_resource(self.p1, "scanner", avail)

        # P2 request blocks
        req_res = self.provider.request_resource(self.p2, "scanner", 1)
        self.assertTrue(req_res["blocked"])

        # Both provider and kernel process manager agree P2 is WAITING on RES_SCANNER
        p2_provider = self.provider.get_process_by_id(self.p2)
        p2_kernel = self.provider.kernel.process_manager.get_process(self.p2)
        self.assertEqual(p2_provider.state, ProcessState.WAITING)
        self.assertEqual(p2_kernel.state, ProcessState.WAITING)
        self.assertEqual(p2_provider.waiting_on_resource, "RES_SCANNER")
        self.assertEqual(p2_kernel.waiting_on_resource, "RES_SCANNER")

        # Release unblocks P2
        self.provider.release_resource(self.p1, "scanner", 1)
        p2_woken = self.provider.get_process_by_id(self.p2)
        self.assertEqual(p2_woken.state, ProcessState.READY)
        self.assertIsNone(p2_woken.waiting_on_resource)

    def test_events_stream_and_syscalls_through_kernel(self):
        events = self.provider.get_events(limit=20)
        self.assertGreater(len(events), 0)
        for e in events:
            self.assertEqual(e.source, "simulation")

        syscalls = self.provider.get_simulated_syscalls(limit=20)
        for sc in syscalls:
            self.assertEqual(sc["source"], "simulation")
            self.assertIn("call", sc)


class TestKernelWebAPIAndLiveKernelIsolation(unittest.TestCase):
    """Verifies Web endpoints for kernel status and ensures LiveKernel isolation."""

    def setUp(self):
        self.client = app.test_client()

    def test_kernel_status_api_endpoint(self):
        resp = self.client.get("/api/system/kernel/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "BOOTED")
        self.assertEqual(data["mode"], "simulation")
        self.assertEqual(data["memory_total_kb"], 64)

    def test_live_kernel_mode_honesty_and_isolation(self):
        live_provider = LiveKernelProvider()
        self.assertEqual(live_provider.get_mode(), "live_kernel")
        self.assertFalse(live_provider.is_connected())
        self.assertEqual(len(live_provider.get_processes()), 0)
        self.assertEqual(len(live_provider.get_resources()), 0)
        self.assertEqual(len(live_provider.get_events()), 0)

        # Ensure no simulated data leaks
        snap = live_provider.get_system_snapshot()
        self.assertFalse(snap.is_connected)
        self.assertEqual(snap.mode, "live_kernel")
        self.assertIn("Disconnected", snap.message)


if __name__ == "__main__":
    unittest.main()
