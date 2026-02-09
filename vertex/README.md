# Vertex 生成相关命令

## 生成

下面按 `generate_cylinders_vertex.py` 的命令行参数逐个解释：

- `--out`
  - 输出的 `.vertex` 文件名（默认 `cylinder2d.vertex`）。这个文件会被 `input2d` 里的 `structure_names = "cylinder2d"` 对应读取。

- `--cyl x,y,r`
  - 添加一个圆柱（2D 里是“圆盘”）：圆心 `(x,y)`，半径 `r`。可重复多次来批量添加多个圆柱。

- `--cyl-file PATH`
  - 从文件批量读圆柱列表。
  - 支持 `.json`：内容是 `[{ "x":..., "y":..., "r":... }, ...]`
  - 支持 `.csv`：表头必须有 `x,y,r` 三列。

下面这些参数是用来决定“点阵间距” `dx,dy` 的（三选一，三种方式互斥，脚本按优先级选择）：

- `--dx DX` / `--dy DY`
  - 直接指定生成拉格朗日点的点阵间距（单位和你的坐标一致）。
  - `--dy` 不给时默认 `dy = dx`。
  - 默认值是 0.001953125

- `--lx LX --ly LY --nx NX --ny NY`
  - 按 `Cylinder2d.m` 的写法指定：`dx = lx/nx`，`dy = ly/ny`。
  - `--nx`/`--ny` 支持简单表达式（比如 `64*4*4*4*4`），就是为了复刻你 Matlab 里那种写法。

- `--input2d input2d`
  - 从 IBAMR 的输入文件里自动推导 **最细层** 的 `dx,dy`（会读 `x_lo/x_up`、`domain_boxes`、`N`、`MAX_LEVELS`、`REF_RATIO`）。

生成点云相关的选项：

- `--no-recenter`
  - 默认脚本会对“每个圆盘的点云”做一次重心修正（recenter）：先用点云算离散重心，再把点云平移到重心为 0，然后再加上你给的圆心 `(x,y)`。
  - 加了 `--no-recenter` 就跳过这一步（点云会因为离散采样导致重心可能有极小偏移）。

- `--split`
  - 除了写一个合并的 `--out` 之外，再额外为每个圆柱写一个单独文件：`out_0.vertex`, `out_1.vertex`, ...
  - 注意：你当前这个例子（`input2d`）只读取一个结构名 `"cylinder2d"`，所以真正用于仿真的还是合并后的 `--out`；`--split` 主要用于检查/可视化或以后扩展多结构时备用。

### 可直接使用命令

```sh   
cd vertex

```

## 可视化  

我给你加了一个可视化脚本：`plot_vertex.py`（不依赖第三方库，默认用 SVG 输出；如果你本机装了 `matplotlib` 也可以输出 PNG/hexbin）。

最常用（推荐，零依赖）：
- `python3 plot_vertex.py cylinder2d.vertex --backend svg --out cylinder2d.svg --max-points 200000`

参数说明（核心几个）：
- `vertex`：要画的 `.vertex` 文件路径
- `--backend auto|matplotlib|svg`
  - `svg`：不需要安装任何包，直接生成 `*.svg`
  - `matplotlib`：需要你环境里有 `matplotlib`，才能 `--show` 或输出高质量 png/pdf
  - `auto`：有 matplotlib 就用，没有就退回 svg
- `--out`：输出图片路径；不填时自动用 `<vertex>.svg`（svg）或 `<vertex>.png`（matplotlib）
- `--max-points`：点太多时随机采样到最多这么多个点（默认 200000；设为 `0` 表示不采样，可能会非常慢/文件很大）
- `--stride`：每隔 N 个点取 1 个（比如 `--stride 10` 会大幅减小绘图量）
- `--width/--height/--svg-point-radius`：只对 `--backend svg` 生效，控制 SVG 画布和点大小


### 可直接使用命令

```sh
cd vertex
python plot_vertex.py ../cylinder2d.vertex --backend matplotlib --out cylinder2d.svg --max-points 20000 --stride 100
```

## 静态矩形障碍物（.npy -> .vertex）

脚本：`generate_rect_obstacles_vertex.py`

输入：
- `centers.npy`：形状 `(N,2)`（或更多列，只取前两列），每行是矩形中心 `(x,y)`
- `sizes.npy`：形状 `(N,)` / `(N,1)` / `(N,2)`，每行是矩形尺寸 `(w,h)`；如果只有一个值则认为 `w=h`

示例：

```sh
cd vertex
python3 generate_rect_obstacles_vertex.py \
  --centers ./terrain1/static_obstacle_centers.npy \
  --sizes ./terrain1/static_obstacle_sizes.npy \
  --out ../static_obstacles.vertex

python3 plot_vertex.py ../static_obstacles.vertex --backend svg --out static_obstacles.svg --max-points 200000
```
