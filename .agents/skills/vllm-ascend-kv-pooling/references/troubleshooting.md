# 已验证故障与判据

下表是 2026-09-16 单节点 A3、DeepSeek-V4-Flash W8A8 MTP、TP4/DP2 的实际经验。A5 的容器库冲突、逐 worker DRAM 容量及 negate_sin 兼容案例见 [a5.md](a5.md)。这些不是所有新环境都需要执行的修复清单。

| 现象 | 核验证据 | 对应处理 |
| --- | --- | --- |
| Meta 仍监听 127.0.0.1，修改 conf 无效 | stdout 提示 MMC_META_CONFIG_PATH 未设置；原生日志显示内置地址 | 给 Meta job 显式设置该变量，停止自己启动的错误实例后重启；核验两个端口 |
| 设备选择没有生效 | 脚本变量拼成 ASCEND_RT_CISIBLE_DEVICES | 修为 ASCEND_RT_VISIBLE_DEVICES，并从 worker 绑定日志确认物理设备 |
| 模型路径不存在 | 检查目录、config、tokenizer、量化描述、权重索引所有分片 | 只在同一模型/量化/MTP 候选唯一时纠正路径；不能把 FP8 模型当作 W8A8 替代 |
| cannot import SamplingParams from vllm (unknown location) | 从源码父目录启动，find_spec 显示 loader=None 和外层仓库路径；从 scripts 目录解析正常 | 从 scripts 目录执行；必要时明确包根 PYTHONPATH，优先避免改变已安装源码 |
| _get_packed_kv_cache_groups 不存在 | Ascend 按版本选择 main 分支，但 git describe --tags --exact-match HEAD 是 v0.28.0；对应 release API 存在，editable 版本误为 0.28.1.dev0 | 此条件下设置 VLLM_VERSION=0.28.0 选择已有 release 分支；任意 dev 版本不能照抄。真实源码不兼容需要另外对齐，不能伪装版本 |
| /dev/shm 64 MiB，小于需求 160 MiB | 完整异常、df、容器 IPC/mount 信息 | 能在容器内安全 remount 时提高 tmpfs 上限，如 mount -o remount,size=16G /dev/shm；不会立即占满 16G。该在线改动可能在容器重建/重启后丢失，应记录并在复用时检查；不擅自重建容器 |
| Triton npu_utils.cpp 链接失败，找不到 /lib64/libc.so.6 | /usr/lib64/libc.so linker script 引用了缺失路径；相同架构的 /lib/aarch64-linux-gnu/libc.so.6 已存在 | 仅在缺失路径和兼容库已确认、且目录属于容器时补单个软链，不替换 glibc；先执行 import torch_npu._inductor 并读取 Triton target 验证，再重启 vLLM |
| standalone 分配阶段长时间无新日志 | 日志已完成 device_sdma/200GB 参数校验，进程和内存分配仍推进 | 等待明确成功/失败，不能在注册完成前启动依赖；也不能仅按 D 状态判断失败 |

诊断先检查 stdout、stderr 和库自身日志里的第一个实际异常，不以最后的 `Engine core initialization failed` 为根因。失败重试保留单独日志。停止顺序为本任务 vLLM → standalone → Meta；若只重试 vLLM 且池化仍健康，无需重启池化。PID 可能成为僵尸或复用，停止前检查命令、启动时间/job 身份；`/proc/PID` 存在不等于仍监听。

## 这次完成的证据

- 使用容器中原有的两个 editable 源码安装，无源码切换或包重装。
- Meta 的 5000/6000 端口可达；standalone 注册容量 214748364800 bytes。
- vLLM TP4、DP2、EP8；使用物理设备 8–15，standalone init(0) 使用另一可用设备。
- 健康检查、模型列表及 1-token smoke 均成功。
- 显式评测参数 `--dp 4` 被保留；它对应预热数量/并发，服务仍为 DP2。
- 正式 4 条全部成功；平均 TTFT 312 ms，P90 318.1 ms。
- 两个 engine 各命中 16384/16550；总计 32768/33100（99.00%）。
- Meta、standalone、vLLM 在收尾时继续运行。

上述指标只证明该小样本流程打通，不是后续运行的保证或回归性能基线。机器坐标和完整原始日志保存在工作区本地 profile/run 记录中，不归入通用技能。
