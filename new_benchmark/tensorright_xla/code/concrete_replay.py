"""Independent concrete integer-tensor replay.

This module deliberately does not import Z3 or the symbolic generator. Shapes,
operator validity, ceiling division, padding and index transformations execute
using Python integers. A caller supplies concrete parameters and a callback
reading the solver's total input-tensor functions at concrete coordinates.
Native Haskell TensorRight and production HLO are not executed.
"""

class InvalidAccess(ValueError):
    pass


class Tensor:
    def __init__(self, shape, valid, read):
        self.shape = tuple(shape)
        self.valid = bool(valid)
        self._read = read

    def access(self, coordinates):
        coordinates = tuple(coordinates)
        if not self.valid or not in_bounds(coordinates, self.shape):
            raise InvalidAccess("concrete tensor access outside a valid shape")
        return self._read(coordinates)


def in_bounds(coordinates, shape):
    return len(coordinates) == len(shape) and all(
        0 <= coordinate < extent for coordinate, extent in zip(coordinates, shape))


def sliced(tensor, starts, ends, strides):
    dimensions = len(tensor.shape)
    valid = (tensor.valid and len(starts) == len(ends) == len(strides) == dimensions
             and all(0 <= start <= end <= size and stride > 0
                     for size, start, end, stride in zip(tensor.shape, starts, ends, strides)))
    # An invalid operation is itself an observable rewrite violation. Avoid
    # assigning any language-specific meaning to division by a zero stride.
    shape = tuple((end - start + stride - 1) // stride if stride > 0 else 0
                  for start, end, stride in zip(starts, ends, strides))
    def read(index):
        source = tuple(start + offset * stride
                       for start, offset, stride in zip(starts, index, strides))
        return tensor.access(source)
    return Tensor(shape, valid, read)


def padded(tensor, low, high, interior, value):
    shape = tuple(size + left + right + max(size - 1, 0) * gap
                  for size, left, right, gap in zip(tensor.shape, low, high, interior))
    valid = (tensor.valid and all(left >= 0 and right >= 0 and gap >= 0
                                 for left, right, gap in zip(low, high, interior))
             and all(size >= 0 for size in shape))
    def read(index):
        source = []
        for coordinate, size, left, gap in zip(index, tensor.shape, low, interior):
            relative = coordinate - left
            interior_end = size + max(size - 1, 0) * gap
            if relative < 0 or relative >= interior_end or relative % (gap + 1) != 0:
                return value
            source.append(relative // (gap + 1))
        return tensor.access(source)
    return Tensor(shape, valid, read)


def reversed_tensor(tensor, axes):
    axes = frozenset(axes)
    valid = tensor.valid and axes.issubset(range(len(tensor.shape)))
    def read(index):
        source = tuple(size - coordinate - 1 if axis in axes else coordinate
                       for axis, (size, coordinate) in enumerate(zip(tensor.shape, index)))
        return tensor.access(source)
    return Tensor(tensor.shape, valid, read)


def concatenated(left, right):
    valid = left.valid and right.valid and left.shape[1:] == right.shape[1:]
    shape = (left.shape[0] + right.shape[0],) + left.shape[1:]
    def read(index):
        if index[0] < left.shape[0]:
            return left.access(index)
        return right.access((index[0] - left.shape[0],) + index[1:])
    return Tensor(shape, valid, read)


def broadcast_last(tensor, extent):
    return Tensor(tensor.shape + (extent,), tensor.valid and extent >= 0,
                  lambda index: tensor.access(index[:-1]))


def replay(rule, rank, fault, parameters, read_tensor):
    """Return pass only when the concrete model reproduces a rewrite violation."""
    report = {
        "result": "fail",
        "level": "independent_python_integer_tensor_replay",
        "native_application_replay": "not_run",
        "parameters": dict(parameters),
        "uf_reads": [],
        "limitation": "fixed-rank unbounded-integer adapter; native TensorRight/HLO not executed",
    }
    def fail(reason):
        report["reason"] = reason
        return report
    def vector(prefix, count=rank):
        return tuple(int(parameters[f"{prefix}_{axis}"]) for axis in range(count))
    def base(name, shape):
        return Tensor(shape, all(size >= 0 for size in shape),
                      lambda coordinates: fetch(name, coordinates))
    cache = {}
    def fetch(name, coordinates):
        key = (name, tuple(coordinates))
        if key not in cache:
            value = read_tensor(name, tuple(coordinates))
            if type(value) is not int:
                raise ValueError("input tensor callback must return an exact Python integer")
            cache[key] = value
            report["uf_reads"].append({"tensor": name, "coordinates": list(coordinates), "value": value})
        return cache[key]
    def nonempty_slice(shape, starts, ends, strides):
        return all(0 <= start < end <= size and stride > 0
                   for size, start, end, stride in zip(shape, starts, ends, strides))
    try:
        if not 1 <= rank <= 6:
            return fail("unsupported rank")
        n, index = vector("extent"), vector("index")
        precondition = all(size > 0 for size in n)
        a = base("A", n)
        if rule == "pad_restore":
            low, high, interior = vector("low"), vector("high"), vector("interior")
            precondition = precondition and all(v >= 0 for v in low + high + interior)
            pad = padded(a, low, high, interior, int(parameters["padding_value"]))
            starts = low
            ends = tuple(left + size + (size - 1) * gap
                         for size, left, gap in zip(n, low, interior))
            strides = (1,) * rank if fault == "unit_stride" else tuple(gap + 1 for gap in interior)
            lhs, rhs = sliced(pad, starts, ends, strides), a
        elif rule == "reverse_slice":
            starts, ends, strides = vector("start"), vector("end"), vector("stride")
            precondition = precondition and nonempty_slice(n, starts, ends, strides)
            if not precondition:
                return fail("input or rewrite precondition is false")
            axes = range(rank)
            lhs = sliced(reversed_tensor(a, axes), starts, ends, strides)
            # Find the final actual sampled source position, then mirror it.
            mirrored_starts = tuple(
                size - end if fault == "naive_mirror"
                else size - 1 - (start + ((end - start - 1) // stride) * stride)
                for size, start, end, stride in zip(n, starts, ends, strides))
            mirrored_ends = tuple(size - start for size, start in zip(n, starts))
            rhs = reversed_tensor(sliced(a, mirrored_starts, mirrored_ends, strides), axes)
        elif rule in {"concat_left", "concat_right", "concat_tail"}:
            bn = vector("second_extent")
            precondition = precondition and bn[0] > 0
            b = base("B", (bn[0],) + n[1:])
            starts, ends, strides = vector("start"), vector("end"), vector("stride")
            if rule == "concat_tail":
                cn = vector("third_extent")
                precondition = precondition and cn[0] > 0
                c = base("C", (cn[0],) + n[1:])
                joined, target = concatenated(concatenated(a, b), c), concatenated(b, c)
            else:
                joined = concatenated(a, b)
                target = a if rule == "concat_left" else b
            precondition = precondition and nonempty_slice(joined.shape, starts, ends, strides)
            precondition = precondition and (ends[0] <= n[0] if rule == "concat_left" else starts[0] >= n[0])
            lhs = sliced(joined, starts, ends, strides)
            if rule == "concat_left" or fault == "missing_offset":
                rhs_starts, rhs_ends = starts, ends
            else:
                rhs_starts = (starts[0] - n[0],) + starts[1:]
                rhs_ends = (ends[0] - n[0],) + ends[1:]
            rhs = sliced(target, rhs_starts, rhs_ends, strides)
        elif rule == "slice_broadcast":
            if rank < 2:
                return fail("slice_broadcast requires rank >= 2")
            starts, ends, strides = vector("start"), vector("end"), vector("stride")
            precondition = precondition and nonempty_slice(n, starts, ends, strides)
            if not precondition:
                return fail("input or rewrite precondition is false")
            original = base("A", n[:-1])
            lhs = sliced(broadcast_last(original, n[-1]), starts, ends, strides)
            inner = sliced(original, starts[:-1], ends[:-1], strides[:-1])
            extent = (ends[-1] - starts[-1] + strides[-1] - 1) // strides[-1]
            rhs = broadcast_last(inner, extent)
        elif rule == "nested_slice":
            s1, e1, p1 = vector("inner_start"), vector("inner_end"), vector("inner_stride")
            s2, e2, p2 = vector("outer_start"), vector("outer_end"), vector("outer_stride")
            precondition = precondition and nonempty_slice(n, s1, e1, p1)
            if not precondition:
                return fail("input or rewrite precondition is false")
            inner = sliced(a, s1, e1, p1)
            precondition = precondition and nonempty_slice(inner.shape, s2, e2, p2)
            if not precondition:
                return fail("input or rewrite precondition is false")
            lhs = sliced(inner, s2, e2, p2)
            starts, ends, strides = [], [], []
            for start1, step1, start2, end2, step2 in zip(s1, p1, s2, e2, p2):
                final_inner_index = start2 + ((end2 - start2 - 1) // step2) * step2
                starts.append(start1 + start2 * step1)
                ends.append(start1 + final_inner_index * step1 + 1)
                strides.append(step2 if fault == "dropped_inner_stride" else step1 * step2)
            rhs = sliced(a, starts, ends, strides)
        else:
            return fail("unsupported rewrite")
        report.update(lhs_shape=list(lhs.shape), rhs_shape=list(rhs.shape), index=list(index))
        if not precondition:
            return fail("input or rewrite precondition is false")
        if not lhs.valid or not in_bounds(index, lhs.shape):
            return fail("lhs operation or selected access is invalid")
        if not rhs.valid:
            violation = "rhs_invalid"
        elif lhs.shape != rhs.shape:
            violation = "shape_mismatch"
        elif not in_bounds(index, rhs.shape):
            violation = "rhs_access_invalid"
        else:
            left, right = lhs.access(index), rhs.access(index)
            report.update(lhs_value=left, rhs_value=right)
            if left == right:
                return fail("no concrete rewrite violation")
            violation = "element_mismatch"
        report.update(result="pass", violation=violation)
        return report
    except (KeyError, ValueError, IndexError, ZeroDivisionError) as error:
        return fail("concrete replay error: " + str(error))
