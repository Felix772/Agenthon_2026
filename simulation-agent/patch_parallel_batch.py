"""Install the isolated batch candidate only over the exact retained adapter."""

import hashlib
from pathlib import Path

target = Path("/opt/abides_fork/simulate_batch.py")
candidate = Path("/opt/participant_build/simulate_batch_parallel.py")
expected_sha256 = "2fdab1b7aa622c69328fe76ada06935d77629f161b8df4f1534fcbae7b274f83"
actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
if actual_sha256 != expected_sha256:
    raise RuntimeError(f"Unexpected baseline batch adapter: {actual_sha256}")
shim = Path("/usr/local/bin/simulate-batch")
shim_candidate = Path("/opt/participant_build/simulate-batch.parallel")
expected_shim_sha256 = "7a56826516e69c5e44e51b36d168f2cac2c466c87bad3a5b0ec6f3eafacd2ccf"
actual_shim_sha256 = hashlib.sha256(shim.read_bytes()).hexdigest()
if actual_shim_sha256 != expected_shim_sha256:
    raise RuntimeError(f"Unexpected baseline batch command: {actual_shim_sha256}")
target.write_bytes(candidate.read_bytes())
shim.write_bytes(shim_candidate.read_bytes())
print("Installed batch candidate", hashlib.sha256(target.read_bytes()).hexdigest())
