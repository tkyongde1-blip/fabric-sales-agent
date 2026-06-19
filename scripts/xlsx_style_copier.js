#!/usr/bin/env node
/**
 * 100% 模板保真风格复制器
 *
 * 策略：
 *   1. styles.xml 从模板复制（边框/字体/填充定义）
 *   2. sharedStrings.xml 从模板复制（表头文字按索引引用）
 *   3. 所有 sheet 从模板复制完整结构
 *   4. 仅从 SheetJS 输出提取数据 VALUE 注入到模板的 cell 结构中
 *   5. 共享字符串（t="s"）→ 内联文本（t="str"），避免索引冲突
 */
const fs = require('fs');
const path = require('path');
const JSZip = require('jszip');

async function copyStyles(templatePath, outputPath) {
  const [tmplZip, outZip] = await Promise.all([
    JSZip.loadAsync(fs.readFileSync(templatePath)),
    JSZip.loadAsync(fs.readFileSync(outputPath)),
  ]);

  // sharedStrings
  const outSsFile = outZip.file('xl/sharedStrings.xml');
  const outStrings = outSsFile ? buildStringTable(await outSsFile.async('string')) : [];

  // 1. styles.xml from template
  outZip.file('xl/styles.xml', await tmplZip.file('xl/styles.xml').async('string'));

  // 2. sharedStrings.xml from template
  outZip.file('xl/sharedStrings.xml', await tmplZip.file('xl/sharedStrings.xml').async('string'));

  // 3. template sheet1 reference structure
  const tmplSheet = await tmplZip.file('xl/worksheets/sheet1.xml').async('string');
  const tmplBefore = tmplSheet.split('<sheetData>')[0];
  const tmplData = tmplSheet.match(/<sheetData>([\s\S]*?)<\/sheetData>/)[1];
  const tmplAfter = tmplSheet.split('</sheetData>')[1];

  // 模板 cell 索引
  const tmplCells = new Map();
  const cellRe = /<c\s[^>]*?r="(\w+)"[^>]*?(?:\/>|>[\s\S]*?<\/c>)/g;
  let m;
  while ((m = cellRe.exec(tmplData)) !== null) {
    tmplCells.set(m[1], { xml: m[0], len: m[0].length, idx: m.index });
  }

  // 模板行属性
  const rowAttrMap = new Map();
  const rowRe = /(<row\s[^>]*?r="(\d+)"[^>]*?>)/g;
  while ((m = rowRe.exec(tmplData)) !== null) {
    rowAttrMap.set(m[2], m[1].replace(/^<row\s/, '').replace(/>$/, '').trim());
  }

  // 4. 处理每个 sheet
  const sheetKeys = Object.keys(outZip.files).filter(k =>
    k.startsWith('xl/worksheets/sheet') && k.endsWith('.xml')
  );

  for (const key of sheetKeys) {
    const outXml = await outZip.file(key).async('string');
    const outVals = extractValuesFromOutput(outXml, outStrings);

    const mods = [];

    for (const [ref, { value, type, formula }] of outVals) {
      const tc = tmplCells.get(ref);
      if (!tc) continue;

      const oldXml = tc.xml;
      const closing = oldXml.endsWith('/>');

      let newXml;
      if (closing) {
        const tag = oldXml.slice(0, -2);
        const ta = type && type !== 'n' ? ` t="${type}"` : '';
        if (value !== undefined) {
          newXml = `${tag}${ta}><v>${esc(value)}</v></c>`;
        } else {
          newXml = oldXml;
        }
      } else {
        const openTag = oldXml.match(/<c\s[^>]*?>/)[0];
        const closeTag = '</c>';
        const oldF = oldXml.match(/<f(?:\s[^>]*)?(?:\/>|>[\s\S]*?<\/f>)/);
        const oldV = oldXml.match(/<v>[\s\S]*?<\/v>/);
        const parts = [];
        if (oldF) parts.push(oldF[0]);
        if (value !== undefined) {
          parts.push(`<v>${esc(value)}</v>`);
        } else if (!formula && oldV) {
          parts.push(oldV[0]);
        }
        let newOpen;
        if (type && type !== 'n') {
          newOpen = /\bt="/.test(openTag)
            ? openTag.replace(/\bt="\w+"/, ` t="${type}"`)
            : openTag.replace('<c ', `<c t="${type}" `);
        } else {
          newOpen = openTag.replace(/\bt="\w+"/, '');
        }
        newXml = `${newOpen}${parts.join('')}${closeTag}`;
      }

      if (newXml !== oldXml) {
        mods.push({ ref, idx: tc.idx, oldLen: tc.len, newXml });
      }
    }

    mods.sort((a, b) => b.idx - a.idx);
    let nd = tmplData;
    for (const mod of mods) {
      nd = nd.slice(0, mod.idx) + mod.newXml + nd.slice(mod.idx + mod.oldLen);
    }

    // row attributes
    nd = nd.replace(/<row\s[^>]*?>/g, match => {
      const r = match.match(/r="(\d+)"/);
      const ta = r && rowAttrMap.get(r[1]);
      return ta ? `<row ${ta}>` : match;
    });

    outZip.file(key, tmplBefore + '<sheetData>' + nd + '</sheetData>' + tmplAfter);
  }

  // 5. write
  fs.writeFileSync(outputPath, await outZip.generateAsync({ type: 'nodebuffer' }));
  return true;
}

function buildStringTable(ssXml) {
  const r = [];
  const re = /<si>([\s\S]*?)<\/si>/g;
  let m;
  while ((m = re.exec(ssXml)) !== null) {
    const t = m[1].match(/<t[^>]*>([^<]*)<\/t>/);
    r.push(t ? unesc(t[1]) : '');
  }
  return r;
}

function extractValuesFromOutput(xml, strings) {
  const r = new Map();
  const re = /<c\s[^>]*?r="(\w+)"([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const ref = m[1], attrs = m[2], body = m[3] || '';
    const t = (attrs.match(/\bt="(\w+)"/) || [])[1];
    const v = (body.match(/<v>([^<]*)<\/v>/) || [])[1];
    const f = (body.match(/<f[^>]*>[\s\S]*?<\/f>/) || [])[0];
    if (v === undefined && !f) continue;
    if (t === 'z') continue;
    let fv = v, ft = t;
    if (t === 's' && v !== undefined) {
      const idx = parseInt(v);
      fv = !isNaN(idx) && idx < strings.length ? strings[idx] : '';
      ft = 'str';
    }
    if (v !== undefined || f) r.set(ref, { value: fv, type: ft, formula: f });
  }
  return r;
}

function esc(s) {
  if (s === undefined || s === null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function unesc(s) {
  return s.replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&');
}

module.exports = { copyStyles };

if (require.main === module) {
  (async () => {
    const [tmpl, out] = process.argv.slice(2);
    if (!tmpl || !out) { console.error('Usage: node xlsx_style_copier.js <template.xlsx> <output.xlsx>'); process.exit(1); }
    try { await copyStyles(path.resolve(tmpl), path.resolve(out)); console.log('✓ All sheets styled'); }
    catch (e) { console.error('✗', e.message); process.exit(1); }
  })();
}
