// 生成 GCM 权威测试向量（用 Node 内置 crypto 作为参考实现）
// 用法：node gen_vectors.js > test_vectors.py
const crypto = require('crypto');

const V = [
  ['aes256-empty', '00'.repeat(32), '00'.repeat(12), '', ''],
  ['aes128-oneblock', '00'.repeat(16), '00'.repeat(12), '', '00'.repeat(16)],
  ['aes128-aad16', 'feffe9928665731c6d6a8f9467308308', 'cafebabefacedbaddecaf888',
    'feedfacedeadbeeffeedfacedeadbeefabaddad2',
    'd9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255'],
  ['aes256-aad8-200b', '60'.repeat(32), '1af38c2dc2b96ffdd8669409',
    '0102030405060708', '11'.repeat(200)],
  ['aes256-aad54', '9b'.repeat(32), '00112233445566778899aabb',
    '57'.repeat(54), 'ab'.repeat(333)],
];

console.log('"""AES-GCM 权威测试向量（由 Node crypto 生成，勿手改）。');
console.log('');
console.log('重新生成：node gen_vectors.js > test_vectors.py');
console.log('每项为 (名称, key_hex, nonce_hex, aad_hex, pt_hex, ct_hex, tag_hex)。');
console.log('"""\n');
console.log('VECTORS = [');
for (const [name, k, n, aad, pt] of V) {
  const bits = k.length === 64 ? 256 : 128;
  const c = crypto.createCipheriv(`aes-${bits}-gcm`, Buffer.from(k, 'hex'),
    Buffer.from(n, 'hex'), { authTagLength: 16 });
  if (aad) c.setAAD(Buffer.from(aad, 'hex'));
  const ct = Buffer.concat([c.update(Buffer.from(pt, 'hex')), c.final()]);
  const tag = c.getAuthTag().toString('hex');
  console.log(`    (${JSON.stringify(name)},`);
  console.log(`     ${JSON.stringify(k)}, ${JSON.stringify(n)}, ${JSON.stringify(aad)},`);
  console.log(`     ${JSON.stringify(pt)}, ${JSON.stringify(ct.toString('hex'))},`);
  console.log(`     ${JSON.stringify(tag)}),`);
}
console.log(']');
