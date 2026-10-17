# 必须条件、偏好与替代确认（v0.1.1）

这是一份显式候选的条件文件mcfuke.intent.v1，可编译为原有recipe.v1；不是任意属性自动搜索的新配方协议。“杯型不限”和“无糖饮料不限品牌”需先澄清并列出具体候选，不能直接执行不限。

## 对话流程

1. 读取meal.intent.json或按用户输入建立条件草稿；旧meal.recipe.json仍可按旧方式使用，不能假称旧配方有新增条件确认。
2. 运行review，将must_keep、preferences、allowed_changes用普通语言分别展示。问清只换杯还是允许换其他饮料，不把沉默、跳过或仅问问题当作接受。
3. 用户明确同意展示的这一版条件后，使用review输出的intent_fingerprint运行confirm。条件变更则重新review并等待确认；不由模型自行改候选后沿用旧确认。
4. 从本店当前菜单/详情建立绑定，运行prepare --intent --confirmation，用原生MCP执行requests并保存真实回执。
5. assess和card也必须提供当前intent与confirmation，程序会阻止旧条件任务继续使用。不要转用--recipe绕过确认；那会丢失条件版本检查。

确认只表示用户接受这份目标及替代范围，指纹不认证人的身份或工具来源，不是下单授权。没有当前官方MCP连接时只做模拟，不伪造真实数据。

## 文件格式

```json
{
  "format": "mcfuke.intent.v1",
  "name": "测试无糖餐单",
  "budget_fen": 4000,
  "preference_policy": "balanced",
  "slots": [{
    "label": "饮料", "quantity": 1,
    "required_options": {"糖": "无糖"},
    "substitution": "size_only",
    "choices": [
      {"name": "测试无糖中杯", "item_group": "测试同款饮料", "options": {"规格": "中杯"}},
      {"name": "测试无糖大杯", "item_group": "测试同款饮料", "options": {"规格": "大杯"}}
    ]
  }]
}
```

以上餐品为虚构。随包assets/flexible-cup.intent.json是实际餐品名称的目标模板，不保证当前可售或在预算内，仍需本人接受条件并实时查证。

预算和份数是确认后的必须条件。required_options目前只允许糖、规格、冰量、温度、配料等已支持属性；这些值会合并进每个具体候选，冲突拒绝，未知不会满足要求。choice顺序就是该位置偏好。

substitution三种范围：fixed只有一个候选；size_only只允许同一item_group、其他属性相同且规格明确的候选；accepted_items允许列出的不同餐品。item_group只是本地声明，不是官方属性或商品ID。size_only当前还要求实际可售匹配绑定使用同一容器productCode；不能确认时停下，不猜测两个不同商品代码一定同款。

每个候选根quantity由配方决定，同款容器不同杯型使用不同本地sku绑定，真实productCode与roundList按详情保留。不得将绑定别名或子选项当独立官方商品。

balanced策略：在预算内，最小化各位置候选序号之和（首选为0），同分选总价低的。用户有“某一位置绝对优先”等冲突要求时先澄清成明确候选，不声称支持自定义权重或优先级策略。生成请求先按该偏好排序，最多5份；超出组合未检查时明确说明。

## 命令

```text
python scripts/run.py review --intent meal.intent.json --output review.local.json
python scripts/run.py confirm --intent meal.intent.json --reviewed-fingerprint <用户已接受的review中指纹> --output confirmation.local.json
python scripts/run.py prepare --intent meal.intent.json --confirmation confirmation.local.json --bindings bindings.local.json --output job.local.json
python scripts/run.py assess --job job.local.json --receipts receipts.local.json --intent meal.intent.json --confirmation confirmation.local.json --output assessment.local.json
python scripts/run.py card --job job.local.json --receipts receipts.local.json --intent meal.intent.json --confirmation confirmation.local.json --output result.svg
python scripts/run.py share --intent meal.intent.json --output conditions-share.zip
```

脚本只做本地处理，不调用MCP。live时效仍为本地报价120秒、资料600秒。历史演示需明确标记回放；不能伪造历史复核时点来绕过失败。

分享ZIP只有meal.intent.json、兼容的meal.recipe.json、card.md，不带确认文件、门店、报价、券或凭据。接收者读取intent后重新确认，用本人门店执行。只有旧recipe的接收者仍能保留每个已列候选中的必须属性，但没有新的条件版本校验；优先使用v0.1.1 intent流程。
