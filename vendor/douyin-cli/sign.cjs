// Local wrapper: fixed reviewed source, JSON stdin, no shell or network access.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
if (typeof input.query !== 'string' || typeof input.userAgent !== 'string' || input.query.length > 10000) throw new Error('Invalid request');
const context = vm.createContext({query: input.query, userAgent: input.userAgent});
const source = fs.readFileSync(path.join(__dirname, 'douyin.js'), 'utf8');
const value = new vm.Script(source + '\nsign_datail(query,userAgent)').runInContext(context, {timeout: 1000});
process.stdout.write(JSON.stringify({signature: value}));
