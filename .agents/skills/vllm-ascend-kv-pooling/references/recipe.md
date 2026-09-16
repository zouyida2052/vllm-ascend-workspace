# Memcache 池化启动配方

以下步骤在选定的现有容器中执行。变量由 Agent 根据现场填入；不是要求用户提供的一组参数。

## 路径、资源及配置

用实际运行 vLLM 的 Python 查 `memcache_hybrid` 安装位置（如 `importlib.util.find_spec` / `pip show`），定位其 `config` 目录，不硬编码 Python 版本。解析而非执行待检查的启动脚本。确认端口属于谁；A3 主机核验所有将使用的 NPU 和 200GB DRAM 可用量。standalone 的 `store.init(0)` 使用可见设备 0，须把该设备也纳入占用检查，不能只检查 vLLM 使用的设备。

Meta IP 必须是 standalone、scheduler 和 worker 均可达的地址。host 网络可使用主机业务 IP；bridge 网络不能直接假定宿主机 IP 能在容器内 bind，须核验容器地址/端口发布。已有外部 Meta 只接入，不改写或重启它。

修改前把原文件保存到此次 run 目录，保留权限。对以下**精确键**执行更新或追加；保留注释及无关配置，重复的活动键需要消除歧义。不要全局替换所有 `127.0.0.1`（metrics URL 不属于本次替换范围）。standalone 文件首次从原 `mmc-local.conf` 复制；已有文件先备份再更新。

| 文件 | 键 | A3 值 |
| --- | --- | --- |
| mmc-meta.conf | ock.mmc.meta_service_url | tcp://META_IP:5000 |
| mmc-meta.conf | ock.mmc.meta_service.config_store_url | tcp://META_IP:6000 |
| mmc-local-standalone.conf | ock.mmc.meta_service_url | 与 Meta 完全相同 |
| mmc-local-standalone.conf | ock.mmc.local_service.config_store_url | 与 Meta config store 完全相同 |
| mmc-local-standalone.conf | ock.mmc.local_service.protocol | device_sdma |
| mmc-local-standalone.conf | ock.mmc.local_service.dram.size | 200GB |
| mmc-local-standalone.conf | ock.mmc.local_service.max.dram.size | 1024GB |
| mmc-local.conf | ock.mmc.meta_service_url | 与 Meta 完全相同 |
| mmc-local.conf | ock.mmc.local_service.config_store_url | 与 Meta config store 完全相同 |
| mmc-local.conf | ock.mmc.local_service.protocol | device_sdma |
| mmc-local.conf | ock.mmc.local_service.dram.size | 0GB |
| mmc-local.conf | ock.mmc.local_service.max.dram.size | 1024GB |

5000/6000 为缺少现有约定时的配方端口；已有明确端口则一致沿用。其他硬件场景若没有 standalone，不能把承载存储的 local service DRAM 也设为 0；采用已验证硬件配方，不泛化 A3 的 `device_sdma`。

## 启动顺序和验收

每条长运行命令提交为独立持久 job，保存标准输出/错误；原生库可能另写 `/var/log/memcache_hybrid`，从启动日志确认实际位置。SSH 断连后先查询 job/PID 与端口，不能重放不确定是否成功的启动动作。

### 1. Meta

```bash
export MMC_META_CONFIG_PATH="$CONFIG_DIR/mmc-meta.conf"
python3 -u -c 'from memcache_hybrid import MetaService; MetaService.main()'
```

**必须显式传配置路径**：已验证版本未设置此变量时会使用内置默认值，而不是自动读取修改后的安装目录文件。确认日志里的配置地址，并从消费者环境探测 Meta 和 config-store 端口；仅 localhost 可达不够。

### 2. A3 standalone

优先复用远端 `scripts/memcache_standalone.py`。缺失时复制 [模板](../assets/memcache_standalone.py) 到该路径；明确文件名，不沿用聊天记录里的截断路径笔误。

```bash
cd "$REMOTE_ROOT/scripts"
export MMC_LOCAL_CONFIG_PATH="$CONFIG_DIR/mmc-local-standalone.conf"
python3 -u memcache_standalone.py
```

如设置可见设备，`init(0)` 指的是其逻辑设备 0，记录实际物理设备。等待 `Successfully initialized memcache` 且日志证明配置加载成功、注册容量正确。200GB 主存分配可能持续数十秒；`D` 状态本身不证明死锁。

### 3. vLLM

从脚本自身目录执行 `bash run_server.sh`，为该 job 单独设置 `MMC_LOCAL_CONFIG_PATH=.../mmc-local.conf`，避免继承 standalone 配置。保留用户现有 TP/DP、MTP、图模式等选择，确认脚本最终使用：

```text
ASCEND_RT_VISIBLE_DEVICES=明确的设备列表
--kv-transfer-config '{"kv_connector":"AscendStoreConnector","kv_role":"kv_both","kv_load_failure_policy":"recompute","kv_connector_extra_config":{"lookup_rpc_port":"0","backend":"memcache"}}'
```

上面是已验证 connector 配方；已有合法等价配置可保留。`lookup_rpc_port=0` 是此版本自动分配方式，不保证所有版本支持。

已验证的 DeepSeek-V4 hybrid 场景还使用 `--no-disable-hybrid-kv-cache-manager --no-enable-prefix-caching`，目的是在关闭 HBM prefix cache 的情况下验证外部池化；不要因测试名含 prefix 就反向打开 HBM prefix cache。其他模型保留其适用配置。

按日志区分模型解析、worker、权重、MTP、compile、warmup、graph capture 和绑定阶段。持续有进度的加载不应被当作短超时失败；检查健康接口、模型列表和真实生成后才称 ready。独立 smoke 用与评测不同的短提示，避免污染测试前缀。

## AISBench 前缀验证

读取现有工具目录中的 README、config、命令构造及结果保存代码。若工具不在目标中，可从用户工作区传输该目录；没有可用副本时报告缺失，不凭空声称已测。

自动填写 `config.py`：

| 字段 | 值的来源 |
| --- | --- |
| DATASET_PATH | 远端工作目录下可写的生成数据集目录 |
| WORK_PATH | **包含** `ais_bench/` 的根目录；普通安装通常是 site-packages，editable 则是实际项目根目录。验证 configs/models/vllm_api 路径存在 |
| MODEL_NAME | `/v1/models` 返回的服务模型名 |
| MODEL_PATH | 同一模型的 tokenizer/权重目录 |
| HOST_IP / HOST_PORT | 从评测环境可达的 vLLM endpoint |
| OUTPUT_DIR | 本次唯一 run 目录下的 aisbench 输出目录 |
| POD_INFO | 单 API 入口通常留空；多个实际 metrics 入口按拓扑填入 |

只对评测进程关闭干扰内网请求的代理，或设置适当 NO_PROXY；不要改机器全局代理。检查 `ais_bench --help` 支持的参数及 tokenizer 能否加载。备份脚本将覆盖的既有模型配置链接、gsm8k test 链接和当前结果；不要干扰另一轮正在运行的 AISBench。

默认测试命令（DP_COUNT 取服务真实 DP，除非用户明确覆盖）：

```bash
cd "$REMOTE_ROOT/aisbench_auto_tools_prefix"
python3 aisbench_test.py --input_len 8192 --output_len 1 --data_num 4 \
  --concurrency 1 --request_rate 0 --dataset_type prefix_cache \
  --repeat_rate 1.0 --prefix_test --dp "$DP_COUNT"
```

这版工具的 `--dp` 同时控制预热条数/并发，不改变 vLLM DP。已验证的 `write_data` 会循环补足条数，因此 DP2 也能生成 4 条完全重复的正式数据。核对生成文件的实际条数；已明确传入 `--dp 4` 时保持原命令，不把服务改成 DP4。

先取指标快照，再预热，随后取新的快照并正式测试。以**正式测试前后差值**计算 `external_hits / external_queries`，同时查看每个 engine 的指标；缺失指标或分母为 0 表示无法验证，不能宣称 100%。关闭 HBM prefix cache 时 HBM 指标可能为 0/0，这是正常的独立指标。

`subprocess.run(..., shell=True, check=True)` 若执行的是 `ais_bench | tee`，退出码可能只来自 tee；必须核对 aisbench 错误、失败请求、结果文件、预热和正式测试各自完成情况。CSV 的 `99999` TPOT 是工具占位值，1-token 测试没有可解释的 TPOT。`--npu_num` 默认可能为 1，未设置真实数量时不要展示 CSV 的“单卡吞吐”。

聊天模板会使服务端实际输入超过 8192。一次验证中，每请求输入 8275、external hits 8192，整体命中为 99.00%；这说明输入口径不同，不应为凑足 100% 修改请求。若正式测试成功但没有外部命中，说明 serving 可用但池化验证未通过，继续检查 connector 的存取日志和计数。
