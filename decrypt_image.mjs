// Decrypt an SDK image with its 32-byte AES-256-GCM key (Douyin image messages).
// Usage: node decrypt_image.mjs <encrypted.bin> <skey-hex> <plain.bin>
import { createDecipheriv } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';

const [, , input, skeyHex, output] = process.argv;
if (!input || !skeyHex || !output) throw new Error('usage: decrypt_image.mjs <encrypted> <skey> <plain>');
const key = Buffer.from(skeyHex, 'hex');
if (key.length !== 32 || skeyHex.length !== 64) throw new Error('image skey must be 32 bytes hex');
const data = readFileSync(input);
if (data.length < 28) throw new Error('encrypted image too short');
const iv = data.subarray(0, 12);
const tag = data.subarray(data.length - 16);
const ciphertext = data.subarray(12, data.length - 16);
const decipher = createDecipheriv('aes-256-gcm', key, iv);
decipher.setAuthTag(tag);
writeFileSync(output, Buffer.concat([decipher.update(ciphertext), decipher.final()]));
