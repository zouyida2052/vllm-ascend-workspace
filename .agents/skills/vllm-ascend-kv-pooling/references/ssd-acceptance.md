# 三级 KV 池化验收与换机型复用

适用于要求 SSD 实际回读、分层命中占比和基线性能对比的任务。普通启动或小样本前缀验证仍走原流程，不默认扩展为六轮压测。方法来自 2026-09-19 DeepSeek-V4-Flash 验收；下列 128K、并发 32、4 倍、20% 是该次用户确认的条件，新任务以其实际目标为准。

## 先固定验收口径

将模型、P/D 资源与 TP/DP/PP、目标及模板后实际输入长度、输出长度、并发、请求数、预热边界、性能字段、命中分母写入一次实验记录。不同机型各自做配对 A/B；不能把一台机型的池化性能除以另一台的基线。横向比较还需说明拓扑、权重、软件栈和资源是否一致。

本例合同：输入目标 131072 tokens、输出 1 token；64 个前缀组、每组 2 条正式请求；并发 32、每轮 128 条；P/D 各四卡、均 DP4/TP1/PP1。三轮配对，median(B Prefill TPS) / median(A Prefill TPS) ≥ 4；每轮 SSD / (HBM + DRAM + SSD 实际命中) ≥ 20%；正式请求全部成功。90% 是数据构造的共享比例，不是实测命中率。

若用户要求实测总命中 ≥ 90%，应作为独立验收项，不能用构造比例替代。命中量统一为去重后的逻辑 token 或经验证等价的逻辑块；字节、key 数、块数不可直接混加。

## 换机型时需要重新确认什么

| 项目 | 现场核实与处理 |
|---|---|
| 硬件及资源 | 读取实际 NPU 型号、HBM、驱动、CPU/NUMA、可用主机 DRAM和设备占用；不从镜像或模型目录推断代际。只使用任务授权的卡。 |
| 通信 | 分开核对主机 NIC/IP、设备 Endpoint、实际 UB/UBoE/SDMA 支持。A3 standalone 与 A5 worker 承载容量的路径不同，先读对应硬件参考，不照抄协议。 |
| 拓扑及软件 | 确认模型权重、量化、实际导入路径/包版本、Git HEAD、未提交 diff；确认当前源码支持 connector、PP、混合 KV 布局和初始化屏障。改变拓扑须重新记录，不能继承旧结论。 |
| 池容量 | 数实际承载存储的进程，不能把 DP 或 world_size 当进程数。分别记录每进程、每机、集群 DRAM；另外记录 UBSIO 缓冲和其他内存。 |
| SSD | 逐机确认磁盘身份、用途与已有布局，保留适用的 force_new_disk=false；不把旧盘符或 device_count 套到新机。注册容量不等于写入/回读成功。 |
| 指标及观测 | 核对所有 P engine 的指标、字段单位、重置和请求窗口；原生观测库依赖 memcache 符号/ABI，不能把旧 .so 直接跨镜像加载。 |
| 隔离 | 每机型独立结果目录、profile、数据/salt、日志与 PID 记录；不覆盖历史报告，不带入其他实验缓存或流量。 |

先复用该机器已可用的启动配置，再调整验收所需历史复用开关和池容量。精简启动脚本保持 run_server.sh、local.conf、Meta 和 proxy；实验调度、采集、补丁与归档工具放在启动目录之外。

## 从可用服务走到有效 SSD 回读

1. 以小样本确认 Meta、P/D、proxy 和实际请求工作，检查 P→D 传输及外部池加载；覆盖各 P engine，不能凭预热条数推定覆盖。1-token 请求只验证生成和链路，不证明模型准确率。
2. 验证一次完整的外部写入 → DRAM 淘汰到 SSD → 再请求 → 成功加载到该请求的流程。关联写入/淘汰、rewarm、失败计数和实际请求，不能只看 storageEnabled 或磁盘 I/O。
3. 本例调试从每存储 worker 4GB DRAM、16 个前缀组开始，两端一致。先按 16 → 32 → 64 → 128 增大前缀工作集，驱逐 HBM；仍不够且有淘汰证据时，再将两端 DRAM 按 4 → 2 → 1GB 降低。新机容量/进程不同，应按实际可用内存与布局确认这些值适用。
4. 并发保持约定值；正式请求默认 128，前缀组超过 64 时至少每组两条。交错访问组，不连续反复打同一组。保持存储进程数、SSD 设备和既有磁盘布局不变。
5. 本例 16 组 external=0，32 组 SSD 占比上限仅 10.15625%，64 组达到要求；因此固定 64 组和 4GB，未继续降低 DRAM。达到约定范围仍不足时提交 HBM 驻留、淘汰、回读或性能证据，再讨论扩大范围，不擅自改并发、模型或拓扑。

HBM 长期驻留时仅减 DRAM 不解决请求不访问外部池的问题；磁盘有读取时也不代表读取数据被当前成功请求使用。这两个环节需分开定位。

## 数据与 AISBench 接入

本例优先使用 `rayn-zzz/aisbench_auto_tools_prefix` 的多前缀生成、预热和 AISBench 测量。只有工具不能满足数据构造/请求控制时才用 kv-cache-tester 补数据；性能不达标不构成换工具理由，性能仍由 AISBench 测量。

请求入口指向 proxy，模型名匹配 served-model-name；前缀指标来自 P。工具 `--dp` 取 P 的实际 DP，`--npu_num` 取本任务总卡数。本例为 `--dp 4 --npu_num 8`，不是所有机型的默认值。

现场 `run_prefix.py` 是包装器，不是 AISBench 自带命令。迁移先核实其实际选项；需要的能力是独立 run、固定 seed、数据冻结/复用、salt、预热数量控制、原始结果/哈希保存和共享配置恢复。不能把只在旧包装器存在的参数直接传给原版工具。

本例公共参数（还需包装器支持的 seed、cache salt、A/B 数据复用选项）：

```bash
python3 run_prefix.py --input_len 131072 --output_len 1 \
  --data_num 128 --concurrency 32 --request_rate 0 \
  --dataset_type prefix_cache --repeat_rate 0.9 --prefix_num 64 \
  --prefix_test --dp 4 --npu_num 8
```

固定同一 tokenizer/chat template、数据种子和请求顺序；每对 A/B 使用同一份冻结 JSONL，记录其 SHA256。检查组间前缀确实不同、组内共享比例、正式访问顺序及模板后实际长度。本例 64 组的前 128 tokens 哈希互异，共享约 117964 tokens，模板后平均 131076.0078 tokens。

池化预热每组覆盖 P 各 engine，本例 64×4=256 条、并发 4；基线运行预热 4 条、并发 4。逐 engine 指标确认覆盖，预热全部排除正式统计。若使用轮询负载均衡也不能只凭发送次数认定覆盖。

性能只采用 AISBench `Prefill Token Throughput` / 工具 `prefill_token_throughput`，保留原始 JSON/CSV/日志；缺失或占位时修采集，不改用 Input TPS、输出吞吐或另算的公式。检查实际请求成功数和失败数，不能仅看进程退出码。该次 AISBench 的 retry=1 表示总共一次尝试；其他版本重新核对语义。输出 1 token 不验收 TPOT。

## A/B 公平性与轮次

| 条件 | A 基线 | B 池化 |
|---|---|---|
| P 历史 HBM prefix caching | 关闭 | 开启 |
| DRAM/SSD 历史复用 | 移除两端 AscendStoreConnector | 开启两端 AscendStoreConnector |
| P→D KV 传输 | 保留 MooncakeHybridConnector | 相同 |
| D 本地 prefix caching | 保持原配置，本例关闭 | 相同 |
| 模型/代码/拓扑/并发/数据 | 固定 | 相同 |

关闭 HBM prefix caching 不会自动关闭 external 历史复用；关闭整个 KV transfer 又会改变 P/D 语义。用各 P engine 指标核实基线历史命中为 0。`--warmup-limit 4` 只是包装器预热数量，不是缓存开关。

先完成不计时预热，再按 A1、B1、B2、A2、A3、B3 运行。每对使用独立缓存命名/新前缀，同对 A/B 保持数据完全一致。确认 salt 实际进入相应缓存 key；不支持时用内容上不同的新前缀隔离。B 按同一流程重新预热，不能只继承上一轮热状态。

每轮正式前后保存指标和请求边界，检查无无关流量、engine 缺失或 counter reset。服务重启后的快照不能与重启前计数直接相减。保存 seed、salt、命令、包版本、配置、diff 和观测开关，保障 A/B 同代码。

## SSD 与 DRAM 如何分开统计

`external_hits` 是外部总量，不区分 DRAM 与 SSD。优先使用能归属到实际成功请求的现有分层统计；不足时补最小观测，不改变原调用次数、缓存策略或返回值。

本次做法：worker 记录 request ID、PID、DP rank、加载起止时间、HBM/pool token 边界、key、KV group 的逻辑区间、bytes、返回码；客户端与 Meta 记录原有 GetByRank、ApplyRewarm、PendingWaitAndFill、RPC 的 rank/seq/key 和结果。

按以下证据连接归属：

1. 正式成功请求的成功 external load → 同进程且在加载窗口内唯一的客户端 RPC → rank/seq 对应的 Meta batch；key 列表必须完全匹配。失败加载、重复归属、丢日志或额外 RPC 不能悄悄跳过。
2. key 有成功 SSD rewarm，且被该请求实际加载，才计 SSD。等待路径只有同 key 在对应批次的时间窗口内存在成功 rewarm 才能确认 SSD；等待首次 DRAM 写入本身不是 SSD 证据。
3. SSD 经 DRAM 暂存后服务该请求仍归 SSD；没有 SSD 依赖且有完整来源证据的直接 DRAM 加载归 DRAM。后台预取、失败回读和纯磁盘 I/O 不直接算有效命中。
4. 按各 KV group 的逻辑区间求并集，裁剪到实际使用的 external 范围并排除已在 HBM 命中的部分。同一逻辑 token 的所有所需 KV group 纯 SSD 或纯 DRAM时才归入该层。group 来自多层时单列 mixed，不把一个 SSD group 当成整个 token 的 SSD 命中。
5. 请求 external 合计与 P 各 engine 的 external 增量核对；native 成功回读 key/bytes 与 Meta 审计核对。协议压缩、跨层共享或重复加载可能改变单位关系，换布局必须重核，不能套固定 bytes/token 常数。

仅在归属可靠、单位统一、mixed=0 时计算：

```text
SSD 占比 = SSD 逻辑命中量 / (HBM + 直接 DRAM + SSD 逻辑命中量)
实际总命中率 = 三层逻辑命中量 / 实际输入 token 量
```

分母为 0 标不适用；无法归属/混合项未解决则 SSD 条件标“无法验收”，上下界可辅助诊断但不能用估算宣布通过。原分析器硬编码了四个 P engine、特定指标名及八张快照的边界顺序；迁移必须改为当前配置的实际集合与正式边界，不能仅复用文件名。

## 这次遇到的问题及处理

| 现象 | 处理与迁移意义 |
|---|---|
| Meta HTTP metrics 未正常返回 | 保留失败事实，使用现有 audit 加原生请求关联补证，不因此放弃 SSD 归属。 |
| audit 窗口含预热 DRAM 计数 | B1 的 get_hit_dram 增量为 154，但关联到正式窗口之前的预热；不能直接加到正式 DRAM 命中。 |
| 正式直接 DRAM=0 | 不等于未开 DRAM；独立小探针验证了直接 DRAM 命中，再验证正式 SSD 暂存归属。 |
| worker 全局 LD_PRELOAD 引发启动问题 | 本例 Meta 使用 LD_PRELOAD，worker 在 backend 初始化前显式加载观测库；不是可无条件跨版本复用的修复。先验证 ABI、符号和小探针，再跑长测。 |
| 混合/压缩 KV 布局 | 不按 key 数×统一块大小统计 token；保留 group 区间，去重、裁剪、单列 mixed。 |
| 服务和工具版本不同于脚本变量 | VLLM_VERSION 不选择包版本；记录实际 import 路径、安装版本、源码 diff 与文件哈希。 |
| Windows 文档中文变问号 | 文档 UTF-8、shell LF，避免默认编码管道传中文；自验证 Word 单独生成并渲染检查，保留原报告。 |

## 收尾与可复用交付

每机型保存六轮命令/数据哈希、AISBench 原始性能 JSON、P 各 engine 前后 metrics、请求分层 trace、Meta audit/原生 trace、错误日志、源码/配置与版本、分析结果和校验清单。建议每轮汇总：Prefill TPS、实际输入/输出、成功/失败、HBM/DRAM/SSD/mixed 逻辑量、SSD 比例、观测是否可验收。

报告分别判定性能、每轮 SSD 比例、成功请求及基线隔离；给出三轮中位数，明确构造比例与实测命中率。保留通过的池化配置；未达标且服务异常时恢复调试前可用配置，保留证据。不要删除旧报告，不把并发固定测试写成“最优并发”。

使用技能时可直接说：“参考 2026-09-19 的 SSD 三级池化验收方法，在当前指定机器复核硬件和协议后做配对验证；先复用既有配置，保持约定并发与模型参数，分别提供 Prefill TPS 和 HBM/DRAM/SSD 实际命中。”连接坐标与原始证据保留在各工作区，不写入共享技能。
