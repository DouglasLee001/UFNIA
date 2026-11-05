"""Strict standalone signature checks in addition to the shared typed AST audit."""
from __future__ import annotations
import z3
from common.smt_tools import audit_formula, parse_commands

def _declaration_errors(commands):
    free_sorts={"Int","Bool"}
    aliases={}
    errors=[]
    for command in commands:
        if command[0]=="declare-sort":
            if len(command)!=3 or command[2]!="0":
                errors.append("Only nullary uninterpreted sort declarations are permitted")
            else:
                free_sorts.add(command[1])
        elif command[0]=="define-sort":
            if len(command)!=4 or command[2]!=[]:
                errors.append("Only nullary sort aliases are permitted")
            else:
                aliases[command[1]]=command[3]
    def allowed(sort,seen=frozenset()):
        if not isinstance(sort,str):
            return False
        if sort in aliases:
            return sort not in seen and allowed(aliases[sort],seen|{sort})
        return sort in free_sorts
    for name,body in aliases.items():
        if not allowed(body,{name}):
            errors.append("Unsupported sort alias: "+name)
    for command in commands:
        head=command[0]
        if head=="declare-const":
            sorts=command[2:3]
        elif head=="declare-fun":
            sorts=list(command[2])+[command[3]]
        elif head=="define-fun":
            sorts=[binding[1] for binding in command[2]]+[command[3]]
        elif head.startswith("declare-") and head!="declare-sort":
            errors.append("Unsupported declaration command: "+head)
            continue
        else:
            continue
        if not all(allowed(sort) for sort in sorts):
            errors.append("Unsupported declared signature: "+str(command[1]))
    return errors

def audit_snapshot(text):
    """Reject extra theories even when hidden in unused declarations."""
    report=audit_formula(text)
    try:
        errors=_declaration_errors(parse_commands(text))
    except (ValueError,IndexError,TypeError) as error:
        errors=["Invalid declaration syntax: "+type(error).__name__]
    report["declaration_errors"]=errors
    if errors:
        report["result"]="fail"
        report["research_eligible"]=False
        report["errors"].extend(errors)
    return report

def assert_serialization_matches(text,assertions):
    """Require each serialized assertion to be the original typed expression."""
    try:
        parsed=list(z3.parse_smt2_string(text))
    except z3.Z3Exception as error:
        raise ValueError("Serialized snapshot cannot be parsed") from error
    if len(parsed)!=len(assertions):
        raise ValueError("Serialization changed the number of active assertions")
    for index,(actual,expected) in enumerate(zip(parsed,assertions)):
        if not actual.eq(expected):
            raise ValueError("Serialization changed assertion "+str(index))
