# DeepSeek V4 Flash 128K SSD 三级池化验证记录

Status: dated evidence，2026-09-19，服务器日志 UTC+08:00。

本记录汇总一次已完成验证，供其他机型参考。共享仓库仅保存脱敏的过程、配置和结果摘要，原始数据、trace、工作区 diff 及完整报告仍在原实验工作区；不代表其他型号已通过。操作方法见 [SSD 验收参考](../../.agents/skills/vllm-ascend-kv-pooling/references/ssd-acceptance.md)。

## 结论和范围

并发固定 32，池化 Prefill TPS 中位数 / 基线中位数 = 5534.9349 / 1076.5583 = **5.1413238837**。B1、B2、B3 的 SSD / 三层实际总命中均为 **39.0625%**。六轮各 128 条正式请求，**768/768 成功**，失败 0，服务日志检查未发现请求超时或服务错误。

按最终确认的 ≥4 倍、每轮 SSD ≥20%、全部请求成功的方案通过。90% 是共享前缀构造比例；实际总命中率 **87.4973%**，不满足另设的“实测命中至少 90%”。没有搜索最优并发，也没有验收长输出解码、模型准确率或 PP2。

## 测试环境及版本

| 项目 | 已验证配置 |
|---|---|
| 模型 | DeepSeek-V4-Flash，API 模型名 dsv |
| NPU / 拓扑 | Ascend950DT，每卡 96GiB HBM；双机各用卡 0–3，各 DP4/TP1/PP1 |
| CPU / 内存 | 双路 Kunpeng 950，P 为 7592C、D 为 7590；每机 384 逻辑 CPU、4 NUMA；主机总内存约 1505.39GiB |
| SSD | 两端均启用；各一块 HWE72P453T8L007N，3,840,755,982,336 bytes |
| 池化 | device_uboe；每端 4 存储 worker、每 worker 4GB DRAM，集群配置 32GiB；UBSIO buffer 5GB 参数另计；force_new_disk=false |
| OS / Python / npu-smi | Ubuntu 24.04.5 LTS、aarch64 / 3.11.10 / 25.6.rc1 |
| 容器 | host 网络；镜像 dev-26.2.0.day20260914-A5-py311-Ubuntu24.04-lts-aarch64 |
| vLLM | 0.28.1.dev0+g2cf0a6915.d20260917.empty；HEAD `2cf0a6915ce544dc493a0990f2ea38d81601128a` |
| vllm-ascend | 0.19.1rc2.dev2227+gd6acae3b6；HEAD `d6acae3b6ada1139704f98370cf8ec66bed1bdb8` |
| memcache_hybrid | 1.2.0；HEAD `4310d68fa8964a3032b71c09296b0ffa80330033` |
| AISBench / 前缀工具 | 3.1.20260630 / rayn-zzz/aisbench_auto_tools_prefix，上游参考 `2e6cd4af54307b96aca388e9666c172c991446a1`；运行副本另有哈希 |

两台宿主机其余卡有其他任务，因此是共享宿主机结果。实际网卡、IP、容器与绝对路径保留在原工作区；迁移不得推定新机器一致。源码存在未提交修改，仅 checkout 上述 HEAD 不能完整复现。

P/D 均 max-model-len=1048576、block-size=32、EP/async 开启、MTP=1、retention interval=4096。P batch tokens=3072、max seqs=8、HBM utilization=0.85、eager；D 分别为 256、56、0.92、FULL_DECODE_ONLY。P 开 prefix caching，D 保持关闭。AscendStoreConnector backend=memcache、use_layerwise=false；MooncakeHybridConnector 保留 P→D，D pool_init_barrier=true。两端 enable_cpu_binding 和 multistream_overlap_shared_expert 开启，D 另开 recompute_scheduler_enable。

## 执行过程与改动

早期曾验证 P DP2/PP2 的小样本外部复用，但当时未验证 SSD 回读。该阶段见 [PD/SSD 启动参考](../../.agents/skills/vllm-ascend-kv-pooling/references/pd-ssd.md)；本记录是后来 P/D 都 DP4/PP1、use_layerwise=false 的正式验收，不能合并成 PP2 性能结论。

1. 冻结验收口径、代码、拓扑、AISBench 指标与数据；确认基线只禁历史复用，保留 P→D 传输。
2. 两端 DRAM 从每 worker 25GB 改为 4GB，保持存储进程、SSD 和原磁盘布局；先核实外部写入、淘汰、实际回读链路。
3. 前缀工作集从 16 → 32 → 64 组；16 组 external=0，32 组 SSD 占比上限 10.15625%，64 组满足条件。32 组诊断有 8668 次成功 rewarm、5,128,486,912 bytes，失败 0。未使用 128 组，也未降到 2GB/1GB。
4. 完成直接 DRAM 小探针，验证归属器能区分 DRAM 与 SSD：HBM 12288、直接 DRAM 102400、SSD 0 逻辑 tokens。
5. 正式顺序 A1、B1、B2、A2、A3、B3。A 运行预热 4 条，B 前缀预热 256 条（64×DP4），预热并发 4，均排除；各 P engine 确认覆盖。
6. 归档六轮结果、配置、源码 diff 和哈希，恢复并保留池化脚本及 4GB 配置。健康状态引用验收结束 05:22 快照，硬件补采在 07:21，不表示当前仍在线。

本次添加/调整：run_prefix.py 的固定种子、数据复用/salt、预热控制和结果保存；pool_worker.py 的可选观测；meta_read_trace.cpp/.so 原调用追踪；analyze_hits.py、validate_data.py、acceptance_control.py、acceptance_rounds.py 及归档脚本。原生 memcache 库未替换，没有修改淘汰策略或 AISBench 公式。已有混合 KV/PP 页布局、池层索引和 D 初始化屏障修复在 A/B 中保留。

这些是原实验工作区的辅助脚本名，不是本仓库新提供的通用 CLI。启动器仍精简，Meta 两行、proxy 直接传参，server 用 pipefail 和 tee。观测开销未独立量化；移植原生 hook 前需核对源码、符号和 ABI。

## 数据和场景

64 个前缀组，128 正式请求，每组两条且交错访问；目标 131072 输入 tokens、输出 1 token。组内共享约 117964 tokens（89.9994%），组间前 128 tokens 哈希互异。模板后六轮平均输入均 131076.0078，实际输出 1。三对 seed=19101/19102/19103，各对独立 salt，同对复用完全相同的冻结 JSONL。

| 条件 | A | B |
|---|---|---|
| P 历史 HBM | 关闭 | 开启 |
| 两端池化 connector | 移除 | 开启 |
| P→D connector | 保留 | 保留 |
| D prefix caching | 关闭 | 关闭 |
| 并发 / 正式请求 | 32 / 128 | 32 / 128 |

性能来自 AISBench 原生 Prefill Token Throughput。最大实际并发均为 32，平均并发约 28.46～28.75（含升降载）；retry=1 在该版本仅一次尝试；1-token TPOT 占位值不用于验收。

## 六轮结果

| 轮次 | 工具 run ID | Prefill TPS | TTFT ms | HBM tokens | 直接 DRAM tokens | SSD tokens | SSD 占比 | 成功/失败 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | 20260919_032129_912048 | 1076.5583 | 121754.7 | 0 | 0 | 0 | 不适用 | 128/0 |
| B1 | 20260919_033636_106419 | 5523.1518 | 23732.1 | 8945664 | 0 | 5734400 | 39.0625% | 128/0 |
| B2 | 20260919_040248_827276 | 5534.9349 | 23681.6 | 8945664 | 0 | 5734400 | 39.0625% | 128/0 |
| A2 | 20260919_042916_815754 | 1076.4170 | 121770.7 | 0 | 0 | 0 | 不适用 | 128/0 |
| A3 | 20260919_044335_852837 | 1076.6292 | 121746.7 | 0 | 0 | 0 | 不适用 | 128/0 |
| B3 | 20260919_045900_895987 | 5535.1457 | 23680.7 | 8945664 | 0 | 5734400 | 39.0625% | 128/0 |

每轮 B 共有 50 个成功 external 加载请求，各 114688 逻辑 tokens、968 keys、571588608 bytes；总 48400 keys、28579430400 bytes（26.6167GiB），与 Meta 成功 rewarm 相等、失败 0。HBM+external=14680064，mixed=0。

归属依据是成功请求/worker → 唯一客户端 RPC → Meta rank/seq/key → 成功 SSD rewarm，再对混合 KV group 的 token 区间去重和裁剪；external 指标只作外部总量核对。SSD 经 DRAM 暂存仍归 SSD，不重复算 DRAM。B1 audit 的 get_hit_dram=154 属于窗口边界附近预热，未计入正式直接 DRAM。因此直接 DRAM=0 并非关闭 DRAM。

Meta HTTP metrics 未返回，使用 audit 与原生 trace；worker 全局 LD_PRELOAD 的早期失败尝试被排除正式轮次，改为显式加载观测库，Meta 保留 LD_PRELOAD。无法归属时应标无法验收，不以 I/O 或注册容量替代。

## 原始证据定位和下一机型复用

原实验归档目录名 `reports/deepseek_v4_flash_128k_20260919/`，配套同名 ZIP。`report.md`、`report.html` 是完整报告，另有单独 SSD 自验证 DOCX；本次沉淀不修改或删除它们。`FILES-SHA256.json` 是完整归档清单。

归档内 `launch/{126,127}` 保存启动配置与 diff；`tools/aisbench_auto_tools_prefix` 保存实际工具副本；`tools/operator` 保存调度工具；`evidence/acceptance` 保存 result.json、formal-state.json、analyze_hits.py、观测源码及六轮目录。每轮 `outputs/<时间戳>/performances/vllm-api-stream-chat/gsm8k.json` 为原始性能，`tier-hits.json` 为分层关联结果，`dataset-validation.json` 为数据核验。这里的路径是原归档索引，不表示原始附件已上传至本仓库。

下一机型先复核资源/通信/安装/存储进程与指标字段，再运行小样本直接 DRAM 和真实 SSD 回读，按容量与工作集证据调参，最后固定配置做该机型自己的六轮 A/B。保留用户确认的模型和并发，迁移不自动授权重装环境、格式化磁盘或更改其他任务服务。
