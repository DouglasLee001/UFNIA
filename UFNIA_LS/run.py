#!/usr/bin/env python3
"""UFNIA session -> Z3 preprocessing -> arbitrary-precision custom SLS.

Z3 is used only for parsing, tactics, model conversion and model evaluation.
No complete Z3 solver is called by this driver.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
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
MATURE_CORE = ROOT / 'build' / 'ufnia_ls' / 'ufnia_mature'
MATURE_VERSION = 'mature-mpz-v1'
MATURE_UF_VERSION = 'mature-mpz-v2'
_CORE_STRATEGY_DEFAULTS = {
    'interaction': 'joint', 'scoring': 'count', 'distance': 'classic',
    'arithmetic_repair': 'off', 'active_arithmetic_rows': 'off',
    'search_policy': 'legacy', 'search_phase': 'mixed', 'candidate_policy': 'broad',
}
_OPTION_FIELDS = ('backend', 'seconds', 'seed', 'preprocess_timeout', 'ackermann_limit',
                  'uf_observers', 'interaction', 'scoring', 'cnf', 'distance',
                  'arithmetic_repair', 'active_arithmetic_rows', 'post_lower_eqs',
                  'search_policy', 'search_phase', 'candidate_policy', 'dump_candidate')


def _selected_core(args):
    return MATURE_CORE if args.backend == 'mature' else CORE


def _backend_report(args):
    requested = {name: getattr(args, name) for name in _OPTION_FIELDS}
    effective = dict(requested)
    if args.backend == 'mature':
        effective.update({name: None for name in _CORE_STRATEGY_DEFAULTS})
        if args.uf_observers:
            effective.update(interaction=args.interaction, scoring=args.scoring)
    result = {'backend': args.backend,
              'backend_configuration': ('mature_uf_observed' if args.uf_observers else 'mature_intrinsic')
                                       if args.backend == 'mature' else 'ufnia_core',
              'backend_version': None, 'core_binary': str(_selected_core(args)),
              'core_sha256': getattr(args, '_core_sha256', None),
              'requested_options': requested, 'effective_options': effective}
    if args.backend == 'mature':
        result.update({name: None for name in _CORE_STRATEGY_DEFAULTS})
        if args.uf_observers:
            result.update(interaction=args.interaction, scoring=args.scoring)
    return result


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
    parser.add_argument('--backend', choices=('core', 'mature'), default='core', help='GMP search backend (default: core)')
    parser.add_argument('--parse-only', action='store_true', help='preprocess and verify actual GMP core input parsing')
    parser.add_argument('--seconds', type=_number, default=600, help='SLS/refinement budget per query (default: 600)')
    parser.add_argument('--preprocess-timeout', type=_number, default=120, help='tactic timeout in seconds per preprocessing round (default: 120)')
    parser.add_argument('--ackermann-limit', type=_positive, default=1000000)
    parser.add_argument('--seed', type=_seed, default=0)
    parser.add_argument('--uf-observers', action='store_true', help='evaluate live UF congruence observations inside the SLS core')
    parser.add_argument('--interaction', choices=('none', 'soft', 'joint'), default='joint', help='UF search interaction with live observers (default: joint)')
    parser.add_argument('--scoring', choices=('count', 'paws'), default='count', help='SLS scoring policy (default: count)')
    parser.add_argument('--cnf', choices=('on', 'off'), default='on', help='Tseitin CNF conversion; off requires UF observers (default: on)')
    parser.add_argument('--distance', choices=('classic', 'structural'), default='classic', help='Boolean distance policy (default: classic)')
    parser.add_argument('--post-lower-eqs', choices=('on', 'off'), default='off', help='optional bounded equation elimination after arithmetic lowering (default: off)')
    parser.add_argument('--arithmetic-repair', choices=('off', 'linear', 'quotient', 'joint'), default='off', help='arithmetic joint candidate generation (default: off)')
    parser.add_argument('--active-arithmetic-rows', choices=('off', 'on'), default='off', help='temporary guarded arithmetic repair rows; requires arithmetic repair (default: off)')
    parser.add_argument('--search-policy', choices=('legacy', 'greedy'), default='legacy', help='SLS move selection policy (default: legacy)')
    parser.add_argument('--search-phase', choices=('mixed', 'alternating'), default='mixed', help='Boolean/integer phase policy; alternating requires greedy search (default: mixed)')
    parser.add_argument('--candidate-policy', choices=('broad', 'critical'), default='broad', help='arithmetic candidate ordering policy (default: broad)')
    parser.add_argument('--model-output', type=Path, help='verified model responses in SMT-LIB, with query comments')
    parser.add_argument('--keep-dir', type=Path, help='retain intermediate files in a unique subdirectory')
    parser.add_argument('--dump-candidate', action='store_true', help='retain unknown candidate and normalized violations; requires --keep-dir')
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
    if args.backend == 'mature':
        if args.cnf != 'on':
            raise ValueError('--backend mature requires --cnf on')
        unsupported = [name.replace('_', '-') for name, default in _CORE_STRATEGY_DEFAULTS.items()
                       if getattr(args, name) != default and
                       not (args.uf_observers and name in ('interaction', 'scoring'))]
        if args.dump_candidate:
            unsupported.append('dump-candidate')
        if unsupported:
            raise ValueError('--backend mature uses its intrinsic strategy; unsupported options: ' +
                             ', '.join('--' + name for name in unsupported))
    if args.dump_candidate and args.keep_dir is None:
        raise ValueError('--dump-candidate requires --keep-dir')
    if args.cnf == 'off' and not args.uf_observers:
        raise ValueError('--cnf off requires --uf-observers')
    if args.active_arithmetic_rows == 'on' and args.arithmetic_repair == 'off':
        raise ValueError('--active-arithmetic-rows on requires --arithmetic-repair linear, quotient, or joint')
    if args.search_phase == 'alternating' and args.search_policy != 'greedy':
        raise ValueError('--search-phase alternating requires --search-policy greedy')
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
    names = ('format', 'backend', 'native_format_version', 'native_uf_observer_format_version',
             'native_uf_observer_count', 'arbitrary_precision', 'carrier_sort_encoding', 'z3_version',
             'atom_count', 'clause_count', 'input_assertion_count',
             'integer_variable_count', 'boolean_variable_count', 'binary_product_count',
             'ackermann_limit', 'conversion_seconds', 'model_reconstruction',
             'uf_mode', 'uf_observers', 'uf_observer_prefix', 'cnf', 'post_lower_eqs', 'uf_application_count', 'uf_applications_count',
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
              'uf_observers': args.uf_observers, 'interaction': args.interaction if args.uf_observers else None,
              'scoring': args.scoring,
              'cnf': args.cnf, 'distance': args.distance,
              'post_lower_eqs': args.post_lower_eqs,
              'arithmetic_repair': args.arithmetic_repair,
              'active_arithmetic_rows': args.active_arithmetic_rows,
              'search_policy': args.search_policy,
              'search_phase': args.search_phase,
              'candidate_policy': args.candidate_policy,
              'dump_candidate': args.dump_candidate,
              'timing': {'preprocessing_seconds': 0.0, 'core_seconds': 0.0,
                         'model_validation_seconds': 0.0}}
    report.update(_backend_report(args))
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
        native = Path(str(stem) + '.native')
        metadata_path = Path(str(stem) + '.metadata.json')
        core_model = Path(str(stem) + '.core-model.json')
        candidate = Path(str(stem) + '.candidate.json')
        violations = Path(str(stem) + '.violations.json')
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
                    ackermann_limit=args.ackermann_limit, arbitrary_precision=True,
                    uf_observers=args.uf_observers, cnf=args.cnf == 'on',
                    post_lower_eqs=args.post_lower_eqs == 'on', backend=args.backend)
        except Exception as error:
            report['timing']['preprocessing_seconds'] += time.monotonic() - before
            report['reason'] = f'preprocessing failed: {error}'
            report['error'] = type(error).__name__
            if args.parse_only:
                report['status'] = 'error'
            break
        report['timing']['preprocessing_seconds'] += time.monotonic() - before
        normalized.write_text(conversion.normalized_smt2)
        if args.backend == 'mature':
            native.write_text(conversion.native)
        _json(metadata_path, conversion.metadata)
        report['preprocessing'] = _compact_metadata(conversion.metadata)
        carrier = bool(conversion.metadata.get('carrier_sort_encoding'))
        abstract = conversion.metadata.get('uf_mode') in ('lazy', 'observed')
        report['normalized_semantics'] = 'abstraction_with_congruence_table' if abstract else 'equisatisfiable'
        report['model_limitation'] = (
            'Opaque carrier representatives remain encoded as Int; original carrier-sort models are not reconstructed.'
            if carrier else None)
        if args.keep_dir is not None:
            report['artifacts'] = {'normalized': str(normalized), 'metadata': str(metadata_path),
                                   'core_model': str(core_model)}
            if args.backend == 'mature':
                report['artifacts']['native'] = str(native)
        report['rounds'] += 1
        core_input = native if args.backend == 'mature' else normalized
        executable = _selected_core(args)
        if args.parse_only:
            command = [str(executable), str(core_input), '--parse-only']
            timeout = None
        else:
            if deadline is None:
                deadline = time.monotonic() + args.seconds
            remaining = max(0.0, deadline - time.monotonic())
            command = [str(executable), str(core_input), '--seconds', str(remaining),
                       '--seed', str(args.seed), '--model-output', str(core_model)]
            timeout = remaining + 5
        if args.uf_observers:
            command += ['--uf-observers', '--interaction', args.interaction]
        if args.uf_observers or (args.backend == 'core' and args.scoring != 'count'):
            command += ['--scoring', args.scoring]
        if args.backend == 'core' and (args.uf_observers or args.distance != 'classic'):
            command += ['--distance', args.distance]
        if args.backend == 'core' and args.arithmetic_repair != 'off':
            command += ['--arithmetic-repair', args.arithmetic_repair]
        if args.backend == 'core' and args.active_arithmetic_rows == 'on':
            command += ['--active-arithmetic-rows', 'on']
        if args.backend == 'core' and args.search_policy != 'legacy':
            command += ['--search-policy', args.search_policy]
        if args.backend == 'core' and args.search_phase != 'mixed':
            command += ['--search-phase', args.search_phase]
        if args.backend == 'core' and args.candidate_policy != 'broad':
            command += ['--candidate-policy', args.candidate_policy]
        if args.backend == 'core' and args.dump_candidate:
            command += ['--candidate-output', str(candidate), '--violations-output', str(violations)]
        before = time.monotonic()
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            report['timing']['core_seconds'] += time.monotonic() - before
            report['reason'] = 'SLS process exceeded the wall-clock budget'
            break
        finally:
            if args.dump_candidate:
                for name, path in (('candidate', candidate), ('violations', violations)):
                    if path.is_file():
                        report['artifacts'][name] = str(path)
        report['timing']['core_seconds'] += time.monotonic() - before
        if args.keep_dir is not None:
            Path(str(stem) + '.core.stdout').write_text(proc.stdout)
            Path(str(stem) + '.core.stderr').write_text(proc.stderr)
        stats = _last_json(proc.stdout if args.parse_only else proc.stderr)
        report['core'] = stats
        report['backend_version'] = stats.get('backend_version')
        if args.backend == 'mature' and (stats.get('backend') != 'mature_mpz' or
                                         stats.get('backend_version') !=
                                         (MATURE_UF_VERSION if args.uf_observers else MATURE_VERSION)):
            report['reason'] = stats.get('error', 'mature core backend identity/version mismatch')
            if args.parse_only:
                report['status'] = 'error'
            break
        if args.parse_only:
            if proc.returncode == 0 and stats.get('status') == 'parsed':
                report['status'] = 'parsed'
            else:
                report['status'] = 'error'
                report['reason'] = stats.get('error', proc.stderr.strip() or 'GMP core failed to parse normalized input')
            break
        if args.backend == 'mature' and args.uf_observers and (
                stats.get('uf_observers') is not True or
                stats.get('interaction') != args.interaction or stats.get('scoring') != args.scoring):
            report['reason'] = 'mature UF core option evidence does not match the requested policy'
            break
        if proc.returncode != 0 or proc.stdout.strip() != 'sat':
            report['reason'] = stats.get('error', 'SLS did not find a validated model within the budget')
            break
        if stats.get('validated') is not True:
            report['reason'] = 'core reported sat without a validated assignment'
            break
        if args.backend == 'mature' and args.uf_observers and (
                stats.get('status') != 'sat' or type(stats.get('uf_conflicts')) is not int or
                stats.get('validation_scope') != 'native_cnf_and_uf_congruence' or
                stats.get('uf_conflicts') != 0):
            report['reason'] = 'mature UF core reported sat without exact CNF/congruence validation'
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
        executable = _selected_core(args)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            build = 'make -C UFNIA_LS mature' if args.backend == 'mature' else 'make -C UFNIA_LS'
            raise ValueError(f'Core executable missing: {executable}. Run {build} from {ROOT}.')
        args._core_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    started = time.monotonic()
    summary = {'input': str(args.input), 'mode': 'parse-only' if args.parse_only else 'solve',
               'uf_observers': args.uf_observers, 'interaction': args.interaction if args.uf_observers else None,
               'scoring': args.scoring,
               'cnf': args.cnf, 'distance': args.distance,
               'post_lower_eqs': args.post_lower_eqs,
               'arithmetic_repair': args.arithmetic_repair,
               'active_arithmetic_rows': args.active_arithmetic_rows,
               'search_policy': args.search_policy,
               'search_phase': args.search_phase,
               'candidate_policy': args.candidate_policy,
               'dump_candidate': args.dump_candidate,
               'status': 'complete', 'queries_seen': 0, 'queries_processed': 0,
               'counts': {'parsed': 0, 'sat': 0, 'unknown': 0, 'errors': 0},
               'timing': {'preprocessing_seconds': 0.0, 'core_seconds': 0.0,
                          'model_validation_seconds': 0.0},
               'complete_solver_used': False, 'model_output_written': False,
               'model_output_sha256': None, 'work_dir': None, 'last_query': None}
    summary.update(_backend_report(args))
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
                if report.get('backend_version') is not None:
                    summary['backend_version'] = report['backend_version']
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
            summary['model_output_sha256'] = hashlib.sha256(args.model_output.read_bytes()).hexdigest()
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
