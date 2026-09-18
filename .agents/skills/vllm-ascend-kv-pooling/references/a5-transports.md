# A5 UB/UBoE：配置知识与来源

适用于 950DT/950PR。型号、宿主机网卡名、能够访问 Meta，都不能单独证明 KV 数据面协议。优先采用用户已确认的组网，再结合 NPU 链路、Endpoint 与实际传输日志核对。

## 官方来源与固定快照

资料读取日期：2026-09-18。这里维护任务所需的摘要，不复制整份动态 Wiki。

| 来源 | 阅读范围 | Wiki Git 快照 |
| --- | --- | --- |
| [HIXL 使用指南（构建中）](https://gitcode.com/cann/hixl/wiki/HIXL使用指南%28构建中%29.md#3-使能方式) | §3.1 指定协议与自动探测；协议/硬件约束 | `cann/hixl.wiki.git` @ `8982f84ec2fcca877f7046424b2cf15ff419c08d` |
| [池化使能 950DT 和 950PR 的 UB 和 UBoE](https://gitcode.com/Ascend/memcache/wiki/池化使能950DT和950PR的UB和UBoE.md) | 机型、MMC 协议选择和内核失败排查 | `Ascend/memcache.wiki.git` @ `14b2b7a642680b7b6d18be75a592c55e2ac5f3f0` |
| [Mooncake KVPool 指南](https://gitcode.com/cann/hixl/wiki/Mooncake%20KVPool指南.md) | UBoE 版本前提、异常断链设置 | 同一 HIXL Wiki 快照 |

网页无法读取时可通过官方 Wiki Git 仓库读取 Markdown，并保存 commit；不能仅凭搜索摘要声称读过全文。现场版本与 Wiki 不一致时，查实际安装版本/接口，保留限制，不把新文档能力套到旧镜像。

## 协议对应关系

| 实际目标 | HIXL/Mooncake 显式协议 | MMC local protocol |
| --- | --- | --- |
| Device UB | `ub_ctp:device` | `device_urma` |
| Device UBoE | `uboe:device` | `device_uboe` |

MemCache Wiki 区分：950 Pod 的 `device_urma` 使用 6 个出框口；950 Server 的 `device_urma` 使用 die0 的 8 个出框口，`device_uboe` 使用 die1 的参数面口。此为该文档描述，不应机械推广到其它机型/版本。

两条独立配置路径需要分别核对：HIXL 设置用于相应 TransferEngine；MMC 的 `protocol` 决定 MemFabric 池化数据路径。只修改 HIXL 环境变量，不会把 MMC 的 `device_urma` 自动变成 `device_uboe`。混用协议并非普遍禁止，但用户明确要求整条数据路径走 UBoE 时，两处都要配置 UBoE。

## 明确 UBoE 时的配方

在每个参与节点设置：

```bash
export ASCEND_GLOBAL_RESOURCE_CONFIG='{"comm_resource_config.protocol_desc":["uboe:device"]}'
unset ASCEND_LOCAL_COMM_RES
export ASCEND_AUTO_CONNECT=1
```

```ini
ock.mmc.local_service.protocol = device_uboe
```

HIXL §3.1.1 的显式协议方式允许自动生成相应 Endpoint；§3.1.2 的 `ASCEND_LOCAL_COMM_RES='{"version":"1.3"}'` 是不指定协议、自动选链路的方式。这里 unset 是为清除继承的手工/自动配置，并非宣称两个变量共存必然非法。需要手工 Endpoint 的现场应按实际版本配置对应 UBoE Endpoint，不无条件删除用户有效配置。

`ASCEND_AUTO_CONNECT=1` 的依据是 Mooncake KVPool 指南的异常心跳断链要求；该指南指出 Mooncake >= 0.3.12 已默认开启。它不是 MMC protocol 的替代，也不证明实际传输已成功。文档给出的 UBoE 自动配置前提为 CANN >= 9.1.0、HDK >= 25.6.rc1，仍须核对现场版本。

HCCL/GLOO/TP 的宿主机 NIC、`VLLM_HOST_IP`、Meta TCP 地址与 NPU UBoE bond IP 不是同一个概念。不要把宿主机 IP 直接写成 Device Endpoint。用 `hccn_tool -g -dev_info -i <device_id>` 检查链路与 bond 信息；它只能证明配置/链路状态，不能替代两端传输验证。

协议修订同步修改实际启动脚本与 MMC 配置；只有已有生成器时才同步其输入，避免下次覆盖回旧协议。不为此额外拆文件。检查两端目标协议；SSD 容量、盘路径、Meta 地址不因这个变更自动改变。

## MemCache 文档中的条件性操作

- 文档列出宿主机关闭自定义算子验签的前置操作。这是主机安全设置变化，不作为每次起池化的隐式步骤。先确认现场镜像/驱动要求和已有状态；需要改变时明确说明影响并取得对应授权，不把阅读文档当作执行授权。
- `device_urma` 出现 `AclrtSynchronizeStream ... 507018`，且 plog 同时指向 `libcann_hybm_kernel.so` / `HybmBatchWrite` 时，文档建议在推理前执行 `python -c "import memfabric_hybrid"`。保留这些触发条件；不能把任何 507018 或 UBoE 失败都归为同一问题。

这些资料是有出处的配置依据，现场功能证据另见 [PD 与 SSD](pd-ssd.md)：950DT、P DP2/PP2、D DP4/PP1 已完成小样本代理生成与外部前缀复用验证；两端 SSD 已启用，但 SSD 写入和回读尚未验证。
