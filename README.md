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
