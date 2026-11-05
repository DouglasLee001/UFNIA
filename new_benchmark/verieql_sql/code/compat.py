"""Preserve upstream's explicit-context API using standard Z3 4.16 constructors.

Only process-local names in the official constants module are rebound. Neither
the upstream files nor the installed Z3 package are edited.
"""
import z3

def prepare_z3_api():
    import constants as c
    c.Z3_And=lambda *args,ctx: z3.And(*args,ctx)
    c.Z3_Or=lambda *args,ctx: z3.Or(*args,ctx)
    c.Z3_Sum=lambda *args,ctx: z3.Sum(*args) if args else z3.IntVal(0,ctx=ctx)
    return {"method":"process_local_explicit_context_API_adapter",
        "operations":["And","Or","Sum"],
        "meaning":"Identical Boolean conjunction/disjunction and integer sum; empty Sum is IntVal(0) in official context",
        "upstream_files_modified":False,"installed_z3_modified":False}
