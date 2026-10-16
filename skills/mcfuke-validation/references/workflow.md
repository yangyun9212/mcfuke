# 配方、门店绑定和核价回执

需要保存或复用执行流程时读取本页。脚本从本技能目录运行，需要Python 3.10以上。命令里的文件名是工作文件示例，实际路径用独立参数正确引用。凭据由宿主连接器管理，不进入这些文件。

## 分享

```text
python scripts/run.py share --recipe meal.recipe.json --output meal-share.zip
```

生成的本地ZIP只有meal.recipe.json与card.md，不含门店、SKU、券、报价或执行授权；脚本不发布或发送。配方需先由用户接受目标和具体替代。名称自由文本仍需检查隐私。可分享配方不能直接当官方请求。

## 生成核价请求

宿主先读取本门店当前菜单和详情，建立内部绑定文件。实际官方参数以当前工具schema为准，脚本目前只支持已观测的到店、无券、普通商品/选配/特调字段。如果工具需要本程序不支持的字段，停止并说明需扩展适配，不删除字段凑成功。

绑定文件结构示例（纯虚构，recorded模式）：

```json
{
  "format": "mcfuke.bindings.v1",
  "mode": "recorded",
  "observed_at": "2026-10-15T17:00:00+08:00",
  "store": {"code": "TEST-STORE", "name": "虚构店"},
  "menu": [{
    "sku": "TEST-BINDING-1",
    "name": "测试餐品",
    "options": {},
    "available": true,
    "request_item": {"productCode": "TEST-FOOD", "quantity": 1},
    "detail_reference": "实际详情调用步骤或结果定位",
    "option_echo": "not_applicable"
  }]
}
```

mode可为simulated、recorded或live，必须与事实一致。observed_at是本轮资料的实际取得时间。sku是本地唯一绑定ID，不能把子选项编码冒充独立productCode。options只能从详情明确支持的规格建立，目前允许糖、冰量、温度、规格、配料。别名需由当前菜单/详情支持，不凭空补属性。

request_item可含modification.values和roundList/comboItemList，保留宿主根据真实schema构造的结构。绑定描述一份商品，根quantity=1；配方决定最终份数。option_echo是资料声明，脚本不会把它当作已认证的服务端回显：有规格要求或子选配时，评估结果仍标记待核实。

```text
python scripts/run.py prepare --recipe meal.recipe.json --bindings store-bindings.local.json --output quote-job.local.json
```

脚本匹配已接受候选，生成最多5个完整calculate-price请求和指纹。只处理当前门店，另一门店独立生成绑定。找不到匹配就报告，不补模拟数据。配方或绑定变化后重新生成任务。

若prepare返回needs_input/no_approved_match，requests为空，不调用calculate-price。保存同模式的空quotes回执后仍运行assess：新版会正常生成needs_input结果，指出缺少的餐单位置及需要用户选择的下一步。不会自动切店、换菜或断言何时恢复销售。live菜单观察仍需在600秒本地窗口内。旧版无指纹任务需从本轮绑定重新prepare，过期则先重新查询，不能手工补指纹。

## 宿主核价后核对回执

宿主使用任务requests中每个request调用真实calculate-price。保存原请求、可安全保留的响应、实际时间及步骤定位。不要只让模型抄一个总金额，也不要伪造响应或traceID。

回执顶层：

```json
{
  "format": "mcfuke.receipts.v1",
  "mode": "recorded",
  "quotes": []
}
```

每个quotes元素需含：request（完整实际调用参数）、response（实际成功响应中的success/code/data及data.price/originalPrice/discount/productList）、called_at（带时区ISO8601）、reference（实际调用定位）。选配未回显就保留未知，不能添加服务端没有返回的选项。

```text
python scripts/run.py assess --job quote-job.local.json --receipts quote-receipts.local.json --output quote-assessment.local.json
```

脚本检查任务是否被更改、请求是否属于当前门店和候选、数量回显和金额是否一致。live模式要求报价不早于资料，距本地复核不超过120秒、门店资料不超过600秒；这是本地复核窗口，不是官方价格有效期承诺。窗口过期需重新取得资料和核价，不把历史数据标为live。

所有候选尚未完成则状态为incomplete；有预算内候选则quoted；都超预算则needs_input并提供最低已核价候选及差额。检索受限，不能称全菜单最优。业务拒绝或网络错误的回执暂未建模为成功报价，遇到时如实终止该轮，保留错误，不能换用旧报价补齐。

指纹检测本地内容变化，不是授权签名，也不能认证远端来源。所有结果orderable=false，本版不创建订单；后续正式版本需单独实现方案确认、写入及不确定状态核对。

## 生成可读结果卡

```text
python scripts/run.py card --job quote-job.local.json --receipts quote-receipts.local.json --output quote-result.svg
```

card重新运行assess，不直接美化未核对的assessment。live过期时默认拒绝，缺失回执不会显示局部报价为完整结果。卡片包含预算、记录时间、已核价比较、变化与未知项；不显示结构化门店编码、SKU、个人券或追踪号。用户自填的配方名、餐品名等自由文本仍需检查隐私。

如果明确展示历史记录，可加`--historical-at`指定记录中真实的复核时点（含时区ISO8601），卡片始终醒目标记“历史记录回放·报价需重查”，不作为当前有效报价。不能为了让失败任务通过而虚构复核时间。recorded与simulated模式也分别标识历史和模拟。

结果卡保存价格和时间，不是可执行配方；需要复用目标时仍用share导出。SVG无脚本或外链，由标准库生成，无额外运行依赖。程序不发布或发送。
