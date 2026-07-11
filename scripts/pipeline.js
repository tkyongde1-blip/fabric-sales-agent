#!/usr/bin/env node
/**
 * 大利纺织 完整流水线：
 *   图片解析 → 销售单生成
 *
 * 单产品用法：
 *   node scripts/pipeline.js <image.png> \
 *       --customer 恒泽 \
 *       --name 牛奶丝双磨 \
 *       --spec 190CM180G \
 *       --color 白色
 *
 * 双产品用法（前N匹一个颜色/价格，后面另一个）：
 *   node scripts/pipeline.js <image.png> \
 *       --customer 巫石 \
 *       --name 牛奶丝双磨 \
 *       --spec 185CM180G \
 *       --color 黑色 --price 12 --split 20 \
 *       --color2 白色 --price2 11
 */
const path = require('path');
const fs = require('fs');
const { parseInvoice } = require('./parse_invoice');
const { generate, getNextNO } = require('./make_sales_order');

async function main() {
  const args = process.argv.slice(2);
  const imgArg = args.find(a => !a.startsWith('--'));
  if (!imgArg) {
    console.error('用法: node scripts/pipeline.js <image.png> --customer 恒泽 --name 牛奶丝双磨 --spec 190CM180G');
    process.exit(1);
  }

  function getArg(name) {
    const i = args.indexOf(`--${name}`);
    return i >= 0 && i + 1 < args.length ? args[i + 1] : undefined;
  }

  const customer = getArg('customer') || '客户';
  // 品名/规格优先从图片OCR提取，命令行参数可覆盖
  const nameFromCli = getArg('name');
  const specFromCli = getArg('spec');
  const color = getArg('color') || '';
  const price = parseFloat(getArg('price')) || undefined;
  const unit = getArg('unit') || '公斤';
  const no = parseInt(getArg('no')) || getNextNO();
  const split = parseInt(getArg('split')) || 0;
  const color2 = getArg('color2');
  const price2 = parseFloat(getArg('price2')) || undefined;

  // ===== Step 1: 解析图片 =====
  console.log('══════════════════════════════════════');
  console.log('  Step 1/2: 图片解析');
  console.log('══════════════════════════════════════\n');

  const imgPath = path.resolve(imgArg);
  if (!fs.existsSync(imgPath)) {
    console.error(`图片不存在: ${imgPath}`);
    process.exit(1);
  }

  const result = await parseInvoice(imgPath);

  // 提取重量
  const allWeights = result.weights || result.cells
    .filter(c => c.value >= 15 && c.value <= 60)
    .map(c => Math.round(c.value * 10) / 10);

  if (allWeights.length === 0) {
    console.error('未解析到任何重量数据');
    process.exit(1);
  }

  const totalKg = allWeights.reduce((s, w) => s + w, 0);
  const imgPrice = result.summary.price;

  // ===== 校验：逐格提取必须与图片总结行一致 =====
  const summaryPieces = result.summary.pieces;
  const summaryKg = result.summary.kg;
  const kgDiff = summaryKg ? Math.abs(totalKg - summaryKg) : 0;

  const errors = [];
  if (summaryPieces && allWeights.length !== summaryPieces) {
    errors.push(`匹数不符: 逐格提取${allWeights.length}匹 ≠ 图片总结${summaryPieces}匹`);
  }
  if (summaryKg && kgDiff > 0.5) {
    errors.push(`公斤不符: 逐格合计${totalKg.toFixed(1)}kg ≠ 图片总结${summaryKg}kg (差${kgDiff.toFixed(1)}kg)`);
  }
  if (!summaryPieces && !summaryKg) {
    errors.push('图片总结行未识别到总匹数和总公斤，数据不可信');
  }

  if (errors.length > 0) {
    console.error(`\n✗ 数据校验失败，拒绝生成：`);
    errors.forEach(e => console.error(`  - ${e}`));
    console.error(`\n  逐格提取: ${allWeights.length}匹 ${totalKg.toFixed(1)}公斤`);
    if (summaryPieces || summaryKg) console.error(`  图片总结: ${summaryPieces||'?'}匹 ${summaryKg||'?'}公斤`);
    process.exit(1);
  }

  console.log(`  ✓ 数据校验通过: ${allWeights.length}匹 ${totalKg.toFixed(1)}kg (图片${summaryPieces}匹 ${summaryKg}kg)`);

  // ===== 构建产品列表 =====
  // 从图片OCR提取品名/规格，命令行参数可覆盖
  const ocrName = result.products && result.products[0] ? result.products[0].name : undefined;
  const ocrSpec = result.products && result.products[0] ? result.products[0].spec : undefined;
  const name = nameFromCli || ocrName || '产品';
  const spec = specFromCli || ocrSpec || '';

  console.log(`  品名来源: ${nameFromCli ? '命令行' : '图片OCR'} → ${name}`);
  console.log(`  规格来源: ${specFromCli ? '命令行' : '图片OCR'} → ${spec}`);

  let products;
  if (split > 0 && color2) {
    // 双产品：前split匹为color1，后面为color2
    const w1 = allWeights.slice(0, split);
    const w2 = allWeights.slice(split);
    products = [
      { name, spec, color, unit, price: price || imgPrice, weights: w1 },
      { name, spec: getArg('spec2') || spec, color: color2, unit, price: price2 || imgPrice, weights: w2 },
    ];
  } else {
    products = [{
      name, spec, color, unit, price: price || imgPrice, weights: allWeights,
    }];
  }

  // ===== Step 2: 生成销售单 =====
  console.log('\n══════════════════════════════════════');
  console.log('  Step 2/2: 销售单生成');
  console.log('══════════════════════════════════════\n');

  const input = { customer, no, products };

  console.log(`  客户: ${customer}`);
  for (const p of products) {
    console.log(`  ${p.color} ${p.name} ${p.spec} 单价${p.price}元 ${p.weights.length}匹`);
    if (p.weights.length > 0) {
      const pk = p.weights.reduce((s, w) => s + w, 0);
      console.log(`    ${pk.toFixed(1)}公斤`);
    }
  }
  console.log(`  总匹数: ${allWeights.length}`);
  console.log(`  总公斤: ${totalKg.toFixed(1)}`);
  if (result.summary.kg) console.log(`  总公斤(图片): ${result.summary.kg}`);
  if (result.summary.amount) console.log(`  总金额(图片): ${result.summary.amount}元`);
  console.log(`  均值: ${(totalKg / allWeights.length).toFixed(1)}kg/匹\n`);

  const output = await generate(input);

  console.log(`\n✅ 生成完成`);
  console.log(`  ${output.filename}`);
  console.log(`  xlsx: ${output.path}`);
  if (output.htmlPath) console.log(`  html: ${output.htmlPath}`);
  console.log(`  客户: ${customer}  单号: XFS${output.orderNo}  NO.${output.no}`);
  console.log(`  匹数: ${output.totalWeights}${output.rule2Applied ? ' (规则2已应用)' : ''}`);
  console.log(`  Sheet数: ${output.sheetCount}`);

  // 生成合并图（表格+收款码）→ 剪贴板
  const qrArg = getArg('qr');
  if (qrArg && output.htmlPath) {
    const qrPaths = qrArg.split(';').filter(fs.existsSync);
    const qrArgs = qrPaths.map(q => `"${q}"`).join(' ');
    try {
      require('child_process').execSync(
        `node "${path.join(__dirname, 'copy_to_clipboard.js')}" "${output.path}" ${qrArgs}`,
        { stdio: 'inherit' }
      );
    } catch (e) {
      console.log(`  ⚠ 合并图生成失败: ${e.message}`);
    }
  }
}

main().catch(e => {
  console.error(`\n✗ 错误: ${e.message}`);
  process.exit(1);
});
