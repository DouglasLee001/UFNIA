"""Guarded finite ground equality-domain abstraction for VeriEQL label Strings."""
import z3

def abstract_labels(assertions):
    assertions=list(assertions)
    if not assertions:
        return [], {"relation":"equisatisfiable","labels":0,"functions":0}
    ctx=assertions[0].ctx
    label=z3.DeclareSort("__VeriEQL_Label",ctx=ctx)
    cache={};decls={};labels=set()
    def sort(s):
        if s.kind()==z3.Z3_SEQ_SORT:
            if s.sexpr()!="String":
                raise ValueError("String abstraction refuses non-String sequences")
            return label
        return s
    def visit(e):
        if e.get_id() in cache:
            return cache[e.get_id()]
        if z3.is_quantifier(e):
            raise ValueError("String abstraction requires quantifier-free input")
        d=e.decl()
        signature=[d.domain(i) for i in range(d.arity())]+[d.range()]
        if any(s.kind()==z3.Z3_UNINTERPRETED_SORT and str(s.name())=="__VeriEQL_Label" for s in signature):
            raise ValueError("String abstraction reserved sort collision")
        has_string=any(s.kind()==z3.Z3_SEQ_SORT for s in signature)
        if has_string and d.kind() not in (z3.Z3_OP_UNINTERPRETED,z3.Z3_OP_EQ,z3.Z3_OP_DISTINCT,z3.Z3_OP_ITE):
            raise ValueError("String interpretation is observable: "+str(d.name()))
        args=[visit(c) for c in e.children()]
        if d.kind()==z3.Z3_OP_UNINTERPRETED:
            if has_string:
                if d.get_id() not in decls:
                    decls[d.get_id()]=z3.Function(str(d.name()),*[sort(s) for s in signature])
                out=decls[d.get_id()](*args)
                if d.arity()==0:labels.add(str(d.name()))
            else:
                out=d(*args)
        elif d.kind()==z3.Z3_OP_EQ:
            out=args[0]==args[1]
        elif d.kind()==z3.Z3_OP_DISTINCT:
            out=z3.Distinct(*args)
        elif d.kind()==z3.Z3_OP_ITE:
            out=z3.If(*args)
        else:
            out=d(*args)
        cache[e.get_id()]=out
        return out
    result=[visit(a) for a in assertions]
    return result, {
        "relation":"equisatisfiable", "method":"ground_equality_String_to_fresh_free_sort",
        "labels":len(labels), "functions":len(decls),
        "string_constants":sorted(labels),
        "guard":"QF; no String literals, sequence/string builtin operations; all label equality and UF arguments preserved",
    }
