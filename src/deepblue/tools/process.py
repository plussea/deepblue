"""Windows job lifetime: descendants cannot outlive a shell tool invocation."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [("user", ctypes.c_longlong), ("kernel", ctypes.c_longlong),
                ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
                ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]


class ProcessIds(ctypes.Structure):
    _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                ("ids", ctypes.c_size_t * 4096)]


class WindowsJob:
    def __init__(self, process):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.c_void_p]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            api.CloseHandle(self.handle)
            self.handle = None
            raise

    def close(self):
        if self.handle is None:
            return
        handles = []
        try:
            # ActiveProcesses reaches zero before Windows has finished releasing
            # every process resource. Keep process handles and wait for the
            # signalled state as well, so redirected log files can be reopened.
            processes = ProcessIds()
            if not self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(processes), ctypes.sizeof(processes), None):
                raise ctypes.WinError(ctypes.get_last_error())
            for pid in processes.ids[:processes.count]:
                handle = self.api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                if handle:
                    handles.append(handle)
            if not self.api.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 10
            while True:
                info = Accounting()
                if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.active == 0:
                    break
                if time.monotonic() >= deadline:
                    raise OSError("等待命令子进程退出超时。")
                time.sleep(0.01)
            for handle in handles:
                if self.api.WaitForSingleObject(handle, 10000) != 0:
                    raise OSError("等待命令子进程释放资源超时。")
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)
            self.api.CloseHandle(self.handle)
            self.handle = None
