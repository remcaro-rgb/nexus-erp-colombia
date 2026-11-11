attempt: 1

# FIX-P07-CPU-W05 progress

Plan:
- CB-1: backend/workflow/formula/limits.py default budget clock = thread_time() minus cyclic-GC pauses
  in the same thread (gc.callbacks hook registered once, thread-local accounting, raw-clock seam).
- CB-2: topology tests evaluate with an injected deterministic clock; worst-case cost test uses
  the engine's budget clock, min of 5 runs; new deterministic FIX_P07_CPU tests.
