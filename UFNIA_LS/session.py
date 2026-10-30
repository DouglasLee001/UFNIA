"""Stream SMT-LIB sessions into independent query AST snapshots.

Z3 is used as a persistent parser, never as a complete solver.  Declaration
scopes live in its parser context; assertion scopes are maintained here because
Z3's parser API returns only the assertions parsed by the current API call.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Iterator, TextIO

import z3
from z3 import z3core


class SessionError(ValueError):
    """Malformed or unsupported SMT-LIB session."""


@dataclass(frozen=True)
class Query:
    index: int
    assertions: tuple[z3.BoolRef, ...]
    command: str = 'check-sat'
    assumptions: tuple[z3.BoolRef, ...] = ()
    assertion_count: int = 0


_SPECIAL = re.compile(r'[();"|\r\n]')
_TOKENS = re.compile(r';[^\r\n]*|"(?:[^"]|"")*"|\|[^|]*\||[()]|[^\s();]+')
_HEAD = re.compile(r'\((?:\s|;[^\r\n]*)*([^\s();]+)')


def _commands(stream: TextIO, chunk_size: int) -> Iterator[str]:
    """Scan one top-level command at a time, retaining at most one command."""
    depth = 0
    mode = 'normal'
    pieces: list[str] = []
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        start = 0 if depth else None
        previous = 0
        for match in _SPECIAL.finditer(chunk):
            pos = match.start()
            char = match.group()
            if depth == 0 and mode == 'normal' and chunk[previous:pos].strip():
                raise SessionError('malformed SMT-LIB: expected a top-level command')
            previous = pos + 1
            if mode == 'comment':
                if char in '\r\n':
                    mode = 'normal'
                continue
            if mode == 'string':
                if char == '"':
                    mode = 'normal'
                continue
            if mode == 'quoted':
                if char == '|':
                    mode = 'normal'
                continue
            if char == ';':
                mode = 'comment'
            elif char == '"' or char == '|':
                if depth == 0:
                    raise SessionError('malformed SMT-LIB: expected a top-level command')
                mode = 'string' if char == '"' else 'quoted'
            elif char == '(':
                if depth == 0:
                    start = pos
                depth += 1
            elif char == ')':
                depth -= 1
                if depth < 0:
                    raise SessionError('malformed SMT-LIB: unmatched closing parenthesis')
                if depth == 0:
                    pieces.append(chunk[start:pos + 1])
                    yield ''.join(pieces)
                    pieces.clear()
                    start = None
        if depth:
            pieces.append(chunk[start:])
        elif mode == 'normal' and chunk[previous:].strip():
            raise SessionError('malformed SMT-LIB: expected a top-level command')
    if depth or mode in ('string', 'quoted'):
        raise SessionError('malformed SMT-LIB: unclosed command, string, or quoted symbol')


def _tokens(command: str) -> list[re.Match]:
    return [m for m in _TOKENS.finditer(command) if not m.group().startswith(';')]


def _flat(command: str, expected: str, nargs: int) -> list[str]:
    tokens = [m.group() for m in _tokens(command)]
    if len(tokens) != nargs + 3 or tokens[:2] != ['(', expected] or tokens[-1] != ')':
        raise SessionError(f'malformed {expected} command')
    if any(t in ('(', ')') for t in tokens[2:-1]):
        raise SessionError(f'malformed {expected} command')
    return tokens[2:-1]


def _assuming_terms(command: str) -> list[str]:
    """Extract complete assumption terms without building an S-expression tree."""
    tokens = _tokens(command)
    if len(tokens) < 5 or [m.group() for m in tokens[:3]] != ['(', 'check-sat-assuming', '(']:
        raise SessionError('malformed check-sat-assuming command')
    if [m.group() for m in tokens[-2:]] != [')', ')']:
        raise SessionError('malformed check-sat-assuming command')
    terms: list[str] = []
    depth = 0
    begin = None
    for token in tokens[3:-2]:
        value = token.group()
        if depth == 0:
            if value == ')':
                raise SessionError('malformed check-sat-assuming command')
            begin = token.start()
        if value == '(':
            depth += 1
        elif value == ')':
            depth -= 1
        if depth == 0:
            terms.append(command[begin:token.end()])
    if depth:
        raise SessionError('malformed check-sat-assuming command')
    return terms


class _Parser:
    def __init__(self, ctx: z3.Context):
        self.ctx = ctx
        self.ref = z3core.Z3_mk_parser_context(ctx.ref())
        z3core.Z3_parser_context_inc_ref(ctx.ref(), self.ref)

    def parse(self, text: str) -> tuple[z3.BoolRef, ...]:
        try:
            vector = z3.AstVector(z3core.Z3_parser_context_from_string(self.ctx.ref(), self.ref, text), self.ctx)
            return tuple(vector)
        except z3.Z3Exception as error:
            raise SessionError(f'Z3 parsing failed: {error}') from error

    def close(self):
        if self.ref is not None:
            z3core.Z3_parser_context_dec_ref(self.ctx.ref(), self.ref)
            self.ref = None


class SessionReader:
    """Single-pass reader for a path or an already-open text stream.

    ``on_assertions`` sees each newly parsed assertion batch and each temporary
    assumption batch once. It can validate/cache AST DAG nodes without walking
    every active assertion again at every check. Query assertions include the
    temporary assumptions, while Query.assumptions also identifies them.
    With ``snapshots=False``, assertions is empty and assertion_count records
    the real active count; this avoids copying large contexts at every check.
    """
    def __init__(self, path: str | Path | TextIO, *,
                 on_assertions: Callable[[tuple[z3.BoolRef, ...]], None] | None = None,
                 chunk_size: int = 65536, batch_bytes: int = 1048576,
                 snapshots: bool = True):
        if chunk_size <= 0 or batch_bytes <= 0:
            raise ValueError('chunk_size and batch_bytes must be positive')
        self.path = path
        self.on_assertions = on_assertions
        self.chunk_size = chunk_size
        self.batch_bytes = batch_bytes
        self.snapshots = snapshots
        self.ctx = z3.Context()
        self.stats = {'commands': {}, 'command_count': 0, 'checks': 0,
                      'push': 0, 'pop': 0, 'maxdepth': 0, 'assertions': 0,
                      'assumptions': 0, 'parse_calls': 0}
        self._started = False

    def __iter__(self) -> Iterator[Query]:
        if self._started:
            raise SessionError('SessionReader is single-pass; create a new reader to reread a session')
        self._started = True
        stream = self.path if hasattr(self.path, 'read') else open(self.path, encoding='utf-8')
        own_stream = not hasattr(self.path, 'read')
        parser = _Parser(self.ctx)
        active: list[z3.BoolRef] = []
        marks: list[int] = []
        pending: list[str] = []
        pending_size = 0
        global_declarations = False
        parser_depth = 0
        exited = False

        def parse(text):
            self.stats['parse_calls'] += 1
            return parser.parse(text)

        def notify(expressions):
            if expressions and self.on_assertions is not None:
                self.on_assertions(expressions)

        def flush():
            nonlocal pending_size
            if pending:
                expressions = parse('\n'.join(pending))
                notify(expressions)
                active.extend(expressions)
                pending.clear()
                pending_size = 0

        try:
            for raw in _commands(stream, self.chunk_size):
                head = _HEAD.match(raw)
                if head is None:
                    raise SessionError('malformed SMT-LIB: missing command name')
                command = head.group(1)
                if exited:
                    raise SessionError('commands after exit are unsupported')
                counts = self.stats['commands']
                counts[command] = counts.get(command, 0) + 1
                self.stats['command_count'] += 1

                if command in ('declare-fun', 'declare-const', 'define-fun', 'declare-sort', 'define-sort', 'assert', 'set-logic'):
                    # Ordinary assertions create no parser symbols. Materialize
                    # declaration scopes only when a declaration or :named
                    # attribute can actually introduce a scoped binding.
                    binds_name = command in ('declare-fun', 'declare-const', 'define-fun', 'declare-sort', 'define-sort') or ':named' in raw
                    if binds_name and not global_declarations and len(marks) > parser_depth:
                        flush()
                        parse(f'(push {len(marks) - parser_depth})')
                        parser_depth = len(marks)
                    pending.append(raw)
                    pending_size += len(raw)
                    if command == 'assert':
                        self.stats['assertions'] += 1
                    if pending_size >= self.batch_bytes:
                        flush()
                    continue

                if command == 'set-info':
                    # Metadata can contain megabytes of quoted source text.
                    continue
                if command == 'set-option':
                    args = _flat(raw, command, 2)
                    if args[0] in (':global-declarations', ':global-decls'):
                        flush()
                        if args[1] not in ('true', 'false'):
                            raise SessionError('global-declarations must be true or false')
                        value = args[1] == 'true'
                        if marks and value != global_declarations:
                            raise SessionError('changing global-declarations inside an active scope is unsupported')
                        global_declarations = value
                    # Search, output, and model options do not affect the parsed
                    # formula; do not invoke output-channel or solver commands.
                    continue
                if command in ('get-model', 'get-value', 'get-assignment', 'get-proof',
                               'get-unsat-core', 'get-unsat-assumptions', 'get-info',
                               'get-option', 'get-assertions', 'echo'):
                    continue

                flush()
                if command in ('push', 'pop'):
                    arg, = _flat(raw, command, 1)
                    if not re.fullmatch(r'[0-9]+', arg):
                        raise SessionError(f'{command} requires a nonnegative numeral')
                    count = int(arg)
                    if command == 'pop' and count > len(marks):
                        raise SessionError('pop exceeds the active assertion scope depth')
                    if command == 'push' and count > 1000000 - len(marks):
                        raise SessionError('assertion scope resource limit exceeded (1000000)')
                    # Assertion scopes are Python-owned. Parser pop is only
                    # necessary when the removed scopes contain bindings.
                    target_depth = len(marks) - count if command == 'pop' else len(marks) + count
                    if command == 'pop' and parser_depth > target_depth:
                        parse(f'(pop {parser_depth - target_depth})')
                        parser_depth = target_depth
                    self.stats[command] += 1
                    if command == 'push':
                        marks.extend([len(active)] * count)
                        self.stats['maxdepth'] = max(self.stats['maxdepth'], len(marks))
                    elif count:
                        del active[marks[-count]:]
                        del marks[-count:]
                elif command in ('check-sat', 'check-sat-assuming'):
                    assumptions = ()
                    if command == 'check-sat':
                        _flat(raw, command, 0)
                    else:
                        terms = _assuming_terms(raw)
                        if terms:
                            # Even Boolean-term extensions with :named must not
                            # leak bindings into the next check's namespace.
                            assumptions = parse('(push 1)\n' + '\n'.join('(assert ' + term + ')' for term in terms))
                            parse('(pop 1)')
                            notify(assumptions)
                            self.stats['assumptions'] += len(assumptions)
                    self.stats['checks'] += 1
                    count = len(active) + len(assumptions)
                    snapshot = tuple(active) + assumptions if self.snapshots else ()
                    yield Query(self.stats['checks'], snapshot, command, assumptions, count)
                elif command == 'reset-assertions':
                    _flat(raw, command, 0)
                    if parser_depth:
                        parse(f'(pop {parser_depth})')
                        parser_depth = 0
                    active.clear()
                    marks.clear()
                elif command == 'reset':
                    _flat(raw, command, 0)
                    parser.close()
                    parser = _Parser(self.ctx)
                    parser_depth = 0
                    active.clear()
                    marks.clear()
                    global_declarations = False
                elif command == 'exit':
                    _flat(raw, command, 0)
                    exited = True
                else:
                    raise SessionError(f'unsupported SMT-LIB command {command!r}')
            flush()
        finally:
            parser.close()
            if own_stream:
                stream.close()
