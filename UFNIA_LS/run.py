#!/usr/bin/env python3
"""UFNIA session -> Z3 preprocessing -> arbitrary-precision custom SLS.

Z3 is used only for parsing, tactics, model conversion and model evaluation.
No complete Z3 solver is called by this driver.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ufnia_converter import convert_assertions
from UFNIA_LS.session import SessionReader

CORE = ROOT / 'build' / 'ufnia_ls' / 'ufnia_core'


def _number(value):
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 4294967:
        raise argparse.ArgumentTypeError('must be finite and between 0 and 4294967 seconds')
    return result


def _positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return result


def _seed(value):
    result = int(value)
    if not 0 <= result <= 4294967295:
        raise argparse.ArgumentTypeError('must be between 0 and 4294967295')
    return result


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='SMT-LIB input; incremental sessions are supported')
    parser.add_argument('--parse-only', action='store_true', help='preprocess and verify actual GMP core input parsing')
    parser.add_argument('--seconds', type=_number, default=600, help='SLS/refinement budget per query (default: 600)')
    parser.add_argument('--preprocess-timeout', type=_number, default=120, help='tactic timeout in seconds per preprocessing round (default: 120)')
    parser.add_argument('--ackermann-limit', type=_positive, default=1000000)
    parser.add_argument('--seed', type=_seed, default=0)
    parser.add_argument('--model-output', type=Path, help='verified model responses in SMT-LIB, with query comments')
    parser.add_argument('--keep-dir', type=Path, help='retain intermediate files in a unique subdirectory')
    parser.add_argument('--json-report', type=Path, help='write aggregate counts, timings and the last query report')
    parser.add_argument('--query', type=_positive, help='process only this 1-based query')
    return parser.parse_args(argv)


def _aliases(left, right):
    if left.resolve() == right.resolve():
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _paths(args):
    args.input = args.input.resolve(strict=True)
    if not args.input.is_file():
        raise ValueError('input must be a regular SMT-LIB file')
    outputs = []
    for name in ('model_output', 'json_report', 'keep_dir'):
        path = getattr(args, name)
        if path is None:
            continue
        path = path.resolve()
        if _aliases(args.input, path):
            raise ValueError(f'{name.replace("_", "-")} aliases the input; refusing to overwrite the input')
        setattr(args, name, path)
        if name != 'keep_dir':
            if path.exists() and not path.is_file():
                raise ValueError(f'{name.replace("_", "-")} must name a file')
            outputs.append(path)
        elif path.exists() and not path.is_dir():
            raise ValueError('keep-dir must name a directory')
    if len(outputs) == 2 and _aliases(*outputs):
        raise ValueError('model-output and json-report must name different files')


def _publish(source, destination):
    """Atomically publish an output after all input-alias checks have passed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as src:
            shutil.copyfileobj(src, out)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json(path, value):
    with path.open('w') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def _last_json(text):
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    return {}


def _compact_metadata(metadata):
    # Full symbol/application tables are retained in the intermediate metadata,
    # never copied into the aggregate report for every query.
    names = ('format', 'arbitrary_precision', 'carrier_sort_encoding', 'z3_version',
             'atom_count', 'clause_count', 'input_assertion_count',
             'integer_variable_count', 'boolean_variable_count', 'binary_product_count',
             'ackermann_limit', 'conversion_seconds', 'model_reconstruction',
             'uf_mode', 'uf_application_count', 'uf_applications_count',
             'estimated_congruence_pairs', 'congruence_pair_count', 'uf_pair_upper_bound',
             'requires_congruence_refinement', 'intermediate_semantics')
    compact = {name: metadata[name] for name in names if name in metadata}
    if 'uf_applications' in metadata:
        compact['uf_application_count'] = len(metadata['uf_applications'])
    if 'uf_declarations' in metadata:
        compact['uf_declaration_count'] = len(metadata['uf_declarations'])
    return compact


def _query(query, args, work):
    report = {'index': query.index, 'command': query.command,
              'input_assertions': query.assertion_count, 'status': 'unknown',
              'original_model_validated': False, 'validated': False,
              'rounds': 0, 'refinement_lemmas': 0,
              'timing': {'preprocessing_seconds': 0.0, 'core_seconds': 0.0,
                         'model_validation_seconds': 0.0}}
    base = query.assertions
    lemmas = []
    seen_lemmas = set()
    deadline = None
    restored_text = None
    conversion = None
    while True:
        iteration = report['rounds']
        stem = work / f'query-{query.index:06d}.round-{iteration:04d}'
        normalized = Path(str(stem) + '.normalized.smt2')
        metadata_path = Path(str(stem) + '.metadata.json')
        core_model = Path(str(stem) + '.core-model.json')
        before = time.monotonic()
        if deadline is not None and before >= deadline:
            report['reason'] = 'SLS/refinement budget exhausted'
            break
        try:
            refiner = getattr(conversion, 'refine', None)
            if refiner is not None:
                # Congruence lemmas mention the conversion's stable abstract
                # symbols; rebuilding from original UF terms would rename them.
                remaining = min(args.preprocess_timeout, max(0, deadline - before))
                conversion = refiner(lemmas, timeout_ms=max(1, int(remaining * 1000)))
            else:
                timeout_seconds = args.preprocess_timeout
                if deadline is not None:
                    timeout_seconds = min(timeout_seconds, max(0, deadline - before))
                conversion = convert_assertions(base + tuple(lemmas),
                    timeout_ms=max(1, int(timeout_seconds * 1000)),
                    ackermann_limit=args.ackermann_limit, arbitrary_precision=True)
        except Exception as error:
            report['timing']['preprocessing_seconds'] += time.monotonic() - before
            report['reason'] = f'preprocessing failed: {error}'
            report['error'] = type(error).__name__
            if args.parse_only:
                report['status'] = 'error'
            break
        report['timing']['preprocessing_seconds'] += time.monotonic() - before
        normalized.write_text(conversion.normalized_smt2)
        _json(metadata_path, conversion.metadata)
        report['preprocessing'] = _compact_metadata(conversion.metadata)
        carrier = bool(conversion.metadata.get('carrier_sort_encoding'))
        lazy = conversion.metadata.get('uf_mode') == 'lazy'
        report['normalized_semantics'] = 'abstraction_with_congruence_table' if lazy else 'equisatisfiable'
        report['model_limitation'] = (
            'Opaque carrier representatives remain encoded as Int; original carrier-sort models are not reconstructed.'
            if carrier else None)
        if args.keep_dir is not None:
            report['artifacts'] = {'normalized': str(normalized), 'metadata': str(metadata_path),
                                   'core_model': str(core_model)}
        report['rounds'] += 1
        if args.parse_only:
            command = [str(CORE), str(normalized), '--parse-only']
            timeout = None
        else:
            if deadline is None:
                deadline = time.monotonic() + args.seconds
            remaining = max(0.0, deadline - time.monotonic())
            command = [str(CORE), str(normalized), '--seconds', str(remaining),
                       '--seed', str(args.seed), '--model-output', str(core_model)]
            timeout = remaining + 5
        before = time.monotonic()
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            report['timing']['core_seconds'] += time.monotonic() - before
            report['reason'] = 'SLS process exceeded the wall-clock budget'
            break
        report['timing']['core_seconds'] += time.monotonic() - before
        if args.keep_dir is not None:
            Path(str(stem) + '.core.stdout').write_text(proc.stdout)
            Path(str(stem) + '.core.stderr').write_text(proc.stderr)
        if args.parse_only:
            stats = _last_json(proc.stdout)
            report['core'] = stats
            if proc.returncode == 0 and stats.get('status') == 'parsed':
                report['status'] = 'parsed'
            else:
                report['status'] = 'error'
                report['reason'] = stats.get('error', proc.stderr.strip() or 'GMP core failed to parse normalized input')
            break
        stats = _last_json(proc.stderr)
        report['core'] = stats
        if proc.returncode != 0 or proc.stdout.strip() != 'sat':
            report['reason'] = stats.get('error', 'SLS did not find a validated model within the budget')
            break
        if stats.get('validated') is not True:
            report['reason'] = 'core reported sat without a validated assignment'
            break
        before = time.monotonic()
        values = None
        try:
            values = json.loads(core_model.read_text())
            restored, valid = conversion.restore_model(values)
        except Exception as error:
            restored, valid = None, False
            report['reason'] = f'model reconstruction failed: {error}'
        report['timing']['model_validation_seconds'] += time.monotonic() - before
        if valid:
            report['status'] = 'sat'
            report['validated'] = True
            report['original_model_validated'] = not carrier
            report['validation_scope'] = 'encoded_carrier_assertions' if carrier else 'original_assertions'
            comment = '; ' + report['model_limitation'] + '\n' if carrier else ''
            restored_text = comment + '(model\n' + restored.sexpr() + '\n)\n'
            if args.keep_dir is not None:
                model_path = work / f'query-{query.index:06d}.model.smt2'
                model_path.write_text(restored_text)
                report['artifacts']['restored_model'] = str(model_path)
            report.pop('reason', None)
            break
        conflicts = getattr(conversion, 'congruence_conflicts', None)
        new_lemmas = []
        if conflicts is not None and values is not None:
            try:
                for lemma in conflicts(values):
                    key = lemma.get_id()
                    if key not in seen_lemmas:
                        seen_lemmas.add(key)
                        new_lemmas.append(lemma)
            except Exception as error:
                report['reason'] = f'UF congruence refinement failed: {error}'
        if new_lemmas and time.monotonic() < deadline:
            lemmas.extend(new_lemmas)
            report['refinement_lemmas'] = len(lemmas)
            print(f'[query {query.index}] refining {len(new_lemmas)} UF congruence constraints', file=sys.stderr)
            continue
        report.setdefault('reason', 'reconstructed model did not satisfy the source assertions')
        break
    report['timing'] = {key: round(value, 6) for key, value in report['timing'].items()}
    if args.keep_dir is not None:
        _json(work / f'query-{query.index:06d}.report.json', report)
    return report, restored_text


def _main(argv=None):
    args = _arguments(argv)
    try:
        _paths(args)
        if not CORE.is_file() or not os.access(CORE, os.X_OK):
            raise ValueError(f'Core executable missing: {CORE}. Run make -C UFNIA_LS from {ROOT}.')
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    started = time.monotonic()
    summary = {'input': str(args.input), 'mode': 'parse-only' if args.parse_only else 'solve',
               'status': 'complete', 'queries_seen': 0, 'queries_processed': 0,
               'counts': {'parsed': 0, 'sat': 0, 'unknown': 0, 'errors': 0},
               'timing': {'preprocessing_seconds': 0.0, 'core_seconds': 0.0,
                          'model_validation_seconds': 0.0},
               'complete_solver_used': False, 'model_output_written': False,
               'work_dir': None, 'last_query': None}
    exit_code = 0
    with ExitStack() as stack:
        if args.keep_dir is not None:
            args.keep_dir.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix='run-', dir=args.keep_dir))
            summary['work_dir'] = str(work)
        else:
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='ufnia-')))
        model_path = work / 'verified-models.smt2'
        model_stream = None
        reader = SessionReader(args.input)
        iterator = iter(reader)
        try:
            for query in iterator:
                summary['queries_seen'] = query.index
                if args.query is not None and query.index != args.query:
                    continue
                print(f'[query {query.index}] preprocessing {query.assertion_count} assertions', file=sys.stderr)
                report, model_text = _query(query, args, work)
                summary['last_query'] = report
                summary['queries_processed'] += 1
                status = report['status']
                if status in summary['counts']:
                    summary['counts'][status] += 1
                if status == 'error':
                    exit_code = 1
                for key, value in report['timing'].items():
                    summary['timing'][key] += value
                if not args.parse_only:
                    print(status if status in ('sat', 'unknown') else 'unknown', flush=True)
                if model_text is not None and args.model_output is not None:
                    if model_stream is None:
                        model_stream = stack.enter_context(model_path.open('w'))
                    model_stream.write(f'; query {query.index}\n' + model_text + '\n')
                    model_stream.flush()
                diagnostic = f'[query {query.index}] {status}'
                if report.get('reason'):
                    diagnostic += ': ' + report['reason']
                if report.get('model_limitation'):
                    diagnostic += '; ' + report['model_limitation']
                print(diagnostic, file=sys.stderr)
                if args.query is not None:
                    break
            if summary['queries_processed'] == 0:
                raise ValueError(f'query {args.query} was not found' if args.query else 'input contains no check-sat query')
        except Exception as error:
            summary['error'] = f'{type(error).__name__}: {error}'
            summary['counts']['errors'] += 1
            exit_code = 2
            print(f'error: {summary["error"]}', file=sys.stderr)
            if not args.parse_only and summary['queries_processed'] == 0:
                print('unknown', flush=True)
        finally:
            iterator.close()
            summary['session'] = reader.stats
        if model_stream is not None:
            model_stream.flush()
            _publish(model_path, args.model_output)
            summary['model_output_written'] = True
            summary['model_output'] = str(args.model_output)
        summary['timing']['total_seconds'] = round(time.monotonic() - started, 6)
        summary['timing'] = {key: round(value, 6) for key, value in summary['timing'].items()}
        if exit_code:
            summary['status'] = 'error'
        elif args.parse_only:
            summary['status'] = 'parsed'
        if args.json_report is not None:
            temporary_report = work / 'summary.json'
            _json(temporary_report, summary)
            _publish(temporary_report, args.json_report)
        if args.keep_dir is not None:
            _json(work / 'summary.json', summary)
        if args.parse_only:
            print(json.dumps(summary, ensure_ascii=False))
        print(f'[pipeline] processed {summary["queries_processed"]} queries in {summary["timing"]["total_seconds"]:.3f}s', file=sys.stderr)
    return exit_code


def main(argv=None):
    try:
        return _main(argv)
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
