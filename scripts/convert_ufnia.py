#!/usr/bin/env python3
"""Convert one non-incremental UFNIA SMT-LIB file to NIA_LS native input."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile


def _worker(input_path, temporary_paths, ackermann_limit, timeout, target, connection):
    try:
        from ufnia_converter import convert_text
        source = Path(input_path).read_bytes()
        result = convert_text(source.decode('utf-8'), int(timeout * 1000), ackermann_limit,
                              arbitrary_precision=target == 'ufnia')
        result.metadata.update({'input': str(input_path), 'input_sha256': hashlib.sha256(source).hexdigest()})
        contents = [result.normalized_smt2 if target == 'ufnia' else result.native,
                    json.dumps(result.metadata, ensure_ascii=False, indent=2) + '\n']
        if len(temporary_paths) == 3:
            contents.append(result.normalized_smt2)
        for path, text in zip(temporary_paths, contents):
            Path(path).write_text(text, encoding='utf-8')
        connection.send({'ok': True, 'atoms': result.metadata['atom_count'], 'clauses': result.metadata['clause_count'], 'z3_version': result.metadata['z3_version']})
    except Exception as error:
        # Keep a failure message smaller than a pipe buffer: the parent waits
        # for process termination before reading this small control message.
        message = str(error).encode('utf-8', errors='replace')[:3000].decode('utf-8', errors='replace')
        connection.send({'ok': False, 'error': message})
    finally:
        connection.close()


def _positive_seconds(value):
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError()
        return seconds
    except ValueError:
        raise argparse.ArgumentTypeError('timeout must be a positive finite number of seconds')


def _ackermann_limit(value):
    try:
        limit = int(value)
        if not 0 <= limit <= 0xFFFFFFFF:
            raise ValueError()
        return limit
    except ValueError:
        raise argparse.ArgumentTypeError('Ackermann limit must be an integer between 0 and 4294967295')


def _publish(temporary, outputs):
    published = []
    try:
        # Hard-link publication is atomic and refuses even late-created paths.
        # Each temporary lives on the same filesystem as its destination.
        for index in list(range(1, len(outputs))) + [0]:
            os.link(temporary[index], outputs[index])
            published.append(index)
    except OSError:
        for index in reversed(published):
            try:
                if os.path.samefile(temporary[index], outputs[index]):
                    outputs[index].unlink()
            except FileNotFoundError:
                pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='one SMT-LIB input (Int/Bool, non-incremental)')
    parser.add_argument('-o', '--output', type=Path, required=True, help='native output; metadata is written to OUTPUT.json')
    parser.add_argument('--emit-smt2', type=Path, help='also write the normalized QF_NIA formula')
    parser.add_argument('--timeout', type=_positive_seconds, default=30.0, help='whole-conversion wall-clock limit in seconds (default: 30)')
    parser.add_argument('--ackermann-limit', type=_ackermann_limit, default=100_000, help='maximum Ackermann lemma estimate (default: 100000)')
    parser.add_argument('--target', choices=('nia_ls', 'ufnia'), default='nia_ls', help='legacy int64 atom format or arbitrary-precision UFNIA_LS SMT-LIB')
    args = parser.parse_args(argv)
    temporary = []
    process = None
    try:
        input_path = args.input.resolve(strict=True)
        if not input_path.is_file():
            raise ValueError('input must be a regular file')
        outputs = [args.output, Path(str(args.output) + '.json')]
        if args.emit_smt2 is not None:
            outputs.append(args.emit_smt2)
        resolved = [p.resolve() for p in outputs]
        if len(set(resolved)) != len(resolved):
            raise ValueError('output, metadata and SMT2 dump paths must be different')
        for path in outputs:
            if path.resolve() == input_path or (path.exists() and os.path.samefile(path, input_path)):
                raise ValueError('output must not overwrite or alias the input benchmark')
            if path.exists():
                raise ValueError(f'output already exists: {path}; choose a new output path')
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
            os.close(handle)
            temporary.append(name)
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(str(input_path), temporary, args.ackermann_limit, args.timeout, args.target, sender))
        process.start()
        sender.close()
        process.join(args.timeout)
        if process.is_alive():
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
            raise ValueError(f'conversion timeout after {args.timeout:g} seconds')
        report = receiver.recv() if receiver.poll() else None
        receiver.close()
        if not report:
            raise ValueError(f'conversion worker exited without a result (exit code {process.exitcode})')
        if not report['ok']:
            raise ValueError(report['error'])
        # Publish native input last, after both optional artifacts are complete.
        _publish(temporary, outputs)
        print(f'{args.output}: {report["atoms"]} atoms, {report["clauses"]} clauses (Z3Py {report["z3_version"]})')
        return 0
    except (OSError, ValueError, EOFError) as error:
        print(f'conversion error: {error}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('conversion interrupted', file=sys.stderr)
        return 130
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            process.join()
        for path in temporary:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
