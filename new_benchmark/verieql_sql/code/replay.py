"""Replay the common integer SQL subset using an actual SQLite DBMS."""
from collections import Counter
import re
import sqlite3
import z3

def replay(task,model):
    spec=task.get("replay_spec")
    if spec is None:
        return {"status":"not_run","reason":"No model/database projection recorded"}
    queries=spec["sql"]
    if any(re.search(r"/|\b(DIV|MOD|DATE|TIMESTAMP|IF|IFNULL|ROUND|AVG|ORDER\s+BY)\b",q,re.I) for q in queries):
        return {"status":"not_run","reason":"Outside the documented common MySQL/SQLite integer bag subset"}
    declarations={str(d.name()):d for d in model.decls()}
    row_sort=z3.DeclareSort("TupleSort",ctx=model.ctx)
    null_decl=declarations.get("NULL")
    label_sort=null_decl.domain(1) if null_decl is not None else z3.DeclareSort("__VeriEQL_Label",ctx=model.ctx)
    null_decl=null_decl if null_decl is not None else z3.Function("NULL",row_sort,label_sort,z3.BoolSort(ctx=model.ctx))
    database={}
    try:
        for table,rows in spec["tables"].items():
            database[table]=[]
            for row in rows:
                r=z3.Const(row["tuple"],row_sort)
                values=[]
                for col in row["columns"]:
                    label=z3.Const(col["label"],label_sort)
                    isnull=z3.is_true(model.eval(null_decl(r,label),model_completion=True))
                    decl=declarations.get(col["value_function"])
                    if decl is None:decl=z3.Function(col["value_function"],row_sort,z3.IntSort(ctx=model.ctx))
                    value=None if isnull else model.eval(decl(r),model_completion=True).as_long()
                    if value is not None and not -(2**63)<=value<2**63:
                        return {"status":"not_run","reason":"SQLite signed-64-bit input range exceeded"}
                    values.append(value)
                database[table].append(values)
        con=sqlite3.connect(":memory:")
        for table,cols in spec["schema"].items():
            quote=lambda name:'"'+name.replace('"','""')+'"'
            colnames=", ".join(quote(c)+" INTEGER" for c in cols)
            con.execute("CREATE TABLE "+quote(table)+" ("+colnames+")")
            marks=", ".join("?" for _ in cols)
            con.executemany("INSERT INTO "+quote(table)+" VALUES ("+marks+")",database[table])
        results=[con.execute(q).fetchall() for q in queries]
        con.close()
        if any(isinstance(v,float) for rows in results for row in rows for v in row):
            return {"status":"not_run","reason":"SQLite promoted an arithmetic result to floating point","database":database}
        unequal=Counter(results[0])!=Counter(results[1])
        return {"status":"pass" if unequal else "fail","database":database,
            "results":[[list(row) for row in rows] for rows in results],
            "comparison":"bag multiplicities, NULL represented by None",
            "dbms":"SQLite "+sqlite3.sqlite_version,
            "scope":"Common MySQL/SQLite integer + NULL + bag subset; no division, date, coercion or ordering"}
    except Exception as error:
        return {"status":"error","reason":str(error),"database":database}
