"""Real-Haiku leak test: plant fake identifiers, scrub, assert none survive. Run: python tests/planted_fakes.py"""
import json, time, tempfile, pathlib
from vlt.scrub import scrub
from vlt.cache import Store

# assembled at runtime so GitHub push protection does not mistake the fake key for a real one
FAKE_KEY = "sk_" + "live_" + "51HxQ2bKz8VmPq3RtY7wNcLd9FgJhUaE0"
steps = [
 {"step_id":1,"source":"user","message":"hey can you fix the deploy script? Jessica Moreno from Quantumleaf Robotics said it breaks on db7.quantumleaf-internal.net, ping her at jessica.moreno@quantumleaf.io"},
 {"step_id":2,"source":"agent","message":"Let me look at the script.","tool_calls":[{"tool_call_id":"t1","function_name":"Bash","arguments":{"command":"cat /Users/minhtrinh/code/deploy.sh"}}],"observation":{"results":[{"source_call_id":"t1","content":"export STRIPE_KEY=" + FAKE_KEY + "\nssh deploy@db7.quantumleaf-internal.net"}]}},
 {"step_id":3,"source":"agent","message":"The script hardcodes a live Stripe key and the host. I'll move the key to an env var."},
 {"step_id":4,"source":"user","message":"cool thanks, also use numpy for the stats part"},
]
traj={"schema_version":"ATIF-v1.8","agent":{"name":"claude-code","version":"x","model_name":"m"},"steps":steps,"extra":{"vlt":{"harness":"claude_code","vlt_version":"0.1.0"}}}
store=Store(pathlib.Path(tempfile.mkdtemp()))
t=time.time(); r=scrub("claude_code", traj, store); print("%.1fs"%(time.time()-t))
out=json.dumps(r["traj"])
planted=["Jessica","Moreno","Quantumleaf","quantumleaf","db7",FAKE_KEY,"jessica.moreno","minhtrinh"]
print("LEAKED:", [p for p in planted if p in out])
print("table:", r["table"]); print("tags:", r["tags"], "rephrased:", r["rephrased"], "dropped:", r["dropped"])
for s in r["traj"]["steps"]: print(s["step_id"], s["source"], json.dumps(s)[:300])
t=time.time(); r2=scrub("claude_code", traj, store, log=lambda *_: None); print("rerun %.2fs, identical=%s"%(time.time()-t, r2==r))
assert not [p for p in planted if p in out], "planted identifiers leaked"
assert r2 == r, "rerun must be identical and cached"
print("PASS")
