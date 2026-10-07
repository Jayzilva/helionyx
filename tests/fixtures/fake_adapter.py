"""Stand-in for an external adapter program: checks the input schema, writes a canned result."""

import json
import sys

assert sys.argv[1] == "run"
with open(sys.argv[2], encoding="utf-8") as fh:
    inp = json.load(fh)
assert inp["schema"] == "helionyx-adapter-io/1" and len(inp["time_series"]["load_kw"]) == 8760
out = {"schema": inp["schema"], "status": "optimal", "solver": {"name": "fake", "version": "1.0", "label": "test"},
       "sizes": {"pv_kwp": 42.0, "bess_kwh": 10.0, "bess_kw": 5.0, "genset_kw": 0.0},
       "metrics": {"npc": 1234.5, "lcoe_per_kwh": 0.5, "initial_capital": 1000.0, "renewable_fraction_pct": 50.0,
                   "capacity_shortage_pct": 0.0}, "messages": ["canned"]}
with open(sys.argv[3], "w", encoding="utf-8") as fh:
    json.dump(out, fh)
