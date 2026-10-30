"""Fast validation and UF abstraction in the active Z3Py context.

The native helper owns one reference to each returned AstVector. Wrapping those
vectors acquires Python references before releasing the native references; the
returned expressions and function declarations then retain their own ASTs.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import subprocess
import threading

import z3
from z3.z3types import AstVectorObj, ContextObj


class NativeFrontendError(Exception):
    """The native pass could not prepare the input."""


class UnsupportedInput(NativeFrontendError):
    """The input contains a rejected sort, operator, or quantifier."""


class UnsupportedCarrier(UnsupportedInput):
    """Use the Python carrier encoder for uninterpreted sorts."""


_DIRECTORY = Path(__file__).resolve().parent
_LIBRARY_PATH = _DIRECTORY.parent / "build/ufnia_ls/native_frontend.so"
_LIBRARY = None
_LOAD_LOCK = threading.Lock()


def _library():
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    with _LOAD_LOCK:
        if _LIBRARY is not None:
            return _LIBRARY
        source = _DIRECTORY / "native_frontend.cpp"
        if not _LIBRARY_PATH.exists() or source.stat().st_mtime_ns > _LIBRARY_PATH.stat().st_mtime_ns:
            result = subprocess.run(["make", "-C", str(_DIRECTORY), "native-frontend"],
                                    text=True, capture_output=True)
            if result.returncode:
                raise NativeFrontendError("native frontend build failed: " + result.stderr)
        library = ctypes.CDLL(str(_LIBRARY_PATH))
        library.ufnia_frontend_prepare.argtypes = [ContextObj, AstVectorObj,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)]
        library.ufnia_frontend_prepare.restype = ctypes.c_int
        library.ufnia_frontend_error.argtypes = []
        library.ufnia_frontend_error.restype = ctypes.c_char_p
        library.ufnia_frontend_release.argtypes = [ContextObj, ctypes.c_void_p]
        library.ufnia_frontend_release.restype = None
        library.ufnia_frontend_z3_version.argtypes = []
        library.ufnia_frontend_z3_version.restype = ctypes.c_char_p
        library.ufnia_frontend_ast_kind_address.argtypes = []
        library.ufnia_frontend_ast_kind_address.restype = ctypes.c_size_t
        library.ufnia_frontend_analyze.argtypes = [ContextObj, AstVectorObj,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        library.ufnia_frontend_analyze.restype = ctypes.c_int
        library.ufnia_frontend_serialize.argtypes = [ContextObj, AstVectorObj, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        library.ufnia_frontend_serialize.restype = ctypes.c_char_p
        native_address = library.ufnia_frontend_ast_kind_address()
        python_address = ctypes.cast(z3.z3core.Z3_get_ast_kind.__defaults__[0].f, ctypes.c_void_p).value
        if native_address != python_address:
            raise NativeFrontendError("native helper and Z3Py loaded different libz3 instances; rebuild against the active Python Z3 package")
        if library.ufnia_frontend_z3_version().decode() != z3.get_full_version():
            raise NativeFrontendError("native helper and Z3Py have different Z3 versions")
        _LIBRARY = library
        return library


def prepare(assertions):
    """Return ``(assertions_tuple, ((decl, args_tuple, fresh_value), ...))``.

    Validates the same Int/Bool operator fragment as the Python converter, then
    replaces each distinct nonzero-arity UF application with a fresh constant.
    Congruence constraints remain the caller's responsibility. No solving occurs.
    ``UnsupportedCarrier`` signals that carrier-sort encoding must run first.
    """
    if isinstance(assertions, z3.AstVector):
        vector = assertions
        if not len(vector):
            return (), ()
        ctx = vector.ctx
    else:
        assertions = tuple(assertions)
        if not assertions:
            return (), ()
        if not isinstance(assertions[0], z3.ExprRef):
            raise TypeError("assertions must be Z3 expressions")
        ctx = assertions[0].ctx
        vector = z3.AstVector(ctx=ctx)
        for assertion in assertions:
            if not isinstance(assertion, z3.ExprRef):
                raise TypeError("assertions must be Z3 expressions")
            if assertion.ctx != ctx:
                raise ValueError("all assertions must belong to one Z3 context")
            vector.push(assertion)
    library = _library()
    outputs = [ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()]
    nodes = ctypes.c_size_t()
    code = library.ufnia_frontend_prepare(ctx.ref(), vector.vector,
        ctypes.byref(outputs[0]), ctypes.byref(outputs[1]), ctypes.byref(outputs[2]), ctypes.byref(nodes))
    try:
        if code:
            message = library.ufnia_frontend_error().decode("utf-8", errors="replace")
            error = UnsupportedCarrier if code == 1 else UnsupportedInput if code == 2 else NativeFrontendError
            raise error(message)
        wrapped = []
        for pointer in outputs:
            # Z3Py's ctypes pointer subclass overrides __init__ without setting
            # c_void_p.value. Populate that field explicitly before C API use.
            handle = AstVectorObj(pointer.value)
            handle.value = pointer.value
            wrapped.append(z3.AstVector(handle, ctx))
        transformed = tuple(wrapped[0])
        applications = tuple((application.decl(), tuple(application.children()), value)
                             for application, value in zip(wrapped[1], wrapped[2]))
        return transformed, applications
    finally:
        # Each AstVector wrapper has already acquired its own reference. The
        # ExprRefs extracted above in turn own independent AST references.
        for pointer in outputs:
            if pointer.value:
                library.ufnia_frontend_release(ctx.ref(), pointer)


def _normalized_vector(assertions):
    if isinstance(assertions, z3.AstVector):
        return assertions.ctx, assertions
    assertions = tuple(assertions)
    ctx = assertions[0].ctx if assertions else z3.main_ctx()
    vector = z3.AstVector(ctx=ctx)
    for assertion in assertions:
        if not isinstance(assertion, z3.ExprRef):
            raise TypeError("assertions must be Z3 expressions")
        if assertion.ctx != ctx:
            raise ValueError("all assertions must belong to one Z3 context")
        vector.push(assertion)
    return ctx, vector


def _normalized_error(library, code):
    message = library.ufnia_frontend_error().decode("utf-8", errors="replace")
    error = UnsupportedInput if code == 2 else NativeFrontendError
    raise error(message)


def analyze_normalized(assertions):
    """Return ``(symbols_tuple, unique_boolean_atom_count, ast_node_count)``.

    Rejects every residual operator unsupported by the exact GMP evaluator.
    Symbol order is deterministic and is used by ``serialize_normalized`` for
    canonical i_INDEX/b_INDEX names. Bool equalities are logical connectives;
    arithmetic comparisons and Bool variables count as atoms.
    """
    ctx, vector = _normalized_vector(assertions)
    library = _library()
    output = ctypes.c_void_p()
    atoms, nodes = ctypes.c_size_t(), ctypes.c_size_t()
    code = library.ufnia_frontend_analyze(ctx.ref(), vector.vector,
        ctypes.byref(output), ctypes.byref(atoms), ctypes.byref(nodes))
    try:
        if code:
            _normalized_error(library, code)
        handle = AstVectorObj(output.value)
        handle.value = output.value
        symbols = z3.AstVector(handle, ctx)
        return tuple(symbols), atoms.value, nodes.value
    finally:
        if output.value:
            library.ufnia_frontend_release(ctx.ref(), output)


def serialize_normalized(assertions, symbols=None):
    """Print one shared-DAG SMT-LIB assertion with canonical variable names.

    Optional ``symbols`` must be the tuple returned by ``analyze_normalized``
    for this exact input, in its original order. Supplying it avoids a second
    whole-DAG analysis. Canonical names are i_INDEX or b_INDEX for that order.
    No complete solver or tactic is used.
    """
    ctx, vector = _normalized_vector(assertions)
    library = _library()
    symbol_vector = None
    if symbols is not None:
        symbol_vector = z3.AstVector(ctx=ctx)
        for symbol in symbols:
            if symbol.ctx != ctx:
                raise ValueError("symbols and assertions must belong to one Z3 context")
            symbol_vector.push(symbol)
    code = ctypes.c_int()
    serialized = library.ufnia_frontend_serialize(ctx.ref(), vector.vector,
        symbol_vector.vector if symbol_vector is not None else None, ctypes.byref(code))
    if code.value:
        _normalized_error(library, code.value)
    if serialized is None:
        raise NativeFrontendError("normalized serialization returned no text")
    return serialized.decode("utf-8")
