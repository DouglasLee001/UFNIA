"""Snapshot the active Z3 solver state plus per-call assumptions."""
import z3

def snapshot_text(solver,assumptions=()):
    snap=z3.Solver(ctx=solver.ctx)
    snap.add(*solver.assertions())
    snap.add(*assumptions)
    return snap.to_smt2()+"\n(exit)\n"
