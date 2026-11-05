#!/usr/bin/env python3
"""Fetch and Git-verify the unmodified official runtime subset; no system install."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

CODE=Path(__file__).resolve().parent
UP=CODE/"upstream"
COMMIT="3b99928bf976c11cb59c9db6a177adba21f75798"
ARCHIVE_SHA256="9732fdaa51356242e5e448950fbf1938959bc8f4439a94f0a552a6f040fa23a2"
EXTRA={"README.md","license.md","requirements.txt","benchmarks/calcite/calcite2.jsonlines",
       "benchmarks/literature/literature-rewrite.jsonlines","benchmarks/leetcode/leetcode.jsonlines"}
def fetch(url,path):
    subprocess.run(["curl","-4","--fail","--location","--connect-timeout","15","--max-time","1200",url,"-o",str(path)],check=True)
def main():
    UP.mkdir(parents=True,exist_ok=True)
    tree=UP/"tree.json"
    if not tree.exists():fetch("https://api.github.com/repos/VeriEQL/VeriEQL/git/trees/"+COMMIT+"?recursive=1",tree)
    rows=json.loads(tree.read_text())["tree"]
    wanted={r["path"]:r for r in rows if r["type"]=="blob" and
        (r["path"] in EXTRA or r["path"].endswith(".py") and r["path"].split("/")[0] not in
        ["experiments","test","dbms_checker","parallel","paper","z3py_libs"])}
    archive=UP/"source-full.tar.gz"
    if not archive.exists():fetch("https://api.github.com/repos/VeriEQL/VeriEQL/tarball/"+COMMIT,archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA256:
        raise RuntimeError("Archive SHA256 mismatch or incomplete download")
    inventory={}
    with tarfile.open(archive,"r:gz") as tar:
        for member in tar:
            name="/".join(member.name.split("/")[1:])
            if member.isfile() and name in wanted:
                data=tar.extractfile(member).read()
                expected=wanted[name]["sha"]
                actual=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\x00"+data).hexdigest()
                if actual!=expected:raise RuntimeError("Git blob mismatch "+name)
                dest=UP/"src"/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
                inventory[name]=hashlib.sha256(data).hexdigest()
    if set(inventory)!=set(wanted):raise RuntimeError("Missing source members")
    (UP/"runtime-sha256.json").write_text(json.dumps(inventory,indent=2)+"\n")
    print("Verified",len(inventory),"source/input files at",COMMIT)
if __name__=="__main__":main()
