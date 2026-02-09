#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Iterable, TextIO


def _iter_points(f: TextIO) -> Iterable[tuple[float, ...]]:
    """
    Reads IBAMR-style .vertex:
      first line: N
      following: x y [z]
    """
    first = f.readline()
    if not first:
        raise ValueError("empty file")
    try:
        n = int(first.strip().split()[0])
    except Exception as e:
        raise ValueError("first line must be an integer point count") from e

    for _ in range(n):
        line = f.readline()
        if not line:
            raise ValueError("unexpected EOF while reading points")
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"bad point line: {line!r}")
        if len(parts) == 2:
            yield (float(parts[0]), float(parts[1]))
        else:
            yield (float(parts[0]), float(parts[1]), float(parts[2]))


def _reservoir_sample(points: Iterable[tuple[float, ...]], k: int, rng: random.Random) -> list[tuple[float, ...]]:
    sample: list[tuple[float, ...]] = []
    for i, p in enumerate(points):
        if i < k:
            sample.append(p)
        else:
            j = rng.randrange(i + 1)
            if j < k:
                sample[j] = p
    return sample


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Visualize an IBAMR .vertex file (2D scatter, or XY projection for 3D).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("vertex", type=Path, help="Path to .vertex file")
    ap.add_argument(
        "--backend",
        choices=["auto", "matplotlib", "svg"],
        default="auto",
        help="Plot backend. 'svg' requires no Python packages; 'matplotlib' needs matplotlib installed.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output image path. Default: <vertex>.png (matplotlib) or <vertex>.svg (svg backend)",
    )
    ap.add_argument("--show", action="store_true", help="Display interactively (requires a GUI backend)")
    ap.add_argument("--title", type=str, default=None, help="Plot title")
    ap.add_argument("--mode", choices=["scatter", "hexbin"], default="scatter", help="Plot mode")
    ap.add_argument("--s", type=float, default=0.2, help="Marker size for scatter")
    ap.add_argument("--alpha", type=float, default=0.6, help="Marker alpha for scatter")
    ap.add_argument("--stride", type=int, default=1, help="Keep every Nth point (1 = keep all)")
    ap.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help="If too many points, randomly sample down to this many (0 disables sampling)",
    )
    ap.add_argument("--seed", type=int, default=0, help="Random seed for sampling")
    ap.add_argument("--gridsize", type=int, default=200, help="Hexbin gridsize (mode=hexbin)")
    ap.add_argument("--dpi", type=int, default=200, help="Output DPI (raster formats)")
    ap.add_argument("--width", type=int, default=1200, help="SVG width in pixels (backend=svg)")
    ap.add_argument("--height", type=int, default=700, help="SVG height in pixels (backend=svg)")
    ap.add_argument("--svg-point-radius", type=float, default=0.6, help="SVG point radius in px (backend=svg)")

    args = ap.parse_args(argv)
    vertex_path: Path = args.vertex
    if args.stride < 1:
        raise SystemExit("--stride must be >= 1")
    if args.max_points < 0:
        raise SystemExit("--max-points must be >= 0")

    # Decide backend
    have_matplotlib = False
    if args.backend in ("auto", "matplotlib"):
        try:
            import matplotlib  # type: ignore

            have_matplotlib = True
        except ModuleNotFoundError:
            have_matplotlib = False
            if args.backend == "matplotlib":
                raise SystemExit("matplotlib backend requested but matplotlib is not installed; use --backend svg")

    backend = "matplotlib" if (args.backend != "svg" and have_matplotlib) else "svg"
    if args.show and backend != "matplotlib":
        raise SystemExit("--show requires matplotlib; install matplotlib or omit --show")
    if args.mode == "hexbin" and backend != "matplotlib":
        raise SystemExit("--mode hexbin requires matplotlib; use --backend matplotlib")

    if args.out is None:
        out_path = vertex_path.with_suffix(".png" if backend == "matplotlib" else ".svg")
    else:
        out_path = args.out

    rng = random.Random(args.seed)

    with vertex_path.open("r", encoding="utf-8", errors="replace") as f:
        pts_iter = _iter_points(f)
        if args.stride != 1:
            pts_iter = (p for i, p in enumerate(pts_iter) if (i % args.stride) == 0)

        if args.max_points == 0:
            points = list(pts_iter)
        else:
            points = _reservoir_sample(pts_iter, args.max_points, rng)

    if not points:
        raise SystemExit("No points to plot (check --stride/--max-points).")

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    pad = 0.02 * max(x_max - x_min, y_max - y_min, 1.0)
    x0, x1 = x_min - pad, x_max + pad
    y0, y1 = y_min - pad, y_max + pad

    if backend == "matplotlib":
        # Use a non-interactive backend unless the user asked to show.
        import matplotlib  # type: ignore

        if not args.show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore

        fig, ax = plt.subplots(figsize=(7, 4))
        if args.mode == "scatter":
            ax.scatter(xs, ys, s=args.s, alpha=args.alpha, linewidths=0)
        else:
            hb = ax.hexbin(xs, ys, gridsize=args.gridsize, mincnt=1, bins="log")
            fig.colorbar(hb, ax=ax, label="log10(count)")

        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_title(args.title if args.title is not None else vertex_path.name)
        ax.set_xlim(-8, 24)
        ax.set_ylim(-8, 8)
        # ax.set_xlim(x0, x1)
        # ax.set_ylim(y0, y1)
        fig.tight_layout()
        fig.savefig(out_path, dpi=args.dpi)
        if args.show:
            plt.show()
        plt.close(fig)
    else:
        # Minimal dependency-free SVG scatter.
        w = int(args.width)
        h = int(args.height)
        if w <= 0 or h <= 0:
            raise SystemExit("--width/--height must be > 0")
        rx = (x1 - x0) if (x1 - x0) != 0 else 1.0
        ry = (y1 - y0) if (y1 - y0) != 0 else 1.0

        def sx(x: float) -> float:
            return (x - x0) / rx * w

        def sy(y: float) -> float:
            # flip y for SVG (y down)
            return h - (y - y0) / ry * h

        title = args.title if args.title is not None else vertex_path.name
        r = float(args.svg_point_radius)
        opacity = float(args.alpha)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as out:
            out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            out.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n')
            out.write(f'  <title>{title}</title>\n')
            out.write('  <rect width="100%" height="100%" fill="white"/>\n')
            out.write(f'  <g fill="black" fill-opacity="{opacity}">\n')
            for x, y in zip(xs, ys):
                out.write(f'    <circle cx="{sx(x):.3f}" cy="{sy(y):.3f}" r="{r:.3f}"/>\n')
            out.write("  </g>\n")
            out.write("</svg>\n")

    print(f"Wrote {out_path}")
    print(f"Plotted {len(points)} points (stride={args.stride}, max_points={args.max_points}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
