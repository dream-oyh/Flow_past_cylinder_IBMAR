#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CylinderSpec:
    x: float
    y: float
    r: float


class _SafeExpr(ast.NodeVisitor):
    allowed_binops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow)
    allowed_unaryops = (ast.UAdd, ast.USub)

    def __init__(self, names: dict[str, float]):
        self._names = names

    def visit(self, node: ast.AST) -> float:  # type: ignore[override]
        return super().visit(node)

    def visit_Expression(self, node: ast.Expression) -> float:
        return self.visit(node.body)

    def visit_Name(self, node: ast.Name) -> float:
        if node.id not in self._names:
            raise ValueError(f"unknown name '{node.id}'")
        return float(self._names[node.id])

    def visit_Constant(self, node: ast.Constant) -> float:
        if not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
        return float(node.value)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        if not isinstance(node.op, self.allowed_unaryops):
            raise ValueError("unsupported unary operator")
        val = self.visit(node.operand)
        return +val if isinstance(node.op, ast.UAdd) else -val

    def visit_BinOp(self, node: ast.BinOp) -> float:
        if not isinstance(node.op, self.allowed_binops):
            raise ValueError("unsupported binary operator")
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Pow):
            return left**right
        raise ValueError("unreachable")

    def generic_visit(self, node: ast.AST) -> float:
        raise ValueError(f"unsupported expression node: {type(node).__name__}")


def _eval_expr(expr: str, names: dict[str, float]) -> float:
    tree = ast.parse(expr, mode="eval")
    return _SafeExpr(names).visit(tree)


def _parse_input2d_for_dx_dy(input2d_path: Path) -> tuple[float, float]:
    """
    Derive finest-level dx,dy from an IBAMR-style input2d file:
      - uses x_lo/x_up for domain size
      - uses domain_boxes upper indices for coarse cell counts (typically N, N/2)
      - uses MAX_LEVELS and REF_RATIO for finest-level refinement
    """
    text = input2d_path.read_text(encoding="utf-8", errors="replace")

    def find_scalar(name: str) -> float:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*([^\n/]+)", text, flags=re.MULTILINE)
        if not m:
            raise ValueError(f"could not find '{name} = ...' in {input2d_path}")
        expr = m.group(1).split("//", 1)[0].strip()
        return _eval_expr(expr, names={})

    # Pull N/MAX_LEVELS/REF_RATIO as plain numbers first.
    # (They are simple in this example; we keep the parser conservative.)
    n = int(find_scalar("N"))
    max_levels = int(find_scalar("MAX_LEVELS"))
    ref_ratio = int(find_scalar("REF_RATIO"))

    # x_lo and x_up are 2D vectors; read first two comps.
    def find_vec2(name: str) -> tuple[float, float]:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*([^,\n]+)\s*,\s*([^,\n]+)", text, flags=re.MULTILINE)
        if not m:
            raise ValueError(f"could not find '{name} = a, b' in {input2d_path}")
        a = m.group(1).split("//", 1)[0].strip()
        b = m.group(2).split("//", 1)[0].strip()
        return float(_eval_expr(a, {"N": n})), float(_eval_expr(b, {"N": n}))

    x_lo, y_lo = find_vec2("x_lo")
    x_up, y_up = find_vec2("x_up")
    lx = x_up - x_lo
    ly = y_up - y_lo

    # domain_boxes = [ (0,0) , (N , N/2) ]
    m = re.search(r"domain_boxes\s*=\s*\[\s*\(\s*0\s*,\s*0\s*\)\s*,\s*\(\s*([^,]+)\s*,\s*([^)]+)\)\s*\]",
                  text)
    if not m:
        # Fallback: assume Nx = N, Ny = N
        nx0 = n
        ny0 = n
    else:
        a = m.group(1).split("//", 1)[0].strip()
        b = m.group(2).split("//", 1)[0].strip()
        nx0 = int(_eval_expr(a, {"N": n}))
        ny0 = int(_eval_expr(b, {"N": n}))

    finest_factor = ref_ratio ** max(0, max_levels - 1)
    dx = lx / (nx0 * finest_factor)
    dy = ly / (ny0 * finest_factor)
    return dx, dy


def _disk_points(center_x: float, center_y: float, radius: float, dx: float, dy: float, recenter: bool) -> list[tuple[float, float]]:
    if radius <= 0.0:
        raise ValueError("radius must be > 0")
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("dx and dy must be > 0")

    num_pts_x = int(math.ceil(2.0 * radius / dx))
    num_pts_y = int(math.ceil(2.0 * radius / dy))

    pts: list[tuple[float, float]] = []
    for i in range(1, num_pts_x + 1):
        x = (i - 1) * dx - radius
        for j in range(1, num_pts_y + 1):
            y = (j - 1) * dy - radius
            if x * x + y * y <= radius * radius:
                pts.append((x, y))

    if not pts:
        raise ValueError("no points generated; try smaller dx/dy or larger radius")

    if recenter:
        xcom = sum(p[0] for p in pts) / len(pts)
        ycom = sum(p[1] for p in pts) / len(pts)
        pts = [(px - xcom, py - ycom) for (px, py) in pts]

    pts = [(px + center_x, py + center_y) for (px, py) in pts]
    return pts


def _parse_cylinders_from_args(cyl_args: list[str]) -> list[CylinderSpec]:
    cylinders: list[CylinderSpec] = []
    for item in cyl_args:
        parts = [p.strip() for p in item.split(",")]
        if len(parts) != 3:
            raise ValueError(f"--cyl expects 'x,y,r' but got: {item!r}")
        x, y, r = (float(parts[0]), float(parts[1]), float(parts[2]))
        cylinders.append(CylinderSpec(x=x, y=y, r=r))
    return cylinders


def _parse_cylinders_from_file(path: Path) -> list[CylinderSpec]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of objects like {'x':..., 'y':..., 'r':...}")
        out: list[CylinderSpec] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"JSON item {i} is not an object")
            out.append(CylinderSpec(x=float(item["x"]), y=float(item["y"]), r=float(item["r"])))
        return out

    if path.suffix.lower() == ".csv":
        out: list[CylinderSpec] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    out.append(CylinderSpec(x=float(row["x"]), y=float(row["y"]), r=float(row["r"])))
                except Exception as e:
                    raise ValueError(f"bad CSV row {i}: expected columns x,y,r") from e
        return out

    raise ValueError("unsupported cylinder file type; use .json or .csv")


def _write_vertex(path: Path, points: Iterable[tuple[float, float]]) -> None:
    pts = list(points)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"{len(pts)}\n")
        for x, y in pts:
            f.write(f"{x:.9f}\t{y:.9f}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate 2D IBAMR .vertex files for one or more filled cylinders (disks).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--out", type=Path, default=Path("cylinder2d.vertex"), help="Output .vertex filename")
    p.add_argument(
        "--input2d",
        type=Path,
        default=None,
        help="Parse dx/dy from an IBAMR-style input2d file (uses finest level spacing).",
    )
    p.add_argument("--dx", type=float, default=0.001953125, help="Point lattice spacing in x (overrides --input2d)")
    p.add_argument("--dy", type=float, default=0.001953125, help="Point lattice spacing in y (defaults to dx)")
    p.add_argument("--lx", type=float, default=None, help="Domain length in x (Cylinder2d.m style)")
    p.add_argument("--ly", type=float, default=None, help="Domain length in y (Cylinder2d.m style)")
    p.add_argument(
        "--nx",
        type=str,
        default=None,
        help="Grid count in x (Cylinder2d.m style; dx = lx/nx). Supports simple expressions like '64*4*4*4*4'.",
    )
    p.add_argument(
        "--ny",
        type=str,
        default=None,
        help="Grid count in y (Cylinder2d.m style; dy = ly/ny). Supports simple expressions like '32*4*4*4*4'.",
    )
    p.add_argument(
        "--cyl",
        action="append",
        default=[],
        help="Cylinder spec as 'x,y,r'. Repeat for multiple cylinders.",
    )
    p.add_argument("--cyl-file", type=Path, default=None, help="Cylinder list in .json or .csv (columns x,y,r)")
    p.add_argument(
        "--no-recenter",
        action="store_true",
        help="Do not recenter each cylinder's point cloud to remove discretization COM offset.",
    )
    p.add_argument(
        "--split",
        action="store_true",
        help="Also write per-cylinder files next to --out as out_0.vertex, out_1.vertex, ...",
    )

    args = p.parse_args(argv)

    cylinders = _parse_cylinders_from_args(args.cyl)
    if args.cyl_file is not None:
        cylinders.extend(_parse_cylinders_from_file(args.cyl_file))
    if not cylinders:
        raise SystemExit("No cylinders specified. Use --cyl x,y,r (repeatable) or --cyl-file cylinders.json/.csv")

    if args.dx is not None:
        dx = float(args.dx)
        dy = float(args.dy) if args.dy is not None else dx
    elif any(v is not None for v in (args.lx, args.ly, args.nx, args.ny)):
        if any(v is None for v in (args.lx, args.ly, args.nx, args.ny)):
            raise SystemExit("If using --lx/--ly/--nx/--ny, you must provide all four.")
        nx = _eval_expr(str(args.nx), {})
        ny = _eval_expr(str(args.ny), {})
        dx = float(args.lx) / float(nx)
        dy = float(args.ly) / float(ny)
    elif args.input2d is not None:
        dx, dy = _parse_input2d_for_dx_dy(args.input2d)
    else:
        raise SystemExit(
            "Need spacing: provide --dx (and optionally --dy), OR provide --lx/--ly/--nx/--ny, OR provide --input2d input2d"
        )

    recenter = not args.no_recenter
    all_points: list[tuple[float, float]] = []
    for i, spec in enumerate(cylinders):
        pts = _disk_points(spec.x, spec.y, spec.r, dx, dy, recenter=recenter)
        if args.split:
            per_path = args.out.with_name(f"{args.out.stem}_{i}{args.out.suffix}")
            _write_vertex(per_path, pts)
        all_points.extend(pts)

    _write_vertex(args.out, all_points)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
