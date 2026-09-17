# PD 分离叠加池化、PP、layerwise 与 SSD

## 从现有框架生成脚本

先读用户工作区实际版本的模型部署文档、PD 文档、已有 `run_server.sh` 与 proxy 实现；核对 Python 实际导入路径。editable vLLM 不代表 vllm-ascend 也 editable。不要照搬文档中的模型路径、量化、网卡、示例 IP、DP/TP 元数据；FP8 权重不能沿用 W8A8 示例的 `--quantization ascend`。保留用户图模式/MTP选择，故障时不静默切换 eager。

发现或补齐：P/D 主机与容器、模型、物理设备、DP/TP/PP、协议、Meta、每机容量、SSD 所在角色/设备、proxy 所在节点。SSD 只放 P 是一种部署选择，不是必需规则。能从用户指令和现有配置取得的内容不要重复询问。

用户要求所有脚本位于 `scripts/` 时，每端交付同目录的参数、环境、配置生成器、MMC 配置、KV JSON、服务启动、proxy 副本和检查脚本；日志、PID和备份也在其子目录。入口解析脚本自身目录，不能依赖调用者 cwd。原脚本先备份，修改文件用 UTF-8/LF。不要额外创建另一套隐藏启动目录。

推荐文件职责：`config.env` 保存参数；`env.sh` 加载环境并导出 MMC 路径；`prepare_pool.py` 从当前安装包模板生成私有 MMC 配置；`run_meta.sh`、`run_server.sh`、`run_proxy.sh` 分别启动；`start.sh` 可提供后台日志/PID；`check.sh`、`run_smoke.sh` 验证健康与真实请求。文件名不是强制接口，优先延用用户框架。

## 并行度与 connector

八卡示例：P 为 DP4×TP1×PP2，D 为 DP8×TP1×PP1。这个组合仅是已准备配置示例，不是已验证性能或可启动保证。所检查版本的 MooncakeHybridConnector 明确限制 Decode PP=1；换版本重新看源码。存储 worker 数必须由日志核实，不能只按 DP 数计算，PP rank 也可能承载存储。

两端 `MultiConnector` 的子 connector 都使用该端角色：P `kv_producer`、D `kv_consumer`。组合结构：

```json
{
  "kv_connector": "MultiConnector",
  "kv_role": "kv_producer",
  "kv_connector_extra_config": {
    "connectors": [
      {
        "kv_connector": "MooncakeHybridConnector",
        "kv_role": "kv_producer",
        "kv_buffer_device": "npu",
        "kv_port": "23001",
        "kv_connector_extra_config": {
          "prefill": {"dp_size": 4, "tp_size": 1, "pp_size": 2},
          "decode": {"dp_size": 8, "tp_size": 1, "pp_size": 1}
        }
      },
      {
        "kv_connector": "AscendStoreConnector",
        "kv_role": "kv_producer",
        "kv_connector_extra_config": {
          "backend": "memcache",
          "lookup_rpc_port": "0",
          "use_layerwise": true
        }
      }
    ]
  }
}
```

D 端替换角色及其 KV 基础端口；两端 prefill/decode 元数据必须一致并匹配真实启动参数。显式 PP 分层时同步 connector 的分层描述，检查模型/MTP支持和 hybrid cache group 对齐。`lookup_rpc_port=0` 是所检查版本的自动分配方式；核对实际版本。

这里 layerwise 是 **AscendStoreConnector 池化按层传输**，不是把 PD connector 换成 MooncakeLayerwiseConnector。按 connector 的请求路径选择 proxy：此例复用仓库 `examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py` 的 P→D 实现，并复制到 scripts；别误用 D→P layerwise proxy。端口、CLI 参数、依赖与健康接口以复制版本为准。

一个 API 管理本地 DP 与逐 rank 启动是两种方案。根据用户框架选择一种；不能保留逐 rank 的启动器，再额外启动内部完整 DP，造成重复占卡。文档中的 `--data-parallel-rank/address` 参数只在实际布局需要时保留。协议按 [A5 传输配置](a5-transports.md)，不因叠加 PD 把 UB 当成 UBoE。

## SSD 配置与容量

在承担 SSD 的节点生成：

```ini
ock.mmc.local_service.storage.enabled = true
ubsio.disk.path = /dev/SELECTED_DEVICE
ubsio.mem.size_in_gb = 5
ubsio.standalone.device_count = 8
ubsio.standalone.force_new_disk = false
```

`standalone.device_count` 是 UBSIO 的存储进程分配参数，不表示 A5 要启动 standalone 服务。值应等于本节点 DRAM 非零的实际 local-service 进程数。8 是示例，不能原样用于任意 DP/PP 配置。

在宿主机按路径和序列号确认 SSD，检查分区、文件系统、挂载、swap、LVM/RAID/holders、现有进程及测试授权。设备能在容器看到、不挂载，都不证明可独占或可擦除。记录容量与健康信息。复用既有 UBSIO 盘保持 `force_new_disk=false`，同时核对旧 device_count/布局；改变进程数不能直接沿用旧布局。新盘初始化或重新分区需单独明确目标及数据用途，不能默认擦盘。

DRAM 按每存储进程分配，设备协议按 1GiB 对齐。8×25GB 表示每机约200GiB，两机均如此则集群约400GiB；另加 UBSIO 内存池、模型及系统内存。为触发 SSD 淘汰可在授权测试配置中缩小 P 的 DRAM（例如每进程1GB），不要用该值静默替代用户生产容量。

## 启动和验收边界

1. 仅准备脚本：检查 Bash/Python 语法、生成后的 JSON/CONF、两端拓扑与环境一致性，打印真实导入路径。同步按文件核对，保留用户改动；不加载模型、不初始化 SSD，不称启动成功。
2. 请求启动：预检设备/端口/Meta；Meta → P → D → proxy，每层就绪后启动下层。不终止不属于本部署的进程；后台启动只报告 PID，不冒充 ready。
3. 基本服务：P/D health、models、经 proxy 的真实生成，确认非空且内容合理。核对 PD 收发日志；HTTP 200 或乱码内容不能证明 KV 传输正确。
4. 前缀池化：优先 `aisbench_auto_tools_prefix`，保留预热/重放的成功数和 external hit/query 增量。PD 工具参数按真实 P 侧 DP/路由含义核对，不能把卡数当作 DP。
5. SSD：先写入足够多不同长前缀，使日志出现实际 `evict_to_ssd` 和 SSD 写入；重放早期已淘汰前缀，检查 SSD Get/rewarm、磁盘读取、成功输出和外部命中增量。仅有 DRAM Get 或 external hit 不能证明 SSD 读取。记录 `rewarm_failed`/`get_not_found`；允许重算时仍需证明读取成功，不能用请求成功遮盖回读失败。

硬件层、PD层、DRAM命中、SSD写入、SSD回读分别报告通过/失败/未验证。既往曾出现“external hit 高、但 SSD read 为0、rewarm_failed 增长、输出为空”的失败，不能包装成三级池化成功；底层原因需结合实际日志另行定位。完成后按用户意愿保留服务。交付脚本不能升级成真实组合运行验证。
