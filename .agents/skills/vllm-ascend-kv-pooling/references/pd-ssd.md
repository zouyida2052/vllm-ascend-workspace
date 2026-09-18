# PD 分离、PP、layerwise 与 SSD 池化

本文的 PP2、layerwise、每 worker 25GB 和末尾小样本结果属于早期启动验证。后续 2026-09-19 正式 SSD 验收改为 P/D 均 DP4/TP1/PP1、use_layerwise=false、每 worker 4GB；并发 32，Prefill TPS 中位数提升 5.1413 倍，三轮 SSD 占三层实际命中均 39.0625%。需要性能验收或换机型复用时读 [SSD 验收方法](ssd-acceptance.md)，不要把两阶段的配置和结果混合。本文 JSON 仍作为 PP2 启动示例保留，不代表最终验收配置。

## 精简脚本结构

复用现场已工作的脚本、安装路径与拓扑。仅整理脚本时不启动服务；一次性发现、检查和源码修复由 Agent 完成，不塞进每次启动命令。

| 文件 | 职责 |
| --- | --- |
| run_server.sh | 环境变量、直接填写参数的 vllm serve、内联 KV JSON、双路日志 |
| local.conf | 本节点协议、DRAM、SSD |
| meta.conf、run_meta.sh | Meta 配置与启动；明确配置是实际加载文件还是安装目录副本 |
| run_proxy.sh、proxy.py | 直接启动对应代理，仅代理节点需要 |
| README.md | 简短顺序、拓扑与源码前提 |

本次精简模式不再拆 config.env、env.sh、KV JSON、配置生成器、补丁应用、健康检查或总启动器。参数直接填写，不先命名再传参。已有自动化按用户需求保留；评测工具放在 scripts 之外。修改脚本不自动重启服务。

Meta 只需两行；交付时将路径直接替换为实际安装路径，不照搬其他镜像的 Python 版本：

```bash
export MMC_META_CONFIG_PATH=/ACTUAL/site-packages/memcache_hybrid/config/mmc-meta.conf
python3 -c 'from memcache_hybrid import MetaService; MetaService.main()'
```

若采用本地 meta.conf，上面的变量直接指向它，不能只修改副本。Proxy 示例中的 P_IP/D_IP 在交付前直接替换：

```bash
python3 -u "$(dirname "$(readlink -f "$0")")/proxy.py" \
  --host 0.0.0.0 --port 30350 --workers 1 \
  --prefiller-hosts P_IP --prefiller-ports 30351 \
  --decoder-hosts D_IP --decoder-ports 30352
```

复用实际仓库 `examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py` 的 P→D 实现并核对 CLI，不误用 D→P layerwise proxy。不在启动器加入 curl health、依赖安装或探测。

Server 开头使用 `set -o pipefail`，保留核实的 CANN/ATB 环境、库路径和模型参数；配置文件路径不依赖调用者 cwd：

```bash
export MMC_LOCAL_CONFIG_PATH="$(dirname "$(readlink -f "$0")")/local.conf"
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
```

完整 vllm serve 命令末尾接：

```bash
  2>&1 | tee "$(dirname "$(readlink -f "$0")")/server.log"
```

同时打屏和写文件，默认覆盖上次日志；要求追加时用 tee -a。pipefail 避免 tee 成功掩盖服务失败。`VLLM_VERSION` 不能选择运行时版本，本次实际 vLLM 提示未知变量，以导入路径、包版本和启动日志为准。

文件使用 UTF-8、LF；Windows 不通过默认编码的 shell 管道传递中文给 Python。同步核对真实远端路径和内容；要求镜像同步时仅删除明确属于同步范围的旧文件，保留日志和数据。SFTP 显示成功但新目录未变时，核对插件实际加载的 remotePath 与文件哈希，旧路径缓存不等于上传失败。

## 拓扑、通信和源码前提

本次验证组合：Ascend950DT、DeepSeek-V4-Flash，P 为 DP2×TP1×PP2，D 为 DP4×TP1×PP1，各用卡 0–3。这是配套源码与权重条件下的实例，不是任意版本的启动保证。改变 DP 也会影响 EP 分片与每卡权重。

本次 GLOO_SOCKET_IFNAME、TP_SOCKET_IFNAME、HCCL_SOCKET_IFNAME 均为 enp34s0f1；迁移时按实际 NIC 替换。VLLM_HOST_IP、HCCL_IF_IP 填各节点可达地址。宿主 NIC/IP 与 NPU UBoE Endpoint 分开核对，不根据模型目录名判断硬件。

UBoE 见 [协议配方](a5-transports.md)：HIXL 使用 `uboe:device`，MMC 使用 `device_uboe`，分别配置。UB 的 `device_urma` 不能直接当成 UBoE。HCCL 缓冲区、线程数、超时、分配器等沿用适用现场参数，不把单次调优值当通用默认。

P 的内联 JSON 示例：

```json
{"kv_connector":"MultiConnector","kv_role":"kv_producer","kv_connector_extra_config":{"pool_init_barrier":false,"connectors":[{"kv_connector":"MooncakeHybridConnector","kv_role":"kv_producer","kv_buffer_device":"npu","kv_port":"23001","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":2,"tp_size":1,"pp_size":2},"decode":{"dp_size":4,"tp_size":1,"pp_size":1}}},{"kv_connector":"AscendStoreConnector","kv_role":"kv_producer","kv_connector_extra_config":{"backend":"memcache","lookup_rpc_port":"0","use_layerwise":true}}]}}
```

D 将三处角色改为 kv_consumer、KV 端口改为 23002，本次配套实现使用 pool_init_barrier=true。两端 prefill/decode 元数据一致并匹配 CLI。use_ascend_direct 是沿用现场兼容参数，不能仅凭它证明传输路径生效。

- 当前 MooncakeHybridConnector 限制 Decode PP=1；换版本核对源码。
- 未显式指定 PP 层数时，当前 get_pp_indices 自动划分，43 层、PP2 实际为 22/21。显式划分时同步 connector 布局。
- 当前 get_zmq_rpc_path_lookup 将 lookup_rpc_port="0" 用于 `ipc://.../lookup_rpc_port_0_dp_rankN`，它是 IPC 名称的一部分，**不是 TCP 自动分配端口**。
- use_layerwise 指 AscendStoreConnector 按层池化，不代表换成 MooncakeLayerwiseConnector。
- 本次源码已包含 PP hybrid KV 页布局、池化本地/全局层索引、D 端池初始化 CPU barrier 修复。pool_init_barrier 是配套实现参数，不能当上游通用能力；核对实际导入包，不在启动器重复打补丁。
- D 请求 FULL_DECODE_ONLY，但日志因 layerwise 异步加载切换为 PIECEWISE。报告实际模式；故障时不静默取消池化或切 eager。

一个 API 管理本地 DP 与逐 rank 启动二选一，避免重复占卡。模型路径、量化、MTP、tokenizer/parser 和编译参数沿用匹配版本，不照搬其他模型示例。

## 两端 SSD 与容量

P、D 都开 SSD 时，两端 local.conf 均配置（磁盘逐机确认）：

```ini
ock.mmc.local_service.protocol = device_uboe
ock.mmc.local_service.dram.size = 25GB
ock.mmc.local_service.max.dram.size = 1024GB
ock.mmc.local_service.storage.enabled = true
ubsio.disk.path = /dev/SELECTED_DEVICE
ubsio.mem.size_in_gb = 5
ubsio.standalone.device_count = 4
ubsio.standalone.force_new_disk = false
```

两端指向同一 Meta/config store，本例端口 15100/16100，Meta 指标端口 18100；其余配置保留实际安装模板。world_size 不等于实际进程数。

device_count 对应本节点承载 DRAM 的实际 local-service 进程数，不意味着 A5 要启动 standalone。PP rank 也承载池；本次每端四进程各 25GB，约每机 100GiB、集群 200GiB，另算 UBSIO 和其他内存。

复用已有 UBSIO 盘保持 force_new_disk=false。改变设备数前核对旧布局兼容性，不自动初始化。新盘按路径/序列号、分区、挂载、swap、LVM/RAID/holders 与用途确认；裸盘可见不等于可以擦除。SSD 可在单端或双端，以用户要求为准。

## 启动和日志

顺序：Meta → P/D → proxy。Agent 按依赖检查就绪，检查不必写进启动器。分别验证 P/D health、models 和经 proxy 的真实非空合理生成；proxy 未实现 health 时 404 不等于故障。PID 或 HTTP 200 单独不足以证明 KV 正确。

mmc-meta.log 用于运行故障、连接、注册和容量信息。mmc-meta-audit.log 在本次版本周期输出 Metrics 汇总；warning 级别汇总本身不代表故障。观察 query、alloc、dram/ssd、evict_to_ssd、rewarm 等实际字段。Get 计数不一定覆盖 query 路径，不能直接当 vLLM 前缀命中率。

## AISBench 接入和验证边界

复用 aisbench_auto_tools_prefix，放在 scripts 旁。config.py 的 HOST_IP/HOST_PORT 指向 **proxy**，POD_INFO 指向 **P 的 metrics 地址**。WORK_PATH 是包含 ais_bench/ 的安装根目录；MODEL_NAME 匹配 served-model-name，MODEL_PATH 指向 tokenizer。仅在模型支持时设置关闭 thinking 的 chat template 参数。

已有隔离 wrapper run_prefix.py 时：

```bash
python3 run_prefix.py --input_len 8192 --output_len 1 \
  --data_num 4 --concurrency 1 --request_rate 0 \
  --dataset_type prefix_cache --repeat_rate 1.0 --prefix_test \
  --dp 2 --npu_num 8
```

wrapper 是本次工作区工具，不是 AISBench 自带入口，其他工作区先定位现有工具。dp=2 是 P 的 DP，控制工具预热条数/并发，不是 D 的 DP 或总卡数；npu_num=8 是两端总卡数。两次预热不能保证覆盖两个 DP，核对逐 engine 指标。repeat_rate 是构造输入的共享前缀比例，可设 0.5、0.8、1.0，不是实测命中率。

工具可能改写安装目录模型配置和 gsm8k train/test。复用已有隔离方式：独立 run 目录、共享文件锁、备份原文件/软链并在 finally 恢复，保存正式阶段前后原始 metrics。不能只看 ais_bench 与 tee 管道退出码，还要看真实成功/失败数与输出。

外部命中率为正式阶段 Δexternal_hits/Δexternal_queries，按 P 各 engine 汇总，排除预热、其他流量与计数器重置。禁用 HBM prefix caching 时 HBM 0/0 不代表外部池失效。输出 1 token 不报告 TPOT，四条小样本不作吞吐结论。

2026-09-19 现场：预热 2/2、正式 4/4 成功；external hit/query 增量 32768/32780，约 99.963%，正式平均 TTFT 511.4ms。仅证明该配置的小样本 PD＋外部前缀复用流程可用，不代表准确率或稳定性能评测。

早期小样本阶段两端 SSD 已启用并注册容量，但当时 SSD 使用量、evict_to_ssd、rewarm 都为 0，**该阶段未验证 SSD 写入和回读**。后续完整验证已完成，见上述 SSD 验收方法。完整验证需写入足量不同前缀触发淘汰，再重放早期前缀，联合 SSD 写入/读取、rewarm、失败计数、实际成功请求和外部命中证据。storageEnabled=1 或 external hit 高不等于 SSD 回读通过。

机器路径、源码版本和原始结果留在当前工作区 profile/run 记录，共享技能不保存私有 IP 和用户目录。
