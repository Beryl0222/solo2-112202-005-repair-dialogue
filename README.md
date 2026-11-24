# 返修咨询中的排除证据与工位接力

业务资料使用虚构编号和合成叙述。文本保持来件拼写和措辞。各表是独立的来源记录。
TSV 文件采用 UTF-8，首行为列名。行首 id 是该表的稳定记录号，空格和标点属于原文，空单元格表示来件未填。
日期时间采用所在地本地时间。资料内注明为编号列表的字段用竖线分隔。

## 资料字段

- `materials/conversation.tsv`。turn 是接件内顺序。refer_to 表示系统里可直接看到的引用消息，空值表示没有显式引用。
- `materials/manual.tsv`。manual 为手册发版号，route 是检修路径名。文字描述可观察条件及适用工位。
- `materials/stations.tsv`。availability 为本班实际设备状态。handoff 是已经交接的工作，空值表示没有登记。
- `materials/prior_check.tsv`。recorded_at 为检查实际发生时间；config 保留原记录的组件和负载信息。
- `materials/loan_log.tsv`。asset 是对照台编号，retained_state 是移动检具时会改变的物理配置。

## 运行资料检查

使用 CPython 3.11.2 和标准库。资料检查仅读取输入。

```sh
python3 pack_check.py
python3 -m unittest -v
```

## 返修接力（repairs 包）

`repairs/` 把"用户描述 → 复测 → 派修"的接力建成记录：

- `model.py`。观察 = 来源 × 配置（机头/机身）× 工况（档位/负载）× 结论，空位一律是"未记录"。
  "没试"就是未试，不读成"没响"；否定某一档位/负载不取消其他工况的观察。
- `extract.py`。把会话原文保守转写成观察：指代（"还是这样"）记下解析来源，
  转述只留痕不进证据，"更正"追加订正版并保留原记录。
- `casefile.py`。对账（同配置同工况复测可推翻口述，条件对应不上则保留差异）、
  接力计划（先问 / 先做鉴别检查 / 派修）与用户可查的接件状态。
- `knowledge.py`。手册路径的适用前提与检查；原因候选只来自 manual.tsv。
- `stations.py` / `assets.py`。按工位本班能力与检查前置条件派修，不按问题词数；
  同一组对照件只托给一个未完成的检修；会拆改用户封装的检查，动手前列出将不可复现的观察。

```sh
python3 -m repairs        # 全部案号的接力计划与接件状态
python3 -m repairs R7     # 只看一案
```

工位已签收不代表故障已确认；已完成的检查留原证据，不因换担当而消失。
