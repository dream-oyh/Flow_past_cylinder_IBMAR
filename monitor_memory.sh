#!/bin/bash
# 追踪 main2d 内存占用 vs 仿真步数
# 用法: bash monitor_memory.sh [采样间隔秒数, 默认30] [内存阈值百分比, 默认70]
# 输出: memory_trace.csv

export LC_ALL=C

INTERVAL=${1:-30}
MEM_THRESHOLD_PCT=${2:-${MEM_THRESHOLD_PCT:-70}}
LOGFILE_DEFAULT="output.log"
OUTFILE="memory_trace.csv"

pick_logfile() {
    if [ -f "$LOGFILE_DEFAULT" ]; then
        echo "$LOGFILE_DEFAULT"
        return 0
    fi
    if [ -f "IB2d_cylinder2d.log" ]; then
        echo "IB2d_cylinder2d.log"
        return 0
    fi
    ls -1t *.log 2>/dev/null | grep -v '^monitor\.log$' | head -1
}

LOGFILE="$(pick_logfile)"
if [ -z "$LOGFILE" ]; then
    echo "找不到仿真日志文件（尝试过 $LOGFILE_DEFAULT / IB2d_cylinder2d.log / *.log）。"
    echo "建议用重定向启动仿真，例如：nohup mpirun -np 4 ./main2d input2d > output.log 2>&1 &"
    exit 1
fi

get_mem_used_pct() {
    # 基于 MemAvailable 计算系统内存使用率，避免 buff/cache 误差
    awk '
        /MemTotal/ { total_kb = $2 }
        /MemAvailable/ { avail_kb = $2 }
        END {
            if (total_kb > 0 && avail_kb >= 0) {
                used_kb = total_kb - avail_kb
                printf "%.1f", (used_kb * 100.0 / total_kb)
            } else {
                print ""
            }
        }
    ' /proc/meminfo 2>/dev/null
}

get_mem_summary() {
    # 输出: "<used_pct> <used_mb> <total_mb>"
    awk '
        /MemTotal/ { total_kb = $2 }
        /MemAvailable/ { avail_kb = $2 }
        END {
            if (total_kb > 0 && avail_kb >= 0) {
                used_kb = total_kb - avail_kb
                used_pct = used_kb * 100.0 / total_kb
                used_mb = used_kb / 1024.0
                total_mb = total_kb / 1024.0
                printf "%.1f %.1f %.1f", used_pct, used_mb, total_mb
            } else {
                print ""
            }
        }
    ' /proc/meminfo 2>/dev/null
}

shutdown_main2d() {
    local ts msg pids
    ts="$(date '+%Y-%m-%d %H:%M:%S')"

    msg="[$ts] 内存超过阈值，准备关闭 main2d（优雅终止 SIGTERM -> 强制 SIGKILL）"
    echo "$msg"

    # 优先关闭启动器（mpirun/mpiexec/orterun），让 MPI ranks 一起退出
    pids="$(
        { pgrep -f 'mpirun.*main2d' 2>/dev/null || true; \
          pgrep -f 'mpiexec.*main2d' 2>/dev/null || true; \
          pgrep -f 'orterun.*main2d' 2>/dev/null || true; } \
        | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//'
    )"
    if [ -n "$pids" ]; then
        echo "[$(date '+%H:%M:%S')] 发送 SIGTERM 到 MPI 启动器 PID(s): $pids"
        kill -TERM $pids 2>/dev/null || true
    fi

    # 同时尝试终止 main2d 本体进程
    pids="$(pgrep -x main2d 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    if [ -n "$pids" ]; then
        echo "[$(date '+%H:%M:%S')] 发送 SIGTERM 到 main2d PID(s): $pids"
        kill -TERM $pids 2>/dev/null || true
    fi

    sleep 10

    if pgrep -x main2d >/dev/null 2>&1; then
        pids="$(pgrep -x main2d 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
        echo "[$(date '+%H:%M:%S')] main2d 仍在运行，发送 SIGKILL PID(s): $pids"
        kill -KILL $pids 2>/dev/null || true
    fi
}

echo "timestamp,timestep,sim_time,total_rss_MB,num_procs,max_proc_rss_MB,mem_used_pct" > "$OUTFILE"
echo "开始监控 (每 ${INTERVAL}s 采样一次)，输出到 $OUTFILE；内存阈值: ${MEM_THRESHOLD_PCT}%"
echo "使用日志文件: $LOGFILE"
echo "按 Ctrl+C 停止"
echo ""

while true; do
    # 检查 main2d 是否还在运行
    if ! pgrep -x main2d > /dev/null 2>&1; then
        MEM_SUMMARY="$(get_mem_summary)"
        if [ -n "$MEM_SUMMARY" ]; then
            read -r MEM_PCT MEM_USED_MB MEM_TOTAL_MB <<< "$MEM_SUMMARY"
            echo "[$(date '+%H:%M:%S')] main2d 已停止运行，监控结束；系统内存: ${MEM_PCT}% (${MEM_USED_MB} MB / ${MEM_TOTAL_MB} MB)"
        else
            echo "[$(date '+%H:%M:%S')] main2d 已停止运行，监控结束；系统内存: N/A"
        fi
        break
    fi

    MEM_USED_PCT="$(get_mem_used_pct)"
    if [ -n "$MEM_USED_PCT" ] && awk -v used="$MEM_USED_PCT" -v th="$MEM_THRESHOLD_PCT" 'BEGIN { exit !(used > th) }'; then
        echo "[$(date '+%H:%M:%S')] 系统内存使用率 ${MEM_USED_PCT}% > ${MEM_THRESHOLD_PCT}%"
        shutdown_main2d
        break
    fi

    # 获取所有 main2d 进程的 RSS (kB)
    RSS_DATA=$(ps -C main2d -o rss --no-headers 2>/dev/null)
    if [ -z "$RSS_DATA" ]; then
        sleep "$INTERVAL"
        continue
    fi

    NUM_PROCS=$(echo "$RSS_DATA" | wc -l)
    TOTAL_RSS_KB=$(echo "$RSS_DATA" | awk '{sum+=$1} END {print sum}')
    MAX_RSS_KB=$(echo "$RSS_DATA" | sort -n | tail -1)
    TOTAL_RSS_MB=$(echo "scale=1; $TOTAL_RSS_KB / 1024" | bc)
    MAX_RSS_MB=$(echo "scale=1; $MAX_RSS_KB / 1024" | bc)

    # 从日志中提取最新的时间步和仿真时间（用最后一个字段，避免依赖 grep -P）
    LAST_STEP=$(grep "At beginning of timestep #" "$LOGFILE" 2>/dev/null | tail -1 | awk '{print $NF}')
    SIM_TIME=$(grep "Simulation time is" "$LOGFILE" 2>/dev/null | tail -1 | awk '{print $NF}')

    LAST_STEP=${LAST_STEP:-0}
    SIM_TIME=${SIM_TIME:-0}

    NOW=$(date '+%Y-%m-%d %H:%M:%S')

    # 写入 CSV
    echo "$NOW,$LAST_STEP,$SIM_TIME,$TOTAL_RSS_MB,$NUM_PROCS,$MAX_RSS_MB,${MEM_USED_PCT:-}" >> "$OUTFILE"

    # 终端输出
    printf "[%s] 步数: %-8s  时间: %-8s  总RSS: %7s MB (%s进程)  最大单进程: %s MB  系统内存: %s%%\n" \
        "$(date '+%H:%M:%S')" "$LAST_STEP" "$SIM_TIME" "$TOTAL_RSS_MB" "$NUM_PROCS" "$MAX_RSS_MB" "${MEM_USED_PCT:-N/A}"

    sleep "$INTERVAL"
done

echo ""
echo "=== 监控结果已保存到 $OUTFILE ==="
echo "可用以下命令查看趋势:"
echo "  column -t -s',' $OUTFILE"
