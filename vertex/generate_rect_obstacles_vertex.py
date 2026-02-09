#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import math
import struct
from dataclasses import dataclass
from functools import reduce
from operator import mul
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Literal, Sequence, TextIO


@dataclass(frozen=True)
class NpyArray:
    shape: tuple[int, ...]
    data: list[float]

    @property
    def ndim(self) -> int:
        return len(self.shape)

    def get(self, *idx: int) -> float:
        if len(idx) != self.ndim:
            raise IndexError("index dimensionality mismatch")
        flat = 0
        stride = 1
        for size, i in zip(reversed(self.shape), reversed(idx)):
            if i < 0 or i >= size:
                raise IndexError("index out of bounds")
            flat += i * stride
            stride *= size
        return float(self.data[flat])


def _read_exact(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise ValueError("unexpected EOF")
    return b


def _parse_dtype(descr: str) -> tuple[str, int, Literal["f", "i", "u"]]:
    """
    Parse numpy dtype descriptors like '<f8', '<f4', '<i4', '<u2', '|u1'.
    Returns: (endian, itemsize, kind)
    """
    if len(descr) < 3:
        raise ValueError(f"unsupported dtype descr: {descr!r}")
    endian = descr[0]
    kind = descr[1]
    try:
        itemsize = int(descr[2:])
    except Exception as e:
        raise ValueError(f"bad dtype itemsize: {descr!r}") from e
    if endian not in ("<", ">", "|", "="):
        raise ValueError(f"unsupported dtype endian: {descr!r}")
    if kind not in ("f", "i", "u"):
        raise ValueError(f"unsupported dtype kind: {descr!r}")
    if kind == "f" and itemsize not in (4, 8):
        raise ValueError(f"unsupported float itemsize: {descr!r}")
    if kind in ("i", "u") and itemsize not in (1, 2, 4, 8):
        raise ValueError(f"unsupported int itemsize: {descr!r}")
    return endian, itemsize, kind  # type: ignore[return-value]


def _dtype_to_struct_fmt(endian: str, itemsize: int, kind: str) -> str:
    if endian in ("|", "="):
        # Assume native/little is fine for our use-case; '=' means native endianness.
        endian = "<"
    prefix = "<" if endian == "<" else ">"
    if kind == "f":
        code = "f" if itemsize == 4 else "d"
    elif kind == "i":
        code = {1: "b", 2: "h", 4: "i", 8: "q"}[itemsize]
    elif kind == "u":
        code = {1: "B", 2: "H", 4: "I", 8: "Q"}[itemsize]
    else:
        raise ValueError("unreachable")
    return prefix + code


def load_npy(path: Path) -> NpyArray:
    """
    Minimal .npy loader for numeric (non-object) arrays.
    Supports:
      - C-order arrays
      - dtype: f4/f8, i1/i2/i4/i8, u1/u2/u4/u8
      - little/big-endian
    """
    with path.open("rb") as f:
        magic = _read_exact(f, 6)
        if magic != b"\x93NUMPY":
            raise ValueError(f"not a .npy file: {path}")
        ver_major, ver_minor = struct.unpack("BB", _read_exact(f, 2))

        if (ver_major, ver_minor) == (1, 0):
            header_len = struct.unpack("<H", _read_exact(f, 2))[0]
        elif (ver_major, ver_minor) in ((2, 0), (3, 0)):
            header_len = struct.unpack("<I", _read_exact(f, 4))[0]
        else:
            raise ValueError(f"unsupported .npy version: {(ver_major, ver_minor)}")

        header = _read_exact(f, header_len).decode("latin1")
        try:
            header_dict = ast.literal_eval(header.strip())
        except Exception as e:
            raise ValueError(f"could not parse .npy header: {path}") from e
        if not isinstance(header_dict, dict):
            raise ValueError(f"bad .npy header dict: {path}")

        descr = header_dict.get("descr")
        fortran_order = header_dict.get("fortran_order")
        shape = header_dict.get("shape")
        if not isinstance(descr, str) or not isinstance(fortran_order, bool) or not isinstance(shape, tuple):
            raise ValueError(f"unsupported .npy header fields: {path}")
        if fortran_order:
            raise ValueError("Fortran-ordered .npy not supported")

        endian, itemsize, kind = _parse_dtype(descr)
        fmt = _dtype_to_struct_fmt(endian, itemsize, kind)

        if not shape:
            # scalar
            count = 1
            shape = (1,)
        else:
            if any((not isinstance(s, int) or s < 0) for s in shape):
                raise ValueError(f"bad shape in .npy: {shape}")
            count = reduce(mul, shape, 1)

        raw = f.read()
        needed = count * itemsize
        if len(raw) < needed:
            raise ValueError(f"unexpected EOF: expected {needed} bytes, got {len(raw)}")
        raw = raw[:needed]

        values: list[float] = []
        for (v,) in struct.iter_unpack(fmt, raw):
            values.append(float(v))

        return NpyArray(shape=tuple(int(s) for s in shape), data=values)


def _as_centers(arr: NpyArray) -> list[tuple[float, float]]:
    if arr.ndim == 1:
        raise ValueError("centers must be 2D (N,2)")
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"centers must have shape (N,2) (or more columns); got {arr.shape}")
    n = arr.shape[0]
    return [(arr.get(i, 0), arr.get(i, 1)) for i in range(n)]


def _as_sizes(arr: NpyArray, n: int) -> list[tuple[float, float]]:
    if arr.ndim == 1:
        if arr.shape[0] != n:
            raise ValueError(f"sizes length mismatch: centers={n}, sizes={arr.shape[0]}")
        out = []
        for i in range(n):
            w = float(arr.get(i))
            out.append((w, w))
        return out
    if arr.ndim == 2:
        if arr.shape[0] != n:
            raise ValueError(f"sizes length mismatch: centers={n}, sizes={arr.shape[0]}")
        if arr.shape[1] == 1:
            out = []
            for i in range(n):
                w = float(arr.get(i, 0))
                out.append((w, w))
            return out
        if arr.shape[1] >= 2:
            return [(float(arr.get(i, 0)), float(arr.get(i, 1))) for i in range(n)]
    raise ValueError(f"unsupported sizes shape: {arr.shape}; expected (N,), (N,1), or (N,2)")


def _grid_counts(half_w: float, half_h: float, dx: float, dy: float) -> tuple[int, int]:
    if half_w <= 0.0 or half_h <= 0.0:
        raise ValueError("obstacle size must be > 0 in both directions")
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("dx and dy must be > 0")
    # Keep points within [-half_w, +half_w] and [-half_h, +half_h]
    nx = int(math.floor((2.0 * half_w) / dx + 1e-12)) + 1
    ny = int(math.floor((2.0 * half_h) / dy + 1e-12)) + 1
    return max(nx, 1), max(ny, 1)


def _iter_rect_points(cx: float, cy: float, w: float, h: float, dx: float, dy: float) -> Iterable[tuple[float, float]]:
    half_w = 0.5 * w
    half_h = 0.5 * h
    nx, ny = _grid_counts(half_w, half_h, dx, dy)
    x0 = cx - half_w
    y0 = cy - half_h
    for i in range(nx):
        x = x0 + i * dx
        for j in range(ny):
            y = y0 + j * dy
            yield (x, y)


def _write_vertex(path: Path, points: Iterable[tuple[float, float]], count: int) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(f"{count}\n")
        for x, y in points:
            f.write(f"{x:.9f}\t{y:.9f}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a filled-rectangle obstacle .vertex file from two .npy arrays (centers and sizes).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--centers", type=Path, required=True, help="Path to centers.npy with shape (N,2[+])")
    ap.add_argument("--sizes", type=Path, required=True, help="Path to sizes.npy with shape (N,), (N,1), or (N,2)")
    ap.add_argument("--dx", type=float, required=False, default=0.03, help="Point spacing in x")
    ap.add_argument("--dy", type=float, required=False, default=0.03, help="Point spacing in y")
    ap.add_argument("--out", type=Path, default=Path("static_obstacles.vertex"), help="Output .vertex file")

    args = ap.parse_args(argv)

    centers_arr = load_npy(args.centers)
    centers = _as_centers(centers_arr)
    sizes_arr = load_npy(args.sizes)
    sizes = _as_sizes(sizes_arr, n=len(centers))

    dx = float(args.dx)
    dy = float(args.dy)

    # First pass: count points without storing them all.
    total = 0
    rect_meta: list[tuple[float, float, float, float, int, int]] = []
    for (cx, cy), (w, h) in zip(centers, sizes):
        half_w = 0.5 * float(w)
        half_h = 0.5 * float(h)
        nx, ny = _grid_counts(half_w, half_h, dx, dy)
        rect_meta.append((cx, cy, float(w), float(h), nx, ny))
        total += nx * ny

    def all_points() -> Iterable[tuple[float, float]]:
        for cx, cy, w, h, nx, ny in rect_meta:
            half_w = 0.5 * w
            half_h = 0.5 * h
            x0 = cx - half_w
            y0 = cy - half_h
            for i in range(nx):
                x = x0 + i * dx
                for j in range(ny):
                    y = y0 + j * dy
                    yield (x, y)

    _write_vertex(args.out, all_points(), total)
    print(f"Wrote {args.out} ({total} points) from {len(centers)} rectangles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

