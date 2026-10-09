# Isolated native-window capture research

These original research components capture one explicitly identified macOS window
at native resolution. They are separate from the launcher and have no default
runtime entry point. Importing the library starts no process or desktop action.
There is no game launcher, GUI controller, profile restorer, retry queue or batch
runner here. The check command below executes synthetic controls only.

```sh
python3 -B scripts/research/native_window_capture/check.py
```

`capture.py` requires an independently pinned point geometry and exact window
owner plus process PID, birth and executable before and after a capture. A changed
target is refused; a suspect image is removed only after exact owned-file checks,
without decoding it. Native pixels must have a uniform integer backing scale.
PNG validation checks complete chunks, CRCs and reconstructed raster data before
an independently supplied decoder can accept the image.

`ownedleaf.py` contains fixed capture, inspector, decoder, synthetic fixture and
inert timeout commands. It reserves output before process creation, counts failed
capture attempts, and records child settlement. Limits are 180 seconds including
cleanup, eight capture attempts, 31 MiB per image and 256 MiB aggregate reserved
output. Unknown settlement holds the lane. Helper footprint checks are sampled;
OS and framework allocations are not completely traced or kernel limited.

`consumer.py` permits explicit root calls using one persistent ledger, one fixed
private directory and the original individually admitted game clock. Its native
capture sub-window is limited to the first 180 seconds of that clock; it cannot
reset the clock per call. Supplied root callbacks must enforce fresh independent
monitoring, process, source, provider, ownership, preservation and action-phase
gates. Callback filename checks do not establish the complete dependency closure;
the operator must freeze those dependencies and inputs before admission. A
blocking callback or report write crossing the deadline refuses and holds the
consumer. It never grants restoration or another job.

`WindowProbe.swift` supplies a deliberately owned two-window fixture and bounded
window/image inspection. `proof.py` exercises that fixture only after an explicit
admission with frozen sources and independent callbacks. Its programme failure
history fields remain closed; this bundle supplies no authority to retry a failed
proof. No compiled helper, screenshots, map data, executable, save or private
runtime record is included.

Synthetic tests cover wrong targets, malformed PNGs, output reservations, child
failure and settlement, immutable geometry, source changes and deadline overruns.
Two owned native fixture windows have separately exercised the capture mechanism;
that result does not certify capture of a game window, cargo delivery, a map,
save/reload persistence or full gameplay compatibility. Non-atomic window-ID reuse,
compositor behaviour and operating-system scheduling remain operational limits.
No test bypasses macOS privacy permissions or proves kernel-atomic isolation.
