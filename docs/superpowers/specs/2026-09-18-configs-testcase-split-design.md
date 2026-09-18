# diag 工具重构设计：configs/testcase 分离 + 27 解锁配置化

日期：2026-09-18
状态：已确认（log 优化暂不做，本期不涉及）

## 背景与目标

tools/diag 目前的问题：

1. `configs/<项目>/xxx.yaml` 单文件同时承载配置（can/diagnostic/timing）与流程
   （steps/on_fail），同一项目的总线/寻址参数在每个流程文件里重复。
2. `security.py` 硬编码 N60（cpj_baic）的三个安全等级；其他项目算法完全不同
   （如 RX/cpj_cmc 是自定义 mask_table 运算），无法复用。

目标：

- **配置与流程分离**：configs/ 只放配置，新建 testcase/ 放流程，run 命令显式
  传两个文件，同一流程可换配置跑不同项目。
- **27 解锁配置化**：每个项目的解锁参数（子功能号、长度、密钥材料）写入该项目
  配置；算法以命名注册表形式实现在 security.py；develop（61/62，公司内部通用）
  内置默认，所有项目自动可用。

非目标：log 文件优化（本期不做）。

## 1. 目录结构与命令

```
configs/                     # 纯配置：can / diagnostic / timing / security
  N60/n60.yaml
  RX/rx.yaml
  T1LFL1/t1lfl1.yaml
  example.yaml               # 真机示例
  example_mock.yaml          # 免硬件示例
testcase/                    # 纯流程：steps / on_fail / 可选 timing 覆盖
  N60/probe_1363_nvm.yaml
  N60/verify_1363_final.yaml
  ...
  RX/verify_f4cmc59.yaml
  T1LFL1/verify_2cf5_clear_2a1e.yaml
  example.yaml               # 与 configs/example.yaml 配套的示例流程
  example_mock.yaml           # 与 configs/example_mock.yaml 配套的示例流程
```

- `run <testcase.yaml> --config configs/N60/n60.yaml`：--config 必填。
- `probe` / `chat` / `unlock` 用法不变（只吃 --config）；`unlock` 的可选等级
  与 chat 的 `unlock <level>` 改为从该配置的 security 节读取（加内置 develop）。
- 日志目录前缀（logs/<前缀>_<时间戳>/）由 testcase 文件名 stem 决定（现为
  config stem）。
- on_fail 属于流程行为，随 testcase。
- testcase 可带可选 `timing` 节覆盖配置默认值（仅覆盖出现的键）。存量流程的
  p2star_ms 有 5000/8000/15000 三档，迁移时各自保留原 timing，行为不变。

## 2. security 配置节

项目配置新增可选节 `security`（键为等级名，值含算法与参数）：

```yaml
security:
  app:
    seed_sub: 0x01          # 取种子子功能号
    key_sub: 0x02           # 发密钥子功能号，校验必须 = seed_sub + 1
    algorithm: cmc_mask_app # security.py 算法注册表中的名字
    params:                 # 算法参数，随算法而定
      mask_table: ["f7cbc641", "c81dc123", ...]  # 16 个 u32
  boot:
    seed_sub: 0x11
    key_sub: 0x12
    algorithm: cmc_mask_boot
    params:
      mask_table: [...]
```

种子/密钥长度由算法固有长度决定（见下表），不在配置中重复声明。

### 算法注册表（security.py）

| 算法名 | 用途 | params | 密钥计算 |
| --- | --- | --- | --- |
| `aes_cmac_df_token` | N60 app/boot | `key_factor`（16B hex） | DF 随机 16B；SK=CMAC(KF,DF)，Key=DF‖CMAC(SK,Seed)，key_len=32 |
| `aes_cmac_seed` | develop（61/62） | `key`（16B hex）、`magic`（16B hex，取种子请求携带） | Key=CMAC(key,Seed)，key_len=16 |
| `cmc_mask_app` | RX app | `mask_table`（16×u32） | 按 cpj_cmc seedToKeyApp 移植 |
| `cmc_mask_boot` | RX boot | `mask_table` | 按 cpj_cmc seedToKeyBoot 移植 |

- 每个算法是一个纯函数 `compute_key(seed, params) -> bytes`，加可选的
  种子请求附加字节（如 magic）。
- 新客户算法 = 新增一个函数并注册；参数仍走配置。

### develop 内置默认

- `security.py` 内置 develop 等级（seed_sub 0x61 / key_sub 0x62 / 16B 种子
  魔数 / 16B 密钥 / `aes_cmac_seed` + 公司 DEV_KEY），所有项目自动可用，
  unlock/chat/流程步骤 `security_access: develop` 均可引用。
- 配置 `security.develop` 可显式覆盖（公司内部约定不变时无需写）。

### 校验（加载期，沿用未知字段即报错原则）

- 子功能号 0x01–0x7E，key_sub == seed_sub + 1。
- params 按算法 schema 校验（hex 字节数、mask_table 必须 16 项）。
- 算法名必须在注册表内。

## 3. 代码改动范围

- `flow.py`：
  - `load_config(path)` → 只加载并校验配置（can/diagnostic/timing/security），
    不再要求 steps；
  - 新增 `load_testcase(path)` → 加载并校验 steps/on_fail；
  - `run_flow(cfg, testcase, driver, run_dir)` 接收两者；security_access 步骤
    的等级查配置（+内置 develop）。
- `security.py`：删除硬编码 LEVELS/KF/DEV_KEY 常量中项目相关部分；保留
  develop 内置默认；实现算法注册表与 RX 两个 mask 算法（对照
  cpj_cmc `seedToKeyApp`/`seedToKeyBoot` 逐行移植，注意 u32 截断与
  大端序：密钥按 Key[0]<<24|Key[1]<<16|Key[2]<<8|Key[3] 发送）；
  `perform_unlock` 改为接收等级定义对象。
- `diag.py`：run 加必填 `--config`；unlock 的 argparse choices 改为运行时
  从配置取；chat 的 unlock 同理；日志前缀取 testcase stem。
- `tests/`：test_security（配置驱动构建等级 + 两个 RX 算法用 C 源码
  手算期望值做向量单测）、test_flow_config（配置/流程分别校验）、
  test_run_flow、test_diag_cli 同步更新。
- `README.md`：目录结构、命令、security 节、算法表说明全部更新。

## 4. 存量迁移（一次性）

现有 12 个 yaml 拆分：

- `configs/N60/*.yaml`（7 个）→ can/diagnostic/timing 去重合成
  `configs/N60/n60.yaml`（security 节按 n60 算法补全：app/boot
  aes_cmac_df_token + 对应 KF）；steps/on_fail → `testcase/N60/<原名>.yaml`。
- `configs/RX/*.yaml`（2 个）→ `configs/RX/rx.yaml`（app/boot 用 cmc_mask 算法，
  mask_table 从 cpj_cmc 源文件抄）；steps → `testcase/RX/<原名>.yaml`。
- `configs/T1LFL1/*.yaml`（1 个）→ `configs/T1LFL1/t1lfl1.yaml` + testcase
  （T1LFL1 解锁算法暂缺源码，security 节先不配，需要时再补）。
- `configs/example.yaml`、`example_mock.yaml` → 拆成
  configs/example*.yaml + testcase/example.yaml、example_mock.yaml。
- 迁移用一次性脚本做（写完跑一次即删），拆分后人工抽查。

RX 的 app/boot 子功能号暂按 01/02、11/12 配置（Dcm 生成配置未直接确认），
真机自测时验证。

## 5. 验证

- `python -m pytest tests/ -v` 全绿；example_mock 配置 + example 流程
  mock 全流程跑通（run/probe/chat/unlock 四命令冒烟）。
- RX 真机自测（用户执行）：`chat` 先 1003 再 `unlock app` 验证 2B 种子
  → 4B 密钥的 mask 算法与子功能号；N60 回归 `unlock app`。
